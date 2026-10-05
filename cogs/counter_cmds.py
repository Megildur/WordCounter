from __future__ import annotations
import calendar
from collections import defaultdict
from typing import Dict, List, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import (
    BRAND_COLOR,
    NEUTRAL_COLOR,
    SERVER_ONLY_MESSAGE,
    SUCCESS_COLOR,
    TRACKING_OFF_MESSAGE,
    WARNING_COLOR,
    AuthorOnlyView,
    Callback,
    activity_line,
    create_v2_container,
    create_v2_view,
    error_view,
    format_channel_or_category,
    make_button,
    manager_check_error,
)
from paginator import ButtonPaginator

ANALYSIS_BUSY_MESSAGE = "An analysis is running in this server. Try again once it finishes."

SECTIONS = (
    ("overview", "Overview", "Turn tracking on or off and pick a mode"),
    ("channels", "Channels & Categories", "Choose what to track or ignore"),
    ("keywords", "Keywords", "Add, edit or remove keywords"),
    ("reset", "Data & Reset Tools", "Reset counts or allow re-analysis"),
)

LEADERBOARDS = {
    "words": "Words",
    "messages": "Messages",
    "attachments": "Attachments",
    "emojis": "Emojis",
    "keywords": "Keywords",
}

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}

TRACKABLE_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.forum,
    discord.ChannelType.voice,
]


def parse_keywords(raw: str) -> List[str]:
    parts = [part.strip().lower() for line in raw.splitlines() for part in line.split(",") if part.strip()]
    return list(dict.fromkeys(parts))


def rank_prefix(rank: int) -> str:
    return MEDALS.get(rank, f"**{rank}.**")


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
        self.keywords_input.default = ", ".join(current_keywords)[:2000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        keywords = parse_keywords(self.keywords_input.value or "")
        await self.menu_view.bot.db.replace_keywords(interaction.guild_id, keywords)
        self.menu_view.status_banner = (f"Keyword list saved. Tracking {len(keywords)} keyword(s).", SUCCESS_COLOR)
        await self.menu_view.refresh_and_edit(interaction)


class SettingsMenuView(AuthorOnlyView):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, author_id: int) -> None:
        super().__init__(
            author_id=author_id,
            denied_message="Only the admin who opened these settings can use them.",
            timeout=300.0,
        )
        self.bot = bot
        self.guild = guild
        self.active_tab = "overview"
        self.pending_confirmation: Optional[str] = None
        self.status_banner: Optional[Tuple[str, discord.Colour]] = None
        self.selected_reset_user_id: Optional[int] = None
        self.selected_reset_channel_id: Optional[int] = None
        self.watched_ids: Set[int] = set()
        self.ignored_ids: Set[int] = set()
        self.keywords: List[str] = []

    async def _analysis_state(self) -> Tuple[bool, bool]:
        cog = self.bot.get_cog("AnalyzeChat")
        running = cog is not None and self.guild.id in cog.running_guilds
        return running, running or await self.bot.db.has_analysis_run(self.guild.id)

    async def load_state(self) -> None:
        self.watched_ids, self.ignored_ids = await self.bot.db.get_guild_tracking_config(self.guild.id)
        self.keywords = await self.bot.db.get_keywords(self.guild.id)

    @property
    def is_whole_server(self) -> bool:
        return 1 in self.watched_ids

    @property
    def is_enabled(self) -> bool:
        return bool(self.watched_ids)

    def _confirmation(
        self, container: discord.ui.Container, text: str, confirm_label: str, style: discord.ButtonStyle, on_confirm: Callback
    ) -> None:
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(text))
        container.add_item(
            discord.ui.ActionRow(
                make_button(confirm_label, style, on_confirm),
                make_button("Cancel", discord.ButtonStyle.secondary, self._cancel_confirmation),
            )
        )

    def build_ui(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=self.status_banner[1] if self.status_banner else BRAND_COLOR)

        header = discord.ui.TextDisplay(f"### Settings\nTracking, channels, keywords and resets for **{self.guild.name}**.")
        if self.guild.icon:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(self.guild.icon.url)))
        else:
            container.add_item(header)

        if self.status_banner:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(self.status_banner[0]))

        navigation = discord.ui.Select(
            placeholder="Go to a section",
            options=[
                discord.SelectOption(label=label, value=value, description=description, default=self.active_tab == value)
                for value, label, description in SECTIONS
            ],
        )
        navigation.callback = self._on_tab_select
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(navigation))
        container.add_item(discord.ui.Separator())

        {
            "overview": self._populate_overview_tab,
            "channels": self._populate_channels_tab,
            "keywords": self._populate_keywords_tab,
            "reset": self._populate_reset_tab,
        }[self.active_tab](container)

        bottom = []
        if self.active_tab != "overview":
            bottom.append(make_button("Back to overview", discord.ButtonStyle.primary, self._on_back_to_overview))
        bottom.append(make_button("Close", discord.ButtonStyle.secondary, self._on_close_menu))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(*bottom))
        self.add_item(container)

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
                make_button("Turn off tracking", discord.ButtonStyle.danger, self._btn_disable_tracking, disabled=not self.is_enabled),
            )
        )

    def _populate_channels_tab(self, container: discord.ui.Container) -> None:
        if self.is_whole_server:
            header = (
                "### Ignored channels\n"
                "Whole server mode is on, so everything is counted unless you ignore it here. "
                "Ignoring a category ignores every channel in it."
            )
            active_list = sorted(self.ignored_ids)
            list_label = "Ignored"
            add_placeholder = "Add channels or categories to ignore"
            remove_placeholder = "Remove from the ignore list"
        else:
            header = (
                "### Tracked channels\n"
                "Only these channels and categories are counted. "
                "Picking a category includes every channel and thread inside it."
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
                "This deletes all of this server's stats (words, messages, attachments, emojis and keywords), "
                "clears who has been analyzed, and resets tracking settings and keywords. It can't be undone.",
                "Reset everything",
                discord.ButtonStyle.danger,
                self._confirm_wipe_server,
            )
            return
        if self.pending_confirmation == "scoped_reset":
            self._confirmation(
                container,
                f"**Reset all counts for {self._reset_scope_text()}?**\n"
                "Words, messages, attachments, emojis, keywords and monthly history are deleted for this scope. "
                "It can't be undone.",
                "Reset all counts",
                discord.ButtonStyle.danger,
                self._execute_scoped_reset,
            )
            return
        if self.pending_confirmation == "reanalysis":
            if self.selected_reset_user_id is not None:
                question = (
                    f"**Re-analyze <@{self.selected_reset_user_id}> from scratch?**\n"
                    "Their counts are cleared now. The next `/analyze_chat single_user` rebuilds their full history, "
                    "so nothing is counted twice."
                )
            else:
                question = (
                    "**Re-analyze everyone from scratch?**\n"
                    "Every member's counts are cleared now (settings and keywords stay). The next "
                    "`/analyze_chat whole_server` rebuilds the full history, so nothing is counted twice."
                )
            self._confirmation(container, question, "Clear and allow re-analysis", discord.ButtonStyle.danger, self._execute_reanalysis)
            return

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
                make_button("Reset all counts", discord.ButtonStyle.primary, self._prompt_scoped_reset, disabled=nothing_selected),
                make_button(
                    "Allow re-analysis" if self.selected_reset_user_id else "Allow re-analysis for everyone",
                    discord.ButtonStyle.secondary,
                    self._prompt_reanalysis,
                ),
                make_button("Reset everything", discord.ButtonStyle.danger, self._prompt_wipe_server),
            )
        )

    def _goto(self, tab: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            self.active_tab = tab
            self.pending_confirmation = None
            self.status_banner = None
            await self.refresh_and_edit(interaction)

        return callback

    async def _on_tab_select(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or ["overview"]
        await self._goto(values[0])(interaction)

    async def _on_back_to_overview(self, interaction: discord.Interaction) -> None:
        await self._goto("overview")(interaction)

    async def _btn_enable_whole_server(self, interaction: discord.Interaction) -> None:
        if any(cid != 1 for cid in self.watched_ids):
            self.pending_confirmation = "whole_server"
            await self.refresh_and_edit(interaction)
        else:
            await self._confirm_whole_server(interaction)

    async def _confirm_whole_server(self, interaction: discord.Interaction) -> None:
        await self.bot.db.enable_whole_server(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("Whole server mode is on. Every channel is counted now.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _switch_to_specific_mode(self, interaction: discord.Interaction) -> None:
        await self.bot.db.switch_to_specific_mode(self.guild.id)
        self.status_banner = ("Switched to specific channels. Pick what to track below.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _btn_disable_tracking(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "disable_server"
        await self.refresh_and_edit(interaction)

    async def _confirm_disable_server(self, interaction: discord.Interaction) -> None:
        await self.bot.db.disable_server_tracking(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("Tracking is off.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _cancel_confirmation(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        self.status_banner = ("Cancelled.", NEUTRAL_COLOR)
        await self.refresh_and_edit(interaction)

    def _selected_ids(self, interaction: discord.Interaction) -> List[int]:
        return [int(value) for value in interaction.data.get("values", [])]

    async def _on_channels_added(self, interaction: discord.Interaction) -> None:
        selected = self._selected_ids(interaction)
        listed = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected)
        if self.is_whole_server:
            await self.bot.db.add_ignored_channels(self.guild.id, selected)
            self.status_banner = (f"Now ignoring: {listed}", SUCCESS_COLOR)
        else:
            await self.bot.db.add_watched_channels(self.guild.id, selected)
            self.status_banner = (f"Now tracking: {listed}", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_channels_removed(self, interaction: discord.Interaction) -> None:
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
        removed = interaction.data.get("values", [])
        await self.bot.db.remove_keywords(self.guild.id, removed)
        self.status_banner = (f"Removed {len(removed)} keyword(s): `{', '.join(removed)}`", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _clear_all_keywords(self, interaction: discord.Interaction) -> None:
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

    async def _prompt_scoped_reset(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "scoped_reset"
        await self.refresh_and_edit(interaction)

    async def _execute_scoped_reset(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        _, busy = await self._analysis_state()
        if busy:
            self.status_banner = (ANALYSIS_BUSY_MESSAGE, WARNING_COLOR)
        elif self.selected_reset_user_id is not None or self.selected_reset_channel_id is not None:
            await self.bot.db.reset_counts(self.guild.id, self.selected_reset_user_id, self.selected_reset_channel_id)
            self.status_banner = (f"Reset all counts for {self._reset_scope_text()}.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _prompt_reanalysis(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "reanalysis"
        await self.refresh_and_edit(interaction)

    async def _execute_reanalysis(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        uid = self.selected_reset_user_id
        _, busy = await self._analysis_state()
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

    async def _prompt_wipe_server(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "wipe_server"
        await self.refresh_and_edit(interaction)

    async def _confirm_wipe_server(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        running, _ = await self._analysis_state()
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

    async def refresh_and_edit(self, interaction: discord.Interaction) -> None:
        await self.load_state()
        self.build_ui()
        await interaction.response.edit_message(view=self)

    async def _on_close_menu(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            await interaction.edit_original_response(
                view=create_v2_view("Settings closed", "Run /settings to open them again.", color=NEUTRAL_COLOR)
            )


class LeaderboardPaginator(ButtonPaginator):
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
        self.history: List[Tuple[str, str]] = []

    def _name(self, user_id: int) -> str:
        member = self.guild.get_member(user_id)
        return member.display_name if member else f"<@{user_id}>"

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
            per_keyword: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
            for keyword, count, user_id in await self.bot.db.get_keyword_leaderboard(self.guild.id, channel_id):
                per_keyword[keyword][user_id] += count
            entries: List = sorted(per_keyword.items())
            size = 5
        else:
            entries = await self.bot.db.get_leaderboard(category, self.guild.id, channel_id)
            size = 10
        self.entry_count = len(entries)
        self.set_pages([entries[i:i + size] for i in range(0, len(entries), size)] or [[]])

    def format_page(self, chunk: List) -> discord.ui.Container:
        where = self.channel.mention if self.channel else "this server"
        fields: List[Tuple[str, str]] = []
        if self.category == "keywords":
            description = f"Who says each keyword most in {where}"
            if not chunk:
                description += "\n\nNo keyword use recorded yet."
            for keyword, users in chunk:
                top = sorted(users.items(), key=lambda item: item[1], reverse=True)[:10]
                fields.append((
                    f'"{keyword}"',
                    "\n".join(f"{rank_prefix(rank)} **{self._name(uid)}** · {count:,}" for rank, (uid, count) in enumerate(top, start=1)),
                ))
            footer = f"{self.entry_count} keyword(s)"
        else:
            start = self.current_page * 10 + 1
            lines = [
                f"{rank_prefix(rank)} **{self._name(uid)}** · {count:,} {self.category}"
                for rank, (uid, count) in enumerate(chunk, start=start)
            ]
            description = f"Top members in {where}\n\n" + ("\n".join(lines) or "Nothing recorded yet.")
            footer = f"{self.entry_count} member(s)"

        return create_v2_container(
            title=f"{LEADERBOARDS[self.category]} leaderboard",
            description=description,
            fields=(fields + self.history) or None,
            footer=footer,
            color=BRAND_COLOR,
        )

    def extra_rows(self) -> List[discord.ui.ActionRow]:
        return [
            discord.ui.ActionRow(*(
                make_button(
                    label,
                    discord.ButtonStyle.primary if key == self.category else discord.ButtonStyle.secondary,
                    self._switch_to(key),
                    disabled=key == self.category,
                )
                for key, label in LEADERBOARDS.items()
            ))
        ]

    def _switch_to(self, category: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await self.load(category)
            await self.update_page(interaction)

        return callback


class Counter_Cmds(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="settings", description="Choose what gets tracked, manage keywords and reset data")
    @app_commands.default_permissions(manage_guild=True)
    async def settings_command(self, interaction: discord.Interaction) -> None:
        reason = manager_check_error(interaction)
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return

        menu = SettingsMenuView(self.bot, interaction.guild, interaction.user.id)
        await menu.load_state()
        menu.build_ui()
        await interaction.response.send_message(view=menu, ephemeral=True)

    @app_commands.command(name="leaderboard", description="Leaderboards for words, messages, attachments, emojis and keywords")
    @app_commands.describe(channel="Only count activity in this channel")
    async def leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(view=error_view(SERVER_ONLY_MESSAGE), ephemeral=True)
            return
        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            await interaction.response.send_message(view=error_view(TRACKING_OFF_MESSAGE), ephemeral=True)
            return

        paginator = LeaderboardPaginator(self.bot, interaction.guild, interaction.user.id, channel)
        await paginator.load("words")
        await paginator.start(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counter_Cmds(bot))
