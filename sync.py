import os
import discord
from discord.app_commands.commands import guilds
from discord.ext import commands
from discord import app_commands
import random
import asyncio

allowed_guilds = [1293647067998326936]

class SyncCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        print(f"SyncCog loaded")

    @commands.command(name='sync', description='Syncs the bot', hidden=True)
    @commands.is_owner()
    async def sync(self, ctx) -> None:
        await self.bot.tree.sync(guild=None)
        await ctx.send('Commands synced.')

    @commands.command(name='syncg', description='Syncs the bot', hidden=True)
    @commands.is_owner()
    async def syncg(self, ctx, guild: discord.Guild) -> None:
        await self.bot.tree.sync(guild=guild)
        await ctx.send('Commands synced.')

    @commands.command(name='clear', description='Clears all commands from the tree', hidden=True)
    @commands.is_owner()
    async def clear(self, ctx) -> None:
        ctx.bot.tree.clear_commands(guild=None)
        await ctx.send('Commands cleared.')

    @app_commands.command(name='help', description='Show help for slash commands')
    async def help(self, ctx):
        embed = discord.Embed(title='Help', description='**List of available slash commands:**', color=discord.Color.from_str('#af2202'))
        embed.add_field(name='**General commands:**', value='', inline=False)
        for command in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input, ):
            if command.parent is None and not isinstance(command, discord.app_commands.Group):
                 embed.add_field(name=f'/{command.name}', value=command.description, inline=False)
        embed.add_field(name='\u200b', value='', inline=False)
        embed.add_field(name='**Count settings commands:**', value='*(these comands require manage server permissions except a couple exceptions)*\n\u200b', inline=False)
        for command2 in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input, ):
            if command2.root_parent is not None and command2.root_parent.name == 'count' and not isinstance(command2, discord.app_commands.Group) or command2.parent is not None and command2.root_parent.name == 'keyword' and not isinstance(command2, discord.app_commands.Group) and command2.name != 'leaderboard' or command2.name == "reset":
                embed.add_field(name=f'/{command2.qualified_name}', value=command2.description, inline=False)
        embed.add_field(name='\u200b', value='', inline=False)
        embed.add_field(name='**Stats commands:**', value='*(these comands can be used by anyone to view user and server stats)*\n\u200b', inline=False)
        for command3 in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input, ):
            if command3.root_parent is not None and command3.root_parent.name == 'words' and not isinstance(command3, discord.app_commands.Group) or command3.name == "leaderboard" and command3.name != "reset":
                embed.add_field(name=f'/{command3.qualified_name}', value=command3.description, inline=False)
        await ctx.response.send_message(embed=embed)

    async def cycle(self):
        while True:
            presences = [
    (discord.Game(name="with words"), discord.Status.idle),
    (discord.Activity(type=discord.ActivityType.listening, name="your commands"), discord.Status.do_not_disturb),
    (discord.Activity(type=discord.ActivityType.watching, name="your messages"), discord.Status.online),
    (discord.Activity(type=discord.ActivityType.listening, name=f"to {len(self.bot.guilds)} servers"), discord.Status.idle),
    (discord.Activity(type=discord.ActivityType.listening, name="your messages"), discord.Status.do_not_disturb),
    (discord.Activity(type=discord.ActivityType.watching, name=f"{len(self.bot.guilds)} servers"), discord.Status.online)
            ]
            Activity, Status = random.choice(presences)
            await self.bot.change_presence(activity=Activity, status=Status)
            print(f'Changed status to {Activity} {Status}')
            await asyncio.sleep(3600)
            
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        print(f'Logged in as {self.bot.user.name} (ID: {self.bot.user.id})')
        self.bot.loop.create_task(self.cycle())

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if ctx.guild.id not in allowed_guilds and isinstance(error, commands.NotOwner):
            print(f"Error: this user tried to use an owner command in another guild {ctx.author.name} in {ctx.guild.name}:{ctx.guild.id}")
            return
        if ctx.guild.id not in allowed_guilds:
            print(f"Error: this user tried to use a command in another guild {ctx.author.name} in {ctx.guild.name}:{ctx.guild.id}")
            return
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(f"Invalid command. Type `!wchelp` for a list of available commands.")
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send("You don't have the required permissions to use this command.")
        elif isinstance(error, commands.NotOwner):
            print(f"Error: this user tried to use an owner command {ctx.author.name}")
            await ctx.send("You cannot use this command because you are not the owner of this bot.")
        else:
            print(f"Error: {error}")
            await ctx.send(f"An error occurred. {error}")
        
async def setup(bot) -> None:
    await bot.add_cog(SyncCog(bot))