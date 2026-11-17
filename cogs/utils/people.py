"""The one count of how many people WordCounter reaches: every person in a server with it, once, bots left out.

The home page, the start-up log and the live stats panel all use this, so the number never differs between them. A bot is never counted as a person here or anywhere else WordCounter reports its reach.
"""

from __future__ import annotations
import time
from typing import Any, Optional, Tuple

# The count walks every cached member, which is slow with a very large cache, and a busy home page asks for it on every visit.
CACHE_SECONDS = 60
_cache: Optional[Tuple[float, int]] = None


def unique_users(bot: Any) -> Tuple[int, bool]:
    """Returns (people, complete): how many different non-bot members share a server with the bot, and whether every server's member list is fully loaded. While one isn't, the count is a floor and isn't kept, so the next ask counts again."""
    global _cache
    guilds = list(getattr(bot, "guilds", None) or ())
    if not guilds:
        return 0, True
    if _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
        return _cache[1], True
    people = set()
    for guild in guilds:
        people.update(member.id for member in guild.members if not member.bot)
    complete = all(guild.chunked for guild in guilds)
    if complete:
        _cache = (time.monotonic(), len(people))
    return len(people), complete
