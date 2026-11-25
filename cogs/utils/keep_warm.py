"""Keeps the database connection open while the bot is quiet.

The host's MySQL drops a connection that has been idle for 180 seconds (its wait_timeout), and the pool replaces one idle for RECYCLE_SECONDS (mysql.py) when it hands it out, so after a quiet few minutes the next message would wait for a new connection, about four round trips to a server about 90 ms away. So every EVERY_SECONDS the connection is sent a ping, one round trip, if it has sat unused for IDLE_SECONDS and nobody holds the lock (`SessionLock.ping_idle`). Botzilla's Source/Utils/KeepWarm.py does the same for its pool.
"""
import asyncio
import logging
from typing import Any

log = logging.getLogger(__name__)

# Checked every minute, a connection unused for 100 seconds is pinged by its 160th second at the latest, before the pool's 170.
EVERY_SECONDS = 60.0
IDLE_SECONDS = 100.0


async def keep_warm(lock: Any, every: float = EVERY_SECONDS, idle: float = IDLE_SECONDS) -> None:
    """Runs until cancelled, pinging the connection behind `lock` (a `SessionLock`) when it has been idle for `idle` seconds."""
    while True:
        await asyncio.sleep(every)
        try:
            await lock.ping_idle(idle)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.debug("Could not ping the idle database connection", exc_info=True)
