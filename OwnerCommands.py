from __future__ import annotations
import logging
import os
from typing import List, Literal
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import ERROR_COLOR, SUCCESS_COLOR, WARNING_COLOR, create_v2_view
from cogs.utils.config import ALLOWED_GUILD_IDS

log = logging.getLogger(__name__)

ALLOWED_GUILDS = [discord.Object(id=guild_id) for guild_id in ALLOWED_GUILD_IDS]


def code_list(names: List[str], limit: int = 1500) -> str:
    text = ", ".join(f"`{name}`" for name in names)
    return text if len(text) <= limit else text[:limit].rsplit(",", 1)[0] + ", …"


@app_commands.guilds(*ALLOWED_GUILDS)
@app_commands.default_permissions(administrator=True)
class OwnerCog(commands.GroupCog, group_name="owner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def all_extensions(self) -> List[str]:
        found = {f"cogs.{name[:-3]}" for name in os.listdir("cogs") if name.endswith(".py")}
        return sorted(found | set(self.bot.extensions))

    @app_commands.command(name="sync", description="Sync slash commands")
    async def sync(self, interaction: discord.Interaction, sync_type: Literal["Global", "Guild"]) -> None:
        await interaction.response.defer()
        await interaction.followup.send(
            view=create_v2_view(f"Syncing commands ({sync_type})", "This can take a few seconds.", color=WARNING_COLOR)
        )
        try:
            if sync_type == "Global":
                synced = await self.bot.tree.sync()
                target = "globally"
            else:
                synced = []
                for guild in ALLOWED_GUILDS:
                    synced = await self.bot.tree.sync(guild=guild)
                target = f"to {len(ALLOWED_GUILDS)} allowed server(s)"
        except discord.HTTPException as error:
            log.warning("Command sync failed: %s", error)
            await interaction.edit_original_response(
                view=create_v2_view("Sync failed", f"```{str(error)[:1000]}```", color=ERROR_COLOR)
            )
            return

        log.info("Synced %s commands %s: %s", len(synced), target, ", ".join(c.name for c in synced))
        await interaction.edit_original_response(
            view=create_v2_view(
                "Commands synced",
                f"Synced **{len(synced)}** command(s) {target}.\n\n{code_list([c.name for c in synced]) or 'No commands found.'}",
                color=SUCCESS_COLOR,
            )
        )

    @app_commands.command(name="sync_clear", description="Remove every command from the tree")
    async def sync_clear(self, interaction: discord.Interaction, clear_type: Literal["Global", "Guild"]) -> None:
        await interaction.response.defer()
        if clear_type == "Global":
            removed = len(self.bot.tree.get_commands())
            self.bot.tree.clear_commands(guild=None)
            target = "globally"
        else:
            removed = len(self.bot.tree.get_commands(guild=interaction.guild))
            self.bot.tree.clear_commands(guild=interaction.guild)
            target = f"from **{interaction.guild.name}**"
        log.info("Cleared %s commands %s", removed, target.replace("**", ""))
        await interaction.followup.send(
            view=create_v2_view(
                "Commands cleared",
                f"Removed **{removed}** command(s) {target}. Nobody will see them until you sync again.",
                footer="Run /owner sync to add them back",
                color=SUCCESS_COLOR,
            )
        )

    @app_commands.command(name="ext", description="Load, unload or reload an extension")
    async def ext(self, interaction: discord.Interaction, action: Literal["load", "unload", "reload"], extension: str) -> None:
        await interaction.response.defer()
        handlers = {
            "load": self.bot.load_extension,
            "unload": self.bot.unload_extension,
            "reload": self.bot.reload_extension,
        }
        try:
            await handlers[action](extension)
        except commands.ExtensionAlreadyLoaded:
            problem = "It's already loaded."
        except commands.ExtensionNotLoaded:
            problem = "It isn't loaded."
        except commands.ExtensionNotFound:
            problem = "No extension with that name exists."
        except commands.ExtensionFailed as error:
            problem = f"It failed during setup.\n```{error}```"
        else:
            problem = ""

        if problem:
            view = create_v2_view(f"Couldn't {action} `{extension}`", problem, color=ERROR_COLOR)
        else:
            view = create_v2_view(f"{action.capitalize()}ed `{extension}`", "Done.", color=WARNING_COLOR if action == "unload" else SUCCESS_COLOR)
        await interaction.followup.send(view=view)

    @ext.autocomplete("extension")
    async def ext_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=name, value=name)
            for name in self.all_extensions()
            if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="cogs", description="Reload every loaded extension and load any new cogs")
    async def cogs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        results = {"Reloaded": [], "Loaded": [], "Failed": []}
        extensions = self.all_extensions()
        for name in extensions:
            try:
                if name in self.bot.extensions:
                    await self.bot.reload_extension(name)
                    results["Reloaded"].append(name)
                else:
                    await self.bot.load_extension(name)
                    results["Loaded"].append(name)
            except commands.ExtensionError as error:
                log.warning("Extension %s failed: %s", name, error)
                results["Failed"].append(name)

        body = "\n\n".join(f"**{label}**\n{code_list(names)}" for label, names in results.items() if names)
        await interaction.followup.send(
            view=create_v2_view(
                "Extensions",
                body or "Nothing to do.",
                footer=f"Processed {len(extensions)} extension(s)",
                color=ERROR_COLOR if results["Failed"] else SUCCESS_COLOR,
            )
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerCog(bot))
