from __future__ import annotations
import os
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

API_TOKEN = os.getenv("API_TOKEN", "").strip()
SUPPORT_URL = os.getenv("BOT_SERVER", "").strip() or "https://discord.gg/prUsgFHvRS"
WEBHOOK_URL = os.getenv("BOT_WEBHOOK_URL", "").strip()
ALLOWED_GUILD_IDS: List[int] = [
    int(part) for part in os.getenv("ALLOWED_GUILDS", "").replace(" ", "").split(",") if part.isdigit()
]

DEFAULT_CLIENT_ID = 1551875701748277299
INVITE_PERMISSIONS = 1126177200925776
PRIVACY_URL = "https://github.com/Megildur/WordCounter/blob/main/PRIVACY.md"
TERMS_URL = "https://github.com/Megildur/WordCounter/blob/main/TERMS.md"


def invite_url(client_id: Optional[int] = None) -> str:
    return (
        f"https://discord.com/oauth2/authorize?client_id={client_id or DEFAULT_CLIENT_ID}"
        f"&permissions={INVITE_PERMISSIONS}&scope=bot+applications.commands&integration_type=0"
    )
