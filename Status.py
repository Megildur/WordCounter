from __future__ import annotations
import logging
import random
from typing import List, Optional
import aiosqlite
import discord
from discord.ext import commands, tasks

log = logging.getLogger(__name__)

ROTATE_MINUTES = 5


class Status(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.last: Optional[str] = None
        self.cycle.start()

    def cog_unload(self) -> None:
        self.cycle.cancel()

    def statuses(self, messages: int, words: int) -> List[str]:
        servers = len(self.bot.guilds)
        lines = [
            f"📊 Counting words in {servers:,} servers",
            "🏆 Who talks the most? Try /leaderboard",
            "📈 Anyone's totals, month by month. /stats user",
            "🔑 Count the words your server cares about. /keyword list",
            "⏪ I can count messages from before I joined. /analyze_chat",
            "😀 Emojis count too. So do stickers and links",
            "⚙️ Pick the channels I count in /settings",
            "🖱️ Right-click a message for its word count",
            "🌐 Your stats are on wordcounter.dev",
            "👋 New here? Try /help",
            "🫙 Every swear costs. /swearjar wallet",
            "🧼 Swear debt is only paid off by clean streaks",
            "🤬 Who has the worst potty mouth? /swearjar top",
            "🎯 What's the swear jar saving up for? /swearjar goal",
            "🫢 Somebody owes the swear jar",
            "✍️ Counting every word",
            "🤓 Actually, that was 12 words",
            "🔢 Lost count at a million. Starting over",
            "🐑 Counting sheep. And words",
            "🤐 Shh. I'm counting",
            "📝 This status has five words",
            "💬 Brevity is the soul of wit. Six words",
            "📖 Reading the dictionary for fun",
            "🧪 Specimen 02 from the Botzilla lab",
            "⌛ New status every 5 minutes",
        ]
        if words:
            lines.append(f"🔢 {words:,} words counted so far")
        if messages:
            lines.append(f"💬 {messages:,} messages counted so far")
        return lines

    @tasks.loop(minutes=ROTATE_MINUTES)
    async def cycle(self) -> None:
        try:
            messages, words = await self.bot.db.get_global_totals()
        except aiosqlite.Error as error:
            log.warning("Could not read totals for the status: %s", error)
            messages, words = 0, 0
        choice = random.choice([line for line in self.statuses(messages or 0, words or 0) if line != self.last])
        self.last = choice
        try:
            await self.bot.change_presence(activity=discord.CustomActivity(name=choice), status=discord.Status.online)
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
