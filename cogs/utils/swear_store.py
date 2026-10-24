from __future__ import annotations
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import discord

FINE_HISTORY_DAYS = 30
DAY_HISTORY_DAYS = 14

JAR_DEFAULTS: Dict[str, Any] = {
    "enabled": 0,
    "delete_mode": 0,
    "currency": "money",
    "unit_cents": 25,
    "token_name": "token",
    "token_plural": "tokens",
    "tokens_per_unit": 1,
    "tier1": 1,
    "tier2": 4,
    "tier3": 10,
    "skip_age_restricted": 1,
    "potty_role_id": None,
    "clean_role_id": None,
    "processed_day": 0,
    "rotated_week": 0,
    "lifetime_fines": 0,
}

JAR_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS jar_settings (
        guild_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, delete_mode INTEGER NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'money', unit_cents INTEGER NOT NULL DEFAULT 25,
        token_name TEXT NOT NULL DEFAULT 'token', token_plural TEXT NOT NULL DEFAULT 'tokens',
        tokens_per_unit INTEGER NOT NULL DEFAULT 1, tier1 INTEGER NOT NULL DEFAULT 1, tier2 INTEGER NOT NULL DEFAULT 4,
        tier3 INTEGER NOT NULL DEFAULT 10, skip_age_restricted INTEGER NOT NULL DEFAULT 1, potty_role_id INTEGER,
        clean_role_id INTEGER, processed_day INTEGER NOT NULL DEFAULT 0, rotated_week INTEGER NOT NULL DEFAULT 0,
        lifetime_fines INTEGER NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS jar_words (
        guild_id INTEGER NOT NULL, word TEXT NOT NULL, tier INTEGER NOT NULL, removed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, word))""",
    """CREATE TABLE IF NOT EXISTS jar_channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, rule TEXT NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS jar_members (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, balance INTEGER NOT NULL DEFAULT 0,
        streak INTEGER NOT NULL DEFAULT 0, best_streak INTEGER NOT NULL DEFAULT 0, fines INTEGER NOT NULL DEFAULT 0,
        spam_until REAL NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS jar_days (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, day INTEGER NOT NULL,
        messages INTEGER NOT NULL DEFAULT 0, fined INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, day))""",
    """CREATE TABLE IF NOT EXISTS jar_fines (
        message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL, swears INTEGER NOT NULL, amount INTEGER NOT NULL, fined_at REAL NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS jar_goals (
        guild_id INTEGER PRIMARY KEY, label TEXT NOT NULL, target INTEGER NOT NULL, channel_id INTEGER,
        ping INTEGER NOT NULL DEFAULT 0, progress INTEGER NOT NULL DEFAULT 0, started_at REAL NOT NULL,
        completed_at REAL)""",
    """CREATE TABLE IF NOT EXISTS jar_coverage (
        guild_id INTEGER NOT NULL, started INTEGER NOT NULL, ended INTEGER,
        PRIMARY KEY (guild_id, started))""",
    """CREATE TABLE IF NOT EXISTS swears_channels (
        guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS swears_users (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, count INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
    "CREATE INDEX IF NOT EXISTS idx_jar_fines_guild ON jar_fines (guild_id, fined_at)",
)

JAR_PLAYER_TABLES = ("jar_members", "jar_days", "jar_fines", "swears_channels", "swears_users")
JAR_TABLES = ("jar_settings", "jar_words", "jar_channels", "jar_goals", "jar_coverage", *JAR_PLAYER_TABLES)

SWEAR_COUNT_TABLES: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "swears_channels": (("guild_id", "channel_id", "user_id"), "count"),
    "swears_users": (("guild_id", "user_id"), "count"),
}

JAR_LEADERBOARDS = {
    "debt": "SELECT user_id, -balance FROM jar_members WHERE guild_id = ? AND balance < 0 ORDER BY balance ASC",
    "wallet": "SELECT user_id, balance FROM jar_members WHERE guild_id = ? AND balance > 0 ORDER BY balance DESC",
    "streak": "SELECT user_id, streak FROM jar_members WHERE guild_id = ? AND streak > 0 ORDER BY streak DESC",
}

SWEAR_CHANNEL_UPSERT = """INSERT INTO swears_channels (guild_id, channel_id, user_id, count) VALUES (?, ?, ?, ?)
    ON CONFLICT(guild_id, channel_id, user_id) DO UPDATE SET count = count + excluded.count"""
SWEAR_USER_UPSERT = """INSERT INTO swears_users (guild_id, user_id, count) VALUES (?, ?, ?)
    ON CONFLICT(guild_id, user_id) DO UPDATE SET count = count + excluded.count"""

SwearCounts = Mapping[int, Mapping[int, int]]


def now_snowflake() -> int:
    return discord.utils.time_snowflake(discord.utils.utcnow())


class SwearJarQueries:
    async def jar_settings(self, guild_id: int) -> Dict[str, Any]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(f"SELECT {', '.join(JAR_DEFAULTS)} FROM jar_settings WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
        return dict(zip(JAR_DEFAULTS, row)) if row else dict(JAR_DEFAULTS)

    async def jar_update(self, guild_id: int, **fields: Any) -> None:
        unknown = set(fields) - set(JAR_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown swear jar settings: {', '.join(sorted(unknown))}")
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        updates = ", ".join(f"{name} = excluded.{name}" for name in fields)
        await self._write([(
            f"INSERT INTO jar_settings (guild_id, {columns}) VALUES (?, {marks}) ON CONFLICT(guild_id) DO UPDATE SET {updates}",
            [(guild_id, *fields.values())],
        )])

    async def jar_set_enabled(self, guild_id: int, enabled: bool, today: int, week: int) -> None:
        stamp = now_snowflake()
        statements: List[Tuple[str, Sequence[Sequence[Any]]]] = []
        if enabled:
            statements += [
                (
                    """INSERT INTO jar_settings (guild_id, enabled, processed_day, rotated_week) VALUES (?, 1, ?, ?)
                    ON CONFLICT(guild_id) DO UPDATE SET enabled = 1, processed_day = excluded.processed_day,
                    rotated_week = MAX(rotated_week, excluded.rotated_week)""",
                    [(guild_id, today - 1, week)],
                ),
                (
                    """INSERT INTO jar_coverage (guild_id, started, ended) SELECT ?, ?, NULL
                    WHERE NOT EXISTS (SELECT 1 FROM jar_coverage WHERE guild_id = ? AND ended IS NULL)""",
                    [(guild_id, stamp, guild_id)],
                ),
            ]
        else:
            statements += [
                ("UPDATE jar_settings SET enabled = 0 WHERE guild_id = ?", [(guild_id,)]),
                ("UPDATE jar_coverage SET ended = ? WHERE guild_id = ? AND ended IS NULL", [(stamp, guild_id)]),
            ]
        await self._write(statements)

    async def jar_enabled_guilds(self) -> List[int]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT guild_id FROM jar_settings WHERE enabled = 1")
            return [row[0] for row in await cursor.fetchall()]

    async def jar_word_overrides(self, guild_id: int) -> List[Tuple[str, int, bool]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT word, tier, removed FROM jar_words WHERE guild_id = ? ORDER BY word", (guild_id,)
            )
            return [(word, tier, bool(removed)) for word, tier, removed in await cursor.fetchall()]

    async def jar_set_words(self, guild_id: int, words: Iterable[Tuple[str, int, bool]]) -> None:
        await self._write([(
            """INSERT INTO jar_words (guild_id, word, tier, removed) VALUES (?, ?, ?, ?)
            ON CONFLICT(guild_id, word) DO UPDATE SET tier = excluded.tier, removed = excluded.removed""",
            [(guild_id, word, tier, int(removed)) for word, tier, removed in words],
        )])

    async def jar_forget_words(self, guild_id: int, words: Iterable[str]) -> None:
        await self._write([("DELETE FROM jar_words WHERE guild_id = ? AND word = ?", [(guild_id, word) for word in words])])

    async def jar_reset_words(self, guild_id: int) -> None:
        await self._write([("DELETE FROM jar_words WHERE guild_id = ?", [(guild_id,)])])

    async def jar_channel_rules(self, guild_id: int) -> Dict[int, str]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT channel_id, rule FROM jar_channels WHERE guild_id = ?", (guild_id,))
            return {channel_id: rule for channel_id, rule in await cursor.fetchall()}

    async def jar_set_channel_rule(self, guild_id: int, channel_ids: Iterable[int], rule: Optional[str]) -> None:
        if rule is None:
            await self._write([("DELETE FROM jar_channels WHERE guild_id = ? AND channel_id = ?", [(guild_id, cid) for cid in channel_ids])])
            return
        await self._write([(
            """INSERT INTO jar_channels (guild_id, channel_id, rule) VALUES (?, ?, ?)
            ON CONFLICT(guild_id, channel_id) DO UPDATE SET rule = excluded.rule""",
            [(guild_id, cid, rule) for cid in channel_ids],
        )])

    async def jar_member(self, guild_id: int, user_id: int, week_start: float) -> Dict[str, Any]:
        await self.ensure_connected()
        params = (guild_id, user_id)
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT balance, streak, best_streak, fines, spam_until FROM jar_members WHERE guild_id = ? AND user_id = ?",
                params,
            )
            row = await cursor.fetchone() or (0, 0, 0, 0, 0.0)
            swears = await self._fetch_value("SELECT count FROM swears_users WHERE guild_id = ? AND user_id = ?", params)
            week = await self._fetch_value(
                "SELECT SUM(amount) FROM jar_fines WHERE guild_id = ? AND user_id = ? AND fined_at >= ?",
                (*params, week_start),
            )
        balance, streak, best_streak, fines, spam_until = row
        return {
            "balance": balance,
            "streak": streak,
            "best_streak": best_streak,
            "fines": fines,
            "spam_until": spam_until,
            "swears": swears,
            "week_fines": week,
        }

    async def jar_message_fine(self, message_id: int) -> Optional[Dict[str, Any]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT guild_id, user_id, channel_id, swears, amount, fined_at FROM jar_fines WHERE message_id = ?",
                (message_id,),
            )
            row = await cursor.fetchone()
        keys = ("guild_id", "user_id", "channel_id", "swears", "amount", "fined_at")
        return dict(zip(keys, row)) if row else None

    async def jar_record_fine(
        self,
        guild_id: int,
        user_id: int,
        channel_id: int,
        message_id: int,
        *,
        swears: int,
        amount: int,
        total_swears: int,
        total_amount: int,
        day: int,
        spam_until: float,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        now = time.time()
        member = (guild_id, user_id)
        await self.ensure_connected()
        async with self.db_lock:
            await self.db.execute(
                """INSERT INTO jar_fines (message_id, guild_id, user_id, channel_id, swears, amount, fined_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET swears = excluded.swears, amount = excluded.amount""",
                (message_id, guild_id, user_id, channel_id, total_swears, total_amount, now),
            )
            await self.db.execute(
                """INSERT INTO jar_members (guild_id, user_id, balance, streak, best_streak, fines, spam_until)
                VALUES (?, ?, ?, 0, 0, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = balance - ?, streak = 0,
                fines = fines + ?, spam_until = MAX(spam_until, excluded.spam_until)""",
                (*member, -amount, amount, spam_until, amount, amount),
            )
            await self.db.execute(
                """INSERT INTO jar_days (guild_id, user_id, day, messages, fined) VALUES (?, ?, ?, 0, 1)
                ON CONFLICT(guild_id, user_id, day) DO UPDATE SET fined = 1""",
                (*member, day),
            )
            if swears:
                await self.db.execute(SWEAR_CHANNEL_UPSERT, (guild_id, channel_id, user_id, swears))
                await self.db.execute(SWEAR_USER_UPSERT, (*member, swears))
            await self.db.execute(
                """INSERT INTO jar_settings (guild_id, lifetime_fines) VALUES (?, ?)
                ON CONFLICT(guild_id) DO UPDATE SET lifetime_fines = lifetime_fines + excluded.lifetime_fines""",
                (guild_id, amount),
            )
            cursor = await self.db.execute(
                """UPDATE jar_goals SET progress = progress + ?,
                completed_at = CASE WHEN progress + ? >= target THEN ? ELSE NULL END
                WHERE guild_id = ? AND completed_at IS NULL""",
                (amount, amount, now, guild_id),
            )
            reached = None
            if cursor.rowcount:
                reached = await self._goal_row(guild_id)
                if reached is None or reached["completed_at"] != now:
                    reached = None
            cursor = await self.db.execute(
                "SELECT balance, streak, best_streak, fines, spam_until FROM jar_members WHERE guild_id = ? AND user_id = ?",
                member,
            )
            row = await cursor.fetchone()
            await self.db.commit()
        return dict(zip(("balance", "streak", "best_streak", "fines", "spam_until"), row)), reached

    async def jar_add_day_messages(self, rows: Iterable[Tuple[int, int, int, int]]) -> None:
        await self._write([(
            """INSERT INTO jar_days (guild_id, user_id, day, messages, fined) VALUES (?, ?, ?, ?, 0)
            ON CONFLICT(guild_id, user_id, day) DO UPDATE SET messages = messages + excluded.messages""",
            list(rows),
        )])

    async def jar_credit_day(
        self, guild_id: int, day: int, active_messages: int, credit_for: Callable[[int], int]
    ) -> List[Tuple[int, int]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                """SELECT d.user_id, COALESCE(m.balance, 0), COALESCE(m.streak, 0), COALESCE(m.best_streak, 0)
                FROM jar_days d LEFT JOIN jar_members m ON m.guild_id = d.guild_id AND m.user_id = d.user_id
                WHERE d.guild_id = ? AND d.day = ? AND d.fined = 0 AND d.messages >= ?""",
                (guild_id, day, active_messages),
            )
            credited = []
            rows = []
            for user_id, balance, streak, best in await cursor.fetchall():
                streak += 1
                rows.append((guild_id, user_id, balance + credit_for(streak), streak, max(best, streak)))
                credited.append((user_id, streak))
            await self.db.executemany(
                """INSERT INTO jar_members (guild_id, user_id, balance, streak, best_streak) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(guild_id, user_id) DO UPDATE SET balance = excluded.balance, streak = excluded.streak,
                best_streak = excluded.best_streak""",
                rows,
            )
            await self.db.execute("UPDATE jar_settings SET processed_day = ? WHERE guild_id = ?", (day, guild_id))
            await self.db.execute("DELETE FROM jar_days WHERE guild_id = ? AND day < ?", (guild_id, day - DAY_HISTORY_DAYS))
            await self.db.commit()
        return credited

    async def jar_top_fined(self, guild_id: int, since: float, until: float) -> Optional[int]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                """SELECT user_id FROM jar_fines WHERE guild_id = ? AND fined_at >= ? AND fined_at < ?
                GROUP BY user_id ORDER BY SUM(amount) DESC, MAX(fined_at) DESC LIMIT 1""",
                (guild_id, since, until),
            )
            row = await cursor.fetchone()
        return row[0] if row else None

    async def jar_prune_fines(self) -> None:
        await self._write([("DELETE FROM jar_fines WHERE fined_at < ?", [(time.time() - FINE_HISTORY_DAYS * 86400,)])])

    async def jar_leaderboard(self, guild_id: int, kind: str) -> List[Tuple[int, int]]:
        if kind == "swears":
            return await self.get_leaderboard("swears", guild_id)
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(JAR_LEADERBOARDS[kind], (guild_id,))
            return await cursor.fetchall()

    async def _goal_row(self, guild_id: int) -> Optional[Dict[str, Any]]:
        cursor = await self.db.execute(
            "SELECT label, target, channel_id, ping, progress, started_at, completed_at FROM jar_goals WHERE guild_id = ?",
            (guild_id,),
        )
        row = await cursor.fetchone()
        keys = ("label", "target", "channel_id", "ping", "progress", "started_at", "completed_at")
        return dict(zip(keys, row)) if row else None

    async def jar_goal(self, guild_id: int) -> Optional[Dict[str, Any]]:
        await self.ensure_connected()
        async with self.db_lock:
            return await self._goal_row(guild_id)

    async def jar_set_goal(self, guild_id: int, label: str, target: int, channel_id: Optional[int], ping: bool) -> None:
        await self._write([(
            """INSERT INTO jar_goals (guild_id, label, target, channel_id, ping, progress, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, NULL)
            ON CONFLICT(guild_id) DO UPDATE SET label = excluded.label, target = excluded.target,
            channel_id = excluded.channel_id, ping = excluded.ping, progress = 0, started_at = excluded.started_at,
            completed_at = NULL""",
            [(guild_id, label, target, channel_id, int(ping), time.time())],
        )])

    async def jar_update_goal(self, guild_id: int, *, channel_id: Optional[int] = None, ping: Optional[bool] = None) -> None:
        statements = []
        if channel_id is not None:
            statements.append(("UPDATE jar_goals SET channel_id = ? WHERE guild_id = ?", [(channel_id, guild_id)]))
        if ping is not None:
            statements.append(("UPDATE jar_goals SET ping = ? WHERE guild_id = ?", [(int(ping), guild_id)]))
        if statements:
            await self._write(statements)

    async def jar_end_goal(self, guild_id: int) -> None:
        await self._write([("DELETE FROM jar_goals WHERE guild_id = ?", [(guild_id,)])])

    async def jar_reset_members(self, guild_id: int, user_id: Optional[int] = None) -> None:
        scope = "guild_id = ?" + (" AND user_id = ?" if user_id is not None else "")
        params = (guild_id,) + ((user_id,) if user_id is not None else ())
        statements: List[Tuple[str, Sequence[Sequence[Any]]]] = [
            (f"DELETE FROM {table} WHERE {scope}", [params]) for table in JAR_PLAYER_TABLES
        ]
        if user_id is None:
            statements += [
                ("UPDATE jar_settings SET lifetime_fines = 0 WHERE guild_id = ?", [(guild_id,)]),
                ("UPDATE jar_goals SET progress = 0, completed_at = NULL WHERE guild_id = ?", [(guild_id,)]),
            ]
        await self._write(statements)

    async def jar_coverage(self, guild_id: int) -> List[Tuple[int, Optional[int]]]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT started, ended FROM jar_coverage WHERE guild_id = ?", (guild_id,))
            return [(row[0], row[1]) for row in await cursor.fetchall()]

    async def _write_swear_counts(self, guild_id: int, counts: SwearCounts) -> None:
        channel_rows = [
            (guild_id, channel_id, user_id, count)
            for user_id, channels in counts.items()
            for channel_id, count in channels.items()
            if count > 0
        ]
        user_rows = [(guild_id, user_id, sum(channels.values())) for user_id, channels in counts.items() if sum(channels.values()) > 0]
        await self.db.executemany(SWEAR_CHANNEL_UPSERT, channel_rows)
        await self.db.executemany(SWEAR_USER_UPSERT, user_rows)

    async def _cover_swear_history(self, guild_id: int, before: int) -> None:
        await self.db.execute(
            """INSERT INTO jar_coverage (guild_id, started, ended) VALUES (?, 0, ?)
            ON CONFLICT(guild_id, started) DO UPDATE SET ended = MAX(ended, excluded.ended)""",
            (guild_id, before),
        )
