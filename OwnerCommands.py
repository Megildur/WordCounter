import discord
from discord.ext import commands
from discord import app_commands
import os
from typing import Literal
from dotenv import load_dotenv

load_dotenv()

allowed_guilds_str = os.getenv('ALLOWED_GUILDS', '')
ALLOWED_GUILDS = [
    discord.Object(id=int(g.strip())) 
    for g in allowed_guilds_str.split(',') 
    if g.strip().isdigit()
]

def get_all_extensions() -> list[str]:
    extensions = []
    for root, dirs, files in os.walk('Source'):
        dirs[:] = [d for d in dirs if d not in ['__pycache__', 'Utils', 'utils']]
            
        if '__init__.py' in files:
            ext_name = root.replace(os.sep, '.')
            extensions.append(ext_name)
            dirs.clear()
        else:
            for file in files:
                if file.endswith('.py'):
                    ext_name = os.path.join(root, file[:-3]).replace(os.sep, '.')
                    extensions.append(ext_name)
    return extensions

@app_commands.guilds(*ALLOWED_GUILDS)
@app_commands.default_permissions(administrator=True)
class OwnerCog(commands.GroupCog, group_name='owner'):
    def __init__(self, bot) -> None:
        self.bot = bot
        print("OwnerCog loaded")

    @app_commands.command(name='sync', description='Syncs the bot commands')
    async def sync(self, interaction: discord.Interaction, sync_type: Literal['Global', 'Guild']) -> None:
        await interaction.response.defer(ephemeral=False)
        
        try:
            loading_layout = discord.ui.LayoutView()
            loading_container = discord.ui.Container(
                discord.ui.TextDisplay(content=f'# 🔄 Command Sync ({sync_type})'),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f'> Starting {sync_type.lower()} command synchronization...'),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(content='This may take a few moments'),
                accent_colour=discord.Colour.yellow()
            )
            loading_layout.add_item(loading_container)
            await interaction.followup.send(view=loading_layout)

            if sync_type == 'Global':
                synced = await self.bot.tree.sync(guild=None)
                target_text = "globally"
            else:
                synced = []
                for guild_obj in ALLOWED_GUILDS:
                    synced = await self.bot.tree.sync(guild=guild_obj)
                target_text = f"across **{len(ALLOWED_GUILDS)} allowed guilds**"

            result_layout = discord.ui.LayoutView()
            content_lines = ["\n>>> **📝 Synced Commands**"]
            
            if synced:
                command_list = '\n'.join([f'• `{command.name}`' for command in synced])
                display_list = command_list if len(command_list) < 1024 else f'{command_list[:1000]}...\n*+{len(synced)-command_list[:1000].count("•")} more*'
                content_lines.append(display_list)
            else:
                content_lines.append("No commands found to sync.")
                
            main_text = discord.ui.TextDisplay(content='\n'.join(content_lines))
            footer_text = discord.ui.TextDisplay(content="-# All commands are now available • Sync completed")
            
            avatar_url = self.bot.user.avatar.url if self.bot.user.avatar else None
            if avatar_url:
                footer_element = discord.ui.Section(
                    footer_text,
                    accessory=discord.ui.Thumbnail(media=avatar_url)
                )
            else:
                footer_element = footer_text
                
            success_container = discord.ui.Container(
                discord.ui.TextDisplay(content=f"### ✅ Sync Successful\n**{len(synced)} commands** have been synchronized {target_text}"),
                discord.ui.Separator(),
                main_text,
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                footer_element,
                accent_colour=discord.Colour.green()
            )
            result_layout.add_item(success_container)
            await interaction.edit_original_response(view=result_layout)
            
            print(f"Synced {len(synced)} commands {target_text.replace('**', '')}")
            for command in synced:
                print(f"  - {command.name}")

        except Exception as e:
            error_layout = discord.ui.LayoutView()
            error_container = discord.ui.Container(
                discord.ui.TextDisplay(content='### ❌ Sync Error'),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f'Failed to sync commands {sync_type.lower()}.'),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(content=f'🔍 **Error Details**\n```{str(e)[:1000]}```'),
                accent_colour=discord.Colour.red()
            )
            error_layout.add_item(error_container)
            await interaction.edit_original_response(view=error_layout)
            print(f"Error during sync: {e}")

    @app_commands.command(name='sync_clear', description='Clears all commands from the tree')
    async def sync_clear(self, interaction: discord.Interaction, clear_type: Literal['Global', 'Guild']) -> None:
        await interaction.response.defer(ephemeral=False)
        try:
            loading_layout = discord.ui.LayoutView()
            loading_container = discord.ui.Container(
                discord.ui.TextDisplay(content=f'# 🗑️ Clearing Commands ({clear_type})'),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f'> Removing all {clear_type.lower()} commands from the command tree...'),
                accent_colour=discord.Colour.orange()
            )
            loading_layout.add_item(loading_container)
            await interaction.followup.send(view=loading_layout)

            if clear_type == 'Global':
                before_count = len(self.bot.tree.get_commands(guild=None))
                self.bot.tree.clear_commands(guild=None)
                target_text = "globally"
            else:
                before_count = len(self.bot.tree.get_commands(guild=interaction.guild))
                self.bot.tree.clear_commands(guild=interaction.guild)
                target_text = f"from **{interaction.guild.name}**"

            result_layout = discord.ui.LayoutView()
            success_container = discord.ui.Container(
                discord.ui.TextDisplay(content=f"### 🧹 Commands Cleared\nSuccessfully removed **{before_count} commands** {target_text}."),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="### 📊 Summary"),
                discord.ui.TextDisplay(content=f"• **{before_count}** commands removed\n• Command tree is now empty\n• Users will no longer see slash commands"),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(content="-# Use /sync to re-add commands to the tree"),
                accent_colour=discord.Colour.green()
            )
            result_layout.add_item(success_container)
            await interaction.edit_original_response(view=result_layout)
            print(f"Cleared {before_count} commands {target_text.replace('**', '')}")

        except Exception as e:
            error_layout = discord.ui.LayoutView()
            error_container = discord.ui.Container(
                discord.ui.TextDisplay(content='### ❌ Clear Failed'),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content='An error occurred while clearing commands.'),
                discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
                discord.ui.TextDisplay(content=f'🔍 **Error Details**\n```{str(e)[:1000]}```'),
                accent_colour=discord.Colour.red()
            )
            error_layout.add_item(error_container)
            await interaction.edit_original_response(view=error_layout)
            print(f"Error clearing commands: {e}")

    @app_commands.command(name="ext", description="Loads, unloads, or reloads an extension")
    async def ext(self, interaction: discord.Interaction, action: Literal["load", "unload", "reload"], extension: str) -> None:
        await interaction.response.defer(ephemeral=False)
        
        error_msg = None
        color = discord.Colour.green()
        
        try:
            if action == "load":
                await self.bot.load_extension(extension)
            elif action == "unload":
                await self.bot.unload_extension(extension)
                color = discord.Colour.yellow()
            elif action == "reload":
                await self.bot.reload_extension(extension)
        except commands.ExtensionAlreadyLoaded:
            error_msg = "Extension is already loaded."
        except commands.ExtensionNotLoaded:
            error_msg = "Extension is not currently loaded."
        except commands.ExtensionNotFound:
            error_msg = "Extension file or package was not found."
        except commands.ExtensionFailed as e:
            error_msg = f"Extension failed during setup.\n```{str(e)}```"
        except Exception as e:
            error_msg = f"Unexpected error occurred.\n```{str(e)}```"

        result_layout = discord.ui.LayoutView()
        if error_msg:
            container = discord.ui.Container(
                discord.ui.TextDisplay(content=f"### ❌ Action Failed: {action.capitalize()}"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f"Target: `{extension}`\n\n> {error_msg}"),
                accent_colour=discord.Colour.red()
            )
        else:
            container = discord.ui.Container(
                discord.ui.TextDisplay(content=f"### ✅ Extension {action.capitalize()}ed"),
                discord.ui.Separator(),
                discord.ui.TextDisplay(content=f"Successfully {action}ed `{extension}`."),
                accent_colour=color
            )
            
        result_layout.add_item(container)
        await interaction.followup.send(view=result_layout)

    @ext.autocomplete('extension')
    async def ext_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        extensions = get_all_extensions()
        return [
            app_commands.Choice(name=ext, value=ext)
            for ext in extensions if current.lower() in ext.lower()
        ][:25]

    @app_commands.command(name="cogs", description="Walks through Source to load or reload all cogs and packages")
    async def cogs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=False)
        
        reloaded_cogs = []
        loaded_cogs = []
        not_found = []
        failed = []

        all_extensions = get_all_extensions()

        for ext_path in all_extensions:
            try:
                await self.bot.reload_extension(ext_path)
                reloaded_cogs.append(ext_path)
            except commands.ExtensionNotLoaded:
                try:
                    await self.bot.load_extension(ext_path)
                    loaded_cogs.append(ext_path)
                except commands.ExtensionNotFound:
                    not_found.append(ext_path)
                except commands.ExtensionFailed:
                    failed.append(ext_path)
            except commands.ExtensionNotFound:
                not_found.append(ext_path)
            except commands.ExtensionFailed:
                failed.append(ext_path)

        display_elements = [discord.ui.TextDisplay(content="# ⚙️ Cogs & Packages Setup")]
        
        if reloaded_cogs:
            display_elements.extend([
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="### 🔄 Reloaded"),
                discord.ui.TextDisplay(content=", ".join([f"`{c}`" for c in reloaded_cogs]))
            ])
            
        if loaded_cogs:
            display_elements.extend([
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="### ✅ Loaded"),
                discord.ui.TextDisplay(content=", ".join([f"`{c}`" for c in loaded_cogs]))
            ])
            
        if not_found:
            display_elements.extend([
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="### ❓ Not Found"),
                discord.ui.TextDisplay(content=", ".join([f"`{c}`" for c in not_found]))
            ])
            
        if failed:
            display_elements.extend([
                discord.ui.Separator(),
                discord.ui.TextDisplay(content="### ❌ Failed to Load"),
                discord.ui.TextDisplay(content=", ".join([f"`{c}`" for c in failed]))
            ])

        color = discord.Colour.red() if failed else discord.Colour.green()
        result_layout = discord.ui.LayoutView()
        container = discord.ui.Container(
            *display_elements,
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(content=f"-# Processed a total of {len(all_extensions)} modules/packages."),
            accent_colour=color
        )
        
        result_layout.add_item(container)
        await interaction.followup.send(view=result_layout)


async def setup(bot) -> None:
    await bot.add_cog(OwnerCog(bot))