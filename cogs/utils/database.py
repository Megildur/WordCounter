from __future__ import annotations
import asyncio
from collections import defaultdict
import os
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
import aiosqlite


class WordCounterDatabase:
    def __init__(self, bot) -> None:
        self.db_path = "Data/WordCounter.db"
        self.db: Optional[aiosqlite.Connection] = None
        self.db_lock = asyncio.Lock()
        self.bot = bot

    async def connect(self) -> None:
        dir_name = os.path.dirname(self.db_path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        self.db = await aiosqlite.connect(self.db_path)
        await self.db.execute("PRAGMA journal_mode=WAL;")
        await self.db.execute("PRAGMA synchronous=NORMAL;")
        await self.db.commit()
        await self.initialize_database()

    async def ensure_connected(self) -> None:
        if self.db is None:
            await self.connect()

    async def close(self) -> None:
        async with self.db_lock:
            if self.db is not None:
                await self.db.close()
                self.db = None

    async def initialize_database(self) -> None:
        async with self.db_lock:
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS counters (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, channel_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS server (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS ignore (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS keyword (
                    guild_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    PRIMARY KEY (guild_id, keyword)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS keyword_channel (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, keyword, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS keyword_user (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, keyword)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS attachments_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS attachments_users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS message_channels (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    messages INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id, channel_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS message_user (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    messages INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS analyzed_users (
                    user_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    PRIMARY KEY (user_id, guild_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS emojis_channels (
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, channel_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS emojis_users (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    count INTEGER NOT NULL,
                    PRIMARY KEY (guild_id, user_id)
                )"""
            )
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS monthly_user_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    words INTEGER NOT NULL DEFAULT 0,
                    messages INTEGER NOT NULL DEFAULT 0,
                    attachments INTEGER NOT NULL DEFAULT 0,
                    emojis INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, channel_id, year, month)
                )"""
            )
            try:
                await self.db.execute("ALTER TABLE monthly_user_stats ADD COLUMN emojis INTEGER NOT NULL DEFAULT 0")
            except Exception:
                pass
            await self.db.execute(
                """CREATE TABLE IF NOT EXISTS monthly_user_keywords (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    keyword TEXT NOT NULL,
                    year INTEGER NOT NULL,
                    month INTEGER NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id, channel_id, keyword, year, month)
                )"""
            )
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_monthly_user ON monthly_user_stats (guild_id, user_id, year DESC, month DESC)"
            )
            await self.db.execute(
                "CREATE INDEX IF NOT EXISTS idx_monthly_channel ON monthly_user_stats (guild_id, channel_id, year DESC, month DESC)"
            )
            await self.db.commit()

            legacy_files = [
                ("channels.db", "channels"),
                ("ignore.db", "ignore"),
                ("server.db", "server"),
                ("counter.db", "counters"),
                ("keyword.db", "keyword"),
                ("keyword_channel.db", "keyword_channel"),
                ("keyword_user.db", "keyword_user"),
                ("attachments_channels.db", "attachments_channels"),
                ("attachments_users.db", "attachments_users"),
                ("message_channels.db", "message_channels"),
                ("message_user.db", "message_user"),
                ("analyzed_users.db", "analyzed_users"),
            ]
            for file_name, table_name in legacy_files:
                if os.path.isfile(file_name):
                    try:
                        await self.db.execute(f"ATTACH DATABASE '{file_name}' AS legacy_db")
                        await self.db.execute(f"INSERT OR IGNORE INTO main.{table_name} SELECT * FROM legacy_db.{table_name}")
                        await self.db.commit()
                        await self.db.execute("DETACH DATABASE legacy_db")
                    except Exception:
                        try:
                            await self.db.execute("DETACH DATABASE legacy_db")
                        except Exception:
                            pass

    async def get_guild_tracking_config(self, guild_id: int) -> Tuple[Set[int], Set[int]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT channel_id FROM channels WHERE guild_id = ?", (guild_id,))
            watched_rows = await cursor.fetchall()
            watched_ids = {row[0] for row in watched_rows}

            cursor = await self.db.execute("SELECT channel_id FROM ignore WHERE guild_id = ?", (guild_id,))
            ignored_rows = await cursor.fetchall()
            ignored_ids = {row[0] for row in ignored_rows}

            return watched_ids, ignored_ids

    async def has_tracking_enabled(self, guild_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT 1 FROM channels WHERE guild_id = ? LIMIT 1", (guild_id,))
            result = await cursor.fetchone()
            return result is not None

    async def enable_whole_server(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("INSERT INTO channels (guild_id, channel_id) VALUES (?, ?)", (guild_id, 1))
            await self.db.commit()

    async def switch_to_specific_mode(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM channels WHERE guild_id = ? AND channel_id = 1", (guild_id,))
            await self.db.commit()

    async def disable_server_tracking(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM ignore WHERE guild_id = ?", (guild_id,))
            await self.db.commit()

    async def add_watched_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for cid in channel_ids:
                await self.db.execute(
                    "INSERT OR IGNORE INTO channels (guild_id, channel_id) VALUES (?, ?)",
                    (guild_id, cid),
                )
            await self.db.commit()

    async def remove_watched_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for cid in channel_ids:
                await self.db.execute(
                    "DELETE FROM channels WHERE guild_id = ? AND channel_id = ?",
                    (guild_id, cid),
                )
            await self.db.commit()

    async def clear_watched_channels(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM channels WHERE guild_id = ? AND channel_id != 1", (guild_id,))
            await self.db.commit()

    async def add_ignored_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for cid in channel_ids:
                await self.db.execute(
                    "INSERT OR IGNORE INTO ignore (guild_id, channel_id) VALUES (?, ?)",
                    (guild_id, cid),
                )
            await self.db.commit()

    async def remove_ignored_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for cid in channel_ids:
                await self.db.execute(
                    "DELETE FROM ignore WHERE guild_id = ? AND channel_id = ?",
                    (guild_id, cid),
                )
            await self.db.commit()

    async def clear_ignored_channels(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM ignore WHERE guild_id = ?", (guild_id,))
            await self.db.commit()

    async def update_word_count(self, guild_id: int, user_id: int, channel_id: int, count: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM counters WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            result = await cursor.fetchone()
            if result is None:
                await self.db.execute(
                    "INSERT INTO counters (guild_id, user_id, channel_id, count) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, channel_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE counters SET count = ? WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                    (result[0] + count, guild_id, user_id, channel_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM server WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            s_result = await cursor.fetchone()
            if s_result is None:
                await self.db.execute(
                    "INSERT INTO server (guild_id, user_id, count) VALUES (?, ?, ?)",
                    (guild_id, user_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE server SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (s_result[0] + count, guild_id, user_id),
                )
            await self.db.commit()

    async def remove_word_count(self, guild_id: int, user_id: int, channel_id: int, count: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM counters WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            result = await cursor.fetchone()
            if result is not None:
                await self.db.execute(
                    "UPDATE counters SET count = ? WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                    (max(0, result[0] - count), guild_id, user_id, channel_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM server WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            s_result = await cursor.fetchone()
            if s_result is not None:
                await self.db.execute(
                    "UPDATE server SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (max(0, s_result[0] - count), guild_id, user_id),
                )
            await self.db.commit()

    async def get_word_leaderboard(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM server WHERE guild_id = ? ORDER BY count DESC",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM counters WHERE guild_id = ? AND channel_id = ? ORDER BY count DESC",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def add_message_count(self, guild_id: int, user_id: int, channel_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT messages FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await self.db.execute(
                    "INSERT INTO message_channels (guild_id, user_id, channel_id, messages) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, channel_id, 1),
                )
            else:
                await self.db.execute(
                    "UPDATE message_channels SET messages = messages + 1 WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                    (guild_id, user_id, channel_id),
                )

            cursor = await self.db.execute(
                "SELECT messages FROM message_user WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_row = await cursor.fetchone()
            if u_row is None:
                await self.db.execute(
                    "INSERT INTO message_user (guild_id, user_id, messages) VALUES (?, ?, ?)",
                    (guild_id, user_id, 1),
                )
            else:
                await self.db.execute(
                    "UPDATE message_user SET messages = messages + 1 WHERE guild_id = ? AND user_id = ?",
                    (guild_id, user_id),
                )
            await self.db.commit()

    async def remove_message_count(self, guild_id: int, user_id: int, channel_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT messages FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            row = await cursor.fetchone()
            if row is not None:
                if row[0] <= 1:
                    await self.db.execute(
                        "DELETE FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                        (guild_id, user_id, channel_id),
                    )
                else:
                    await self.db.execute(
                        "UPDATE message_channels SET messages = messages - 1 WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                        (guild_id, user_id, channel_id),
                    )

            cursor = await self.db.execute(
                "SELECT messages FROM message_user WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_row = await cursor.fetchone()
            if u_row is not None:
                if u_row[0] <= 1:
                    await self.db.execute(
                        "DELETE FROM message_user WHERE guild_id = ? AND user_id = ?",
                        (guild_id, user_id),
                    )
                else:
                    await self.db.execute(
                        "UPDATE message_user SET messages = messages - 1 WHERE guild_id = ? AND user_id = ?",
                        (guild_id, user_id),
                    )
            await self.db.commit()

    async def get_message_leaderboard(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT user_id, messages FROM message_user WHERE guild_id = ? ORDER BY messages DESC",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT user_id, messages FROM message_channels WHERE guild_id = ? AND channel_id = ? ORDER BY messages DESC",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def add_attachment_count(self, guild_id: int, channel_id: int, user_id: int, count: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                (guild_id, channel_id, user_id),
            )
            result = await cursor.fetchone()
            if result is None:
                await self.db.execute(
                    "INSERT INTO attachments_channels (guild_id, channel_id, user_id, count) VALUES (?, ?, ?, ?)",
                    (guild_id, channel_id, user_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE attachments_channels SET count = ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                    (result[0] + count, guild_id, channel_id, user_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM attachments_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_result = await cursor.fetchone()
            if u_result is None:
                await self.db.execute(
                    "INSERT INTO attachments_users (guild_id, user_id, count) VALUES (?, ?, ?)",
                    (guild_id, user_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE attachments_users SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (u_result[0] + count, guild_id, user_id),
                )
            await self.db.commit()

    async def remove_attachment_count(self, guild_id: int, channel_id: int, user_id: int, count: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                (guild_id, channel_id, user_id),
            )
            result = await cursor.fetchone()
            if result is not None:
                await self.db.execute(
                    "UPDATE attachments_channels SET count = ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                    (max(0, result[0] - count), guild_id, channel_id, user_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM attachments_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_result = await cursor.fetchone()
            if u_result is not None:
                await self.db.execute(
                    "UPDATE attachments_users SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (max(0, u_result[0] - count), guild_id, user_id),
                )
            await self.db.commit()

    async def get_attachment_leaderboard(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM attachments_users WHERE guild_id = ? ORDER BY count DESC",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? ORDER BY count DESC",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def add_emoji_count(self, guild_id: int, user_id: int, channel_id: int, count: int) -> None:
        if count <= 0:
            return
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM emojis_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                (guild_id, channel_id, user_id),
            )
            result = await cursor.fetchone()
            if result is None:
                await self.db.execute(
                    "INSERT INTO emojis_channels (guild_id, channel_id, user_id, count) VALUES (?, ?, ?, ?)",
                    (guild_id, channel_id, user_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE emojis_channels SET count = ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                    (result[0] + count, guild_id, channel_id, user_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM emojis_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_result = await cursor.fetchone()
            if u_result is None:
                await self.db.execute(
                    "INSERT INTO emojis_users (guild_id, user_id, count) VALUES (?, ?, ?)",
                    (guild_id, user_id, count),
                )
            else:
                await self.db.execute(
                    "UPDATE emojis_users SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (u_result[0] + count, guild_id, user_id),
                )
            await self.db.commit()

    async def remove_emoji_count(self, guild_id: int, user_id: int, channel_id: int, count: int) -> None:
        if count <= 0:
            return
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM emojis_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                (guild_id, channel_id, user_id),
            )
            result = await cursor.fetchone()
            if result is not None:
                await self.db.execute(
                    "UPDATE emojis_channels SET count = ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?",
                    (max(0, result[0] - count), guild_id, channel_id, user_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM emojis_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            u_result = await cursor.fetchone()
            if u_result is not None:
                await self.db.execute(
                    "UPDATE emojis_users SET count = ? WHERE guild_id = ? AND user_id = ?",
                    (max(0, u_result[0] - count), guild_id, user_id),
                )
            await self.db.commit()

    async def get_emoji_leaderboard(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM emojis_users WHERE guild_id = ? ORDER BY count DESC",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT user_id, count FROM emojis_channels WHERE guild_id = ? AND channel_id = ? ORDER BY count DESC",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def get_keywords(self, guild_id: int) -> List[str]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT keyword FROM keyword WHERE guild_id = ? ORDER BY keyword ASC",
                (guild_id,),
            )
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

    async def add_keywords(self, guild_id: int, keywords: Sequence[str]) -> Tuple[List[str], List[str]]:
        await self.ensure_connected()
        added: List[str] = []
        skipped: List[str] = []
        async with self.db_lock:
            cursor = await self.db.execute("SELECT keyword FROM keyword WHERE guild_id = ?", (guild_id,))
            existing = {row[0].lower() for row in await cursor.fetchall()}

            for kw in keywords:
                kw_clean = kw.strip().lower()
                if not kw_clean:
                    continue
                if kw_clean in existing:
                    skipped.append(kw_clean)
                else:
                    await self.db.execute(
                        "INSERT INTO keyword (guild_id, keyword) VALUES (?, ?)",
                        (guild_id, kw_clean),
                    )
                    existing.add(kw_clean)
                    added.append(kw_clean)
            await self.db.commit()
        return added, skipped

    async def replace_keywords(self, guild_id: int, keywords: Sequence[str]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM keyword WHERE guild_id = ?", (guild_id,))
            for kw in keywords:
                await self.db.execute(
                    "INSERT OR IGNORE INTO keyword (guild_id, keyword) VALUES (?, ?)",
                    (guild_id, kw.strip().lower()),
                )
            await self.db.commit()

    async def remove_keywords(self, guild_id: int, keywords: Sequence[str]) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for kw in keywords:
                await self.db.execute(
                    "DELETE FROM keyword WHERE guild_id = ? AND keyword = ?",
                    (guild_id, kw),
                )
            await self.db.commit()

    async def clear_keywords(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM keyword WHERE guild_id = ?", (guild_id,))
            await self.db.commit()

    async def update_keyword_count(self, word: str, count: int, guild_id: int, channel_id: int, user_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?",
                (guild_id, channel_id, word, user_id),
            )
            result = await cursor.fetchone()
            if result:
                await self.db.execute(
                    "UPDATE keyword_channel SET count = ? WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?",
                    (count + result[0], guild_id, channel_id, word, user_id),
                )
            else:
                await self.db.execute(
                    "INSERT INTO keyword_channel (guild_id, channel_id, keyword, user_id, count) VALUES (?, ?, ?, ?, ?)",
                    (guild_id, channel_id, word, user_id, count),
                )

            cursor = await self.db.execute(
                "SELECT count FROM keyword_user WHERE guild_id = ? AND user_id = ? AND keyword = ?",
                (guild_id, user_id, word),
            )
            u_result = await cursor.fetchone()
            if u_result:
                await self.db.execute(
                    "UPDATE keyword_user SET count = ? WHERE guild_id = ? AND user_id = ? AND keyword = ?",
                    (count + u_result[0], guild_id, user_id, word),
                )
            else:
                await self.db.execute(
                    "INSERT INTO keyword_user (guild_id, user_id, keyword, count) VALUES (?, ?, ?, ?)",
                    (guild_id, user_id, word, count),
                )
            await self.db.commit()

    async def remove_keyword_count(self, keyword: str, word_count: int, guild_id: int, channel_id: int, user_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?",
                (guild_id, channel_id, keyword, user_id),
            )
            result = await cursor.fetchone()
            if result and result[0] > 0:
                await self.db.execute(
                    "UPDATE keyword_channel SET count = ? WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?",
                    (max(0, result[0] - word_count), guild_id, channel_id, keyword, user_id),
                )

            cursor = await self.db.execute(
                "SELECT count FROM keyword_user WHERE guild_id = ? AND user_id = ? AND keyword = ?",
                (guild_id, user_id, keyword),
            )
            u_result = await cursor.fetchone()
            if u_result and u_result[0] > 0:
                await self.db.execute(
                    "UPDATE keyword_user SET count = ? WHERE guild_id = ? AND user_id = ? AND keyword = ?",
                    (max(0, u_result[0] - word_count), guild_id, user_id, keyword),
                )
            await self.db.commit()

    async def get_keyword_leaderboard(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[str, int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT keyword, count, user_id FROM keyword_user WHERE guild_id = ? AND count > 0",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT keyword, count, user_id FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND count > 0",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def get_user_full_stats(
        self, guild_id: int, user_id: int
    ) -> Tuple[int, int, int, int, List[Tuple[str, int]]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM server WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            wresult = await cursor.fetchone()
            if wresult is None:
                await self.db.execute(
                    "INSERT INTO server (user_id, guild_id, count) VALUES (?, ?, ?)",
                    (user_id, guild_id, 0),
                )
                words = 0
            else:
                words = wresult[0]

            cursor = await self.db.execute(
                "SELECT count FROM attachments_users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            aresult = await cursor.fetchone()
            if aresult is None:
                await self.db.execute(
                    "INSERT INTO attachments_users (user_id, guild_id, count) VALUES (?, ?, ?)",
                    (user_id, guild_id, 0),
                )
                attachments = 0
            else:
                attachments = aresult[0]

            cursor = await self.db.execute(
                "SELECT messages FROM message_user WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            mresult = await cursor.fetchone()
            if mresult is None:
                await self.db.execute(
                    "INSERT INTO message_user (user_id, guild_id, messages) VALUES (?, ?, ?)",
                    (user_id, guild_id, 0),
                )
                messages = 0
            else:
                messages = mresult[0]

            cursor = await self.db.execute(
                "SELECT count FROM emojis_users WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
            eresult = await cursor.fetchone()
            if eresult is None:
                await self.db.execute(
                    "INSERT INTO emojis_users (guild_id, user_id, count) VALUES (?, ?, ?)",
                    (guild_id, user_id, 0),
                )
                emojis = 0
            else:
                emojis = eresult[0]

            cursor = await self.db.execute(
                "SELECT keyword, count FROM keyword_user WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            kresult = await cursor.fetchall()
            await self.db.commit()
            return words, messages, attachments, emojis, kresult

    async def reset_user_server_counts(self, guild_id: int, user_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM server WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.execute("DELETE FROM counters WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.execute("DELETE FROM emojis_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.execute("DELETE FROM emojis_channels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.execute("DELETE FROM monthly_user_stats WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.execute("DELETE FROM monthly_user_keywords WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.commit()

    async def reset_user_channel_counts(self, guild_id: int, user_id: int, channel_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT count FROM counters WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            row = await cursor.fetchone()
            removed = row[0] if row else 0
            await self.db.execute(
                "DELETE FROM counters WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )

            cursor = await self.db.execute(
                "SELECT count FROM emojis_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            e_row = await cursor.fetchone()
            e_removed = e_row[0] if e_row else 0
            await self.db.execute(
                "DELETE FROM emojis_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )

            await self.db.execute(
                "DELETE FROM monthly_user_stats WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            await self.db.execute(
                "DELETE FROM monthly_user_keywords WHERE guild_id = ? AND user_id = ? AND channel_id = ?",
                (guild_id, user_id, channel_id),
            )
            if removed > 0:
                await self.db.execute(
                    "UPDATE server SET count = MAX(0, count - ?) WHERE guild_id = ? AND user_id = ?",
                    (removed, guild_id, user_id),
                )
            if e_removed > 0:
                await self.db.execute(
                    "UPDATE emojis_users SET count = MAX(0, count - ?) WHERE guild_id = ? AND user_id = ?",
                    (e_removed, guild_id, user_id),
                )
            await self.db.commit()

    async def reset_channel_counts(self, guild_id: int, channel_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id, count FROM counters WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            rows = await cursor.fetchall()
            await self.db.execute(
                "DELETE FROM counters WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )

            cursor = await self.db.execute(
                "SELECT user_id, count FROM emojis_channels WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            e_rows = await cursor.fetchall()
            await self.db.execute(
                "DELETE FROM emojis_channels WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )

            await self.db.execute(
                "DELETE FROM monthly_user_stats WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            await self.db.execute(
                "DELETE FROM monthly_user_keywords WHERE guild_id = ? AND channel_id = ?",
                (guild_id, channel_id),
            )
            for r_uid, r_count in rows:
                await self.db.execute(
                    "UPDATE server SET count = MAX(0, count - ?) WHERE guild_id = ? AND user_id = ?",
                    (r_count, guild_id, r_uid),
                )
            for r_uid, r_count in e_rows:
                await self.db.execute(
                    "UPDATE emojis_users SET count = MAX(0, count - ?) WHERE guild_id = ? AND user_id = ?",
                    (r_count, guild_id, r_uid),
                )
            await self.db.commit()

    async def reset_entire_server(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute("DELETE FROM server WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM counters WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM message_user WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM message_channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM attachments_users WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM attachments_channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM emojis_users WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM emojis_channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM keyword_user WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM keyword_channel WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM monthly_user_stats WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM monthly_user_keywords WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM analyzed_users WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM channels WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM ignore WHERE guild_id = ?", (guild_id,))
            await self.db.execute("DELETE FROM keyword WHERE guild_id = ?", (guild_id,))
            await self.db.commit()

    async def reset_entire_server_counts(self, guild_id: int) -> None:
        await self.reset_entire_server(guild_id)

    async def is_user_analyzed(self, guild_id: int, user_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT 1 FROM analyzed_users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            result = await cursor.fetchone()
            return result is not None

    async def mark_user_analyzed(self, guild_id: int, user_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                "INSERT OR IGNORE INTO analyzed_users (user_id, guild_id) VALUES (?, ?)",
                (user_id, guild_id),
            )
            await self.db.commit()

    async def unlock_user_analyzed(self, guild_id: int, user_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                "DELETE FROM analyzed_users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            await self.db.commit()

    async def unlock_all_analyzed(self, guild_id: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                "DELETE FROM analyzed_users WHERE guild_id = ?",
                (guild_id,),
            )
            await self.db.commit()

    async def get_analyzed_users(self, guild_id: int) -> Set[int]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT user_id FROM analyzed_users WHERE guild_id = ?",
                (guild_id,),
            )
            rows = await cursor.fetchall()
            return {r[0] for r in rows}

    async def save_retroactive_analysis(
        self,
        guild_id: int,
        user_id: int,
        total_words: int,
        counted_messages: int,
        total_attachments: int,
        keyword_counts: Dict[str, int],
        channel_words: Dict[int, int],
        channel_messages: Dict[int, int],
        channel_attachments: Dict[int, int],
        channel_keywords: Dict[Tuple[int, str], int],
        monthly_stats: Optional[Dict[Tuple[int, int, int], Dict[str, int]]] = None,
        monthly_keywords: Optional[Dict[Tuple[int, str, int, int], int]] = None,
        channel_emojis: Optional[Dict[int, int]] = None,
        total_emojis: int = 0,
    ) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """
                INSERT INTO server (guild_id, user_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    count = count + excluded.count
                """,
                (guild_id, user_id, total_words),
            )
            for cid, w_cnt in channel_words.items():
                if w_cnt > 0:
                    await self.db.execute(
                        """
                        INSERT INTO counters (guild_id, user_id, channel_id, count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, channel_id) DO UPDATE SET
                            count = count + excluded.count
                        """,
                        (guild_id, user_id, cid, w_cnt),
                    )

            await self.db.execute(
                """
                INSERT INTO message_user (guild_id, user_id, messages)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    messages = messages + excluded.messages
                """,
                (guild_id, user_id, counted_messages),
            )
            for cid, m_cnt in channel_messages.items():
                if m_cnt > 0:
                    await self.db.execute(
                        """
                        INSERT INTO message_channels (guild_id, user_id, channel_id, messages)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, channel_id) DO UPDATE SET
                            messages = messages + excluded.messages
                        """,
                        (guild_id, user_id, cid, m_cnt),
                    )

            await self.db.execute(
                """
                INSERT INTO attachments_users (guild_id, user_id, count)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET
                    count = count + excluded.count
                """,
                (guild_id, user_id, total_attachments),
            )
            for cid, a_cnt in channel_attachments.items():
                if a_cnt > 0:
                    await self.db.execute(
                        """
                        INSERT INTO attachments_channels (guild_id, channel_id, user_id, count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, channel_id, user_id) DO UPDATE SET
                            count = count + excluded.count
                        """,
                        (guild_id, cid, user_id, a_cnt),
                    )

            if total_emojis > 0:
                await self.db.execute(
                    """
                    INSERT INTO emojis_users (guild_id, user_id, count)
                    VALUES (?, ?, ?)
                    ON CONFLICT(guild_id, user_id) DO UPDATE SET
                        count = count + excluded.count
                    """,
                    (guild_id, user_id, total_emojis),
                )
            if channel_emojis:
                for cid, e_cnt in channel_emojis.items():
                    if e_cnt > 0:
                        await self.db.execute(
                            """
                            INSERT INTO emojis_channels (guild_id, channel_id, user_id, count)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(guild_id, channel_id, user_id) DO UPDATE SET
                                count = count + excluded.count
                            """,
                            (guild_id, cid, user_id, e_cnt),
                        )

            for kw, k_cnt in keyword_counts.items():
                if k_cnt > 0:
                    await self.db.execute(
                        """
                        INSERT INTO keyword_user (guild_id, user_id, keyword, count)
                        VALUES (?, ?, ?, ?)
                        ON CONFLICT(guild_id, user_id, keyword) DO UPDATE SET
                            count = count + excluded.count
                        """,
                        (guild_id, user_id, kw, k_cnt),
                    )
            for (cid, kw), k_cnt in channel_keywords.items():
                if k_cnt > 0:
                    await self.db.execute(
                        """
                        INSERT INTO keyword_channel (guild_id, channel_id, keyword, user_id, count)
                        VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT(guild_id, channel_id, keyword, user_id) DO UPDATE SET
                            count = count + excluded.count
                        """,
                        (guild_id, cid, kw, user_id, k_cnt),
                    )

            if monthly_stats:
                for (cid, y, m), stats in monthly_stats.items():
                    w = stats.get("words", 0)
                    msg_c = stats.get("messages", 0)
                    att_c = stats.get("attachments", 0)
                    emo_c = stats.get("emojis", 0)
                    if w > 0 or msg_c > 0 or att_c > 0 or emo_c > 0:
                        await self.db.execute(
                            """
                            INSERT INTO monthly_user_stats (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(guild_id, user_id, channel_id, year, month) DO UPDATE SET
                                words = words + excluded.words,
                                messages = messages + excluded.messages,
                                attachments = attachments + excluded.attachments,
                                emojis = emojis + excluded.emojis
                            """,
                            (guild_id, user_id, cid, y, m, w, msg_c, att_c, emo_c),
                        )

            if monthly_keywords:
                for (cid, kw, y, m), k_cnt in monthly_keywords.items():
                    if k_cnt > 0:
                        await self.db.execute(
                            """
                            INSERT INTO monthly_user_keywords (guild_id, user_id, channel_id, keyword, year, month, count)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(guild_id, user_id, channel_id, keyword, year, month) DO UPDATE SET
                                count = count + excluded.count
                            """,
                            (guild_id, user_id, cid, kw, y, m, k_cnt),
                        )

            await self.db.execute(
                "INSERT OR IGNORE INTO analyzed_users (user_id, guild_id) VALUES (?, ?)",
                (user_id, guild_id),
            )
            await self.db.commit()

    async def record_monthly_activity(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        year: int,
        month: int,
        words: int = 0,
        messages: int = 0,
        attachments: int = 0,
        emojis: int = 0,
    ) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """
                INSERT INTO monthly_user_stats (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, channel_id, year, month) DO UPDATE SET
                    words = words + excluded.words,
                    messages = messages + excluded.messages,
                    attachments = attachments + excluded.attachments,
                    emojis = emojis + excluded.emojis
                """,
                (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis),
            )
            await self.db.commit()

    async def remove_monthly_activity(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        year: int,
        month: int,
        words: int = 0,
        messages: int = 0,
        attachments: int = 0,
        emojis: int = 0,
    ) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """
                UPDATE monthly_user_stats
                SET words = MAX(0, words - ?),
                    messages = MAX(0, messages - ?),
                    attachments = MAX(0, attachments - ?),
                    emojis = MAX(0, emojis - ?)
                WHERE guild_id = ? AND user_id = ? AND channel_id = ? AND year = ? AND month = ?
                """,
                (words, messages, attachments, emojis, guild_id, user_id, channel_id, year, month),
            )
            await self.db.commit()

    async def record_monthly_keyword(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        keyword: str,
        year: int,
        month: int,
        count: int,
    ) -> None:
        if count <= 0:
            return
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """
                INSERT INTO monthly_user_keywords (guild_id, user_id, channel_id, keyword, year, month, count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id, channel_id, keyword, year, month) DO UPDATE SET
                    count = count + excluded.count
                """,
                (guild_id, user_id, channel_id, keyword, year, month, count),
            )
            await self.db.commit()

    async def remove_monthly_keyword(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        keyword: str,
        year: int,
        month: int,
        count: int,
    ) -> None:
        if count <= 0:
            return
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """
                UPDATE monthly_user_keywords
                SET count = MAX(0, count - ?)
                WHERE guild_id = ? AND user_id = ? AND channel_id = ? AND keyword = ? AND year = ? AND month = ?
                """,
                (count, guild_id, user_id, channel_id, keyword, year, month),
            )
            await self.db.commit()

    async def get_user_monthly_breakdown(
        self, guild_id: int, user_id: int
    ) -> List[Dict[str, Any]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                """
                SELECT year, month, channel_id, words, messages, attachments, emojis
                FROM monthly_user_stats
                WHERE guild_id = ? AND user_id = ?
                ORDER BY year DESC, month DESC, words DESC
                """,
                (guild_id, user_id),
            )
            stat_rows = await cursor.fetchall()

            cursor = await self.db.execute(
                """
                SELECT year, month, channel_id, keyword, count
                FROM monthly_user_keywords
                WHERE guild_id = ? AND user_id = ?
                ORDER BY year DESC, month DESC
                """,
                (guild_id, user_id),
            )
            kw_rows = await cursor.fetchall()

            months_dict: Dict[Tuple[int, int], Dict[str, Any]] = {}
            for y, m, cid, w, msg_c, att_c, emo_c in stat_rows:
                key = (y, m)
                if key not in months_dict:
                    months_dict[key] = {
                        "year": y,
                        "month": m,
                        "words": 0,
                        "messages": 0,
                        "attachments": 0,
                        "emojis": 0,
                        "keywords": defaultdict(int),
                        "channels": {},
                    }
                months_dict[key]["words"] += w
                months_dict[key]["messages"] += msg_c
                months_dict[key]["attachments"] += att_c
                months_dict[key]["emojis"] += emo_c
                months_dict[key]["channels"][cid] = {
                    "words": w,
                    "messages": msg_c,
                    "attachments": att_c,
                    "emojis": emo_c,
                    "keywords": {},
                }

            for y, m, cid, kw, k_cnt in kw_rows:
                key = (y, m)
                if key in months_dict:
                    months_dict[key]["keywords"][kw] += k_cnt
                    if cid in months_dict[key]["channels"]:
                        months_dict[key]["channels"][cid]["keywords"][kw] = k_cnt

            sorted_months = sorted(
                months_dict.values(),
                key=lambda item: (item["year"], item["month"]),
                reverse=True,
            )
            for m_item in sorted_months:
                m_item["keywords"] = dict(m_item["keywords"])
            return sorted_months

    async def get_channel_monthly_breakdown(
        self, guild_id: int, channel_id: int
    ) -> List[Tuple[int, int, int, int, int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                """
                SELECT year, month, SUM(words), SUM(messages), SUM(attachments), SUM(emojis)
                FROM monthly_user_stats
                WHERE guild_id = ? AND channel_id = ?
                GROUP BY year, month
                ORDER BY year DESC, month DESC
                """,
                (guild_id, channel_id),
            )
            rows = await cursor.fetchall()
            return [(r[0], r[1], r[2] or 0, r[3] or 0, r[4] or 0, r[5] or 0) for r in rows]