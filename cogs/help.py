from __future__ import annotations
import os
from typing import List, Tuple, Optional
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_container,
    error_view,
)

load_dotenv()


def get_support_server_url() -> str:
    url = os.getenv("BOT_SERVER", "").strip()
    return url if url else "https://discord.gg"


def get_bot_invite_url(bot: commands.Bot) -> str:
    client_id = bot.user.id if bot.user else None
    if client_id:
        return f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=277025507392&scope=bot+applications.commands"
    return "https://discord.com"


class HelpView(discord.ui.LayoutView):

    def __init__(self, bot: commands.Bot, author_id: int) -> None:
        super().__init__(timeout=180.0)
        self.bot = bot
        self.author_id = author_id
        self.current_page: int = 0
        self.total_pages: int = 4
        self.build_page()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=error_view("Only the person who opened this help menu can change pages."),
                ephemeral=True,
            )
            return False
        return True

    def _get_page_content(self, page_index: int) -> Tuple[str, str, Optional[List[Tuple[str, str]]]]:
        if page_index == 0:
            title = "📊 Stats & Leaderboard"
            description = "Commands for viewing server activity rankings and individual user stats."
            fields = [
                (
                    "🏆 /leaderboard [channel]",
                    "View rankings for **Words**, **Messages**, **Attachments**, or **Keywords**.\n"
                    "Filter by a specific channel using the optional `channel` option.",
                ),
                (
                    "👤 /stats user <member>",
                    "View a member's total words, messages, attachments, and tracked keyword counts.",
                ),
                (
                    "🔑 /keyword list",
                    "List all keywords currently tracked in this server.",
                ),
                (
                    "🖱️ Context Menus (Right-Click)",
                    "• **Message Word Count**: Right-click any message → Apps → Message Word Count\n"
                    "• **User Stats**: Right-click any user profile → Apps → User Stats",
                ),
            ]
            return title, description, fields

        elif page_index == 1:
            title = "⚙️ Server Settings (/settings)"
            description = "Manage tracking channels, watched keywords, and server data. Requires **Manage Server** permission."
            fields = [
                (
                    "🏠 Tracking Modes",
                    "• **Whole Server**: Tracks all channels automatically. Add channels or categories to the ignore list to exclude them.\n"
                    "• **Specific Mode**: Only tracks channels and categories you explicitly choose.",
                ),
                (
                    "📁 Category Tracking",
                    "Adding or ignoring a category applies to all channels inside it.",
                ),
                (
                    "🔑 Keywords Watchlist",
                    "Add or remove custom words for the bot to count across messages.",
                ),
                (
                    "🛠️ Data & Reset Tools",
                    "Reset stats for a specific user, clear channel counts, or wipe server data.",
                ),
            ]
            return title, description, fields

        elif page_index == 2:
            title = "🔍 Historical Chat Analysis (/analyze_chat)"
            description = "Scan messages sent before WordCounter joined the server. Requires **Manage Server** permission."
            fields = [
                (
                    "👤 /analyze_chat single_user <member>",
                    "Scans historical messages for a specific member and adds them to their stats.",
                ),
                (
                    "🌐 /analyze_chat whole_server",
                    "Scans historical messages for all non-bot members one by one. Automatically skips members who were already scanned.",
                ),
                (
                    "⚠️ Important Note on Keywords",
                    "Keywords must be configured in `/settings` **before** running an analysis. Keywords added later will not be counted in past scans.",
                ),
            ]
            return title, description, fields

        else:
            title = "📱 Bot Info & User App"
            description = "WordCounter commands and install options."
            fields = [
                (
                    "🚀 User App Installation",
                    "You can install WordCounter directly to your Discord account. This allows you to run `/help` and `/advertisement` in any server or DM.",
                ),
                (
                    "📢 /advertisement",
                    "Post a quick feature summary card with invite links.",
                ),
                (
                    "🔗 Quick Links",
                    "Use the buttons below to join the support server or invite WordCounter to another server.",
                ),
            ]
            return title, description, fields

    def build_page(self) -> None:
        self.clear_items()
        title, description, fields = self._get_page_content(self.current_page)

        container = create_v2_container(
            title=title,
            description=description,
            fields=fields,
            footer=f"Page {self.current_page + 1}/{self.total_pages} • WordCounter Help",
            color=BRAND_COLOR,
        )

        btn_prev = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.current_page <= 0,
        )
        btn_prev.callback = self._on_prev

        btn_ind = discord.ui.Button(
            label=f"Page {self.current_page + 1}/{self.total_pages}",
            style=discord.ButtonStyle.primary,
            disabled=True,
        )

        btn_next = discord.ui.Button(
            label="Next ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=self.current_page >= self.total_pages - 1,
        )
        btn_next.callback = self._on_next

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(btn_prev, btn_ind, btn_next))

        support_url = get_support_server_url()
        invite_url = get_bot_invite_url(self.bot)

        link_buttons = [
            discord.ui.Button(
                label="Support Server",
                style=discord.ButtonStyle.link,
                url=support_url,
                emoji="🌐",
            ),
            discord.ui.Button(
                label="Add to Server",
                style=discord.ButtonStyle.link,
                url=invite_url,
                emoji="➕",
            ),
        ]
        container.add_item(discord.ui.ActionRow(*link_buttons))

        self.add_item(container)

    async def _on_prev(self, interaction: discord.Interaction) -> None:
        if self.current_page > 0:
            self.current_page -= 1
        self.build_page()
        await interaction.response.edit_message(view=self)

    async def _on_next(self, interaction: discord.Interaction) -> None:
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
        self.build_page()
        await interaction.response.edit_message(view=self)


class AdvertisementView(discord.ui.LayoutView):

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self._build_ui()

    def _build_ui(self) -> None:
        self.clear_items()
        bot_user = self.bot.user
        avatar_url = bot_user.display_avatar.url if bot_user and bot_user.display_avatar else None

        desc = (
            "Track words, messages, attachments, and custom keywords across your server with real-time leaderboards and historical chat analysis."
        )

        fields = [
            (
                "⚡ Features",
                "• **Live Tracking**: Counts words, messages, attachments, media links, and custom keywords.\n"
                "• **Interactive Leaderboard**: Switch between Words, Messages, Attachments, and Keywords.\n"
                "• **Historical Chat Sweep**: Scan messages sent before the bot joined (per-user or whole server).\n"
                "• **Channel & Category Control**: Whole-server mode with ignore lists, or specific channel/category tracking.\n"
                "• **Keyword Watchlist**: Track custom phrases and see who says them most.",
            ),
            (
                "📱 User Installable",
                "Install WordCounter to your Discord account to use commands across any server or DM.",
            ),
        ]

        container = create_v2_container(
            title="📊 WordCounter — Chat & Word Analytics",
            description=desc,
            fields=fields,
            thumbnail_url=avatar_url,
            footer="WordCounter • Real-Time Chat & Word Analytics",
            color=BRAND_COLOR,
        )

        support_url = get_support_server_url()
        invite_url = get_bot_invite_url(self.bot)

        link_buttons = [
            discord.ui.Button(
                label="Support Server",
                style=discord.ButtonStyle.link,
                url=support_url,
                emoji="🌐",
            ),
            discord.ui.Button(
                label="Add to Server",
                style=discord.ButtonStyle.link,
                url=invite_url,
                emoji="➕",
            ),
        ]
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(*link_buttons))
        self.add_item(container)


class HelpCog(commands.Cog, name="Help"):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        print("Help cog loaded")

    @app_commands.command(
        name="help",
        description="View guide to WordCounter commands and features",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(ephemeral="Whether to show the help menu privately (default: False)")
    async def help_command(self, interaction: discord.Interaction, ephemeral: bool = False) -> None:
        view = HelpView(self.bot, interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    @app_commands.command(
        name="advertisement",
        description="Show an overview card of WordCounter features and links",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def advertisement_command(self, interaction: discord.Interaction) -> None:
        view = AdvertisementView(self.bot)
        await interaction.response.send_message(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))