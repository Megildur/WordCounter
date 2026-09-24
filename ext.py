import discord
from discord.ext import commands
from typing import Literal
import os
from cogs.utils.components import error_view, success_view, create_v2_view, BRAND_COLOR


class Extensions(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.command(name="ext", description="Loads or reloads an extension", hidden=True)
    @commands.is_owner()
    async def ext(self, ctx: commands.Context, action: Literal["load", "unload", "reload"], *, extension: str) -> None:
        if action == "load":
            try:
                await self.bot.load_extension(extension)
            except commands.ExtensionAlreadyLoaded:
                await ctx.send(view=error_view(f"Extension `{extension}` is already loaded."))
            except commands.ExtensionNotFound:
                await ctx.send(view=error_view("Extension not found."))
            except commands.ExtensionFailed as e:
                await ctx.send(view=error_view(f"Extension failed to load: `{e}`"))
            else:
                await ctx.send(view=success_view(f'Loaded extension `{extension}`.'))
        elif action == "unload":
            try:
                await self.bot.unload_extension(extension)
            except commands.ExtensionNotLoaded:
                await ctx.send(view=error_view(f"Extension `{extension}` is not loaded."))
            else:
                await ctx.send(view=success_view(f'Unloaded extension `{extension}`.'))
        elif action == "reload":
            try:
                await self.bot.reload_extension(extension)
            except commands.ExtensionNotLoaded:
                await ctx.send(view=error_view(f"Extension `{extension}` is not loaded."))
            except commands.ExtensionNotFound:
                await ctx.send(view=error_view("Extension not found."))
            except commands.ExtensionFailed as e:
                await ctx.send(view=error_view(f"Extension failed to load: `{e}`"))
            else:
                await ctx.send(view=success_view(f'Reloaded extension `{extension}`.'))

    @commands.command(name="cogs", description="Reloads and loads all cogs", hidden=True)
    @commands.is_owner()
    async def cogs(self, ctx: commands.Context) -> None:
        reloaded_cogs = []
        loaded = []
        not_found = []
        failed = []
        for filename in os.listdir('cogs'):
            if filename.endswith('.py'):
                cog_name = filename[:-3]
                try:
                    await self.bot.reload_extension(f'cogs.{cog_name}')
                    reloaded_cogs.append(f'`{cog_name}`')
                except commands.ExtensionNotLoaded:
                    await self.bot.load_extension(f'cogs.{cog_name}')
                    loaded.append(f'`{cog_name}`')
                except commands.ExtensionNotFound:
                    not_found.append(f'`{cog_name}`')
                except commands.ExtensionFailed:
                    failed.append(f'`{cog_name}`')

        fields = []
        if reloaded_cogs:
            fields.append(('🔄 Reloaded Cogs', ', '.join(reloaded_cogs)))
        if loaded:
            fields.append(('✅ Loaded Cogs', ', '.join(loaded)))
        if not_found:
            fields.append(('❓ Not Found Cogs', ', '.join(not_found)))
        if failed:
            fields.append(('❌ Failed Cogs', ', '.join(failed)))

        await ctx.send(
            view=create_v2_view(
                title='⚙️ Cog Manager Summary',
                description='Completed reloading and loading cogs.',
                fields=fields,
                color=BRAND_COLOR,
            )
        )


async def setup(bot) -> None:
    await bot.add_cog(Extensions(bot))
    print('Extensions cog loaded')