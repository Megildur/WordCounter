from __future__ import annotations
import re
from typing import Optional, Sequence, Tuple, List, Set, Union, Dict
import discord

CUSTOM_EMOJI_PATTERN = re.compile(r'<a?:[a-zA-Z0-9_]{2,32}:\d+>')

UNICODE_EMOJI_PATTERN = re.compile(
    r'(?:'
    r'[\U0001F1E6-\U0001F1FF]{2}'
    r'|[\U0001F600-\U0001F64F]'
    r'|[\U0001F300-\U0001F5FF]'
    r'|[\U0001F680-\U0001F6FF]'
    r'|[\U0001F700-\U0001F77F]'
    r'|[\U0001F780-\U0001F7FF]'
    r'|[\U0001F800-\U0001F8FF]'
    r'|[\U0001F900-\U0001F9FF]'
    r'|[\U0001FA00-\U0001FA6F]'
    r'|[\U0001FA70-\U0001FAFF]'
    r'|[\U00002600-\U000026FF]'
    r'|[\U00002700-\U000027BF]'
    r'|[\U00002300-\U000023FF]'
    r'|[\U00002B50\U00002B55\U0000203C\U00002049\U00002139\U00002122\U00003030\U0000303D\U000000A9\U000000AE]'
    r')(?:[\U0001F3FB-\U0001F3FF\uFE0E\uFE0F]|\u200D(?:[\U0001F000-\U0001FAFF\u2600-\u27BF][\U0001F3FB-\U0001F3FF\uFE0E\uFE0F]*))*'
)


def count_emojis(text: str) -> int:
    if not text:
        return 0
    c_count = len(CUSTOM_EMOJI_PATTERN.findall(text))
    cleaned = CUSTOM_EMOJI_PATTERN.sub('', text)
    u_count = len(UNICODE_EMOJI_PATTERN.findall(cleaned))
    return c_count + u_count


BRAND_COLOR = discord.Colour.from_str('#af2202')
SUCCESS_COLOR = discord.Colour.from_rgb(0, 255, 136)
ERROR_COLOR = discord.Colour.from_rgb(255, 68, 68)
WARNING_COLOR = discord.Colour.from_rgb(255, 170, 0)
INFO_COLOR = discord.Colour.blue()


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

    header_text = f"### {title}"
    if description:
        header_text += f"\n{description}"

    if thumbnail_url:
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(header_text),
                accessory=discord.ui.Thumbnail(thumbnail_url),
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(header_text))

    if fields:
        container.add_item(discord.ui.Separator())
        field_chunks: List[str] = []
        current_chunk = ""
        for name, value in fields:
            entry = f"**{name}**\n{value}\n\n"
            if len(current_chunk) + len(entry) > 3500:
                if current_chunk:
                    field_chunks.append(current_chunk.strip())
                current_chunk = entry
            else:
                current_chunk += entry
        if current_chunk.strip():
            field_chunks.append(current_chunk.strip())

        for idx, chunk in enumerate(field_chunks):
            if idx > 0:
                container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(chunk))

    if footer:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# {footer}"))

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
    container = create_v2_container(
        title=title,
        description=description,
        fields=fields,
        footer=footer,
        thumbnail_url=thumbnail_url,
        color=color,
        action_rows=action_rows,
    )
    view.add_item(container)
    if not action_rows:
        view.stop()
    return view


def error_view(description: str, title: str = "❌ Error", footer: Optional[str] = None) -> discord.ui.LayoutView:
    return create_v2_view(title=title, description=description, footer=footer, color=ERROR_COLOR)


def success_view(description: str, title: str = "✅ Success", footer: Optional[str] = None) -> discord.ui.LayoutView:
    return create_v2_view(title=title, description=description, footer=footer, color=SUCCESS_COLOR)


def warning_view(description: str, title: str = "⚠️ Warning", footer: Optional[str] = None) -> discord.ui.LayoutView:
    return create_v2_view(title=title, description=description, footer=footer, color=WARNING_COLOR)


def check_channel_with_config(
    guild: discord.Guild,
    channel_or_id: Union[discord.abc.GuildChannel, discord.Thread, int],
    watched_ids: Set[int],
    ignored_ids: Set[int],
    thread_parent_map: Optional[Dict[int, int]] = None,
) -> Tuple[bool, int]:
    if isinstance(channel_or_id, int):
        channel_id = channel_or_id
        channel_obj = guild.get_channel_or_thread(channel_id)
    else:
        channel_obj = channel_or_id
        channel_id = channel_obj.id

    parent_id: Optional[int] = None
    category_id: Optional[int] = None
    effective_channel_id: int = channel_id

    if channel_obj is not None:
        if isinstance(channel_obj, discord.Thread) or getattr(channel_obj, 'type', None) in (
            discord.ChannelType.public_thread,
            discord.ChannelType.private_thread,
            discord.ChannelType.news_thread,
        ):
            parent_id = getattr(channel_obj, 'parent_id', None)
            if parent_id:
                effective_channel_id = parent_id
            parent_chan = getattr(channel_obj, 'parent', None) or (guild.get_channel(parent_id) if parent_id else None)
            category_id = getattr(parent_chan, 'category_id', None) if parent_chan else None
        else:
            category_id = getattr(channel_obj, 'category_id', None)
    elif thread_parent_map and channel_id in thread_parent_map:
        parent_id = thread_parent_map[channel_id]
        if parent_id:
            effective_channel_id = parent_id
            parent_chan = guild.get_channel(parent_id)
            category_id = getattr(parent_chan, 'category_id', None) if parent_chan else None

    if not watched_ids:
        return False, effective_channel_id

    if (
        channel_id in ignored_ids
        or effective_channel_id in ignored_ids
        or (parent_id and parent_id in ignored_ids)
        or (category_id and category_id in ignored_ids)
    ):
        return False, effective_channel_id

    if 1 in watched_ids:
        return True, effective_channel_id

    if (
        channel_id in watched_ids
        or effective_channel_id in watched_ids
        or (parent_id and parent_id in watched_ids)
        or (category_id and category_id in watched_ids)
    ):
        return True, effective_channel_id

    return False, effective_channel_id


def format_channel_or_category(guild: discord.Guild, target_id: int) -> str:
    if target_id == 1:
        return "🌐 **Entire Server**"
    ch = guild.get_channel(target_id)
    if isinstance(ch, discord.CategoryChannel):
        return f"📁 **{ch.name}** *(Category)*"
    elif ch is not None:
        return f"{ch.mention}"
    return f"<#{target_id}>"