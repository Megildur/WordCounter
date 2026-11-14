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
# Turns on limiting the website's requests by visitor address, and says where the address is: the name of the request header the website's proxy fills in with it, such as CF-Connecting-IP, or `peer` when nothing stands in front of the website. Unset, nothing is limited by address, because a proxy connects from a public address too and every visitor would count as one. With a header name, set it only if the website can't be reached except through that proxy, because anyone who can reach it directly can write any value in the header. The word `cloudflare` is for a website behind Cloudflare: it reads CF-Connecting-IP and only believes it on a connection from Cloudflare's own address ranges, so reaching the website directly gives no way to be counted as someone else.
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
# How long, in seconds, a message's counts wait in memory so they can be written together with others'. 0 writes every message at once, as before.
try:
    ACTIVITY_FLUSH_SECONDS = max(0.0, float(_env("ACTIVITY_FLUSH_SECONDS", "2")))
except ValueError:
    ACTIVITY_FLUSH_SECONDS = 2.0
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


def guild_invite_url(guild_id: int, client_id: Optional[int] = None) -> str:
    """The invite link with one server already picked and locked. Discord only honours guild_id when the link asks for the bot scope, which is why it is built on `invite_url` and not on a custom link."""
    return f"{invite_url(client_id)}&guild_id={int(guild_id)}&disable_guild_select=true"
