from __future__ import annotations
import asyncio
import contextlib
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Sequence, Set, Tuple
import discord
from discord import app_commands
from discord.ext import commands, tasks
from cogs.utils.components import (
    BRAND_COLOR,
    NEUTRAL_COLOR,
    SERVER_ONLY_MESSAGE,
    SUCCESS_COLOR,
    WARNING_COLOR,
    Callback,
    create_v2_container,
    create_v2_view,
    error_view,
    format_channel_or_category,
    make_button,
    manager_check_error,
    member_name,
    rank_prefix,
    tab_rows,
)
from cogs.utils.jar import (
    ACTIVE_DAY_MESSAGES,
    CLEAN_ROLE_STREAK,
    DAY_SECONDS,
    JAR_OFF_MESSAGE,
    SPAM_LIMIT,
    SPAM_MULTIPLIER,
    SPAM_NOTE,
    SPAM_PENALTY,
    SPAM_WINDOW,
    credit_for,
    days_to_clear,
    jar_checks,
    listed_words,
    money,
    moved_words,
    parse_amount,
    parse_prices,
    parse_tier,
    parse_tokens,
    parse_unit_cents,
    prices,
    removed_words,
    role_problem,
    shown_word,
    unit_label,
    utc_day,
    week_index,
    week_start,
    word_changes,
)
from cogs.utils.menus import SectionMenu
from cogs.utils.server_settings import ANALYSIS_BUSY_MESSAGE, TRACKABLE_CHANNEL_TYPES, analysis_state, parse_keywords
from cogs.utils.swear_filter import (
    DEFAULT_ENTRIES,
    DEFAULT_KEYS,
    MAX_FINED_PER_MESSAGE,
    SwearMatcher,
    fine_for,
    matcher_for,
)
from cogs.utils.swear_store import DAY_HISTORY_DAYS
from cogs.utils.swear_words import TIER_NAMES
from paginator import ButtonPaginator

log = logging.getLogger(__name__)

JAR_EMOJI = "🫙"
SPAM_EMOJI = "🚫"
NOTICE_SECONDS = 5
STATE_TTL = 15.0
DAILY_INTERVAL = 600.0
WORDS_PER_PAGE = 60

TOP_TABS = {"swears": "Swears", "debt": "Debt", "wallet": "Wallet", "streak": "Streak"}
TOP_DESCRIPTIONS = {
    "swears": "Who swears the most",
    "debt": "Who owes the jar the most",
    "wallet": "Who has saved up the most by not swearing",
    "streak": "Longest clean streaks right now",
}

SECTIONS = (
    ("overview", "Overview", "Turn the jar on or off and pick a mode"),
    ("fines", "Fines", "Money or tokens, and what each tier costs"),
    ("words", "Words", "Add, remove or move swear words"),
    ("channels", "Channels", "Where the jar listens"),
    ("roles", "Roles", "Potty Mouth and clean streak roles"),
    ("goal", "Goal", "What the jar is saving up for"),
    ("reset", "Resets", "Reset a member's jar or the whole jar"),
)


@dataclass
class JarState:
    settings: Dict[str, Any]
    matcher: Optional[SwearMatcher] = None
    rules: Dict[int, str] = field(default_factory=dict)
    watched: Set[int] = field(default_factory=set)
    ignored: Set[int] = field(default_factory=set)
    live_since: int = 0
    loaded_at: float = field(default_factory=time.monotonic)


def role_mention(role_id: Optional[int], empty: str) -> str:
    return f"<@&{role_id}>" if role_id else empty


def word_list(words: Sequence[str], limit: int = 25) -> str:
    shown = ", ".join(discord.utils.escape_markdown(word) for word in words[:limit])
    if len(words) > limit:
        shown += f" and {len(words) - limit} more"
    return shown or "None"


def wallet_fields(settings: Dict[str, Any], data: Dict[str, Any]) -> List[Tuple[str, str]]:
    balance = data["balance"]
    fields = [
        ("Debt" if balance < 0 else "Wallet", f"**{money(settings, balance)}**"),
        ("Clean streak", f"{data['streak']:,} day(s) · best {data['best_streak']:,}"),
        ("Swears", f"{data['swears']:,} counted · {money(settings, data['week_fines'])} fined this week"),
    ]
    if balance < 0:
        fields.append(("Paying it off", f"About {days_to_clear(-balance, data['streak']):,} more clean active day(s)."))
    if data["spam_until"] > time.time():
        fields.append(("Spam penalty", f"Fines are doubled until <t:{int(data['spam_until'])}:t>. {SPAM_NOTE}"))
    return fields


def earning_footer(settings: Dict[str, Any]) -> str:
    return (
        f"A clean active day ({ACTIVE_DAY_MESSAGES}+ messages, no swears) pays back {unit_label(settings)}. "
        "Streak bonuses at 7 and 30 days."
    )


class FormModal(discord.ui.Modal):
    def __init__(
        self,
        title: str,
        fields: Sequence[Tuple[str, str, str, int]],
        handler: Callable[[discord.Interaction, List[str]], Awaitable[None]],
    ) -> None:
        super().__init__(title=title)
        self.inputs: List[discord.ui.TextInput] = []
        for label, default, placeholder, max_length in fields:
            item = discord.ui.TextInput(
                label=label,
                default=default or None,
                placeholder=placeholder or None,
                max_length=max_length,
                style=discord.TextStyle.paragraph if max_length > 200 else discord.TextStyle.short,
            )
            self.add_item(item)
            self.inputs.append(item)
        self.handler = handler

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.handler(interaction, [item.value.strip() for item in self.inputs])


class JarTopPaginator(ButtonPaginator):
    def __init__(self, bot: commands.Bot, guild: discord.Guild, author_id: int, settings: Dict[str, Any]) -> None:
        super().__init__([], author_id=author_id)
        self.denied_message = "Only the person who ran /swearjar top can use these buttons."
        self.bot = bot
        self.guild = guild
        self.settings = settings
        self.kind = "swears"
        self.entry_count = 0

    async def load(self, kind: str) -> None:
        self.kind = kind
        self.current_page = 0
        rows = await self.bot.db.jar_leaderboard(self.guild.id, kind)
        self.entry_count = len(rows)
        self.set_pages([rows[i:i + 10] for i in range(0, len(rows), 10)] or [[]])

    def _value(self, value: int) -> str:
        if self.kind == "swears":
            return f"{value:,} swears"
        if self.kind == "streak":
            return f"{value:,} day(s)"
        return money(self.settings, value)

    def format_page(self, chunk: List[Tuple[int, int]]) -> discord.ui.Container:
        lines = [
            f"{rank_prefix(rank)} **{member_name(self.guild, uid)}** · {self._value(value)}"
            for rank, (uid, value) in enumerate(chunk, start=self.current_page * 10 + 1)
        ]
        return create_v2_container(
            title=f"Swear jar: {TOP_TABS[self.kind]}",
            description=f"{TOP_DESCRIPTIONS[self.kind]}\n\n" + ("\n".join(lines) or "Nobody here yet."),
            footer=f"{self.entry_count} member(s)",
            color=BRAND_COLOR,
        )

    def extra_rows(self) -> List[discord.ui.ActionRow]:
        return tab_rows(TOP_TABS, self.kind, self._switch_to)

    def _switch_to(self, kind: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await self.load(kind)
            await self.update_page(interaction)

        return callback


class JarSettingsView(SectionMenu):
    sections = SECTIONS
    title = "Swear jar settings"
    closed_hint = "Run /swearjar settings to open them again."

    def __init__(self, cog: SwearJar, guild: discord.Guild, author_id: int) -> None:
        super().__init__(cog.bot, guild, author_id, denied_message="Only the admin who opened these settings can use them.")
        self.cog = cog
        self.settings: Dict[str, Any] = {}
        self.overrides: List[Tuple[str, int, bool]] = []
        self.rules: Dict[int, str] = {}
        self.goal: Optional[Dict[str, Any]] = None
        self.reset_user_id: Optional[int] = None

    async def load_state(self) -> None:
        db = self.bot.db
        self.settings = await db.jar_settings(self.guild.id)
        self.overrides = await db.jar_word_overrides(self.guild.id)
        self.rules = await db.jar_channel_rules(self.guild.id)
        self.goal = await db.jar_goal(self.guild.id)
        self.cog.invalidate(self.guild.id)

    def description(self) -> str:
        return f"Fines for swearing in **{self.guild.name}**, paid back with clean streaks."

    def _money(self, units: int) -> str:
        return money(self.settings, units)

    def _rule_ids(self, rule: str) -> List[int]:
        return sorted(cid for cid, value in self.rules.items() if value == rule)

    def _goal_line(self) -> str:
        goal = self.goal
        if goal is None:
            return "None"
        if goal["completed_at"]:
            return f"{goal['label']}, reached <t:{int(goal['completed_at'])}:D>"
        return f"{goal['label']}: {self._money(goal['progress'])} of {self._money(goal['target'])}"

    def _populate_overview_tab(self, container: discord.ui.Container) -> None:
        s = self.settings
        mode = "Delete the message and fine it" if s["delete_mode"] else f"Fine it and react with {JAR_EMOJI}"
        fines = " · ".join(f"{TIER_NAMES[tier]} {self._money(price)}" for tier, price in prices(s).items())
        exceptions = len(self.rules)
        channels = "Follows your tracking settings" + (f", with {exceptions} exception(s)" if exceptions else "")
        if s["skip_age_restricted"]:
            channels += ", skips age-restricted channels"
        roles = f"Potty Mouth {role_mention(s['potty_role_id'], 'not set')} · Clean streak {role_mention(s['clean_role_id'], 'not set')}"
        container.add_item(
            discord.ui.TextDisplay(
                f"**Status:** {'On' if s['enabled'] else 'Off'}\n"
                f"**When someone swears:** {mode}\n"
                f"**Fines:** {fines}\n"
                f"**Channels:** {channels}\n"
                f"**Roles:** {roles}\n"
                f"**Goal:** {self._goal_line()}\n\n"
                f"-# {SPAM_NOTE}\n"
                "-# Run `/analyze_chat swears` to add past swears to the Swears leaderboard. Past swears aren't fined."
            )
        )
        if self.pending_confirmation == "turn_off":
            self._confirmation(
                container,
                "**Turn off the swear jar?**\nNothing is fined or paid back while it's off. Wallets, streaks and counts stay.",
                "Turn off",
                discord.ButtonStyle.danger,
                self._turn_off,
            )
            return
        container.add_item(
            discord.ui.ActionRow(
                make_button("Turn off", discord.ButtonStyle.danger, self._prompt("turn_off"))
                if s["enabled"]
                else make_button("Turn on", discord.ButtonStyle.success, self._turn_on),
                make_button(
                    "Switch to react and fine" if s["delete_mode"] else "Switch to delete and fine",
                    discord.ButtonStyle.secondary,
                    self._toggle_delete_mode,
                ),
            )
        )

    def _populate_fines_tab(self, container: discord.ui.Container) -> None:
        s = self.settings
        if s["currency"] == "money":
            currency = f"Money, {self._money(1)} per unit"
        else:
            currency = f"Tokens called {s['token_name']} / {s['token_plural']}, {s['tokens_per_unit']:,} per unit"
        tiers = "\n".join(
            f"- **{TIER_NAMES[tier]}:** {price:,} unit(s) = {self._money(price)}" for tier, price in prices(s).items()
        )
        container.add_item(
            discord.ui.TextDisplay(
                f"### Fines\n**Currency:** {currency}\n\n**What each swear costs:**\n{tiers}\n\n"
                f"-# A message is fined for at most its {MAX_FINED_PER_MESSAGE} priciest swears. A clean active day pays back 1 unit."
            )
        )
        currency_select = discord.ui.Select(
            placeholder="Show fines as",
            options=[
                discord.SelectOption(label="Money", value="money", description="Like $0.25 per unit", default=s["currency"] == "money"),
                discord.SelectOption(label="Tokens", value="tokens", description="Your own server currency", default=s["currency"] == "tokens"),
            ],
        )
        currency_select.callback = self._on_currency
        container.add_item(discord.ui.ActionRow(currency_select))
        container.add_item(
            discord.ui.ActionRow(
                make_button("Set money value" if s["currency"] == "money" else "Set token names", discord.ButtonStyle.primary, self._open_currency_modal),
                make_button("Set tier prices", discord.ButtonStyle.primary, self._open_prices_modal),
            )
        )

    def _populate_words_tab(self, container: discord.ui.Container) -> None:
        removed = [word for _, word, _ in removed_words(self.overrides)]
        moved = [f"{shown_word(key)} → {TIER_NAMES[tier]}" for key, tier in moved_words(self.overrides).items()]
        custom = [f"{key} ({TIER_NAMES[tier]})" for key, tier, gone in self.overrides if not gone and key not in DEFAULT_KEYS]
        container.add_item(
            discord.ui.TextDisplay(
                f"### Words\nThe default list has **{len(DEFAULT_ENTRIES)}** English and Polish swear words and slurs in three tiers. "
                "Matching ignores case and accents, and catches leetspeak like `sh!t`, stretched letters and spacing like `f u c k`. "
                "Press **View the list** to see every word.\n\n"
                f"**Your words ({len(custom)}):** {word_list(custom)}\n"
                f"**Removed from the default list ({len(removed)}):** {word_list(removed)}\n"
                f"**Moved to another tier ({len(moved)}):** {word_list(moved)}"
            )
        )
        if self.pending_confirmation == "restore_words":
            self._confirmation(
                container,
                "**Restore the default list?**\nYour own words are deleted, and removed or moved words go back to normal.",
                "Restore defaults",
                discord.ButtonStyle.danger,
                self._restore_words,
            )
            return
        container.add_item(
            discord.ui.ActionRow(
                make_button("Add words", discord.ButtonStyle.success, self._open_words_modal("add")),
                make_button("Remove words", discord.ButtonStyle.secondary, self._open_words_modal("remove")),
                make_button("Change tier", discord.ButtonStyle.secondary, self._open_words_modal("tier")),
            )
        )
        container.add_item(
            discord.ui.ActionRow(
                make_button("View the list", discord.ButtonStyle.primary, self._view_list),
                make_button("Restore defaults", discord.ButtonStyle.danger, self._prompt("restore_words"), disabled=not self.overrides),
            )
        )

    def _populate_channels_tab(self, container: discord.ui.Container) -> None:
        includes, excludes = self._rule_ids("include"), self._rule_ids("exclude")

        def listed(ids: List[int]) -> str:
            return ", ".join(format_channel_or_category(self.guild, cid) for cid in ids) or "None"

        container.add_item(
            discord.ui.TextDisplay(
                "### Channels\nThe jar listens wherever WordCounter tracks messages (see `/settings`), with these exceptions.\n\n"
                f"**Also listens in ({len(includes)}):** {listed(includes)}\n"
                f"**Never listens in ({len(excludes)}):** {listed(excludes)}\n"
                f"**Age-restricted channels:** {'skipped' if self.settings['skip_age_restricted'] else 'checked'}"
            )
        )
        channel_types = [*TRACKABLE_CHANNEL_TYPES, discord.ChannelType.category]
        include_select = discord.ui.ChannelSelect(placeholder="Also listen here, even if tracking skips it", channel_types=channel_types, max_values=10)
        include_select.callback = self._on_rule("include")
        exclude_select = discord.ui.ChannelSelect(placeholder="Never listen here", channel_types=channel_types, max_values=10)
        exclude_select.callback = self._on_rule("exclude")
        container.add_item(discord.ui.ActionRow(include_select))
        container.add_item(discord.ui.ActionRow(exclude_select))
        if self.rules:
            options = []
            for cid in sorted(self.rules)[:25]:
                channel = self.guild.get_channel(cid)
                name = f"#{channel.name}" if channel is not None else f"Deleted channel {cid}"
                options.append(discord.SelectOption(label=name[:100], value=str(cid), description="Also listens" if self.rules[cid] == "include" else "Never listens"))
            remove_select = discord.ui.Select(placeholder="Remove exceptions", options=options, max_values=len(options))
            remove_select.callback = self._on_rule(None)
            container.add_item(discord.ui.ActionRow(remove_select))
        container.add_item(
            discord.ui.ActionRow(
                make_button(
                    "Check age-restricted channels" if self.settings["skip_age_restricted"] else "Skip age-restricted channels",
                    discord.ButtonStyle.secondary,
                    self._toggle_age_restricted,
                )
            )
        )

    def _populate_roles_tab(self, container: discord.ui.Container) -> None:
        s = self.settings
        container.add_item(
            discord.ui.TextDisplay(
                "### Roles\n"
                f"**Potty Mouth:** {role_mention(s['potty_role_id'], 'Not set')}\n"
                "-# Goes to the member fined the most each week. It moves every Monday (UTC).\n"
                f"**Clean streak:** {role_mention(s['clean_role_id'], 'Not set')}\n"
                f"-# Given at a {CLEAN_ROLE_STREAK}-day clean streak and taken away when they swear.\n\n"
                "WordCounter needs Manage Roles, and its own role has to be above these roles."
            )
        )
        for name, column in (("Potty Mouth", "potty_role_id"), ("clean streak", "clean_role_id")):
            role = self.guild.get_role(s[column] or 0)
            select = discord.ui.RoleSelect(
                placeholder=f"Pick the {name} role",
                min_values=0,
                max_values=1,
                default_values=[discord.SelectDefaultValue(id=role.id, type=discord.SelectDefaultValueType.role)] if role else [],
            )
            select.callback = self._on_role(column)
            container.add_item(discord.ui.ActionRow(select))
        container.add_item(
            discord.ui.ActionRow(
                make_button("Clear Potty Mouth role", discord.ButtonStyle.secondary, self._clear_role("potty_role_id"), disabled=not s["potty_role_id"]),
                make_button("Clear clean streak role", discord.ButtonStyle.secondary, self._clear_role("clean_role_id"), disabled=not s["clean_role_id"]),
            )
        )

    def _populate_goal_tab(self, container: discord.ui.Container) -> None:
        goal = self.goal
        if goal is None or goal["completed_at"]:
            text = "### Goal\nNo goal right now. Set one, like **$50 for a movie night**, and the jar posts once when it's reached."
            if goal is not None:
                text += f"\n\n**Last goal:** {goal['label']}, reached <t:{int(goal['completed_at'])}:D>."
            container.add_item(discord.ui.TextDisplay(text))
            container.add_item(discord.ui.ActionRow(make_button("Set a goal", discord.ButtonStyle.success, self._open_goal_modal)))
            return
        percent = min(100, goal["progress"] * 100 // max(1, goal["target"]))
        channel = f"<#{goal['channel_id']}>" if goal["channel_id"] else "Nowhere yet. Pick a channel below."
        container.add_item(
            discord.ui.TextDisplay(
                f"### Goal\n**Saving up for:** {goal['label']}\n"
                f"**Progress:** {self._money(goal['progress'])} of {self._money(goal['target'])} ({percent}%)\n"
                f"**Announced in:** {channel}\n**Pings @everyone:** {'Yes' if goal['ping'] else 'No'}\n\n"
                "-# Fines fill the jar. Paying debt back with clean days doesn't take money out of it."
            )
        )
        if self.pending_confirmation == "end_goal":
            self._confirmation(container, "**End this goal?**\nIts progress is deleted.", "End goal", discord.ButtonStyle.danger, self._end_goal)
            return
        channel_select = discord.ui.ChannelSelect(
            placeholder="Channel to announce it in",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            max_values=1,
        )
        channel_select.callback = self._on_goal_channel
        container.add_item(discord.ui.ActionRow(channel_select))
        container.add_item(
            discord.ui.ActionRow(
                make_button("Change goal", discord.ButtonStyle.primary, self._open_goal_modal),
                make_button("Stop pinging @everyone" if goal["ping"] else "Ping @everyone", discord.ButtonStyle.secondary, self._toggle_goal_ping),
                make_button("End goal", discord.ButtonStyle.danger, self._prompt("end_goal")),
            )
        )

    def _populate_reset_tab(self, container: discord.ui.Container) -> None:
        member = f"<@{self.reset_user_id}>" if self.reset_user_id else "Nobody picked"
        container.add_item(
            discord.ui.TextDisplay(
                "### Resets\nResetting deletes swear counts, fines, wallets and streaks. Settings, words and the goal stay. "
                f"Past swears aren't counted again after a reset.\n\n**Member:** {member}"
            )
        )
        if self.pending_confirmation == "reset_member":
            self._confirmation(container, f"**Reset <@{self.reset_user_id}>'s jar?**\nIt can't be undone.", "Reset member", discord.ButtonStyle.danger, self._reset(True))
            return
        if self.pending_confirmation == "reset_all":
            self._confirmation(
                container,
                "**Reset the whole jar?**\nEvery member's swear counts, fines, wallet and streak are deleted, and the goal starts again from zero. It can't be undone.",
                "Reset the whole jar",
                discord.ButtonStyle.danger,
                self._reset(False),
            )
            return
        user_select = discord.ui.UserSelect(placeholder="Pick a member", min_values=0, max_values=1)
        user_select.callback = self._on_reset_user
        container.add_item(discord.ui.ActionRow(user_select))
        container.add_item(
            discord.ui.ActionRow(
                make_button("Reset this member", discord.ButtonStyle.primary, self._prompt("reset_member"), disabled=self.reset_user_id is None),
                make_button("Reset the whole jar", discord.ButtonStyle.danger, self._prompt("reset_all")),
            )
        )

    def _done(self, text: str, color: discord.Colour = SUCCESS_COLOR) -> None:
        self.pending_confirmation = None
        self.status_banner = (text, color)

    async def _turn_on(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        today = utc_day()
        await self.bot.db.jar_set_enabled(self.guild.id, True, today, week_index(today))
        self._done("The swear jar is on. Swears are fined from now on.")
        await self.refresh_and_edit(interaction)

    async def _turn_off(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.jar_set_enabled(self.guild.id, False, utc_day(), 0)
        self._done("The swear jar is off.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _toggle_delete_mode(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        turning_on = not self.settings["delete_mode"]
        if turning_on and not self.guild.me.guild_permissions.manage_messages:
            self._done("WordCounter needs the Manage Messages permission to delete messages.", WARNING_COLOR)
        else:
            await self.bot.db.jar_update(self.guild.id, delete_mode=int(turning_on))
            self._done(
                f"Swearing messages are now deleted, with a notice that disappears after {NOTICE_SECONDS} seconds."
                if turning_on
                else f"Swearing messages now stay up and get a {JAR_EMOJI} reaction."
            )
        await self.refresh_and_edit(interaction)

    async def _on_currency(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        value = (interaction.data.get("values") or ["money"])[0]
        await self.bot.db.jar_update(self.guild.id, currency=value)
        self._done("Fines now show as money." if value == "money" else "Fines now show as tokens.")
        await self.refresh_and_edit(interaction)

    async def _open_currency_modal(self, interaction: discord.Interaction) -> None:
        s = self.settings
        if s["currency"] == "money":
            fields = [("Value of one unit, in dollars", f"{s['unit_cents'] / 100:.2f}", "0.25", 10)]
        else:
            fields = [
                ("Token name, for one", s["token_name"], "Jar Dime", 30),
                ("Token name, for more than one", s["token_plural"], "Jar Dimes", 30),
                ("Tokens per unit", str(s["tokens_per_unit"]), "5", 6),
            ]
        await interaction.response.send_modal(FormModal("Fine currency", fields, self._save_currency))

    async def _save_currency(self, interaction: discord.Interaction, values: List[str]) -> None:
        if self.settings["currency"] == "money":
            cents = parse_unit_cents(values[0])
            if cents is None:
                self._done("Enter a value from 0.01 to 1000, like 0.25.", WARNING_COLOR)
            else:
                await self.bot.db.jar_update(self.guild.id, unit_cents=cents)
                self._done(f"One unit is now worth ${cents / 100:,.2f}.")
        else:
            tokens = parse_tokens(*values)
            if tokens is None:
                self._done("Give both names and a whole number of tokens from 1 to 10000.", WARNING_COLOR)
            else:
                await self.bot.db.jar_update(self.guild.id, **tokens)
                self._done(f"Fines now show as {tokens['token_plural']}.")
        await self.refresh_and_edit(interaction)

    async def _open_prices_modal(self, interaction: discord.Interaction) -> None:
        fields = [(f"{TIER_NAMES[tier]} (units)", str(price), "", 6) for tier, price in prices(self.settings).items()]
        await interaction.response.send_modal(FormModal("Tier prices", fields, self._save_prices))

    async def _save_prices(self, interaction: discord.Interaction, values: List[str]) -> None:
        new_prices = parse_prices(values)
        if new_prices is None:
            self._done("Each price has to be a whole number of units from 1 to 1000.", WARNING_COLOR)
        else:
            await self.bot.db.jar_update(self.guild.id, **new_prices)
            self._done("Tier prices saved.")
        await self.refresh_and_edit(interaction)

    def _open_words_modal(self, action: str) -> Callback:
        titles = {"add": "Add swear words", "remove": "Remove swear words", "tier": "Change tier"}

        async def callback(interaction: discord.Interaction) -> None:
            fields = [("Words, separated by commas", "", "pog, yeet, end a word with * to match its endings", 1000)]
            if action != "remove":
                fields.append(("Tier: 1 mild, 2 explicit, 3 slurs and custom", "3" if action == "add" else "", "3", 10))
            await interaction.response.send_modal(FormModal(titles[action], fields, self._words_handler(action)))

        return callback

    def _words_handler(self, action: str) -> Callable[[discord.Interaction, List[str]], Awaitable[None]]:
        async def handler(interaction: discord.Interaction, values: List[str]) -> None:
            tier = parse_tier(values[1]) if action != "remove" else None
            if action != "remove" and tier is None:
                self._done("The tier has to be 1, 2 or 3.", WARNING_COLOR)
            else:
                self._done(await self._apply_words(action, parse_keywords(values[0]), tier))
            await self.refresh_and_edit(interaction)

        return handler

    async def _apply_words(self, action: str, words: List[str], tier: Optional[int]) -> str:
        changes, forget, skipped = word_changes(action, words, tier, self.overrides)
        if changes:
            await self.bot.db.jar_set_words(self.guild.id, changes)
        if forget:
            await self.bot.db.jar_forget_words(self.guild.id, forget)
        done = len(changes) + len(forget)
        verb = {"add": "Added", "remove": "Removed", "tier": "Moved"}[action]
        text = f"{verb} {done} word(s)." if done else "Nothing changed."
        if skipped:
            reason = "not on the list" if action != "add" else "not a word the jar can match"
            text += f" Skipped ({reason}): {word_list(skipped, 10)}"
        return text

    async def _restore_words(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.jar_reset_words(self.guild.id)
        self._done("The default word list is back.")
        await self.refresh_and_edit(interaction)

    async def _view_list(self, interaction: discord.Interaction) -> None:
        pages = []
        for tier, words in listed_words(self.overrides).items():
            for start in range(0, max(1, len(words)), WORDS_PER_PAGE):
                chunk = [f"{word} ({note})" if note else word for _, word, note in words[start:start + WORDS_PER_PAGE]]
                pages.append(
                    create_v2_container(
                        title=f"{TIER_NAMES[tier]}: {money(self.settings, prices(self.settings)[tier])} each",
                        description=word_list(chunk, WORDS_PER_PAGE) if chunk else "No words in this tier.",
                        footer=f"{len(words)} word(s) in this tier · a * means endings count too, and *word* also counts inside longer words",
                        color=BRAND_COLOR,
                    )
                )
        await ButtonPaginator(pages, author_id=interaction.user.id).start(interaction, ephemeral=True)

    def _on_rule(self, rule: Optional[str]) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            ids = self._selected_ids(interaction)
            await self.bot.db.jar_set_channel_rule(self.guild.id, ids, rule)
            listed = ", ".join(format_channel_or_category(self.guild, cid) for cid in ids)
            self._done({"include": f"Also listening in {listed}.", "exclude": f"Never listening in {listed}.", None: f"Removed exceptions for {listed}."}[rule])
            await self.refresh_and_edit(interaction)

        return callback

    async def _toggle_age_restricted(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        skip = not self.settings["skip_age_restricted"]
        await self.bot.db.jar_update(self.guild.id, skip_age_restricted=int(skip))
        self._done("Age-restricted channels are skipped." if skip else "Age-restricted channels are checked too.")
        await self.refresh_and_edit(interaction)

    def _on_role(self, column: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            ids = self._selected_ids(interaction)
            if not ids:
                await self.bot.db.jar_update(self.guild.id, **{column: None})
                self._done("Role cleared.")
            else:
                role = self.guild.get_role(ids[0])
                problem = role_problem(self.guild, role)
                if problem:
                    self._done(problem, WARNING_COLOR)
                else:
                    await self.bot.db.jar_update(self.guild.id, **{column: role.id})
                    self._done(f"{role.mention} saved.")
            await self.refresh_and_edit(interaction)

        return callback

    def _clear_role(self, column: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            await self.bot.db.jar_update(self.guild.id, **{column: None})
            self._done("Role cleared.")
            await self.refresh_and_edit(interaction)

        return callback

    async def _open_goal_modal(self, interaction: discord.Interaction) -> None:
        goal = self.goal if self.goal and not self.goal["completed_at"] else None
        target = money(self.settings, goal["target"]).lstrip("$").split(" ")[0] if goal else ""
        fields = [
            ("What the jar is saving up for", goal["label"] if goal else "", "Movie night", 100),
            ("Amount to reach", target.replace(",", ""), "50" if self.settings["currency"] == "money" else "500", 12),
        ]
        await interaction.response.send_modal(FormModal("Swear jar goal", fields, self._save_goal))

    async def _save_goal(self, interaction: discord.Interaction, values: List[str]) -> None:
        label, amount = values
        target = parse_amount(self.settings, amount)
        if not label or target is None:
            self._done("Give the goal a name and an amount above zero.", WARNING_COLOR)
        else:
            current = self.goal if self.goal and not self.goal["completed_at"] else None
            channel_id = current["channel_id"] if current and current["channel_id"] else interaction.channel_id
            await self.bot.db.jar_set_goal(self.guild.id, label[:100], target, channel_id, bool(current and current["ping"]))
            self._done(f"Goal set: {label[:100]}, {money(self.settings, target)}. It's announced in <#{channel_id}>.")
        await self.refresh_and_edit(interaction)

    async def _on_goal_channel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ids = self._selected_ids(interaction)
        channel = self.guild.get_channel(ids[0]) if ids else None
        if channel is None or not channel.permissions_for(self.guild.me).send_messages:
            self._done("WordCounter can't send messages there. Pick another channel.", WARNING_COLOR)
        else:
            await self.bot.db.jar_update_goal(self.guild.id, channel_id=channel.id)
            self._done(f"The goal is announced in {channel.mention}.")
        await self.refresh_and_edit(interaction)

    async def _toggle_goal_ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        ping = not (self.goal and self.goal["ping"])
        if ping and not self.guild.me.guild_permissions.mention_everyone:
            self._done("WordCounter needs the Mention Everyone permission to ping @everyone.", WARNING_COLOR)
        else:
            await self.bot.db.jar_update_goal(self.guild.id, ping=ping)
            self._done("The announcement pings @everyone." if ping else "The announcement won't ping anyone.")
        await self.refresh_and_edit(interaction)

    async def _end_goal(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.bot.db.jar_end_goal(self.guild.id)
        self._done("Goal ended.", WARNING_COLOR)
        await self.refresh_and_edit(interaction)

    async def _on_reset_user(self, interaction: discord.Interaction) -> None:
        ids = self._selected_ids(interaction)
        self.reset_user_id = ids[0] if ids else None
        self.status_banner = None
        await self.refresh_and_edit(interaction)

    def _reset(self, member_only: bool) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            running, _ = await analysis_state(self.bot, self.guild.id)
            if running:
                self._done(ANALYSIS_BUSY_MESSAGE, WARNING_COLOR)
            elif member_only and self.reset_user_id is not None:
                await self.bot.db.jar_reset_members(self.guild.id, self.reset_user_id)
                self._done(f"Reset <@{self.reset_user_id}>'s jar.")
            else:
                await self.bot.db.jar_reset_members(self.guild.id)
                self._done("The whole jar was reset.")
            await self.refresh_and_edit(interaction)

        return callback


class SwearJar(commands.Cog):
    jar = app_commands.Group(name="swearjar", description="Fines for swearing, paid back with clean streaks")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.states: Dict[int, JarState] = {}
        self.recent: Dict[Tuple[int, int], Deque[float]] = defaultdict(deque)
        self.spam_until: Dict[Tuple[int, int], float] = {}
        self.day_counts: Dict[Tuple[int, int, int], int] = defaultdict(int)
        self.last_daily = 0.0
        self.fine_menu = app_commands.ContextMenu(name="Swear Jar", callback=self.message_fine)
        self.bot.tree.add_command(self.fine_menu)

    async def cog_load(self) -> None:
        self.upkeep.start()

    async def cog_unload(self) -> None:
        self.upkeep.cancel()
        self.bot.tree.remove_command(self.fine_menu.name, type=self.fine_menu.type)
        await self._flush_days()

    def invalidate(self, guild_id: int) -> None:
        self.states.pop(guild_id, None)

    def forget(self, target_id: int) -> None:
        self.invalidate(target_id)
        for store in (self.recent, self.spam_until, self.day_counts):
            for key in [key for key in store if target_id in key]:
                del store[key]

    async def state(self, guild_id: int) -> JarState:
        state = self.states.get(guild_id)
        if state is not None and time.monotonic() - state.loaded_at < STATE_TTL:
            return state
        db = self.bot.db
        settings = await db.jar_settings(guild_id)
        if not settings["enabled"]:
            state = JarState(settings)
        else:
            overrides = tuple(await db.jar_word_overrides(guild_id))
            watched, ignored = await db.get_guild_tracking_config(guild_id)
            live = [start for start, end in await db.jar_coverage(guild_id) if end is None]
            state = JarState(
                settings,
                await asyncio.to_thread(matcher_for, overrides),
                await db.jar_channel_rules(guild_id),
                watched,
                ignored,
                min(live) if live else 0,
            )
        self.states[guild_id] = state
        return state

    def _checks(self, guild: discord.Guild, channel: Any, state: JarState) -> Tuple[bool, int]:
        return jar_checks(guild, channel, state.watched, state.ignored, state.rules, bool(state.settings["skip_age_restricted"]))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None or message.author.bot or message.is_system():
            return
        state = await self.state(message.guild.id)
        if state.matcher is None:
            return
        allowed, channel_id = self._checks(message.guild, message.channel, state)
        if not allowed:
            return
        self.day_counts[(message.guild.id, message.author.id, utc_day(message.created_at.timestamp()))] += 1
        tiers = state.matcher.find(message.content)
        if tiers:
            await self._fine(message, channel_id, state, tiers, None)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if after.guild is None or after.author.bot or before.content == after.content:
            return
        state = await self.state(after.guild.id)
        if state.matcher is None or after.id < state.live_since:
            return
        allowed, channel_id = self._checks(after.guild, after.channel, state)
        if not allowed:
            return
        tiers = state.matcher.find(after.content)
        if tiers:
            await self._fine(after, channel_id, state, tiers, await self.bot.db.jar_message_fine(after.id))

    def _register_fine(self, key: Tuple[int, int], now: float) -> bool:
        recent = self.recent[key]
        recent.append(now)
        while recent and now - recent[0] > SPAM_WINDOW:
            recent.popleft()
        if len(recent) > SPAM_LIMIT:
            self.spam_until[key] = now + SPAM_PENALTY
        return self.spam_until.get(key, 0.0) > now

    async def _fine(
        self,
        message: discord.Message,
        channel_id: int,
        state: JarState,
        tiers: List[int],
        previous: Optional[Dict[str, Any]],
    ) -> None:
        swears, amount = fine_for(tiers, prices(state.settings))
        before_swears, before_amount = (previous["swears"], previous["amount"]) if previous else (0, 0)
        added_swears, added_amount = max(0, swears - before_swears), max(0, amount - before_amount)
        if not added_amount:
            return
        now = time.time()
        key = (message.guild.id, message.author.id)
        spam = self._register_fine(key, now)
        if spam:
            added_amount *= SPAM_MULTIPLIER
        member, reached = await self.bot.db.jar_record_fine(
            message.guild.id,
            message.author.id,
            channel_id,
            message.id,
            swears=added_swears,
            amount=added_amount,
            total_swears=before_swears + added_swears,
            total_amount=before_amount + added_amount,
            day=utc_day(now),
            spam_until=self.spam_until.get(key, 0.0),
        )
        await self._answer(message, state.settings, added_amount, member["balance"], spam)
        if hasattr(message.author, "roles"):
            await self._set_role(message.guild, state.settings["clean_role_id"], remove=[message.author])
        if reached is not None:
            await self._announce_goal(message.guild, state.settings, reached)

    async def _answer(self, message: discord.Message, settings: Dict[str, Any], amount: int, balance: int, spam: bool) -> None:
        if settings["delete_mode"]:
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            else:
                doubled = f" That's doubled: {SPAM_NOTE}" if spam else ""
                notice = create_v2_view(
                    "Message removed",
                    f"{message.author.mention}'s message was removed for swearing and fined **{money(settings, amount)}**.{doubled}\n"
                    f"Wallet: **{money(settings, balance)}**",
                    color=WARNING_COLOR,
                )
                with contextlib.suppress(discord.HTTPException):
                    await message.channel.send(view=notice, delete_after=NOTICE_SECONDS, allowed_mentions=discord.AllowedMentions.none())
                return
        with contextlib.suppress(discord.HTTPException):
            await message.add_reaction(SPAM_EMOJI if spam else JAR_EMOJI)

    async def _set_role(
        self,
        guild: discord.Guild,
        role_id: Optional[int],
        *,
        add: Sequence[discord.Member] = (),
        remove: Sequence[discord.Member] = (),
    ) -> None:
        role = guild.get_role(role_id or 0)
        if role is None or role_problem(guild, role):
            return
        for member in add:
            if role not in member.roles:
                with contextlib.suppress(discord.HTTPException):
                    await member.add_roles(role, reason="Swear jar")
        for member in remove:
            if role in member.roles:
                with contextlib.suppress(discord.HTTPException):
                    await member.remove_roles(role, reason="Swear jar")

    async def _announce_goal(self, guild: discord.Guild, settings: Dict[str, Any], goal: Dict[str, Any]) -> None:
        channel = guild.get_channel(goal["channel_id"] or 0)
        if channel is None or guild.me is None:
            return
        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages:
            return
        ping = bool(goal["ping"]) and permissions.mention_everyone
        view = create_v2_view(
            "Swear jar goal reached",
            f"{'@everyone ' if ping else ''}The jar collected **{money(settings, goal['target'])}**, "
            f"so it's time for: **{goal['label']}**",
            footer="Admins can set the next goal in /swearjar settings.",
            color=SUCCESS_COLOR,
        )
        with contextlib.suppress(discord.HTTPException):
            await channel.send(view=view, allowed_mentions=discord.AllowedMentions(everyone=ping, users=False, roles=False))

    async def _flush_days(self) -> None:
        if not self.day_counts:
            return
        counts, self.day_counts = self.day_counts, defaultdict(int)
        await self.bot.db.jar_add_day_messages((guild_id, user_id, day, n) for (guild_id, user_id, day), n in counts.items())

    def _forget_old_spam(self, now: float) -> None:
        for key in [key for key, recent in self.recent.items() if not recent or now - recent[-1] > SPAM_WINDOW]:
            del self.recent[key]
        for key in [key for key, until in self.spam_until.items() if until <= now]:
            del self.spam_until[key]

    @tasks.loop(minutes=1)
    async def upkeep(self) -> None:
        try:
            await self._flush_days()
            now = time.time()
            self._forget_old_spam(now)
            if now - self.last_daily >= DAILY_INTERVAL:
                self.last_daily = now
                await self.run_daily(utc_day(now))
        except Exception:
            log.exception("Swear jar upkeep failed")

    @upkeep.before_loop
    async def _before_upkeep(self) -> None:
        await self.bot.wait_until_ready()

    async def run_daily(self, today: int) -> None:
        db = self.bot.db
        await self._flush_days()
        for guild_id in await db.jar_enabled_guilds():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            settings = await db.jar_settings(guild_id)
            for day in range(max(settings["processed_day"] + 1, today - DAY_HISTORY_DAYS), today):
                credited = await db.jar_credit_day(guild_id, day, ACTIVE_DAY_MESSAGES, credit_for)
                clean = [guild.get_member(uid) for uid, streak in credited if streak >= CLEAN_ROLE_STREAK]
                await self._set_role(guild, settings["clean_role_id"], add=[member for member in clean if member])
            if settings["rotated_week"] < week_index(today):
                await self._rotate_potty_role(guild, settings, today)
                await db.jar_update(guild_id, rotated_week=week_index(today))
        await db.jar_prune_fines()

    async def _rotate_potty_role(self, guild: discord.Guild, settings: Dict[str, Any], today: int) -> None:
        role = guild.get_role(settings["potty_role_id"] or 0)
        if role is None or role_problem(guild, role):
            return
        start = week_start(today)
        winner_id = await self.bot.db.jar_top_fined(guild.id, start - 7 * DAY_SECONDS, start)
        winner = guild.get_member(winner_id) if winner_id else None
        await self._set_role(
            guild,
            role.id,
            add=[winner] if winner else [],
            remove=[member for member in role.members if winner is None or member.id != winner.id],
        )

    async def _enabled_settings(self, interaction: discord.Interaction) -> Optional[Dict[str, Any]]:
        if interaction.guild is None:
            await interaction.response.send_message(view=error_view(SERVER_ONLY_MESSAGE), ephemeral=True)
            return None
        settings = await self.bot.db.jar_settings(interaction.guild.id)
        if not settings["enabled"]:
            await interaction.response.send_message(view=error_view(JAR_OFF_MESSAGE), ephemeral=True)
            return None
        return settings

    async def _member_data(self, guild_id: int, user_id: int) -> Dict[str, Any]:
        return await self.bot.db.jar_member(guild_id, user_id, week_start(utc_day()))

    @jar.command(name="wallet", description="See a member's swear jar wallet, debt and clean streak")
    @app_commands.describe(member="Whose wallet to show (yours if you leave it empty)")
    async def wallet(self, interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
        settings = await self._enabled_settings(interaction)
        if settings is None:
            return
        member = member or interaction.user
        if member.bot:
            await interaction.response.send_message(view=error_view("Bots don't have a swear jar wallet."), ephemeral=True)
            return
        data = await self._member_data(interaction.guild.id, member.id)
        await interaction.response.send_message(
            view=create_v2_view(
                "Swear jar wallet",
                f"{member.mention}'s swear jar in **{interaction.guild.name}**.",
                fields=wallet_fields(settings, data),
                footer=earning_footer(settings),
                thumbnail_url=member.display_avatar.url,
                color=WARNING_COLOR if data["balance"] < 0 else SUCCESS_COLOR,
            )
        )

    @jar.command(name="top", description="Who swears most, owes most, saved most and has the longest clean streak")
    async def top(self, interaction: discord.Interaction) -> None:
        settings = await self._enabled_settings(interaction)
        if settings is None:
            return
        paginator = JarTopPaginator(self.bot, interaction.guild, interaction.user.id, settings)
        await paginator.load("swears")
        await paginator.start(interaction)

    @jar.command(name="goal", description="See what the swear jar is saving up for")
    async def goal(self, interaction: discord.Interaction) -> None:
        settings = await self._enabled_settings(interaction)
        if settings is None:
            return
        goal = await self.bot.db.jar_goal(interaction.guild.id)
        if goal is None:
            view = create_v2_view("Swear jar goal", "There's no goal right now. Admins can set one in `/swearjar settings`.", color=NEUTRAL_COLOR)
        elif goal["completed_at"]:
            view = create_v2_view(
                "Swear jar goal",
                f"The last goal, **{goal['label']}**, was reached <t:{int(goal['completed_at'])}:D> at {money(settings, goal['target'])}.",
                color=SUCCESS_COLOR,
            )
        else:
            percent = min(100, goal["progress"] * 100 // max(1, goal["target"]))
            filled = percent // 10
            view = create_v2_view(
                "Swear jar goal",
                f"Saving up for **{goal['label']}**.\n\n`{'█' * filled}{'░' * (10 - filled)}` {percent}%\n"
                f"**{money(settings, goal['progress'])}** of **{money(settings, goal['target'])}**",
                footer="Every fine goes into the jar.",
                color=BRAND_COLOR,
            )
        await interaction.response.send_message(view=view)

    @jar.command(name="settings", description="Turn the swear jar on and choose fines, words, channels, roles and goals")
    @app_commands.default_permissions(manage_guild=True)
    async def settings(self, interaction: discord.Interaction) -> None:
        reason = manager_check_error(interaction)
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return
        menu = JarSettingsView(self, interaction.guild, interaction.user.id)
        await menu.load_state()
        menu.build_ui()
        await interaction.response.send_message(view=menu, ephemeral=True)

    async def message_fine(self, interaction: discord.Interaction, message: discord.Message) -> None:
        settings = await self._enabled_settings(interaction)
        if settings is None:
            return
        if message.author.bot:
            await interaction.response.send_message(view=error_view("Bot messages aren't fined."), ephemeral=True)
            return
        record = await self.bot.db.jar_message_fine(message.id)
        if record:
            description = (
                f"{message.author.mention} was fined **{money(settings, record['amount'])}** "
                f"for {record['swears']} swear(s) in this message."
            )
        else:
            description = f"This message from {message.author.mention} wasn't fined."
        data = await self._member_data(interaction.guild.id, message.author.id)
        await interaction.response.send_message(
            view=create_v2_view(
                "Swear jar",
                description,
                fields=wallet_fields(settings, data),
                footer=earning_footer(settings),
                color=WARNING_COLOR if record else NEUTRAL_COLOR,
            ),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SwearJar(bot))
