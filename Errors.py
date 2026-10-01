import os
import io
import sys
import logging
import traceback
import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import LayoutView, Container, Button, TextDisplay, ActionRow, Modal
from discord.ui.view import BaseView
from dotenv import load_dotenv

log = logging.getLogger("Errors")

load_dotenv()

bot_server = os.getenv("BOT_SERVER", "").strip() or "https://discord.gg/prUsgFHvRS"


class ErrorDisplayView(LayoutView):
    def __init__(
        self,
        title: str,
        description: str,
        traceback_str: str = "",
        author_id: int | None = None,
    ):
        super().__init__(timeout=300)
        self.traceback_str = traceback_str
        self.author_id = author_id

        container = Container(accent_colour=discord.Colour.red())
        container.add_item(TextDisplay(f"# **{title}**"))
        container.add_item(discord.ui.Separator())
        container.add_item(TextDisplay(description))
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        support_text = "If this keeps happening, please contact the developer, Brandon, for help."
        if bot_server:
            support_text += f"\n\n> Please join the support server if you haven't!\n 🔗 **[Support Server Invite]({bot_server})**"
        container.add_item(TextDisplay(support_text))

        if self.traceback_str:
            btn = Button(
                label="Show Full Traceback",
                emoji="📜",
                style=discord.ButtonStyle.secondary,
            )
            btn.callback = self._on_show_traceback
            container.add_item(discord.ui.Separator())
            container.add_item(ActionRow(btn))

        self.add_item(container)

    async def _on_show_traceback(self, interaction: discord.Interaction) -> None:
        if self.author_id and interaction.user and interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the user who encountered this error can view the traceback.",
                ephemeral=True,
            )
            return

        if not self.traceback_str:
            await interaction.response.send_message(
                "No traceback details available for this error.",
                ephemeral=True,
            )
            return

        # Discord messages have a 2000 character limit
        if len(self.traceback_str) <= 1900:
            await interaction.response.send_message(
                f"### 📋 Full Traceback\n```py\n{self.traceback_str}\n```",
                ephemeral=True,
            )
        else:
            file = discord.File(
                io.BytesIO(self.traceback_str.encode("utf-8")),
                filename="traceback.txt",
            )
            await interaction.response.send_message(
                f"### 📋 Full Traceback (Truncated below, full file attached)\n```py\n{self.traceback_str[-1800:]}\n```",
                file=file,
                ephemeral=True,
            )


def format_error_details(error: BaseException) -> tuple[str, str, str]:
    """Returns (title, short_summary, full_traceback_str)."""
    original_error = getattr(error, "original", error)
    tb_lines = traceback.format_exception(
        type(original_error), original_error, original_error.__traceback__
    )
    full_traceback = "".join(tb_lines).strip()
    if not full_traceback:
        full_traceback = f"{type(original_error).__name__}: {original_error}"

    if isinstance(error, app_commands.MissingPermissions):
        title = "⛔ Missing Permissions"
        formatted_perms = [
            perm.replace("_", " ").title() for perm in error.missing_permissions
        ]
        missing_list = ", ".join(formatted_perms)
        desc = (
            f"You do not have the required permissions to use this command.\n\n"
            f"**Missing:** `{missing_list}`"
        )
    elif isinstance(error, app_commands.BotMissingPermissions):
        title = "⛔ Bot Missing Permissions"
        formatted_perms = [
            perm.replace("_", " ").title() for perm in error.missing_permissions
        ]
        missing_list = ", ".join(formatted_perms)
        desc = (
            f"The bot is missing the required permissions to complete this action.\n\n"
            f"**Missing:** `{missing_list}`"
        )
    elif isinstance(error, app_commands.CommandOnCooldown):
        title = "⏳ Command on Cooldown"
        desc = f"This command is currently on cooldown. Please try again in **{error.retry_after:.1f}s**."
    elif isinstance(error, app_commands.CheckFailure):
        title = "⛔ Access Denied"
        desc = "You cannot use this command here or do not meet the command requirements."
    elif isinstance(original_error, discord.HTTPException):
        title = "📡 Discord API Error"
        if original_error.status == 403:
            desc = "Discord denied the request (403 Forbidden). The bot might be missing permissions or role hierarchy."
        elif original_error.status == 404:
            desc = "The requested Discord resource (channel, message, or user) was not found."
        elif original_error.status == 429:
            desc = "Discord is currently rate limiting this action. Please wait a moment and try again."
        elif original_error.status >= 500:
            desc = "Discord's servers are currently encountering issues. Please try again later."
        else:
            desc = f"The Discord API returned an error.\n\n**Details:**\n> `{original_error.text or str(original_error)}`"
    else:
        title = "⚠️ An Error Occurred"
        error_name = type(original_error).__name__
        error_msg = str(original_error).strip() or "No additional details provided."
        desc = f"**Error Type:** `{error_name}`\n**Details:**\n> {error_msg}"

    return title, desc, full_traceback


async def handle_interaction_error(
    interaction: discord.Interaction,
    error: BaseException,
    context_name: str = "Command",
) -> None:
    title, desc, full_traceback = format_error_details(error)

    # Extract contextual metadata for logs and console
    cmd_name = (
        interaction.command.name
        if interaction.command
        else (
            interaction.data.get("name")
            if interaction.data and isinstance(interaction.data, dict)
            else context_name
        )
    )
    user_info = f"{interaction.user} ({interaction.user.id})" if interaction.user else "Unknown User"
    guild_info = f"'{interaction.guild.name}' ({interaction.guild_id})" if interaction.guild else "Direct Messages"
    channel_info = (
        f"#{interaction.channel.name} ({interaction.channel_id})"
        if hasattr(interaction.channel, "name")
        else f"Channel ID {interaction.channel_id}"
    )

    # 1. Log to discord.log (and console via configured root logger handlers)
    log.error(
        f"Error in {context_name} '{cmd_name}' by {user_info} in {guild_info}, {channel_info}:\n{full_traceback}"
    )

    # 2. Print directly to console for immediate visibility in terminal/container output
    print(
        f"\n[ERROR] Exception in {context_name} '{cmd_name}'\n"
        f"  User:    {user_info}\n"
        f"  Guild:   {guild_info}\n"
        f"  Channel: {channel_info}\n"
        f"{full_traceback}\n",
        flush=True,
    )

    # 3. Send ephemeral response with short summary + full traceback button
    view = ErrorDisplayView(
        title=title,
        description=desc,
        traceback_str=full_traceback,
        author_id=interaction.user.id if interaction.user else None,
    )

    try:
        if interaction.response.is_done():
            await interaction.followup.send(view=view, ephemeral=True)
        else:
            await interaction.response.send_message(view=view, ephemeral=True)
    except discord.HTTPException as e:
        log.warning(f"Error handler failed to send UI to user: {e}")
        print(f"[WARNING] Could not send error UI to user: {e}", flush=True)


class ErrorHandler(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._old_tree_error = None
        self._old_view_error = None
        self._old_modal_error = None

    async def cog_load(self) -> None:
        tree = self.bot.tree
        self._old_tree_error = tree.on_error
        tree.on_error = self.tree_on_error

        # Hook BaseView.on_error so ANY button/select interaction error across ALL views triggers reporting
        self._old_view_error = BaseView.on_error

        async def global_view_on_error(
            view_self: BaseView,
            interaction: discord.Interaction,
            error: Exception,
            item: discord.ui.Item,
        ) -> None:
            item_name = (
                getattr(item, "label", None)
                or getattr(item, "custom_id", None)
                or type(item).__name__
            )
            await handle_interaction_error(
                interaction,
                error,
                context_name=f"UI Component '{item_name}' in {type(view_self).__name__}",
            )

        BaseView.on_error = global_view_on_error

        # Hook Modal.on_error so ANY modal submit error triggers reporting
        self._old_modal_error = Modal.on_error

        async def global_modal_on_error(
            modal_self: Modal,
            interaction: discord.Interaction,
            error: Exception,
        ) -> None:
            modal_name = getattr(modal_self, "title", None) or type(modal_self).__name__
            await handle_interaction_error(
                interaction,
                error,
                context_name=f"Modal '{modal_name}'",
            )

        Modal.on_error = global_modal_on_error

    async def cog_unload(self) -> None:
        if self._old_tree_error:
            self.bot.tree.on_error = self._old_tree_error
        if self._old_view_error:
            BaseView.on_error = self._old_view_error
        if self._old_modal_error:
            Modal.on_error = self._old_modal_error

    async def tree_on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        await handle_interaction_error(
            interaction, error, context_name="Slash/Context Command"
        )

    @commands.Cog.listener()
    async def on_error(self, event_method: str, *args, **kwargs) -> None:
        """Catches errors in bot event processes like on_message, on_guild_join, etc."""
        exc_type, exc_val, exc_tb = sys.exc_info()
        if exc_val is not None:
            tb_str = "".join(traceback.format_exception(exc_type, exc_val, exc_tb)).strip()
        else:
            tb_str = traceback.format_exc().strip()

        log.error(f"Unhandled exception in bot process event '{event_method}':\n{tb_str}")
        print(
            f"\n[ERROR] Unhandled exception in bot process event '{event_method}':\n{tb_str}\n",
            flush=True,
        )

    @commands.Cog.listener()
    async def on_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        """Catches errors in prefix commands."""
        original_error = getattr(error, "original", error)
        tb_lines = traceback.format_exception(
            type(original_error), original_error, original_error.__traceback__
        )
        tb_str = "".join(tb_lines).strip()

        log.error(f"Unhandled exception in prefix command '{ctx.command}':\n{tb_str}")
        print(
            f"\n[ERROR] Unhandled exception in prefix command '{ctx.command}':\n{tb_str}\n",
            flush=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ErrorHandler(bot))