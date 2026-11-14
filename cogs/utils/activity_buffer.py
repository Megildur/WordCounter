"""Counts that wait in memory for a moment before they are written, so a busy server doesn't take the database's one connection for every message.

`WordCounterDatabase.queue_activity` adds each message's changes here, and they are written together in one transaction: every couple of seconds, before any other database block runs (so a read, a reset or a deletion always sees them applied first, in the order they happened), and when the bot shuts down. A crash or a kill can lose the last couple of seconds of counts.

The changes to one counter row are kept in the order they arrived. When they are all additions they are summed into one upsert, which is the same as applying them one by one. When any of them takes a count away (a deleted or shortened message), they are applied one after another as before, because a count never goes below zero and so the order matters.
"""

from __future__ import annotations
from typing import Any, Dict, List, Mapping, Optional, Tuple

# (table, the values of the table's key columns, in the order the table lists them). A change is an int, or four ints for the monthly table's words, messages, attachments and emojis.
Key = Tuple[str, Tuple[Any, ...]]
Taken = Dict[Key, List[Any]]


class ActivityBuffer:
    """The waiting changes. `key_names` says, for each table, the names of its key columns, which `forget` uses to find a person's or a server's rows."""

    def __init__(self, key_names: Mapping[str, Tuple[str, ...]]) -> None:
        self.key_names = key_names
        self.ops: Taken = {}
        self.count = 0

    def __bool__(self) -> bool:
        return bool(self.ops)

    def add(self, key: Key, change: Any) -> None:
        self.ops.setdefault(key, []).append(change)
        self.count += 1

    def take(self) -> Taken:
        """Hands over everything waiting and starts empty, so changes that arrive while it is being written wait for the next time."""
        taken, self.ops, self.count = self.ops, {}, 0
        return taken

    def restore(self, taken: Taken) -> None:
        """Puts back what `take` handed over when it couldn't be written, ahead of anything that arrived since, so each row's changes stay in order."""
        for key, changes in taken.items():
            self.ops[key] = changes + self.ops.get(key, [])
            self.count += len(changes)

    def forget(self, target_id: int) -> int:
        """Drops every waiting change that has `target_id` in any of its `_id` key columns, the same rows `delete_by_id` removes from the database. Returns how many changes were dropped."""
        dropped = 0
        for key in list(self.ops):
            table, values = key
            if any(name.endswith("_id") and value == target_id for name, value in zip(self.key_names[table], values)):
                dropped += len(self.ops.pop(key))
        self.count -= dropped
        return dropped
