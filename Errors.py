import os
import discord
import logging
from discord.ext import commands
from discord.app_commands.commands import guilds
from discord import app_commands
import asyncio
from dotenv import load_dotenv
from .Utils.Cooldowns import handle_cooldown_error

log = logging.getLogger(__name__)

load_dotenv()

bot_server = str(os.getenv('BOT_SERVER'))

class ErrorDisplayView(discord.ui.LayoutView):
    def __init__(self, title: str, description: str):
        super().__init__(timeout=None)

      
        container = discord.ui.Container(
            discord.ui.TextDisplay(content=f"# **{title}**"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content=description),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(
                content=f"If this keeps happening, please contact the developer, Brandon, for help.\n\n> Please join the support server if you haven't!\n 🔗 **[Support Server Invite]({bot_server})**"
            ),
            accent_colour=discord.Colour.red()
        )
        self.add_item(container)

class ErrorHandler(commands.Cog):
    def __init__(self, bot):
       self.bot = bot

    async def cog_load(self) -> None:
        tree = self.bot.tree
        self._old_tree_error = tree.on_error
        tree.on_error = self.tree_on_error
        self._old_view_error = discord.ui.LayoutView.on_error

    async def global_view_error(view_instance, interaction: discord.Interaction, error: Exception, item: discord.ui.Item):
        await self.view_on_error(interaction, error, item)
            
        discord.ui.LayoutView.on_error = global_view_error


    async def cog_unload(self) -> None:
        tree = self.bot.tree
        tree.on_error = self._old_tree_error

    async def tree_on_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError
    ) -> None:
        
        original_error = getattr(error, 'original', error)
        short_error = f"{type(original_error).__name__}: {str(original_error)}"
      
        if interaction.response.is_done():
            return

        if isinstance(error, app_commands.CommandOnCooldown):
            await handle_cooldown_error(interaction, error)
            return
        elif isinstance(error, app_commands.MissingPermissions):
            title = "⛔ Missing Permissions"
            formatted_perms = [
                perm.replace("_", " ").title() for perm in error.missing_permissions
            ]
            missing_list = ", ".join(formatted_perms)
            desc = f"You do not have the required permissions to use this command.\n\n**Missing:** `{missing_list}`"
        elif isinstance(original_error, discord.HTTPException):
            title = "📡 Discord API Error"
            
            if original_error.status == 403:
                desc = "Discord denied the request. The bot might be missing permissions to perform this specific action (e.g., trying to manage a user with a higher role)."
            elif original_error.status >= 500:
                desc = "Discord's servers are currently having issues. Please try again later."
            else:
                desc = f"The Discord API returned an error.\n\n**Details:**\n> `{original_error.text or short_error}`"
                log.error(f"Unhandled exception in command {interaction.command.name}:", exc_info=original_error)
           
        else:
            title = "⚠️ An Error Occurred"
            desc = f"**Error Details:**\n> `{short_error}`"

            log.error(f"Unhandled exception in command {interaction.command.name}:", exc_info=original_error)
           
        view = ErrorDisplayView(title, desc)
  
        try:
            if interaction.response.is_done():
                await interaction.followup.send(view=view, ephemeral=True)
            else:
                await interaction.response.send_message(view=view, ephemeral=True)
        except discord.HTTPException as e:
            log.warning(f"Error handler failed to send the UI to the user: {e}")
            print(f"[WARNING] Could not send error UI to user: {e}")
          
async def setup(bot):
    await bot.add_cog(ErrorHandler(bot))