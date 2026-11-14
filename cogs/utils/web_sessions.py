"""The website's login sessions: one row for each signed-in browser, so a login can be ended from this side.

The cookie is signed and carries a random session id. Only a SHA-256 of that id is stored here, next to the Discord user ID, when the session began and when it ends, so a copy of the table can't be used to sign in. A cookie whose row is gone is refused, which is how signing out, "sign out everywhere", a deletion of someone's data and a ban end a login at once. The look-up is kept in memory for a little while, so the dashboard doesn't ask the database on every click.
"""

from __future__ import annotations
import logging
import time
from typing import Dict, Optional, Tuple
from cogs.utils.mysql import TABLE_OPTIONS

# user_id is an integer `_id` column, so /owner export_data and delete_data find these rows like any other.
WEB_SESSION_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS web_sessions (
        token_hash CHAR(64) PRIMARY KEY, user_id BIGINT NOT NULL, created BIGINT NOT NULL, expires BIGINT NOT NULL,
        KEY idx_web_sessions_user (user_id), KEY idx_web_sessions_expires (expires))""" + TABLE_OPTIONS,
)

log = logging.getLogger(__name__)

# Drops all but a person's newest sessions. MySQL won't delete from a table it is also reading in a subquery, so the ones to keep are listed in a derived table, and the LIMIT inside it keeps MySQL from folding it back into the DELETE.
KEEP_NEWEST = """DELETE FROM web_sessions WHERE user_id = ? AND token_hash NOT IN (
    SELECT token_hash FROM (SELECT token_hash FROM web_sessions WHERE user_id = ? ORDER BY created DESC, token_hash LIMIT ?) AS newest)"""

# How long a session found in the table is trusted without asking again, and how long one found missing is remembered. A session can't come back, so a missing one is remembered longer.
ALIVE_SECONDS = 30.0
GONE_SECONDS = 300.0
KEPT_LIMIT = 5000
# Someone who signs in on more browsers than this loses the oldest sessions.
SESSIONS_PER_USER = 10


class WebSessionQueries:
    """Mixed into WordCounterDatabase. Public methods take `db_lock` themselves. `_web_kept` and `_web_epoch` are set up in its __init__: {token hash: (when it was looked up, the user ID, or None when the session doesn't exist)}, and a number that goes up whenever something is forgotten."""

    async def create_web_session(self, token_hash: str, user_id: int, lifetime: int) -> None:
        """Records a new session that ends `lifetime` seconds from now, and clears out the sessions that have ended. Raises when the session couldn't be saved. Someone's oldest sessions beyond SESSIONS_PER_USER are then dropped, which is only tidying: if it fails the login still stands."""
        now = int(time.time())
        await self._write([
            ("DELETE FROM web_sessions WHERE expires < ?", [(now,)]),
            ("INSERT INTO web_sessions (token_hash, user_id, created, expires) VALUES (?, ?, ?, ?)", [(token_hash, user_id, now, now + lifetime)]),
        ])
        try:
            await self._write([(KEEP_NEWEST, [(user_id, user_id, SESSIONS_PER_USER)])])
        except Exception as error:
            log.warning("Couldn't drop a person's oldest login sessions: %s", error)
        # A session that was dropped by the last statement is no longer alive, so nothing in memory may still say it is.
        self.forget_web_sessions(user_id)

    async def web_session_user(self, token_hash: str) -> Optional[int]:
        """Returns the user ID a session belongs to, or None when it doesn't exist or has ended. Raises when the database can't be read, and the caller decides what that means."""
        now = time.monotonic()
        kept = self._web_kept.get(token_hash)
        if kept is not None and now - kept[0] < (ALIVE_SECONDS if kept[1] is not None else GONE_SECONDS):
            return kept[1]
        await self.ensure_connected()
        epoch = self._web_epoch
        async with self.db_lock:
            cursor = await self.db.execute("SELECT user_id FROM web_sessions WHERE token_hash = ? AND expires > ?", (token_hash, int(time.time())))
            row = await cursor.fetchone()
        owner = int(row[0]) if row else None
        # A look-up that began before a revoke or a deletion and ended after it may have read the row just before it went, so its answer is used once and not kept.
        if epoch == self._web_epoch:
            if len(self._web_kept) >= KEPT_LIMIT:
                self._web_kept.clear()
            self._web_kept[token_hash] = (time.monotonic(), owner)
        return owner

    async def revoke_web_session(self, token_hash: str) -> None:
        """Ends one session. Memory is told first, so it is never kept longer than the row."""
        self._web_epoch += 1
        self._web_kept.pop(token_hash, None)
        await self._write([("DELETE FROM web_sessions WHERE token_hash = ?", [(token_hash,)])])
        self._web_epoch += 1
        self._web_kept.pop(token_hash, None)

    async def revoke_web_sessions(self, user_id: int) -> None:
        """Ends every session of one person, on every browser."""
        self.forget_web_sessions(user_id)
        await self._write([("DELETE FROM web_sessions WHERE user_id = ?", [(user_id,)])])
        self.forget_web_sessions(user_id)

    def forget_web_sessions(self, user_id: Optional[int] = None) -> None:
        """Drops what is kept in memory about sessions, for one person or for everyone. The data deletion calls it, so a login doesn't outlive the rows `delete_by_id` removed."""
        self._web_epoch += 1
        if user_id is None:
            self._web_kept.clear()
            return
        for token_hash in [key for key, (_, owner) in self._web_kept.items() if owner == user_id]:
            del self._web_kept[token_hash]
