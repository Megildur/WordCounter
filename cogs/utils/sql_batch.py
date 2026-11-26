"""Several statements in one round trip to MySQL.

The database is far from the bot: each round trip takes about 90 ms, and a statement costs one whatever it does. So statements that don't wait on each other's answers go together. The connection allows multi-statement queries (CLIENT.MULTI_STATEMENTS, set in mysql.py), and `run_many` fills in each statement's values with the driver's own escaping (`mogrify`, as `execute` does for one statement), sends them joined as one query and reads each one's result in turn. Botzilla's Source/Utils/SqlBatch.py is the same.

If one of them fails, MySQL runs none after it and the error is raised here; the block it ran in rolls back as for any failed statement.

The server takes a query of at most its max_allowed_packet (16 MB on the host), so no query or statement made here is bigger than MAX_BYTES: `many_rows` cuts its rows into statements that size, and `run_many` sends its statements in as many round trips as that takes.
"""
import decimal
import re
from dataclasses import dataclass
from typing import Any, List, Sequence, Tuple

import pymysql

Statement = Tuple[str, Sequence[Any]]

# A quarter of the host's 16 MB max_allowed_packet, so a row's estimated size (its repr) can be far off and still fit.
MAX_BYTES = 4_000_000

# Errors that indicate the underlying MySQL socket/protocol state is no longer safe to reuse.
_BROKEN_MYSQL = (pymysql.err.OperationalError, pymysql.err.InterfaceError, pymysql.err.InternalError)
_COMMANDS_OUT_OF_SYNC = 2014


def plain(value: Any) -> Any:
    """MySQL returns SUM() and some arithmetic as Decimal, so whole values become int and the rest float."""
    if isinstance(value, decimal.Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _row(row: Sequence[Any]) -> tuple:
    return tuple(plain(value) for value in row)


def must_close_connection(cursor: Any, error: BaseException) -> bool:
    """True when an interrupted multi-statement query may have left unread packets or broken protocol state on the socket."""
    if not isinstance(error, Exception):
        return True
    if isinstance(error, _BROKEN_MYSQL):
        return True
    if isinstance(error, pymysql.err.ProgrammingError):
        return bool(error.args and error.args[0] == _COMMANDS_OUT_OF_SYNC)
    if isinstance(error, pymysql.err.MySQLError):
        # MySQL stops executing further statements in a multi-statement query after an ERR packet, so only close if unread results remain.
        return bool(getattr(getattr(cursor, "_result", None), "has_next", False))
    return True


def close_connection(cursor: Any) -> None:
    """Closes the cursor's underlying connection so the pool discards it instead of handing out a desynchronized socket."""
    conn = getattr(cursor, "connection", None) or getattr(cursor, "_connection", None)
    close = getattr(conn, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass

def _one_row(mark: str) -> "re.Pattern[str]":
    """An INSERT or REPLACE whose one VALUES group is all `mark` placeholders, with what comes after it (ON DUPLICATE KEY UPDATE): the shape aiomysql's executemany folds into one statement."""
    m = re.escape(mark)
    return re.compile(r"^(\s*(?:INSERT|REPLACE)\b.+?\bVALUES\s*)(\(\s*%s(?:\s*,\s*%s)*\s*\))(.*)$" % (m, m), re.IGNORECASE | re.DOTALL)


_ONE_ROW = {"%s": _one_row("%s"), "?": _one_row("?")}


class Outcomes(list):
    """The outcomes of run_many, in order, and how many queries (round trips) carried them."""

    queries = 0


@dataclass
class Outcome:
    """One statement's result: rows it changed or found, the rows of a SELECT, and the id an INSERT made."""

    rowcount: int
    rows: List[tuple]
    lastrowid: int


def many_rows(sql: str, rows: Sequence[Sequence[Any]], mark: str = "%s") -> List[Statement]:
    """The statements that write `rows` with `sql` (placeholders written as `mark`: MySQL's `%s`, or the `?` the rest of the code writes): multi-row statements of at most about MAX_BYTES each when `sql` is an INSERT of one VALUES group, as executemany sends it, or one statement a row otherwise."""
    rows = [tuple(row) for row in rows]
    match = _ONE_ROW[mark].match(sql)
    if not rows or match is None or mark in match.group(3):
        return [(sql, row) for row in rows]
    head, group, tail = match.groups()
    statements: List[Statement] = []
    chunk: List[tuple] = []
    size = 0
    for row in rows:
        cost = len(repr(row)) + len(group)
        if chunk and size + cost > MAX_BYTES:
            statements.append((head + ", ".join([group] * len(chunk)) + tail, tuple(value for one in chunk for value in one)))
            chunk, size = [], 0
        chunk.append(row)
        size += cost
    statements.append((head + ", ".join([group] * len(chunk)) + tail, tuple(value for one in chunk for value in one)))
    return statements


async def run_many(cursor: Any, statements: Sequence[Statement]) -> List[Outcome]:
    """Runs `statements` (in MySQL's `%s` form) in order on `cursor`, in one round trip unless together they pass MAX_BYTES, and returns each one's outcome."""
    if not statements:
        return []
    queries: List[List[str]] = [[]]
    size = 0
    for sql, args in statements:
        text = cursor.mogrify(sql, None if args is None else tuple(args))
        text_bytes = len(text.encode("utf-8"))
        if queries[-1] and size + text_bytes > MAX_BYTES:
            queries.append([])
            size = 0
        queries[-1].append(text)
        size += text_bytes + 2
    outcomes = Outcomes()
    outcomes.queries = len(queries)
    for query in queries:
        drained = False
        try:
            await cursor.execute(";\n".join(query))
            while True:
                rows = [_row(row) for row in await cursor.fetchall()] if cursor.description else []
                outcomes.append(Outcome(int(cursor.rowcount or 0), rows, int(cursor.lastrowid or 0)))
                if not await cursor.nextset():
                    break
            drained = True
        except BaseException as error:
            if not drained and must_close_connection(cursor, error):
                close_connection(cursor)
            raise
    if len(outcomes) != len(statements):
        raise RuntimeError(f"{len(statements)} statements gave {len(outcomes)} results")
    return outcomes
