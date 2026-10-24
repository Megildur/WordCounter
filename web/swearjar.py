from __future__ import annotations
from typing import Any, Dict, List, Optional
import discord
from aiohttp import web
from cogs.utils.jar import (
    SPAM_NOTE,
    money,
    parse_amount,
    parse_prices,
    parse_tier,
    parse_tokens,
    parse_unit_cents,
    prices,
    role_problem,
    utc_day,
    week_index,
    word_changes,
)
from cogs.utils.server_settings import parse_keywords
from cogs.utils.swear_filter import DEFAULT_ENTRIES, DEFAULT_KEYS
from cogs.utils.swear_words import TIER_NAMES
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
)

routes = web.RouteTableDef()

WORD_ACTIONS = {"words_add": "add", "words_remove": "remove", "words_tier": "tier"}
RULE_ACTIONS = {"rule_include": "include", "rule_exclude": "exclude"}
ROLE_COLUMNS = ("potty_role_id", "clean_role_id")


def word_label(key: str) -> str:
    return next((entry.word for entry in DEFAULT_ENTRIES if entry.key == key), key.lstrip("~=").rstrip("*"))


def role_options(guild: discord.Guild) -> List[Dict[str, Any]]:
    roles = sorted((role for role in guild.roles if role_problem(guild, role) is None), key=lambda role: -role.position)
    return [{"id": role.id, "label": f"@{role.name}"} for role in roles]


def goal_channels(guild: discord.Guild) -> List[Dict[str, Any]]:
    channels = [
        channel for channel in guild.text_channels
        if guild.me is not None and channel.permissions_for(guild.me).send_messages
    ]
    return [{"id": channel.id, "label": f"#{channel.name}"} for channel in channels]


def goal_view(settings: Dict[str, Any], goal: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if goal is None:
        return None
    return {
        **goal,
        "progress_text": money(settings, goal["progress"]),
        "target_text": money(settings, goal["target"]),
        "target_input": money(settings, goal["target"]).lstrip("$").split(" ")[0].replace(",", ""),
        "percent": min(100, goal["progress"] * 100 // max(1, goal["target"])),
    }


@routes.get(r"/dashboard/{guild_id:\d+}/swearjar")
async def swearjar(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    settings = await db.jar_settings(guild.id)
    overrides = await db.jar_word_overrides(guild.id)
    rules = await db.jar_channel_rules(guild.id)
    me = guild.me
    return render(
        request,
        "server_swearjar.html",
        "dashboard",
        jar=settings,
        tiers=[{"tier": tier, "name": TIER_NAMES[tier], "price": price, "money": money(settings, price)} for tier, price in prices(settings).items()],
        unit=money(settings, 1),
        word_count=len(DEFAULT_ENTRIES),
        custom=[f"{key} ({TIER_NAMES[tier]})" for key, tier, gone in overrides if not gone and key not in DEFAULT_KEYS],
        removed=[word_label(key) for key, _, gone in overrides if gone],
        moved=[f"{word_label(key)} ({TIER_NAMES[tier]})" for key, tier, gone in overrides if not gone and key in DEFAULT_KEYS],
        has_overrides=bool(overrides),
        rules=[{"id": cid, "label": channel_label(guild, cid), "rule": rule} for cid, rule in sorted(rules.items())],
        add_groups=channel_groups(guild, include_categories=True, exclude=tuple(rules)),
        roles=role_options(guild),
        goal=goal_view(settings, await db.jar_goal(guild.id)),
        goal_channels=goal_channels(guild),
        can_delete=bool(me and me.guild_permissions.manage_messages),
        can_manage_roles=bool(me and me.guild_permissions.manage_roles),
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
    tier = parse_tier(str(form.get("tier", ""))) if action != "remove" else None
    if action != "remove" and tier is None:
        return "bad_tier"
    words = parse_keywords(str(form.get("words", ""))[:2000])
    changes, forget, skipped = word_changes(action, words, tier, await db.jar_word_overrides(guild.id))
    if changes:
        await db.jar_set_words(guild.id, changes)
    if forget:
        await db.jar_forget_words(guild.id, forget)
    return "words_skipped" if skipped else "words_saved"


async def _roles(db, guild: discord.Guild, form) -> str:
    updates: Dict[str, Optional[int]] = {}
    for column in ROLE_COLUMNS:
        value = str(form.get(column, ""))
        role_id = int(value) if value.isdigit() else 0
        if role_id:
            if role_problem(guild, guild.get_role(role_id)):
                return "role_problem"
            updates[column] = role_id
        else:
            updates[column] = None
    await db.jar_update(guild.id, **updates)
    return "roles_saved"


async def _goal(db, guild: discord.Guild, settings: Dict[str, Any], form) -> str:
    label = str(form.get("label", "")).strip()[:100]
    target = parse_amount(settings, str(form.get("amount", "")))
    if not label or target is None:
        return "bad_goal"
    ping = form.get("ping") == "yes"
    if ping and not (guild.me and guild.me.guild_permissions.mention_everyone):
        return "need_mention_everyone"
    channel = guild.get_channel(int(form.get("channel")) if str(form.get("channel", "")).isdigit() else 0)
    if channel is not None and not channel.permissions_for(guild.me).send_messages:
        channel = None
    await db.jar_set_goal(guild.id, label, target, channel.id if channel else None, ping)
    return "goal_saved"


@routes.post(r"/dashboard/{guild_id:\d+}/swearjar")
async def swearjar_action(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    settings = await db.jar_settings(guild.id)
    result = "expired_form"
    if csrf_ok:
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
        elif action == "rule_remove" and str(form.get("channel", "")).isdigit():
            await db.jar_set_channel_rule(guild.id, [int(form.get("channel"))], None)
            result = "rules_saved"
        elif action == "age_toggle":
            await db.jar_update(guild.id, skip_age_restricted=int(not settings["skip_age_restricted"]))
            result = "rules_saved"
        elif action == "roles":
            result = await _roles(db, guild, form)
        elif action == "goal_set":
            result = await _goal(db, guild, settings, form)
        elif action == "goal_end":
            result = "goal_ended" if form.get("confirm") == "yes" else "confirm_needed"
            if result == "goal_ended":
                await db.jar_end_goal(guild.id)
        elif action == "jar_reset":
            result = await confirmed_and_free(request, form, guild, running_only=True) or "jar_reset"
            if result == "jar_reset":
                await db.jar_reset_members(guild.id)
        refresh_jar(request, guild.id)
    return redirect(f"/dashboard/{guild.id}/swearjar?msg={result}")
