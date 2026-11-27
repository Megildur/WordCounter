"""A thin async layer over aiomysql, so the rest of the code writes `?` placeholders and shares one connection.

`SessionLock` is what `WordCounterDatabase.db_lock` is. `async with db_lock` takes the pool's only connection and sets `owner.db` to a `Session` for the block. The connection autocommits: a block's first write (or locking read) begins a transaction in the same round trip, and that transaction is committed when the block ends and rolled back if it raised, so a block's writes still succeed or fail together while a block that only read has nothing to commit. Reads before a block's first write run outside its transaction, which is safe because this connection, behind this lock, is the only one that writes.

The database is about 90 ms away, so round trips are what a block costs: `Session.run_many` sends several statements as one (sql_batch.py).

`SessionLock` is an asyncio.Lock, so database blocks run one at a time and the pool needs only one connection. The lock is not re-entrant, so a block must never open another one.
"""

from __future__ import annotations
import asyncio
import logging
import re
import time
import warnings
from typing import Any, Iterable, List, Optional, Sequence, Set, Tuple
import aiomysql
import pymysql
from pymysql.constants import CLIENT
from cogs.utils import config, keep_warm, sql_batch

# aiomysql raises pymysql's errors, so this catches any MySQL failure.
DatabaseError = pymysql.err.MySQLError
# Added to every CREATE TABLE. utf8mb4_bin makes text comparisons case-sensitive.
TABLE_OPTIONS = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
# A connection idle longer than this is replaced when the pool hands it out. The host's MySQL drops one idle for 180 seconds (its wait_timeout), so this comes first.
RECYCLE_SECONDS = 170
# Retries pool creation on transient startup blips while bounding TCP handshake wait and failing fast on refused logins or missing databases.
DATABASE_TRIES = 6
DATABASE_RETRY_SECONDS = 2.0
DATABASE_RETRY_MAX_SECONDS = 30.0
DATABASE_CONNECT_SECONDS = 10
DATABASE_NOT_RETRIED = (1044, 1045, 1049, 1130)
# The connection is checked with a ping (one round trip to the server) only when it has sat unused for this long, or after an error that means the connection broke. The host's MySQL keeps an idle connection for 180 seconds, so one used less than this long ago is still open.
PING_IDLE_SECONDS = 150.0
# A database block that waited for the lock, or held it, longer than this is logged, because every other answer that needs the database waits behind it.
LOCK_SLOW_SECONDS = 1.0

log = logging.getLogger(__name__)

# The database layer keeps the rows of channels, ignore and keyword in memory, because every message reads them. A statement that changes one of those tables is spotted here, whoever wrote it, and the layer is told to forget what it kept when the block ends, however it ends. The table's name has to follow INTO, UPDATE, FROM, JOIN or TABLE, so keyword_channel, a column called keyword and INSERT IGNORE don't count.
_LEADING_COMMENTS = re.compile(r"^(?:\s|--[^\n]*\n|/\*.*?\*/)+", re.DOTALL)
_WRITES = re.compile(r"^\s*(?:INSERT|REPLACE|UPDATE|DELETE|TRUNCATE|ALTER|DROP)\b", re.IGNORECASE)
# A read that takes row locks, which only hold inside a transaction.
_LOCKING_READ = re.compile(r"\bFOR\s+UPDATE\b|\bLOCK\s+IN\s+SHARE\s+MODE\b", re.IGNORECASE)
_KEPT_TABLE = re.compile(r"\b(?:INTO|UPDATE|FROM|JOIN|TABLE)\s+`?(?:channels|ignore|keyword)`?(?!\w)", re.IGNORECASE)

# What a statement raises when the connection to the server is gone or was cut, as opposed to a mistake in the statement.
BROKEN_CONNECTION = (pymysql.err.OperationalError, pymysql.err.InterfaceError, ConnectionError, TimeoutError, EOFError)

warnings.filterwarnings("ignore", module="aiomysql")


def mysql_sql(sql: str) -> str:
    """Turns `?` placeholders into MySQL's `%s`, after doubling any `%` so the driver doesn't read it as a placeholder. A `?` inside a string literal would be rewritten too, so keep question marks out of SQL text."""
    return sql.replace("%", "%%").replace("?", "%s")


def _plain(value: Any) -> Any:
    """MySQL returns SUM() and some arithmetic as Decimal, so whole values become int and the rest float."""
    return sql_batch.plain(value)


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

    async def close(self) -> None:
        await self._cursor.close()

    async def __aenter__(self) -> Cursor:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()


class Session:
    def __init__(self, connection: aiomysql.Connection) -> None:
        self.connection = connection
        # The start of the first statement of the block, so a lock held for too long can say what held it. Never the values.
        self.first_sql: Optional[str] = None
        # True once the block has written to a table whose rows the database layer keeps in memory.
        self.touches_kept = False
        # When every kept-table write in the block sets `kept_scope = guild_id`, only those guilds are invalidated instead of every server.
        self.kept_scope: Optional[int] = None
        self._kept_guilds: Set[int] = set()
        self._kept_all = False
        # How many round trips went to the server, so a slow block's warning can tell many trips from one slow one.
        self.trips = 0
        # Open raw cursors from execute/executemany, closed when the session ends so unread results can't poison the next block.
        self._cursors: List[aiomysql.Cursor] = []

    def _note(self, sql: str) -> bool:
        """Keeps what the warnings and the kept tables need, and says whether this statement has to run inside the block's transaction (a write or a locking read)."""
        cleaned = _LEADING_COMMENTS.sub("", sql)
        if self.first_sql is None:
            self.first_sql = " ".join(cleaned.split())[:80]
        writes = bool(_WRITES.match(cleaned))
        if writes and _KEPT_TABLE.search(cleaned):
            self.touches_kept = True
            if self.kept_scope is not None:
                self._kept_guilds.add(self.kept_scope)
            else:
                self._kept_all = True
        return writes or bool(_LOCKING_READ.search(cleaned))

    def _beginning(self, needs_transaction: bool) -> bool:
        """Whether this round trip has to open the block's transaction first: it needs one and none is open yet."""
        return needs_transaction and not self.connection.get_transaction_status()

    async def _close_cursor(self, cursor: Any) -> None:
        close = getattr(cursor, "close", None)
        if callable(close):
            await close()

    def _close_connection(self) -> None:
        close = getattr(self.connection, "close", None)
        if callable(close) and not getattr(self.connection, "closed", False):
            close()

    async def _run_single(self, cursor: aiomysql.Cursor, sql: str, params: Tuple[Any, ...], begin: bool) -> None:
        try:
            if begin:
                # START TRANSACTION and the statement in one query; the cursor then moves on to the statement's own result.
                await cursor.execute("START TRANSACTION;\n" + cursor.mogrify(sql, params))
                await cursor.nextset()
            else:
                await cursor.execute(sql, params)
        except BaseException as error:
            if sql_batch.must_close_connection(cursor, error):
                sql_batch.close_connection(cursor)
            else:
                try:
                    await self._close_cursor(cursor)
                except BaseException:
                    sql_batch.close_connection(cursor)
            raise

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        begin = self._beginning(self._note(sql))
        self.trips += 1
        cursor = await self.connection.cursor()
        await self._run_single(cursor, mysql_sql(sql), tuple(params), begin)
        self._cursors.append(cursor)
        return Cursor(cursor)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Optional[Cursor]:
        """Runs one statement for many rows and returns None when there are no rows.

        An `INSERT ... VALUES (...)` becomes multi-row statements, as aiomysql's executemany makes it, and fills in only the VALUES list; a big one is cut into several so none passes the server's packet limit (sql_batch.MAX_BYTES). A `?` anywhere else, such as in `ON DUPLICATE KEY UPDATE`, gets no value, so bulk upserts write `VALUES(col)` there. Anything else is one statement a row. Several statements go through run_many, one round trip for each MAX_BYTES of them; the cursor returned is the last statement's.
        """
        rows = [tuple(row) for row in rows]
        if not rows:
            return None
        statements = sql_batch.many_rows(mysql_sql(sql), rows)
        begin = self._beginning(self._note(sql))
        if len(statements) == 1:
            self.trips += 1
            cursor = await self.connection.cursor()
            await self._run_single(cursor, statements[0][0], tuple(statements[0][1]), begin)
            self._cursors.append(cursor)
            return Cursor(cursor)
        batch = ([("START TRANSACTION", ())] if begin else []) + statements
        cursor = await self.connection.cursor()
        try:
            self.trips += (await sql_batch.run_many(cursor, batch)).queries
        except BaseException:
            if not getattr(self.connection, "closed", False):
                try:
                    await self._close_cursor(cursor)
                except BaseException:
                    sql_batch.close_connection(cursor)
            raise
        self._cursors.append(cursor)
        return Cursor(cursor)

    async def run_many(self, statements: Sequence[Tuple[str, Sequence[Any]]]) -> List[sql_batch.Outcome]:
        """Runs `statements` (with `?` placeholders) in one round trip (more only past sql_batch.MAX_BYTES), opening the block's transaction in it if one of them needs it, and returns each one's outcome with Decimal values normalized."""
        statements = [(sql, tuple(params)) for sql, params in statements]
        if not statements:
            return []
        needs = [self._note(sql) for sql, _ in statements]
        begin = self._beginning(any(needs))
        batch = ([("START TRANSACTION", ())] if begin else []) + [(mysql_sql(sql), params) for sql, params in statements]
        cursor = await self.connection.cursor()
        try:
            outcomes = await sql_batch.run_many(cursor, batch)
        finally:
            if not getattr(self.connection, "closed", False):
                try:
                    await self._close_cursor(cursor)
                except BaseException:
                    self._close_connection()
                    raise
        self.trips += outcomes.queries
        return outcomes[1:] if begin else outcomes

    async def close(self) -> None:
        """Closes every cursor opened during this block, discarding the connection if closing fails so unread results never leak."""
        cursors, self._cursors = self._cursors, []
        if not cursors or getattr(self.connection, "closed", False):
            return
        for cursor in cursors:
            try:
                await self._close_cursor(cursor)
            except BaseException:
                self._close_connection()
                raise

    async def commit(self) -> None:
        """Commits the block's transaction; with none open (nothing written yet) there is nothing to send."""
        if self.connection.get_transaction_status():
            self.trips += 1
            await self.connection.commit()


def _warn(text: str, *args: Any) -> None:
    """Logs a warning and swallows anything the logging itself raises: a log line must never be the reason the lock isn't given back or a block isn't committed."""
    try:
        log.warning(text, *args)
    except Exception:
        pass


class SessionLock:
    """The async context manager behind `db_lock`. It hands out the pool's one connection as a `Session`, and commits the block's transaction on a clean exit or rolls it back when the block raised (a block that wrote nothing has none)."""
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.lock = asyncio.Lock()
        self.connection: Optional[aiomysql.Connection] = None
        self._owner: Optional[asyncio.Task] = None
        self._held_since = 0.0
        # The connection the last block used, when that block ended, and whether it ended in an error that says the connection broke.
        # The pool the connection came from, so it goes back there even if `owner.pool` was set to None by `close` while the block ran. aiomysql's `wait_closed` waits for every connection to be given back.
        self._pool: Optional[aiomysql.Pool] = None
        self._last_connection: Optional[aiomysql.Connection] = None
        self._last_used = float("-inf")
        self._needs_ping = True
        self._ping_took = self._flush_took = 0.0
        self._flush_trips = 0

    async def __aenter__(self) -> Session:
        current = asyncio.current_task()
        if current is not None and self._owner is current:
            raise RuntimeError("db_lock is not re-entrant")
        asked = time.monotonic()
        await self.lock.acquire()
        self._owner = current
        self._held_since = time.monotonic()
        try:
            if self._held_since - asked > LOCK_SLOW_SECONDS:
                _warn("Waited %.1f s for the database lock", self._held_since - asked)
            if self.owner.pool is None:
                await self.owner.connect_pool()
            self._pool = self.owner.pool
            self.connection = await self._pool.acquire()
            # Where the time before the block went, for the warning about a lock held too long: a ping is one round trip to the server.
            self._ping_took = self._flush_took = 0.0
            self._flush_trips = 0
            # Reopens the connection if the server closed it while it sat idle. A connection used a moment ago isn't asked again.
            if self._needs_ping or self.connection is not self._last_connection or self._held_since - self._last_used > PING_IDLE_SECONDS:
                self._needs_ping = True
                started = time.monotonic()
                await self.connection.ping(reconnect=True)
                self._ping_took = time.monotonic() - started
                self._needs_ping = False
            # Counts that were waiting in memory go in first, in their own transaction (see activity_buffer.py).
            flush = getattr(self.owner, "flush_before_block", None)
            if flush is not None:
                flushing = Session(self.connection)
                started = time.monotonic()
                try:
                    await flush(flushing)
                finally:
                    try:
                        await flushing.close()
                    finally:
                        self._flush_took = time.monotonic() - started
                        self._flush_trips = flushing.trips
        except BaseException:
            # Also covers a cancelled task, and counts that couldn't be written. Give the lock back or every later caller waits forever.
            self._close_connection()
            self._needs_ping = True
            await self._release()
            raise
        # owner.db exists only while the block is open, which is why queries must run inside `async with db_lock`.
        self.owner.db = Session(self.connection)
        return self.owner.db

    def _close_connection(self) -> None:
        if self.connection is not None and not getattr(self.connection, "closed", False):
            close = getattr(self.connection, "close", None)
            if callable(close):
                close()

    async def __aexit__(self, kind, error, trace) -> None:
        held = time.monotonic() - self._held_since
        session = self.owner.db
        try:
            if held > LOCK_SLOW_SECONDS:
                began = getattr(session, "first_sql", None)
                _warn(
                    "The database lock was held for %.1f s by a block that began with: %s (ping %.2f s; waiting counts %.2f s in %d round trips; the block's own %d round trips)",
                    held, began or "no statement", self._ping_took, self._flush_took, self._flush_trips, getattr(session, "trips", 0),
                )
            if kind is not None and not issubclass(kind, Exception):
                # A cancelled block can leave an in-flight read on the socket; discard the connection so the pool opens a clean one.
                self._close_connection()
                self._needs_ping = True
            elif kind is not None and issubclass(kind, BROKEN_CONNECTION):
                # The next block checks the connection before trusting it.
                self._needs_ping = True
            if session is not None:
                await session.close()
            if self.connection is not None and getattr(self.connection, "closed", False):
                self._needs_ping = True
            # Only a block that opened a transaction has one to end; one that only read sends nothing here.
            elif self.connection is not None and self.connection.get_transaction_status():
                if kind is None:
                    await self.connection.commit()
                else:
                    await self.connection.rollback()
            self._last_connection, self._last_used = self.connection, time.monotonic()
        except BaseException:
            # A cursor close, commit or rollback that failed leaves the connection in an unknown state, so close it and force a fresh one.
            self._close_connection()
            self._needs_ping = True
            raise
        finally:
            try:
                # Right after the commit or rollback and before the lock is given back, so nobody reads the old rows from memory any longer than that took. A block that wrote to a kept table makes the layer forget however it ended, because a commit inside the block may already have saved part of it.
                if getattr(self.owner.db, "touches_kept", False):
                    forget = getattr(self.owner, "forget_kept", None)
                    if forget is not None:
                        kept_guilds = getattr(self.owner.db, "_kept_guilds", None)
                        if not getattr(self.owner.db, "_kept_all", True) and kept_guilds:
                            for guild_id in kept_guilds:
                                forget(guild_id)
                        else:
                            forget()
            finally:
                await self._release()

    async def ping_idle(self, idle: float) -> bool:
        """Pings the connection when nobody holds the lock and it has sat unused for `idle` seconds, and returns whether it answered (keep_warm.py calls it). It never waits for the lock: a block holding it is using the connection anyway. One that doesn't answer is closed, so the next block gets a fresh one."""
        pool = self.owner.pool
        if pool is None or self.lock.locked() or time.monotonic() - self._last_used < idle:
            return False
        async with self.lock:
            connection = await pool.acquire()
            try:
                await keep_warm.ping(connection)
                keep_warm.fresh(connection)
            except asyncio.CancelledError:
                # Cancelled mid-ping (the keeper stopped at shutdown): its answer may be unread, so the connection mustn't go back to the pool open.
                connection.close()
                self._needs_ping = True
                raise
            except Exception:
                connection.close()
                self._needs_ping = True
                return False
            finally:
                pool.release(connection)
            self._last_connection, self._last_used, self._needs_ping = connection, time.monotonic(), False
            return True

    async def _release(self) -> None:
        connection, self.connection = self.connection, None
        self.owner.db = None
        try:
            if connection is not None and self._pool is not None:
                self._pool.release(connection)
        finally:
            self._owner = None
            self.lock.release()


async def create_pool() -> aiomysql.Pool:
    """Opens the single-connection MySQL pool with a bounded connect timeout and exponential backoff on transient errors."""
    if not (config.MYSQL_HOST and config.MYSQL_USER):
        raise RuntimeError("MySQL isn't set up. Add MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD and MYSQL_DB to .env.")
    wait = DATABASE_RETRY_SECONDS
    for attempt in range(1, DATABASE_TRIES + 1):
        try:
            return await aiomysql.create_pool(
                host=config.MYSQL_HOST,
                port=config.MYSQL_PORT,
                user=config.MYSQL_USER,
                password=config.MYSQL_PASSWORD,
                db=config.MYSQL_DB,
                charset="utf8mb4",
                # Each statement commits by itself unless a block has begun a transaction (Session), so reads cost no COMMIT.
                autocommit=True,
                # Makes an UPDATE's rowcount count matched rows, so 0 means the row isn't there even when no value changed.
                # FOUND_ROWS for the rowcount above; MULTI_STATEMENTS so several statements cost one round trip (sql_batch.py).
                client_flag=CLIENT.FOUND_ROWS | CLIENT.MULTI_STATEMENTS,
                # A single connection: the session lock already runs database blocks one at a time.
                minsize=1,
                maxsize=1,
                connect_timeout=DATABASE_CONNECT_SECONDS,
                pool_recycle=RECYCLE_SECONDS,
            )
        except pymysql.err.OperationalError as error:
            if attempt == DATABASE_TRIES or (error.args and error.args[0] in DATABASE_NOT_RETRIED):
                raise
            log.warning(
                "Start-up: the database didn't connect (try %d of %d: %s); trying again in %.0f s",
                attempt,
                DATABASE_TRIES,
                error,
                wait,
            )
            await asyncio.sleep(wait)
            wait = min(wait * 2, DATABASE_RETRY_MAX_SECONDS)
