"""Finds, exports and deletes everything WordCounter stores about a Discord user or server, and holds the blacklist queries.

/owner delete_data, export_data, kept_data and blacklist use it. It reads information_schema and searches every integer column whose name ends in `_id`, so any table with such a column is covered without changes here. `find_by_id` counts what it finds and reads the rows only for the export. That is why Discord ids must be stored in `..._id` integer columns. The one other place a user's id is stored is the JSON lists named in ID_LISTS (the members a saved `/analyze_chat` run has counted), which are searched as well. Text that people typed isn't searched. The id search and the deletion skip the blacklist table, and KEPT_POLICY is the text that says why.
"""

from __future__ import annotations
import asyncio
import datetime
import io
import json
import time
import zipfile
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class IdList:
    table: str
    # A text column that holds a JSON object, as json.dumps writes it.
    column: str
    # The columns that make a row unique, so one row can be updated alone.
    keys: Tuple[str, ...]
    # The names in that object whose value is a JSON list of user ids.
    lists: Tuple[str, ...]


# User ids kept inside JSON text, which the integer search can't see. The row belongs to a server, so deleting a user takes only their id out of these lists and keeps the row. `CrawlRun.state()` in cogs/analyze_chat.py writes this format and reads it back when a run resumes, so a change there must change it here too.
ID_LISTS: Tuple[IdList, ...] = (
    IdList("analysis_runs", "totals", ("guild_id",), ("credited", "swear_members")),
)


@dataclass
class Table:
    """The rows of one table that mention an id. `count` is how many there are and `matched` lists the id columns that held it. `listed` holds the ID_LISTS lists among them, written `column.list`: such a row belongs to a server and only lists the id. `columns` and `rows` stay empty unless `find_by_id` was asked for the rows."""
    name: str
    count: int
    matched: List[str]
    listed: List[str] = field(default_factory=list)
    columns: List[str] = field(default_factory=list)
    rows: List[Tuple[Any, ...]] = field(default_factory=list)


@dataclass
class Found:
    target_id: int
    tables: List[Table]
    # Whether the tables carry their rows, which only the export needs.
    has_rows: bool = False

    @property
    def rows(self) -> int:
        """How many rows the id is in, counted over every table."""
        return sum(table.count for table in self.tables)

    @property
    def empty(self) -> bool:
        return not self.tables


@dataclass
class Deleted:
    """What `delete_by_id` did, as {table: rows}: `removed` are rows deleted, `cleared` are rows that stay and lost only the id from a list in them (ID_LISTS)."""
    removed: Dict[str, int] = field(default_factory=dict)
    cleared: Dict[str, int] = field(default_factory=dict)


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
    return f"{_plural(found.rows, 'row')} in {_plural(len({table.name for table in found.tables}), 'table')}"


def _show(value: Any) -> str:
    if value is None:
        return "(empty)"
    if isinstance(value, (bytes, bytearray)):
        value = bytes(value).decode("utf-8", "replace")
    return str(value).replace("\n", "\n        ")


def report(found: Found, heading: str) -> str:
    """Renders what was found as plain text for the export file: the heading, a summary line, then each table's rows. `found` must come from `find_by_id(..., rows=True)`: a count-only result raises instead of exporting empty tables."""
    if not found.has_rows:
        raise ValueError("report() needs find_by_id(..., rows=True), which reads the rows")
    now = datetime.datetime.now(datetime.timezone.utc)
    lines = [heading, f"Exported {now:%Y-%m-%d %H:%M} UTC: {summary(found)}.", ""]
    for table in found.tables:
        lines.append(f"== {table.name}: {_plural(table.count, 'row')} ==")
        lines.append(f"Found in: {', '.join(table.matched)}.")
        if table.listed:
            lines.append("The row belongs to a server and only lists this ID among the members a saved analysis has counted: deleting takes the ID out of the list and keeps the row, and this export shows only those lists, with just this ID in each.")
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


def _text(value: Any) -> str:
    return bytes(value).decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else str(value)


def _object(raw: Any) -> Optional[Dict[str, Any]]:
    """The JSON object in a stored column, or None when there is none or it isn't an object, which `CrawlRun` couldn't read either."""
    if not raw:
        return None
    try:
        value = json.loads(_text(raw))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _holding(raw: Any, entry: IdList, target_id: int) -> List[str]:
    """The names of the entry's lists that have the id as one whole item, so 12 never matches 123."""
    data = _object(raw) or {}
    return [
        name for name in entry.lists
        if isinstance(data.get(name), list) and any(str(item) == str(target_id) for item in data[name])
    ]


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

    async def _listing_rows(self, entry: IdList, target_id: int, id_columns: List[str]) -> List[Tuple[Any, ...]]:
        """The rows of an ID_LISTS table that list the id, as the key columns followed by the JSON column. The LIKE only narrows the search, because it also finds an id that merely contains these digits, so every row is checked with `_holding`. A row the integer search already found (a server's own run) is left out, so it counts once. The caller must hold db_lock."""
        picks = ", ".join(f"`{column}`" for column in (*entry.keys, entry.column))
        # A NULL integer column would turn the whole condition into NULL and hide the row, hence the COALESCE. The percent signs are in the argument, because the session doubles any in the SQL text.
        found_already = " OR ".join(f"`{column}` = ?" for column in id_columns)
        skip = f" AND COALESCE(({found_already}), 0) = 0" if id_columns else ""
        cursor = await self.db.execute(
            f"SELECT {picks} FROM `{entry.table}` WHERE `{entry.column}` LIKE ?{skip}", (f"%{target_id}%", *(target_id,) * len(id_columns))
        )
        return [tuple(row) for row in await cursor.fetchall() if _holding(row[-1], entry, target_id)]

    async def find_by_id(self, target_id: int, *, rows: bool = False) -> Found:
        """Returns every table that holds `target_id` in any `_id` column, or in a list of ID_LISTS, for a user id or a server id, with how many rows it has there. The rows themselves are read only with `rows=True`, which the export needs and the confirm panels and /owner kept_data don't, so a big server costs no memory."""
        await self.ensure_connected()
        tables: List[Table] = []
        async with self.db_lock:
            id_columns = await self._id_columns()
            for name, columns in id_columns.items():
                # Names come from information_schema, not from user input. A row matches when any of the table's id columns holds the id.
                where = " OR ".join(f"`{column}` = ?" for column in columns)
                if rows:
                    cursor = await self.db.execute(f"SELECT * FROM `{name}` WHERE {where}", (target_id,) * len(columns))
                    found = [tuple(row) for row in await cursor.fetchall()]
                    if found:
                        names = [description[0] for description in cursor.description]
                        matched = [column for column in columns if any(row[names.index(column)] == target_id for row in found)]
                        tables.append(Table(name, len(found), matched, columns=names, rows=found))
                    continue
                # One query counts the rows and says which columns held the id: the COUNTs come first in the SELECT, so their placeholders take the first values.
                picks = "".join(f", COUNT(CASE WHEN `{column}` = ? THEN 1 END)" for column in columns)
                cursor = await self.db.execute(f"SELECT COUNT(*){picks} FROM `{name}` WHERE {where}", (target_id,) * (2 * len(columns)))
                total, *per_column = await cursor.fetchone()
                if total:
                    tables.append(Table(name, int(total), [column for column, n in zip(columns, per_column) if n]))
            for entry in ID_LISTS:
                if entry.table not in id_columns:
                    continue
                hits = await self._listing_rows(entry, target_id, id_columns[entry.table])
                if not hits:
                    continue
                matched = [f"{entry.column}.{name}" for name in entry.lists if any(name in _holding(row[-1], entry, target_id) for row in hits)]
                table = Table(entry.table, len(hits), matched, listed=matched)
                if rows:
                    # Only the lists, each with just this id: the other members are other people's data, and the counts in the same JSON belong to the server.
                    table.columns = [*entry.keys, entry.column]
                    table.rows = [
                        (*row[:-1], json.dumps({name: [target_id] if name in _holding(row[-1], entry, target_id) else [] for name in entry.lists}))
                        for row in hits
                    ]
                tables.append(table)
        return Found(target_id, tables, has_rows=rows)

    async def _take_out(self, entry: IdList, target_id: int, id_columns: List[str]) -> int:
        """Takes the id out of every list of an ID_LISTS table that holds it and keeps the rows, which belong to a server. Everything else in the JSON stays as it was. Returns how many rows changed. The write names the JSON as it was read, so a row changed in between isn't overwritten, which the held lock already prevents. The caller must hold db_lock."""
        changed = 0
        where = " AND ".join(f"`{key}` = ?" for key in entry.keys)
        for *keys, raw in await self._listing_rows(entry, target_id, id_columns):
            data = _object(raw)
            for name in _holding(raw, entry, target_id):
                data[name] = [item for item in data[name] if str(item) != str(target_id)]
            cursor = await self.db.execute(
                f"UPDATE `{entry.table}` SET `{entry.column}` = ? WHERE {where} AND `{entry.column}` = ?", (json.dumps(data), *keys, raw)
            )
            if cursor.rowcount and cursor.rowcount > 0:
                changed += 1
        return changed

    async def delete_by_id(self, target_id: int) -> Deleted:
        """Deletes every row find_by_id would return, except the rows of ID_LISTS, which stay and only lose the id from their lists. The blacklist table is not touched."""
        deleted = Deleted()
        try:
            await self.ensure_connected()
            async with self.db_lock:
                id_columns = await self._id_columns()
                for name, columns in id_columns.items():
                    # The same match as find_by_id, so a deletion removes exactly what an export lists.
                    where = " OR ".join(f"`{column}` = ?" for column in columns)
                    cursor = await self.db.execute(f"DELETE FROM `{name}` WHERE {where}", (target_id,) * len(columns))
                    if cursor.rowcount and cursor.rowcount > 0:
                        deleted.removed[name] = cursor.rowcount
                    await asyncio.sleep(0)
                # After the deletes, so a server's own run is gone and not rewritten. An interrupted run isn't running in memory for `AnalyzeChat.forget` to clean, and a resume reads its members back from this row.
                for entry in ID_LISTS:
                    if entry.table in id_columns:
                        changed = await self._take_out(entry, target_id, id_columns[entry.table])
                        if changed:
                            deleted.cleared[entry.table] = changed
                await self.db.commit()
        finally:
            forget_kept = getattr(self, "forget_kept", None)
            if forget_kept is not None:
                forget_kept()
            # A website login whose rows were just deleted must stop working now, not when its look-up expires from memory, and a deleted server leaves everyone's server list too. This runs even when the deletion fails part-way, because a ban has already been recorded by then and the logins and lists of a banned person must not outlive it.
            self.forget_web_sessions(target_id)
            self.drop_web_server(target_id)
        return deleted

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
        # Drop cached website sessions or server lists right away instead of waiting for ALIVE_SECONDS to pass.
        if kind == "user":
            self.forget_web_sessions(target_id)
        else:
            self.drop_web_server(target_id)

    async def remove_from_blacklist(self, target_id: int) -> bool:
        """Lifts a ban. Returns True if the id was on the list."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("DELETE FROM blacklist WHERE target_id = ?", (target_id,))
            await self.db.commit()
            return cursor.rowcount > 0
