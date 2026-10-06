from __future__ import annotations
from typing import Any, Dict, List
from aiohttp import web
from discord import AppCommandOptionType, AppCommandType, app_commands
from cogs.utils.config import DISCORD_CLIENT_SECRET, SITE_URL, SUPPORT_URL, invite_url
from web import auth
from web.core import SECURE_COOKIES, client_id, current_user, redirect, render
from web.developer import developer_profile

routes = web.RouteTableDef()

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


def command_reference(bot) -> Dict[str, List[Dict[str, Any]]]:
    slash = []
    for command in bot.tree.walk_commands():
        if isinstance(command, app_commands.Group):
            continue
        root = command.root_parent or command
        permissions = command.default_permissions or root.default_permissions
        slash.append({
            "name": f"/{command.qualified_name}",
            "description": command.description,
            "managers_only": bool(permissions and permissions.manage_guild),
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
    apps = [
        {"name": menu.name, "place": place}
        for kind, place in APP_PLACES.items()
        for menu in bot.tree.get_commands(type=kind)
    ]
    return {"slash": sorted(slash, key=lambda item: item["name"]), "apps": apps}


@routes.get("/")
async def home(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    messages, words = await bot.db.get_global_totals()
    members = sum(guild.member_count or 0 for guild in bot.guilds)
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
    return render(request, "commands.html", "commands", **command_reference(request.app["bot"]))


@routes.get("/terms")
@routes.get("/tos")
async def terms(request: web.Request) -> web.Response:
    return render(request, "terms.html", "terms")


@routes.get("/privacy")
async def privacy(request: web.Request) -> web.Response:
    return render(request, "privacy.html", "privacy")


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
    if current_user(request) and request.query.get("force") != "1":
        return redirect(next_url)
    nonce, state = auth.new_state(next_url)
    response = redirect(auth.login_url(client_id(request.app["bot"]), state))
    response.set_cookie(
        auth.STATE_COOKIE, nonce, max_age=auth.STATE_MAX_AGE, httponly=True, samesite="Lax", secure=SECURE_COOKIES, path="/"
    )
    return response


@routes.get("/callback")
async def callback(request: web.Request) -> web.Response:
    state = request.query.get("state")
    error = request.query.get("error")
    code = request.query.get("code")
    next_url = auth.check_state(state, request.cookies.get(auth.STATE_COOKIE))
    if next_url is None:
        return redirect("/?msg=oauth_expired")
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
    user = current_user(request)
    form = await request.post()
    if user is None or not auth.check_csrf(user, form.get("csrf")):
        return redirect("/")
    response = redirect("/?msg=logged_out")
    response.del_cookie(auth.SESSION_COOKIE, path="/")
    return response
