"""The swear jar's rules and helpers that need neither Discord events nor the database.

It holds what a clean day pays and the spam limits, the maths for days and weeks, unit and money conversion, parsing of what admins type, the word lists shown in settings, and the check for where the jar listens. The cog (cogs/swear_jar.py) and the dashboard (web/swearjar.py) both use it, so Discord and the website follow the same rules. Balances, fines and prices are stored in whole units, and money() turns units into dollars or tokens only for display.
"""

from __future__ import annotations
import math
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union
import discord
from cogs.utils.components import channel_family, check_channel_with_config
from cogs.utils.swear_filter import DEFAULT_ENTRIES, DEFAULT_KEYS, DEFAULT_LANGUAGE, build_entries, custom_key, default_keys_for, fold
from cogs.utils.swear_words import EXPLICIT, LANGUAGES, MILD, SLUR

# Messages a member must send, in channels the jar listens in, for a day to count as active and be paid back.
ACTIVE_DAY_MESSAGES = 5
CLEAN_DAY_CREDIT = 1
# Extra units paid on the day a clean streak reaches exactly this many days.
STREAK_BONUSES = {7: 3, 30: 10}
# After day 30, every 30th day of a streak (60, 90 and so on) pays REPEAT_BONUS extra.
REPEAT_BONUS_EVERY = 30
REPEAT_BONUS = 10
CLEAN_ROLE_STREAK = 14
# More than SPAM_LIMIT fined messages within SPAM_WINDOW seconds multiplies that member's fines by SPAM_MULTIPLIER for SPAM_PENALTY seconds, starting with that message.
SPAM_WINDOW = 60.0
SPAM_LIMIT = 3
SPAM_PENALTY = 600.0
SPAM_MULTIPLIER = 2
DAY_SECONDS = 86400
# The largest goal a server can set, in units.
AMOUNT_LIMIT = 10_000_000

JAR_OFF_MESSAGE = "The swear jar is off in this server. An admin can turn it on in `/swearjar settings`."
SPAM_NOTE = "Spamming swears isn't appreciated. More than 3 fined messages in a minute doubles fines for 10 minutes."

Channel = Union[discord.abc.GuildChannel, discord.Thread, int]
Override = Tuple[str, int, bool]
ListedWord = Tuple[str, str, str]

TIER_INPUTS = {"1": MILD, "2": EXPLICIT, "3": SLUR, "mild": MILD, "explicit": EXPLICIT, "slur": SLUR, "slurs": SLUR}
SHORT_TIER_NAMES = {MILD: "Mild", EXPLICIT: "Explicit", SLUR: "Slurs"}
DEFAULT_TIERS = {entry.key: entry.tier for entry in DEFAULT_ENTRIES}
PRICE_LIMIT = 1000
CENTS_LIMIT = 100_000
TOKENS_LIMIT = 10_000
NAME_LIMIT = 30


def credit_for(streak: int) -> int:
    """Returns the units paid for the clean active day that takes a streak to `streak` days: 1 unit, plus a bonus on days 7 and 30 and on every 30th day after that."""
    bonus = STREAK_BONUSES.get(streak, 0)
    if streak > REPEAT_BONUS_EVERY and streak % REPEAT_BONUS_EVERY == 0:
        bonus = REPEAT_BONUS
    return CLEAN_DAY_CREDIT + bonus


def days_to_clear(debt: int, streak: int) -> int:
    """Returns how many more clean active days it takes to pay off `debt` units from a streak of `streak` days, bonuses included. It stops counting at 3650 days."""
    days = 0
    while debt > 0 and days < 3650:
        streak += 1
        debt -= credit_for(streak)
        days += 1
    return days


def utc_day(timestamp: Optional[float] = None) -> int:
    """Returns the UTC day number (days since 1970-01-01) of a timestamp, or of now. The jar's days and weeks are all UTC."""
    return int((time.time() if timestamp is None else timestamp) // DAY_SECONDS)


def week_index(day: int) -> int:
    """Returns the number of the week a day is in. Weeks run Monday to Sunday (UTC), and day 0 was a Thursday, hence the +3."""
    return (day + 3) // 7


def week_start(day: int) -> float:
    """Returns the timestamp of the Monday 00:00 UTC that starts the week `day` is in."""
    return float((week_index(day) * 7 - 3) * DAY_SECONDS)


def prices(settings: Mapping[str, Any]) -> Dict[int, int]:
    return {MILD: settings["tier1"], EXPLICIT: settings["tier2"], SLUR: settings["tier3"]}


def money(settings: Mapping[str, Any], units: int) -> str:
    """Formats an amount of units for display, with a leading minus if it is negative.

    In money mode a unit is worth `unit_cents` cents. In token mode it is `tokens_per_unit` tokens, named with the server's singular for exactly one and its plural otherwise.
    """
    sign = "-" if units < 0 else ""
    if settings["currency"] == "money":
        return f"{sign}${abs(units) * settings['unit_cents'] / 100:,.2f}"
    amount = abs(units) * settings["tokens_per_unit"]
    return f"{sign}{amount:,} {settings['token_name'] if amount == 1 else settings['token_plural']}"


def unit_label(settings: Mapping[str, Any]) -> str:
    return money(settings, 1)


def parse_amount(settings: Mapping[str, Any], text: str) -> Optional[int]:
    """Reads a goal amount typed in the server's currency (dollars or tokens) and returns it in whole units, rounded up. Returns None if it isn't a number, is zero or less, or is over AMOUNT_LIMIT units."""
    cleaned = text.strip().replace(",", "").lstrip("$").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    if settings["currency"] == "money":
        units = math.ceil(round(value * 100) / settings["unit_cents"])
    else:
        units = math.ceil(value / settings["tokens_per_unit"])
    return units if 0 < units <= AMOUNT_LIMIT else None


def is_age_restricted(guild: discord.Guild, channel: Channel) -> bool:
    if isinstance(channel, int):
        channel = guild.get_channel_or_thread(channel)
    checker = getattr(channel, "is_nsfw", None)
    return bool(checker()) if callable(checker) else False


def jar_checks(
    guild: discord.Guild,
    channel: Channel,
    watched: Set[int],
    ignored: Set[int],
    rules: Mapping[int, str],
    skip_age_restricted: bool,
    thread_parents: Optional[Dict[int, int]] = None,
) -> Tuple[bool, int]:
    """Returns (listens, channel id): whether the jar listens in a channel, and the id to count it under, which is the parent channel for a thread.

    The tracking settings (`watched` and `ignored`) decide first. The jar's own `rules` then add exceptions for the channel, its parent or its category. An "exclude" rule always wins, and so does an age-restricted channel when `skip_age_restricted` is set. An "include" rule listens even where tracking doesn't. `thread_parents` maps thread ids to their parent channel for ids that can't be looked up.
    """
    tracked, effective_id = check_channel_with_config(guild, channel, watched, ignored, thread_parents)
    _, related = channel_family(guild, channel, thread_parents)
    rules_here = {rules.get(cid) for cid in related}
    if "exclude" in rules_here or (skip_age_restricted and is_age_restricted(guild, channel)):
        return False, effective_id
    return tracked or "include" in rules_here, effective_id


def parse_tier(text: str) -> Optional[int]:
    return TIER_INPUTS.get(text.strip().lower())


def parse_unit_cents(text: str) -> Optional[int]:
    """Reads what one unit is worth in dollars and returns it in cents, or None if it isn't a number or is outside 1 to CENTS_LIMIT cents."""
    try:
        cents = round(float(text.strip().lstrip("$")) * 100)
    except ValueError:
        return None
    return cents if 1 <= cents <= CENTS_LIMIT else None


def parse_tokens(name: str, plural: str, per_unit: str) -> Optional[Dict[str, Any]]:
    """Returns the token settings as jar_settings columns, or None unless both names are filled in and tokens per unit is a whole number from 1 to TOKENS_LIMIT. Names are cut to NAME_LIMIT characters."""
    name, plural, per_unit = name.strip()[:NAME_LIMIT], plural.strip()[:NAME_LIMIT], per_unit.strip()
    if not (name and plural and per_unit.isdigit() and 1 <= int(per_unit) <= TOKENS_LIMIT):
        return None
    return {"token_name": name, "token_plural": plural, "tokens_per_unit": int(per_unit)}


def parse_prices(values: Iterable[str]) -> Optional[Dict[str, int]]:
    """Returns the three tier prices as jar_settings columns, or None unless there are three whole numbers of units from 1 to PRICE_LIMIT."""
    values = [value.strip() for value in values]
    if len(values) != 3 or not all(value.isdigit() and 1 <= int(value) <= PRICE_LIMIT for value in values):
        return None
    return {"tier1": int(values[0]), "tier2": int(values[1]), "tier3": int(values[2])}


def shown_word(key: str) -> str:
    """Returns a list key as people read it: ~word becomes *word*, a leading = is dropped, and anything else is unchanged."""
    return f"*{key[1:]}*" if key.startswith("~") else key.lstrip("=")


def moved_words(overrides: Iterable[Override]) -> Dict[str, int]:
    """Returns {key: tier} for the default words a server has moved to a tier other than their default."""
    return {
        key: tier
        for key, tier, gone in overrides
        if not gone and key in DEFAULT_TIERS and tier != DEFAULT_TIERS[key]
    }


# The word list pages have a tab for each language plus this one, for the words a server added itself.
YOURS = "yours"
WORD_TABS = (*LANGUAGES, YOURS)


def word_tab(value: Optional[str]) -> str:
    return value if value in WORD_TABS else next(iter(LANGUAGES))


def tab_name(tab: str) -> str:
    return LANGUAGES[tab][0] if tab in LANGUAGES else "Your words"


def in_tab(key: str, tab: Optional[str]) -> bool:
    if tab is None:
        return True
    if tab == YOURS:
        return key not in DEFAULT_LANGUAGE
    return DEFAULT_LANGUAGE.get(key) == tab


def listed_words(overrides: Sequence[Override], tab: Optional[str] = None) -> Dict[int, List[ListedWord]]:
    """Returns a server's words grouped by tier, as (key, word as shown, note).

    The note says where a word came from: "yours" for the server's own, "moved from ..." for a default word in another tier, "this spelling only" for a spelling entry, or empty. `tab` limits the list to one language or to the server's own words.
    """
    moved = moved_words(overrides)
    tiers: Dict[int, List[ListedWord]] = {MILD: [], EXPLICIT: [], SLUR: []}
    for entry in sorted(build_entries(overrides), key=lambda entry: fold(entry.word)):
        if not in_tab(entry.key, tab):
            continue
        if entry.key not in DEFAULT_TIERS:
            note = "yours"
        elif entry.key in moved:
            note = f"moved from {SHORT_TIER_NAMES[DEFAULT_TIERS[entry.key]]}"
        else:
            note = "this spelling only" if entry.kind == "spelling" else ""
        tiers[entry.tier].append((entry.key, shown_word(entry.key), note))
    return tiers


def removed_words(overrides: Iterable[Override], tab: Optional[str] = None) -> List[ListedWord]:
    """Returns the default words a server removed, as (key, word as shown, default tier name)."""
    return [
        (key, shown_word(key), SHORT_TIER_NAMES[DEFAULT_TIERS[key]])
        for key, _, gone in overrides
        if gone and key in DEFAULT_TIERS and in_tab(key, tab)
    ]


def tab_counts(overrides: Sequence[Override]) -> Dict[str, int]:
    counts = dict.fromkeys(WORD_TABS, 0)
    for entry in build_entries(overrides):
        counts[YOURS if entry.key not in DEFAULT_LANGUAGE else DEFAULT_LANGUAGE[entry.key]] += 1
    return counts


def default_counts() -> Dict[str, int]:
    counts = dict.fromkeys(LANGUAGES, 0)
    for entry in DEFAULT_ENTRIES:
        counts[entry.language] += 1
    return counts


def key_changes(action: str, keys: Iterable[str], tier: Optional[int]) -> Tuple[List[Override], List[str]]:
    """Turns a remove, add or tier change on list keys into database changes. Returns (changes, forget).

    `changes` are (key, tier, removed) rows to save in jar_words. `forget` are keys whose row should be deleted instead: a server's own word that is being removed, or a default word that is back at its default tier.
    """
    changes: List[Override] = []
    forget: List[str] = []
    for key in keys:
        default = DEFAULT_TIERS.get(key)
        if action == "remove":
            if default is None:
                forget.append(key)
            else:
                changes.append((key, default, True))
        elif tier == default:
            forget.append(key)
        else:
            changes.append((key, tier, False))
    return changes, forget


def word_changes(
    action: str, words: Iterable[str], tier: Optional[int], overrides: Iterable[Override]
) -> Tuple[List[Override], List[str], List[str]]:
    """Works out what typing `words` into the add, remove or tier form changes.

    Returns (changes, forget, skipped). The first two are as in key_changes. `skipped` are the words that couldn't be used: not on the list for remove and tier, or not a word the jar can match for add.
    """
    custom = {key for key, _, gone in overrides if not gone and key not in DEFAULT_KEYS}
    keys: List[str] = []
    skipped: List[str] = []
    for word in words:
        key = custom_key(word)
        found = default_keys_for(word) or ([key] if key and (action == "add" or key in custom) else [])
        if found:
            keys += found
        else:
            skipped.append(word)
    changes, forget = key_changes(action, keys, tier)
    return changes, forget, skipped


def role_problem(guild: discord.Guild, role: Optional[discord.Role]) -> Optional[str]:
    """Returns why the bot can't hand out `role`, in words for the person setting it up, or None if it can."""
    me = guild.me
    if role is None:
        return "That role no longer exists."
    if role.is_default() or role.managed:
        return f"{role.mention} can't be given out by bots."
    if me is None or not me.guild_permissions.manage_roles:
        return "WordCounter needs the Manage Roles permission to hand out roles."
    if role >= me.top_role:
        return f"Move WordCounter's role above {role.mention} so it can give that role out."
    return None
