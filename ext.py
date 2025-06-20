import discord
from discord.ext import commands
from typing import Literal
import os

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
                await ctx.send(f"Extension {extension} is already loaded.")
            except commands.ExtensionNotFound:
                await ctx.send("Extension not found.")
            except commands.ExtensionFailed:
                await ctx.send("Extension failed to load.")
            else:
                await ctx.send(f'Loaded extension "{extension}".')
        elif action == "unload":
            try:
                await self.bot.unload_extension(extension)
            except commands.ExtensionNotLoaded:
                await ctx.send(f"Extension {extension} is not loaded.")
            else:
                await ctx.send(f'Unloaded extension "{extension}".')
        elif action == "reload":
            try:
                await self.bot.reload_extension(extension)
            except commands.ExtensionNotLoaded:
                await ctx.send(f"Extension {extension} is not loaded.")
            except commands.ExtensionNotFound:
                await ctx.send("Extension not found.")
            except commands.ExtensionFailed:
                await ctx.send("Extension failed to load.")
            else:
                await ctx.send(f'Reloaded extension "{extension}".')

    @commands.command(name="cogs", description="Reloads and loads all cogs", hidden=True)
    @commands.is_owner()
    async def cogs(self, ctx: commands.Context) -> None:
        reloaded_cogs = []
        loaded = []
        not_found = []
        failed = []
        for filename in os.listdir('cogs'):
            if filename.endswith('.py'):
                cog_name = filename[:-3]  # Remove the .py extension
                try:
                    await self.bot.reload_extension(f'cogs.{cog_name}')
                    reloaded_cogs.append(f'{cog_name}')
                except commands.ExtensionNotLoaded:
                    await self.bot.load_extension(f'cogs.{cog_name}')
                    loaded.append(f'{cog_name}')
                except commands.ExtensionNotFound:
                    await ctx.send(f"Extension not found: {cog_name}")
                    not_found.append(f'{cog_name}')
                except commands.ExtensionFailed:
                    await ctx.send(f"Extension failed to load: {cog_name}")
                    failed.append(f'{cog_name}')
        if reloaded_cogs:
            await ctx.send(f'Reloaded cogs: {", ".join(reloaded_cogs)}')
        if loaded:
            await ctx.send(f'Loaded cogs: {", ".join(loaded)}')
        if not_found:
            await ctx.send(f'Not found cogs: {", ".join(not_found)}')
        if failed:
            await ctx.send(f'Failed to load cogs: {", ".join(failed)}')

async def setup(bot) -> None:
    await bot.add_cog(Extensions(bot))
    print(f'Extensions cog loaded')