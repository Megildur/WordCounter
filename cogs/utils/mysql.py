from __future__ import annotations
import asyncio
import decimal
from typing import Any, Iterable, List, Optional, Sequence, Tuple
import aiomysql
import pymysql
from pymysql.constants import CLIENT
from cogs.utils import config

DatabaseError = pymysql.err.MySQLError
TABLE_OPTIONS = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"
RECYCLE_SECONDS = 300


def mysql_sql(sql: str) -> str:
    return sql.replace("%", "%%").replace("?", "%s")


def _plain(value: Any) -> Any:
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

    async def execute(self, sql: str, params: Sequence[Any] = ()) -> Cursor:
        cursor = await self.connection.cursor()
        await cursor.execute(mysql_sql(sql), tuple(params))
        return Cursor(cursor)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> Optional[Cursor]:
        rows = [tuple(row) for row in rows]
        if not rows:
            return None
        cursor = await self.connection.cursor()
        await cursor.executemany(mysql_sql(sql), rows)
        return Cursor(cursor)

    async def commit(self) -> None:
        await self.connection.commit()


class SessionLock:
    def __init__(self, owner: Any) -> None:
        self.owner = owner
        self.lock = asyncio.Lock()
        self.connection: Optional[aiomysql.Connection] = None

    async def __aenter__(self) -> Session:
        await self.lock.acquire()
        try:
            if self.owner.pool is None:
                await self.owner.connect_pool()
            self.connection = await self.owner.pool.acquire()
            await self.connection.ping(reconnect=True)
        except BaseException:
            await self._release()
            raise
        self.owner.db = Session(self.connection)
        return self.owner.db

    async def __aexit__(self, kind, error, trace) -> None:
        try:
            if self.connection is not None and not self.connection.closed:
                if kind is None:
                    await self.connection.commit()
                else:
                    await self.connection.rollback()
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
        client_flag=CLIENT.FOUND_ROWS,
        minsize=1,
        maxsize=1,
        pool_recycle=RECYCLE_SECONDS,
    )
