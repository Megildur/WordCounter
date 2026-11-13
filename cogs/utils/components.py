"""Shared Components v2 helpers.

The accent colours, the container and view builders every reply uses, button and tab helpers, display-name lookup, the `can_attach` check, and the channel rules that decide what is tracked (`check_channel_with_config`). Nearly every cog imports from here, so a change affects every reply.
"""

from __future__ import annotations
import asyncio
import time
from typing import Awaitable, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union
import discord

# Accent colours for container stripes, taken from the bot avatar's palette.
BRAND_COLOR = discord.Colour(0xF08A3A)
WARNING_COLOR = discord.Colour(0xFBBF24)
NEUTRAL_COLOR = discord.Colour(0x8C96AC)
SUCCESS_COLOR = discord.Colour(0x5FAE5A)
ERROR_COLOR = discord.Colour(0xE04B3C)

# A Components v2 message holds 4000 characters of text in all, so one container keeps its text under this.
TEXT_BUDGET = 3800
TRACKING_OFF_MESSAGE = "Tracking is off in this server. An admin can turn it on in `/settings`."
SERVER_ONLY_MESSAGE = "This command only works in a server."

Callback = Callable[[discord.Interaction], Awaitable[None]]


def make_button(
    label: str,
    style: discord.ButtonStyle = discord.ButtonStyle.secondary,
    callback: Optional[Callback] = None,
    *,
    disabled: bool = False,
    url: Optional[str] = None,
) -> discord.ui.Button:
    """Builds a button. With `url` it is a link button, which has no callback and ignores `style` and `disabled`."""
    if url:
        return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)
    button = discord.ui.Button(label=label, style=style, disabled=disabled)
    if callback is not None:
        button.callback = callback
    return button


MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def rank_prefix(rank: int) -> str:
    return MEDALS.get(rank, f"**{rank}.**")


FORMER_MEMBER = "Former member"
NAME_TTL = 3600.0
NAME_WAIT = 1.5
NAME_CACHE_LIMIT = 5000
# Names fetched from Discord for people no cache knows: user id to (time fetched, name). Entries last NAME_TTL seconds, and the whole dict is emptied when it reaches NAME_CACHE_LIMIT.
_fetched_names: Dict[int, Tuple[float, str]] = {}
# Keeps the lookup tasks referenced until they finish, since asyncio only holds weak references to running tasks.
_name_lookups: Set[asyncio.Task] = set()


def _cached_name(user_id: int) -> Optional[str]:
    entry = _fetched_names.get(user_id)
    if entry and time.monotonic() - entry[0] < NAME_TTL:
        return entry[1]
    return None


async def _fetch_name(client: discord.Client, user_id: int) -> None:
    try:
        user = await client.fetch_user(user_id)
    except discord.NotFound:
        name = FORMER_MEMBER
    except discord.HTTPException:
        # Not cached, so a temporary Discord error is tried again on the next call.
        return
    else:
        name = user.display_name
    if len(_fetched_names) >= NAME_CACHE_LIMIT:
        _fetched_names.clear()
    _fetched_names[user_id] = (time.monotonic(), name)


def safe_name(name: str) -> str:
    """Escapes markdown and mentions, so a display name can't format the message around it or look like a ping."""
    return discord.utils.escape_mentions(discord.utils.escape_markdown(name))


async def member_names(client: discord.Client, guild: discord.Guild, user_ids: Iterable[int]) -> Dict[int, str]:
    """Same as `display_names`, with each name passed through `safe_name` so it can go straight into a message."""
    return {user_id: safe_name(name) for user_id, name in (await display_names(client, guild, user_ids)).items()}


async def display_names(client: discord.Client, guild: discord.Guild, user_ids: Iterable[int]) -> Dict[int, str]:
    """Returns {user id: display name} for every id.

    Looks in the guild's members, then the bot's user cache, then the fetched-name cache, and fetches the rest from Discord together, waiting at most NAME_WAIT seconds in all. Anyone still unknown is called "Former member". Fetches that miss the wait keep running and fill the cache for next time.
    """
    names: Dict[int, str] = {}
    missing: List[int] = []
    for user_id in dict.fromkeys(user_ids):
        member = guild.get_member(user_id)
        user = member or client.get_user(user_id)
        if user is not None:
            names[user_id] = user.display_name
        elif _cached_name(user_id):
            names[user_id] = _cached_name(user_id)
        else:
            missing.append(user_id)
    if missing:
        lookups = [asyncio.ensure_future(_fetch_name(client, user_id)) for user_id in missing]
        _name_lookups.update(lookups)
        for lookup in lookups:
            lookup.add_done_callback(_name_lookups.discard)
        await asyncio.wait(lookups, timeout=NAME_WAIT)
        for user_id in missing:
            names[user_id] = _cached_name(user_id) or FORMER_MEMBER
    return names


ATTACH_FILES_NEEDED = "WordCounter needs the Attach Files permission in this channel to post its picture. Ask a server admin to allow it, or try another channel."


async def can_attach(interaction: discord.Interaction, title: str) -> bool:
    """Returns True when WordCounter may attach files in this channel.

    When it can't, this answers the interaction with a private explanation titled `title` and returns False. So call it before deferring, and just return when it is False.
    """
    if interaction.app_permissions.attach_files:
        return True
    await interaction.response.send_message(view=error_view(ATTACH_FILES_NEEDED, title=title), ephemeral=True)
    return False


def activity_line(words: int, messages: int, attachments: int, emojis: int, *, bold: bool = True) -> str:
    mark = "**" if bold else ""
    parts = ((words, "words"), (messages, "messages"), (attachments, "attachments"), (emojis, "emojis"))
    return " · ".join(f"{mark}{value:,}{mark} {label}" for value, label in parts)


def keyword_summary(counts: Iterable[Tuple[str, int]]) -> str:
    return " · ".join(f"`{keyword}` {count:,}" for keyword, count in counts)


def manager_check_error(interaction: discord.Interaction) -> Optional[str]:
    """Returns why the user can't manage this server's settings (not in a server, or no Manage Server permission), or None when they can."""
    if interaction.guild is None:
        return SERVER_ONLY_MESSAGE
    if not interaction.user.guild_permissions.manage_guild:
        return "You need the Manage Server permission to do this."
    return None


def clip_text(text: str, limit: int) -> str:
    """Shortens text to at most `limit` characters, cutting at the last line break that fits and ending with an ellipsis line."""
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit - 2)
    if cut <= 0:
        cut = limit - 2
    return text[:cut].rstrip() + "\n…"


def create_v2_container(
    title: str,
    description: str = "",
    *,
    fields: Optional[Sequence[Tuple[str, str]]] = None,
    footer: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    color: Optional[Union[discord.Colour, int]] = BRAND_COLOR,
    action_rows: Optional[Sequence[discord.ui.ActionRow]] = None,
) -> discord.ui.Container:
    """Builds the standard reply container: a header with a title and description (and an optional thumbnail), then the fields, a footer line and any action rows, each part under a separator.

    The text is clipped to TEXT_BUDGET in total, header first, so long field text is cut instead of the message failing. Fields are left out when fewer than 50 characters of budget remain.
    """
    container = discord.ui.Container(accent_colour=color)
    footer_text = f"-# {footer}" if footer else ""
    budget = TEXT_BUDGET - len(footer_text)

    header = clip_text(f"### {title}\n{description}" if description else f"### {title}", budget)
    budget -= len(header)
    if thumbnail_url:
        container.add_item(
            discord.ui.Section(discord.ui.TextDisplay(header), accessory=discord.ui.Thumbnail(thumbnail_url))
        )
    else:
        container.add_item(discord.ui.TextDisplay(header))

    if fields and budget > 50:
        body = "\n\n".join(f"**{name}**\n{value}" for name, value in fields)
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(clip_text(body, budget)))

    if footer_text:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(footer_text))

    if action_rows:
        container.add_item(discord.ui.Separator())
        for row in action_rows:
            container.add_item(row)

    return container


def create_v2_view(
    title: str,
    description: str = "",
    *,
    fields: Optional[Sequence[Tuple[str, str]]] = None,
    footer: Optional[str] = None,
    thumbnail_url: Optional[str] = None,
    color: Optional[Union[discord.Colour, int]] = BRAND_COLOR,
    action_rows: Optional[Sequence[discord.ui.ActionRow]] = None,
    timeout: Optional[float] = 180.0,
) -> discord.ui.LayoutView:
    """Wraps `create_v2_container` in a LayoutView. A view with no action rows is stopped at once, since nothing in it can be pressed."""
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(
        create_v2_container(
            title=title,
            description=description,
            fields=fields,
            footer=footer,
            thumbnail_url=thumbnail_url,
            color=color,
            action_rows=action_rows,
        )
    )
    if not action_rows:
        view.stop()
    return view


def error_view(description: str, title: str = "Something went wrong", footer: Optional[str] = None) -> discord.ui.LayoutView:
    return create_v2_view(title=title, description=description, footer=footer, color=ERROR_COLOR)


class AuthorOnlyView(discord.ui.LayoutView):
    """A LayoutView that only `author_id` can press, or anyone when it is None. Anyone else gets `denied_message` as a private reply."""
    def __init__(
        self,
        *,
        author_id: Optional[int],
        denied_message: str = "Only the person who opened this can use it.",
        timeout: Optional[float] = 180.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.denied_message = denied_message

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.author_id is None or interaction.user.id == self.author_id:
            return True
        await interaction.response.send_message(view=error_view(self.denied_message), ephemeral=True)
        return False


def tab_rows(tabs: Dict[str, str], current: str, switch_to: Callable[[str], Callback]) -> List[discord.ui.ActionRow]:
    """Builds rows of tab buttons from {key: label}, five to a row as Discord allows. The current tab is highlighted and disabled, and `switch_to(key)` supplies each button's callback."""
    buttons = [
        make_button(
            label,
            discord.ButtonStyle.primary if key == current else discord.ButtonStyle.secondary,
            switch_to(key),
            disabled=key == current,
        )
        for key, label in tabs.items()
    ]
    return [discord.ui.ActionRow(*buttons[start:start + 5]) for start in range(0, len(buttons), 5)]


def channel_family(
    guild: discord.Guild,
    channel_or_id: Union[discord.abc.GuildChannel, discord.Thread, int],
    thread_parent_map: Optional[Dict[int, int]] = None,
) -> Tuple[int, Set[int]]:
    """Returns (effective channel id, related ids) for a channel or an id.

    For a thread the effective id is its parent channel. The related ids are the channel, its parent and the category, which is what the watch and ignore lists are matched against. `thread_parent_map` gives the parents of threads the guild cache doesn't hold, which `/analyze_chat` learns from message history.
    """
    if isinstance(channel_or_id, int):
        channel_id = channel_or_id
        channel = guild.get_channel_or_thread(channel_id)
    else:
        channel = channel_or_id
        channel_id = channel.id

    parent_id: Optional[int] = None
    category_id: Optional[int] = None
    effective_id = channel_id

    if isinstance(channel, discord.Thread):
        parent_id = channel.parent_id
    elif channel is not None:
        category_id = getattr(channel, "category_id", None)
    elif thread_parent_map:
        parent_id = thread_parent_map.get(channel_id)

    if parent_id:
        effective_id = parent_id
        parent = guild.get_channel(parent_id)
        category_id = getattr(parent, "category_id", None)

    return effective_id, {channel_id, effective_id, parent_id, category_id} - {None}


def check_channel_with_config(
    guild: discord.Guild,
    channel_or_id: Union[discord.abc.GuildChannel, discord.Thread, int],
    watched_ids: Set[int],
    ignored_ids: Set[int],
    thread_parent_map: Optional[Dict[int, int]] = None,
) -> Tuple[bool, int]:
    """Returns (is it tracked, the effective channel id the counts are stored under).

    Nothing is tracked while the watch list is empty. The ignore list beats the watch list. Channel id 1 in the watch list stands for the whole server.
    """
    effective_id, related = channel_family(guild, channel_or_id, thread_parent_map)
    if not watched_ids:
        return False, effective_id
    if related & ignored_ids:
        return False, effective_id
    if 1 in watched_ids or related & watched_ids:
        return True, effective_id
    return False, effective_id


def format_channel_or_category(guild: discord.Guild, target_id: int) -> str:
    if target_id == 1:
        return "**Entire server**"
    channel = guild.get_channel(target_id)
    if isinstance(channel, discord.CategoryChannel):
        return f"**{channel.name}** (category)"
    return channel.mention if channel is not None else f"<#{target_id}>"
