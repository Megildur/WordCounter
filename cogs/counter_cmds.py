"""The /settings menu and /leaderboard.

/settings (Manage Server only) opens `SettingsMenuView`, a section menu for the tracking mode, tracked or ignored channels, keywords and count resets. Each change goes through `bot.db` (the `channels`, `ignore` and `keyword` tables and the count tables), then the menu redraws. /leaderboard ranks members for one metric with a podium picture and a tab for each metric.
"""

from __future__ import annotations
import asyncio
import calendar
from typing import Any, Dict, List, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import (
    BRAND_COLOR,
    SERVER_ONLY_MESSAGE,
    SUCCESS_COLOR,
    TRACKING_OFF_MESSAGE,
    WARNING_COLOR,
    Callback,
    activity_line,
    create_v2_container,
    error_view,
    format_channel_or_category,
    make_button,
    manager_check_error,
    can_attach,
    private_followup,
    display_names,
    member_names,
    rank_prefix,
    tab_rows,
)
from cogs.utils import cards
from cogs.utils.menus import SectionMenu
from cogs.utils.server_settings import ANALYSIS_BUSY_MESSAGE, LEADERBOARDS, TRACKABLE_CHANNEL_TYPES, analysis_state, parse_keywords
from paginator import ButtonPaginator

# (key, label, description) per section. Each key has a _populate_<key>_tab method on SettingsMenuView.
SECTIONS = (
    ("overview", "Overview", "Turn tracking on or off and pick a mode"),
    ("channels", "Channels & Categories", "Choose what to track or ignore"),
    ("keywords", "Keywords", "Add, edit or remove keywords"),
    ("reset", "Data & Reset Tools", "Reset counts or allow re-analysis"),
)

class AddKeywordModal(discord.ui.Modal, title="Add keywords"):
    keywords_input = discord.ui.TextInput(
        label="Keywords",
        placeholder="One keyword, or several separated by commas (hello, gg, nice)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, menu_view: SettingsMenuView) -> None:
        super().__init__()
        self.menu_view = menu_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Defer first: a modal must be answered within 3 seconds too, and the database work below can take longer.
        await interaction.response.defer()
        keywords = parse_keywords(self.keywords_input.value)
        if not keywords:
            self.menu_view.status_banner = ("No keywords were entered.", WARNING_COLOR)
            await self.menu_view.refresh_and_edit(interaction)
            return

        added, skipped = await self.menu_view.bot.db.add_keywords(interaction.guild_id, keywords)
        lines = []
        if added:
            lines.append(f"Added {len(added)} keyword(s): `{', '.join(added[:10])}`")
        if skipped:
            lines.append(f"Already tracked: `{', '.join(skipped[:10])}`")
        self.menu_view.status_banner = ("\n".join(lines), SUCCESS_COLOR if added else WARNING_COLOR)
        await self.menu_view.refresh_and_edit(interaction)


class BulkEditKeywordsModal(discord.ui.Modal, title="Edit keyword list"):
    keywords_input = discord.ui.TextInput(
        label="All tracked keywords, separated by commas",
        placeholder="Leave empty to remove every keyword",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )

    def __init__(self, menu_view: SettingsMenuView, current_keywords: List[str]) -> None:
        super().__init__()
        self.menu_view = menu_view
        # Cut to the input's 2000 character limit. Saving a list longer than that loses the keywords that were cut off, and a keyword cut in half is saved as a new one.
        self.keywords_input.default = ", ".join(current_keywords)[:2000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        keywords = parse_keywords(self.keywords_input.value or "")
        await self.menu_view.bot.db.replace_keywords(interaction.guild_id, keywords)
        self.menu_view.status_banner = (f"Keyword list saved. Tracking {len(keywords)} keyword(s).", SUCCESS_COLOR)
        await self.menu_view.refresh_and_edit(interaction)


class SettingsMenuView(SectionMenu):
    """The /settings menu, only usable by the admin who opened it.

    `load_state` reads the server's `channels` rows into `watched_ids`, its `ignore` rows into `ignored_ids` and its keywords on every redraw. Channel id 1 in the watched set means the whole server is tracked, and then the channel list manages the ignore list instead of the watch list. An empty watched set means tracking is off.
    """
    sections = SECTIONS
    title = "Settings"
    closed_hint = "Run /settings to open them again."

    def __init__(self, bot: commands.Bot, guild: discord.Guild, author_id: int) -> None:
        super().__init__(bot, guild, author_id, denied_message="Only the admin who opened these settings can use them.")
        self.selected_reset_user_id: Optional[int] = None
        self.selected_reset_channel_id: Optional[int] = None
        self.watched_ids: Set[int] = set()
        self.ignored_ids: Set[int] = set()
        self.keywords: List[str] = []

    async def load_state(self) -> None:
        self.watched_ids, self.ignored_ids = await self.bot.db.get_guild_tracking_config(self.guild.id)
        self.keywords = await self.bot.db.get_keywords(self.guild.id)

    @property
    def is_whole_server(self) -> bool:
        return 1 in self.watched_ids

    @property
    def is_enabled(self) -> bool:
        return bool(self.watched_ids)

    def description(self) -> str:
        return f"Tracking, channels, keywords and resets for **{self.guild.name}**."

    def _populate_overview_tab(self, container: discord.ui.Container) -> None:
        if not self.is_enabled:
            mode = "Off. Nothing is being counted."
            scope_title, scope_value = "Tracked channels and categories", "None"
        elif self.is_whole_server:
            mode = "Whole server. Every channel is counted except ignored ones."
            scope_title = "Ignored"
            scope_value = (
                ", ".join(format_channel_or_category(self.guild, cid) for cid in sorted(self.ignored_ids))
                if self.ignored_ids
                else "Nothing ignored"
            )
        else:
            mode = "Specific channels. Only the channels and categories you pick are counted."
            scope_title = "Tracked channels and categories"
            specific_ids = [cid for cid in sorted(self.watched_ids) if cid != 1]
            scope_value = ", ".join(format_channel_or_category(self.guild, cid) for cid in specific_ids) or "None picked yet"

        keyword_preview = ", ".join(f"`{k}`" for k in self.keywords[:20]) or "None"
        if len(self.keywords) > 20:
            keyword_preview += f" and {len(self.keywords) - 20} more"

        container.add_item(
            discord.ui.TextDisplay(
                f"**Mode:** {mode}\n\n**{scope_title}:** {scope_value}\n\n"
                f"**Keywords ({len(self.keywords)}):** {keyword_preview}"
            )
        )

        if self.pending_confirmation == "whole_server":
            self._confirmation(
                container,
                "**Switch to whole server?**\nThis replaces your list of specific channels with server-wide tracking.",
                "Switch",
                discord.ButtonStyle.success,
                self._confirm_whole_server,
            )
            return
        if self.pending_confirmation == "disable_server":
            self._confirmation(
                container,
                "**Turn off tracking?**\nNew messages won't be counted. Existing stats stay.",
                "Turn off",
                discord.ButtonStyle.danger,
                self._confirm_disable_server,
            )
            return

        container.add_item(
            discord.ui.ActionRow(
                make_button(
                    "Track whole server",
                    discord.ButtonStyle.secondary if self.is_whole_server else discord.ButtonStyle.success,
                    self._btn_enable_whole_server,
                    disabled=self.is_whole_server,
                ),
                make_button("Channels", discord.ButtonStyle.primary, self._goto("channels")),
                make_button("Keywords", discord.ButtonStyle.primary, self._goto("keywords")),
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                make_button("Data & resets", discord.ButtonStyle.primary, self._goto("reset")),
                make_button("Turn off tracking", discord.ButtonStyle.danger, self._prompt("disable_server"), disabled=not self.is_enabled),
            )
        )

    def _populate_channels_tab(self, container: discord.ui.Container) -> None:
        if self.is_whole_server:
            header = (
                "### Ignored channels\n"
                "Whole server mode is on, so everything is counted unless you ignore it here. Ignoring a category ignores every channel in it."
            )
            active_list = sorted(self.ignored_ids)
            list_label = "Ignored"
            add_placeholder = "Add channels or categories to ignore"
            remove_placeholder = "Remove from the ignore list"
        else:
            header = (
                "### Tracked channels\n"
                "Only these channels and categories are counted. Picking a category includes every channel and thread inside it."
            )
            active_list = [cid for cid in sorted(self.watched_ids) if cid != 1]
            list_label = "Tracked"
            add_placeholder = "Add channels or categories to track"
            remove_placeholder = "Remove from the tracked list"

        listed = "\n".join(f"- {format_channel_or_category(self.guild, cid)}" for cid in active_list) or "None yet."
        container.add_item(discord.ui.TextDisplay(f"{header}\n\n**{list_label} ({len(active_list)}):**\n{listed}"))

        channel_select = discord.ui.ChannelSelect(
            placeholder=add_placeholder,
            channel_types=[*TRACKABLE_CHANNEL_TYPES, discord.ChannelType.category],
            min_values=1,
            max_values=10,
        )
        channel_select.callback = self._on_channels_added
        container.add_item(discord.ui.ActionRow(channel_select))

        if active_list:
            options = []
            # A select holds at most 25 options, so only the first 25 can be removed one by one. Clear list covers the rest.
            for cid in active_list[:25]:
                channel = self.guild.get_channel(cid)
                if isinstance(channel, discord.CategoryChannel):
                    label = f"{channel.name} (category)"
                elif channel is not None:
                    label = f"#{channel.name}"
                else:
                    label = f"Deleted channel {cid}"
                options.append(discord.SelectOption(label=label[:100], value=str(cid)))
            remove_select = discord.ui.Select(
                placeholder=remove_placeholder, options=options, min_values=1, max_values=len(options)
            )
            remove_select.callback = self._on_channels_removed
            container.add_item(discord.ui.ActionRow(remove_select))

        if self.is_whole_server:
            switch = make_button("Switch to specific channels", discord.ButtonStyle.primary, self._switch_to_specific_mode)
        else:
            switch = make_button("Switch to whole server", discord.ButtonStyle.success, self._confirm_whole_server)
        container.add_item(
            discord.ui.ActionRow(
                switch,
                make_button("Clear list", discord.ButtonStyle.danger, self._clear_all_channels, disabled=not active_list),
            )
        )

    def _populate_keywords_tab(self, container: discord.ui.Container) -> None:
        listed = "\n".join(f"- `{kw}`" for kw in self.keywords[:30]) or "None yet."
        if len(self.keywords) > 30:
            listed += f"\n…and {len(self.keywords) - 30} more"
        container.add_item(
            discord.ui.TextDisplay(
                "### Keywords\nA keyword is counted every time someone says it in a tracked channel.\n\n"
                f"**Tracked ({len(self.keywords)}):**\n{listed}"
            )
        )

        if self.keywords:
            # At most 25 options, and a label or value over 100 characters is cut. Edit list and Remove all handle the rest.
            options = [discord.SelectOption(label=kw[:100], value=kw[:100]) for kw in self.keywords[:25]]
            remove_select = discord.ui.Select(
                placeholder="Remove keywords", options=options, min_values=1, max_values=len(options)
            )
            remove_select.callback = self._on_keywords_removed
            container.add_item(discord.ui.ActionRow(remove_select))

        container.add_item(
            discord.ui.ActionRow(
                make_button("Add", discord.ButtonStyle.success, self._open_add_keyword_modal),
                make_button("Edit list", discord.ButtonStyle.primary, self._open_bulk_keyword_modal),
                make_button("Remove all", discord.ButtonStyle.danger, self._clear_all_keywords, disabled=not self.keywords),
            )
        )

    def _populate_reset_tab(self, container: discord.ui.Container) -> None:
        member = f"<@{self.selected_reset_user_id}>" if self.selected_reset_user_id else "Everyone"
        channel = f"<#{self.selected_reset_channel_id}>" if self.selected_reset_channel_id else "Whole server"
        container.add_item(
            discord.ui.TextDisplay(
                "### Data & resets\nPick a member, a channel, or both, then choose what to do.\n\n"
                f"**Member:** {member}\n**Channel:** {channel}"
            )
        )

        if self.pending_confirmation == "wipe_server":
            self._confirmation(
                container,
                "**Reset everything?**\n"
                "This deletes all of this server's stats (words, messages, attachments, emojis and keywords), clears who has been analyzed, and resets tracking settings and keywords. It can't be undone.",
                "Reset everything",
                discord.ButtonStyle.danger,
                self._confirm_wipe_server,
            )
            return
        if self.pending_confirmation == "scoped_reset":
            self._confirmation(
                container,
                f"**Reset all counts for {self._reset_scope_text()}?**\n"
                "Words, messages, attachments, emojis, keywords and monthly history are deleted for this scope. It can't be undone.",
                "Reset all counts",
                discord.ButtonStyle.danger,
                self._execute_scoped_reset,
            )
            return
        if self.pending_confirmation == "reanalysis":
            if self.selected_reset_user_id is not None:
                question = (
                    f"**Re-analyze <@{self.selected_reset_user_id}> from scratch?**\n"
                    "Their counts are cleared now. The next `/analyze_chat single_user` rebuilds their full history, so nothing is counted twice."
                )
            else:
                question = (
                    "**Re-analyze everyone from scratch?**\n"
                    "Every member's counts are cleared now (settings and keywords stay). The next `/analyze_chat whole_server` rebuilds the full history, so nothing is counted twice."
                )
            self._confirmation(container, question, "Clear and allow re-analysis", discord.ButtonStyle.danger, self._execute_reanalysis)
            return

        # min_values=0 lets the admin clear a pick, which falls back to everyone for the member and the whole server for the channel.
        user_select = discord.ui.UserSelect(placeholder="Pick a member", min_values=0, max_values=1)
        user_select.callback = self._on_reset_user_selected
        container.add_item(discord.ui.ActionRow(user_select))

        channel_select = discord.ui.ChannelSelect(
            placeholder="Pick a channel (optional)",
            channel_types=TRACKABLE_CHANNEL_TYPES,
            min_values=0,
            max_values=1,
        )
        channel_select.callback = self._on_reset_channel_selected
        container.add_item(discord.ui.ActionRow(channel_select))

        nothing_selected = self.selected_reset_user_id is None and self.selected_reset_channel_id is None
        container.add_item(
            discord.ui.ActionRow(
                make_button("Reset all counts", discord.ButtonStyle.primary, self._prompt("scoped_reset"), disabled=nothing_selected),
                make_button(
                    "Allow re-analysis" if self.selected_reset_user_id else "Allow re-analysis for everyone",
                    discord.ButtonStyle.secondary,
                    self._prompt("reanalysis"),
                ),
                make_button("Reset everything", discord.ButtonStyle.danger, self._prompt("wipe_server")),
            )
        )

    async def _btn_enable_whole_server(self, interaction: discord.Interaction) -> None:
        # Whole server replaces a list of picked channels, so ask first. With no picked channels there is nothing to lose.
        if any(cid != 1 for cid in self.watched_ids):
            await self._prompt("whole_server")(interaction)
        else:
            await self._confirm_whole_server(interaction)

    async def _confirm_whole_server(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.enable_whole_server(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("Whole server mode is on. Every channel is counted now.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _switch_to_specific_mode(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        # This only removes the whole-server row, so with no channels picked yet nothing is counted until some are added.
        await self.bot.db.switch_to_specific_mode(self.guild.id)
        self.status_banner = ("Switched to specific channels. Pick what to track below.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _confirm_disable_server(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.disable_server_tracking(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("Tracking is off.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_channels_added(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        selected = self._selected_ids(interaction)
        listed = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected)
        # In whole server mode the picked channels go on the ignore list, otherwise on the watch list.
        if self.is_whole_server:
            await self.bot.db.add_ignored_channels(self.guild.id, selected)
            self.status_banner = (f"Now ignoring: {listed}", SUCCESS_COLOR)
        else:
            await self.bot.db.add_watched_channels(self.guild.id, selected)
            self.status_banner = (f"Now tracking: {listed}", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_channels_removed(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        selected = self._selected_ids(interaction)
        listed = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected)
        if self.is_whole_server:
            await self.bot.db.remove_ignored_channels(self.guild.id, selected)
            self.status_banner = (f"No longer ignoring: {listed}", SUCCESS_COLOR)
        else:
            await self.bot.db.remove_watched_channels(self.guild.id, selected)
            self.status_banner = (f"No longer tracking: {listed}", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _clear_all_channels(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        if self.is_whole_server:
            await self.bot.db.clear_ignored_channels(self.guild.id)
            self.status_banner = ("Ignore list cleared.", SUCCESS_COLOR)
        else:
            await self.bot.db.clear_watched_channels(self.guild.id)
            self.status_banner = ("Tracked list cleared.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _open_add_keyword_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddKeywordModal(self))

    async def _open_bulk_keyword_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BulkEditKeywordsModal(self, self.keywords))

    async def _on_keywords_removed(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        removed = interaction.data.get("values", [])
        await self.bot.db.remove_keywords(self.guild.id, removed)
        self.status_banner = (f"Removed {len(removed)} keyword(s): `{', '.join(removed)}`", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _clear_all_keywords(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.clear_keywords(self.guild.id)
        self.status_banner = ("All keywords removed.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_reset_user_selected(self, interaction: discord.Interaction) -> None:
        selected = self._selected_ids(interaction)
        self.selected_reset_user_id = selected[0] if selected else None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    async def _on_reset_channel_selected(self, interaction: discord.Interaction) -> None:
        selected = self._selected_ids(interaction)
        self.selected_reset_channel_id = selected[0] if selected else None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    def _reset_scope_text(self) -> str:
        uid, cid = self.selected_reset_user_id, self.selected_reset_channel_id
        if uid is not None and cid is not None:
            return f"<@{uid}> in <#{cid}>"
        if uid is not None:
            return f"<@{uid}> across the server"
        return f"everyone in <#{cid}>"

    async def _execute_scoped_reset(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.pending_confirmation = None
        # Busy means an analysis is running or a saved run exists, and the reset is refused while either holds.
        _, busy = await analysis_state(self.bot, self.guild.id)
        if busy:
            self.status_banner = (ANALYSIS_BUSY_MESSAGE, WARNING_COLOR)
        elif self.selected_reset_user_id is not None or self.selected_reset_channel_id is not None:
            await self.bot.db.reset_counts(self.guild.id, self.selected_reset_user_id, self.selected_reset_channel_id)
            self.status_banner = (f"Reset all counts for {self._reset_scope_text()}.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _execute_reanalysis(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.pending_confirmation = None
        uid = self.selected_reset_user_id
        _, busy = await analysis_state(self.bot, self.guild.id)
        if busy:
            self.status_banner = (ANALYSIS_BUSY_MESSAGE, WARNING_COLOR)
        else:
            await self.bot.db.allow_reanalysis(self.guild.id, uid)
            if uid is not None:
                self.status_banner = (
                    f"Cleared <@{uid}>'s counts. Run `/analyze_chat single_user` to rebuild them.",
                    SUCCESS_COLOR,
                )
            else:
                self.status_banner = (
                    "Cleared every member's counts. Run `/analyze_chat whole_server` to rebuild them.",
                    SUCCESS_COLOR,
                )
        await self.refresh_and_edit(interaction)

    async def _confirm_wipe_server(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.pending_confirmation = None
        # Unlike the other resets, only a run going right now blocks this one, because it also deletes an interrupted run's saved progress.
        running, _ = await analysis_state(self.bot, self.guild.id)
        if running:
            self.status_banner = (ANALYSIS_BUSY_MESSAGE, WARNING_COLOR)
            await self.refresh_and_edit(interaction)
            return
        await self.bot.db.reset_entire_server(self.guild.id)
        self.selected_reset_user_id = None
        self.selected_reset_channel_id = None
        self.active_tab = "overview"
        self.status_banner = ("Server reset. All stats, analysis history, channels and keywords were cleared.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

class LeaderboardPaginator(ButtonPaginator):
    """Pages of ranked members for one metric, with a podium picture on the first page and a row of buttons to switch metric.

    Build it, `await load(category)` to query the database and set the pages, then `start`. Switching metric calls `load` again.
    """
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        author_id: int,
        channel: Optional[discord.TextChannel] = None,
    ) -> None:
        super().__init__([], author_id=author_id)
        self.denied_message = "Only the person who ran /leaderboard can use these buttons."
        self.bot = bot
        self.guild = guild
        self.channel = channel
        self.category = "words"
        self.entry_count = 0
        self.podium: Optional[bytes] = None
        self.history: List[Tuple[str, str]] = []

    async def load(self, category: str) -> None:
        self.category = category
        self.current_page = 0
        channel_id = self.channel.id if self.channel else None

        self.history = []
        if self.channel is not None:
            rows = await self.bot.db.get_channel_monthly_breakdown(self.guild.id, self.channel.id)
            if rows:
                lines = [
                    f"**{calendar.month_name[month]} {year}**: {activity_line(words, messages, attachments, emojis, bold=False)}"
                    for year, month, words, messages, attachments, emojis in rows
                ]
                self.history = [("Monthly history, newest first", "\n".join(lines))]

        if category == "keywords":
            entries: List = await self.bot.db.get_keyword_leaders(self.guild.id, channel_id)
            size = 5
        else:
            entries = await self.bot.db.get_leaderboard(category, self.guild.id, channel_id)
            size = 10
        self.entry_count = len(entries)
        self.set_pages([entries[i:i + size] for i in range(0, len(entries), size)] or [[]])
        self.podium = await self._draw_podium(entries[:3]) if category != "keywords" and entries else None

    async def _draw_podium(self, top: List[Tuple[int, int]]) -> bytes:
        names = await display_names(self.bot, self.guild, [uid for uid, _ in top])
        people = [self.guild.get_member(uid) or self.bot.get_user(uid) for uid, _ in top]
        faces = await asyncio.gather(*(cards.avatar(person) for person in people))
        entries = [(names[uid], face, f"{count:,} {self.category}") for (uid, count), face in zip(top, faces)]
        # Pillow is synchronous, so the picture is drawn in a thread.
        return await asyncio.to_thread(cards.podium, entries)

    async def get_page_kwargs(self, page: Any, skip_formatting: bool = False) -> Dict[str, Any]:
        kwargs = await super().get_page_kwargs(page, skip_formatting)
        # A new File for every send or edit, because a File can only be sent once. Only the first page carries the picture.
        if self.podium and self.current_page == 0:
            kwargs["files"] = [cards.picture_file(self.podium, cards.PODIUM_FILE)]
        return kwargs

    async def update_page(self, interaction: discord.Interaction) -> None:
        if self.message is None:
            self.message = interaction.message
        # Name lookups and the podium can run past 3 seconds, so answer the click first and edit the message afterwards.
        if not interaction.response.is_done():
            await interaction.response.defer()
        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        if "files" in kwargs:
            kwargs["attachments"] = kwargs.pop("files")
        await interaction.edit_original_response(**kwargs)

    async def format_page(self, chunk: List) -> discord.ui.Container:
        where = self.channel.mention if self.channel else "this server"
        fields: List[Tuple[str, str]] = []
        if self.category == "keywords":
            description = f"Who says each keyword most in {where}"
            if not chunk:
                description += "\n\nNo keyword use recorded yet."
            names = await member_names(self.bot, self.guild, [uid for _, users in chunk for uid, _ in users[:10]])
            for keyword, users in chunk:
                top = users[:10]
                fields.append((
                    f'"{keyword}"',
                    "\n".join(f"{rank_prefix(rank)} **{names[uid]}** · {count:,}" for rank, (uid, count) in enumerate(top, start=1)),
                ))
            footer = f"{self.entry_count} keyword(s)"
        else:
            start = self.current_page * 10 + 1
            names = await member_names(self.bot, self.guild, [uid for uid, _ in chunk])
            lines = [
                f"{rank_prefix(rank)} **{names[uid]}** · {count:,} {self.category}"
                for rank, (uid, count) in enumerate(chunk, start=start)
            ]
            description = f"Top members in {where}\n\n" + ("\n".join(lines) or "Nothing recorded yet.")
            footer = f"{self.entry_count} member(s)"

        container = create_v2_container(
            title=f"{LEADERBOARDS[self.category]} leaderboard",
            description=description,
            fields=(fields + self.history) or None,
            footer=footer,
            color=BRAND_COLOR,
        )
        if not (self.podium and self.current_page == 0):
            return container
        picture = cards.gallery(cards.PODIUM_FILE, f"The top three for {self.category} on podium blocks on Cradle Station's observation deck")
        return discord.ui.Container(picture, *container.children, accent_colour=container.accent_colour)

    def extra_rows(self) -> List[discord.ui.ActionRow]:
        return tab_rows(LEADERBOARDS, self.category, self._switch_to)

    def _switch_to(self, category: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await self.load(category)
            await self.update_page(interaction)

        return callback


class Counter_Cmds(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="settings",
        description="Choose what gets tracked, manage keywords and reset data",
        extras={
            "details": "Opens WordCounter's settings privately. Choose how tracking works: the whole server with an ignore list, only the channels and categories you pick, or off. Tracking or ignoring a category covers every channel in it. Add and remove keywords, then use the Data & Reset Tools to reset counts for a member, a channel or both, let a member be re-analyzed from scratch, or reset the whole server. Nothing is counted until you pick channels or turn on whole server mode. Only the person who opened it can press its buttons.",
            "user_perms": (
                "Manage Server"
            ),
        },
    )
    @app_commands.default_permissions(manage_guild=True)
    async def settings_command(self, interaction: discord.Interaction) -> None:
        reason = manager_check_error(interaction)
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return

        # Reading the settings can take longer than the 3 seconds Discord gives a first reply, so defer first. The menu is private either way.
        await interaction.response.defer(ephemeral=True)
        menu = SettingsMenuView(self.bot, interaction.guild, interaction.user.id)
        await menu.load_state()
        menu.build_ui()
        await interaction.followup.send(view=menu, ephemeral=True, wait=True)

    @app_commands.command(
        name="leaderboard",
        description="Leaderboards for words, messages, attachments, emojis and keywords",
        extras={
            "details": "Shows who leads the server: the top three on a pixel-art podium on Cradle Station's observation deck, with their pictures, then ten members a page. Buttons switch between words, messages, attachments (files, stickers and links), emojis, keywords and swears, and Previous and Next page through. The keywords tab shows who says each tracked keyword most. Pick a channel to rank only what was said there, with that channel's month-by-month history under the list. Members who left show by name. Only the person who ran it can press its buttons, and nobody is pinged. It works once tracking is on in /settings.",
            "bot_perms": (
                "Attach Files, for the podium"
            ),
        },
    )
    @app_commands.describe(channel="Only count activity in this channel")
    async def leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(view=error_view(SERVER_ONLY_MESSAGE), ephemeral=True)
            return
        # can_attach answers the interaction itself when it fails, so it comes before the defer.
        if not await can_attach(interaction, "Can't show the leaderboard here"):
            return
        # The tracking check, loading the rankings and drawing the podium can take longer than the 3 seconds a slash command gets, so defer before any of them.
        await interaction.response.defer()
        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            await private_followup(interaction, error_view(TRACKING_OFF_MESSAGE))
            return
        paginator = LeaderboardPaginator(self.bot, interaction.guild, interaction.user.id, channel)
        await paginator.load("words")
        await paginator.start(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counter_Cmds(bot))
