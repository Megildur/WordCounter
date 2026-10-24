from __future__ import annotations
import math
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple, Union
import discord
from cogs.utils.components import channel_family, check_channel_with_config
from cogs.utils.swear_filter import DEFAULT_ENTRIES, DEFAULT_KEYS, build_entries, custom_key, default_keys_for, fold
from cogs.utils.swear_words import EXPLICIT, MILD, SLUR

ACTIVE_DAY_MESSAGES = 5
CLEAN_DAY_CREDIT = 1
STREAK_BONUSES = {7: 3, 30: 10}
REPEAT_BONUS_EVERY = 30
REPEAT_BONUS = 10
CLEAN_ROLE_STREAK = 14
SPAM_WINDOW = 60.0
SPAM_LIMIT = 3
SPAM_PENALTY = 600.0
SPAM_MULTIPLIER = 2
DAY_SECONDS = 86400
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
    bonus = STREAK_BONUSES.get(streak, 0)
    if streak > REPEAT_BONUS_EVERY and streak % REPEAT_BONUS_EVERY == 0:
        bonus = REPEAT_BONUS
    return CLEAN_DAY_CREDIT + bonus


def days_to_clear(debt: int, streak: int) -> int:
    days = 0
    while debt > 0 and days < 3650:
        streak += 1
        debt -= credit_for(streak)
        days += 1
    return days


def utc_day(timestamp: Optional[float] = None) -> int:
    return int((time.time() if timestamp is None else timestamp) // DAY_SECONDS)


def week_index(day: int) -> int:
    return (day + 3) // 7


def week_start(day: int) -> float:
    return float((week_index(day) * 7 - 3) * DAY_SECONDS)


def prices(settings: Mapping[str, Any]) -> Dict[int, int]:
    return {MILD: settings["tier1"], EXPLICIT: settings["tier2"], SLUR: settings["tier3"]}


def money(settings: Mapping[str, Any], units: int) -> str:
    sign = "-" if units < 0 else ""
    if settings["currency"] == "money":
        return f"{sign}${abs(units) * settings['unit_cents'] / 100:,.2f}"
    amount = abs(units) * settings["tokens_per_unit"]
    return f"{sign}{amount:,} {settings['token_name'] if amount == 1 else settings['token_plural']}"


def unit_label(settings: Mapping[str, Any]) -> str:
    return money(settings, 1)


def parse_amount(settings: Mapping[str, Any], text: str) -> Optional[int]:
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
    tracked, effective_id = check_channel_with_config(guild, channel, watched, ignored, thread_parents)
    _, related = channel_family(guild, channel, thread_parents)
    rules_here = {rules.get(cid) for cid in related}
    if "exclude" in rules_here or (skip_age_restricted and is_age_restricted(guild, channel)):
        return False, effective_id
    return tracked or "include" in rules_here, effective_id


def parse_tier(text: str) -> Optional[int]:
    return TIER_INPUTS.get(text.strip().lower())


def parse_unit_cents(text: str) -> Optional[int]:
    try:
        cents = round(float(text.strip().lstrip("$")) * 100)
    except ValueError:
        return None
    return cents if 1 <= cents <= CENTS_LIMIT else None


def parse_tokens(name: str, plural: str, per_unit: str) -> Optional[Dict[str, Any]]:
    name, plural, per_unit = name.strip()[:NAME_LIMIT], plural.strip()[:NAME_LIMIT], per_unit.strip()
    if not (name and plural and per_unit.isdigit() and 1 <= int(per_unit) <= TOKENS_LIMIT):
        return None
    return {"token_name": name, "token_plural": plural, "tokens_per_unit": int(per_unit)}


def parse_prices(values: Iterable[str]) -> Optional[Dict[str, int]]:
    values = [value.strip() for value in values]
    if len(values) != 3 or not all(value.isdigit() and 1 <= int(value) <= PRICE_LIMIT for value in values):
        return None
    return {"tier1": int(values[0]), "tier2": int(values[1]), "tier3": int(values[2])}


def shown_word(key: str) -> str:
    return f"*{key[1:]}*" if key.startswith("~") else key.lstrip("=")


def moved_words(overrides: Iterable[Override]) -> Dict[str, int]:
    return {
        key: tier
        for key, tier, gone in overrides
        if not gone and key in DEFAULT_TIERS and tier != DEFAULT_TIERS[key]
    }


def listed_words(overrides: Sequence[Override]) -> Dict[int, List[ListedWord]]:
    moved = moved_words(overrides)
    tiers: Dict[int, List[ListedWord]] = {MILD: [], EXPLICIT: [], SLUR: []}
    for entry in sorted(build_entries(overrides), key=lambda entry: fold(entry.word)):
        if entry.key not in DEFAULT_TIERS:
            note = "yours"
        elif entry.key in moved:
            note = f"moved from {SHORT_TIER_NAMES[DEFAULT_TIERS[entry.key]]}"
        else:
            note = "this spelling only" if entry.kind == "spelling" else ""
        tiers[entry.tier].append((entry.key, shown_word(entry.key), note))
    return tiers


def removed_words(overrides: Iterable[Override]) -> List[ListedWord]:
    return [
        (key, shown_word(key), SHORT_TIER_NAMES[DEFAULT_TIERS[key]])
        for key, _, gone in overrides
        if gone and key in DEFAULT_TIERS
    ]


def key_changes(action: str, keys: Iterable[str], tier: Optional[int]) -> Tuple[List[Override], List[str]]:
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
