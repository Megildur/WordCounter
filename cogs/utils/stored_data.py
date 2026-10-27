from __future__ import annotations
import datetime
import io
import time
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

BLACKLIST_TABLE = "blacklist"
BLACKLIST_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS blacklist (
        target_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, reason TEXT,
        added_by INTEGER NOT NULL, added_at REAL NOT NULL)""",
)
UPLOAD_LIMIT = 8 * 1024 * 1024


@dataclass
class Table:
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
        cursor = await self.db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        names = [row[0] for row in await cursor.fetchall() if row[0] != BLACKLIST_TABLE]
        found: Dict[str, List[str]] = {}
        for name in names:
            cursor = await self.db.execute(f'PRAGMA table_info("{name}")')
            columns = [row[1] for row in await cursor.fetchall() if row[1].endswith("_id") and "INT" in (row[2] or "").upper()]
            if columns:
                found[name] = columns
        return found

    async def find_by_id(self, target_id: int) -> Found:
        await self.ensure_connected()
        tables: List[Table] = []
        async with self.db_lock:
            for name, columns in (await self._id_columns()).items():
                where = " OR ".join(f'"{column}" = ?' for column in columns)
                cursor = await self.db.execute(f'SELECT * FROM "{name}" WHERE {where}', (target_id,) * len(columns))
                rows = [tuple(row) for row in await cursor.fetchall()]
                if rows:
                    names = [description[0] for description in cursor.description]
                    matched = [column for column in columns if any(row[names.index(column)] == target_id for row in rows)]
                    tables.append(Table(name, names, rows, matched))
        return Found(target_id, tables)

    async def delete_by_id(self, target_id: int) -> Dict[str, int]:
        await self.ensure_connected()
        removed: Dict[str, int] = {}
        async with self.db_lock:
            for name, columns in (await self._id_columns()).items():
                where = " OR ".join(f'"{column}" = ?' for column in columns)
                cursor = await self.db.execute(f'DELETE FROM "{name}" WHERE {where}', (target_id,) * len(columns))
                if cursor.rowcount and cursor.rowcount > 0:
                    removed[name] = cursor.rowcount
            await self.db.commit()
        return removed

    async def blacklisted_ids(self) -> Set[int]:
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
        await self._write([(
            """INSERT INTO blacklist (target_id, kind, reason, added_by, added_at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(target_id) DO UPDATE SET reason = excluded.reason""",
            [(target_id, kind, reason, added_by, time.time())],
        )])

    async def remove_from_blacklist(self, target_id: int) -> bool:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("DELETE FROM blacklist WHERE target_id = ?", (target_id,))
            await self.db.commit()
            return cursor.rowcount > 0
