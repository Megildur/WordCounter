"""Finds, exports and deletes everything WordCounter stores about a Discord user or server, and holds the blacklist queries.

/owner delete_data, export_data and blacklist use it. It reads information_schema and searches every integer column whose name ends in `_id`, so any table with such a column is covered without changes here. That is why Discord ids must be stored in `..._id` integer columns. The id search and the deletion skip the blacklist table, and KEPT_POLICY is the text that says why.
"""

from __future__ import annotations
import datetime
import io
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple
from cogs.utils.mysql import TABLE_OPTIONS

BLACKLIST_TABLE = "blacklist"
META_TABLE = "db_meta"
BLACKLIST_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS blacklist (
        target_id BIGINT PRIMARY KEY, kind VARCHAR(16) NOT NULL, reason TEXT,
        added_by BIGINT NOT NULL, added_at DOUBLE NOT NULL)""" + TABLE_OPTIONS,
)
META_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS db_meta (
        name VARCHAR(64) PRIMARY KEY, value TEXT)""" + TABLE_OPTIONS,
)
INTEGER_TYPES = ("bigint", "int", "integer", "mediumint", "smallint", "tinyint")
UPLOAD_LIMIT = 8 * 1024 * 1024
# What a deletion keeps, as paragraphs. The privacy page shows them under "What we keep after a deletion request" and /owner kept_data quotes them, so the wording is changed here and nowhere else.
KEPT_POLICY = (
    "When you ask us to delete your data, we delete everything WordCounter stores under your Discord ID, with one exception: a ban from using WordCounter. If we've banned your account or your server, we keep its ID, whether it's a user or a server, the reason we noted, who added the ban and when. We keep it for safety and security, so a deletion request can't undo the ban or be used to get around it. It will not be deleted on request.",
    "How long it stays: until we lift the ban, when it's deleted.",
    "Under the GDPR (and the UK GDPR, which uses the same article numbers), we keep it because of our legitimate interest in keeping WordCounter and the servers that use it safe (Article 6(1)(f); Recital 49 recognises keeping a service secure against unlawful or malicious actions as a legitimate interest). The right to erasure doesn't reach it: it is still needed for the purpose it was collected for (Article 17(1)(a)), and keeping a ban in force is an overriding legitimate ground for keeping it (Articles 17(1)(c) and 21(1)). You can still ask for a copy of it (Article 15) and complain to your data protection authority (Article 77).",
)


@dataclass
class Table:
    """The rows of one table that mention an id. `matched` lists the id columns that held it."""
    name: str
    columns: List[str]
    rows: List[Tuple[Any, ...]]
    matched: List[str]


@dataclass
class Found:
    target_id: int
    tables: List[Table]

    @property
    def rows(self) -> int:
        return sum(len(table.rows) for table in self.tables)

    @property
    def empty(self) -> bool:
        return not self.tables


@dataclass(frozen=True)
class BlacklistEntry:
    """A row of the blacklist table. `kind` is "user" or "server"."""
    target_id: int
    kind: str
    reason: Optional[str]
    added_by: int
    added_at: float


def _plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


def summary(found: Found) -> str:
    if found.empty:
        return "nothing"
    return f"{_plural(found.rows, 'row')} in {_plural(len(found.tables), 'table')}"


def _show(value: Any) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    return str(value).replace("\n", "\n        ")


def report(found: Found, heading: str) -> str:
    """Renders what was found as plain text for the export file: the heading, a summary line, then each table's rows."""
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = [heading, f"Exported {now:%Y-%m-%d %H:%M} UTC: {summary(found)}.", ""]
    for table in found.tables:
        lines.append(f"== {table.name}: {_plural(len(table.rows), 'row')} ==")
        lines.append(f"Found in: {', '.join(table.matched)}.")
        for number, row in enumerate(table.rows, 1):
            lines.append(f"  Row {number}")
            lines.extend(f"    {name}: {_show(value)}" for name, value in zip(table.columns, row))
        lines.append("")
    return "\n".join(lines)


def pack(text: str, stem: str) -> Optional[Tuple[str, bytes]]:
    """Returns (file name, bytes) for an export: a .txt if it fits UPLOAD_LIMIT, otherwise a .zip, or None if even the zip is too big."""
    data = text.encode("utf-8")
    if len(data) <= UPLOAD_LIMIT:
        return f"{stem}.txt", data
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{stem}.txt", data)
    packed = buffer.getvalue()
    return (f"{stem}.zip", packed) if len(packed) <= UPLOAD_LIMIT else None


class StoredDataQueries:
    async def _id_columns(self) -> Dict[str, List[str]]:
        """Returns {table: its integer columns whose names end in _id} for the whole database. The caller must hold db_lock."""
        cursor = await self.db.execute(
            """SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME, ORDINAL_POSITION"""
        )
        found: Dict[str, List[str]] = {}
        for table, column, kind in await cursor.fetchall():
            # The blacklist is kept on purpose (see KEPT_POLICY), and db_meta is internal bookkeeping.
            if table in (BLACKLIST_TABLE, META_TABLE):
                continue
            if column.endswith("_id") and str(kind).lower() in INTEGER_TYPES:
                found.setdefault(table, []).append(column)
        return found

    async def find_by_id(self, target_id: int) -> Found:
        """Returns every row that holds `target_id` in any `_id` column of any table, for a user id or a server id."""
        await self.ensure_connected()
        tables: List[Table] = []
        async with self.db_lock:
            for name, columns in (await self._id_columns()).items():
                # Names come from information_schema, not from user input. A row matches when any of the table's id columns holds the id.
                where = " OR ".join(f"`{column}` = ?" for column in columns)
                cursor = await self.db.execute(f"SELECT * FROM `{name}` WHERE {where}", (target_id,) * len(columns))
                rows = [tuple(row) for row in await cursor.fetchall()]
                if rows:
                    names = [description[0] for description in cursor.description]
                    matched = [column for column in columns if any(row[names.index(column)] == target_id for row in rows)]
                    tables.append(Table(name, names, rows, matched))
        return Found(target_id, tables)

    async def delete_by_id(self, target_id: int) -> Dict[str, int]:
        """Deletes every row find_by_id would return and returns {table: rows deleted}. The blacklist table is not touched."""
        await self.ensure_connected()
        removed: Dict[str, int] = {}
        async with self.db_lock:
            for name, columns in (await self._id_columns()).items():
                # The same match as find_by_id, so a deletion removes exactly what an export lists.
                where = " OR ".join(f"`{column}` = ?" for column in columns)
                cursor = await self.db.execute(f"DELETE FROM `{name}` WHERE {where}", (target_id,) * len(columns))
                if cursor.rowcount and cursor.rowcount > 0:
                    removed[name] = cursor.rowcount
            await self.db.commit()
        # A website login whose rows were just deleted must stop working now, not when its look-up expires from memory.
        self.forget_web_sessions()
        return removed

    async def blacklisted_ids(self) -> Set[int]:
        """Returns every banned user and server id, which main.py loads into bot.blacklisted at startup."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT target_id FROM blacklist")
            return {row[0] for row in await cursor.fetchall()}

    async def blacklist_entry(self, target_id: int) -> Optional[BlacklistEntry]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT target_id, kind, reason, added_by, added_at FROM blacklist WHERE target_id = ?", (target_id,)
            )
            row = await cursor.fetchone()
        return BlacklistEntry(*row) if row else None

    async def blacklist_entries(self) -> List[BlacklistEntry]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT target_id, kind, reason, added_by, added_at FROM blacklist ORDER BY added_at DESC"
            )
            return [BlacklistEntry(*row) for row in await cursor.fetchall()]

    async def add_to_blacklist(self, target_id: int, kind: str, reason: Optional[str], added_by: int) -> None:
        # Banning an id that is already banned only changes the reason. Who added it and when stay as they were.
        await self._write([(
            """INSERT INTO blacklist (target_id, kind, reason, added_by, added_at) VALUES (?, ?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE reason = VALUES(reason)""",
            [(target_id, kind, reason, added_by, time.time())],
        )])

    async def remove_from_blacklist(self, target_id: int) -> bool:
        """Lifts a ban. Returns True if the id was on the list."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("DELETE FROM blacklist WHERE target_id = ?", (target_id,))
            await self.db.commit()
            return cursor.rowcount > 0
