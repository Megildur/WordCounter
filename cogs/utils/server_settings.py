"""Constants and small helpers shared by the /settings screens, the swear jar settings and the website dashboard."""

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
    "swears": "Swears",
}

# Channel types the channel pickers and the dashboard offer for counting.
TRACKABLE_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.forum,
    discord.ChannelType.voice,
]


def parse_keywords(raw: str) -> List[str]:
    """Splits typed input on commas and new lines, lowercases it and drops repeats, so "Foo" and "foo" are one keyword even though the tables compare text case-sensitively."""
    parts = [part.strip().lower() for line in raw.splitlines() for part in line.split(",") if part.strip()]
    return list(dict.fromkeys(parts))


async def analysis_state(bot, guild_id: int) -> Tuple[bool, bool]:
    """Returns (running, busy) for a server's analysis.

    `running` is true while an analysis is going in this process. `busy` is also true when an interrupted run is saved and waits to resume.

    Count resets in /settings are refused while busy. The swear jar reset and the whole-server wipe are refused only while running, and the wipe also deletes the saved run.
    """
    cog = bot.get_cog("AnalyzeChat")
    running = cog is not None and guild_id in cog.running_guilds
    return running, running or await bot.db.has_analysis_run(guild_id)
