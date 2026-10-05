from __future__ import annotations
from typing import Awaitable, Callable, Dict, Iterable, Optional, Sequence, Set, Tuple, Union
import discord
from cogs.utils.counting import count_emojis

BRAND_COLOR = discord.Colour(0xB62402)
WARNING_COLOR = discord.Colour(0xD26B42)
NEUTRAL_COLOR = discord.Colour(0x908C90)
SUCCESS_COLOR = discord.Colour(0x5C8A3E)
ERROR_COLOR = discord.Colour(0xE04B3C)

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
    if url:
        return discord.ui.Button(label=label, style=discord.ButtonStyle.link, url=url)
    button = discord.ui.Button(label=label, style=style, disabled=disabled)
    if callback is not None:
        button.callback = callback
    return button


def activity_line(words: int, messages: int, attachments: int, emojis: int, *, bold: bool = True) -> str:
    mark = "**" if bold else ""
    parts = ((words, "words"), (messages, "messages"), (attachments, "attachments"), (emojis, "emojis"))
    return " · ".join(f"{mark}{value:,}{mark} {label}" for value, label in parts)


def keyword_summary(counts: Iterable[Tuple[str, int]]) -> str:
    return " · ".join(f"`{keyword}` {count:,}" for keyword, count in counts)


def manager_check_error(interaction: discord.Interaction) -> Optional[str]:
    if interaction.guild is None:
        return SERVER_ONLY_MESSAGE
    if not interaction.user.guild_permissions.manage_guild:
        return "You need the Manage Server permission to do this."
    return None


def clip_text(text: str, limit: int) -> str:
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


def check_channel_with_config(
    guild: discord.Guild,
    channel_or_id: Union[discord.abc.GuildChannel, discord.Thread, int],
    watched_ids: Set[int],
    ignored_ids: Set[int],
    thread_parent_map: Optional[Dict[int, int]] = None,
) -> Tuple[bool, int]:
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

    if not watched_ids:
        return False, effective_id

    related = {channel_id, effective_id, parent_id, category_id} - {None}
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
