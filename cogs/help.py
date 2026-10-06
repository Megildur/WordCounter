from __future__ import annotations
from typing import List, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, create_v2_container, make_button
from cogs.utils.config import PRIVACY_URL, SITE_URL, SUPPORT_URL, TERMS_URL, invite_url
from paginator import ButtonPaginator

HELP_PAGES: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    (
        "Stats and leaderboards",
        "See who talks the most, overall or in one channel.",
        [
            (
                "/leaderboard [channel]",
                "Rankings for words, messages, attachments, emojis and keywords. "
                "Pick a channel to see its top members and a month-by-month history.",
            ),
            ("/stats user <member>", "A member's totals, with a month-by-month and channel-by-channel breakdown."),
            ("/keyword list", "The keywords this server tracks."),
            (
                "Right-click menus",
                "Right-click a message, then Apps > Message Word Count.\n"
                "Right-click a member, then Apps > User Stats.",
            ),
        ],
    ),
    (
        "Settings",
        "`/settings` controls what gets counted. You need the Manage Server permission.",
        [
            (
                "Tracking modes",
                "Whole server counts every channel. Add channels or categories to the ignore list to leave them out.\n"
                "Specific channels counts only the channels and categories you pick.",
            ),
            ("Categories", "Tracking or ignoring a category applies to every channel inside it."),
            ("Keywords", "Words or phrases to count separately, like an inside joke or a catchphrase."),
            (
                "Data & Reset Tools",
                "Reset all counts for a member, a channel or both, re-analyze a member from scratch, or reset the whole server.",
            ),
        ],
    ),
    (
        "Counting past messages",
        "`/analyze_chat` adds messages that weren't counted live. You need the Manage Server permission.",
        [
            (
                "/analyze_chat single_user <member>",
                "Counts one member's past messages using Discord's message search. Usually a few minutes.",
            ),
            (
                "/analyze_chat whole_server",
                "Reads every tracked channel and thread once and counts past messages for every member who "
                "hasn't been analyzed yet. Progress is saved as it goes, so a restart picks up where it left off.",
            ),
            (
                "Nothing counted twice",
                "Messages sent while tracking was on are already counted, so only older messages and any gaps "
                "when tracking was off are added. Each member is analyzed once.",
            ),
            (
                "Set keywords first",
                "Keywords only count in old messages if they're set in `/settings` before you run an analysis.",
            ),
        ],
    ),
    (
        "About WordCounter",
        "Everything is grouped by month and channel, for live tracking and past messages alike.",
        [
            ("/advertisement", "Post a card about WordCounter with invite links."),
            ("Website", f"See your stats in every server you share with WordCounter, and manage settings for servers you run: {SITE_URL}"),
            ("Coming soon: Swear Jar", "Track swearing in chat and see who owes the jar the most."),
            ("Your data", f"WordCounter stores counts, not message text. [Privacy Policy]({PRIVACY_URL}) · [Terms]({TERMS_URL})"),
        ],
    ),
]


def link_buttons(bot: commands.Bot, invite_label: str, support_label: str) -> List[discord.ui.Button]:
    client_id = bot.user.id if bot.user else None
    return [
        make_button(invite_label, url=invite_url(client_id)),
        make_button(support_label, url=SUPPORT_URL),
        make_button("Website", url=SITE_URL),
    ]


def help_paginator(bot: commands.Bot, author_id: int) -> ButtonPaginator:
    pages = [
        create_v2_container(title=title, description=description, fields=fields, color=BRAND_COLOR)
        for title, description, fields in HELP_PAGES
    ]
    paginator = ButtonPaginator(
        pages,
        author_id=author_id,
        custom_buttons=link_buttons(bot, "Add to a server", "Support server"),
    )
    paginator.denied_message = "Only the person who opened this help menu can change pages."
    return paginator


class AdvertisementView(discord.ui.LayoutView):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        avatar_url = bot.user.display_avatar.url if bot.user else None
        description = (
            "Ever wonder who *actually* sends the most messages, who lives in `#general`, "
            "or who spams that one inside joke non-stop?\n\n"
            "**WordCounter** tracks your server's activity in real time and turns everyday chatter "
            "into leaderboards, member stats and monthly rankings for your community."
        )
        fields = [
            (
                "Leaderboards (`/leaderboard`)",
                "Compete for the top spot in words, messages, memes and attachments, emojis, and keywords. "
                "Filter by channel to see who runs `#general` or who's dominating `#media`.",
            ),
            (
                "Chat profiles (`/stats user`)",
                "Check your own stats or a friend's, with monthly history and your most active channels.",
            ),
            (
                "Inside jokes and catchphrases (`/settings`)",
                "Got an iconic server meme or quote? Add it as a keyword and watch the race for who repeats it most.",
            ),
            (
                "Catch up on past messages (`/analyze_chat`)",
                "Added WordCounter to an existing server? Count the history so nobody loses credit for what they said before the bot joined.",
            ),
            ("Coming soon: the Swear Jar", "A playful way to track swearing and see who owes the server jar the most pennies."),
        ]
        self.add_item(
            create_v2_container(
                title="WordCounter: who talks the most in your server?",
                description=description,
                fields=fields,
                thumbnail_url=avatar_url,
                footer="Use /help to get started",
                color=BRAND_COLOR,
                action_rows=[discord.ui.ActionRow(*link_buttons(bot, "Add to your server", "Join the community server"))],
            )
        )


class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="help", description="How to use WordCounter")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(ephemeral="Only show the help menu to you (default: no)")
    async def help_command(self, interaction: discord.Interaction, ephemeral: bool = False) -> None:
        await help_paginator(self.bot, interaction.user.id).start(interaction, ephemeral=ephemeral)

    @app_commands.command(name="advertisement", description="Post a card about WordCounter with invite links")
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def advertisement_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(view=AdvertisementView(self.bot))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
