"""Error handling for slash commands, context menus, buttons, selects and modals.

`ErrorHandler.cog_load` replaces the command tree's `on_error` and discord.py's view and modal `on_error` hooks, so every failure is logged with who ran what and where, under a short reference, and the person gets an `ErrorDisplayView` (not for an expired interaction). It is private, also after a public defer, where the "thinking" message is removed first, as long as Discord and the network allow checking it. Only an administrator of one of the owner's servers sees the exception text and a button for the full traceback, because those can name file paths, the database host and library versions. Everyone else gets a plain message and the reference.
"""

from __future__ import annotations
import io
import logging
import secrets
import traceback
from typing import Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Modal, Separator, TextDisplay
from discord.ui.view import BaseView
from cogs.utils.components import ERROR_COLOR, NEUTRAL_COLOR, AuthorOnlyView, make_button
from cogs.utils.config import ALLOWED_GUILD_IDS, SUPPORT_URL

log = logging.getLogger("Errors")

# Past this many characters the traceback is sent as a file, with only its end shown in the message, since a message holds 4000 characters of text.
INLINE_TRACEBACK_LIMIT = 3500


class TracebackView(LayoutView):
    def __init__(self, traceback_str: str, file: Optional[discord.File] = None) -> None:
        super().__init__(timeout=None)
        container = Container(accent_colour=NEUTRAL_COLOR)
        if file is None:
            container.add_item(TextDisplay(f"### Full traceback\n```py\n{traceback_str}\n```"))
        else:
            container.add_item(TextDisplay(f"### Full traceback\nThe end of it is below. The whole thing is attached.\n```py\n{traceback_str[-1800:]}\n```"))
            container.add_item(discord.ui.File(file))
        self.add_item(container)
        self.stop()


class ErrorDisplayView(AuthorOnlyView):
    """The error message shown to the user, with a link to the support server and, when there is a traceback, a button that sends it privately. Only the person who hit the error can press it."""
    def __init__(self, title: str, description: str, traceback_str: str = "", author_id: Optional[int] = None, reference: str = "") -> None:
        super().__init__(
            author_id=author_id,
            denied_message="Only the person who hit this error can see the traceback.",
            timeout=300,
        )
        self.traceback_str = traceback_str

        container = Container(accent_colour=ERROR_COLOR)
        container.add_item(TextDisplay(f"### {title}\n{description}"))
        container.add_item(Separator(spacing=discord.SeparatorSpacing.large))
        support = f"If this keeps happening, let the developer, Brandon, know in the [support server]({SUPPORT_URL})."
        container.add_item(TextDisplay(f"{support}\n-# Reference: {reference}" if reference else support))
        if traceback_str:
            container.add_item(Separator())
            container.add_item(ActionRow(make_button("Show traceback", callback=self._on_show_traceback)))
        self.add_item(container)

    async def _on_show_traceback(self, interaction: discord.Interaction) -> None:
        if len(self.traceback_str) <= INLINE_TRACEBACK_LIMIT:
            await interaction.response.send_message(view=TracebackView(self.traceback_str), ephemeral=True)
            return

        file = discord.File(io.BytesIO(self.traceback_str.encode("utf-8")), filename="traceback.txt")
        await interaction.response.send_message(view=TracebackView(self.traceback_str, file), file=file, ephemeral=True)


def can_see_details(interaction: discord.Interaction) -> bool:
    """Whether the person may see an error's exception text and traceback: an administrator in one of the servers the owner commands exist in."""
    return interaction.guild_id in ALLOWED_GUILD_IDS and interaction.permissions.administrator


def format_error_details(error: BaseException, detailed: bool = False) -> Tuple[str, str, str]:
    """Returns (title, description, full traceback) for an error.

    discord.py wraps an exception raised in a command in a CommandInvokeError, so the traceback and the Discord API checks use `.original`. Missing permissions, cooldowns, failed checks and Discord API errors get plain explanations. Anything else says that it was saved, and only with `detailed` shows its type and message, which can name file paths or the database host.
    """
    original = getattr(error, "original", error)
    full_traceback = "".join(traceback.format_exception(type(original), original, original.__traceback__)).strip()
    if not full_traceback:
        full_traceback = f"{type(original).__name__}: {original}"

    if isinstance(error, (app_commands.MissingPermissions, app_commands.BotMissingPermissions)):
        missing = ", ".join(perm.replace("_", " ").title() for perm in error.missing_permissions)
        if isinstance(error, app_commands.MissingPermissions):
            return "Missing permissions", f"You need these permissions to use this command: `{missing}`", full_traceback
        return "Bot is missing permissions", f"WordCounter needs these permissions here: `{missing}`", full_traceback
    if isinstance(error, app_commands.CommandOnCooldown):
        return "Slow down", f"Try again in {error.retry_after:.1f}s.", full_traceback
    if isinstance(error, app_commands.CheckFailure):
        return "Can't use that here", "This command isn't available here, or you don't meet its requirements.", full_traceback
    if isinstance(original, discord.HTTPException):
        if original.status == 403:
            description = "Discord blocked the request. The bot is probably missing a permission or is below a role it needs to manage."
        elif original.status == 404:
            description = "The channel, message or member it needed couldn't be found."
        elif original.status == 429:
            description = "Discord is rate limiting this action. Wait a moment and try again."
        elif original.status >= 500:
            description = "Discord is having trouble right now. Try again later."
        elif detailed:
            description = f"Discord returned an error:\n> `{original.text or original}`"
        else:
            description = f"Discord returned an error ({original.status}). Try again in a moment."
        return "Discord API error", description, full_traceback

    if not detailed:
        return "Something went wrong", "That couldn't be finished. The details were saved so the developer can look into it.", full_traceback
    details = str(original).strip() or "No details were given."
    return "Something went wrong", f"`{type(original).__name__}`: {details}", full_traceback


async def drop_thinking_message(interaction: discord.Interaction) -> None:
    """After a public defer the first follow-up replaces Discord's "thinking" message and can't be private, so an error that arrives while that message is still there removes it first, and its follow-up can be private. A reply that already holds real content is left alone, a message that is private already has nothing to hide, and so is anything that can't be checked. This runs while an error is being reported, so whatever it hits is swallowed."""
    try:
        original = await interaction.original_response()
        if original.flags.loading and not original.flags.ephemeral:
            await interaction.delete_original_response()
    except Exception:
        log.debug("Could not check whether the answer was still the thinking message", exc_info=True)


async def handle_interaction_error(interaction: discord.Interaction, error: BaseException, context_name: str = "Command") -> None:
    """Logs an interaction error with the command, user, server and channel, then shows the user an `ErrorDisplayView`. Slash commands, context menus, components and modals all end up here."""
    detailed = can_see_details(interaction)
    title, description, full_traceback = format_error_details(error, detailed)
    # Written in the log and shown to the person, so the developer can find the entry for a report.
    reference = secrets.token_hex(3)

    if interaction.command:
        command_name = interaction.command.name
    elif isinstance(interaction.data, dict) and interaction.data.get("name"):
        command_name = interaction.data["name"]
    else:
        command_name = context_name
    guild_info = f"'{interaction.guild.name}' ({interaction.guild_id})" if interaction.guild else "DMs"
    channel_name = getattr(interaction.channel, "name", None)
    channel_info = f"#{channel_name} ({interaction.channel_id})" if channel_name else f"channel {interaction.channel_id}"
    log.error(
        "Error [%s] in %s '%s' by %s (%s) in %s, %s:\n%s",
        reference, context_name, command_name, interaction.user, interaction.user.id, guild_info, channel_info, full_traceback,
    )

    original = getattr(error, "original", error)
    # Error 10062 is "Unknown interaction", usually because the 3 second window to answer had passed, so there is nothing to reply to. It is logged above and skipped.
    if isinstance(original, discord.NotFound) and original.code == 10062:
        return

    view = ErrorDisplayView(title, description, full_traceback if detailed else "", interaction.user.id, reference)
    try:
        # If the command already answered or deferred, the error goes out as a follow-up, privately even after a public defer.
        if interaction.response.is_done():
            await drop_thinking_message(interaction)
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
    except discord.HTTPException as send_error:
        log.warning("Could not show the error to the user: %s", send_error)


class ErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._old_tree_error = None
        self._old_view_error = None
        self._old_modal_error = None

    async def cog_load(self) -> None:
        self._old_tree_error = self.bot.tree.on_error
        self.bot.tree.on_error = self.tree_on_error

        # These hooks replace `on_error` on the BaseView and Modal classes themselves, so every view and modal reports through here unless it defines its own `on_error`. `cog_unload` puts the originals back.
        self._old_view_error = BaseView.on_error

        async def view_on_error(view: BaseView, interaction: discord.Interaction, error: Exception, item: discord.ui.Item) -> None:
            item_name = getattr(item, "label", None) or getattr(item, "custom_id", None) or type(item).__name__
            await handle_interaction_error(interaction, error, f"component '{item_name}' in {type(view).__name__}")

        BaseView.on_error = view_on_error

        self._old_modal_error = Modal.on_error

        async def modal_on_error(modal: Modal, interaction: discord.Interaction, error: Exception) -> None:
            await handle_interaction_error(interaction, error, f"modal '{getattr(modal, 'title', None) or type(modal).__name__}'")

        Modal.on_error = modal_on_error

    async def cog_unload(self) -> None:
        if self._old_tree_error:
            self.bot.tree.on_error = self._old_tree_error
        if self._old_view_error:
            BaseView.on_error = self._old_view_error
        if self._old_modal_error:
            Modal.on_error = self._old_modal_error

    async def tree_on_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        await handle_interaction_error(interaction, error, "slash or context command")

    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, (commands.CommandNotFound, commands.NotOwner)):
            return
        original = getattr(error, "original", error)
        log.error(
            "Error in prefix command '%s':\n%s",
            ctx.command,
            "".join(traceback.format_exception(type(original), original, original.__traceback__)).strip(),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorHandler(bot))
