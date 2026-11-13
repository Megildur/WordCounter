"""Answers a press on a menu that nobody is listening for, instead of leaving Discord's "This interaction failed" on screen.

A menu stops listening when its view times out, when the bot restarts, or when it is redrawn with new controls, but its message keeps its old buttons. discord.py drops a press on such a button without answering. `install` wraps its INTERACTION_CREATE parser, like `startup.py` and `blacklist.py`, to check before the press is handed on whether a view or a form is registered for it, and when none is, to answer privately that the menu has expired.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Any, Optional, Set
import discord
from cogs.utils.components import NEUTRAL_COLOR, create_v2_view

log = logging.getLogger(__name__)

# Interaction types: a press on a button or select, and a submitted form.
COMPONENT, MODAL_SUBMIT = 3, 5
# asyncio only keeps weak references to tasks, so the answers are held here until they finish.
_TASKS: Set[asyncio.Task] = set()


def expired_view() -> discord.ui.LayoutView:
    # A menu that was redrawn gives its controls new IDs, so a press that was already on its way lands here too, which is why the text doesn't only say it timed out.
    return create_v2_view("This menu has expired", "It timed out, or a newer version of it replaced it. Use the newest one, or run the command again to get a fresh one.", color=NEUTRAL_COLOR)


def listening(state: Any, data: dict) -> Optional[bool]:
    """Whether a view, a form or a button class is registered for the press in this raw INTERACTION_CREATE payload. None when that can't be told, which callers treat as "listening"."""
    try:
        store = state._view_store
        payload = data.get("data") or {}
        custom_id = str(payload.get("custom_id"))
        if data.get("type") == MODAL_SUBMIT:
            return custom_id in store._modals
        key = (int(payload.get("component_type", 0)), custom_id)
        message_id = int(data["message"]["id"]) if data.get("message") else None
        # A view sent without waiting for its message, such as followup.send without wait=True, is registered under no message, so the None bucket is checked too.
        if key in store._views.get(message_id, {}) or key in store._views.get(None, {}):
            return True
        return any(pattern.fullmatch(custom_id) for pattern in store._dynamic_items)
    except Exception:
        return None


def _check_works(state: Any) -> bool:
    """Registers a throwaway view and makes sure `listening` sees it and doesn't see another message's. A discord.py whose internals differ would otherwise answer "expired" to presses that work."""
    try:
        view = discord.ui.View(timeout=None)
        button = discord.ui.Button(custom_id="wordcounter-expired-check")
        view.add_item(button)
        state.store_view(view, 1)
        payload = {"type": COMPONENT, "data": {"custom_id": "wordcounter-expired-check", "component_type": 2}, "message": {"id": "1"}}
        found = listening(state, payload)
        elsewhere = listening(state, {**payload, "message": {"id": "2"}})
        view.stop()
        gone = listening(state, payload)
        return found is True and elsewhere is False and gone is False
    except Exception:
        return False


async def _answer(interaction: discord.Interaction) -> None:
    try:
        await interaction.response.send_message(view=expired_view(), ephemeral=True)
    except discord.HTTPException as error:
        log.debug("Could not tell someone their menu expired: %s", error)


def install(bot: discord.Client) -> None:
    """Wraps the gateway's INTERACTION_CREATE parser, which is private discord.py state. Safe to call more than once, even after the start-up and blacklist gates have wrapped it, and does nothing when the check in `_check_works` fails."""
    state = bot._connection
    # Kept on the state, not found on the parser, because the wrappers installed afterwards hide this one's marker.
    if getattr(state, "expired_installed", False):
        return
    original = state.parsers["INTERACTION_CREATE"]
    if not _check_works(state):
        log.warning("This discord.py keeps its views differently than expected, so presses on expired menus are left to Discord")
        return

    def parse(data: dict) -> None:
        if data.get("type") in (COMPONENT, MODAL_SUBMIT) and listening(state, data) is False:
            try:
                interaction = discord.Interaction(data=data, state=state)
            except Exception as error:
                log.warning("Couldn't answer a press on an expired menu: %s", error)
            else:
                # The parser is a plain function, so the answer goes out from a task. The press still goes on to discord.py, which drops it, so listeners of on_interaction see it as before.
                task = asyncio.get_running_loop().create_task(_answer(interaction))
                _TASKS.add(task)
                task.add_done_callback(_TASKS.discard)
        original(data)

    # Carries the markers of the wrappers underneath, so the ones that check for their own marker still find it.
    parse.blacklist_gate = getattr(original, "blacklist_gate", False)
    parse.startup_gate = getattr(original, "startup_gate", False)
    state.parsers["INTERACTION_CREATE"] = parse
    state.expired_installed = True
