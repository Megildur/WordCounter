"""Keeps banned users and servers from using WordCounter.

Gateway events from them are dropped by `screen`, which MyBot.dispatch calls first. Commands, buttons and modals don't go through dispatch, so `install` wraps discord.py's INTERACTION_CREATE parser to answer them with a ban notice. The ids live in `bot.blacklisted`, loaded from the blacklist table at startup and changed by /owner blacklist. A cog with `acts_on_blacklisted` still gets a banned user's events, never a banned server's.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Dict, Iterator, Optional, Sequence, Set, Tuple
import discord
from cogs.utils.components import ERROR_COLOR, create_v2_view
from cogs.utils.config import SUPPORT_URL

log = logging.getLogger(__name__)

USER = "user"
SERVER = "server"
# The cog attribute that opts a cog in to a banned user's events.
GUARD = "acts_on_blacklisted"
USER_TYPES = (discord.user.BaseUser, discord.Member)
# Keeps the refusal tasks referenced until they finish, because the event loop only holds weak references to tasks.
_TASKS: Set[asyncio.Task] = set()


def ids(bot: Any) -> Set[int]:
    """Returns the bot's set of banned user and server ids, creating an empty one if the bot doesn't have it yet."""
    found = getattr(bot, "blacklisted", None)
    if found is None:
        found = bot.blacklisted = set()
    return found


def _ids_in(value: Any) -> Iterator[Tuple[str, int]]:
    """Yields (kind, id) for each user or server an event argument refers to: itself, its guild or guild_id, its user_id, author or user, and the author and guild of a message it carries."""
    if isinstance(value, discord.Guild):
        yield SERVER, value.id
        return
    if isinstance(value, USER_TYPES):
        yield USER, value.id
    guild = getattr(value, "guild", None)
    if isinstance(guild, discord.Guild):
        yield SERVER, guild.id
    for name, kind in (("guild_id", SERVER), ("user_id", USER)):
        found = getattr(value, name, None)
        if isinstance(found, int):
            yield kind, found
    for name in ("author", "user"):
        found = getattr(value, name, None)
        if isinstance(found, USER_TYPES):
            yield USER, found.id
    message = getattr(value, "message", None)
    if isinstance(message, discord.Message):
        yield USER, message.author.id
        if message.guild is not None:
            yield SERVER, message.guild.id


def blocked_by(bot: Any, args: Sequence[Any]) -> Optional[str]:
    """Returns SERVER or USER if any event argument is banned, otherwise None. A banned server wins over a banned user. If the check itself fails, the event is let through."""
    blocked = ids(bot)
    if not blocked:
        return None
    try:
        kind = None
        for found_kind, found in (pair for value in args for pair in _ids_in(value)):
            if found in blocked:
                if found_kind == SERVER:
                    return SERVER
                kind = USER
        return kind
    except Exception as error:
        log.warning("Couldn't check an event against the blacklist: %s", error)
        return None


def screen(bot: Any, event: str, args: Sequence[Any], kwargs: Dict[str, Any]) -> bool:
    """Returns True if the event is blocked, and MyBot.dispatch then drops it.

    When only a user is banned, the listeners of cogs with `acts_on_blacklisted` still get the event, scheduled directly. Only cog listeners are re-sent, not the bot's own handlers.
    """
    kind = blocked_by(bot, args)
    if kind is None:
        return False
    if kind == USER:
        name = "on_" + event
        for listener in bot.extra_events.get(name, ()):
            if getattr(getattr(listener, "__self__", None), GUARD, False):
                bot._schedule_event(listener, name, *args, **kwargs)
    return True


def _snowflake(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def blocked_kind(bot: Any, data: dict) -> Optional[str]:
    """Runs the same check on a raw INTERACTION_CREATE payload, before discord.py builds an Interaction from it. Returns USER, SERVER or None, looking at the user first."""
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
    """Answers an interaction from a banned user, or in a banned server, with the ban notice that only the person who triggered it sees, or with no choices for autocomplete. Discord errors are only logged."""
    try:
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message(view=banned_view(kind), ephemeral=True)
    except discord.HTTPException as error:
        log.debug("Could not answer a blacklisted interaction: %s", error)


def install(bot: discord.Client) -> None:
    """Wraps discord.py's INTERACTION_CREATE parser so banned users and servers never reach commands, buttons or modals.

    That parser hands interactions straight to the command tree and the views, not through `dispatch`, so `screen` can't see them. Installing twice does nothing.
    """
    state = bot._connection
    original = state.parsers["INTERACTION_CREATE"]
    # The marker set on the wrapper below tells a second call that the parser is already wrapped.
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
        # The parser is not async, so the reply runs as its own task.
        task = asyncio.get_running_loop().create_task(_refuse(interaction, kind))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    parse.blacklist_gate = True
    state.parsers["INTERACTION_CREATE"] = parse
