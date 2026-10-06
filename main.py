import asyncio
import logging
import os
import sys
import discord
from discord.ext import commands
from cogs.utils.config import API_TOKEN, daily_log_handler
from cogs.utils.database import WordCounterDatabase
from cogs.utils.http import close_session

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8")

formatter = logging.Formatter(fmt="[%(asctime)s] [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
file_handler = daily_log_handler("discord.log")
console_handler = logging.StreamHandler(sys.stdout)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
for handler in (file_handler, console_handler):
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)
    root_logger.addHandler(handler)

log = logging.getLogger("main")

CORE_EXTENSIONS = ("Quicksync", "Status", "Errors", "Server_log", "OwnerCommands")


class MyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!wc", intents=discord.Intents.all())
        self.db = WordCounterDatabase(self)

    async def setup_hook(self) -> None:
        await self.db.connect()

        def handle_async_exception(loop: asyncio.AbstractEventLoop, context: dict) -> None:
            error = context.get("exception")
            log.error("Unhandled background error: %s", context.get("message") or error, exc_info=error)

        asyncio.get_running_loop().set_exception_handler(handle_async_exception)

        self.tree.allowed_installs = discord.app_commands.AppInstallationType(guild=True, user=False)
        self.tree.allowed_contexts = discord.app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False)
        for extension in CORE_EXTENSIONS:
            await self.load_extension(extension)
        for filename in sorted(os.listdir("cogs")):
            if filename.endswith(".py"):
                await self.load_extension(f"cogs.{filename[:-3]}")

    async def close(self) -> None:
        await super().close()
        await close_session()
        await self.db.close()


bot = MyBot()

if __name__ == "__main__":
    if not API_TOKEN:
        sys.exit("API_TOKEN is missing. Add it to your .env file.")
    bot.run(API_TOKEN, log_handler=None)
