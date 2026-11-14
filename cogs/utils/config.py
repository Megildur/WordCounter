"""Settings read from the environment (a .env file is loaded on import) and the constants shared by the bot and the website."""

from __future__ import annotations
import hashlib
import logging
import os
from logging.handlers import TimedRotatingFileHandler
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()


def _env(key: str, default: str = "") -> str:
    """Reads a variable trimmed, and falls back to the default when it is missing or blank."""
    return os.getenv(key, "").strip() or default


API_TOKEN = _env("API_TOKEN")
SUPPORT_URL = _env("BOT_SERVER", "https://discord.gg/prUsgFHvRS")
WEBHOOK_URL = _env("BOT_WEBHOOK_URL")
# The servers where the owner commands exist, from a comma-separated list. Parts that aren't numbers are ignored.
ALLOWED_GUILD_IDS: List[int] = [
    int(part) for part in os.getenv("ALLOWED_GUILDS", "").replace(" ", "").split(",") if part.isdigit()
]

SITE_URL = _env("BASE_URL", "https://wordcounter.dev").rstrip("/")
# The request header the website's proxy fills in with the visitor's address, such as CF-Connecting-IP. Without it the website limits requests by address only when the address that connects is a public one, which isn't the case behind a proxy. Set it only if the website can't be reached except through that proxy, because anyone who can reach it directly can write any value in the header.
CLIENT_IP_HEADER = _env("CLIENT_IP_HEADER")
WEB_HOST = _env("WEB_HOST", "0.0.0.0")
WEB_PORT = int(next((raw for raw in (_env("WEB_PORT"), _env("SERVER_PORT")) if raw.isdigit()), "8080"))
MYSQL_HOST = _env("MYSQL_HOST")
MYSQL_PORT = int(_env("MYSQL_PORT")) if _env("MYSQL_PORT").isdigit() else 3306
MYSQL_USER = _env("MYSQL_USER")
# The password is read as it is, without trimming.
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DB = _env("MYSQL_DB", "wordcounter")
# The database file from before MySQL, which sqlite_import copies over the first time the bot starts with it.
SQLITE_PATH = "Data/WordCounter.db"
DISCORD_CLIENT_SECRET = _env("DISCORD_CLIENT_SECRET")
# Signs the website's login cookies. Without SESSION_SECRET it is derived from the bot token, so it stays the same across restarts.
SESSION_SECRET = _env("SESSION_SECRET") or hashlib.sha256(f"wordcounter-session:{API_TOKEN}".encode()).hexdigest()

DEFAULT_CLIENT_ID = 1551875701748277299
INVITE_PERMISSIONS = 1126177200925776
PRIVACY_URL = f"{SITE_URL}/privacy"
TERMS_URL = f"{SITE_URL}/terms"
SOURCE_URL = "https://github.com/Megildur/WordCounter"
BOTZILLA_URL = "https://bot-zilla.app"
DEVELOPER_ID = 1535886540847325305
DEVELOPER_INVITE_URL = _env("DEVELOPER_INVITE_URL", "https://discord.gg/2X4fUapJ")
LOG_RETENTION_DAYS = 90


def daily_log_handler(filename: str) -> logging.Handler:
    """A log file that rotates at midnight and keeps LOG_RETENTION_DAYS files in total (today's plus the rotated ones)."""
    return TimedRotatingFileHandler(filename, when="midnight", backupCount=LOG_RETENTION_DAYS - 1, encoding="utf-8")


def invite_url(client_id: Optional[int] = None) -> str:
    """The bot's invite link with the permissions it needs. Uses DEFAULT_CLIENT_ID when no ID is passed."""
    return f"https://discord.com/oauth2/authorize?client_id={client_id or DEFAULT_CLIENT_ID}&permissions={INVITE_PERMISSIONS}&scope=bot+applications.commands&integration_type=0"
