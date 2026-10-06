from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
import aiohttp
from cogs.utils.config import DISCORD_CLIENT_SECRET, SESSION_SECRET, SITE_URL
from cogs.utils.http import get_session

log = logging.getLogger("web.auth")

SESSION_COOKIE = "wordcounter_session"
STATE_COOKIE = "wordcounter_login"
SESSION_MAX_AGE = 86400 * 30
STATE_MAX_AGE = 600
API = "https://discord.com/api/v10"
REDIRECT_URI = f"{SITE_URL}/callback"
CONSENT_ERRORS = ("consent_required", "interaction_required", "login_required")

User = Dict[str, Any]


def _sign(value: str) -> str:
    return hmac.new(SESSION_SECRET.encode(), value.encode(), hashlib.sha256).hexdigest()


def sign_session(user: User) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({**user, "ts": int(time.time())}).encode()).decode()
    return f"{payload}.{_sign(payload)}"


def read_session(token: Optional[str]) -> Optional[User]:
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode()))
    except ValueError:
        return None
    if not isinstance(data, dict) or not str(data.get("id", "")).isdigit():
        return None
    if time.time() - data.get("ts", 0) > SESSION_MAX_AGE:
        return None
    return data


def csrf_token(user: User) -> str:
    return _sign(f"csrf:{user['id']}:{user['ts']}")


def check_csrf(user: User, token: Optional[str]) -> bool:
    return bool(token) and hmac.compare_digest(token, csrf_token(user))


def safe_next(path: Optional[str], default: str = "/dashboard") -> str:
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path:
        return default
    return path


def new_state(next_url: str) -> Tuple[str, str]:
    nonce = secrets.token_urlsafe(16)
    return nonce, f"{nonce}:{next_url}"


def check_state(state: Optional[str], nonce: Optional[str]) -> Optional[str]:
    if not state or not nonce or ":" not in state:
        return None
    sent_nonce, next_url = state.split(":", 1)
    if not hmac.compare_digest(sent_nonce, nonce):
        return None
    return safe_next(next_url)


def login_url(client_id: int, state: str, prompt: str = "none") -> str:
    query = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": "identify",
        "prompt": prompt,
        "state": state,
    }
    return f"https://discord.com/oauth2/authorize?{urlencode(query)}"


def avatar_url(raw: Dict[str, Any]) -> str:
    user_id, avatar = raw.get("id"), raw.get("avatar")
    if avatar:
        return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.{'gif' if avatar.startswith('a_') else 'png'}?size=128"
    return f"https://cdn.discordapp.com/embed/avatars/{(int(user_id) >> 22) % 6}.png"


async def fetch_user(code: str, client_id: int) -> Optional[User]:
    if not DISCORD_CLIENT_SECRET:
        return None
    form = {
        "client_id": str(client_id),
        "client_secret": DISCORD_CLIENT_SECRET,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    }
    session = get_session()
    try:
        async with session.post(f"{API}/oauth2/token", data=form) as resp:
            if resp.status != 200:
                log.warning("Discord token exchange failed with status %s", resp.status)
                return None
            access_token = (await resp.json()).get("access_token")
        async with session.get(f"{API}/users/@me", headers={"Authorization": f"Bearer {access_token}"}) as resp:
            if resp.status != 200:
                log.warning("Discord /users/@me failed with status %s", resp.status)
                return None
            raw = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
        log.warning("Discord login failed: %s", error)
        return None
    return {
        "id": str(raw["id"]),
        "username": raw.get("username") or "",
        "name": raw.get("global_name") or raw.get("username") or "",
        "avatar_url": avatar_url(raw),
    }
