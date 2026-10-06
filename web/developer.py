from __future__ import annotations
import asyncio
import time
from typing import Any, Dict, Optional, Tuple
import discord
from cogs.utils.config import DEVELOPER_ID, DEVELOPER_INVITE_URL

FALLBACK_AVATAR = "/static/img/developer.webp"
DEFAULT_STATUS = "Creator of WordCounter and Botzilla. Building custom Discord bots."
STATUS_LABELS = {"online": "Online", "idle": "Idle", "dnd": "Do not disturb", "offline": "Offline"}
PROFILE_TTL = 60 * 60
FETCH_TIMEOUT = 3

_cache: Dict[str, Any] = {"user": None, "at": float("-inf")}


async def _profile_user(bot) -> Optional[discord.User]:
    fetch_user = getattr(bot, "fetch_user", None)
    if fetch_user is None or time.monotonic() - _cache["at"] < PROFILE_TTL:
        return _cache["user"]
    _cache["at"] = time.monotonic()
    try:
        _cache["user"] = await asyncio.wait_for(fetch_user(DEVELOPER_ID), FETCH_TIMEOUT)
    except (discord.HTTPException, asyncio.TimeoutError):
        pass
    return _cache["user"]


def _member(bot) -> Optional[discord.Member]:
    return next((member for guild in bot.guilds if (member := guild.get_member(DEVELOPER_ID)) is not None), None)


def _activities(member: Optional[discord.Member]) -> Tuple[Optional[str], Optional[str]]:
    if member is None:
        return None, None
    custom = next((a for a in member.activities if isinstance(a, discord.CustomActivity)), None)
    spotify = next((a for a in member.activities if isinstance(a, discord.Spotify)), None)
    text = (custom.name or custom.state) if custom else None
    return text, f"{spotify.title} by {spotify.artist}" if spotify else None


async def developer_profile(bot) -> Dict[str, Any]:
    member = _member(bot)
    user = await _profile_user(bot)
    person = user or member
    status = str(member.status) if member is not None else None
    custom_status, listening = _activities(member)
    decoration = getattr(person, "avatar_decoration", None)
    banner = getattr(user, "banner", None)
    return {
        "name": getattr(person, "global_name", None) or "Brandon",
        "username": getattr(person, "name", None) or "megildur272",
        "tag": "BMTH",
        "status": status if status in STATUS_LABELS else None,
        "status_label": STATUS_LABELS.get(status),
        "custom_status": custom_status or DEFAULT_STATUS,
        "listening": listening,
        "avatar_url": person.display_avatar.with_size(256).url if person is not None else FALLBACK_AVATAR,
        "decoration_url": decoration.url if decoration else None,
        "banner_url": banner.with_size(1024).url if banner else None,
        "invite_url": DEVELOPER_INVITE_URL,
    }
