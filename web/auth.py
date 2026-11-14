"""Discord login and sessions for the dashboard.

The session is a signed cookie holding the Discord user's id, name and avatar and a random session id, checked with an HMAC keyed by SESSION_SECRET. A hash of the session id is also saved in the `web_sessions` table (cogs/utils/web_sessions.py), and the cookie only counts while that row exists, so a login can be ended from the server. The login is Discord OAuth with the `identify` and `guilds` scopes, so the site learns who someone is and which servers they are in. The server list is read once at login, cut down to the servers the person owns or manages, and kept only in memory for the dashboard's "Add WordCounter" list (`WebSessionQueries.remember_web_servers`). It never decides who may open or change a server: that is checked live against the bot's own view of the member.
"""

from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from typing import Any, Collection, Dict, List, Optional, Tuple
from urllib.parse import urlencode
import aiohttp
from cogs.utils.config import DISCORD_CLIENT_SECRET, SESSION_SECRET, SITE_URL
from cogs.utils.http import get_session

log = logging.getLogger("web.auth")

# On https the cookies carry the __Host- prefix, which makes the browser refuse to take them from, or give them to, anything but this exact host: no other subdomain can plant or overwrite them. The prefix needs the Secure flag, so a local http copy keeps the plain names.
SECURE = SITE_URL.startswith("https://")
_PREFIX = "__Host-" if SECURE else ""
SESSION_COOKIE = f"{_PREFIX}wordcounter_session"
STATE_COOKIE = f"{_PREFIX}wordcounter_login"
SESSION_MAX_AGE = 86400 * 30
STATE_MAX_AGE = 600
API = "https://discord.com/api/v10"
REDIRECT_URI = f"{SITE_URL}/callback"
CONSENT_ERRORS = ("consent_required", "interaction_required", "login_required")
# Discord's permission bits for Manage Server and Administrator.
MANAGE_GUILD = 0x20
ADMINISTRATOR = 0x8
# An icon hash is lowercase letters and digits, with an a_ in front when the icon moves. It goes into a URL, so anything else is left out.
ICON_HASH = re.compile(r"[a-z0-9_]{1,64}")

User = Dict[str, Any]


def _same(first: str, second: str) -> bool:
    """Compares two strings in constant time. They are compared as bytes, because compare_digest raises TypeError for a str with non-ASCII characters, and a cookie or form field can hold any text, even a lone surrogate."""
    return hmac.compare_digest(first.encode("utf-8", "surrogatepass"), second.encode("utf-8", "surrogatepass"))


def _sign(value: str) -> str:
    # surrogatepass, because aiohttp turns a byte that isn't valid UTF-8 into a lone surrogate, which a strict encode refuses.
    return hmac.new(SESSION_SECRET.encode(), value.encode("utf-8", "surrogatepass"), hashlib.sha256).hexdigest()


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def session_hash(session_id: str) -> str:
    """What the table stores instead of the session id, so a copy of the table can't be used to sign in."""
    return hashlib.sha256(session_id.encode("utf-8", "surrogatepass")).hexdigest()


def sign_session(user: User, session_id: str) -> str:
    """Returns the cookie value "payload.signature": the user, the session id and a timestamp as base64 JSON, signed with HMAC-SHA256. It is signed, not encrypted, so the browser can read the contents but can't change them."""
    payload = base64.urlsafe_b64encode(json.dumps({**user, "sid": session_id, "ts": int(time.time())}).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: Optional[str]) -> Optional[User]:
    """Returns the user from a session cookie whose signature matches, which is under SESSION_MAX_AGE (30 days) old and has a session id. A missing cookie, a wrong signature, bad contents, an old one or one from before sessions were kept in the table gives None. This only reads the cookie: `check_session` also asks whether the session still exists."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    # compare_digest takes the same time wherever the strings differ, so the signature can't be found out a piece at a time.
    if not _same(signature, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("id", "")).isdigit():
        return None
    session_id = data.get("sid")
    if not isinstance(session_id, str) or not 16 <= len(session_id) <= 64:
        return None
    ts = data.get("ts", 0)
    if not isinstance(ts, (int, float)) or time.time() - ts > SESSION_MAX_AGE:
        return None
    return data


async def check_session(db: Any, token: Optional[str]) -> Tuple[Optional[User], bool]:
    """Returns (the user, whether the session is known to be over). The user is the one in a session cookie that is valid and whose session still exists, or None. A session that has ended gives (None, True), so the browser's cookie can be cleared. A database that can't be read gives (None, False): nobody is let in on a guess, and nobody's cookie is thrown away over a blip."""
    user = read_session(token)
    if user is None:
        return None, False
    try:
        owner = await db.web_session_user(session_hash(user["sid"]))
    except Exception as error:
        log.warning("Couldn't check a login session: %s", error)
        return None, False
    if owner == int(user["id"]):
        return user, False
    return None, True


def csrf_token(user: User) -> str:
    """The form token for a signed-in user: an HMAC of their id and their session id, so each login has its own and nothing is stored. Forms send it as `csrf` and `check_csrf` compares it."""
    return _sign(f"csrf:{user['id']}:{user['sid']}")


def check_csrf(user: User, token: Optional[str]) -> bool:
    return isinstance(token, str) and bool(token) and _same(token, csrf_token(user))


def safe_next(path: Optional[str], default: str = "/dashboard") -> str:
    """Returns `path` when it starts with a single `/` and has no backslash, space or control character, otherwise `default`. `//` and a backslash are refused because browsers read them as another site, and so is a tab or line break, because browsers drop those from a URL, which turns `/<tab>/host` into `//host`."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return default
    if any(char.isspace() or not char.isprintable() for char in path):
        return default
    return path


def new_state(next_url: str) -> Tuple[str, str]:
    """Returns (nonce, state). The nonce goes in a cookie and `state` goes to Discord with the next page inside it, so `check_state` can prove the callback belongs to the browser that started the login."""
    nonce = secrets.token_urlsafe(16)
    return nonce, f"{nonce}:{next_url}"


def check_state(state: Optional[str], nonce: Optional[str]) -> Optional[str]:
    """Returns the safe next page when the state's nonce matches the cookie's, otherwise None."""
    if not state or not nonce or ":" not in state:
        return None
    sent_nonce, next_url = state.split(":", 1)
    if not _same(sent_nonce, nonce):
        return None
    return safe_next(next_url)


def login_url(client_id: int, state: str, prompt: str = "none") -> str:
    # The default value asks Discord to skip its consent screen when it can. If Discord answers with one of CONSENT_ERRORS, the callback builds this URL again with "consent".
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify guilds",
        "prompt": prompt,
        "state": state,
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(query)}"


def avatar_url(raw: Dict[str, Any]) -> str:
    user_id, avatar = raw.get("id"), raw.get("avatar")
    if avatar:
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{'gif' if avatar.startswith('a_') else 'png'}?size=128"
    # No custom avatar: Discord's default pictures are numbered 0 to 5, picked from the id.
    return f"https://cdn.discordapp.com/embed/avatars/{(int(user_id) >> 22) % 6}.png"


def managed_servers(raw: Any) -> List[Dict[str, Any]]:
    """Cuts Discord's /users/@me/guilds answer down to the servers the person owns or has Manage Server or Administrator in, as [{id, name, icon_url}] sorted by name. Anything that isn't shaped as expected is skipped, because the answer is only read, never trusted: the id becomes an int, and the icon hash is checked before it goes into a URL."""
    servers = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            guild_id = int(entry.get("id"))
            permissions = int(entry.get("permissions") or 0)
        except (TypeError, ValueError):
            continue
        if not (entry.get("owner") is True or permissions & (MANAGE_GUILD | ADMINISTRATOR)):
            continue
        icon = entry.get("icon")
        icon_url = f"https://cdn.discordapp.com/icons/{guild_id}/{icon}.png?size=96" if isinstance(icon, str) and ICON_HASH.fullmatch(icon) else None
        name = entry.get("name")
        servers.append({"id": guild_id, "name": name.strip()[:100] if isinstance(name, str) and name.strip() else "Unnamed server", "icon_url": icon_url})
    servers.sort(key=lambda server: server["name"].lower())
    return servers


async def fetch_login(code: str, client_id: int, banned: Collection[int] = ()) -> Tuple[Optional[User], Optional[List[Dict[str, Any]]]]:
    """Trades the OAuth `code` for a token, reads the Discord user and the servers they manage, and returns (user, servers).

    The user is {id, username, name, avatar_url}, or None when the login failed. The servers are `managed_servers` of the list, or None when Discord didn't give one, which doesn't fail the login: the dashboard just offers to load it again. A person in `banned` gets None for the servers without asking Discord, since nothing is kept for them.

    The token is used for these two requests and then dropped, never stored. The id is kept as a string.
    """
    if not DISCORD_CLIENT_SECRET:
        return None, None
    form = {
        "client_id": str(client_id),
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    session = get_session()
    headers: Dict[str, str] = {}
    try:
        async with session.post(f"{API}/oauth2/token", data=form) as resp:
            if resp.status != 200:
                log.warning("Discord token exchange failed with status %s", resp.status)
                return None, None
            headers = {"Authorization": f"Bearer {(await resp.json()).get('access_token')}"}
        async with session.get(f"{API}/users/@me", headers=headers) as resp:
            if resp.status != 200:
                log.warning("Discord /users/@me failed with status %s", resp.status)
                return None, None
            raw = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        log.warning("Discord login failed: %s", error)
        return None, None
    user = {
        "id": str(raw["id"]),
        "username": raw.get("username") or "",
        "name": raw.get("global_name") or raw.get("username") or "",
        "avatar_url": avatar_url(raw),
    }
    if int(user["id"]) in banned:
        return user, None
    servers: Optional[List[Dict[str, Any]]] = None
    try:
        async with session.get(f"{API}/users/@me/guilds", headers=headers) as resp:
            if resp.status == 200:
                servers = managed_servers(await resp.json())
            else:
                log.warning("Discord /users/@me/guilds failed with status %s", resp.status)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        log.warning("Discord /users/@me/guilds failed: %s", error)
    return user, servers
