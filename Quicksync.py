import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

class QuickSync(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="quicksync", hidden=True)
    @commands.is_owner()
    async def quicksync(self, ctx):
        raw_guilds = os.getenv("ALLOWED_GUILDS", "")
        if not raw_guilds:
            return await ctx.send("Error: ALLOWED_GUILDS not found in your .env file.")
            
        allowed_guild_ids = [int(g.strip()) for g in raw_guilds.split(",") if g.strip()]
        
        if ctx.guild.id not in allowed_guild_ids:
            return await ctx.send("You cannot use the quicksync command in this server.")
            
        await ctx.send(f"Syncing slash commands to {len(allowed_guild_ids)} allowed guild(s)...")
        
        try:
            for guild_id in allowed_guild_ids:
                guild_obj = discord.Object(id=guild_id)
                await self.bot.tree.sync(guild=guild_obj)
                
            await ctx.send("Successfully synced commands to all allowed guilds!")
        except Exception as e:
            await ctx.send(f"Failed to sync commands: {e}")

async def setup(bot):
    await bot.add_cog(QuickSync(bot))