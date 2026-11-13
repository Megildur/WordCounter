"""Every MySQL read and write WordCounter makes, as methods on `WordCounterDatabase` (`bot.db`).

This file holds the schema, the counter upserts, tracking periods, resets and the analysis run tables that `/analyze_chat` checkpoints into. The swear jar and the owner data tools add their own methods through `SwearJarQueries` (swear_store.py) and `StoredDataQueries` (stored_data.py).

The connection and `?` placeholder handling live in mysql.py, and the one-time copy of the old SQLite file in sqlite_import.py.
"""

from __future__ import annotations
import asyncio
import os
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import logging
from pathlib import Path
from cogs.utils import config
from cogs.utils.mysql import TABLE_OPTIONS, SessionLock, create_pool
from cogs.utils.stored_data import BLACKLIST_SCHEMA, META_SCHEMA, StoredDataQueries
from cogs.utils.sqlite_import import copy_sqlite
from cogs.utils.swear_store import JAR_SCHEMA, JAR_TABLES, SWEAR_COUNT_TABLES, SwearCounts, SwearJarQueries

log = logging.getLogger(__name__)

# What an analysis hands over for one user: counts per (channel_id, year, month) as [words, messages, attachments, emojis], and keyword counts per (channel_id, keyword, year, month).
MonthlyKey = Tuple[int, int, int]
MonthlyKeywordKey = Tuple[int, str, int, int]
UserActivity = Tuple[Mapping[MonthlyKey, Sequence[int]], Mapping[MonthlyKeywordKey, int]]
# What `_write` takes: pairs of an SQL statement and the rows to run it with.
Statements = Sequence[Tuple[str, Sequence[Sequence[Any]]]]

# A Discord snowflake ID holds its creation time as milliseconds since this moment, 2015-01-01 UTC.
DISCORD_EPOCH_MS = 1420070400000

# Every counter table: its key columns in the order the INSERT and `_key_values` use, and the column that holds the count. The statements for these tables are built from this, so a new counter table starts here.
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
    **SWEAR_COUNT_TABLES,
}

# Each metric's (per-channel table, per-user table). A message adds to both, and a channel reset subtracts from the per-user one because it has no channel column.
METRIC_TABLES: Dict[str, Tuple[str, str]] = {
    "words": ("counters", "server"),
    "messages": ("message_channels", "message_user"),
    "attachments": ("attachments_channels", "attachments_users"),
    "emojis": ("emojis_channels", "emojis_users"),
}

LEADERBOARD_TABLES: Dict[str, Tuple[str, str]] = {**METRIC_TABLES, "swears": ("swears_channels", "swears_users")}

MONTHLY_KEYS = "guild_id = ? AND user_id = ? AND channel_id = ? AND year = ? AND month = ?"

# One row, run with execute, so `?` is filled in the UPDATE part too. The insert gets the deltas clamped to 0 and the update adds the signed deltas, floored at 0, because an edit can add words and remove emojis in one go.
MONTHLY_UPSERT = """
    INSERT INTO monthly_user_stats (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
        words = GREATEST(0, words + ?),
        messages = GREATEST(0, messages + ?),
        attachments = GREATEST(0, attachments + ?),
        emojis = GREATEST(0, emojis + ?)
"""

# The same upsert for executemany, which only fills the VALUES list, so the UPDATE part adds VALUES(col) instead of using `?`. Every value is non-negative here, so there is no floor.
MONTHLY_BULK_UPSERT = """
    INSERT INTO monthly_user_stats (guild_id, user_id, channel_id, year, month, words, messages, attachments, emojis)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE
        words = words + VALUES(words),
        messages = messages + VALUES(messages),
        attachments = attachments + VALUES(attachments),
        emojis = emojis + VALUES(emojis)
"""

# For a deleted or shortened message. It only changes a row that exists, and MONTHLY_PRUNE then deletes the row once everything in it is zero.
MONTHLY_DECREMENT = f"""
    UPDATE monthly_user_stats SET
        words = GREATEST(0, words + ?),
        messages = GREATEST(0, messages + ?),
        attachments = GREATEST(0, attachments + ?),
        emojis = GREATEST(0, emojis + ?)
    WHERE {MONTHLY_KEYS}
"""

MONTHLY_PRUNE = f"""
    DELETE FROM monthly_user_stats
    WHERE {MONTHLY_KEYS} AND words <= 0 AND messages <= 0 AND attachments <= 0 AND emojis <= 0
"""

# The width of the keyword VARCHAR(255) columns.
KEYWORD_LIMIT = 255

# Run on every start. CREATE TABLE IF NOT EXISTS leaves existing tables alone, so a column added later also needs an entry in ADDED_COLUMNS.
SCHEMA = tuple(statement + TABLE_OPTIONS for statement in (
    # counters (per channel) and server (per user) hold the word counts. The other metrics have tables named after them.
    """CREATE TABLE IF NOT EXISTS counters (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    # Where counting is on. channel_id is a channel, a category, or 1 for the entire server.
    """CREATE TABLE IF NOT EXISTS channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS server (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    # `ignore` is a reserved word in MySQL, so it is always written with backticks.
    """CREATE TABLE IF NOT EXISTS `ignore` (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS keyword (
        guild_id BIGINT NOT NULL, keyword VARCHAR(255) NOT NULL,
        PRIMARY KEY (guild_id, keyword))""",
    """CREATE TABLE IF NOT EXISTS keyword_channel (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, keyword VARCHAR(255) NOT NULL, user_id BIGINT NOT NULL,
        count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id, keyword, user_id))""",
    """CREATE TABLE IF NOT EXISTS keyword_user (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, keyword VARCHAR(255) NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id, keyword))""",
    """CREATE TABLE IF NOT EXISTS attachments_channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS attachments_users (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS message_channels (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, messages BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS message_user (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, messages BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS analyzed_users (
        user_id BIGINT NOT NULL, guild_id BIGINT NOT NULL,
        PRIMARY KEY (user_id, guild_id))""",
    """CREATE TABLE IF NOT EXISTS emojis_channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS emojis_users (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS monthly_user_stats (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL,
        year INT NOT NULL, month INT NOT NULL,
        words BIGINT NOT NULL DEFAULT 0, messages BIGINT NOT NULL DEFAULT 0,
        attachments BIGINT NOT NULL DEFAULT 0, emojis BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, channel_id, year, month),
        INDEX idx_monthly_user (guild_id, user_id, year, month),
        INDEX idx_monthly_channel (guild_id, channel_id, year, month))""",
    """CREATE TABLE IF NOT EXISTS monthly_user_keywords (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, keyword VARCHAR(255) NOT NULL,
        year INT NOT NULL, month INT NOT NULL, count BIGINT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, channel_id, keyword, year, month))""",
    # started and ended are snowflakes (see now_snowflake), so they compare directly with message IDs. ended is NULL while counting is on.
    """CREATE TABLE IF NOT EXISTS tracking_periods (
        guild_id BIGINT NOT NULL, started BIGINT NOT NULL, ended BIGINT,
        PRIMARY KEY (guild_id, started))""",
    # One marker per reset scope, with user_id or channel_id 0 meaning all. Messages older than reset_at lost their counts, so an analysis counts them again.
    """CREATE TABLE IF NOT EXISTS count_resets (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, reset_at BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id, channel_id))""",
    # The next three tables hold an analysis that is running or was interrupted, one run per server. analysis_runs keeps the kind (member, server or swears), the snowflake the crawl reads back from (cutoff), the progress message to edit, and the running totals as JSON.
    """CREATE TABLE IF NOT EXISTS analysis_runs (
        guild_id BIGINT PRIMARY KEY, kind VARCHAR(32) NOT NULL, cutoff BIGINT NOT NULL,
        status_channel_id BIGINT, status_message_id BIGINT, started_at DOUBLE NOT NULL,
        messages_read BIGINT NOT NULL DEFAULT 0, totals MEDIUMTEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS analysis_run_members (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    # Per channel: while status is reading, before_id is the oldest message read so far and where a restart continues. Finished channels have no before_id and a status of done, unreadable or failed.
    """CREATE TABLE IF NOT EXISTS analysis_run_channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, before_id BIGINT, status VARCHAR(32) NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
))

# Everything a counts reset clears: the counted activity, but none of the settings.
COUNT_DATA_TABLES = (
    "server", "counters", "message_user", "message_channels", "attachments_users", "attachments_channels",
    "emojis_users", "emojis_channels", "keyword_user", "keyword_channel", "monthly_user_stats", "monthly_user_keywords",
)

ANALYSIS_RUN_TABLES = ("analysis_runs", "analysis_run_members", "analysis_run_channels")

# Every table with a guild_id. `reset_entire_server` deletes one server's rows from each, so a new per-server table must be added here.
GUILD_TABLES = (
    *COUNT_DATA_TABLES, "analyzed_users", "channels", "ignore", "keyword",
    "tracking_periods", "count_resets", *ANALYSIS_RUN_TABLES, *JAR_TABLES,
)


def now_snowflake() -> int:
    """The current time as a Discord snowflake, so a moment can be compared with message IDs. Tracking periods and reset markers use it."""
    return (int(time.time() * 1000) - DISCORD_EPOCH_MS) << 22


@lru_cache(maxsize=None)
def _increment_sql(table: str) -> str:
    """The upsert that adds to a counter table. It uses VALUES(col) rather than `?` in the UPDATE part because the same statement also runs through executemany."""
    keys, column = COUNT_TABLES[table]
    names = ", ".join(keys)
    marks = ", ".join("?" * (len(keys) + 1))
    return (
        f"INSERT INTO {table} ({names}, {column}) VALUES ({marks}) "
        f"ON DUPLICATE KEY UPDATE {column} = {column} + VALUES({column})"
    )


@lru_cache(maxsize=None)
def _decrement_sql(table: str) -> Tuple[str, str]:
    """Returns the pair of statements that take a count off a counter table: an UPDATE floored at 0, then a DELETE of rows that reached 0 so empty rows don't pile up."""
    keys, column = COUNT_TABLES[table]
    where = " AND ".join(f"{key} = ?" for key in keys)
    return (
        f"UPDATE {table} SET {column} = GREATEST(0, {column} - ?) WHERE {where}",
        f"DELETE FROM {table} WHERE {where} AND {column} <= 0",
    )


def _key_values(table: str, values: Mapping[str, Any]) -> Tuple[Any, ...]:
    return tuple(values[key] for key in COUNT_TABLES[table][0])


# The db_meta row that records the SQLite copy as done.
SQLITE_IMPORT = "sqlite_import"
# Columns added after their table first shipped, as (table, column, definition). They are in CREATE TABLE too, and `create_tables` adds them to databases made before them.
ADDED_COLUMNS = (
    ("monthly_user_stats", "emojis", "BIGINT NOT NULL DEFAULT 0"),
    ("jar_settings", "english", "INT NOT NULL DEFAULT 1"),
    ("jar_settings", "polish", "INT NOT NULL DEFAULT 0"),
)


class NotMigrated(RuntimeError):
    """Raised at start-up when copying the old SQLite file into MySQL didn't finish. main.py exits with its message."""
    pass


class WordCounterDatabase(SwearJarQueries, StoredDataQueries):
    """The bot's database, available as `bot.db`.

    `self.db` is the open `Session` and exists only inside `async with self.db_lock:`, which commits when the block ends. Public methods that read or write open their own block, so they can't be called from inside one: the lock is not re-entrant and the task would wait on itself.

    Methods that start with an underscore expect the caller to hold the lock already, except `_write`, which takes it.
    """
    def __init__(self, bot) -> None:
        self.pool = None
        self.db = None
        self.db_lock = SessionLock(self)
        self.bot = bot

    async def connect_pool(self) -> None:
        if self.pool is None:
            self.pool = await create_pool()

    async def connect(self) -> None:
        await self.connect_pool()
        await self.initialize_database()

    async def ensure_connected(self) -> None:
        """Opens the pool and creates the tables if that hasn't happened yet. Public methods call it before they take `db_lock`."""
        if self.pool is None:
            await self.connect()

    async def close(self) -> None:
        pool, self.pool = self.pool, None
        if pool is not None:
            pool.close()
            await pool.wait_closed()

    async def create_tables(self) -> None:
        async with self.db_lock:
            for statement in (*SCHEMA, *JAR_SCHEMA, *BLACKLIST_SCHEMA, *META_SCHEMA):
                await self.db.execute(statement)
            # MySQL has no ADD COLUMN IF NOT EXISTS, so look at information_schema before altering.
            cursor = await self.db.execute(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
            )
            present = {(table, column) for table, column in await cursor.fetchall()}
            for table, column, definition in ADDED_COLUMNS:
                if (table, column) not in present:
                    await self.db.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")

    async def sqlite_imported(self) -> bool:
        async with self.db_lock:
            return bool(await self._fetch_value("SELECT COUNT(*) FROM db_meta WHERE name = ?", (SQLITE_IMPORT,)))

    async def mark_sqlite_imported(self, note: str) -> None:
        await self._write([("REPLACE INTO db_meta (name, value) VALUES (?, ?)", [(SQLITE_IMPORT, note)])])

    async def initialize_database(self) -> None:
        """Creates the tables, then copies the old SQLite file once, if it exists and db_meta doesn't already record a copy. A failed copy raises NotMigrated, which stops the bot from starting."""
        await self.create_tables()
        if os.path.isfile(config.SQLITE_PATH) and not await self.sqlite_imported():
            log.info("Copying %s into MySQL before starting", config.SQLITE_PATH)
            result = await copy_sqlite(self, Path(config.SQLITE_PATH))
            if not result.ok:
                raise NotMigrated(result.problem())
            log.info("Copied %s rows from %s into MySQL", f"{sum(result.copied.values()):,}", config.SQLITE_PATH)

    async def _fetch_value(self, sql: str, params: Sequence[Any]) -> int:
        """Returns the first column of the first row, or 0 when there is no row or the value is NULL (a SUM over no rows). Call it inside `db_lock`."""
        cursor = await self.db.execute(sql, params)
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

    async def _write(self, statements: Statements, sync_tracking_for: Optional[int] = None) -> None:
        """Runs each (sql, rows) pair with executemany in one transaction and commits. It takes `db_lock` itself, so don't call it from inside a lock block. Pass `sync_tracking_for` when the statements change a server's channels, so its tracking period is opened or closed in the same transaction."""
        await self.ensure_connected()
        async with self.db_lock:
            for sql, rows in statements:
                await self.db.executemany(sql, list(rows))
            if sync_tracking_for is not None:
                await self._sync_tracking_period(sync_tracking_for)
            await self.db.commit()

    async def _sync_tracking_period(self, guild_id: int) -> None:
        """Keeps tracking_periods in step with `channels`: a period opens when the first channel is switched on and closes when the last one is switched off."""
        enabled = await self._fetch_value("SELECT COUNT(*) FROM channels WHERE guild_id = ?", (guild_id,))
        is_open = await self._fetch_value(
            "SELECT COUNT(*) FROM tracking_periods WHERE guild_id = ? AND ended IS NULL", (guild_id,)
        )
        if enabled and not is_open:
            await self.db.execute(
                "INSERT IGNORE INTO tracking_periods (guild_id, started, ended) VALUES (?, ?, NULL)",
                (guild_id, now_snowflake()),
            )
        elif is_open and not enabled:
            await self.db.execute(
                "UPDATE tracking_periods SET ended = ? WHERE guild_id = ? AND ended IS NULL",
                (now_snowflake(), guild_id),
            )

    async def ensure_tracking_period(self, guild_id: int, started: int) -> None:
        """Opens a period starting at the snowflake `started` when the server has tracking on but no open period. Start-up passes the time the bot joined."""
        await self.ensure_connected()
        async with self.db_lock:
            enabled = await self._fetch_value("SELECT COUNT(*) FROM channels WHERE guild_id = ?", (guild_id,))
            is_open = await self._fetch_value(
                "SELECT COUNT(*) FROM tracking_periods WHERE guild_id = ? AND ended IS NULL", (guild_id,)
            )
            if enabled and not is_open:
                await self.db.execute(
                    "INSERT IGNORE INTO tracking_periods (guild_id, started, ended) VALUES (?, ?, NULL)",
                    (guild_id, started),
                )
                await self.db.commit()

    async def get_count_history(self, guild_id: int) -> Tuple[List[Tuple[int, Optional[int]]], Dict[Tuple[int, int], int]]:
        """Returns the server's tracking periods as (started, ended) and its reset markers as {(user_id, channel_id): reset_at}, with 0 meaning all users or all channels. The analysis uses both to skip messages that were already counted live."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT started, ended FROM tracking_periods WHERE guild_id = ?", (guild_id,))
            periods = [(row[0], row[1]) for row in await cursor.fetchall()]
            cursor = await self.db.execute("SELECT user_id, channel_id, reset_at FROM count_resets WHERE guild_id = ?", (guild_id,))
            resets = {(row[0], row[1]): row[2] for row in await cursor.fetchall()}
        return periods, resets

    async def get_guild_tracking_config(self, guild_id: int) -> Tuple[Set[int], Set[int]]:
        """Returns (watched IDs, ignored IDs). Either set can hold categories as well as channels, and 1 in the watched set means the entire server."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT channel_id FROM channels WHERE guild_id = ?", (guild_id,))
            watched_ids = {row[0] for row in await cursor.fetchall()}
            cursor = await self.db.execute("SELECT channel_id FROM `ignore` WHERE guild_id = ?", (guild_id,))
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
                ("DELETE FROM `ignore` WHERE guild_id = ?", [(guild_id,)]),
            ],
            sync_tracking_for=guild_id,
        )

    async def add_watched_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write(
            [("INSERT IGNORE INTO channels (guild_id, channel_id) VALUES (?, ?)", [(guild_id, cid) for cid in channel_ids])],
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
        await self._write([("INSERT IGNORE INTO `ignore` (guild_id, channel_id) VALUES (?, ?)", [(guild_id, cid) for cid in channel_ids])])

    async def remove_ignored_channels(self, guild_id: int, channel_ids: Sequence[int]) -> None:
        await self._write([("DELETE FROM `ignore` WHERE guild_id = ? AND channel_id = ?", [(guild_id, cid) for cid in channel_ids])])

    async def clear_ignored_channels(self, guild_id: int) -> None:
        await self._write([("DELETE FROM `ignore` WHERE guild_id = ?", [(guild_id,)])])

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
        """Applies one message's effect to every counter table in a single transaction. Each value is a change, so a deleted message passes negatives and an edit passes the difference. `keywords` maps keyword to its change."""
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
                # A positive change upserts. A negative one subtracts without going below zero and deletes the row once it reaches zero.
                if delta > 0:
                    await self.db.execute(_increment_sql(table), (*key_values, delta))
                else:
                    decrement, prune = _decrement_sql(table)
                    await self.db.execute(decrement, (-delta, *key_values))
                    await self.db.execute(prune, key_values)

            if any(monthly):
                month_key = (guild_id, user_id, channel_id, year, month)
                # Any positive change takes the upsert, which applies the negative ones too. Only an all-negative change needs the update and prune.
                if any(delta > 0 for delta in monthly):
                    await self.db.execute(MONTHLY_UPSERT, (*month_key, *(max(0, d) for d in monthly), *monthly))
                else:
                    await self.db.execute(MONTHLY_DECREMENT, (*monthly, *month_key))
                    await self.db.execute(MONTHLY_PRUNE, month_key)
            await self.db.commit()

    async def _write_results(self, guild_id: int, results: Mapping[int, UserActivity]) -> None:
        """Adds analysis results, a `UserActivity` per user, to the counter tables. It adds to what is already stored instead of replacing it, so a checkpoint's partial results can be saved one after another. The caller holds `db_lock` and commits."""
        rows: Dict[str, List[Tuple[Any, ...]]] = defaultdict(list)
        monthly_rows: List[Tuple[int, ...]] = []

        for user_id, (monthly, monthly_keywords) in results.items():
            # The per-channel and per-user totals are summed from the monthly counts, so every table agrees with them.
            channel_totals: Dict[int, List[int]] = defaultdict(lambda: [0, 0, 0, 0])
            for (channel_id, year, month), counts in monthly.items():
                monthly_rows.append((guild_id, user_id, channel_id, year, month, *counts))
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

        # executemany only fills the VALUES list, which is why both of these add with VALUES(col) in their UPDATE part.
        for table, table_rows in rows.items():
            await self.db.executemany(_increment_sql(table), table_rows)
        await self.db.executemany(MONTHLY_BULK_UPSERT, monthly_rows)

    async def _mark_analyzed(self, guild_id: int, user_ids: Iterable[int]) -> None:
        """Marks members as analyzed, which stops `/analyze_chat` from counting them a second time. Members already marked are ignored."""
        await self.db.executemany(
            "INSERT IGNORE INTO analyzed_users (user_id, guild_id) VALUES (?, ?)",
            [(user_id, guild_id) for user_id in user_ids],
        )

    async def save_retroactive_results(
        self,
        guild_id: int,
        results: Mapping[int, UserActivity],
        analyzed_user_ids: Iterable[int],
    ) -> None:
        """Saves a finished search for one member, and marks them analyzed, in one transaction. A search has no checkpoints, so nothing is saved until it is done."""
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
        """Records a new run so it can resume after a restart, replacing any earlier run for the server. `kind` is member, server or swears, `cutoff` is the snowflake the crawl reads back from, and `user_ids` are the members to mark analyzed when it finishes."""
        await self.ensure_connected()
        async with self.db_lock:
            for table in ANALYSIS_RUN_TABLES:
                await self.db.execute(f"DELETE FROM `{table}` WHERE guild_id = ?", (guild_id,))
            await self.db.execute(
                """INSERT INTO analysis_runs (guild_id, kind, cutoff, status_channel_id, status_message_id, started_at, totals)
                VALUES (?, ?, ?, ?, ?, ?, '{}')""",
                (guild_id, kind, cutoff, status_channel_id, status_message_id, time.time()),
            )
            await self.db.executemany(
                "INSERT IGNORE INTO analysis_run_members (guild_id, user_id) VALUES (?, ?)",
                [(guild_id, user_id) for user_id in user_ids],
            )
            await self.db.commit()

    async def get_analysis_run(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Returns the saved run for a server, or None. It has kind, cutoff, status_channel_id, status_message_id, started_at, messages_read and `totals` (still the raw JSON string), plus `members` (a set of user IDs) and `channels` ({channel_id: (before_id, status)})."""
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
        swears: Optional[SwearCounts] = None,
    ) -> bool:
        """Saves one checkpoint of a crawl in a single transaction: the counts read since the last checkpoint, the running `totals` JSON, and where each channel stands. `cursors` maps channel_id to the oldest message read so far and `finished` maps channel_id to its final status.

        Counts and cursors commit together, so after a restart the crawl continues from exactly where the saved counts stop and nothing is counted twice.

        Returns False, with nothing written, when the run row is gone because the server was reset. The crawl treats that as a cancel.
        """
        await self.ensure_connected()
        async with self.db_lock:
            # Updated first on purpose. The reset deletes this row, so a rowcount of 0 means the run was cancelled and none of the writes below should happen.
            cursor = await self.db.execute(
                "UPDATE analysis_runs SET messages_read = ?, totals = ? WHERE guild_id = ?",
                (messages_read, totals, guild_id),
            )
            if cursor.rowcount == 0:
                return False
            await self._write_results(guild_id, results)
            if swears:
                await self._write_swear_counts(guild_id, swears)
            await self.db.executemany(
                "REPLACE INTO analysis_run_channels (guild_id, channel_id, before_id, status) VALUES (?, ?, ?, ?)",
                [(guild_id, cid, before, "reading") for cid, before in cursors.items()]
                + [(guild_id, cid, None, status) for cid, status in finished.items()],
            )
            await self.db.commit()
            return True

    async def finish_analysis_run(
        self,
        guild_id: int,
        results: Mapping[int, UserActivity],
        user_ids: Iterable[int],
        swears: Optional[SwearCounts] = None,
        swear_history_before: Optional[int] = None,
    ) -> bool:
        """Saves the last results, marks the members analyzed and removes the run, all in one transaction. Returns False without writing anything when the run row is already gone. For a swear run, `swear_history_before` is the snowflake the crawl read back from, and everything older than it is recorded as covered."""
        await self.ensure_connected()
        async with self.db_lock:
            # Deleting the run row first doubles as the cancel check, like the update in a checkpoint.
            cursor = await self.db.execute("DELETE FROM analysis_runs WHERE guild_id = ?", (guild_id,))
            if cursor.rowcount == 0:
                return False
            await self._write_results(guild_id, results)
            await self._mark_analyzed(guild_id, user_ids)
            if swears:
                await self._write_swear_counts(guild_id, swears)
            if swear_history_before is not None:
                await self._cover_swear_history(guild_id, swear_history_before)
            # analysis_runs is already deleted, so this clears the other two run tables.
            for table in ANALYSIS_RUN_TABLES[1:]:
                await self.db.execute(f"DELETE FROM `{table}` WHERE guild_id = ?", (guild_id,))
            await self.db.commit()
            return True

    async def get_leaderboard(self, metric: str, guild_id: int, channel_id: Optional[int] = None) -> List[Tuple[int, int]]:
        """Returns [(user_id, count)] with the biggest first, for the whole server or for one channel. Members with a count of 0 are left out. `metric` must be a key of LEADERBOARD_TABLES."""
        channel_table, user_table = LEADERBOARD_TABLES[metric]
        await self.ensure_connected()
        async with self.db_lock:
            # The table and column names in these f-strings come from LEADERBOARD_TABLES and COUNT_TABLES, never from user input.
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
        """Returns [(keyword, [(user_id, count), ...])] sorted by keyword, with each member list biggest first. Covers the whole server or one channel."""
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
        """Returns (messages, words) counted across every server, in that order. The status line uses it."""
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
        """Returns (added, skipped). Keywords are lowercased. A keyword is skipped when the server already has it or it is longer than KEYWORD_LIMIT."""
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
                if cleaned in existing or len(cleaned) > KEYWORD_LIMIT:
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
            (
                "INSERT IGNORE INTO keyword (guild_id, keyword) VALUES (?, ?)",
                [(guild_id, k.strip().lower()) for k in keywords if k.strip() and len(k.strip()) <= KEYWORD_LIMIT],
            ),
        ])

    async def remove_keywords(self, guild_id: int, keywords: Sequence[str]) -> None:
        await self._write([("DELETE FROM keyword WHERE guild_id = ? AND keyword = ?", [(guild_id, k) for k in keywords])])

    async def clear_keywords(self, guild_id: int) -> None:
        await self._write([("DELETE FROM keyword WHERE guild_id = ?", [(guild_id,)])])

    async def get_user_full_stats(self, guild_id: int, user_id: int) -> Tuple[int, int, int, int, List[Tuple[str, int]]]:
        """Returns (words, messages, attachments, emojis, [(keyword, count)]). A member with no rows gets zeros and an empty list."""
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
        """Clears counts for the whole server, one member, one channel, or one member in one channel, and records a reset marker. Settings, the keyword list and the analyzed flags are kept."""
        await self.ensure_connected()
        async with self.db_lock:
            await self._clear_counts(guild_id, user_id, channel_id)
            await self.db.commit()

    async def allow_reanalysis(self, guild_id: int, user_id: Optional[int] = None) -> None:
        """Clears the counts and the analyzed flags for the whole server or one member, so `/analyze_chat` can read their messages again."""
        await self.ensure_connected()
        async with self.db_lock:
            await self._clear_counts(guild_id, user_id, None)
            if user_id is None:
                await self.db.execute("DELETE FROM analyzed_users WHERE guild_id = ?", (guild_id,))
            else:
                await self.db.execute("DELETE FROM analyzed_users WHERE guild_id = ? AND user_id = ?", (guild_id, user_id))
            await self.db.commit()

    async def _clear_counts(self, guild_id: int, user_id: Optional[int], channel_id: Optional[int]) -> None:
        """Deletes counts for the given scope and writes the reset marker. The caller holds `db_lock` and commits."""
        if channel_id is None:
            scope = "guild_id = ?" + (" AND user_id = ?" if user_id is not None else "")
            params = (guild_id,) + ((user_id,) if user_id is not None else ())
            for table in COUNT_DATA_TABLES:
                await self.db.execute(f"DELETE FROM `{table}` WHERE {scope}", params)
        else:
            scope = "guild_id = ? AND channel_id = ?" + (" AND user_id = ?" if user_id is not None else "")
            params = (guild_id, channel_id) + ((user_id,) if user_id is not None else ())
            # The per-user tables have no channel column, so take what the channel held off each member's total before deleting the channel rows.
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
                await self.db.execute(f"DELETE FROM `{table}` WHERE {scope}", params)

        # A marker of 0 for the user or the channel means all of them. The analysis reads these to count the wiped messages again.
        await self.db.execute(
            """INSERT INTO count_resets (guild_id, user_id, channel_id, reset_at) VALUES (?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE reset_at = VALUES(reset_at)""",
            (guild_id, user_id or 0, channel_id or 0, now_snowflake()),
        )

    async def _subtract(self, table: str, removed: List[Dict[str, Any]]) -> None:
        """Takes each row's `amount` off a counter table and deletes rows that reach zero. Each row holds the table's key columns and `amount`."""
        decrement, prune = _decrement_sql(table)
        await self.db.executemany(decrement, [(row["amount"], *_key_values(table, row)) for row in removed])
        await self.db.executemany(prune, [_key_values(table, row) for row in removed])

    async def reset_entire_server(self, guild_id: int) -> None:
        await self._write([(f"DELETE FROM `{table}` WHERE guild_id = ?", [(guild_id,)]) for table in GUILD_TABLES])

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
        """Returns one dict per month that has stats, newest first. Each has year, month, words, messages, attachments, emojis, `keywords` ({keyword: count}) and `channels` ({channel_id: the same counts and keywords}). Keyword rows for a month with no stats row are dropped."""
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
        """Returns (year, month, words, messages, attachments, emojis) per month, summed over every member, newest first."""
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
