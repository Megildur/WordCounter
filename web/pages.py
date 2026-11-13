"""The public pages and the Discord login routes.

The home page, command reference, terms, privacy policy, invite and support redirects, robots.txt and sitemap.xml, plus /login, /callback and /logout. The command reference is built live from the bot's command tree, so it always matches the commands the bot has.
"""

from __future__ import annotations
from typing import Any, Dict, List
from aiohttp import web
from discord import AppCommandOptionType, AppCommandType, app_commands
from cogs.utils.config import DISCORD_CLIENT_SECRET, SITE_URL, SUPPORT_URL, invite_url
from cogs.utils.stored_data import KEPT_POLICY
from web import auth
from web.core import SECURE_COOKIES, client_id, current_user, redirect, render
from web.developer import developer_profile

routes = web.RouteTableDef()

# The pages listed in sitemap.xml. The dashboard and login routes are left out and disallowed in robots.txt.
PUBLIC_PATHS = ("/", "/commands", "/terms", "/privacy")

OPTION_TYPES = {
    AppCommandOptionType.user: "member",
    AppCommandOptionType.channel: "channel",
    AppCommandOptionType.role: "role",
    AppCommandOptionType.boolean: "yes or no",
    AppCommandOptionType.string: "text",
    AppCommandOptionType.integer: "number",
    AppCommandOptionType.number: "number",
    AppCommandOptionType.attachment: "file",
}

APP_PLACES = {AppCommandType.user: "Right-click a member", AppCommandType.message: "Right-click a message"}


# Which group on /commands a command is listed under, by its top-level name. A command that isn't here lands under "Other", so a new command needs an entry.
MODULES = {
    "leaderboard": "Stats",
    "stats": "Stats",
    "keyword": "Keywords",
    "settings": "Settings",
    "analyze_chat": "Past messages",
    "swearjar": "Swear jar",
    "help": "About WordCounter",
    "advertisement": "About WordCounter",
}
APP_MODULES = {"Message Word Count": "Stats", "User Stats": "Stats", "Swear Jar": "Swear jar"}


def module_key(name: str) -> str:
    return name.lower().replace(" ", "-")


def works_anywhere(*items) -> bool:
    """True when a command can be installed to a user's account, so it works anywhere, even in servers without the bot. Uses the first item that sets `allowed_installs`, so pass the command first and its root group after."""
    for item in items:
        installs = getattr(item, "allowed_installs", None)
        if installs is not None:
            return bool(installs.user)
    return False


def extras_of(item) -> Dict[str, str]:
    """Returns the 'details', 'user_perms' and 'bot_perms' text the /commands page shows, taken from a command's `extras` and empty when not set."""
    extras = getattr(item, "extras", None) or {}
    return {key: str(extras.get(key) or "") for key in ("details", "user_perms", "bot_perms")}


def command_reference(bot) -> Dict[str, List[Dict[str, Any]]]:
    """Builds the /commands data from the live command tree: slash commands (subcommands included, groups skipped) and the right-click menus. A command is manager-only when it or its root group requires Manage Server."""
    slash = []
    for command in bot.tree.walk_commands():
        if isinstance(command, app_commands.Group):
            continue
        root = command.root_parent or command
        permissions = command.default_permissions or root.default_permissions
        module = MODULES.get(root.name, "Other")
        slash.append({
            "name": f"/{command.qualified_name}",
            "description": command.description,
            "managers_only": bool(permissions and permissions.manage_guild),
            "reach": "anywhere" if works_anywhere(command, root) else "server",
            "module": module,
            "module_key": module_key(module),
            **extras_of(command),
            "options": [
                {
                    "name": parameter.display_name,
                    "type": OPTION_TYPES.get(parameter.type, parameter.type.name),
                    "required": parameter.required,
                    "description": parameter.description,
                }
                for parameter in command.parameters
            ],
        })
    apps = []
    for kind, place in APP_PLACES.items():
        for menu in bot.tree.get_commands(type=kind):
            module = APP_MODULES.get(menu.name, "Other")
            apps.append({
                "name": menu.name,
                "place": place,
                "description": f"{place}, then Apps > {menu.name}.",
                "reach": "anywhere" if works_anywhere(menu) else "server",
                "module": module,
                "module_key": module_key(module),
                **extras_of(menu),
            })
    return {"slash": sorted(slash, key=lambda item: item["name"]), "apps": apps}


def command_filters(request: web.Request, reference: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Applies the search, group and "works anywhere" filters from the query string and returns what the template needs, counts included. The filtering happens here on the server, since the page has no JavaScript."""
    everything = reference["slash"] + reference["apps"]
    query = request.query.get("q", "").strip()[:80]
    wanted = request.query.get("module", "")
    anywhere = request.query.get("anywhere") == "1"
    modules: Dict[str, Dict[str, Any]] = {}
    for item in everything:
        entry = modules.setdefault(item["module_key"], {"key": item["module_key"], "name": item["module"], "count": 0})
        entry["count"] += 1
    if wanted not in modules:
        wanted = ""
    needle = query.casefold()

    def shown(item: Dict[str, Any]) -> bool:
        if wanted and item["module_key"] != wanted:
            return False
        if anywhere and item["reach"] != "anywhere":
            return False
        haystack = " ".join((item["name"], item["description"], item["details"])).casefold()
        return not needle or needle in haystack

    slash = [item for item in reference["slash"] if shown(item)]
    apps = [item for item in reference["apps"] if shown(item)]
    return {
        "slash": slash,
        "apps": apps,
        "slash_total": len(reference["slash"]),
        "apps_total": len(reference["apps"]),
        "total": len(everything),
        "shown": len(slash) + len(apps),
        "modules": sorted(modules.values(), key=lambda entry: entry["name"]),
        "module": wanted,
        "module_name": modules[wanted]["name"] if wanted else "",
        "query": query,
        "anywhere": anywhere,
        "anywhere_count": sum(1 for item in everything if item["reach"] == "anywhere"),
        "filtered": bool(query or wanted or anywhere),
    }


@routes.get("/")
async def home(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    messages, words = await bot.db.get_global_totals()
    members = sum(guild.member_count or 0 for guild in bot.guilds)
    # A count of 0 becomes None, which the `num` filter shows as a dash.
    stats = {
        "servers": len(bot.guilds) or None,
        "members": members or None,
        "messages": messages or None,
        "words": words or None,
    }
    commands = command_reference(bot)
    return render(
        request,
        "index.html",
        "home",
        stats=stats,
        command_count=len(commands["slash"]),
        developer=await developer_profile(bot),
    )


@routes.get("/commands")
async def commands_page(request: web.Request) -> web.Response:
    reference = command_reference(request.app["bot"])
    return render(request, "commands.html", "commands", **command_filters(request, reference))


@routes.get("/terms")
@routes.get("/tos")
async def terms(request: web.Request) -> web.Response:
    return render(request, "terms.html", "terms")


@routes.get("/privacy")
async def privacy(request: web.Request) -> web.Response:
    # The "what we keep after a deletion request" section is rendered from KEPT_POLICY, the same text /owner kept_data quotes.
    return render(request, "privacy.html", "privacy", kept_policy=KEPT_POLICY)


@routes.get("/invite")
async def invite(request: web.Request) -> web.Response:
    return redirect(invite_url(client_id(request.app["bot"])))


@routes.get("/support")
async def support(request: web.Request) -> web.Response:
    return redirect(SUPPORT_URL)


@routes.get("/robots.txt")
async def robots(request: web.Request) -> web.Response:
    body = (
        "User-agent: *\n"
        "Disallow: /dashboard\n"
        "Disallow: /login\n"
        "Disallow: /callback\n"
        f"Sitemap: {SITE_URL}/sitemap.xml\n"
    )
    return web.Response(text=body, content_type="text/plain")


@routes.get("/sitemap.xml")
async def sitemap(request: web.Request) -> web.Response:
    urls = "".join(f"  <url><loc>{SITE_URL}{path}</loc></url>\n" for path in PUBLIC_PATHS)
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n'
    return web.Response(text=body, content_type="application/xml")


@routes.get("/login")
async def login(request: web.Request) -> web.Response:
    if not DISCORD_CLIENT_SECRET:
        return redirect("/?msg=oauth_missing_secret")
    next_url = auth.safe_next(request.query.get("next"))
    # Someone already signed in goes straight on, unless ?force=1 asks to sign in again.
    if current_user(request) and request.query.get("force") != "1":
        return redirect(next_url)
    nonce, state = auth.new_state(next_url)
    response = redirect(auth.login_url(client_id(request.app["bot"]), state))
    # SameSite=Lax rather than Strict, so the browser still sends this cookie when Discord redirects back to /callback.
    response.set_cookie(
        auth.STATE_COOKIE, nonce, max_age=auth.STATE_MAX_AGE, httponly=True, samesite="Lax", secure=SECURE_COOKIES, path="/"
    )
    return response


@routes.get("/callback")
async def callback(request: web.Request) -> web.Response:
    state = request.query.get("state")
    error = request.query.get("error")
    code = request.query.get("code")
    # The state is checked before anything else, so a stray or forged callback is turned away without contacting Discord.
    next_url = auth.check_state(state, request.cookies.get(auth.STATE_COOKIE))
    if next_url is None:
        return redirect("/?msg=oauth_expired")
    # The first attempt asks Discord to skip its consent screen. If Discord answers with one of these errors, send them back with the screen shown.
    if error in auth.CONSENT_ERRORS:
        return redirect(auth.login_url(client_id(request.app["bot"]), state, prompt="consent"))
    if error == "access_denied":
        return redirect("/?msg=oauth_cancelled")
    if error or not code:
        return redirect("/?msg=oauth_failed")

    user = await auth.fetch_user(code, client_id(request.app["bot"]))
    if user is None:
        return redirect("/?msg=oauth_failed")

    separator = "&" if "?" in next_url else "?"
    response = redirect(f"{next_url}{separator}msg=login_success")
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.sign_session(user),
        max_age=auth.SESSION_MAX_AGE,
        httponly=True,
        samesite="Lax",
        secure=SECURE_COOKIES,
        path="/",
    )
    response.del_cookie(auth.STATE_COOKIE, path="/")
    return response


@routes.post("/logout")
async def logout(request: web.Request) -> web.Response:
    # A POST with the CSRF token, so another site can't sign someone out with a link or an image.
    user = current_user(request)
    form = await request.post()
    if user is None or not auth.check_csrf(user, form.get("csrf")):
        return redirect("/")
    response = redirect("/?msg=logged_out")
    response.del_cookie(auth.SESSION_COOKIE, path="/")
    return response
