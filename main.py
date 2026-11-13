"""Entry point: `python main.py` sets up logging, builds the bot and loads every extension.

The website in `web/` is one of the extensions and serves from the same process. The database connects in `setup_hook`, before the bot receives any events. Run it from the repo root, because `cogs/`, `Data/` and the log files are found by relative path.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Set
import aiohttp
import discord
from discord.ext import commands
from cogs.utils import blacklist, expired, nettrace, startup
from cogs.utils.config import API_TOKEN, daily_log_handler
from cogs.utils.database import NotMigrated, WordCounterDatabase
from cogs.utils.http import close_session

# Print as UTF-8 so non-ASCII text in log lines, like emoji in server names, can't raise on a console with a legacy code page.
for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

formatter = logging.Formatter(fmt="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler = daily_log_handler("discord.log")
console_handler = logging.StreamHandler(sys.stdout)
# Everything, discord.py included, logs through the root logger. bot.run is given log_handler=None below so discord.py doesn't add its own.
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
for handler in (file_handler, console_handler):
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    root_logger.addHandler(handler)

log = logging.getLogger("main")

# How long discord.py's HTTP session remembers the address it looked up for discord.com. Its default is 10 seconds, so a press after a quiet minute waited for a new lookup before its first answer could leave, and lookups on this host fail or take seconds now and then.
DNS_CACHE_SECONDS = 300.0

# The extensions loaded before the cogs: the ones at the repo root, `web` (the website) and the timing log lines in cogs/utils.
CORE_EXTENSIONS = ("Quicksync", "Status", "Errors", "Server_log", "OwnerCommands", "web", "cogs.utils.timing")


class MyBot(commands.Bot):
    """The bot. Nobody is pinged by default, so a message that must ping passes its own `allowed_mentions`."""
    def __init__(self) -> None:
        # The only text command WordCounter adds is !wcquicksync, and Intents.all() includes the privileged message content and members intents the counting needs.
        super().__init__(
            command_prefix="!wc", intents=discord.Intents.all(), allowed_mentions=discord.AllowedMentions.none(), http_trace=nettrace.trace_config(),
        )
        self.db = WordCounterDatabase(self)
        # Banned user and server IDs. Their events and interactions are stopped before the cogs, so cogs check it only for work they start themselves, or for events when they set acts_on_blacklisted.
        self.blacklisted: Set[int] = set()

    def dispatch(self, event_name: str, /, *args: Any, **kwargs: Any) -> None:
        """Drops an event that involves a banned user or server before any cog sees it. Cogs with `acts_on_blacklisted` still get a banned user's events, see `blacklist.screen`."""
        if not blacklist.screen(self, event_name, args, kwargs):
            super().dispatch(event_name, *args, **kwargs)

    async def login(self, token: str) -> None:
        # discord.py makes its HTTP session when it logs in, from this connector, so it is set here, where an event loop is running.
        # limit=0 is what discord.py uses when it makes the connector itself: no cap on open connections.
        self.http.connector = aiohttp.TCPConnector(limit=0, ttl_dns_cache=DNS_CACHE_SECONDS)
        await super().login(token)

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.blacklisted.update(await self.db.blacklisted_ids())
        # Each install wraps the interaction parser, so startup, installed last, runs first: nobody gets through before the bot is ready, then banned users are turned away, then a press on a menu nobody listens for any more is answered as expired.
        expired.install(self)
        blacklist.install(self)
        startup.install(self)

        def handle_async_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            error = context.get("exception")
            task = context.get("task") or context.get("future")
            detail = f" ({task!r})" if task is not None else ""
            log.error("Unhandled background error: %s%s", context.get("message") or error, detail, exc_info=error)

        asyncio.get_running_loop().set_exception_handler(handle_async_exception)

        # The default for every command is server installs and server use only. A command can override it, like /help does.
        self.tree.allowed_installs = discord.app_commands.AppInstallationType(guild=True, user=False)
        self.tree.allowed_contexts = discord.app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False)
        for extension in CORE_EXTENSIONS:
            await self.load_extension(extension)
        # Every .py file in cogs/ is loaded as a cog, so a new cog only needs a `setup` function. Folders such as utils/ are skipped because only names ending in .py are loaded.
        for filename in sorted(os.listdir("cogs")):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")

    async def close(self) -> None:
        # The cogs unload inside super().close(), so the HTTP session and the database stay open until they have finished.
        await super().close()
        await close_session()
        await self.db.close()


bot = MyBot()

if __name__ == "__main__":
    if not API_TOKEN:
        sys.exit("API_TOKEN is missing. Add it to your .env file.")
    try:
        bot.run(API_TOKEN, log_handler=None)
    except NotMigrated as error:
        # The SQLite copy didn't finish, so exit with its message instead of running on half-copied data.
        sys.exit(str(error))
