"""The signed-in dashboard at /dashboard.

Everyone sees their own stats in the servers they share with the bot. People with Manage Server also get that server's leaderboard, members, per-member resets and settings, which do the same things as /settings in Discord. Pages are Jinja templates, and every change is a plain HTML form POST that redirects back with a `?msg=` code, since the site has no JavaScript.
"""

from __future__ import annotations
import math
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode
import discord
from aiohttp import web
from cogs.utils.jar import SPAM_NOTE, days_to_clear, money, unit_label, utc_day, week_start
from cogs.utils.server_settings import LEADERBOARDS, TRACKABLE_CHANNEL_TYPES, analysis_state, parse_keywords
from web import auth
from web.core import ENV, Banned, RateLimit, Refused, current_user, error_page, post_form, redirect, render, sent_from_other_site

routes = web.RouteTableDef()

# The only page under /dashboard/{guild_id} that a member without Manage Server may open: their own stats. `dashboard_guard` closes every other page under a server to non-managers, so a page added later is for managers until it is listed here.
MEMBER_PAGES = {"/dashboard/{guild_id}"}

PAGE_SIZE = 25
KEYWORD_TOP = 10
# Every dashboard page reads the database, whose one connection everything shares, so a signed-in person gets 90 requests per 30 seconds, much more than clicking around needs.
REQUESTS = RateLimit(90, 30.0)
LISTABLE_TYPES = (*TRACKABLE_CHANNEL_TYPES, discord.ChannelType.category)


def whole_number(value: Any) -> Optional[int]:
    """Returns the ID or page number in a query or form value as an int, or None when it isn't one. str.isdigit() also accepts characters like a superscript two, which int() refuses, and int() refuses a very long run of digits, so either would have been a server error. IDs fit in 63 bits, the size of the database's BIGINT."""
    text = str(value or "")
    if not (text.isascii() and text.isdigit()) or len(text) > 19:
        return None
    number = int(text)
    return number if number < 2 ** 63 else None


def page_url(path: str, anchor: str = "", **query: Any) -> str:
    """Builds a URL from a path, a query string that leaves out empty values, and an optional #anchor."""
    filled = {key: value for key, value in query.items() if value not in (None, "")}
    return path + (f"?{urlencode(filled)}" if filled else "") + (f"#{anchor}" if anchor else "")


def leaderboard_url(guild_id: int, metric: str, channel_id: Optional[int] = None, page: Optional[int] = None) -> str:
    return page_url(f"/dashboard/{guild_id}/leaderboard", metric=metric, channel=channel_id, page=page)


# Registered as a template global, so templates build leaderboard links with this same function.
ENV.globals["leaderboard_url"] = leaderboard_url


def paginate(items: List[Any], requested: Any) -> Tuple[List[Any], int, int]:
    """Returns (the items on the requested page, the page number, the page count). A page number outside the range is clamped to it. Pages hold PAGE_SIZE items."""
    pages = max(1, math.ceil(len(items) / PAGE_SIZE))
    page = min(max(1, whole_number(requested) or 1), pages)
    start = (page - 1) * PAGE_SIZE
    return items[start:start + PAGE_SIZE], page, pages


def require_user(request: web.Request) -> auth.User:
    """Returns the signed-in user. Someone who isn't signed in is redirected to /login and back to this page afterwards, and a banned user gets a 403. Every dashboard handler goes through this, directly or through `member_access`."""
    user = current_user(request)
    if user is None:
        raise web.HTTPFound(f"/login?next={quote(request.path_qs, safe='/?=&')}")
    if int(user["id"]) in request.app["bot"].blacklisted:
        raise Banned(text="You've been banned from using WordCounter, so its dashboard is closed to you.")
    # A handler that checks again, such as read_form after member_access, doesn't count the request twice.
    if not request.get("rate_checked"):
        request["rate_checked"] = True
        wait = REQUESTS.wait(int(user["id"]))
        if wait:
            raise web.HTTPTooManyRequests(headers={"Retry-After": str(math.ceil(wait))})
    return user


def icon_url(guild: discord.Guild) -> Optional[str]:
    return guild.icon.with_size(128).url if guild.icon else None


# (server id, user id) to when Discord answered that they aren't in the server, asked because its member list isn't fully cached. A refresh, or a link to many server ids, then doesn't ask Discord each time. Only that answer is kept: a found member could have lost a permission, and an error could be passing.
_asked: Dict[Tuple[int, int], float] = {}
ASKED_SECONDS = 20.0
ASKED_LIMIT = 2000


async def find_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(user_id)
    # Only ask Discord when the member list isn't fully cached. A fully cached list already shows the person isn't in the server.
    if member is None and not guild.chunked:
        key = (guild.id, user_id)
        asked = _asked.get(key)
        if asked is not None and time.monotonic() - asked < ASKED_SECONDS:
            return None
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            if len(_asked) >= ASKED_LIMIT:
                _asked.clear()
            _asked[key] = time.monotonic()
        except discord.HTTPException:
            return None
    return member


def manages(member: discord.Member) -> bool:
    """Whether a member may use the manager pages: they have Manage Server and aren't timed out. Discord takes a timed-out member's permissions away until the timeout ends, and the owner and administrators can't be timed out, so the site does the same."""
    return member.guild_permissions.manage_guild and not member.is_timed_out()


async def member_access(request: web.Request) -> Tuple[discord.Guild, discord.Member, bool]:
    """Checks the signed-in person may see this server's dashboard, and returns (guild, their member, whether they may use the manager pages).

    The bot not being in the server, or the person not being in it, gives the same 404 as any missing page. A banned server gives a 403. `dashboard_guard` runs this before every `/dashboard/{guild_id}` handler and keeps the answer on the request, so a handler that calls it again gets the same answer without another look-up, and the manager-only pages then check the flag themselves too.
    """
    if "access" in request:
        return request["access"]
    user = require_user(request)
    # The route only matches 1 to 19 ASCII digits, and this keeps a number that big from being turned into an ID that doesn't exist.
    guild_id = whole_number(request.match_info["guild_id"])
    guild = request.app["bot"].get_guild(guild_id) if guild_id else None
    if guild is None:
        raise web.HTTPNotFound()
    if guild.id in request.app["bot"].blacklisted:
        raise Banned(text="This server has been banned from using WordCounter, so its dashboard is closed.")
    member = await find_member(guild, int(user["id"]))
    if member is None:
        raise web.HTTPNotFound()
    request["timed_out"] = member.guild_permissions.manage_guild and member.is_timed_out()
    request["access"] = guild, member, manages(member)
    return request["access"]


def manager_only(request: web.Request) -> web.Response:
    if request.get("timed_out"):
        return error_page(request, 403, "Timed out", "Discord takes a member's permissions away while they're timed out, so the manager pages open again when your timeout ends.")
    return error_page(request, 403, "Managers only", "You need the Manage Server permission in this server to see this.")


@web.middleware
async def dashboard_guard(request: web.Request, handler) -> web.StreamResponse:
    """Runs before every handler under /dashboard, so none of them can forget the checks: the person is signed in and not banned, a form posted from another site is refused, the person is in the server and (for every page but their own stats) may manage it, and a POST carries their CSRF token. A form with a bad token is sent back to its page with the "form expired" message and changes nothing.

    It goes by the route's pattern, not by each handler, so a new page is covered the moment it is added.
    """
    resource = request.match_info.route.resource
    pattern = resource.canonical if resource is not None else ""
    if pattern != "/dashboard" and not pattern.startswith("/dashboard/"):
        return await handler(request)
    user = require_user(request)
    posting = request.method == "POST"
    if posting and sent_from_other_site(request):
        raise Refused(text="That form was sent from another site, so it was refused.")
    if "guild_id" in request.match_info:
        _, _, manager = await member_access(request)
        if not manager and pattern not in MEMBER_PAGES:
            return manager_only(request)
    if posting and not auth.check_csrf(user, (await post_form(request)).get("csrf")):
        raise web.HTTPFound(f"{request.path}?msg=expired_form")
    return await handler(request)


def person(guild: discord.Guild, user_id: int) -> Dict[str, Any]:
    """Name and avatar for a row in a list. Someone who has left the server (not in the member cache) shows as "Former member" with no avatar."""
    member = guild.get_member(user_id)
    return {
        "id": user_id,
        "name": member.display_name if member else "Former member",
        "avatar_url": member.display_avatar.with_size(64).url if member else None,
    }


def channel_label(guild: discord.Guild, channel_id: int) -> str:
    # Channel id 1 is stored in the channels table to mean the whole server.
    if channel_id == 1:
        return "Entire server"
    channel = guild.get_channel(channel_id)
    if channel is None:
        return "Deleted channel"
    if isinstance(channel, discord.CategoryChannel):
        return f"{channel.name} (category)"
    return f"#{channel.name}"


def channel_groups(guild: discord.Guild, include_categories: bool, exclude: Tuple[int, ...] = ()) -> List[Dict[str, Any]]:
    """Groups the server's trackable channels by category for a channel picker, as a list of {label, options}.

    With `include_categories` each category also gets a "whole category" option at the top of its group. Ids in `exclude`, such as ones already listed, are left out.
    """
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


def channel_menu(groups: List[Dict[str, Any]], selected: Optional[int], link: Callable[[Optional[int]], str], empty_label: str) -> Dict[str, Any]:
    """Builds a channel picker out of links, because there is no JavaScript to react to a select. Each option's `url` (from `link(channel id)`) reloads the page with that channel chosen, and the first option, `empty_label`, clears the choice."""
    return {
        "current": next((option["label"] for group in groups for option in group["options"] if option["id"] == selected), empty_label),
        "selected": selected,
        "groups": [{"label": None, "options": [{"label": empty_label, "url": link(None), "selected": selected is None}]}] + [
            {
                "label": group["label"],
                "options": [{"label": option["label"], "url": link(option["id"]), "selected": option["id"] == selected} for option in group["options"]],
            }
            for group in groups
        ],
    }


def valid_channel(guild: discord.Guild, value: Any, include_categories: bool) -> Optional[int]:
    """Returns the channel id when it exists in this server and is a type the dashboard allows, otherwise None. Query and form values come from the browser, so they are checked before use."""
    channel = guild.get_channel(whole_number(value) or 0)
    allowed = LISTABLE_TYPES if include_categories else TRACKABLE_CHANNEL_TYPES
    return channel.id if channel is not None and channel.type in allowed else None


async def member_stats(bot, guild: discord.Guild, user_id: int) -> Dict[str, Any]:
    """One member's totals, keywords and monthly history shaped for the templates, months newest first with each month's channels sorted by words. `empty` is True when nothing is recorded."""
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


async def jar_summary(bot, guild_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """The swear jar numbers for one member, or None when the jar is off in this server."""
    settings = await bot.db.jar_settings(guild_id)
    if not settings["enabled"]:
        return None
    data = await bot.db.jar_member(guild_id, user_id, week_start(utc_day()))
    balance = data["balance"]
    spam_until = data["spam_until"] if data["spam_until"] > time.time() else None
    return {
        "label": "Debt" if balance < 0 else "Wallet",
        "balance": money(settings, balance),
        "in_debt": balance < 0,
        "streak": data["streak"],
        "best": data["best_streak"],
        "swears": data["swears"],
        "week": money(settings, data["week_fines"]),
        "days_to_clear": days_to_clear(-balance, data["streak"]) if balance < 0 else 0,
        "spam_until": datetime.fromtimestamp(spam_until, timezone.utc).strftime("%H:%M UTC") if spam_until else None,
        "spam_note": SPAM_NOTE,
        "unit": unit_label(settings),
    }


def refresh_jar(request: web.Request, guild_id: int) -> None:
    """Drops the swear jar cog's cached state for a server, so a change made on the website takes effect in Discord at once instead of when the cache expires."""
    cog = request.app["bot"].get_cog("SwearJar")
    if cog is not None:
        cog.invalidate(guild_id)


def guild_context(guild: discord.Guild, can_manage: bool, tab: str) -> Dict[str, Any]:
    return {"guild": {"id": guild.id, "name": guild.name, "icon_url": icon_url(guild)}, "can_manage": can_manage, "tab": tab}


async def confirmed_and_free(request: web.Request, form, guild: discord.Guild, running_only: bool = False) -> Optional[str]:
    """Returns the `?msg=` code for why a destructive action must not run, or None when it may.

    "confirm_needed" means the confirmation box wasn't ticked. "busy" means an analysis is running or, unless `running_only` is set, a saved run exists.
    """
    if form.get("confirm") != "yes":
        return "confirm_needed"
    running, busy = await analysis_state(request.app["bot"], guild.id)
    return "busy" if (running if running_only else busy) else None


async def read_form(request: web.Request) -> Tuple[Any, bool]:
    """Returns the POSTed form and whether its CSRF token is valid for the signed-in user. Handlers must check the flag before changing anything."""
    user = require_user(request)
    form = await post_form(request)
    return form, auth.check_csrf(user, form.get("csrf"))


@routes.get("/dashboard")
async def dashboard(request: web.Request) -> web.Response:
    user = require_user(request)
    user_id = int(user["id"])
    servers = []
    for guild in sorted(request.app["bot"].guilds, key=lambda g: g.name.lower()):
        member = guild.get_member(user_id)
        if member is not None and guild.id not in request.app["bot"].blacklisted:
            servers.append({
                "id": guild.id,
                "name": guild.name,
                "icon_url": icon_url(guild),
                "manager": manages(member),
            })
    return render(request, "dashboard.html", "dashboard", servers=servers)


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}")
async def own_stats(request: web.Request) -> web.Response:
    guild, member, can_manage = await member_access(request)
    bot = request.app["bot"]
    return render(
        request,
        "server_stats.html",
        "dashboard",
        stats=await member_stats(bot, guild, member.id),
        tracking=await bot.db.has_tracking_enabled(guild.id),
        jar=await jar_summary(bot, guild.id, member.id),
        **guild_context(guild, can_manage, "stats"),
    )


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}/leaderboard")
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
        shown, page, pages = paginate(rows, request.query.get("page"))
        start = (page - 1) * PAGE_SIZE
        context.update(
            rows=[{"rank": start + index, "count": count, **person(guild, uid)} for index, (uid, count) in enumerate(shown, start=1)],
            page=page,
            pages=pages,
            total=len(rows),
        )

    context["history"] = await bot.db.get_channel_monthly_breakdown(guild.id, channel_id) if channel_id else []
    context["channel_menu"] = channel_menu(
        channel_groups(guild, include_categories=False), channel_id, lambda cid: leaderboard_url(guild.id, metric, cid), "Whole server"
    )
    return render(request, "server_leaderboard.html", "dashboard", **context, **guild_context(guild, can_manage, "leaderboard"))


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}/members")
async def members(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    query = request.query.get("q", "").strip()[:100]
    needle = query.casefold()
    found = sorted(
        (m for m in guild.members if not m.bot and (needle in m.display_name.casefold() or needle in m.name.casefold())),
        key=lambda m: m.display_name.casefold(),
    )
    shown, page, pages = paginate(found, request.query.get("page"))
    words = dict(await db.get_leaderboard("words", guild.id, None))
    messages = dict(await db.get_leaderboard("messages", guild.id, None))

    def link(number: int) -> str:
        return page_url(f"/dashboard/{guild.id}/members", q=query, page=number)

    return render(
        request,
        "server_members.html",
        "dashboard",
        rows=[{**person(guild, m.id), "words": words.get(m.id, 0), "messages": messages.get(m.id, 0)} for m in shown],
        query=query,
        total=len(found),
        page=page,
        pages=pages,
        prev_url=link(page - 1) if page > 1 else None,
        next_url=link(page + 1) if page < pages else None,
        **guild_context(guild, can_manage, "members"),
    )


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}/members/{member_id:[0-9]{1,19}}")
async def member_page(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    member_id = whole_number(request.match_info["member_id"])
    if member_id is None:
        raise web.HTTPNotFound()
    running, busy = await analysis_state(bot, guild.id)
    return render(
        request,
        "server_member.html",
        "dashboard",
        person=person(guild, member_id),
        stats=await member_stats(bot, guild, member_id),
        analyzed=await bot.db.is_user_analyzed(guild.id, member_id),
        jar=await jar_summary(bot, guild.id, member_id),
        busy=busy,
        reset_menu=channel_menu(
            channel_groups(guild, include_categories=False),
            valid_channel(guild, request.query.get("channel"), include_categories=False),
            lambda cid: page_url(f"/dashboard/{guild.id}/members/{member_id}", "reset-member", channel=cid),
            "Every channel",
        ),
        **guild_context(guild, can_manage, "members"),
    )


@routes.post(r"/dashboard/{guild_id:[0-9]{1,19}}/members/{member_id:[0-9]{1,19}}")
async def member_action(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    member_id = whole_number(request.match_info["member_id"])
    if member_id is None:
        raise web.HTTPNotFound()
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    # Nothing changes unless the CSRF token matches. Every outcome ends in a redirect with a `?msg=` code, so refreshing the page never repeats the action.
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
        elif result is None and action == "reset_jar":
            await db.jar_reset_members(guild.id, member_id)
            refresh_jar(request, guild.id)
            result = "jar_reset"
    return redirect(f"/dashboard/{guild.id}/members/{member_id}?msg={result or 'expired_form'}")


@routes.get(r"/dashboard/{guild_id:[0-9]{1,19}}/settings")
async def settings(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    bot = request.app["bot"]
    watched, ignored = await bot.db.get_guild_tracking_config(guild.id)
    # The same rule as /settings: no channel rows means tracking is off, and channel id 1 means the whole server.
    mode = "off" if not watched else ("whole" if 1 in watched else "specific")
    # The list shown is the ignore list in whole server mode, and the watch list otherwise.
    listed = tuple(sorted(ignored) if mode == "whole" else sorted(cid for cid in watched if cid != 1))
    running, busy = await analysis_state(bot, guild.id)
    return render(
        request,
        "server_settings.html",
        "dashboard",
        mode=mode,
        listed=[{"id": cid, "label": channel_label(guild, cid)} for cid in listed],
        add_groups=channel_groups(guild, include_categories=True, exclude=listed),
        reset_menu=channel_menu(
            channel_groups(guild, include_categories=False),
            valid_channel(guild, request.query.get("reset_channel"), include_categories=False),
            lambda cid: page_url(f"/dashboard/{guild.id}/settings", "reset-channel", reset_channel=cid),
            "Pick a channel",
        ),
        keywords=await bot.db.get_keywords(guild.id),
        running=running,
        busy=busy,
        **guild_context(guild, can_manage, "settings"),
    )


@routes.post(r"/dashboard/{guild_id:[0-9]{1,19}}/settings")
async def settings_action(request: web.Request) -> web.Response:
    guild, _, can_manage = await member_access(request)
    if not can_manage:
        return manager_only(request)
    db = request.app["bot"].db
    form, csrf_ok = await read_form(request)
    action = form.get("action")
    result = "expired_form"
    # Same as member_action: no change without a valid CSRF token, and every outcome redirects with a `?msg=` code.
    if csrf_ok:
        watched, _ = await db.get_guild_tracking_config(guild.id)
        whole = 1 in watched
        if action == "mode" and form.get("mode") in ("whole", "specific"):
            await (db.enable_whole_server(guild.id) if form.get("mode") == "whole" else db.switch_to_specific_mode(guild.id))
            result = "mode_saved"
        elif action == "add_channels":
            # In whole server mode the picked channels go on the ignore list, otherwise on the watch list.
            ids = [cid for cid in (valid_channel(guild, value, True) for value in form.getall("channel", [])) if cid]
            if ids:
                await (db.add_ignored_channels(guild.id, ids) if whole else db.add_watched_channels(guild.id, ids))
            result = "channels_saved" if ids else "nothing_selected"
        elif action == "remove_channel" and whole_number(form.get("channel")):
            ids = [whole_number(form.get("channel"))]
            await (db.remove_ignored_channels(guild.id, ids) if whole else db.remove_watched_channels(guild.id, ids))
            result = "channels_saved"
        elif action == "keywords":
            # The same 2000 character limit as the keyword list in Discord's edit modal.
            await db.replace_keywords(guild.id, parse_keywords(str(form.get("keywords", ""))[:2000]))
            result = "keywords_saved"
        elif action == "tracking_off":
            # running_only: turning tracking off and resetting the server are blocked only by an analysis that is running now, while the count resets below also wait for an interrupted one.
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
