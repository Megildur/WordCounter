"""One shared aiohttp session for the bot's outgoing web requests (the log webhook and the Discord login on the website)."""

from __future__ import annotations
from typing import Optional
import aiohttp

_session: Optional[aiohttp.ClientSession] = None


def get_session() -> aiohttp.ClientSession:
    """Returns the shared session, creating it on first use or after it was closed. Requests time out after 10 seconds."""
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10))
    return _session


async def close_session() -> None:
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
    _session = None
