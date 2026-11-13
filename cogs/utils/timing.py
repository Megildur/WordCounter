"""Log lines that say why an answer to Discord was slow: the event loop fell behind, or an interaction reached the bot late.

Discord gives a bot 3 seconds to answer an interaction. An answer that lands at the edge shows "interaction failed" and then still updates, so these lines separate the possible causes: a slow network (the interaction arrives late), a blocked event loop (the loop runs late) or a busy database (`mysql.py` logs a lock that was waited for or held too long).
"""

from __future__ import annotations
import asyncio
import logging
from typing import Optional
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

LOOP_INTERVAL = 1.0
# How late the loop's own timer may run before it is logged.
LOOP_LATE = 1.0
# How long after Discord created an interaction it may reach the handler before it is logged. A little of this is clock difference between this host and Discord.
INTERACTION_LATE = 2.0


class Timing(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._watcher: Optional[asyncio.Task] = None

    async def cog_load(self) -> None:
        self._watcher = asyncio.get_running_loop().create_task(self._watch_loop())

    async def cog_unload(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()

    async def _watch_loop(self) -> None:
        """Sleeps for LOOP_INTERVAL again and again. When it wakes up later than it should have, something kept the event loop busy for that long."""
        loop = asyncio.get_running_loop()
        due = loop.time() + LOOP_INTERVAL
        while True:
            await asyncio.sleep(LOOP_INTERVAL)
            late = loop.time() - due
            if late > LOOP_LATE:
                log.warning("The event loop ran %.1f s late, so anything it was about to answer was late too", late)
            due = loop.time() + LOOP_INTERVAL

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        age = (discord.utils.utcnow() - interaction.created_at).total_seconds()
        if age > INTERACTION_LATE:
            name = (interaction.data or {}).get("name") if interaction.type is discord.InteractionType.application_command else None
            log.warning("An interaction (%s%s) reached its handler %.1f s after Discord sent it, and Discord wants the answer 3 s after it sent it", interaction.type.name, f", {name}" if name else "", age)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Timing(bot))
