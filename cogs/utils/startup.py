"""Answers commands and buttons with a private "still starting up" notice until the bot has loaded its servers and members.

Discord sends interactions as soon as the bot connects, before the caches are filled. `install` wraps discord.py's INTERACTION_CREATE parser so nothing reaches the commands until `bot.is_ready()`.
"""

from __future__ import annotations
import asyncio
import logging
from typing import Set
import discord
from cogs.utils.components import WARNING_COLOR, create_v2_view

log = logging.getLogger(__name__)

# asyncio only keeps weak references to tasks, so the answers are held here until they finish.
_TASKS: Set[asyncio.Task] = set()


def starting_up_view() -> discord.ui.LayoutView:
    return create_v2_view(
        "Still starting up",
        "WordCounter is still loading its servers and counts. Try again in a few seconds.",
        color=WARNING_COLOR,
    )


async def _answer(interaction: discord.Interaction) -> None:
    try:
        # Autocomplete can only be answered with choices, so it gets an empty list instead of a message.
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message(view=starting_up_view(), ephemeral=True)
    except discord.HTTPException as error:
        log.debug("Could not answer an interaction during startup: %s", error)


def install(bot: discord.Client) -> None:
    """Wraps the gateway's INTERACTION_CREATE parser, which is private discord.py state. Safe to call more than once."""
    state = bot._connection
    original = state.parsers["INTERACTION_CREATE"]
    if getattr(original, "startup_gate", False):
        return

    def parse(data: dict) -> None:
        if bot.is_ready():
            original(data)
            return
        try:
            interaction = discord.Interaction(data=data, state=state)
        except Exception as error:
            log.warning("Couldn't answer an interaction during startup: %s", error)
            return
        # The parser is a plain function, so the reply is sent from a task.
        task = asyncio.get_running_loop().create_task(_answer(interaction))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    parse.startup_gate = True
    # This wrapper is the outer one, so it carries the blacklist's marker for blacklist.install to see.
    parse.blacklist_gate = getattr(original, "blacklist_gate", False)
    state.parsers["INTERACTION_CREATE"] = parse
