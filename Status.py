from __future__ import annotations
import logging
import random
import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.cycle.start()

    def cog_unload(self) -> None:
        self.cycle.cancel()

    def presences(self) -> list[discord.BaseActivity]:
        servers = len(self.bot.guilds)
        return [
            discord.Activity(type=discord.ActivityType.watching, name=f"words in {servers:,} servers"),
            discord.Activity(type=discord.ActivityType.listening, name="/help"),
            discord.Activity(type=discord.ActivityType.watching, name="the leaderboard"),
            discord.CustomActivity(name="Counting every word"),
            discord.CustomActivity(name="Who talks the most? Try /leaderboard"),
        ]

    @tasks.loop(hours=1.0)
    async def cycle(self) -> None:
        activity = random.choice(self.presences())
        try:
            await self.bot.change_presence(activity=activity, status=discord.Status.online)
        except (discord.DiscordException, OSError) as error:
            log.warning("Could not update presence: %s", error)

    @cycle.before_loop
    async def before_cycle(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        log.info("Logged in as %s (ID: %s)", self.bot.user, self.bot.user.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Status(bot))
