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
    """Returns the support server invite URL loaded from the BOT_SERVER .env variable."""
    url = os.getenv("BOT_SERVER", "").strip()
    return url if url else "https://discord.gg"


def get_bot_invite_url(bot: commands.Bot) -> str:
    """Returns the OAuth2 bot invite URL."""
    client_id = bot.user.id if bot.user else None
    if client_id:
        return f"https://discord.com/oauth2/authorize?client_id={client_id}&permissions=277025507392&scope=bot+applications.commands"
    return "https://discord.com"


class HelpView(discord.ui.LayoutView):
    """
    Interactive Components V2 Paginated Help Menu with navigation controls
    and support server website link button.
    """

    def __init__(self, bot: commands.Bot, author_id: int) -> None:
        super().__init__(timeout=180.0)
        self.bot = bot
        self.author_id = author_id
        self.current_page: int = 0
        self.total_pages: int = 5
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
        """Returns (title, description, fields) for each help page."""
        if page_index == 0:
            title = "📖 WordCounter — Overview & Quick Start"
            description = (
                "Welcome to **WordCounter**, the advanced Discord chat analytics bot! "
                "WordCounter tracks words, messages, attachments, media links, and custom keywords in real-time "
                "with modern Discord Components V2 interfaces and retroactive historical sweeps.\n\n"
                "### 🚀 Quick Start Guide\n"
                "1. **Enable Tracking**: Use `/settings` to choose between **Whole Server Mode** (tracks all channels) or **Specific Mode**.\n"
                "2. **Add Tracked Keywords**: Configure custom keywords in `/settings` → **Keywords Watchlist**.\n"
                "3. **View Rankings**: Use `/leaderboard` to browse real-time server rankings with interactive category buttons.\n"
                "4. **Retroactive Sweep**: Use `/analyze_chat` to count messages sent *before* the bot joined the server!"
            )
            fields = [
                ("💡 Need Help or Support?", "Click the **🌐 Support Server** button below to join our official support community.")
            ]
            return title, description, fields

        elif page_index == 1:
            title = "📊 Leaderboards & Analytics (/leaderboard)"
            description = (
                "**Unified Server Leaderboard (`/leaderboard [channel]`)**\n"
                "All server statistics are consolidated into a single interactive command! "
                "Use the interactive buttons at the bottom of the leaderboard to switch views instantly without typing new commands."
            )
            fields = [
                (
                    "🔘 Leaderboard Categories",
                    "• 📝 **Words**: Top word contributors across the server or channel.\n"
                    "• 💬 **Messages**: Total messages sent by members.\n"
                    "• 📎 **Attachments**: Files, images, and media links shared.\n"
                    "• 🔑 **Keywords**: Usage rankings for all tracked server keywords.",
                ),
                (
                    "📄 Interactive Pagination & Channel Filter",
                    "• Navigate through large user bases with `◀️ Previous` and `Next ▶️` buttons.\n"
                    "• Specify the optional `channel` parameter to filter rankings to a specific text channel.",
                ),
            ]
            return title, description, fields

        elif page_index == 2:
            title = "⚙️ Server Configuration (/settings)"
            description = (
                "**Administrator Dashboard (`/settings`)**\n"
                "The `/settings` dashboard is strictly restricted to members with the **Manage Server** permission "
                "and is always sent **ephemerally** for privacy. It includes a **✖️ Close Menu** button to cleanly dismiss anytime."
            )
            fields = [
                (
                    "🏠 Tracking Modes",
                    "• **Whole Server Mode**: Tracks all text channels and categories automatically, with an ignored list for channels/categories you want excluded.\n"
                    "• **Specific Mode**: Only counts messages inside specifically selected text channels and categories.",
                ),
                (
                    "📁 Category Watching",
                    "Selecting an entire **Category** automatically watches or ignores all channels and threads inside that category!",
                ),
                (
                    "🔑 Keywords Watchlist",
                    "Add keywords via interactive Modals, bulk edit existing lists, or remove keywords from tracking.",
                ),
                (
                    "🛠️ Data & Reset Tools",
                    "Reset word counts per-user or per-channel, unlock retroactive re-analysis, or reset server stats.",
                ),
            ]
            return title, description, fields

        elif page_index == 3:
            title = "🔍 Retroactive Chat Deep-Sweep (/analyze_chat)"
            description = (
                "**Retroactive Historical Chat Analysis (`/analyze_chat`)**\n"
                "Need stats for messages sent *before* WordCounter joined your server? "
                "WordCounter uses Discord's Guild Message Search API to sweep historical archives!"
            )
            fields = [
                (
                    "👤 /analyze_chat single_user @member",
                    "Retroactively sweeps historical messages for a specific member prior to the bot's join date.",
                ),
                (
                    "🌐 /analyze_chat whole_server",
                    "Gathers all non-bot members in the server and retroactively sweeps their chat history one by one, automatically skipping previously analyzed members.",
                ),
                (
                    "⚠️ Pre-Flight Keyword Confirmation",
                    "Keywords must be configured in `/settings` **before** running analysis in order to be counted. Both commands provide an interactive confirmation modal first.",
                ),
                (
                    "⏳ Anti-Ratelimit Pacing & Duration",
                    "Strict 5.0s pacing per request ensures zero Discord API violations. Live container messages display estimated time remaining in minutes.",
                ),
            ]
            return title, description, fields

        else:
            title = "👤 User Stats, Context Menus & Apps"
            description = (
                "**Quick Stats & User App Features**\n"
                "WordCounter includes built-in commands and context menus for quick user and message lookups."
            )
            fields = [
                (
                    "💬 /stats user @member",
                    "Shows detailed statistics for a member including total words, messages, attachments, and specific keyword breakdowns.",
                ),
                (
                    "🔑 /keyword list",
                    "Displays all currently tracked keywords configured in the server.",
                ),
                (
                    "🖱️ Discord Context Menus (Right-Click)",
                    "• **Message Word Count**: Right-click any message → **Apps** → **Message Word Count** to count words in that message.\n"
                    "• **User Stats**: Right-click any user profile → **Apps** → **User Stats** to view their full stats card.",
                ),
                (
                    "📱 User Installable App (/advertisement)",
                    "WordCounter can be installed directly to your personal Discord account! Use `/advertisement` in any server or DM to showcase the bot!",
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
            footer=f"Page {self.current_page + 1}/{self.total_pages} • WordCounter V2 Help",
            color=BRAND_COLOR,
        )

        # Pagination Action Row
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

        # Support Server Website & Bot Invite Link Buttons
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
    """
    Showcase card for WordCounter with support server website link button
    and bot invite link button.
    """

    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self._build_ui()

    def _build_ui(self) -> None:
        self.clear_items()
        bot_user = self.bot.user
        avatar_url = bot_user.display_avatar.url if bot_user and bot_user.display_avatar else None

        desc = (
            "Transform community engagement and analyze your server's activity with **WordCounter**! "
            "Whether you want real-time word counting, interactive leaderboards, or retroactive historical sweeps, "
            "WordCounter provides the most powerful and modern Discord analytics experience."
        )

        fields = [
            (
                "⚡ Core Features at a Glance",
                "• 📝 **Real-Time Tracking**: Counts words, messages, attachments, media links, and keywords automatically.\n"
                "• 🏆 **Unified Leaderboard**: Interactive switcher buttons for Words, Messages, Attachments, and Keywords with pagination.\n"
                "• 🔍 **Retroactive Chat Deep-Sweep**: Sweep messages sent *before* the bot joined the server (per-user or whole server!).\n"
                "• ⚙️ **Comprehensive Settings**: Whole Server vs Specific Mode, category watching, ignored lists, and keyword watchlists.\n"
                "• 🔒 **Enterprise-Grade Architecture**: SQLite WAL mode, async connection locks, and Manage Server security guards.",
            ),
            (
                "📱 User Installable App",
                "Install WordCounter to your personal Discord account to run `/advertisement` and `/help` across any server or DM!",
            ),
        ]

        container = create_v2_container(
            title="📊 WordCounter — The Ultimate Discord Chat Analytics Bot",
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
    """Help and discovery commands for WordCounter."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        print("Help cog loaded")

    @app_commands.command(
        name="help",
        description="Comprehensive paginated guide to all WordCounter commands and features",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(ephemeral="Whether to show the help menu privately (default: False)")
    async def help_command(self, interaction: discord.Interaction, ephemeral: bool = False) -> None:
        view = HelpView(self.bot, interaction.user.id)
        await interaction.response.send_message(view=view, ephemeral=ephemeral)

    @app_commands.command(
        name="advertisement",
        description="Display an interactive advertisement card for WordCounter to share in any server",
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def advertisement_command(self, interaction: discord.Interaction) -> None:
        view = AdvertisementView(self.bot)
        await interaction.response.send_message(view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
