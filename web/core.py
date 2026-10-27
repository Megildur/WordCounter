from __future__ import annotations
import calendar
import hashlib
import ipaddress
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlsplit
import jinja2
from aiohttp import web
from cogs.utils.config import BOTZILLA_URL, DEFAULT_CLIENT_ID, SITE_URL, SOURCE_URL, SUPPORT_URL, invite_url
from cogs.utils.server_settings import ANALYSIS_BUSY_MESSAGE
from web import auth

ROOT = Path(__file__).parent
STATIC_MAX_AGE = 60 * 60 * 24 * 7
SECURE_COOKIES = SITE_URL.startswith("https://")
SITE_HOST = (urlsplit(SITE_URL).hostname or "").lower()
SHOWCASE_DIR = ROOT / "static" / "img" / "showcase"
SHOWCASE_TYPES = (".webp", ".png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WEBP_SIZE_MASK = 0x3FFF

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
    "jar_on": ("success", "The swear jar is on. Swears are fined from now on."),
    "jar_off": ("success", "The swear jar is off. Wallets, streaks and counts stay."),
    "jar_mode_saved": ("success", "Saved what happens when someone swears."),
    "need_manage_messages": ("error", "WordCounter needs the Manage Messages permission to delete messages."),
    "fines_saved": ("success", "Fines saved."),
    "bad_fines": ("error", "Check the fines: prices are whole units from 1 to 1000, a unit is worth 0.01 to 1000, and tokens need both names."),
    "words_saved": ("success", "Words added."),
    "words_skipped": ("error", "Some words were skipped. A word has to start with a letter and be 40 characters or less."),
    "words_moved": ("success", "Moved the ticked words."),
    "words_dropped": ("success", "Took the ticked words off the list."),
    "words_back": ("success", "Put the ticked words back on the list."),
    "no_words_selected": ("error", "Tick at least one word first."),
    "bad_tier": ("error", "Pick a tier first."),
    "words_restored": ("success", "The default word list is back."),
    "rules_saved": ("success", "Swear jar channels saved."),
    "roles_saved": ("success", "Roles saved."),
    "role_problem": ("error", "WordCounter can't give that role out. It needs Manage Roles, and its own role has to be above the role you pick."),
    "goal_saved": ("success", "Goal saved."),
    "bad_goal": ("error", "Give the goal a name and an amount above zero."),
    "need_mention_everyone": ("error", "WordCounter needs the Mention Everyone permission to ping @everyone."),
    "goal_ended": ("success", "Goal ended."),
    "jar_reset": ("success", "Swear jar reset."),
}


def _asset_version() -> str:
    return hashlib.sha1((ROOT / "static" / "css" / "style.css").read_bytes()).hexdigest()[:10]


def _image_size(data: bytes) -> Optional[Tuple[int, int]]:
    if data.startswith(PNG_SIGNATURE) and len(data) >= 24:
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    chunk = data[12:16]
    if chunk == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if chunk == b"VP8 ":
        return int.from_bytes(data[26:28], "little") & WEBP_SIZE_MASK, int.from_bytes(data[28:30], "little") & WEBP_SIZE_MASK
    if chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & WEBP_SIZE_MASK), 1 + ((bits >> 14) & WEBP_SIZE_MASK)
    return None


def _showcase() -> Dict[str, Dict[str, Any]]:
    found: Dict[str, Dict[str, Any]] = {}
    if not SHOWCASE_DIR.is_dir():
        return found
    for path in sorted(SHOWCASE_DIR.iterdir()):
        if path.suffix.lower() not in SHOWCASE_TYPES or path.stem in found:
            continue
        data = path.read_bytes()
        size = _image_size(data)
        if size is None:
            continue
        found[path.stem] = {
            "src": f"/static/img/showcase/{path.name}?v={hashlib.sha1(data).hexdigest()[:10]}",
            "width": size[0],
            "height": size[1],
        }
    return found


def _number(value: Any) -> str:
    return f"{value:,}" if isinstance(value, int) and not isinstance(value, bool) else "–"


ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(ROOT / "templates")),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    auto_reload=False,
)
ENV.filters["num"] = _number
ENV.globals.update(
    site_url=SITE_URL,
    support_url=SUPPORT_URL,
    source_url=SOURCE_URL,
    botzilla_url=BOTZILLA_URL,
    asset_version=_asset_version(),
    showcase=_showcase(),
    month_name=lambda month: calendar.month_name[month],
)


def load_templates() -> None:
    for name in ENV.list_templates():
        ENV.get_template(name)


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


def _old_address(request: web.Request) -> bool:
    host = (request.url.host or "").lower().rstrip(".")
    if not host or host in (SITE_HOST, "localhost"):
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return False


@web.middleware
async def site_middleware(request: web.Request, handler) -> web.StreamResponse:
    if _old_address(request):
        return web.Response(status=301, headers={"Location": f"{SITE_URL}{request.path_qs}", **SECURITY_HEADERS})
    try:
        response = await handler(request)
    except web.HTTPNotFound:
        response = error_page(request, 404, "Page not found", "That page doesn't exist, or you don't have access to it.")
    except web.HTTPForbidden as forbidden:
        response = error_page(request, 403, "Banned from WordCounter", forbidden.text or "You can't use WordCounter.")
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
