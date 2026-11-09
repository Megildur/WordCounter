from __future__ import annotations
import argparse
import asyncio
import datetime
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from cogs.utils import config
from cogs.utils.database import WordCounterDatabase
from cogs.utils.mysql import DatabaseError

BATCH = 2000
TEXT_LIMITS = {"keyword": 255, "word": 100}


def sqlite_tables(source: sqlite3.Connection) -> Dict[str, List[str]]:
    names = [row[0] for row in source.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )]
    return {name: [row[1] for row in source.execute(f'PRAGMA table_info("{name}")')] for name in names}


async def mysql_tables(target: WordCounterDatabase) -> Dict[str, List[str]]:
    async with target.db_lock:
        cursor = await target.db.execute(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() "
            "ORDER BY TABLE_NAME, ORDINAL_POSITION"
        )
        found: Dict[str, List[str]] = {}
        for table, column in await cursor.fetchall():
            found.setdefault(table, []).append(column)
        return found


async def count(target: WordCounterDatabase, table: str) -> int:
    async with target.db_lock:
        cursor = await target.db.execute(f"SELECT COUNT(*) FROM `{table}`")
        return (await cursor.fetchone())[0]


async def copy_rows(target: WordCounterDatabase, table: str, columns: Sequence[str], rows: List[Tuple[Any, ...]]) -> List[str]:
    names = ", ".join(f"`{column}`" for column in columns)
    marks = ", ".join("?" * len(columns))
    sql = f"REPLACE INTO `{table}` ({names}) VALUES ({marks})"
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


async def migrate(source_path: Path, dry_run: bool) -> int:
    if not source_path.is_file():
        print(f"There's no SQLite database at {source_path}.")
        return 1
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    target = WordCounterDatabase(None)
    await target.connect_pool()
    await target.create_tables()
    if await target.sqlite_imported() and not dry_run:
        print("This MySQL database already has WordCounter's SQLite data. Nothing to do.")
        print("To copy it again anyway, delete the sqlite_import row from db_meta first.")
        return 0
    wanted = await mysql_tables(target)
    have = sqlite_tables(source)
    print(f"Copying {source_path} into MySQL database {config.MYSQL_DB} on {config.MYSQL_HOST}:{config.MYSQL_PORT}.")
    failures: List[str] = []
    mismatched: List[str] = []
    for table, sqlite_columns in have.items():
        if table not in wanted:
            print(f"  {table}: skipped, WordCounter doesn't use this table any more")
            continue
        columns = [column for column in sqlite_columns if column in wanted[table]]
        total = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        if dry_run:
            print(f"  {table}: {total:,} rows would be copied ({len(columns)} columns)")
            continue
        selected = ", ".join(f'"{column}"' for column in columns)
        cursor = source.execute(f'SELECT {selected} FROM "{table}"')
        limits = [(columns.index(column), limit) for column, limit in TEXT_LIMITS.items() if column in columns]
        skipped = 0
        while True:
            rows = cursor.fetchmany(BATCH)
            if not rows:
                break
            kept = []
            for row in rows:
                too_long = [row[index] for index, limit in limits if isinstance(row[index], str) and len(row[index]) > limit]
                if too_long:
                    skipped += 1
                    print(f"  {table}: skipped {too_long[0][:40]!r}..., too long for MySQL ({len(too_long[0])} characters)")
                else:
                    kept.append(tuple(row))
            failures += await copy_rows(target, table, columns, kept)
        copied = await count(target, table)
        expected = total - skipped
        note = "" if copied == expected else f" (expected {expected:,} from SQLite)"
        if copied != expected:
            mismatched.append(table)
        print(f"  {table}: {copied:,} rows in MySQL{note}" + (f", {skipped:,} skipped" if skipped else ""))
    if dry_run:
        print("Dry run: nothing was written.")
        await target.close()
        return 0
    for line in failures[:50]:
        print(f"  could not copy {line}")
    if failures or mismatched:
        print(f"{len(failures):,} rows failed and {len(mismatched)} tables don't match. Fix them and run this again; "
              "rows already copied are replaced, never counted twice.")
        await target.close()
        return 1
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await target.mark_sqlite_imported(f"{source_path.name} copied {stamp}")
    await target.close()
    print("Done. Every table matches. Start WordCounter and it runs on MySQL.")
    print(f"Keep {source_path} as a backup until you've checked everything, then move it out of Data/.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Copy WordCounter's SQLite database into MySQL, once. Stop the bot first, so nothing changes mid-copy."
    )
    parser.add_argument("--sqlite", default=config.SQLITE_PATH, help=f"the SQLite file (default {config.SQLITE_PATH})")
    parser.add_argument("--dry-run", action="store_true", help="only show what would be copied")
    args = parser.parse_args()
    return asyncio.run(migrate(Path(args.sqlite), args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
