from __future__ import annotations
import asyncio
import os
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import aiosqlite

MonthlyKey = Tuple[int, int, int]
MonthlyKeywordKey = Tuple[int, str, int, int]
UserActivity = Tuple[Mapping[MonthlyKey, Sequence[int]], Mapping[MonthlyKeywordKey, int]]
Statements = Sequence[Tuple[str, Sequence[Sequence[Any]]]]

DISCORD_EPOCH_MS = 1420070400000

COUNT_TABLES: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "counters": (("guild_id", "user_id", "channel_id"), "count"),
    "server": (("guild_id", "user_id"), "count"),
    "message_channels": (("guild_id", "user_id", "channel_id"), "messages"),
    "message_user": (("guild_id", "user_id"), "messages"),
    "attachments_channels": (("guild_id", "channel_id", "user_id"), "count"),
    "attachments_users": (("guild_id", "user_id"), "count"),
    "emojis_channels": (("guild_id", "channel_id", "user_id"), "count"),
    "emojis_users": (("guild_id", "user_id"), "count"),
    "keyword_channel": (("guild_id", "channel_id", "keyword", "user_id"), "count"),
    "keyword_user": (("guild_id", "user_id", "keyword"), "count"),
    "monthly_user_keywords": (("guild_id", "user_id", "channel_id", "keyword", "year", "month"), "count"),
}

METRIC_TABLES: Dict[str, Tuple[str, str]] = {
    "words": ("counters", "server"),
    "messages": ("message_channels", "message_user"),
    "attachments": ("attachments_channels", "attachments_users"),
    "emojis": ("emojis_channels", "emojis_users"),
}

MONTHLY_KEYS = "guild_id = ? AND user_id = ? AND channel_id = ? AND year = ? AND month = ?"

MONTHLY_UPSERT = """
    INSERT INTO monthly_user_stats (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(guild_id, user_id, channel_id, year, month) DO UPDATE SET
        words = MAX(0, words + ?),
        messages = MAX(0, messages + ?),
        attachments = MAX(0, attachments + ?),
        emojis = MAX(0, emojis + ?)
"""

MONTHLY_DECREMENT = f"""
    UPDATE monthly_user_stats SET
        words = MAX(0, words + ?),
        messages = MAX(0, messages + ?),
        attachments = MAX(0, attachments + ?),
        emojis = MAX(0, emojis + ?)
    WHERE {MONTHLY_KEYS}
"""

MONTHLY_PRUNE = f"""
    DELETE FROM monthly_user_stats
    WHERE {MONTHLY_KEYS} AND words <= 0 AND messages <= 0 AND attachments <= 0 AND emojis <= 0
"""

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS counters (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS server (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS ignore (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS keyword (
        guild_id INTEGER NOT NULL, keyword TEXT NOT NULL,
        PRIMARY KEY (guild_id, keyword))""",
    """CREATE TABLE IF NOT EXISTS keyword_channel (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, keyword TEXT NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id, keyword, user_id))""",
    """CREATE TABLE IF NOT EXISTS keyword_user (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, keyword TEXT NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id, keyword))""",
    """CREATE TABLE IF NOT EXISTS attachments_channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS attachments_users (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS message_channels (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, messages INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS message_user (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, messages INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS analyzed_users (
        user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL,
        PRIMARY KEY (user_id, guild_id))""",
    """CREATE TABLE IF NOT EXISTS emojis_channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS emojis_users (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS monthly_user_stats (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL,
        year INTEGER NOT NULL, month INTEGER NOT NULL,
        words INTEGER NOT NULL DEFAULT 0, messages INTEGER NOT NULL DEFAULT 0,
        attachments INTEGER NOT NULL DEFAULT 0, emojis INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, channel_id, year, month))""",
    """CREATE TABLE IF NOT EXISTS monthly_user_keywords (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, keyword TEXT NOT NULL,
        year INTEGER NOT NULL, month INTEGER NOT NULL, count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, channel_id, keyword, year, month))""",
    """CREATE TABLE IF NOT EXISTS tracking_periods (
        guild_id INTEGER NOT NULL, started INTEGER NOT NULL, ended INTEGER,
        PRIMARY KEY (guild_id, started))""",
    """CREATE TABLE IF NOT EXISTS count_resets (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, reset_at INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS analysis_runs (
        guild_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, cutoff INTEGER NOT NULL,
        status_channel_id INTEGER, status_message_id INTEGER, started_at REAL NOT NULL,
        messages_read INTEGER NOT NULL DEFAULT 0, totals TEXT NOT NULL DEFAULT '{}')""",
    """CREATE TABLE IF NOT EXISTS analysis_run_members (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS analysis_run_channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, before_id INTEGER, status TEXT NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    "CREATE INDEX IF NOT EXISTS idx_monthly_user ON monthly_user_stats (guild_id, user_id, year DESC, month DESC)",
    "CREATE INDEX IF NOT EXISTS idx_monthly_channel ON monthly_user_stats (guild_id, channel_id, year DESC, month DESC)",
)

LEGACY_FILES = (
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
)

COUNT_DATA_TABLES = (
    "server", "counters", "message_user", "message_channels", "attachments_users", "attachments_channels",
    "emojis_users", "emojis_channels", "keyword_user", "keyword_channel", "monthly_user_stats", "monthly_user_keywords",
)

ANALYSIS_RUN_TABLES = ("analysis_runs", "analysis_run_members", "analysis_run_channels")

GUILD_TABLES = (
    *COUNT_DATA_TABLES, "analyzed_users", "channels", "ignore", "keyword",
    "tracking_periods", "count_resets", *ANALYSIS_RUN_TABLES,
)


def now_snowflake() -> int:
    return (int(time.time() * 1000) - DISCORD_EPOCH_MS) << 22


@lru_cache(maxsize=None)
def _increment_sql(table: str) -> str:
    keys, column = COUNT_TABLES[table]
    names = ", ".join(keys)
    marks = ", ".join("?" * (len(keys) + 1))
    return (
        f"INSERT INTO {table} ({names}, {column}) VALUES ({marks}) "
        f"ON CONFLICT({names}) DO UPDATE SET {column} = {column} + excluded.{column}"
    )


@lru_cache(maxsize=None)
def _decrement_sql(table: str) -> Tuple[str, str]:
    keys, column = COUNT_TABLES[table]
    where = " AND ".join(f"{key} = ?" for key in keys)
    return (
        f"UPDATE {table} SET {column} = MAX(0, {column} - ?) WHERE {where}",
        f"DELETE FROM {table} WHERE {where} AND {column} <= 0",
    )


def _key_values(table: str, values: Mapping[str, Any]) -> Tuple[Any, ...]:
    return tuple(values[key] for key in COUNT_TABLES[table][0])


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
            for statement in SCHEMA:
                await self.db.execute(statement)
            try:
                await self.db.execute("ALTER TABLE monthly_user_stats ADD COLUMN emojis INTEGER NOT NULL DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
            await self.db.commit()

            for file_name, table_name in LEGACY_FILES:
                if not os.path.isfile(file_name):
                    continue
                try:
                    await self.db.execute(f"ATTACH DATABASE '{file_name}' AS legacy_db")
                    await self.db.execute(f"INSERT OR IGNORE INTO main.{table_name} SELECT * FROM legacy_db.{table_name}")
                    await self.db.commit()
                except aiosqlite.Error:
                    pass
                finally:
                    try:
                        await self.db.execute("DETACH DATABASE legacy_db")
                    except aiosqlite.Error:
                        pass

    async def _fetch_value(self, sql: str, params: Sequence[Any]) -> int:
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    async def _write(self, statements: Statements, sync_tracking_for: Optional[int] = None) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for sql, rows in statements:
                await self.db.executemany(sql, list(rows))
            if sync_tracking_for is not None:
                await self._sync_tracking_period(sync_tracking_for)
            await self.db.commit()

    async def _sync_tracking_period(self, guild_id: int) -> None:
        enabled = await self._fetch_value("SELECT COUNT(*) FROM channels WHERE guild_id = ?", (guild_id,))
        is_open = await self._fetch_value(
            "SELECT COUNT(*) FROM tracking_periods WHERE guild_id = ? AND ended IS NULL", (guild_id,)
        )
        if enabled and not is_open:
            await self.db.execute(
                "INSERT OR IGNORE INTO tracking_periods (guild_id, started, ended) VALUES (?, ?, NULL)",
                (guild_id, now_snowflake()),
            )
        elif is_open and not enabled:
            await self.db.execute(
                "UPDATE tracking_periods SET ended = ? WHERE guild_id = ? AND ended IS NULL",
                (now_snowflake(), guild_id),
            )

    async def ensure_tracking_period(self, guild_id: int, started: int) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            enabled = await self._fetch_value("SELECT COUNT(*) FROM channels WHERE guild_id = ?", (guild_id,))
            is_open = await self._fetch_value(
                "SELECT COUNT(*) FROM tracking_periods WHERE guild_id = ? AND ended IS NULL", (guild_id,)
            )
            if enabled and not is_open:
                await self.db.execute(
                    "INSERT OR IGNORE INTO tracking_periods (guild_id, started, ended) VALUES (?, ?, NULL)",
                    (guild_id, started),
                )
                await self.db.commit()

    async def get_count_history(self, guild_id: int) -> Tuple[List[Tuple[int, Optional[int]]], Dict[Tuple[int, int], int]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT started, ended FROM tracking_periods WHERE guild_id = ?", (guild_id,))
            periods = [(row[0], row[1]) for row in await cursor.fetchall()]
            cursor = await self.db.execute("SELECT user_id, channel_id, reset_at FROM count_resets WHERE guild_id = ?", (guild_id,))
            resets = {(row[0], row[1]): row[2] for row in await cursor.fetchall()}
        return periods, resets

    async def get_guild_tracking_config(self, guild_id: int) -> Tuple[Set[int], Set[int]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT channel_id FROM channels WHERE guild_id = ?", (guild_id,))
            watched_ids = {row[0] for row in await cursor.fetchall()}
            cursor = await self.db.execute("SELECT channel_id FROM ignore WHERE guild_id = ?", (guild_id,))
            ignored_ids = {row[0] for row in await cursor.fetchall()}
            return watched_ids, ignored_ids

    async def has_tracking_enabled(self, guild_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT 1 FROM channels WHERE guild_id = ? LIMIT 1", (guild_id,))
            return await cursor.fetchone() is not None

    async def enable_whole_server(self, guild_id: int) -> None:
        await self._write(
            [
                ("DELETE FROM channels WHERE guild_id = ?", [(guild_id,)]),
                ("INSERT INTO channels (guild_id, channel_id) VALUES (?, 1)", [(guild_id,)]),
            ],
            sync_tracking_for=guild_id,
        )

    async def switch_to_specific_mode(self, guild_id: int) -> None:
        await self._write(
            [("DELETE FROM channels WHERE guild_id = ? AND channel_id = 1", [(guild_id,)])],
            sync_tracking_for=guild_id,
        )

    async def disable_server_tracking(self, guild_id: int) -> None:
        await self._write(
            [
                ("DELETE FROM channels WHERE guild_id = ?", [(guild_id,)]),
                ("DELETE FROM ignore WHERE guild_id = ?", [(guild_id,)]),
            ],
            sync_tracking_for=guild_id,
        )

    async def add_watched_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write(
            [("INSERT OR IGNORE INTO channels (guild_id, channel_id) VALUES (?, ?)", [(guild_id, cid) for cid in channel_ids])],
            sync_tracking_for=guild_id,
        )

    async def remove_watched_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write(
            [("DELETE FROM channels WHERE guild_id = ? AND channel_id = ?", [(guild_id, cid) for cid in channel_ids])],
            sync_tracking_for=guild_id,
        )

    async def clear_watched_channels(self, guild_id: int) -> None:
        await self._write(
            [("DELETE FROM channels WHERE guild_id = ? AND channel_id != 1", [(guild_id,)])],
            sync_tracking_for=guild_id,
        )

    async def add_ignored_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write([("INSERT OR IGNORE INTO ignore (guild_id, channel_id) VALUES (?, ?)", [(guild_id, cid) for cid in channel_ids])])

    async def remove_ignored_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write([("DELETE FROM ignore WHERE guild_id = ? AND channel_id = ?", [(guild_id, cid) for cid in channel_ids])])

    async def clear_ignored_channels(self, guild_id: int) -> None:
        await self._write([("DELETE FROM ignore WHERE guild_id = ?", [(guild_id,)])])

    async def apply_activity(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        year: int,
        month: int,
        *,
        words: int = 0,
        messages: int = 0,
        attachments: int = 0,
        emojis: int = 0,
        keywords: Optional[Mapping[str, int]] = None,
    ) -> None:
        ids: Dict[str, Any] = {"guild_id": guild_id, "user_id": user_id, "channel_id": channel_id, "year": year, "month": month}
        changes: List[Tuple[str, Mapping[str, Any], int]] = []
        for metric, delta in (("words", words), ("messages", messages), ("attachments", attachments), ("emojis", emojis)):
            if delta:
                channel_table, user_table = METRIC_TABLES[metric]
                changes += [(channel_table, ids, delta), (user_table, ids, delta)]
        for keyword, delta in (keywords or {}).items():
            if delta:
                keyword_ids = {**ids, "keyword": keyword}
                changes += [
                    ("keyword_channel", keyword_ids, delta),
                    ("keyword_user", keyword_ids, delta),
                    ("monthly_user_keywords", keyword_ids, delta),
                ]
        monthly = (words, messages, attachments, emojis)
        if not changes and not any(monthly):
            return

        await self.ensure_connected()
        async with self.db_lock:
            for table, values, delta in changes:
                key_values = _key_values(table, values)
                if delta > 0:
                    await self.db.execute(_increment_sql(table), (*key_values, delta))
                else:
                    decrement, prune = _decrement_sql(table)
                    await self.db.execute(decrement, (-delta, *key_values))
                    await self.db.execute(prune, key_values)

            if any(monthly):
                month_key = (guild_id, user_id, channel_id, year, month)
                if any(delta > 0 for delta in monthly):
                    await self.db.execute(MONTHLY_UPSERT, (*month_key, *(max(0, d) for d in monthly), *monthly))
                else:
                    await self.db.execute(MONTHLY_DECREMENT, (*monthly, *month_key))
                    await self.db.execute(MONTHLY_PRUNE, month_key)
            await self.db.commit()

    async def _write_results(self, guild_id: int, results: Mapping[int, UserActivity]) -> None:
        rows: Dict[str, List[Tuple[Any, ...]]] = defaultdict(list)
        monthly_rows: List[Tuple[int, ...]] = []

        for user_id, (monthly, monthly_keywords) in results.items():
            channel_totals: Dict[int, List[int]] = defaultdict(lambda: [0, 0, 0, 0])
            for (channel_id, year, month), counts in monthly.items():
                monthly_rows.append((guild_id, user_id, channel_id, year, month, *counts, *counts))
                totals = channel_totals[channel_id]
                for index, value in enumerate(counts):
                    totals[index] += value

            user_totals = [sum(column) for column in zip(*channel_totals.values())] or [0, 0, 0, 0]
            for index, metric in enumerate(("words", "messages", "attachments", "emojis")):
                channel_table, user_table = METRIC_TABLES[metric]
                for channel_id, totals in channel_totals.items():
                    if totals[index]:
                        ids = {"guild_id": guild_id, "user_id": user_id, "channel_id": channel_id}
                        rows[channel_table].append((*_key_values(channel_table, ids), totals[index]))
                if user_totals[index]:
                    rows[user_table].append((guild_id, user_id, user_totals[index]))

            keyword_channels: Dict[Tuple[int, str], int] = defaultdict(int)
            keyword_users: Dict[str, int] = defaultdict(int)
            for (channel_id, keyword, year, month), count in monthly_keywords.items():
                if count <= 0:
                    continue
                ids = {"guild_id": guild_id, "user_id": user_id, "channel_id": channel_id, "keyword": keyword, "year": year, "month": month}
                rows["monthly_user_keywords"].append((*_key_values("monthly_user_keywords", ids), count))
                keyword_channels[(channel_id, keyword)] += count
                keyword_users[keyword] += count
            for (channel_id, keyword), count in keyword_channels.items():
                rows["keyword_channel"].append((guild_id, channel_id, keyword, user_id, count))
            for keyword, count in keyword_users.items():
                rows["keyword_user"].append((guild_id, user_id, keyword, count))

        for table, table_rows in rows.items():
            await self.db.executemany(_increment_sql(table), table_rows)
        await self.db.executemany(MONTHLY_UPSERT, monthly_rows)

    async def _mark_analyzed(self, guild_id: int, user_ids: Iterable[int]) -> None:
        await self.db.executemany(
            "INSERT OR IGNORE INTO analyzed_users (user_id, guild_id) VALUES (?, ?)",
            [(user_id, guild_id) for user_id in user_ids],
        )

    async def save_retroactive_results(
        self,
        guild_id: int,
        results: Mapping[int, UserActivity],
        analyzed_user_ids: Iterable[int],
    ) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self._write_results(guild_id, results)
            await self._mark_analyzed(guild_id, analyzed_user_ids)
            await self.db.commit()

    async def start_analysis_run(
        self,
        guild_id: int,
        kind: str,
        cutoff: int,
        status_channel_id: Optional[int],
        status_message_id: Optional[int],
        user_ids: Iterable[int],
    ) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            for table in ANALYSIS_RUN_TABLES:
                await self.db.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            await self.db.execute(
                """INSERT INTO analysis_runs (guild_id, kind, cutoff, status_channel_id, status_message_id, started_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (guild_id, kind, cutoff, status_channel_id, status_message_id, time.time()),
            )
            await self.db.executemany(
                "INSERT OR IGNORE INTO analysis_run_members (guild_id, user_id) VALUES (?, ?)",
                [(guild_id, user_id) for user_id in user_ids],
            )
            await self.db.commit()

    async def get_analysis_run(self, guild_id: int) -> Optional[Dict[str, Any]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                """SELECT kind, cutoff, status_channel_id, status_message_id, started_at, messages_read, totals
                FROM analysis_runs WHERE guild_id = ?""",
                (guild_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cursor = await self.db.execute("SELECT user_id FROM analysis_run_members WHERE guild_id = ?", (guild_id,))
            members = {r[0] for r in await cursor.fetchall()}
            cursor = await self.db.execute(
                "SELECT channel_id, before_id, status FROM analysis_run_channels WHERE guild_id = ?", (guild_id,)
            )
            channels = {r[0]: (r[1], r[2]) for r in await cursor.fetchall()}
        keys = ("kind", "cutoff", "status_channel_id", "status_message_id", "started_at", "messages_read", "totals")
        return {**dict(zip(keys, row)), "members": members, "channels": channels}

    async def has_analysis_run(self, guild_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT 1 FROM analysis_runs WHERE guild_id = ?", (guild_id,))
            return await cursor.fetchone() is not None

    async def list_analysis_runs(self) -> List[int]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT guild_id FROM analysis_runs")
            return [row[0] for row in await cursor.fetchall()]

    async def set_analysis_run_message(self, guild_id: int, channel_id: int, message_id: int) -> None:
        await self._write([
            ("UPDATE analysis_runs SET status_channel_id = ?, status_message_id = ? WHERE guild_id = ?", [(channel_id, message_id, guild_id)])
        ])

    async def checkpoint_analysis_run(
        self,
        guild_id: int,
        results: Mapping[int, UserActivity],
        cursors: Mapping[int, int],
        finished: Mapping[int, str],
        messages_read: int,
        totals: str,
    ) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "UPDATE analysis_runs SET messages_read = ?, totals = ? WHERE guild_id = ?",
                (messages_read, totals, guild_id),
            )
            if cursor.rowcount == 0:
                return False
            await self._write_results(guild_id, results)
            await self.db.executemany(
                "INSERT OR REPLACE INTO analysis_run_channels (guild_id, channel_id, before_id, status) VALUES (?, ?, ?, ?)",
                [(guild_id, cid, before, "reading") for cid, before in cursors.items()]
                + [(guild_id, cid, None, status) for cid, status in finished.items()],
            )
            await self.db.commit()
            return True

    async def finish_analysis_run(self, guild_id: int, results: Mapping[int, UserActivity], user_ids: Iterable[int]) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("DELETE FROM analysis_runs WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                return False
            await self._write_results(guild_id, results)
            await self._mark_analyzed(guild_id, user_ids)
            for table in ANALYSIS_RUN_TABLES[1:]:
                await self.db.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
            await self.db.commit()
            return True

    async def get_leaderboard(self, metric: str, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        channel_table, user_table = METRIC_TABLES[metric]
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                column = COUNT_TABLES[user_table][1]
                cursor = await self.db.execute(
                    f"SELECT user_id, {column} FROM {user_table} WHERE guild_id = ? AND {column} > 0 ORDER BY {column} DESC",
                    (guild_id,),
                )
            else:
                column = COUNT_TABLES[channel_table][1]
                cursor = await self.db.execute(
                    f"SELECT user_id, {column} FROM {channel_table} WHERE guild_id = ? AND channel_id = ? AND {column} > 0 ORDER BY {column} DESC",
                    (guild_id, channel_id),
                )
            return await cursor.fetchall()

    async def get_keyword_leaders(self, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[str, List[Tuple[int, int]]]]:
        await self.ensure_connected()
        async with self.db_lock:
            if channel_id is None:
                cursor = await self.db.execute(
                    "SELECT keyword, user_id, count FROM keyword_user WHERE guild_id = ? AND count > 0",
                    (guild_id,),
                )
            else:
                cursor = await self.db.execute(
                    "SELECT keyword, user_id, count FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND count > 0",
                    (guild_id, channel_id),
                )
            rows = await cursor.fetchall()
        per_keyword: Dict[str, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        for keyword, user_id, count in rows:
            per_keyword[keyword][user_id] += count
        return [
            (keyword, sorted(users.items(), key=lambda item: item[1], reverse=True))
            for keyword, users in sorted(per_keyword.items())
        ]

    async def get_global_totals(self) -> Tuple[int, int]:
        await self.ensure_connected()
        async with self.db_lock:
            messages = await self._fetch_value("SELECT SUM(messages) FROM message_user", ())
            words = await self._fetch_value("SELECT SUM(count) FROM server", ())
        return messages, words

    async def get_keywords(self, guild_id: int) -> List[str]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT keyword FROM keyword WHERE guild_id = ? ORDER BY keyword ASC", (guild_id,))
            return [row[0] for row in await cursor.fetchall()]

    async def add_keywords(self, guild_id: int, keywords: Sequence[str]) -> Tuple[List[str], List[str]]:
        await self.ensure_connected()
        added: List[str] = []
        skipped: List[str] = []
        async with self.db_lock:
            cursor = await self.db.execute("SELECT keyword FROM keyword WHERE guild_id = ?", (guild_id,))
            existing = {row[0].lower() for row in await cursor.fetchall()}
            for keyword in keywords:
                cleaned = keyword.strip().lower()
                if not cleaned:
                    continue
                if cleaned in existing:
                    skipped.append(cleaned)
                    continue
                await self.db.execute("INSERT INTO keyword (guild_id, keyword) VALUES (?, ?)", (guild_id, cleaned))
                existing.add(cleaned)
                added.append(cleaned)
            await self.db.commit()
        return added, skipped

    async def replace_keywords(self, guild_id: int, keywords: Sequence[str]) -> None:
        await self._write([
            ("DELETE FROM keyword WHERE guild_id = ?", [(guild_id,)]),
            ("INSERT OR IGNORE INTO keyword (guild_id, keyword) VALUES (?, ?)", [(guild_id, k.strip().lower()) for k in keywords]),
        ])

    async def remove_keywords(self, guild_id: int, keywords: Sequence[str]) -> None:
        await self._write([("DELETE FROM keyword WHERE guild_id = ? AND keyword = ?", [(guild_id, k) for k in keywords])])

    async def clear_keywords(self, guild_id: int) -> None:
        await self._write([("DELETE FROM keyword WHERE guild_id = ?", [(guild_id,)])])

    async def get_user_full_stats(self, guild_id: int, user_id: int) -> Tuple[int, int, int, int, List[Tuple[str, int]]]:
        await self.ensure_connected()
        params = (guild_id, user_id)
        async with self.db_lock:
            words = await self._fetch_value("SELECT count FROM server WHERE guild_id = ? AND user_id = ?", params)
            messages = await self._fetch_value("SELECT messages FROM message_user WHERE guild_id = ? AND user_id = ?", params)
            attachments = await self._fetch_value("SELECT count FROM attachments_users WHERE guild_id = ? AND user_id = ?", params)
            emojis = await self._fetch_value("SELECT count FROM emojis_users WHERE guild_id = ? AND user_id = ?", params)
            cursor = await self.db.execute(
                "SELECT keyword, count FROM keyword_user WHERE guild_id = ? AND user_id = ? AND count > 0",
                params,
            )
            keywords = await cursor.fetchall()
        return words, messages, attachments, emojis, keywords

    async def reset_counts(self, guild_id: int, user_id: Optional[int] = None, channel_id: Optional[int] = None) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self._clear_counts(guild_id, user_id, channel_id)
            await self.db.commit()

    async def allow_reanalysis(self, guild_id: int, user_id: Optional[int] = None) -> None:
        await self.ensure_connected()
        async with self.db_lock:
            await self._clear_counts(guild_id, user_id, None)
            if user_id is None:
                await self.db.execute("DELETE FROM analyzed_users WHERE guild_id = ?", (guild_id,))
            else:
                await self.db.execute("DELETE FROM analyzed_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.commit()

    async def _clear_counts(self, guild_id: int, user_id: Optional[int], channel_id: Optional[int]) -> None:
        if channel_id is None:
            scope = "guild_id = ?" + (" AND user_id = ?" if user_id is not None else "")
            params = (guild_id,) + ((user_id,) if user_id is not None else ())
            for table in COUNT_DATA_TABLES:
                await self.db.execute(f"DELETE FROM {table} WHERE {scope}", params)
        else:
            scope = "guild_id = ? AND channel_id = ?" + (" AND user_id = ?" if user_id is not None else "")
            params = (guild_id, channel_id) + ((user_id,) if user_id is not None else ())
            for channel_table, user_table in METRIC_TABLES.values():
                column = COUNT_TABLES[channel_table][1]
                cursor = await self.db.execute(f"SELECT user_id, {column} FROM {channel_table} WHERE {scope}", params)
                removed = [{"guild_id": guild_id, "user_id": uid, "amount": amount} for uid, amount in await cursor.fetchall()]
                await self.db.execute(f"DELETE FROM {channel_table} WHERE {scope}", params)
                await self._subtract(user_table, removed)

            cursor = await self.db.execute(f"SELECT user_id, keyword, count FROM keyword_channel WHERE {scope}", params)
            removed = [{"guild_id": guild_id, "user_id": uid, "keyword": kw, "amount": n} for uid, kw, n in await cursor.fetchall()]
            await self.db.execute(f"DELETE FROM keyword_channel WHERE {scope}", params)
            await self._subtract("keyword_user", removed)

            for table in ("monthly_user_stats", "monthly_user_keywords"):
                await self.db.execute(f"DELETE FROM {table} WHERE {scope}", params)

        await self.db.execute(
            """INSERT INTO count_resets (guild_id, user_id, channel_id, reset_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, user_id, channel_id) DO UPDATE SET reset_at = excluded.reset_at""",
            (guild_id, user_id or 0, channel_id or 0, now_snowflake()),
        )

    async def _subtract(self, table: str, removed: List[Dict[str, Any]]) -> None:
        decrement, prune = _decrement_sql(table)
        await self.db.executemany(decrement, [(row["amount"], *_key_values(table, row)) for row in removed])
        await self.db.executemany(prune, [_key_values(table, row) for row in removed])

    async def reset_entire_server(self, guild_id: int) -> None:
        await self._write([(f"DELETE FROM {table} WHERE guild_id = ?", [(guild_id,)]) for table in GUILD_TABLES])

    async def is_user_analyzed(self, guild_id: int, user_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT 1 FROM analyzed_users WHERE user_id = ? AND guild_id = ?",
                (user_id, guild_id),
            )
            return await cursor.fetchone() is not None

    async def get_analyzed_users(self, guild_id: int) -> Set[int]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT user_id FROM analyzed_users WHERE guild_id = ?", (guild_id,))
            return {row[0] for row in await cursor.fetchall()}

    async def get_user_monthly_breakdown(self, guild_id: int, user_id: int) -> List[Dict[str, Any]]:
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
                WHERE guild_id = ? AND user_id = ? AND count > 0
                """,
                (guild_id, user_id),
            )
            keyword_rows = await cursor.fetchall()

        months: Dict[Tuple[int, int], Dict[str, Any]] = {}
        for year, month, channel_id, words, messages, attachments, emojis in stat_rows:
            entry = months.setdefault(
                (year, month),
                {
                    "year": year,
                    "month": month,
                    "words": 0,
                    "messages": 0,
                    "attachments": 0,
                    "emojis": 0,
                    "keywords": defaultdict(int),
                    "channels": {},
                },
            )
            entry["words"] += words
            entry["messages"] += messages
            entry["attachments"] += attachments
            entry["emojis"] += emojis
            entry["channels"][channel_id] = {
                "words": words,
                "messages": messages,
                "attachments": attachments,
                "emojis": emojis,
                "keywords": {},
            }

        for year, month, channel_id, keyword, count in keyword_rows:
            entry = months.get((year, month))
            if entry is None:
                continue
            entry["keywords"][keyword] += count
            if channel_id in entry["channels"]:
                entry["channels"][channel_id]["keywords"][keyword] = count

        ordered = sorted(months.values(), key=lambda item: (item["year"], item["month"]), reverse=True)
        for entry in ordered:
            entry["keywords"] = dict(entry["keywords"])
        return ordered

    async def get_channel_monthly_breakdown(self, guild_id: int, channel_id: int) -> List[Tuple[int, int, int, int, int, int]]:
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
        return [(row[0], row[1], row[2] or 0, row[3] or 0, row[4] or 0, row[5] or 0) for row in rows]
