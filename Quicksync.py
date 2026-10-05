from __future__ import annotations
import discord
from discord.ext import commands
from cogs.utils.components import SUCCESS_COLOR, WARNING_COLOR, create_v2_view, error_view
from cogs.utils.config import ALLOWED_GUILD_IDS


class QuickSync(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="quicksync", hidden=True)
    @commands.is_owner()
    async def quicksync(self, ctx: commands.Context) -> None:
        if not ALLOWED_GUILD_IDS:
            await ctx.send(view=error_view("`ALLOWED_GUILDS` isn't set in the .env file."))
            return
        if ctx.guild is None or ctx.guild.id not in ALLOWED_GUILD_IDS:
            await ctx.send(view=error_view("Quicksync only works in a server listed in `ALLOWED_GUILDS`."))
            return

        count = len(ALLOWED_GUILD_IDS)
        status = await ctx.send(view=create_v2_view("Syncing commands", f"Syncing to {count} allowed server(s).", color=WARNING_COLOR))
        try:
            for guild_id in ALLOWED_GUILD_IDS:
                await self.bot.tree.sync(guild=discord.Object(id=guild_id))
        except discord.HTTPException as error:
            await status.edit(view=error_view(f"Sync failed: `{error}`"))
            return
        await status.edit(view=create_v2_view("Commands synced", f"Synced to {count} allowed server(s).", color=SUCCESS_COLOR))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuickSync(bot))
