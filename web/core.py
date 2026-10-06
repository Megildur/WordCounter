from __future__ import annotations
import calendar
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional
import jinja2
from aiohttp import web
from cogs.utils.config import BOTZILLA_URL, DEFAULT_CLIENT_ID, SITE_URL, SOURCE_URL, SUPPORT_URL, invite_url
from cogs.utils.server_settings import ANALYSIS_BUSY_MESSAGE
from web import auth

ROOT = Path(__file__).parent
STATIC_MAX_AGE = 60 * 60 * 24 * 7
SECURE_COOKIES = SITE_URL.startswith("https://")

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' https://cdn.discordapp.com data:; style-src 'self'; "
        "script-src 'none'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

MESSAGES = {
    "logged_out": ("success", "You're signed out."),
    "oauth_missing_secret": ("error", "Discord login isn't set up on this server yet (missing DISCORD_CLIENT_SECRET)."),
    "oauth_failed": ("error", "Discord login didn't go through. Please try again."),
    "oauth_cancelled": ("error", "Login cancelled. Nothing was shared with WordCounter."),
    "oauth_expired": ("error", "That login link expired. Press Log in to try again."),
    "mode_saved": ("success", "Tracking mode saved."),
    "tracking_off": ("success", "Tracking is off. Existing stats stay."),
    "channels_saved": ("success", "Channel list saved."),
    "keywords_saved": ("success", "Keyword list saved."),
    "counts_reset": ("success", "Counts reset."),
    "reanalysis_ready": ("success", "Counts cleared. Run /analyze_chat in Discord to rebuild them."),
    "server_reset": ("success", "Server reset. All stats, analysis history, channels and keywords were cleared."),
    "busy": ("error", ANALYSIS_BUSY_MESSAGE),
    "confirm_needed": ("error", "Tick the confirmation box to do that."),
    "expired_form": ("error", "That form expired. Please try again."),
    "nothing_selected": ("error", "Pick at least one channel first."),
}


def _asset_version() -> str:
    return hashlib.sha1((ROOT / "static" / "css" / "style.css").read_bytes()).hexdigest()[:10]


def _number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else "–"


ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(ROOT / "templates")),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
ENV.filters["num"] = _number
ENV.globals.update(
    site_url=SITE_URL,
    support_url=SUPPORT_URL,
    source_url=SOURCE_URL,
    botzilla_url=BOTZILLA_URL,
    asset_version=_asset_version(),
    month_name=lambda month: calendar.month_name[month],
)


def client_id(bot) -> int:
    return getattr(bot, "application_id", None) or (bot.user.id if getattr(bot, "user", None) else DEFAULT_CLIENT_ID)


def current_user(request: web.Request) -> Optional[auth.User]:
    return auth.read_session(request.cookies.get(auth.SESSION_COOKIE))


def redirect(location: str) -> web.Response:
    return web.Response(status=302, headers={"Location": location})


def _alert(code: Optional[str], user: Optional[auth.User]) -> Optional[Dict[str, str]]:
    if code == "login_success":
        name = user.get("name") if user else None
        return {"type": "success", "message": f"Signed in as {name}." if name else "Signed in."}
    if code in MESSAGES:
        kind, message = MESSAGES[code]
        return {"type": kind, "message": message}
    return None


def render(request: web.Request, template: str, active_page: str = "", status: int = 200, **context: Any) -> web.Response:
    user = current_user(request)
    page = ENV.get_template(template).render(
        user=user,
        csrf=auth.csrf_token(user) if user else "",
        active_page=active_page,
        invite_url=invite_url(client_id(request.app["bot"])),
        current_url=f"{SITE_URL}{request.path}",
        alert=_alert(request.query.get("msg"), user),
        **context,
    )
    return web.Response(text=page, content_type="text/html", status=status)


def error_page(request: web.Request, status: int, title: str, message: str) -> web.Response:
    return render(request, "error.html", status=status, code=status, title=title, message=message)


@web.middleware
async def site_middleware(request: web.Request, handler) -> web.StreamResponse:
    try:
        response = await handler(request)
    except web.HTTPNotFound:
        response = error_page(request, 404, "Page not found", "That page doesn't exist, or you don't have access to it.")
    if request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", f"public, max-age={STATIC_MAX_AGE}")
        if request.path.endswith(".css"):
            response.enable_compression()
    elif isinstance(response, web.Response) and response.content_type in ("text/html", "text/plain", "application/xml"):
        response.headers.setdefault("Cache-Control", "private, no-cache")
        response.enable_compression()
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
