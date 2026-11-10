from __future__ import annotations
import datetime
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
from cogs.utils.mysql import DatabaseError

log = logging.getLogger(__name__)

BATCH = 2000
TEXT_LIMITS = {"keyword": 255, "word": 100}


@dataclass
class ImportResult:
    copied: Dict[str, int] = field(default_factory=dict)
    skipped: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    mismatched: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and not self.mismatched

    def problem(self) -> str:
        lines = [f"Copying WordCounter's SQLite data into MySQL didn't finish: {len(self.failures):,} rows failed and "
                 f"{len(self.mismatched)} tables don't match ({', '.join(self.mismatched) or 'none'})."]
        lines += [f"  {line}" for line in self.failures[:20]]
        lines.append("Nothing is lost: restart the bot to try again. Rows already copied are replaced, never counted twice.")
        return "\n".join(lines)


def _sqlite_tables(source: sqlite3.Connection) -> Dict[str, List[str]]:
    names = [row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: [row[1] for row in source.execute(f'PRAGMA table_info("{name}")')] for name in names}


async def _mysql_tables(target: Any) -> Dict[str, List[str]]:
    async with target.db_lock:
        cursor = await target.db.execute(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        found: Dict[str, List[str]] = {}
        for table, column in await cursor.fetchall():
            found.setdefault(table, []).append(column)
        return found


async def _count(target: Any, table: str) -> int:
    async with target.db_lock:
        cursor = await target.db.execute(f"SELECT COUNT(*) FROM `{table}`")
        return (await cursor.fetchone())[0]


async def _copy_rows(target: Any, table: str, columns: Sequence[str], rows: List[Tuple[Any, ...]]) -> List[str]:
    names = ", ".join(f"`{column}`" for column in columns)
    sql = f"REPLACE INTO `{table}` ({names}) VALUES ({', '.join('?' * len(columns))})"
    try:
        async with target.db_lock:
            await target.db.executemany(sql, rows)
        return []
    except DatabaseError:
        failed = []
        for row in rows:
            try:
                async with target.db_lock:
                    await target.db.execute(sql, row)
            except DatabaseError as error:
                failed.append(f"{table} {dict(zip(columns, row))}: {error}")
        return failed


async def copy_sqlite(target: Any, path: Path) -> ImportResult:
    result = ImportResult()
    source = sqlite3.connect(str(path))
    try:
        wanted = await _mysql_tables(target)
        for table, sqlite_columns in _sqlite_tables(source).items():
            if table not in wanted:
                log.info("SQLite import: skipping %s, which WordCounter doesn't use any more", table)
                continue
            columns = [column for column in sqlite_columns if column in wanted[table]]
            dropped = [column for column in sqlite_columns if column not in wanted[table]]
            if dropped:
                log.warning("SQLite import: %s has columns MySQL doesn't, not copied: %s", table, ", ".join(dropped))
            total = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            limits = [(columns.index(column), limit) for column, limit in TEXT_LIMITS.items() if column in columns]
            selected = ", ".join(f'"{column}"' for column in columns)
            cursor = source.execute(f'SELECT {selected} FROM "{table}"')
            skipped = 0
            while True:
                rows = cursor.fetchmany(BATCH)
                if not rows:
                    break
                kept = []
                for row in rows:
                    long_text = [row[i] for i, limit in limits if isinstance(row[i], str) and len(row[i]) > limit]
                    if long_text:
                        skipped += 1
                        result.skipped.append(f"{table}: {long_text[0][:60]!r} ({len(long_text[0])} characters)")
                    else:
                        kept.append(tuple(row))
                result.failures += await _copy_rows(target, table, columns, kept)
            copied = await _count(target, table)
            result.copied[table] = copied
            if copied != total - skipped:
                result.mismatched.append(table)
            log.info("SQLite import: %s has %s rows in MySQL (%s in SQLite, %s skipped)", table, copied, total, skipped)
    finally:
        source.close()
    for line in result.skipped:
        log.warning("SQLite import: skipped %s, too long for MySQL", line)
    if result.ok:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await target.mark_sqlite_imported(f"{path.name} copied {stamp}, {sum(result.copied.values()):,} rows")
    return result
