from __future__ import annotations
import math
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote
import discord
from aiohttp import web
from cogs.utils.server_settings import LEADERBOARDS, TRACKABLE_CHANNEL_TYPES, analysis_state, parse_keywords
from web import auth
from web.core import current_user, error_page, redirect, render

routes = web.RouteTableDef()

PAGE_SIZE = 25
KEYWORD_TOP = 10
LISTABLE_TYPES = (*TRACKABLE_CHANNEL_TYPES, discord.ChannelType.category)


def _int(value: Any) -> Optional[int]:
    text = str(value or "")
    return int(text) if text.isdigit() else None


def require_user(request: web.Request) -> auth.User:
    user = current_user(request)
    if user is None:
        raise web.HTTPFound(f"/login?next={quote(request.path_qs, safe='/?=&')}")
    return user


def icon_url(guild: discord.Guild) -> Optional[str]:
    return guild.icon.with_size(128).url if guild.icon else None


async def find_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    if member is None and not guild.chunked:
        try:
            member = await guild.fetch_member(user_id)
        except discord.HTTPException:
            return None
    return member


async def member_access(request: web.Request) -> Tuple[discord.Guild, discord.Member, bool]:
    user = require_user(request)
    guild = request.app["bot"].get_guild(int(request.match_info["guild_id"]))
    if guild is None:
        raise web.HTTPNotFound()
    member = await find_member(guild, int(user["id"]))
    if member is None:
        raise web.HTTPNotFound()
    return guild, member, member.guild_permissions.manage_guild


def manager_only(request: web.Request) -> web.Response:
    return error_page(request, 403, "Managers only", "You need the Manage Server permission in this server to see this.")


def person(guild: discord.Guild, user_id: int) -> Dict[str, Any]:
    member = guild.get_member(user_id)
    return {
        "id": user_id,
        "name": member.display_name if member else "Former member",
        "avatar_url": member.display_avatar.with_size(64).url if member else None,
    }


def channel_label(guild: discord.Guild, channel_id: int) -> str:
    if channel_id == 1:
        return "Entire server"
    channel = guild.get_channel(channel_id)
    if channel is None:
        return "Deleted channel"
    if isinstance(channel, discord.CategoryChannel):
        return f"{channel.name} (category)"
    return f"#{channel.name}"


def channel_groups(guild: discord.Guild, include_categories: bool, exclude: Tuple[int, ...] = ()) -> List[Dict[str, Any]]:
    groups = []
    for category, channels in guild.by_category():
        options = [
            {"id": channel.id, "label": f"#{channel.name}"}
            for channel in channels
            if channel.type in TRACKABLE_CHANNEL_TYPES and channel.id not in exclude
        ]
        if include_categories and category is not None and category.id not in exclude:
            options.insert(0, {"id": category.id, "label": f"{category.name} (whole category)"})
        if options:
            groups.append({"label": category.name if category else "No category", "options": options})
    return groups


def valid_channel(guild: discord.Guild, value: Any, include_categories: bool) -> Optional[int]:
    channel = guild.get_channel(_int(value) or 0)
    allowed = LISTABLE_TYPES if include_categories else TRACKABLE_CHANNEL_TYPES
    return channel.id if channel is not None and channel.type in allowed else None


async def member_stats(bot, guild: discord.Guild, user_id: int) -> Dict[str, Any]:
    words, messages, attachments, emojis, keywords = await bot.db.get_user_full_stats(guild.id, user_id)
    months = []
    for month in await bot.db.get_user_monthly_breakdown(guild.id, user_id):
        channels = sorted(month["channels"].items(), key=lambda item: item[1]["words"], reverse=True)
        months.append({
            **month,
            "keywords": sorted(((k, v) for k, v in month["keywords"].items() if v > 0), key=lambda item: -item[1]),
            "channels": [
                {"label": channel_label(guild, channel_id), **data, "keywords": sorted(data["keywords"].items())}
                for channel_id, data in channels
            ],
        })
    return {
        "totals": {"words": words, "messages": messages, "attachments": attachments, "emojis": emojis},
        "keywords": sorted(keywords, key=lambda item: -item[1]),
        "months": months,
        "empty": not (words or messages or attachments or emojis or keywords),
    }


def guild_context(guild: discord.Guild, can_manage: bool, tab: str) -> Dict[str, Any]:
    return {"guild": {"id": guild.id, "name": guild.name, "icon_url": icon_url(guild)}, "can_manage": can_manage, "tab": tab}


async def confirmed_and_free(request: web.Request, form, guild: discord.Guild, running_only: bool = False) -> Optional[str]:
    if form.get("confirm") != "yes":
        return "confirm_needed"
    running, busy = await analysis_state(request.app["bot"], guild.id)
    return "busy" if (running if running_only else busy) else None


async def read_form(request: web.Request) -> Tuple[Any, bool]:
    user = require_user(request)
    form = await request.post()
    return form, auth.check_csrf(user, form.get("csrf"))


@routes.get("/dashboard")
async def dashboard(request: web.Request) -> web.Response:
    user = require_user(request)
    user_id = int(user["id"])
    servers = []
    for guild in sorted(request.app["bot"].guilds, key=lambda g: g.name.lower()):
        member = guild.get_member(user_id)
        if member is not None:
            servers.append({
                "id": guild.id,
                "name": guild.name,
                "icon_url": icon_url(guild),
                "manager": member.guild_permissions.manage_guild,
            })
    return render(request, "dashboard.html", "dashboard", servers=servers)


@routes.get(r"/dashboard/{guild_id:\d+}")
async def own_stats(request: web.Request) -> web.Response:
    guild, member, can_manage = await member_access(request)
    bot = request.app["bot"]
    return render(
        request,
        "server_stats.html",
        "dashboard",
        stats=await member_stats(bot, guild, member.id),
        tracking=await bot.db.has_tracking_enabled(guild.id),
        **guild_context(guild, can_manage, "stats"),
    )


@routes.get(r"/dashboard/{guild_id:\d+}/leaderboard")
async def leaderboard(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    metric = request.query.get("metric") if request.query.get("metric") in LEADERBOARDS else "words"
    channel_id = valid_channel(guild, request.query.get("channel"), include_categories=False)
    context: Dict[str, Any] = {
        "metric": metric,
        "labels": LEADERBOARDS,
        "channel_id": channel_id,
        "channel_label": channel_label(guild, channel_id) if channel_id else None,
    }

    if metric == "keywords":
        context["keyword_leaders"] = [
            {
                "keyword": keyword,
                "rows": [{"rank": rank, "count": count, **person(guild, uid)} for rank, (uid, count) in enumerate(users[:KEYWORD_TOP], start=1)],
                "total": len(users),
            }
            for keyword, users in await bot.db.get_keyword_leaders(guild.id, channel_id)
        ]
    else:
        rows = await bot.db.get_leaderboard(metric, guild.id, channel_id)
        pages = max(1, math.ceil(len(rows) / PAGE_SIZE))
        page = min(max(1, _int(request.query.get("page")) or 1), pages)
        start = (page - 1) * PAGE_SIZE
        context.update(
            rows=[{"rank": start + index, "count": count, **person(guild, uid)} for index, (uid, count) in enumerate(rows[start:start + PAGE_SIZE], start=1)],
            page=page,
            pages=pages,
            total=len(rows),
        )

    context["history"] = await bot.db.get_channel_monthly_breakdown(guild.id, channel_id) if channel_id else []
    context["channel_groups"] = channel_groups(guild, include_categories=False)
    return render(request, "server_leaderboard.html", "dashboard", **context, **guild_context(guild, can_manage, "leaderboard"))


@routes.get(r"/dashboard/{guild_id:\d+}/members/{member_id:\d+}")
async def member_page(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    member_id = int(request.match_info["member_id"])
    running, busy = await analysis_state(bot, guild.id)
    return render(
        request,
        "server_member.html",
        "dashboard",
        person=person(guild, member_id),
        stats=await member_stats(bot, guild, member_id),
        analyzed=await bot.db.is_user_analyzed(guild.id, member_id),
        busy=busy,
        channel_groups=channel_groups(guild, include_categories=False),
        **guild_context(guild, can_manage, "leaderboard"),
    )


@routes.post(r"/dashboard/{guild_id:\d+}/members/{member_id:\d+}")
async def member_action(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    member_id = int(request.match_info["member_id"])
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    if not csrf_ok:
        result = "expired_form"
    else:
        result = await confirmed_and_free(request, form, guild)
        if result is None and action == "reset_member":
            await db.reset_counts(guild.id, member_id, valid_channel(guild, form.get("channel"), include_categories=False))
            result = "counts_reset"
        elif result is None and action == "reanalyze_member":
            await db.allow_reanalysis(guild.id, member_id)
            result = "reanalysis_ready"
    return redirect(f"/dashboard/{guild.id}/members/{member_id}?msg={result or 'expired_form'}")


@routes.get(r"/dashboard/{guild_id:\d+}/settings")
async def settings(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    watched, ignored = await bot.db.get_guild_tracking_config(guild.id)
    mode = "off" if not watched else ("whole" if 1 in watched else "specific")
    listed = tuple(sorted(ignored) if mode == "whole" else sorted(cid for cid in watched if cid != 1))
    running, busy = await analysis_state(bot, guild.id)
    return render(
        request,
        "server_settings.html",
        "dashboard",
        mode=mode,
        listed=[{"id": cid, "label": channel_label(guild, cid)} for cid in listed],
        add_groups=channel_groups(guild, include_categories=True, exclude=listed),
        reset_groups=channel_groups(guild, include_categories=False),
        keywords=await bot.db.get_keywords(guild.id),
        running=running,
        busy=busy,
        **guild_context(guild, can_manage, "settings"),
    )


@routes.post(r"/dashboard/{guild_id:\d+}/settings")
async def settings_action(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    result = "expired_form"
    if csrf_ok:
        watched, _ = await db.get_guild_tracking_config(guild.id)
        whole = 1 in watched
        if action == "mode" and form.get("mode") in ("whole", "specific"):
            await (db.enable_whole_server(guild.id) if form.get("mode") == "whole" else db.switch_to_specific_mode(guild.id))
            result = "mode_saved"
        elif action == "add_channels":
            ids = [cid for cid in (valid_channel(guild, value, True) for value in form.getall("channel", [])) if cid]
            if ids:
                await (db.add_ignored_channels(guild.id, ids) if whole else db.add_watched_channels(guild.id, ids))
            result = "channels_saved" if ids else "nothing_selected"
        elif action == "remove_channel" and _int(form.get("channel")):
            ids = [_int(form.get("channel"))]
            await (db.remove_ignored_channels(guild.id, ids) if whole else db.remove_watched_channels(guild.id, ids))
            result = "channels_saved"
        elif action == "keywords":
            await db.replace_keywords(guild.id, parse_keywords(str(form.get("keywords", ""))[:2000]))
            result = "keywords_saved"
        elif action == "tracking_off":
            result = await confirmed_and_free(request, form, guild, running_only=True) or "tracking_off"
            if result == "tracking_off":
                await db.disable_server_tracking(guild.id)
        elif action == "reset_channel":
            channel_id = valid_channel(guild, form.get("channel"), include_categories=False)
            result = await confirmed_and_free(request, form, guild) or ("counts_reset" if channel_id else "nothing_selected")
            if result == "counts_reset":
                await db.reset_counts(guild.id, None, channel_id)
        elif action == "reanalyze_all":
            result = await confirmed_and_free(request, form, guild) or "reanalysis_ready"
            if result == "reanalysis_ready":
                await db.allow_reanalysis(guild.id)
        elif action == "reset_server":
            result = await confirmed_and_free(request, form, guild, running_only=True) or "server_reset"
            if result == "server_reset":
                await db.reset_entire_server(guild.id)
    return redirect(f"/dashboard/{guild.id}/settings?msg={result}")
