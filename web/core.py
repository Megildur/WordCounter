"""Shared website plumbing.

The Jinja environment and its globals, `render` and `error_page`, the signed-in user lookup, the flash messages shown from a `?msg=` code, the security headers, and `site_middleware`, which every request passes through. Pages in `web/pages.py`, `web/dashboard.py` and `web/swearjar.py` build on it.
"""

from __future__ import annotations
import calendar
import hashlib
import ipaddress
import logging
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Optional, Tuple
from urllib.parse import urlsplit
import jinja2
from aiohttp import web
from aiohttp.http_exceptions import HttpProcessingError
from multidict import MultiDict, MultiDictProxy
from cogs.utils.config import BOTZILLA_URL, CLIENT_IP_HEADER, DEFAULT_CLIENT_ID, ORIGIN_SECRET, ORIGIN_SECRET_MODE, SITE_URL, SOURCE_URL, SUPPORT_URL, invite_url
from cogs.utils.server_settings import ANALYSIS_BUSY_MESSAGE
from web import auth

log = logging.getLogger("web")

ROOT = Path(__file__).parent
STATIC_MAX_AGE = 60 * 60 * 24 * 7
# Cookies get the Secure flag only when the site runs on https, so a local http copy still works.
SECURE_COOKIES = SITE_URL.startswith("https://")
SITE_HOST = (urlsplit(SITE_URL).hostname or "").lower()
SHOWCASE_DIR = ROOT / "static" / "img" / "showcase"
SHOWCASE_TYPES = (".webp", ".png")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
WEBP_SIZE_MASK = 0x3FFF

# script-src 'none' makes the browser refuse any JavaScript, form-action 'self' keeps forms posting to this site, and frame-ancestors 'none' stops other sites framing it. Avatars come from Discord's CDN.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https://cdn.discordapp.com data:; style-src 'self'; script-src 'none'; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    # The site has no popups, so nothing else needs a window handle to it.
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Browsers are told to use https for a year, but only when the site runs on https, so a local http copy still works.
if SECURE_COOKIES:
    SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000"

# Pages that belong to a signed-in person, or start the login. They are never stored (so Back after signing out doesn't show them) and never indexed.
PRIVATE_PATHS = ("/dashboard", "/login", "/callback", "/logout")

# Flash messages. After a form POST the site redirects to a page with `?msg=<code>`, and `render` shows the matching (kind, text). An unknown code shows nothing.
MESSAGES = {
    "logged_out": ("success", "You're signed out."),
    "oauth_missing_secret": ("error", "Discord login isn't set up on this server yet (missing DISCORD_CLIENT_SECRET)."),
    "oauth_failed": ("error", "Discord login didn't go through. Please try again."),
    "oauth_cancelled": ("error", "Login cancelled. Nothing was shared with WordCounter."),
    "oauth_expired": ("error", "That login link expired. Press Log in to try again."),
    "login_banned": ("error", "You've been banned from using WordCounter, so the dashboard is closed to you."),
    "logged_out_everywhere": ("success", "You're signed out on every device."),
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
    "language_on": ("success", "That language's swears are fined now."),
    "language_off": ("success", "That language's swears aren't fined any more."),
    "need_language": ("error", "Keep at least one language on. Turn the other one on first."),
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


class Banned(web.HTTPForbidden):
    """Raised for a banned person or server. It is the only 403 whose page says "Banned from WordCounter"."""


class Refused(web.HTTPForbidden):
    """Raised for a 403 that has nothing to do with a ban, such as a form sent from another site. Its text is shown to the person."""


class RateLimit:
    """Allows `limit` requests per `window` seconds for each key. `wait` says how many seconds remain until the key may ask again, or 0 when the request is allowed and counted."""
    def __init__(self, limit: int, window: float, max_keys: int = 20_000) -> None:
        self.limit, self.window, self.max_keys = limit, window, max_keys
        self.hits: Dict[Any, Deque[float]] = {}
        self._swept = float("-inf")

    def wait(self, key: Any, now: Optional[float] = None) -> float:
        now = time.monotonic() if now is None else now
        if key not in self.hits and len(self.hits) >= self.max_keys:
            # The table is full. Quiet keys are cleared out once a window; if that doesn't make room, this new key isn't counted, so the table stays a fixed size however many addresses ask.
            self._sweep(now)
            if len(self.hits) >= self.max_keys:
                return 0.0
        recent = self.hits.setdefault(key, deque())
        while recent and now - recent[0] >= self.window:
            recent.popleft()
        if len(recent) >= self.limit:
            return self.window - (now - recent[0])
        recent.append(now)
        if len(self.hits) > 5000:
            self._sweep(now)
        return 0.0

    def _sweep(self, now: float) -> None:
        """Forgets the keys that have been quiet for a whole window. It looks through the table once a window at most, not on every request, so a flood from many addresses can't make each request walk the whole table."""
        if now - self._swept < self.window:
            return
        self._swept = now
        for stale in [other for other, hits in self.hits.items() if not hits or now - hits[-1] >= self.window]:
            del self.hits[stale]


def _asset_version() -> str:
    """Hash of the stylesheet for the `?v=` in its URL, so browsers fetch it again when it changes and otherwise keep it for STATIC_MAX_AGE."""
    return hashlib.sha1((ROOT / "static" / "css" / "style.css").read_bytes()).hexdigest()[:10]


ICON_FILES = (
    "wordcounter-96.webp", "wordcounter-256.png", "wordcounter-256.webp", "wordcounter-512.png",
    "wordcounter-512.webp", "favicon-32.png", "apple-touch-icon.png",
)


IMG = ROOT / "static" / "img"


def _files_version(paths) -> str:
    digest = hashlib.sha1()
    for path in sorted(paths):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


def _icon_version() -> str:
    return _files_version(IMG / name for name in ICON_FILES)


def _image_size(data: bytes) -> Optional[Tuple[int, int]]:
    """Returns (width, height) read from a PNG or WebP header, or None for any other data."""
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
    """Returns {name: {src, width, height}} for the PNG and WebP screenshots in static/img/showcase, with a content hash on each URL. The folder can be empty, and the templates then show text only for a name with no file."""
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


# Autoescape is on, so a value is HTML-escaped unless a template marks it safe. auto_reload is off, so templates are read once at startup and an edit needs a restart.
ENV = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(ROOT / "templates")),
    autoescape=True,
    trim_blocks=True,
    lstrip_blocks=True,
    auto_reload=False,
)
ENV.filters["num"] = _number
# These globals are worked out once when the module loads, so changed static files and new showcase images appear after a restart.
ENV.globals.update(
    site_url=SITE_URL,
    support_url=SUPPORT_URL,
    source_url=SOURCE_URL,
    botzilla_url=BOTZILLA_URL,
    asset_version=_asset_version(),
    icon_version=_icon_version(),
    pixel_version=_files_version([*(IMG / "pixel").glob("*.png"), IMG / "game-title.png"]),
    social_version=_files_version((IMG / "social").glob("*.png")),
    showcase=_showcase(),
    month_name=lambda month: calendar.month_name[month],
)


def load_templates() -> None:
    for name in ENV.list_templates():
        ENV.get_template(name)


def client_id(bot) -> int:
    """The bot's application id, for the OAuth login and invite links. Falls back to the bot user's id, then to DEFAULT_CLIENT_ID when the bot isn't ready yet."""
    return getattr(bot, "application_id", None) or (bot.user.id if getattr(bot, "user", None) else DEFAULT_CLIENT_ID)


def current_user(request: web.Request) -> Optional[auth.User]:
    """The signed-in person. On the pages that act for someone (PRIVATE_PATHS) `site_middleware` has already checked that their session still exists. On a public page only the cookie is read, which is enough to show their name in the header, since nothing there changes anything."""
    if "user" in request:
        return request["user"]
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
    """Renders a template into an HTML response. Every template also gets the signed-in user (or None), their CSRF token, the `?msg=` flash message, the invite link and the page's URL."""
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


STATIC_DIR = (ROOT / "static").resolve()


async def static_file(request: web.Request) -> web.StreamResponse:
    """Serves a file from web/static, with the ETag, Range and conditional-request handling of aiohttp's FileResponse. aiohttp's own static route decides a missing file's 404 only after the middleware has run, so that 404 had no themed page and was cached for a week. Here a missing file, a folder and a path that leaves the folder (even through a link) are all a plain 404 raised before any response exists."""
    target = (STATIC_DIR / request.match_info["filename"]).resolve()
    if not target.is_relative_to(STATIC_DIR) or not target.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(target)


def _other_domain(host: str) -> bool:
    """Whether a host name is a domain other than SITE_URL's. localhost and raw IP addresses don't count, so a request made by the server's address isn't redirected. With ORIGIN_SECRET enforced, one that lacks the secret is refused before this runs."""
    host = host.lower().rstrip(".")
    if not host or host in (SITE_HOST, "localhost"):
        return False
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return True
    return False


# Requests are limited by the visitor's address. The login pages are limited harder than the rest because each one costs a round trip to Discord or a database write, and 30 in 5 minutes is far more than a person needs. Static files aren't counted, and the dashboard has its own limit for each signed-in person on top of this.
AUTH_PATHS = ("/login", "/callback", "/logout")
AUTH_LIMIT = RateLimit(30, 300.0)
PAGE_LIMIT = RateLimit(240, 60.0)
# Request headers that a proxy may use to pass on the visitor's address. Only their names are ever logged, never what they hold.
PROXY_HEADERS = ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP", "X-Forwarded-For", "Forwarded", "X-Forwarded-Proto", "Via")
# Cloudflare's published address ranges (cloudflare.com/ips-v4 and ips-v6). They change rarely, and a range missing from here only means visitors that come through it aren't limited by address, which the log says, never that they are turned away.
CLOUDFLARE_RANGES = tuple(ipaddress.ip_network(text) for text in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22", "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20",
    "188.114.96.0/20", "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13", "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32", "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
))
_told = 0
_confirmed = False
TELL_LIMIT = 3
ORIGIN_HEADER = "X-Origin-Secret"
_origin_told = 0
_origin_confirmed = False


def _ip(text: Optional[str]) -> Optional[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        address = ipaddress.ip_address((text or "").strip().strip("[]"))
    except ValueError:
        return None
    # A dual-stack socket or a proxy may write an IPv4 visitor as ::ffff:a.b.c.d. As an IPv6 address they would all share one /64.
    if address.version == 6 and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _grouped(address) -> str:
    """The key a visitor is counted under. An IPv6 visitor usually controls a whole /64, so counting by the single address would let one person rotate through billions of them, and so the /64 is the key."""
    if address.version == 6:
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _from_cloudflare(peer) -> bool:
    return peer is not None and any(peer.version == network.version and peer in network for network in CLOUDFLARE_RANGES)


def client_address(request: web.Request) -> Optional[str]:
    """The visitor's address, or None when it can't be trusted, and nothing is limited by address then. The owner turns this on with CLIENT_IP_HEADER, because the website can't tell by itself whether the connecting address is the visitor's or a proxy's: a proxy or a CDN connects from a public address too, and counting by it would put every visitor in one group and lock them all out together. The setting is the name of the header the proxy sets to the visitor's address (for a list such as X-Forwarded-For the last entry is used, the one our own proxy added, since the rest is whatever the visitor sent), or the word `peer` when nothing stands in front of the website, and the connecting address, if it is a public one, is the visitor's. The word `cloudflare` reads CF-Connecting-IP, but only on a connection that comes from one of Cloudflare's address ranges, so someone who reaches the website without Cloudflare can't write a header to be counted as another visitor: that request has no trusted address and isn't limited."""
    if not CLIENT_IP_HEADER:
        return None
    mode = CLIENT_IP_HEADER.lower()
    if mode == "peer":
        peer = _ip(request.remote)
        return _grouped(peer) if peer is not None and peer.is_global else None
    header = "CF-Connecting-IP" if mode == "cloudflare" else CLIENT_IP_HEADER
    if mode == "cloudflare" and not _from_cloudflare(_ip(request.remote)):
        return None
    # A proxy may add its own line of the same header after the visitor's, so every line is read and the last entry of all is the one our proxy saw.
    forwarded = _ip(",".join(request.headers.getall(header, [])).split(",")[-1])
    return _grouped(forwarded) if forwarded is not None else None


def _tell_about_proxy(request: web.Request) -> None:
    """Says in the log, a few times, why a request isn't limited by address, with what is needed to decide how to fix it: whether the address that connected is private (or, with `cloudflare`, inside Cloudflare's ranges), whether CLIENT_IP_HEADER is set and present, and the names of the headers that could carry the visitor's address. No address is ever logged."""
    global _told
    if _told >= TELL_LIMIT:
        return
    _told += 1
    peer = _ip(request.remote)
    kind = "unknown" if peer is None else ("public" if peer.is_global else "private")
    if CLIENT_IP_HEADER.lower() == "cloudflare":
        if _from_cloudflare(peer):
            log.info("A request from Cloudflare isn't limited by visitor address, because it has no usable CF-Connecting-IP (the header is %s).", "present but isn't an address" if "CF-Connecting-IP" in request.headers else "missing")
        else:
            log.info(
                "A request isn't limited by visitor address, because CLIENT_IP_HEADER is cloudflare and the connection didn't come from one of Cloudflare's address ranges (the connecting address is %s, CF-Connecting-IP is %s). Visitors that reach the website through Cloudflare are limited as usual.",
                kind,
                "present" if "CF-Connecting-IP" in request.headers else "missing",
            )
        return
    present = [name for name in PROXY_HEADERS if name in request.headers]
    log.info(
        "Requests aren't limited by visitor address, because limiting by address is off until CLIENT_IP_HEADER says where to find it: the connecting address is %s, CLIENT_IP_HEADER is %s, and the request has these headers that may carry the visitor's address: %s",
        kind,
        ("peer" if CLIENT_IP_HEADER.lower() == "peer" else f"{CLIENT_IP_HEADER} ({'present' if CLIENT_IP_HEADER in request.headers else 'missing from this request'})") if CLIENT_IP_HEADER else "not set",
        ", ".join(present) or "none",
    )


def limit_by_address(request: web.Request) -> None:
    """Raises HTTPTooManyRequests when this visitor has asked for too many pages lately. Does nothing for static files, and for a visitor whose address can't be trusted."""
    if request.path.startswith("/static/"):
        return
    address = client_address(request)
    if address is None:
        _tell_about_proxy(request)
        return
    global _confirmed
    if not _confirmed:
        _confirmed = True
        log.info("Requests are limited by visitor address (CLIENT_IP_HEADER is %s).", CLIENT_IP_HEADER)
    wait = (AUTH_LIMIT if request.path in AUTH_PATHS else PAGE_LIMIT).wait(address)
    if wait:
        raise web.HTTPTooManyRequests(headers={"Retry-After": str(max(1, int(wait) + 1))})


def _old_address(request: web.Request) -> bool:
    """Returns True for a request to a domain name other than SITE_URL's, which `site_middleware` redirects to SITE_URL."""
    return _other_domain(request.url.host or "")


def sent_from_other_site(request: web.Request) -> bool:
    """Whether the browser says another site started this request. `Sec-Fetch-Site` is set by the browser and a page can't change it, and `Origin` is the older signal. A request with neither, such as one from an old browser, isn't refused here: the CSRF token still has to match."""
    if request.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
        return True
    origin = request.headers.get("Origin")
    # "null" is what a browser sends after some redirects and from some privacy settings, so it says nothing about the site.
    if not origin or origin == "null":
        return False
    host = urlsplit(origin).hostname
    return host is None or _other_domain(host)


async def post_form(request: web.Request) -> MultiDictProxy:
    """Returns the POSTed form, keeping only its text fields, so a handler never meets an uploaded file or raw bytes where it expects a string. A body that can't be read, such as a raw byte that isn't UTF-8, an unknown charset or a broken multipart upload, gives an empty form, so the CSRF check fails and the request is refused like a forged one instead of ending in a server error."""
    try:
        form = await request.post()
    except (ValueError, LookupError, RuntimeError, HttpProcessingError):
        return MultiDictProxy(MultiDict())
    return MultiDictProxy(MultiDict((key, value) for key, value in form.items() if isinstance(value, str)))


def origin_allowed(request: web.Request) -> bool:
    """Whether the request may go on, by the secret Cloudflare adds in `X-Origin-Secret`. With ORIGIN_SECRET empty everything may. With it set, a request that carries the right value always may; one that doesn't may only in `log` mode, which says so in the log (a few times, without any address) so the Cloudflare rule can be checked before `enforce` is switched on. The value is compared in constant time."""
    if not ORIGIN_SECRET:
        return True
    global _origin_told, _origin_confirmed
    given = request.headers.get(ORIGIN_HEADER)
    if given is not None and auth._same(given, ORIGIN_SECRET):
        if not _origin_confirmed:
            _origin_confirmed = True
            log.info("A request with the right %s arrived, so the Cloudflare rule that adds it works.", ORIGIN_HEADER)
        return True
    if _origin_told < TELL_LIMIT:
        _origin_told += 1
        log.info(
            "A request without the right %s %s (the header is %s). %s",
            ORIGIN_HEADER,
            "was refused" if ORIGIN_SECRET_MODE == "enforce" else "was let through",
            "missing" if given is None else "wrong",
            "It didn't come through the owner's Cloudflare rule." if ORIGIN_SECRET_MODE == "enforce" else "ORIGIN_SECRET_MODE is log, so nothing is refused yet.",
        )
    return ORIGIN_SECRET_MODE != "enforce"


@web.middleware
async def site_middleware(request: web.Request, handler) -> web.StreamResponse:
    if not origin_allowed(request):
        return web.Response(status=403, text="Forbidden", headers={"Cache-Control": "no-store", **SECURITY_HEADERS})
    if _old_address(request):
        return web.Response(status=301, headers={"Location": f"{SITE_URL}{request.path_qs}", **SECURITY_HEADERS})
    try:
        limit_by_address(request)
    except web.HTTPTooManyRequests as slow:
        response = error_page(request, 429, "Slow down", "That's a lot of requests in a short time. Wait a few seconds and try again.")
        response.headers["Retry-After"] = slow.headers.get("Retry-After", "10")
        response.headers["Cache-Control"] = "no-store"
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
    if request.path.startswith(PRIVATE_PATHS):
        # These pages act for the signed-in person, so their session has to still exist (it is ended by logging out, by "sign out everywhere", by a data deletion and by a ban). It is kept in memory for a little while, so this isn't a database read on every click.
        request["user"], request["session_ended"] = await auth.check_session(request.app["bot"].db, request.cookies.get(auth.SESSION_COOKIE))
    # Handlers raise these exceptions, for example the dashboard's ban check, and they become themed pages here. A raised redirect becomes a plain redirect response, so it gets the headers below. Anything else aiohttp raises on its own, such as a 405, gets a themed page, and a 405 keeps its Allow header.
    try:
        response = await handler(request)
    except web.HTTPNotFound:
        response = error_page(request, 404, "Page not found", "That page doesn't exist, or you don't have access to it.")
    except Banned as banned:
        response = error_page(request, 403, "Banned from WordCounter", banned.text or "You can't use WordCounter.")
    except Refused as refused:
        response = error_page(request, 403, "Not allowed", refused.text or "You can't do that.")
    except web.HTTPForbidden:
        # Raised by aiohttp itself, for example for a folder under /static/. Its text is the framework's, so it isn't shown.
        response = error_page(request, 403, "Not allowed", "You can't open that page.")
    except web.HTTPTooManyRequests as slow:
        response = error_page(request, 429, "Slow down", "That's a lot of requests in a short time. Wait a few seconds and try again.")
        response.headers["Retry-After"] = slow.headers.get("Retry-After", "10")
    except web.HTTPRedirection as moved:
        # Built again instead of returning the exception, which aiohttp has deprecated. It keeps the headers the exception carries (Location on a redirect), but not its text body.
        response = web.Response(status=moved.status, headers={name: value for name, value in moved.headers.items() if name.lower() not in ("content-type", "content-length")})
    except web.HTTPException as problem:
        response = error_page(request, problem.status, problem.reason or "Something went wrong", "That request couldn't be handled.")
        if "Allow" in problem.headers:
            response.headers["Allow"] = problem.headers["Allow"]
    # A cookie for a session that has ended is dropped, so the header doesn't keep showing someone as logged in. A login that just set a new cookie keeps it.
    if request.get("session_ended") and auth.SESSION_COOKIE not in response.cookies:
        response.del_cookie(auth.SESSION_COOKIE, path="/", secure=SECURE_COOKIES, httponly=True, samesite="Lax")
    # Static files are cached for a week, and the stylesheet's URL carries a `?v=` hash so a changed one is fetched again. Pages are "private, no-cache", so a shared cache never keeps one and the browser checks it again each time, and the pages that belong to a signed-in person are "no-store".
    if request.path.startswith(PRIVATE_PATHS):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    if request.path.startswith("/static/"):
        # Only a file that was found is kept for a week. A missing one is asked for again next time, so adding it later shows up at once.
        # A FileResponse only works out its status (200, 206, 304, 416) when it is sent, after this, but the file is known to exist because static_file raised a 404 otherwise.
        if isinstance(response, web.FileResponse) or response.status in (200, 206, 304):
            response.headers.setdefault("Cache-Control", f"public, max-age={STATIC_MAX_AGE}")
        else:
            response.headers.setdefault("Cache-Control", "no-cache")
        # A part of a file (Range) is sent as it is: compressing it would make its Content-Range wrong.
        if request.path.endswith(".css") and "Range" not in request.headers and (isinstance(response, web.FileResponse) or response.status == 200):
            response.enable_compression()
    elif isinstance(response, web.Response) and response.content_type in ("text/html", "text/plain", "application/xml"):
        response.headers.setdefault("Cache-Control", "private, no-cache")
        response.enable_compression()
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response
