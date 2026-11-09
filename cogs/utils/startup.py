from __future__ import annotations
import asyncio
import logging
from typing import Set
import discord
from cogs.utils.components import WARNING_COLOR, create_v2_view

log = logging.getLogger(__name__)

_TASKS: Set[asyncio.Task] = set()


def starting_up_view() -> discord.ui.LayoutView:
    return create_v2_view(
        "Still starting up",
        "WordCounter is still loading its servers and counts. Try again in a few seconds.",
        color=WARNING_COLOR,
    )


async def _answer(interaction: discord.Interaction) -> None:
    try:
        if interaction.type is discord.InteractionType.autocomplete:
            await interaction.response.autocomplete([])
        else:
            await interaction.response.send_message(view=starting_up_view(), ephemeral=True)
    except discord.HTTPException as error:
        log.debug("Could not answer an interaction during startup: %s", error)


def install(bot: discord.Client) -> None:
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
        task = asyncio.get_running_loop().create_task(_answer(interaction))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)

    parse.startup_gate = True
    parse.blacklist_gate = getattr(original, "blacklist_gate", False)
    state.parsers["INTERACTION_CREATE"] = parse
