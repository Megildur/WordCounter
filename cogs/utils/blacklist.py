from __future__ import annotations
import asyncio
import logging
from typing import Any, Iterator, Optional, Sequence, Set
import discord
from cogs.utils.components import ERROR_COLOR, create_v2_view
from cogs.utils.config import SUPPORT_URL

log = logging.getLogger(__name__)

USER = "user"
SERVER = "server"
USER_TYPES = (discord.user.BaseUser, discord.Member)
_TASKS: Set[asyncio.Task] = set()


def ids(bot: Any) -> Set[int]:
    found = getattr(bot, "blacklisted", None)
    if found is None:
        found = bot.blacklisted = set()
    return found


def _ids_in(value: Any) -> Iterator[int]:
    if isinstance(value, discord.Guild):
        yield value.id
        return
    if isinstance(value, USER_TYPES):
        yield value.id
    guild = getattr(value, "guild", None)
    if isinstance(guild, discord.Guild):
        yield guild.id
    for name in ("guild_id", "user_id"):
        found = getattr(value, name, None)
        if isinstance(found, int):
            yield found
    for name in ("author", "user"):
        found = getattr(value, name, None)
        if isinstance(found, USER_TYPES):
            yield found.id
    message = getattr(value, "message", None)
    if isinstance(message, discord.Message):
        yield message.author.id
        if message.guild is not None:
            yield message.guild.id


def ignores(bot: Any, args: Sequence[Any]) -> bool:
    blocked = ids(bot)
    if not blocked:
        return False
    try:
        return any(found in blocked for value in args for found in _ids_in(value))
    except Exception as error:
        log.warning("Couldn't check an event against the blacklist: %s", error)
        return False


def _snowflake(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def blocked_kind(bot: Any, data: dict) -> Optional[str]:
    blocked = ids(bot)
    if not blocked:
        return None
    user = (data.get("member") or {}).get("user") or data.get("user") or {}
    if _snowflake(user.get("id")) in blocked:
        return USER
    if _snowflake(data.get("guild_id")) in blocked:
        return SERVER
    return None


def banned_view(kind: str) -> discord.ui.LayoutView:
    if kind == SERVER:
        title = "WordCounter is off in this server"
        description = "This server has been banned from using WordCounter, so its commands and buttons don't work here."
    else:
        title = "You can't use WordCounter"
        description = "You've been banned from using WordCounter, so it won't answer your commands or buttons."
    footer = f"If you think this is a mistake, ask in the [support server]({SUPPORT_URL})."
    return create_v2_view(title, description, footer=footer, color=ERROR_COLOR)


async def _refuse(interaction: discord.Interaction, kind: str) -> None:
    try:
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message(view=banned_view(kind), ephemeral=True)
    except discord.HTTPException as error:
        log.debug("Could not answer a blacklisted interaction: %s", error)


def install(bot: discord.Client) -> None:
    state = bot._connection
    original = state.parsers["INTERACTION_CREATE"]
    if getattr(original, "blacklist_gate", False):
        return

    def parse(data: dict) -> None:
        kind = blocked_kind(bot, data)
        if kind is None:
            original(data)
            return
        try:
            interaction = discord.Interaction(data=data, state=state)
        except Exception as error:
            log.warning("Couldn't answer a blacklisted interaction: %s", error)
            return
        task = asyncio.get_running_loop().create_task(_refuse(interaction, kind))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    parse.blacklist_gate = True
    state.parsers["INTERACTION_CREATE"] = parse
