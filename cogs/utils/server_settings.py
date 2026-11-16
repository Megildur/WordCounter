"""Constants and small helpers shared by the /settings screens, the swear jar settings and the website dashboard."""

from __future__ import annotations
from typing import List, Tuple
import discord

ANALYSIS_BUSY_MESSAGE = "An analysis is running in this server. Try again once it finishes."

# The choices of "Reset everything" in Discord and on the dashboard: what to reset (the stats and settings, the swear jar) and what to tell the person afterwards.
WIPES = {
    "stats": (True, False, "Stats and settings reset. All counts, analysis history, channels and keywords were cleared. The swear jar was kept."),
    "jar": (False, True, "Swear jar reset. Its settings, words, rules, goal, balances, fines and counts were cleared. Stats and tracking were kept."),
    "both": (True, True, "Server reset. All stats, analysis history, channels, keywords and the swear jar were cleared."),
}

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

    Count resets in /settings are refused while busy. The swear jar reset and the whole-server wipe are refused only while running, and the wipe also deletes the saved run that belongs to what it resets.
    """
    cog = bot.get_cog("AnalyzeChat")
    running = cog is not None and guild_id in cog.running_guilds
    return running, running or await bot.db.has_analysis_run(guild_id)
