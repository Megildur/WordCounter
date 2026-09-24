from __future__ import annotations
from typing import List, Optional, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from paginator import ButtonPaginator
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    create_v2_container,
    error_view,
    format_channel_or_category,
)


class AddKeywordModal(discord.ui.Modal, title="Add Tracked Keywords"):
    keywords_input = discord.ui.TextInput(
        label="Keyword(s) to Watch",
        placeholder="Enter a keyword, or multiple separated by commas (e.g. hello, gg, nice)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, menu_view: "SettingsMenuView") -> None:
        super().__init__()
        self.menu_view = menu_view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.keywords_input.value
        parts = [p.strip().lower() for chunk in raw.splitlines() for p in chunk.split(",") if p.strip()]
        unique_new = list(dict.fromkeys(parts))

        if not unique_new:
            self.menu_view.status_banner = ("⚠️ No valid keywords were entered.", WARNING_COLOR)
            await self.menu_view.refresh_and_edit(interaction)
            return

        added, skipped = await self.menu_view.bot.db.add_keywords(interaction.guild_id, unique_new)

        msg_parts = []
        if added:
            msg_parts.append(f"✅ Added **{len(added)}** keyword(s): `{', '.join(added[:10])}`")
        if skipped:
            msg_parts.append(f"ℹ️ Already existed: `{', '.join(skipped[:10])}`")

        self.menu_view.status_banner = ("\n".join(msg_parts), SUCCESS_COLOR if added else WARNING_COLOR)
        await self.menu_view.refresh_and_edit(interaction)


class BulkEditKeywordsModal(discord.ui.Modal, title="Bulk Edit Tracked Keywords"):
    keywords_input = discord.ui.TextInput(
        label="Comma-separated list of all watched keywords",
        placeholder="Leave blank to clear all keywords, or edit the comma-separated list",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )

    def __init__(self, menu_view: "SettingsMenuView", current_keywords: List[str]) -> None:
        super().__init__()
        self.menu_view = menu_view
        self.keywords_input.default = ", ".join(current_keywords)[:2000]

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.keywords_input.value or ""
        parts = [p.strip().lower() for chunk in raw.splitlines() for p in chunk.split(",") if p.strip()]
        new_set = list(dict.fromkeys(parts))

        await self.menu_view.bot.db.replace_keywords(interaction.guild_id, new_set)

        self.menu_view.status_banner = (
            f"✅ Updated keyword watch list! Now tracking **{len(new_set)}** keyword(s).",
            SUCCESS_COLOR,
        )
        await self.menu_view.refresh_and_edit(interaction)


class SettingsMenuView(discord.ui.LayoutView):
    """Interactive Components V2 Settings Menu combining all server setup & management functions."""

    def __init__(self, bot: commands.Bot, guild: discord.Guild, author_id: int) -> None:
        super().__init__(timeout=300.0)
        self.bot = bot
        self.guild = guild
        self.author_id = author_id
        self.active_tab: str = "overview"  # overview | channels | keywords | reset
        self.pending_confirmation: Optional[str] = None  # whole_server | disable_server | wipe_server
        self.status_banner: Optional[Tuple[str, discord.Colour]] = None

        # State for Reset tab
        self.selected_reset_user_id: Optional[int] = None
        self.selected_reset_channel_id: Optional[int] = None

        # Cached DB state
        self.watched_ids: Set[int] = set()
        self.ignored_ids: Set[int] = set()
        self.keywords: List[str] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=error_view("Only the administrator who opened this settings menu can interact with it."),
                ephemeral=True,
            )
            return False
        return True

    async def load_state(self) -> None:
        self.watched_ids, self.ignored_ids = await self.bot.db.get_guild_tracking_config(self.guild.id)
        self.keywords = await self.bot.db.get_keywords(self.guild.id)

    @property
    def is_whole_server(self) -> bool:
        return 1 in self.watched_ids

    @property
    def is_enabled(self) -> bool:
        return len(self.watched_ids) > 0

    def _build_navigation_row(self) -> discord.ui.ActionRow:
        select = discord.ui.Select(
            placeholder="📂 Navigate Settings Sections...",
            options=[
                discord.SelectOption(
                    label="Overview & Tracking Mode",
                    value="overview",
                    emoji="🏠",
                    description="Enable/disable tracking or switch Whole Server vs Specific mode",
                    default=self.active_tab == "overview",
                ),
                discord.SelectOption(
                    label="Channels & Categories",
                    value="channels",
                    emoji="📺",
                    description="Choose which channels and/or categories to watch or ignore",
                    default=self.active_tab == "channels",
                ),
                discord.SelectOption(
                    label="Keywords Watchlist",
                    value="keywords",
                    emoji="🔑",
                    description="Add, bulk edit, or remove tracked keywords via modals & dropdowns",
                    default=self.active_tab == "keywords",
                ),
                discord.SelectOption(
                    label="Data & Reset Tools",
                    value="reset",
                    emoji="🛠️",
                    description="Reset word counts for users/channels or unlock user re-analysis",
                    default=self.active_tab == "reset",
                ),
            ],
        )
        select.callback = self._on_tab_select
        return discord.ui.ActionRow(select)

    async def _on_tab_select(self, interaction: discord.Interaction) -> None:
        select: discord.ui.Select = interaction.data.get("values", ["overview"])  # type: ignore
        self.active_tab = select[0] if select else "overview"
        self.pending_confirmation = None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    def build_ui(self) -> None:
        self.clear_items()
        accent = self.status_banner[1] if self.status_banner else BRAND_COLOR
        container = discord.ui.Container(accent_colour=accent)

        # Header Section
        icon_url = self.guild.icon.url if self.guild.icon else None
        header_md = (
            f"## ⚙️ Server Word Counter Settings\n"
            f"Configure tracking mode, watched/ignored channels & categories, keywords, and data resets for **{self.guild.name}**."
        )
        if icon_url:
            container.add_item(
                discord.ui.Section(
                    discord.ui.TextDisplay(header_md),
                    accessory=discord.ui.Thumbnail(icon_url),
                )
            )
        else:
            container.add_item(discord.ui.TextDisplay(header_md))

        # Status notification banner if present
        if self.status_banner:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(self.status_banner[0]))

        # Navigation Dropdown
        container.add_item(discord.ui.Separator())
        container.add_item(self._build_navigation_row())
        container.add_item(discord.ui.Separator())

        # Render active tab content
        if self.active_tab == "overview":
            self._populate_overview_tab(container)
        elif self.active_tab == "channels":
            self._populate_channels_tab(container)
        elif self.active_tab == "keywords":
            self._populate_keywords_tab(container)
        elif self.active_tab == "reset":
            self._populate_reset_tab(container)

        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                f"-# Word Counter V2 Settings Dashboard • Active Tab: {self.active_tab.title()}"
            )
        )
        self.add_item(container)

    def _populate_overview_tab(self, container: discord.ui.Container) -> None:
        if not self.is_enabled:
            mode_str = "🔴 **Disabled** *(No messages are currently being counted)*"
            scope_title = "📋 Watched Channels & Categories"
            scope_value = "*None configured*"
        elif self.is_whole_server:
            mode_str = "🌐 **Whole Server Mode** *(All channels & categories counted except ignored)*"
            scope_title = "🚫 Ignored Channels & Categories"
            if self.ignored_ids:
                scope_value = ", ".join(format_channel_or_category(self.guild, cid) for cid in sorted(self.ignored_ids))
            else:
                scope_value = "*None (Counting every channel & category in the server)*"
        else:
            mode_str = "📋 **Specific Channels & Categories Mode**"
            scope_title = "📺 Watched Channels & Categories"
            specific_ids = [cid for cid in sorted(self.watched_ids) if cid != 1]
            scope_value = (
                ", ".join(format_channel_or_category(self.guild, cid) for cid in specific_ids)
                if specific_ids
                else "*None selected*"
            )

        kw_preview = (
            ", ".join(f"`{k}`" for k in self.keywords[:20])
            + (f" *...and {len(self.keywords) - 20} more*" if len(self.keywords) > 20 else "")
            if self.keywords
            else "*No keywords configured*"
        )

        overview_md = (
            f"### 🏠 Current Configuration Summary\n"
            f"**Tracking Mode:** {mode_str}\n\n"
            f"**{scope_title}:**\n{scope_value}\n\n"
            f"**🔑 Tracked Keywords ({len(self.keywords)}):**\n{kw_preview}"
        )
        container.add_item(discord.ui.TextDisplay(overview_md))

        if self.pending_confirmation == "whole_server":
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "⚠️ **Confirm Mode Switch**\n"
                    "Word count is currently set to specific channels/categories. Switching to **Whole Server Mode** will replace your specific channel list with server-wide tracking. Proceed?"
                )
            )
            confirm_btn = discord.ui.Button(label="✅ Confirm Whole Server", style=discord.ButtonStyle.success)
            cancel_btn = discord.ui.Button(label="✖️ Cancel", style=discord.ButtonStyle.secondary)
            confirm_btn.callback = self._confirm_whole_server
            cancel_btn.callback = self._cancel_confirmation
            container.add_item(discord.ui.ActionRow(confirm_btn, cancel_btn))
            return

        if self.pending_confirmation == "disable_server":
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "⚠️ **Confirm Disable Tracking**\n"
                    "Are you sure you want to stop recording word counts in this server? (Existing leaderboard stats will be preserved)."
                )
            )
            confirm_btn = discord.ui.Button(label="🔴 Confirm Disable", style=discord.ButtonStyle.danger)
            cancel_btn = discord.ui.Button(label="✖️ Cancel", style=discord.ButtonStyle.secondary)
            confirm_btn.callback = self._confirm_disable_server
            cancel_btn.callback = self._cancel_confirmation
            container.add_item(discord.ui.ActionRow(confirm_btn, cancel_btn))
            return

        btn_whole = discord.ui.Button(
            label="🌐 Enable Whole Server",
            style=discord.ButtonStyle.success if not self.is_whole_server else discord.ButtonStyle.secondary,
            disabled=self.is_whole_server,
        )
        btn_whole.callback = self._btn_enable_whole_server

        btn_specific = discord.ui.Button(
            label="📋 Manage Channels / Categories",
            style=discord.ButtonStyle.primary,
        )
        btn_specific.callback = self._btn_goto_channels

        btn_keywords = discord.ui.Button(
            label="🔑 Manage Keywords",
            style=discord.ButtonStyle.primary,
        )
        btn_keywords.callback = self._btn_goto_keywords

        btn_disable = discord.ui.Button(
            label="🔴 Disable Tracking",
            style=discord.ButtonStyle.danger,
            disabled=not self.is_enabled,
        )
        btn_disable.callback = self._btn_disable_tracking

        container.add_item(discord.ui.ActionRow(btn_whole, btn_specific, btn_keywords, btn_disable))

    def _populate_channels_tab(self, container: discord.ui.Container) -> None:
        if self.is_whole_server:
            header = (
                "### 🚫 Manage Ignored Channels & Categories *(Whole Server Mode)*\n"
                "Since **Whole Server Mode** is active, every channel is tracked by default. "
                "Select any **Text Channels** or **Categories** below to **ignore** them (ignoring a category automatically ignores all channels inside it)."
            )
            active_list = sorted(self.ignored_ids)
            list_label = "Currently Ignored Channels & Categories"
            placeholder_add = "➕ Select Channels or Categories to Ignore..."
            placeholder_rem = "➖ Select Ignored Channels/Categories to Remove..."
        else:
            header = (
                "### 📺 Manage Watched Channels & Categories *(Specific Mode)*\n"
                "Select which **Text Channels** and/or **Categories** the bot should actively watch. "
                "Selecting a **Category** automatically watches every channel and thread inside that category!"
            )
            active_list = [cid for cid in sorted(self.watched_ids) if cid != 1]
            list_label = "Currently Watched Channels & Categories"
            placeholder_add = "➕ Select Channels or Categories to Watch..."
            placeholder_rem = "➖ Select Watched Channels/Categories to Remove..."

        formatted_items = (
            "\n".join(f"• {format_channel_or_category(self.guild, cid)}" for cid in active_list)
            if active_list
            else "*None selected yet.*"
        )
        container.add_item(discord.ui.TextDisplay(f"{header}\n\n**{list_label} ({len(active_list)}):**\n{formatted_items}"))

        # ChannelSelect to add channels and/or categories
        channel_select = discord.ui.ChannelSelect(
            placeholder=placeholder_add,
            channel_types=[
                discord.ChannelType.text,
                discord.ChannelType.category,
                discord.ChannelType.news,
                discord.ChannelType.forum,
                discord.ChannelType.voice,
            ],
            min_values=1,
            max_values=10,
        )
        channel_select.callback = self._on_channels_added
        container.add_item(discord.ui.ActionRow(channel_select))

        # Select dropdown to remove existing channels/categories if any exist
        if active_list:
            remove_options = []
            for cid in active_list[:25]:
                ch = self.guild.get_channel(cid)
                if isinstance(ch, discord.CategoryChannel):
                    label = f"📁 {ch.name} (Category)"[:100]
                elif ch is not None:
                    label = f"#{ch.name}"[:100]
                else:
                    label = f"ID: {cid}"
                remove_options.append(discord.SelectOption(label=label, value=str(cid)))

            rem_select = discord.ui.Select(
                placeholder=placeholder_rem,
                options=remove_options,
                min_values=1,
                max_values=len(remove_options),
            )
            rem_select.callback = self._on_channels_removed
            container.add_item(discord.ui.ActionRow(rem_select))

        # Mode switch & clear buttons
        if self.is_whole_server:
            switch_btn = discord.ui.Button(
                label="📋 Switch to Specific Channels/Categories Mode",
                style=discord.ButtonStyle.primary,
            )
            switch_btn.callback = self._switch_to_specific_mode
        else:
            switch_btn = discord.ui.Button(
                label="🌐 Switch to Whole Server Mode",
                style=discord.ButtonStyle.success,
            )
            switch_btn.callback = self._confirm_whole_server

        clear_btn = discord.ui.Button(
            label="🗑️ Clear All Listed Channels/Categories",
            style=discord.ButtonStyle.danger,
            disabled=len(active_list) == 0,
        )
        clear_btn.callback = self._clear_all_channels
        container.add_item(discord.ui.ActionRow(switch_btn, clear_btn))

    def _populate_keywords_tab(self, container: discord.ui.Container) -> None:
        kw_display = (
            "\n".join(f"• `{kw}`" for kw in self.keywords[:30])
            + (f"\n*...and {len(self.keywords) - 30} more*" if len(self.keywords) > 30 else "")
            if self.keywords
            else "*No keywords are currently being watched.*"
        )
        container.add_item(
            discord.ui.TextDisplay(
                f"### 🔑 Tracked Keywords Management\n"
                f"Keywords are counted whenever a user says them inside a watched channel or category.\n\n"
                f"**Currently Watched Keywords ({len(self.keywords)}):**\n{kw_display}"
            )
        )

        # Dropdown to remove selected keywords
        if self.keywords:
            kw_options = [
                discord.SelectOption(label=kw[:100], value=kw[:100], emoji="🔑")
                for kw in self.keywords[:25]
            ]
            rem_kw_select = discord.ui.Select(
                placeholder="➖ Select Keyword(s) to Remove...",
                options=kw_options,
                min_values=1,
                max_values=len(kw_options),
            )
            rem_kw_select.callback = self._on_keywords_removed
            container.add_item(discord.ui.ActionRow(rem_kw_select))

        add_kw_btn = discord.ui.Button(label="➕ Add Keyword(s) (Modal)", style=discord.ButtonStyle.success)
        add_kw_btn.callback = self._open_add_keyword_modal

        bulk_kw_btn = discord.ui.Button(label="✏️ Bulk Edit List (Modal)", style=discord.ButtonStyle.primary)
        bulk_kw_btn.callback = self._open_bulk_keyword_modal

        clear_kw_btn = discord.ui.Button(
            label="🗑️ Clear All Keywords",
            style=discord.ButtonStyle.danger,
            disabled=len(self.keywords) == 0,
        )
        clear_kw_btn.callback = self._clear_all_keywords

        container.add_item(discord.ui.ActionRow(add_kw_btn, bulk_kw_btn, clear_kw_btn))

    def _populate_reset_tab(self, container: discord.ui.Container) -> None:
        selected_user_str = f"<@{self.selected_reset_user_id}>" if self.selected_reset_user_id else "*All Users (None selected)*"
        selected_chan_str = f"<#{self.selected_reset_channel_id}>" if self.selected_reset_channel_id else "*Entire Server (None selected)*"

        container.add_item(
            discord.ui.TextDisplay(
                f"### 🛠️ Data & Reset Management\n"
                f"Use the selectors below to target a specific **User** and/or **Channel**, then choose a reset action.\n\n"
                f"**Selected User Target:** {selected_user_str}\n"
                f"**Selected Channel Scope:** {selected_chan_str}"
            )
        )

        if self.pending_confirmation == "wipe_server":
            container.add_item(discord.ui.Separator())
            container.add_item(
                discord.ui.TextDisplay(
                    "⚠️ **DANGER: Confirm Full Server Word Count Reset**\n"
                    "This will reset the recorded word counts for **ALL users across the entire server** to `0`. Are you sure?"
                )
            )
            confirm_btn = discord.ui.Button(label="⚠️ Yes, Reset All Server Counts", style=discord.ButtonStyle.danger)
            cancel_btn = discord.ui.Button(label="✖️ Cancel", style=discord.ButtonStyle.secondary)
            confirm_btn.callback = self._confirm_wipe_server
            cancel_btn.callback = self._cancel_confirmation
            container.add_item(discord.ui.ActionRow(confirm_btn, cancel_btn))
            return

        user_select = discord.ui.UserSelect(
            placeholder="👤 Select a User to Reset or Unlock Re-Analyze...",
            min_values=0,
            max_values=1,
        )
        user_select.callback = self._on_reset_user_selected
        container.add_item(discord.ui.ActionRow(user_select))

        chan_select = discord.ui.ChannelSelect(
            placeholder="📺 Optional: Select a Channel Scope for Reset...",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news, discord.ChannelType.forum, discord.ChannelType.voice],
            min_values=0,
            max_values=1,
        )
        chan_select.callback = self._on_reset_channel_selected
        container.add_item(discord.ui.ActionRow(chan_select))

        btn_exec_reset = discord.ui.Button(
            label="🔄 Reset Word Count (Selected Scope)",
            style=discord.ButtonStyle.primary,
            disabled=(self.selected_reset_user_id is None and self.selected_reset_channel_id is None),
        )
        btn_exec_reset.callback = self._execute_scoped_reset

        btn_unlock_analyze = discord.ui.Button(
            label="🔓 Unlock User Re-Analyze",
            style=discord.ButtonStyle.secondary,
            disabled=(self.selected_reset_user_id is None),
        )
        btn_unlock_analyze.callback = self._unlock_user_analyze

        btn_wipe_all = discord.ui.Button(
            label="⚠️ Reset Entire Server",
            style=discord.ButtonStyle.danger,
        )
        btn_wipe_all.callback = self._prompt_wipe_server

        container.add_item(discord.ui.ActionRow(btn_exec_reset, btn_unlock_analyze, btn_wipe_all))

    # --- Callbacks ---

    async def _btn_enable_whole_server(self, interaction: discord.Interaction) -> None:
        specific_ids = [cid for cid in self.watched_ids if cid != 1]
        if specific_ids:
            self.pending_confirmation = "whole_server"
            await self.refresh_and_edit(interaction)
        else:
            await self._confirm_whole_server(interaction)

    async def _confirm_whole_server(self, interaction: discord.Interaction) -> None:
        await self.bot.db.enable_whole_server(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("✅ **Whole Server Mode Enabled!** Word counts are now recorded across the entire server.", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _switch_to_specific_mode(self, interaction: discord.Interaction) -> None:
        await self.bot.db.switch_to_specific_mode(self.guild.id)
        self.status_banner = (
            "📋 Switched to **Specific Channels & Categories Mode**! Select the channels or categories you want to watch below.",
            SUCCESS_COLOR,
        )
        await self.refresh_and_edit(interaction)

    async def _btn_disable_tracking(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "disable_server"
        await self.refresh_and_edit(interaction)

    async def _confirm_disable_server(self, interaction: discord.Interaction) -> None:
        await self.bot.db.disable_server_tracking(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("🔴 Word counting has been **disabled** for this server.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _cancel_confirmation(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        self.status_banner = ("ℹ️ Action cancelled.", BRAND_COLOR)
        await self.refresh_and_edit(interaction)

    async def _btn_goto_channels(self, interaction: discord.Interaction) -> None:
        self.active_tab = "channels"
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    async def _btn_goto_keywords(self, interaction: discord.Interaction) -> None:
        self.active_tab = "keywords"
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    async def _on_channels_added(self, interaction: discord.Interaction) -> None:
        raw_values = interaction.data.get("values", [])  # type: ignore
        selected_ids = [int(v) for v in raw_values]

        if self.is_whole_server:
            await self.bot.db.add_ignored_channels(self.guild.id, selected_ids)
            formatted = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected_ids)
            self.status_banner = (f"🚫 Added to **Ignored** list: {formatted}", SUCCESS_COLOR)
        else:
            await self.bot.db.add_watched_channels(self.guild.id, selected_ids)
            formatted = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected_ids)
            self.status_banner = (f"✅ Added to **Watched** list: {formatted}", SUCCESS_COLOR)

        await self.refresh_and_edit(interaction)

    async def _on_channels_removed(self, interaction: discord.Interaction) -> None:
        raw_values = interaction.data.get("values", [])  # type: ignore
        selected_ids = [int(v) for v in raw_values]

        if self.is_whole_server:
            await self.bot.db.remove_ignored_channels(self.guild.id, selected_ids)
            formatted = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected_ids)
            self.status_banner = (f"✅ Removed from **Ignored** list: {formatted}", SUCCESS_COLOR)
        else:
            await self.bot.db.remove_watched_channels(self.guild.id, selected_ids)
            formatted = ", ".join(format_channel_or_category(self.guild, cid) for cid in selected_ids)
            self.status_banner = (f"🗑️ Removed from **Watched** list: {formatted}", SUCCESS_COLOR)

        await self.refresh_and_edit(interaction)

    async def _clear_all_channels(self, interaction: discord.Interaction) -> None:
        if self.is_whole_server:
            await self.bot.db.clear_ignored_channels(self.guild.id)
            self.status_banner = ("✅ Cleared all ignored channels and categories.", SUCCESS_COLOR)
        else:
            await self.bot.db.clear_watched_channels(self.guild.id)
            self.status_banner = ("🗑️ Cleared all watched channels and categories.", WARNING_COLOR)

        await self.refresh_and_edit(interaction)

    async def _open_add_keyword_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AddKeywordModal(self))

    async def _open_bulk_keyword_modal(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(BulkEditKeywordsModal(self, self.keywords))

    async def _on_keywords_removed(self, interaction: discord.Interaction) -> None:
        raw_values = interaction.data.get("values", [])  # type: ignore
        await self.bot.db.remove_keywords(self.guild.id, raw_values)
        self.status_banner = (f"🗑️ Removed **{len(raw_values)}** keyword(s): `{', '.join(raw_values)}`", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _clear_all_keywords(self, interaction: discord.Interaction) -> None:
        await self.bot.db.clear_keywords(self.guild.id)
        self.status_banner = ("🗑️ All tracked keywords have been removed from the server.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_reset_user_selected(self, interaction: discord.Interaction) -> None:
        raw_values = interaction.data.get("values", [])  # type: ignore
        self.selected_reset_user_id = int(raw_values[0]) if raw_values else None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    async def _on_reset_channel_selected(self, interaction: discord.Interaction) -> None:
        raw_values = interaction.data.get("values", [])  # type: ignore
        self.selected_reset_channel_id = int(raw_values[0]) if raw_values else None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    async def _execute_scoped_reset(self, interaction: discord.Interaction) -> None:
        uid = self.selected_reset_user_id
        cid = self.selected_reset_channel_id

        if uid is not None and cid is None:
            await self.bot.db.reset_user_server_counts(self.guild.id, uid)
            self.status_banner = (f"✅ Reset server-wide word counts for <@{uid}>!", SUCCESS_COLOR)

        elif uid is not None and cid is not None:
            await self.bot.db.reset_user_channel_counts(self.guild.id, uid, cid)
            self.status_banner = (f"✅ Reset word count for <@{uid}> in <#{cid}>!", SUCCESS_COLOR)

        elif uid is None and cid is not None:
            await self.bot.db.reset_channel_counts(self.guild.id, cid)
            self.status_banner = (f"✅ Reset word counts for all users in <#{cid}>!", SUCCESS_COLOR)

        await self.refresh_and_edit(interaction)

    async def _unlock_user_analyze(self, interaction: discord.Interaction) -> None:
        uid = self.selected_reset_user_id
        if uid is None:
            return
        await self.bot.db.unlock_user_analyzed(self.guild.id, uid)
        self.status_banner = (f"🔓 Unlocked retroactive `/analyze` for <@{uid}>!", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def _prompt_wipe_server(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = "wipe_server"
        await self.refresh_and_edit(interaction)

    async def _confirm_wipe_server(self, interaction: discord.Interaction) -> None:
        await self.bot.db.reset_entire_server_counts(self.guild.id)
        self.pending_confirmation = None
        self.status_banner = ("✅ All word counts across the entire server have been reset to 0!", SUCCESS_COLOR)
        await self.refresh_and_edit(interaction)

    async def refresh_and_edit(self, interaction: discord.Interaction) -> None:
        await self.load_state()
        self.build_ui()
        await interaction.response.edit_message(view=self)


class Counter_Cmds(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)
        print("Counter_Cmds cog loaded")

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    @app_commands.command(
        name="settings",
        description="Interactive settings dashboard to configure channels, categories, keywords, and resets",
    )
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(ephemeral="Whether to show the settings menu privately (default: False)")
    async def settings_command(self, interaction: discord.Interaction, ephemeral: bool = False) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                view=error_view("This command can only be used inside a server."),
                ephemeral=True,
            )
            return

        menu = SettingsMenuView(self.bot, interaction.guild, interaction.user.id)
        await menu.load_state()
        menu.build_ui()
        await interaction.response.send_message(view=menu, ephemeral=ephemeral)

    words = app_commands.Group(name="words", description="Commands to view the current word count stats of the server.")

    @words.command(name="leaderboard", description="Shows the word count leaderboard of the server")
    @app_commands.describe(channel="The channel to show the leaderboard of members in")
    async def leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if interaction.guild_id is None or not await self.bot.db.has_tracking_enabled(interaction.guild_id):
            await interaction.response.send_message(
                view=error_view("Word count is not enabled on this server! Use `/settings` to enable it."),
                ephemeral=True,
            )
            return

        result = await self.bot.db.get_word_leaderboard(
            interaction.guild_id, channel.id if channel else None
        )
        if channel is None:
            empty_msg = "No one has said any words in this server yet."
            subtitle = "Top word contributors in the server"
        else:
            empty_msg = f"No one has said any words in {channel.mention} yet."
            subtitle = f"Top word contributors in {channel.mention}"

        if not result:
            await interaction.response.send_message(view=error_view(empty_msg), ephemeral=True)
            return

        valid_results = []
        for user_id, count in result:
            user = interaction.guild.get_member(user_id)
            if user is not None:
                valid_results.append((user, count))

        if not valid_results:
            await interaction.response.send_message(
                view=error_view("No active users found."),
                ephemeral=True,
            )
            return

        containers = []
        users_per_page = 10
        total_pages = (len(valid_results) + users_per_page - 1) // users_per_page

        for page_num in range(total_pages):
            start_idx = page_num * users_per_page
            end_idx = min(start_idx + users_per_page, len(valid_results))
            page_data = valid_results[start_idx:end_idx]

            lines = []
            for index, (user, count) in enumerate(page_data, start=start_idx + 1):
                if index == 1:
                    lines.append(f"🥇 **{user.display_name}** - {count:,} words")
                elif index == 2:
                    lines.append(f"🥈 **{user.display_name}** - {count:,} words")
                elif index == 3:
                    lines.append(f"🥉 **{user.display_name}** - {count:,} words")
                else:
                    lines.append(f"**{index}.** {user.display_name} - {count:,} words")

            container = create_v2_container(
                title="📝 Word Count Leaderboard",
                description=f"{subtitle}\n\n" + "\n".join(lines),
                footer=f"Page {page_num + 1}/{total_pages} • Total users: {len(valid_results)}",
                color=BRAND_COLOR,
            )
            containers.append(container)

        paginator = ButtonPaginator.create_standard_paginator(
            containers,
            author_id=interaction.user.id,
            timeout=180.0,
        )
        await paginator.start(interaction)


async def setup(bot) -> None:
    await bot.add_cog(Counter_Cmds(bot))