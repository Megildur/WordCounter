"""Where the live stats panel is posted, mixed into WordCounterDatabase.

One row per server: the channel and the message the bot keeps up to date, and when the row was made. Public methods take db_lock themselves, so don't call them from inside a lock block.
"""

from __future__ import annotations
from typing import List, Optional, Tuple
from cogs.utils.mysql import TABLE_OPTIONS

STATS_PANEL_SCHEMA = tuple(statement + TABLE_OPTIONS for statement in (
    """CREATE TABLE IF NOT EXISTS stats_panels (
        guild_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, message_id BIGINT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_stats_panels_channel (channel_id))""",
))

# A panel as (guild_id, channel_id, message_id).
Panel = Tuple[int, int, int]


class StatsPanelQueries:
    async def stats_panels(self) -> List[Panel]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT guild_id, channel_id, message_id FROM stats_panels ORDER BY guild_id", ())
            rows = await cursor.fetchall()
        return [(int(guild_id), int(channel_id), int(message_id)) for guild_id, channel_id, message_id in rows]

    async def stats_panel(self, guild_id: int) -> Optional[Panel]:
        await self.ensure_connected()
        async with self.db_lock:
            cursor = await self.db.execute("SELECT guild_id, channel_id, message_id FROM stats_panels WHERE guild_id = ?", (guild_id,))
            row = await cursor.fetchone()
        return (int(row[0]), int(row[1]), int(row[2])) if row else None

    async def save_stats_panel(self, guild_id: int, channel_id: int, message_id: int) -> None:
        # _write runs executemany, which only fills the VALUES list, so the update part reuses the inserted values with VALUES(column).
        await self._write([(
            "INSERT INTO stats_panels (guild_id, channel_id, message_id) VALUES (?, ?, ?) "
            "ON DUPLICATE KEY UPDATE channel_id = VALUES(channel_id), message_id = VALUES(message_id)",
            [(guild_id, channel_id, message_id)],
        )])

    async def set_stats_panel_message(self, guild_id: int, channel_id: int, message_id: int) -> None:
        """Remember a reposted panel, unless the panel was moved or taken down while it was being posted."""
        await self._write([("UPDATE stats_panels SET message_id = ? WHERE guild_id = ? AND channel_id = ?", [(message_id, guild_id, channel_id)])])

    async def delete_stats_panel(self, guild_id: int) -> None:
        await self._write([("DELETE FROM stats_panels WHERE guild_id = ?", [(guild_id,)])])

    async def delete_stats_panel_channel(self, channel_id: int) -> None:
        """A channel was deleted: its panel goes with it."""
        await self._write([("DELETE FROM stats_panels WHERE channel_id = ?", [(channel_id,)])])
