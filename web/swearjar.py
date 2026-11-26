"""The swear jar page of the dashboard, at /dashboard/{guild_id}/swearjar, for members with Manage Server.

The page shows the jar's settings, words, channel rules, roles and goal. One POST route handles every form on it, picking what to do from the `action` field, and redirects back with a `msg` code for the alert box. It uses the same database methods and rules (cogs/utils/jar.py) as /swearjar settings in Discord.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional
import discord
from aiohttp import web
from cogs.utils.jar import (
    SPAM_NOTE,
    WORD_TABS,
    YOURS,
    default_counts,
    key_changes,
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
    tab_counts,
    tab_name,
    utc_day,
    week_index,
    word_changes,
    word_tab,
)
from cogs.utils.server_settings import analysis_state, parse_keywords
from cogs.utils.swear_filter import DEFAULT_KEYS, Override, build_entries, languages_for
from cogs.utils.swear_words import LANGUAGE_COLUMNS, LANGUAGES, TIER_NAMES
from web.core import redirect, render
from web.dashboard import (
    channel_groups,
    channel_label,
    confirmed_and_free,
    guild_context,
    manager_only,
    member_access,
    read_form,
    refresh_jar,
    valid_channel,
    whole_number,
)

routes = web.RouteTableDef()

# Form action to the kind of word change it makes. WORD_RESULTS gives the msg code shown after each kind.
WORD_ACTIONS = {"words_add": "add", "words_move": "tier", "words_drop": "remove", "words_back": "back"}
WORD_RESULTS = {"add": "words_saved", "tier": "words_moved", "remove": "words_dropped", "back": "words_back"}
RULE_ACTIONS = {"rule_include": "include", "rule_exclude": "exclude"}
ROLE_COLUMNS = ("potty_role_id", "clean_role_id")


def word_lists(settings: Dict[str, Any], overrides: List[Override], tab: str) -> Dict[str, Any]:
    """Builds what the Words tab shows for one language tab: each tier's price and words, the removed words, the tab and language switches, and the totals. `total` counts the words of languages that are on plus the server's own."""
    listed = listed_words(overrides, tab)
    tiers = [
        {"tier": tier, "name": TIER_NAMES[tier], "price": price, "money": money(settings, price), "words": listed[tier]}
        for tier, price in prices(settings).items()
    ]
    counts = tab_counts(overrides)
    sizes = default_counts()
    on = set(languages_for(settings))
    return {
        "tiers": tiers,
        "removed": removed_words(overrides, tab),
        "total": sum(counts[code] for code in on) + counts[YOURS],
        "added": sum(not gone and key not in DEFAULT_KEYS for key, _, gone in overrides),
        "moved": len(moved_words(overrides)),
        "tab": tab,
        "tab_name": tab_name(tab),
        "tab_off": tab in LANGUAGES and tab not in on,
        "tabs": [{"key": key, "name": tab_name(key), "count": counts[key], "off": key in LANGUAGES and key not in on} for key in WORD_TABS],
        "languages": [{"code": code, "name": name, "on": code in on, "size": sizes[code]} for code, (name, _) in LANGUAGES.items()],
    }


def role_options(
    guild: discord.Guild,
    member: Optional[discord.Member] = None,
    *,
    single_holder: bool = False,
) -> List[Dict[str, Any]]:
    roles = sorted(
        (role for role in guild.roles if role_problem(guild, role, member, single_holder=single_holder) is None),
        key=lambda role: -role.position,
    )
    return [{"id": role.id, "label": f"@{role.name}"} for role in roles]


def current_role_info(
    guild: discord.Guild,
    role_id: Optional[int],
    member: Optional[discord.Member] = None,
    *,
    single_holder: bool = False,
) -> Optional[Dict[str, Any]]:
    """Describes an already-configured role so the UI can display and preserve it even when it is currently absent from `role_options`."""
    if not role_id:
        return None
    role = guild.get_role(role_id)
    problem = role_problem(guild, role, member, single_holder=single_holder)
    label = f"@{role.name}" if role is not None else f"Deleted role ({role_id})"
    if problem and role is not None:
        problem = problem.replace(role.mention, label)
    return {"id": role_id, "label": label, "problem": problem, "assignable": problem is None}


def _can_post(guild: discord.Guild, channel: Any) -> bool:
    if not isinstance(channel, discord.TextChannel) or guild.me is None:
        return False
    perms = channel.permissions_for(guild.me)
    return perms.view_channel and perms.send_messages


def _can_mention(guild: discord.Guild, channel: Any) -> bool:
    me = guild.me
    if me is None:
        return False
    if isinstance(channel, discord.TextChannel):
        return channel.permissions_for(me).mention_everyone
    return me.guild_permissions.mention_everyone


def goal_channels(guild: discord.Guild) -> List[Dict[str, Any]]:
    channels = [channel for channel in guild.text_channels if _can_post(guild, channel)]
    return [{"id": channel.id, "label": f"#{channel.name}"} for channel in channels]


def goal_view(settings: Dict[str, Any], goal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if goal is None:
        return None
    return {
        **goal,
        "progress_text": money(settings, goal["progress"]),
        "target_text": money(settings, goal["target"]),
        # The amount box is filled the way an admin would type it: money without the $, tokens without their name, and no thousands commas.
        "target_input": money(settings, goal["target"]).lstrip("$").split(" ")[0].replace(",", ""),
        "percent": min(100, goal["progress"] * 100 // max(1, goal["target"])),
    }


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}/swearjar")
async def swearjar(request: web.Request) -> web.Response:
    guild, member, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    db = bot.db
    settings = await db.jar_settings(guild.id)
    overrides = await db.jar_word_overrides(guild.id)
    rules = await db.jar_channel_rules(guild.id)
    words = word_lists(settings, overrides, word_tab(request.query.get("lang")))
    running, _ = await analysis_state(bot, guild.id)
    me = guild.me
    return render(
        request,
        "server_swearjar.html",
        "dashboard",
        jar=settings,
        tiers=words["tiers"],
        words=words,
        unit=money(settings, 1),
        has_overrides=bool(overrides),
        rules=[{"id": cid, "label": channel_label(guild, cid), "rule": rule} for cid, rule in sorted(rules.items())],
        add_groups=channel_groups(guild, include_categories=True, exclude=tuple(rules)),
        potty_roles=role_options(guild, member, single_holder=True),
        clean_roles=role_options(guild, member),
        potty_current=current_role_info(guild, settings["potty_role_id"], member, single_holder=True),
        clean_current=current_role_info(guild, settings["clean_role_id"], member),
        goal=goal_view(settings, await db.jar_goal(guild.id)),
        goal_channels=goal_channels(guild),
        can_delete=bool(me and me.guild_permissions.manage_messages),
        can_manage_roles=bool(me and me.guild_permissions.manage_roles),
        running=running,
        spam_note=SPAM_NOTE,
        **guild_context(guild, can_manage, "swearjar"),
    )


async def _fines(db, guild: discord.Guild, settings: Dict[str, Any], form) -> str:
    currency = form.get("currency") if form.get("currency") in ("money", "tokens") else settings["currency"]
    new_prices = parse_prices([str(form.get(f"tier{n}", "")) for n in (1, 2, 3)])
    if currency == "money":
        cents = parse_unit_cents(str(form.get("unit", "")))
        extra = {"unit_cents": cents} if cents else None
    else:
        extra = parse_tokens(str(form.get("token_name", "")), str(form.get("token_plural", "")), str(form.get("tokens_per_unit", "")))
    if new_prices is None or extra is None:
        return "bad_fines"
    await db.jar_update(guild.id, currency=currency, **new_prices, **extra)
    return "fines_saved"


async def _words(db, guild: discord.Guild, action: str, form) -> str:
    overrides = await db.jar_word_overrides(guild.id)
    tier = parse_tier(str(form.get("tier", ""))) if action in ("add", "tier") else None
    skipped: List[str] = []
    if action == "add":
        if tier is None:
            return "bad_tier"
        changes, forget, skipped = word_changes(action, parse_keywords(str(form.get("words", ""))[:2000]), tier, overrides)
    else:
        # Words arrive as list keys, so only keys that are on the list (or, for back, that were removed) are accepted.
        if action == "back":
            allowed = {key for key, _, gone in overrides if gone}
        else:
            allowed = {entry.key for entry in build_entries(overrides)}
        keys = [key for key in dict.fromkeys(form.getall("word", [])) if key in allowed]
        if not keys:
            return "no_words_selected"
        if action == "tier" and tier is None:
            return "bad_tier"
        changes, forget = ([], keys) if action == "back" else key_changes(action, keys, tier)
    if changes:
        await db.jar_set_words(guild.id, changes)
    if forget:
        await db.jar_forget_words(guild.id, forget)
    return "words_skipped" if skipped else WORD_RESULTS[action]


async def _roles(db, guild: discord.Guild, settings: Dict[str, Any], form, member: Optional[discord.Member] = None) -> str:
    updates: Dict[str, Optional[int]] = {}
    for column in ROLE_COLUMNS:
        raw = form.get(column)
        # Missing field or explicit "keep" preserves the currently saved role (prevents saving one role from silently wiping an unassignable other role).
        if raw is None or raw == "keep":
            continue
        if raw == "":
            updates[column] = None
            continue
        role_id = whole_number(raw)
        if not role_id or role_problem(guild, guild.get_role(role_id), member, single_holder=(column == "potty_role_id")):
            return "role_problem"
        updates[column] = role_id
    if updates:
        await db.jar_update(guild.id, **updates)
    return "roles_saved"


async def _goal_update(db, guild: discord.Guild, goal: Optional[Dict[str, Any]], form) -> str:
    """Updates an active goal's announcement channel and @everyone ping without resetting progress."""
    if not goal or goal.get("completed_at"):
        return "bad_goal"
    raw_channel = form.get("channel")
    if raw_channel not in (None, ""):
        channel = guild.get_channel(whole_number(raw_channel) or 0)
        if not _can_post(guild, channel):
            return "bad_goal_channel"
        channel_id = channel.id
    else:
        channel_id = goal.get("channel_id")
        channel = guild.get_channel(channel_id) if channel_id else None
    ping = form.get("ping") == "yes"
    if ping and not _can_mention(guild, channel):
        return "need_mention_everyone"
    await db.jar_update_goal(guild.id, channel_id=channel_id, ping=ping)
    return "goal_updated"


async def _goal(db, guild: discord.Guild, settings: Dict[str, Any], goal: Optional[Dict[str, Any]], form) -> str:
    label = str(form.get("label", "")).strip()[:100]
    target = parse_amount(settings, str(form.get("amount", "")))
    if not label or target is None:
        return "bad_goal"
    if goal and not goal.get("completed_at") and goal.get("progress", 0) > 0 and form.get("confirm") != "yes":
        return "confirm_needed"
    raw_channel = form.get("channel")
    channel = None
    if raw_channel not in (None, ""):
        channel = guild.get_channel(whole_number(raw_channel) or 0)
        if not _can_post(guild, channel):
            return "bad_goal_channel"
    ping = form.get("ping") == "yes"
    if ping and not _can_mention(guild, channel):
        return "need_mention_everyone"
    # Saving a new goal starts from zero progress (explicitly confirmed above when replacing an active goal with progress).
    await db.jar_set_goal(guild.id, label, target, channel.id if channel else None, ping)
    return "goal_saved"


@routes.post(r"/dashboard/{guild_id:[0-9]{1,19}}/swearjar")
async def swearjar_action(request: web.Request) -> web.Response:
    """Handles every form on the page. The `action` field picks what to do, and the result code goes into the redirect as `msg`.

    The _fines, _words, _roles, _goal_update and _goal helpers each return their code. A form with a bad CSRF token changes nothing and gets "expired_form".
    """
    guild, member, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    settings = await db.jar_settings(guild.id)
    result = "expired_form"
    if csrf_ok:
        result = "bad_action"
        if action in ("jar_on", "jar_off"):
            today = utc_day()
            await db.jar_set_enabled(guild.id, action == "jar_on", today, week_index(today))
            result = action
        elif action == "mode":
            delete = form.get("mode") == "delete"
            if delete and not (guild.me and guild.me.guild_permissions.manage_messages):
                result = "need_manage_messages"
            else:
                await db.jar_update(guild.id, delete_mode=int(delete))
                result = "jar_mode_saved"
        elif action == "fines":
            result = await _fines(db, guild, settings, form)
        elif action == "language" and form.get("lang") in LANGUAGE_COLUMNS:
            column = LANGUAGE_COLUMNS[form.get("lang")]
            turning_on = form.get("state") == "on"
            # At least one language has to stay on, checked atomically inside db_lock as in the Discord menu.
            if not await db.jar_set_language(guild.id, column, turning_on):
                result = "need_language"
            else:
                result = "language_on" if turning_on else "language_off"
        elif action in WORD_ACTIONS:
            result = await _words(db, guild, WORD_ACTIONS[action], form)
        elif action == "words_restore":
            result = "words_restored" if form.get("confirm") == "yes" else "confirm_needed"
            if result == "words_restored":
                await db.jar_reset_words(guild.id)
        elif action in RULE_ACTIONS:
            ids = [cid for cid in (valid_channel(guild, value, True) for value in form.getall("channel", [])) if cid]
            if ids:
                await db.jar_set_channel_rule(guild.id, ids, RULE_ACTIONS[action])
            result = "rules_saved" if ids else "nothing_selected"
        elif action == "rule_remove" and whole_number(form.get("channel")):
            await db.jar_set_channel_rule(guild.id, [whole_number(form.get("channel"))], None)
            result = "rules_saved"
        elif action == "age_toggle":
            await db.jar_update(guild.id, skip_age_restricted=int(not settings["skip_age_restricted"]))
            result = "rules_saved"
        elif action == "roles":
            result = await _roles(db, guild, settings, form, member)
        elif action == "goal_update":
            result = await _goal_update(db, guild, await db.jar_goal(guild.id), form)
        elif action == "goal_set":
            result = await _goal(db, guild, settings, await db.jar_goal(guild.id), form)
        elif action == "goal_end":
            result = "goal_ended" if form.get("confirm") == "yes" else "confirm_needed"
            if result == "goal_ended":
                await db.jar_end_goal(guild.id)
        elif action == "jar_reset":
            # Needs confirm=yes, and is refused while an analysis is running here.
            result = await confirmed_and_free(request, form, guild, running_only=True) or "jar_reset"
            if result == "jar_reset":
                # Drop unsaved day counts and spam tracking both before and after the delete so an in-flight _flush_days can't flush wiped counts back.
                cog = request.app["bot"].get_cog("SwearJar")
                if cog is not None:
                    cog.forget(guild.id)
                await db.jar_reset_members(guild.id)
                if cog is not None:
                    cog.forget(guild.id)
        # Drops the bot's cached jar state only when a recognized action ran.
        if result != "bad_action":
            refresh_jar(request, guild.id)
    return redirect(f"/dashboard/{guild.id}/swearjar?msg={result}&lang={word_tab(form.get('tab') if csrf_ok else None)}")
