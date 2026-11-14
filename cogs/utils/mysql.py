"""A thin async layer over aiomysql, so the rest of the code writes `?` placeholders and shares one connection.

`SessionLock` is what `WordCounterDatabase.db_lock` is. `async with db_lock` takes the pool's only connection, sets `owner.db` to a `Session` for the block, commits when the block ends and rolls back if it raised.

`SessionLock` is an asyncio.Lock, so database blocks run one at a time and the pool needs only one connection. The lock is not re-entrant, so a block must never open another one.
"""

from __future__ import annotations
import asyncio
import decimal
import logging
import re
import time
import warnings
from typing import Any, Iterable, List, Optional, Sequence, Tuple
import aiomysql
import pymysql
from pymysql.constants import CLIENT
from cogs.utils import config

# aiomysql raises pymysql's errors, so this catches any MySQL failure.
DatabaseError = pymysql.err.MySQLError
# Added to every CREATE TABLE. utf8mb4_bin makes text comparisons case-sensitive.
TABLE_OPTIONS = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
RECYCLE_SECONDS = 120
# A database block that waited for the lock, or held it, longer than this is logged, because every other answer that needs the database waits behind it.
LOCK_SLOW_SECONDS = 1.0

log = logging.getLogger(__name__)

# The database layer keeps the rows of channels, ignore and keyword in memory, because every message reads them. A statement that changes one of those tables is spotted here, whoever wrote it, and the layer is told to forget what it kept when the block ends, however it ends. The table's name has to follow INTO, UPDATE, FROM, JOIN or TABLE, so keyword_channel, a column called keyword and INSERT IGNORE don't count.
_WRITES = re.compile(r"^\s*(?:INSERT|REPLACE|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", re.IGNORECASE)
_KEPT_TABLE = re.compile(r"\b(?:INTO|UPDATE|FROM|JOIN|TABLE)\s+`?(?:channels|ignore|keyword)`?(?!\w)", re.IGNORECASE)

warnings.filterwarnings("ignore", module="aiomysql")


def mysql_sql(sql: str) -> str:
    """Turns `?` placeholders into MySQL's `%s`, after doubling any `%` so the driver doesn't read it as a placeholder. A `?` inside a string literal would be rewritten too, so keep question marks out of SQL text."""
    return sql.replace("%", "%%").replace("?", "%s")


def _plain(value: Any) -> Any:
    """MySQL returns SUM() and some arithmetic as Decimal, so whole values become int and the rest float."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _row(row: Optional[Sequence[Any]]) -> Optional[Tuple[Any, ...]]:
    return None if row is None else tuple(_plain(value) for value in row)


class Cursor:
    def __init__(self, cursor: aiomysql.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        # Rows matched, not rows changed, because create_pool sets CLIENT.FOUND_ROWS. An UPDATE that writes the same values still counts.
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    async def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return _row(await self._cursor.fetchone())

    async def fetchall(self) -> List[Tuple[Any, ...]]:
        return [_row(row) for row in await self._cursor.fetchall()]


class Session:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection
        # The start of the first statement of the block, so a lock held for too long can say what held it. Never the values.
        self.first_sql: Optional[str] = None
        # True once the block has written to a table whose rows the database layer keeps in memory.
        self.touches_kept = False

    def _note(self, sql: str) -> None:
        if self.first_sql is None:
            self.first_sql = " ".join(sql.split())[:80]
        if not self.touches_kept and _WRITES.match(sql) and _KEPT_TABLE.search(sql):
            self.touches_kept = True

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        self._note(sql)
        cursor = await self.connection.cursor()
        await cursor.execute(mysql_sql(sql), tuple(params))
        return Cursor(cursor)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Optional[Cursor]:
        """Runs one statement for many rows and returns None when there are no rows.

        aiomysql turns an `INSERT ... VALUES (...)` into one multi-row statement and fills in only the VALUES list. A `?` anywhere else, such as in `ON DUPLICATE KEY UPDATE`, gets no value, so bulk upserts write `VALUES(col)` there.
        """
        rows = [tuple(row) for row in rows]
        if not rows:
            return None
        self._note(sql)
        cursor = await self.connection.cursor()
        await cursor.executemany(mysql_sql(sql), rows)
        return Cursor(cursor)

    async def commit(self) -> None:
        await self.connection.commit()


def _warn(text: str, *args: Any) -> None:
    """Logs a warning and swallows anything the logging itself raises: a log line must never be the reason the lock isn't given back or a block isn't committed."""
    try:
        log.warning(text, *args)
    except Exception:
        pass


class SessionLock:
    """The async context manager behind `db_lock`. It hands out the pool's one connection as a `Session`, commits on a clean exit and rolls back when the block raised."""
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.lock = asyncio.Lock()
        self.connection: Optional[aiomysql.Connection] = None
        self._held_since = 0.0

    async def __aenter__(self) -> Session:
        asked = time.monotonic()
        await self.lock.acquire()
        self._held_since = time.monotonic()
        try:
            if self._held_since - asked > LOCK_SLOW_SECONDS:
                _warn("Waited %.1f s for the database lock", self._held_since - asked)
            if self.owner.pool is None:
                await self.owner.connect_pool()
            self.connection = await self.owner.pool.acquire()
            # Reopens the connection if the server closed it while it sat idle.
            await self.connection.ping(reconnect=True)
        except BaseException:
            # Also covers a cancelled task. Give the lock back or every later caller waits forever.
            await self._release()
            raise
        # owner.db exists only while the block is open, which is why queries must run inside `async with db_lock`.
        self.owner.db = Session(self.connection)
        return self.owner.db

    async def __aexit__(self, kind, error, trace) -> None:
        held = time.monotonic() - self._held_since
        try:
            if held > LOCK_SLOW_SECONDS:
                began = getattr(self.owner.db, "first_sql", None)
                _warn("The database lock was held for %.1f s by a block that began with: %s", held, began or "no statement")
            if self.connection is not None and not self.connection.closed:
                if kind is None:
                    await self.connection.commit()
                else:
                    await self.connection.rollback()
        finally:
            try:
                # Right after the commit or rollback and before the lock is given back, so nobody reads the old rows from memory any longer than that took. A block that wrote to a kept table makes the layer forget however it ended, because a commit inside the block may already have saved part of it.
                if getattr(self.owner.db, "touches_kept", False):
                    forget = getattr(self.owner, "forget_kept", None)
                    if forget is not None:
                        forget()
            finally:
                await self._release()

    async def _release(self) -> None:
        connection, self.connection = self.connection, None
        self.owner.db = None
        try:
            if connection is not None and self.owner.pool is not None:
                self.owner.pool.release(connection)
        finally:
            self.lock.release()


async def create_pool() -> aiomysql.Pool:
    if not (config.MYSQL_HOST and config.MYSQL_USER):
        raise RuntimeError("MySQL isn't set up. Add MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD and MYSQL_DB to .env.")
    return await aiomysql.create_pool(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        db=config.MYSQL_DB,
        charset="utf8mb4",
        autocommit=False,
        # Makes an UPDATE's rowcount count matched rows, so 0 means the row isn't there even when no value changed.
        client_flag=CLIENT.FOUND_ROWS,
        # A single connection: the session lock already runs database blocks one at a time.
        minsize=1,
        maxsize=1,
        pool_recycle=RECYCLE_SECONDS,
    )
