from __future__ import annotations
import logging
from pathlib import Path
from typing import List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, can_attach, create_v2_container, make_button
from cogs.utils.config import BOTZILLA_URL, PRIVACY_URL, SITE_URL, SUPPORT_URL, TERMS_URL, invite_url
from cogs.utils.jar import SPAM_NOTE
from cogs.utils.swear_filter import MAX_FINED_PER_MESSAGE
from paginator import ButtonPaginator

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent / "assets"
GAME_URL = f"{BOTZILLA_URL}/game"
NO_PINGS = discord.AllowedMentions.none()
WELCOME_NEEDS = ("view_channel", "send_messages", "embed_links", "attach_files")

Picture = Tuple[str, str, str]
HELP_BANNER: Picture = (
    "help.png",
    "wordcounter_help.png",
    "WordCounter, an orange flip counter showing 02, floating above Earth beside its name",
)
WELCOME_BANNER: Picture = (
    "welcome.png",
    "wordcounter_welcome.png",
    "WordCounter floating above Earth beside its name, thanking the server for adding it",
)
AD_POSTER: Picture = (
    "advertisement.png",
    "wordcounter_ad.png",
    "WordCounter floating above Earth beside its name and the words Who talks the most, with chat bubbles rising "
    "toward it, above signs for words, emojis, files, keywords, ranks, history, swears and stats",
)

ABOUT_TEXT = (
    "WordCounter counts words, messages, attachments, emojis and the keywords you choose, for every member and "
    "channel. It keeps a monthly history, can count messages sent before it joined, and can run a swear jar."
)
BOTZILLA_TEXT = (
    "### Meet Specimen 01\n"
    "WordCounter comes from the same lab as **Botzilla**, one bot for your whole server: moderation and AutoMod, "
    "welcome cards, levels, an economy, pixel-art pets and games with friends."
)
GAME_TEXT = (
    "**Botzilla: Specimen 01** is a free pixel adventure about how Botzilla began, on a research station in orbit. "
    "WordCounter is in it too, as the station's archivist. Play in your browser, on Discord or on Android."
)
AD_PITCH = (
    "See who talks the most in your server. WordCounter counts words, messages, attachments, emojis and your own "
    "keywords for every member and channel, and turns them into leaderboards."
)
AD_FEATURES = (
    "### What it does\n"
    "- **Leaderboards:** `/leaderboard` ranks words, messages, attachments, emojis, keywords and swears, for the "
    "whole server or one channel.\n"
    "- **Member stats:** `/stats user` shows anyone's totals, month by month and channel by channel.\n"
    "- **Keywords:** count an inside joke or a catchphrase and see who says it most.\n"
    "- **Past messages:** `/analyze_chat` counts what was said before WordCounter joined.\n"
    "- **Swear jar:** every swear costs, debt is paid back with clean streaks, and the server can save up for a goal.\n"
    "- **Website:** see your stats in every server you share with WordCounter, and manage the servers you run."
)

HELP_PAGES: List[Tuple[str, str, List[Tuple[str, str]]]] = [
    (
        "Stats and leaderboards",
        "See who talks the most, overall or in one channel.",
        [
            (
                "/leaderboard [channel]",
                "Rankings for words, messages, attachments, emojis, keywords and swears. "
                "Pick a channel to see its top members and a month-by-month history.",
            ),
            (
                "/stats user [member]",
                "Your totals, or a member's, with a month-by-month and channel-by-channel breakdown.",
            ),
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
        "Swear jar",
        "Every swear costs. Debt is only paid back by not swearing. An admin turns it on in `/swearjar settings`.",
        [
            (
                "How fines work",
                "Swears are fined by tier: mild, explicit, and slurs or words your server adds. A message is fined for "
                f"at most {MAX_FINED_PER_MESSAGE} swears. WordCounter reacts with 🫙, or deletes the message in delete mode.",
            ),
            (
                "Paying it off",
                "Only clean streaks pay debt back. Every clean active day (5 or more messages, no swears) pays back one unit, "
                "with bonuses at 7 and 30 days. Members who never swear build up a wallet instead.",
            ),
            ("Spamming", SPAM_NOTE),
            ("/swearjar wallet [member]", "A member's wallet or debt, clean streak and swears."),
            ("/swearjar top", "Who swears most, owes most, saved most and has the longest clean streak."),
            ("/swearjar goal", "What the jar is saving up for, like a movie night."),
            (
                "/swearjar settings",
                "Turn it on, pick money or tokens, set prices, edit the word list, choose channels, roles and a goal. "
                "Needs Manage Server.",
            ),
            ("Past swears", "`/analyze_chat swears` adds past swears to the Swears leaderboard. They aren't fined."),
            ("Right-click menu", "Right-click a message, then Apps > Swear Jar to see its fine."),
        ],
    ),
    (
        "About WordCounter",
        "Everything is grouped by month and channel, for live tracking and past messages alike.",
        [
            ("/advertisement", "Post a card about WordCounter with invite links."),
            ("Website", f"See your stats in every server you share with WordCounter, and manage settings for servers you run: {SITE_URL}"),
            ("Your data", f"WordCounter stores counts, not message text. [Privacy Policy]({PRIVACY_URL}) · [Terms]({TERMS_URL})"),
            (
                "From the same lab",
                f"WordCounter is Specimen 02. Specimen 01 is [Botzilla]({BOTZILLA_URL}), and its free game "
                f"[Botzilla: Specimen 01]({GAME_URL}) has WordCounter in it as the station's archivist.",
            ),
        ],
    ),
]


def picture_file(picture: Picture) -> discord.File:
    name, filename, _ = picture
    return discord.File(ASSETS / name, filename=filename)


def gallery(picture: Picture) -> discord.ui.MediaGallery:
    _, filename, description = picture
    return discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}", description=description))


def link_buttons(bot: commands.Bot, invite_label: str = "Add to server", support_label: str = "Support server") -> List[discord.ui.Button]:
    client_id = bot.user.id if bot.user else None
    return [
        make_button(invite_label, url=invite_url(client_id)),
        make_button(support_label, url=SUPPORT_URL),
        make_button("Website", url=SITE_URL),
    ]


def welcome_page() -> discord.ui.Container:
    pages = "\n".join(f"{number}. **{title}**" for number, (title, _, _) in enumerate(HELP_PAGES, start=2))
    return discord.ui.Container(
        gallery(HELP_BANNER),
        discord.ui.TextDisplay(f"# Welcome to WordCounter\n{ABOUT_TEXT}"),
        discord.ui.TextDisplay(
            "### Finding your way\n"
            "- **Flip through** the pages with Previous and Next.\n"
            "- **Everything is a slash command:** type `/` and pick WordCounter.\n"
            "- **Right-click menus** count one message or show a member's stats."
        ),
        discord.ui.TextDisplay(
            "### Good places to start\n"
            "**For everyone**\n"
            "- `/leaderboard` to see who talks the most\n"
            "- `/stats user` for anyone's totals and monthly history\n"
            "**For server staff**\n"
            "- `/settings` to choose what gets counted. Nothing is counted until you pick channels or turn on whole server mode.\n"
            "- `/analyze_chat whole_server` to count messages from before WordCounter joined"
        ),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"### Pages\n{pages}"),
        accent_colour=BRAND_COLOR,
    )


def help_paginator(bot: commands.Bot, author_id: int) -> ButtonPaginator:
    pages = [welcome_page()] + [
        create_v2_container(title=title, description=description, fields=fields, color=BRAND_COLOR)
        for title, description, fields in HELP_PAGES
    ]
    paginator = ButtonPaginator(pages, author_id=author_id, custom_buttons=link_buttons(bot))
    paginator.denied_message = "Only the person who opened this help menu can change pages."
    return paginator


def welcome_view(bot: commands.Bot) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## 👋 Thanks for adding WordCounter"),
        gallery(WELCOME_BANNER),
        discord.ui.TextDisplay(
            f"{ABOUT_TEXT}\n\n"
            "- **Turn counting on:** nothing is counted until staff pick channels in `/settings`, or turn on whole server mode there.\n"
            "- **Count the past:** `/analyze_chat whole_server` adds messages sent before WordCounter joined.\n"
            "- **See who's on top:** `/leaderboard` and `/stats user`, or sign in on the website.\n"
            "- **Start here:** `/help` explains every command."
        ),
        discord.ui.Separator(),
        discord.ui.Section(discord.ui.TextDisplay(BOTZILLA_TEXT), accessory=make_button("Meet Botzilla", url=BOTZILLA_URL)),
        discord.ui.Section(discord.ui.TextDisplay(GAME_TEXT), accessory=make_button("Play the game", url=GAME_URL)),
        discord.ui.Separator(),
        discord.ui.TextDisplay("-# Questions? The support server is one button away."),
        discord.ui.ActionRow(*link_buttons(bot)),
        accent_colour=BRAND_COLOR,
    ))
    return view


def welcome_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    me = guild.me
    if me is None:
        return None
    candidates = [guild.system_channel] + sorted(guild.text_channels, key=lambda c: (c.position, c.id))
    for channel in candidates:
        if channel is not None and all(getattr(channel.permissions_for(me), name) for name in WELCOME_NEEDS):
            return channel
    return None


class AdvertisementView(discord.ui.LayoutView):
    def __init__(self, bot: commands.Bot) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Container(
            gallery(AD_POSTER),
            discord.ui.TextDisplay(f"# WordCounter\n{AD_PITCH}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(AD_FEATURES),
            discord.ui.Separator(),
            discord.ui.TextDisplay("-# Specimen 02 from the Botzilla lab. After adding it, type `/help` to get started."),
            discord.ui.ActionRow(*link_buttons(bot)),
            accent_colour=BRAND_COLOR,
        ))


class HelpCog(commands.Cog, name="Help"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="help",
        description="How to use WordCounter",
        extras={
            "details": (
                "Opens WordCounter's guide. It starts on a welcome page under WordCounter's banner: what it "
                "counts, how to find your way, and good commands to start with for everyone and for server "
                "staff. Previous and Next flip through the pages on stats and leaderboards, settings, counting "
                "past messages, the swear jar and WordCounter itself, and every page has Add to server, Support "
                "server and Website buttons. Only you can turn the pages. Choose ephemeral to see it privately. "
                "When WordCounter joins a server, it posts a welcome with the same banner in the server's system "
                "channel, or the first channel where it can attach a picture, pointing staff to /settings and "
                "/help, with Botzilla and its game from the same lab. Nobody is pinged."
            ),
            "bot_perms": (
                "Attach Files, for the banner"
            ),
        },
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.describe(ephemeral="Only show the help menu to you (default: no)")
    async def help_command(self, interaction: discord.Interaction, ephemeral: bool = False) -> None:
        if not await can_attach(interaction, "Can't show the help here"):
            return
        await help_paginator(self.bot, interaction.user.id).start(
            interaction, ephemeral=ephemeral, file=picture_file(HELP_BANNER), allowed_mentions=NO_PINGS,
        )

    @app_commands.command(
        name="advertisement",
        description="Post a card about WordCounter with invite links",
        extras={
            "details": (
                "Posts a card about WordCounter for anyone to see: a pixel-art poster of WordCounter over Earth "
                "with signs for what it counts, a short pitch, a line on each part of it, and Add to server, "
                "Support server and Website buttons. Anyone can post it, to show a friend or a server what "
                "WordCounter does. It's your reply to the command, so Discord shows who posted it. Nobody is "
                "pinged."
            ),
            "bot_perms": (
                "Attach Files, for the poster"
            ),
        },
    )
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    async def advertisement_command(self, interaction: discord.Interaction) -> None:
        if not await can_attach(interaction, "Can't post the card here"):
            return
        await interaction.response.send_message(
            view=AdvertisementView(self.bot), file=picture_file(AD_POSTER), allowed_mentions=NO_PINGS,
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        channel = welcome_channel(guild)
        if channel is None:
            return
        try:
            await channel.send(view=welcome_view(self.bot), file=picture_file(WELCOME_BANNER), allowed_mentions=NO_PINGS)
        except discord.HTTPException:
            log.info("Could not post the welcome in %s", guild.id, exc_info=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
