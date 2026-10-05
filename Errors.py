from __future__ import annotations
import io
import logging
import traceback
from typing import Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from discord.ui import ActionRow, Container, LayoutView, Modal, Separator, TextDisplay
from discord.ui.view import BaseView
from cogs.utils.components import ERROR_COLOR, NEUTRAL_COLOR, AuthorOnlyView, make_button
from cogs.utils.config import SUPPORT_URL

log = logging.getLogger("Errors")

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
    def __init__(self, title: str, description: str, traceback_str: str = "", author_id: Optional[int] = None) -> None:
        super().__init__(
            author_id=author_id,
            denied_message="Only the person who hit this error can see the traceback.",
            timeout=300,
        )
        self.traceback_str = traceback_str

        container = Container(accent_colour=ERROR_COLOR)
        container.add_item(TextDisplay(f"### {title}\n{description}"))
        container.add_item(Separator(spacing=discord.SeparatorSpacing.large))
        container.add_item(
            TextDisplay(
                "If this keeps happening, let the developer, Brandon, know in the "
                f"[support server]({SUPPORT_URL})."
            )
        )
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


def format_error_details(error: BaseException) -> Tuple[str, str, str]:
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
        else:
            description = f"Discord returned an error:\n> `{original.text or original}`"
        return "Discord API error", description, full_traceback

    details = str(original).strip() or "No details were given."
    return "Something went wrong", f"`{type(original).__name__}`: {details}", full_traceback


async def handle_interaction_error(interaction: discord.Interaction, error: BaseException, context_name: str = "Command") -> None:
    title, description, full_traceback = format_error_details(error)

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
        "Error in %s '%s' by %s (%s) in %s, %s:\n%s",
        context_name, command_name, interaction.user, interaction.user.id, guild_info, channel_info, full_traceback,
    )

    view = ErrorDisplayView(title, description, full_traceback, interaction.user.id)
    try:
        if interaction.response.is_done():
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
