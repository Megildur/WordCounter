from __future__ import annotations
from typing import List, Tuple
import discord

ANALYSIS_BUSY_MESSAGE = "An analysis is running in this server. Try again once it finishes."

LEADERBOARDS = {
    "words": "Words",
    "messages": "Messages",
    "attachments": "Attachments",
    "emojis": "Emojis",
    "keywords": "Keywords",
}

TRACKABLE_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.forum,
    discord.ChannelType.voice,
]


def parse_keywords(raw: str) -> List[str]:
    parts = [part.strip().lower() for line in raw.splitlines() for part in line.split(",") if part.strip()]
    return list(dict.fromkeys(parts))


async def analysis_state(bot, guild_id: int) -> Tuple[bool, bool]:
    cog = bot.get_cog("AnalyzeChat")
    running = cog is not None and guild_id in cog.running_guilds
    return running, running or await bot.db.has_analysis_run(guild_id)
