"""The swear jar's tables and every query on them, mixed into WordCounterDatabase.

Tables: jar_settings (one row per server), jar_words (a server's changes to the default list), jar_channels (listen or skip rules), jar_members (balance and streak), jar_days (messages and fined flag per day), jar_fines (one row per fined message), jar_goals (at most one per server) and jar_coverage (which message ids the swear counts already include), plus the swears_users and swears_channels counts behind the Swears leaderboard. Amounts are whole units (see jar.money). Public methods take db_lock themselves. The underscore helpers at the bottom and _goal_row expect the caller to hold it, because the lock can't be taken twice.
"""

from __future__ import annotations
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import discord
from cogs.utils.mysql import TABLE_OPTIONS

# jar_prune_fines deletes a message's fine record after this many days. After that the message menu says the message wasn't fined.
FINE_HISTORY_DAYS = 30
# How many days back run_daily will still credit missed days, and how long jar_days rows are kept.
DAY_HISTORY_DAYS = 14

# Every jar_settings column except guild_id with its default, in the order jar_settings selects them. A server with no row gets these, so its jar is off.
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
    "english": 1,
    "polish": 0,
    "potty_role_id": None,
    "clean_role_id": None,
    "processed_day": 0,
    "rotated_week": 0,
    "lifetime_fines": 0,
}

# english and polish are also listed in ADDED_COLUMNS (database.py), which adds them to a jar_settings table made without them.
JAR_SCHEMA = tuple(statement + TABLE_OPTIONS for statement in (
    """CREATE TABLE IF NOT EXISTS jar_settings (
        guild_id BIGINT PRIMARY KEY, enabled INT NOT NULL DEFAULT 0, delete_mode INT NOT NULL DEFAULT 0,
        currency VARCHAR(16) NOT NULL DEFAULT 'money', unit_cents BIGINT NOT NULL DEFAULT 25,
        token_name VARCHAR(64) NOT NULL DEFAULT 'token', token_plural VARCHAR(64) NOT NULL DEFAULT 'tokens',
        tokens_per_unit BIGINT NOT NULL DEFAULT 1, tier1 INT NOT NULL DEFAULT 1, tier2 INT NOT NULL DEFAULT 4,
        tier3 INT NOT NULL DEFAULT 10, skip_age_restricted INT NOT NULL DEFAULT 1, potty_role_id BIGINT,
        clean_role_id BIGINT, processed_day BIGINT NOT NULL DEFAULT 0, rotated_week BIGINT NOT NULL DEFAULT 0,
        lifetime_fines BIGINT NOT NULL DEFAULT 0, english INT NOT NULL DEFAULT 1,
        polish INT NOT NULL DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS jar_words (
        guild_id BIGINT NOT NULL, word VARCHAR(100) NOT NULL, tier INT NOT NULL, removed INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, word))""",
    """CREATE TABLE IF NOT EXISTS jar_channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, rule VARCHAR(16) NOT NULL,
        PRIMARY KEY (guild_id, channel_id))""",
    """CREATE TABLE IF NOT EXISTS jar_members (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, balance BIGINT NOT NULL DEFAULT 0,
        streak BIGINT NOT NULL DEFAULT 0, best_streak BIGINT NOT NULL DEFAULT 0, fines BIGINT NOT NULL DEFAULT 0,
        spam_until DOUBLE NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS jar_days (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, day BIGINT NOT NULL,
        messages BIGINT NOT NULL DEFAULT 0, fined INT NOT NULL DEFAULT 0,
        PRIMARY KEY (guild_id, user_id, day))""",
    """CREATE TABLE IF NOT EXISTS jar_fines (
        message_id BIGINT PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
        channel_id BIGINT NOT NULL, swears BIGINT NOT NULL, amount BIGINT NOT NULL, fined_at DOUBLE NOT NULL,
        INDEX idx_jar_fines_guild (guild_id, fined_at))""",
    """CREATE TABLE IF NOT EXISTS jar_goals (
        guild_id BIGINT PRIMARY KEY, label VARCHAR(255) NOT NULL, target BIGINT NOT NULL, channel_id BIGINT,
        ping INT NOT NULL DEFAULT 0, progress BIGINT NOT NULL DEFAULT 0, started_at DOUBLE NOT NULL,
        completed_at DOUBLE)""",
    """CREATE TABLE IF NOT EXISTS jar_coverage (
        guild_id BIGINT NOT NULL, started BIGINT NOT NULL, ended BIGINT,
        PRIMARY KEY (guild_id, started))""",
    """CREATE TABLE IF NOT EXISTS swears_channels (
        guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, channel_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS swears_users (
        guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, count BIGINT NOT NULL,
        PRIMARY KEY (guild_id, user_id))""",
))

# The per-member tables, which jar_reset_members clears. The server's settings, words, rules, goal and coverage rows stay.
JAR_PLAYER_TABLES = ("jar_members", "jar_days", "jar_fines", "swears_channels", "swears_users")
# Every jar table, so that resetting a whole server (GUILD_TABLES in database.py) wipes the jar too.
JAR_TABLES = ("jar_settings", "jar_words", "jar_channels", "jar_goals", "jar_coverage", *JAR_PLAYER_TABLES)

# Same shape as COUNT_TABLES in database.py, which includes these so get_leaderboard can read the swear counts like the others.
SWEAR_COUNT_TABLES: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "swears_channels": (("guild_id", "channel_id", "user_id"), "count"),
    "swears_users": (("guild_id", "user_id"), "count"),
}

# Each query returns (user_id, value), biggest first. The swears board isn't here: jar_leaderboard reads it from the shared swears_users counts.
JAR_LEADERBOARDS = {
    "debt": "SELECT user_id, -balance FROM jar_members WHERE guild_id = ? AND balance < 0 ORDER BY balance ASC",
    "wallet": "SELECT user_id, balance FROM jar_members WHERE guild_id = ? AND balance > 0 ORDER BY balance DESC",
    "streak": "SELECT user_id, streak FROM jar_members WHERE guild_id = ? AND streak > 0 ORDER BY streak DESC",
}

# These add to the stored count and take no placeholders in the update part, so the same text works with execute and executemany.
SWEAR_CHANNEL_UPSERT = """INSERT INTO swears_channels (guild_id, channel_id, user_id, count) VALUES (?, ?, ?, ?)
    ON DUPLICATE KEY UPDATE count = count + VALUES(count)"""
SWEAR_USER_UPSERT = """INSERT INTO swears_users (guild_id, user_id, count) VALUES (?, ?, ?)
    ON DUPLICATE KEY UPDATE count = count + VALUES(count)"""

SwearCounts = Mapping[int, Mapping[int, int]]


def now_snowflake() -> int:
    """Returns the current time as a Discord snowflake, so it can be compared with message ids."""
    return discord.utils.time_snowflake(discord.utils.utcnow())


class SwearJarQueries:
    """The jar's queries, as a mixin for WordCounterDatabase, which supplies the connection, db_lock, _write and _fetch_value."""
    async def jar_settings(self, guild_id: int) -> Dict[str, Any]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(f"SELECT {', '.join(JAR_DEFAULTS)} FROM jar_settings WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
        return dict(zip(JAR_DEFAULTS, row)) if row else dict(JAR_DEFAULTS)

    async def jar_update(self, guild_id: int, **fields: Any) -> None:
        # The column names go into the SQL text, so only names from JAR_DEFAULTS are accepted.
        unknown = set(fields) - set(JAR_DEFAULTS)
        if unknown:
            raise ValueError(f"unknown swear jar settings: {', '.join(sorted(unknown))}")
        columns = ", ".join(fields)
        marks = ", ".join("?" * len(fields))
        # _write runs executemany, which only fills the VALUES list, so the update part reuses the inserted values with VALUES(column).
        updates = ", ".join(f"{name} = VALUES({name})" for name in fields)
        await self._write([(
            f"INSERT INTO jar_settings (guild_id, {columns}) VALUES (?, {marks}) ON DUPLICATE KEY UPDATE {updates}",
            [(guild_id, *fields.values())],
        )])

    async def jar_set_enabled(self, guild_id: int, enabled: bool, today: int, week: int) -> None:
        """Turns a server's jar on or off, and opens or closes its coverage period. `today` and `week` are only used when turning it on."""
        stamp = now_snowflake()
        statements: List[Tuple[str, Sequence[Sequence[Any]]]] = []
        if enabled:
            statements += [
                # processed_day starts at yesterday so earlier days are never credited, and rotated_week is raised to this week so the Potty Mouth role first moves next Monday.
                (
                    """INSERT INTO jar_settings (guild_id, enabled, processed_day, rotated_week) VALUES (?, 1, ?, ?)
                    ON DUPLICATE KEY UPDATE enabled = 1, processed_day = VALUES(processed_day),
                    rotated_week = GREATEST(rotated_week, VALUES(rotated_week))""",
                    [(guild_id, today - 1, week)],
                ),
                # Opens a coverage period that starts now (as a snowflake), unless one is already open.
                (
                    """INSERT INTO jar_coverage (guild_id, started, ended) SELECT ?, ?, NULL FROM DUAL
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
        """Returns a server's changes to the default word list as (key, tier, removed) rows."""
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(
                "SELECT word, tier, removed FROM jar_words WHERE guild_id = ? ORDER BY word", (guild_id,)
            )
            return [(word, tier, bool(removed)) for word, tier, removed in await cursor.fetchall()]

    async def jar_set_words(self, guild_id: int, words: Iterable[Tuple[str, int, bool]]) -> None:
        await self._write([(
            """INSERT INTO jar_words (guild_id, word, tier, removed) VALUES (?, ?, ?, ?)
            ON DUPLICATE KEY UPDATE tier = VALUES(tier), removed = VALUES(removed)""",
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
            ON DUPLICATE KEY UPDATE rule = VALUES(rule)""",
            [(guild_id, cid, rule) for cid in channel_ids],
        )])

    async def jar_member(self, guild_id: int, user_id: int, week_start: float) -> Dict[str, Any]:
        """Returns a member's jar as a dict, with zeros for anyone with no rows.

        `balance` is in units and negative is debt. `fines` is all the units they have been fined, `swears` their counted swears, and `spam_until` a timestamp kept for display. `week_fines` is the units fined since the timestamp `week_start`.
        """
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
        """Returns what a message was fined as a dict (swears, amount in units, who and when), or None if it wasn't fined or its record has been pruned."""
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
        """Saves a fine in one transaction. Returns (the member's row, the goal if this fine completed it).

        `swears` and `amount` are what this call adds. `total_swears` and `total_amount` are the message's totals after it, which jar_fines keeps so an edited message is only fined for the increase. The member's balance drops by `amount` and their streak goes back to zero.
        """
        now = time.time()
        # Everything below is one transaction and uses execute rather than _write, so the update parts can take placeholders of their own.
        member = (guild_id, user_id)
        await self.ensure_connected()
        async with self.db_lock:
            # On an edit only swears and amount change. fined_at keeps the time of the first fine, which the weekly totals use.
            await self.db.execute(
                """INSERT INTO jar_fines (message_id, guild_id, user_id, channel_id, swears, amount, fined_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE swears = VALUES(swears), amount = VALUES(amount)""",
                (message_id, guild_id, user_id, channel_id, total_swears, total_amount, now),
            )
            # A new member starts at minus the fine and an existing one loses it. Either way the streak goes to zero (best_streak stays) and spam_until only moves forward.
            await self.db.execute(
                """INSERT INTO jar_members (guild_id, user_id, balance, streak, best_streak, fines, spam_until)
                VALUES (?, ?, ?, 0, 0, ?, ?)
                ON DUPLICATE KEY UPDATE balance = balance - ?, streak = 0,
                fines = fines + ?, spam_until = GREATEST(spam_until, VALUES(spam_until))""",
                (*member, -amount, amount, spam_until, amount, amount),
            )
            # Marks the day as fined so it isn't paid back, and leaves its message count alone.
            await self.db.execute(
                """INSERT INTO jar_days (guild_id, user_id, day, messages, fined) VALUES (?, ?, ?, 0, 1)
                ON DUPLICATE KEY UPDATE fined = 1""",
                (*member, day),
            )
            # swears is 0 when an edit only raised the price, for example by swapping a mild word for a pricier one.
            if swears:
                await self.db.execute(SWEAR_CHANNEL_UPSERT, (guild_id, channel_id, user_id, swears))
                await self.db.execute(SWEAR_USER_UPSERT, (*member, swears))
            await self.db.execute(
                """INSERT INTO jar_settings (guild_id, lifetime_fines) VALUES (?, ?)
                ON DUPLICATE KEY UPDATE lifetime_fines = lifetime_fines + VALUES(lifetime_fines)""",
                (guild_id, amount),
            )
            # MySQL applies SET assignments from left to right, so completed_at has to come before progress to be compared with the old progress. Only an open goal is touched.
            cursor = await self.db.execute(
                """UPDATE jar_goals SET completed_at = CASE WHEN progress + ? >= target THEN ? ELSE NULL END,
                progress = progress + ?
                WHERE guild_id = ? AND completed_at IS NULL""",
                (amount, now, amount, guild_id),
            )
            reached = None
            # The connection uses the FOUND_ROWS flag, so rowcount counts matched rows and is non-zero whenever a goal was open.
            if cursor.rowcount:
                reached = await self._goal_row(guild_id)
                # The goal is returned only if this very fine completed it, so it is announced once.
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
        """Adds message counts to jar_days. `rows` are (guild_id, user_id, day, messages). A new row starts unfined, and an existing row keeps its fined flag."""
        await self._write([(
            """INSERT INTO jar_days (guild_id, user_id, day, messages, fined) VALUES (?, ?, ?, ?, 0)
            ON DUPLICATE KEY UPDATE messages = messages + VALUES(messages)""",
            # Not turned into a list here: `_write` reads it once it holds the database lock, so the caller can leave rows out up to that moment.
            rows,
        )])

    async def jar_credit_day(
        self, guild_id: int, day: int, active_messages: int, credit_for: Callable[[int], int]
    ) -> List[Tuple[int, int]]:
        """Pays one finished day to every member who was active and clean in it. Returns (user_id, new streak) for each.

        A member counts if they sent at least `active_messages` messages that day and weren't fined. Their streak goes up by one and `credit_for(streak)` units are added to their balance. Members who didn't qualify keep their streak, because only a fine resets it. The day is also saved as processed and old jar_days rows are deleted.
        """
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
            # The new totals are worked out above and saved as absolute values, because executemany can't take placeholders in the update part. db_lock is held throughout, so nothing changes the balances in between.
            await self.db.executemany(
                """INSERT INTO jar_members (guild_id, user_id, balance, streak, best_streak) VALUES (?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE balance = VALUES(balance), streak = VALUES(streak),
                best_streak = VALUES(best_streak)""",
                rows,
            )
            await self.db.execute("UPDATE jar_settings SET processed_day = ? WHERE guild_id = ?", (day, guild_id))
            await self.db.execute("DELETE FROM jar_days WHERE guild_id = ? AND day < ?", (guild_id, day - DAY_HISTORY_DAYS))
            await self.db.commit()
        return credited

    async def jar_top_fined(self, guild_id: int, since: float, until: float) -> Optional[int]:
        """Returns the user id fined the most units from timestamp `since` up to `until`, or None. A tie goes to whoever was fined last."""
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
        """Returns (user_id, value) rows, biggest first, for "swears", "debt", "wallet" or "streak". Debt is returned as a positive amount."""
        if kind == "swears":
            return await self.get_leaderboard("swears", guild_id)
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute(JAR_LEADERBOARDS[kind], (guild_id,))
            return await cursor.fetchall()

    async def _goal_row(self, guild_id: int) -> Optional[Dict[str, Any]]:
        """Reads a server's goal row. The caller must hold db_lock."""
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
        # A server has one goal row. Saving replaces it and starts again from zero progress, even when an admin only changes a goal that is running.
        await self._write([(
            """INSERT INTO jar_goals (guild_id, label, target, channel_id, ping, progress, started_at, completed_at)
            VALUES (?, ?, ?, ?, ?, 0, ?, NULL)
            ON DUPLICATE KEY UPDATE label = VALUES(label), target = VALUES(target),
            channel_id = VALUES(channel_id), ping = VALUES(ping), progress = 0, started_at = VALUES(started_at),
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
        """Deletes one member's jar data, or every member's when `user_id` is None.

        Resetting everyone also zeroes lifetime_fines and the goal's progress, which reopens a goal that was completed. Settings, words, rules and coverage stay, so past swears aren't counted again.
        """
        scope = "guild_id = ?" + (" AND user_id = ?" if user_id is not None else "")
        params = (guild_id,) + ((user_id,) if user_id is not None else ())
        statements: List[Tuple[str, Sequence[Sequence[Any]]]] = [
            (f"DELETE FROM `{table}` WHERE {scope}", [params]) for table in JAR_PLAYER_TABLES
        ]
        if user_id is None:
            statements += [
                ("UPDATE jar_settings SET lifetime_fines = 0 WHERE guild_id = ?", [(guild_id,)]),
                ("UPDATE jar_goals SET progress = 0, completed_at = NULL WHERE guild_id = ?", [(guild_id,)]),
            ]
        await self._write(statements)

    async def jar_coverage(self, guild_id: int) -> List[Tuple[int, Optional[int]]]:
        """Returns the server's coverage periods as (started, ended) snowflakes that compare with message ids, ended being None while a period is open.

        They mark the messages the swear counts already include. A period is open while the jar is on, every earlier on and off stretch leaves a closed one, and a period starting at 0 covers history added by /analyze_chat. The analysis skips messages inside any of them.
        """
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT started, ended FROM jar_coverage WHERE guild_id = ?", (guild_id,))
            return [(row[0], row[1]) for row in await cursor.fetchall()]

    async def _write_swear_counts(self, guild_id: int, counts: SwearCounts) -> None:
        """Adds swears found by /analyze_chat to the stored counts. `counts` is {user_id: {channel_id: swears}}. The caller must hold db_lock."""
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
        """Records that swears in messages older than the snowflake `before` are counted, by extending the period that starts at 0. The caller must hold db_lock."""
        await self.db.execute(
            """INSERT INTO jar_coverage (guild_id, started, ended) VALUES (?, 0, ?)
            ON DUPLICATE KEY UPDATE ended = GREATEST(ended, VALUES(ended))""",
            (guild_id, before),
        )
