from __future__ import annotations
import hashlib
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.getenv(key, "").strip() or default


API_TOKEN = _env("API_TOKEN")
SUPPORT_URL = _env("BOT_SERVER", "https://discord.gg/prUsgFHvRS")
WEBHOOK_URL = _env("BOT_WEBHOOK_URL")
ALLOWED_GUILD_IDS: List[int] = [
    int(part) for part in os.getenv("ALLOWED_GUILDS", "").replace(" ", "").split(",") if part.isdigit()
]

SITE_URL = _env("BASE_URL", "https://wordcounter.dev").rstrip("/")
WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(next((raw for raw in (_env("WEB_PORT"), _env("SERVER_PORT")) if raw.isdigit()), "8080"))
DISCORD_CLIENT_SECRET = _env("DISCORD_CLIENT_SECRET")
SESSION_SECRET = _env("SESSION_SECRET") or hashlib.sha256(f"wordcounter-session:{API_TOKEN}".encode()).hexdigest()

DEFAULT_CLIENT_ID = 1551875701748277299
INVITE_PERMISSIONS = 1126177200925776
PRIVACY_URL = f"{SITE_URL}/privacy"
TERMS_URL = f"{SITE_URL}/terms"
SOURCE_URL = "https://github.com/Megildur/WordCounter"
BOTZILLA_URL = "https://bot-zilla.app"
LOG_RETENTION_DAYS = 90


def daily_log_handler(filename: str) -> logging.Handler:
    return TimedRotatingFileHandler(filename, when="midnight", backupCount=LOG_RETENTION_DAYS - 1, encoding="utf-8")


def invite_url(client_id: Optional[int] = None) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id or DEFAULT_CLIENT_ID}"
        f"&permissions={INVITE_PERMISSIONS}&scope=bot+applications.commands&integration_type=0"
    )
