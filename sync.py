import discord
from discord.ext import commands
from discord import app_commands

allowed_guilds = [1406313376279298088]

class SyncCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        print(f"SyncCog loaded")

    @commands.command(name='sync', description='Syncs the bot', hidden=True)
    @commands.is_owner()
    async def sync(self, ctx) -> None:
        try:
            initial_embed = discord.Embed(
                title='🔄 Command Sync',
                description='Starting global command synchronization...',
                color=0xffaa00
            )
            initial_embed.set_footer(text='This may take a few moments')
            await ctx.send(embed=initial_embed)
            synced = await self.bot.tree.sync(guild=None)
            success_embed = discord.Embed(
                title='✅ Sync Successful',
                description=f'**{len(synced)} commands** have been synchronized globally',
                color=0x00ff88
            )
            if synced:
                command_list = '\n'.join([f'• `{command.name}`' for command in synced])
                success_embed.add_field(
                    name='📝 Synced Commands',
                    value=command_list if len(command_list) < 1024 else f'{command_list[:1000]}...\n*+{len(synced)-command_list[:1000].count("•")} more*',
                    inline=False
                )
            success_embed.set_footer(
                text=f'All commands are now available as slash commands • Sync completed',
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            await ctx.send(embed=success_embed)
            print(f"Synced {len(synced)} commands globally")
            for command in synced:
                print(f"  - {command.name}")
                
        except discord.HTTPException as e:
            error_embed = discord.Embed(
                title='❌ HTTP Error',
                description='Failed to sync commands due to an HTTP error',
                color=0xff4444
            )
            error_embed.add_field(
                name='🔍 Error Details',
                value=f'```{str(e)[:1000]}```',
                inline=False
            )
            error_embed.set_footer(text='Try again later or contact support')
            await ctx.send(embed=error_embed)
            print(f"HTTP Error during sync: {e}")
            
        except Exception as e:
            error_embed = discord.Embed(
                title='⚠️ Unexpected Error',
                description='An unexpected error occurred during sync',
                color=0xff4444
            )
            error_embed.add_field(
                name='🔍 Error Details',
                value=f'```{str(e)[:1000]}```',
                inline=False
            )
            error_embed.set_footer(text='Please report this error to the developer')
            await ctx.send(embed=error_embed)
            print(f"Unexpected error during sync: {e}")
            
    @commands.command(name='clear', description='Clears all commands from the tree', hidden=True)
    @commands.is_owner()
    async def clear(self, ctx) -> None:
        try:
            initial_embed = discord.Embed(
                title='🗑️ Clearing Commands',
                description='Removing all commands from the command tree...',
                color=0xffaa00
            )
            initial_embed.set_footer(text='This will remove all slash commands')
            await ctx.send(embed=initial_embed)
            before_count = len(ctx.bot.tree.get_commands())
            ctx.bot.tree.clear_commands(guild=None)
            success_embed = discord.Embed(
                title='🧹 Commands Cleared',
                description=f'Successfully removed **{before_count} commands** from the command tree',
                color=0x00ff88
            )
            if before_count > 0:
                success_embed.add_field(
                    name='📊 Summary',
                    value=f'• **{before_count}** commands removed\n• Command tree is now empty\n• Users will no longer see slash commands',
                    inline=False
                )
            else:
                success_embed.add_field(
                    name='ℹ️ Note',
                    value='Command tree was already empty',
                    inline=False
                )
            success_embed.set_footer(
                text='Use !sync to re-add commands to the tree',
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            await ctx.send(embed=success_embed)
            print(f"Cleared {before_count} commands from tree")
            
        except Exception as e:
            error_embed = discord.Embed(
                title='❌ Clear Failed',
                description='An error occurred while clearing commands',
                color=0xff4444
            )
            error_embed.add_field(
                name='🔍 Error Details',
                value=f'```{str(e)[:1000]}```',
                inline=False
            )
            error_embed.set_footer(text='Please try again or contact support')
            await ctx.send(embed=error_embed)
            print(f"Error clearing commands: {e}")

    @commands.command(name='list_commands', description='List all loaded prefix commands', hidden=True)
    @commands.is_owner()
    async def list_commands(self, ctx) -> None:
        try:
            prefix_commands = []
            for command in self.bot.commands:
                if command.cog is None:
                    prefix_commands.append({
                        'name': command.name,
                        'description': command.description or command.brief or 'No description',
                        'cog': 'No Cog',
                        'aliases': command.aliases,
                        'hidden': command.hidden
                    })
            cog_commands = {}
            for cog_name, cog in self.bot.cogs.items():
                cog_command_list = []
                for command in cog.get_commands():
                    cog_command_list.append({
                        'name': command.name,
                        'description': command.description or command.brief or 'No description',
                        'aliases': command.aliases,
                        'hidden': command.hidden
                    })
                if cog_command_list:
                    cog_commands[cog_name] = cog_command_list
            
            total_commands = len(prefix_commands) + sum(len(commands) for commands in cog_commands.values())
            if total_commands == 0:
                empty_embed = discord.Embed(
                    title='📋 Prefix Commands Status',
                    description='🚫 **No prefix commands found**',
                    color=0xffaa00
                )
                empty_embed.add_field(
                    name='💡 Note',
                    value='No prefix commands are currently loaded in any cogs',
                    inline=False
                )
                empty_embed.set_footer(text='Only showing prefix commands (not slash commands)')
                await ctx.send(embed=empty_embed)
                return
            main_embed = discord.Embed(
                title='📋 Prefix Commands Overview',
                description=f'**{total_commands} prefix commands** currently loaded\n\n*These are traditional `!` commands, not slash commands*',
                color=0x00ff88
            )
            if prefix_commands:
                command_list = '\n'.join([
                    f'🔸 `!{cmd["name"]}` - {cmd["description"][:45]}{"..." if len(cmd["description"]) > 45 else ""}'
                    + (' 🔇' if cmd["hidden"] else '')
                    + (f'\n    └ *Aliases: {", ".join([f"`{alias}`" for alias in cmd["aliases"]])}*' if cmd["aliases"] else '')
                    for cmd in prefix_commands[:8]
                ])
                if len(prefix_commands) > 8:
                    command_list += f'\n*...and {len(prefix_commands) - 8} more*'
                
                main_embed.add_field(
                    name=f'🎯 Standalone Commands ({len(prefix_commands)})',
                    value=command_list,
                    inline=False
                )
            for cog_name, commands_list in list(cog_commands.items())[:4]:
                command_list = '\n'.join([
                    f'🔹 `!{cmd["name"]}` - {cmd["description"][:40]}{"..." if len(cmd["description"]) > 40 else ""}'
                    + (' 🔇' if cmd["hidden"] else '')
                    + (f'\n    └ *Aliases: {", ".join([f"`{alias}`" for alias in cmd["aliases"]])}*' if cmd["aliases"] else '')
                    for cmd in commands_list[:6]
                ])
                if len(commands_list) > 6:
                    command_list += f'\n*...and {len(commands_list) - 6} more*'
                
                main_embed.add_field(
                    name=f'⚙️ {cog_name} Cog ({len(commands_list)})',
                    value=command_list,
                    inline=False
                )
            if len(cog_commands) > 4:
                remaining_cogs = len(cog_commands) - 4
                remaining_commands = sum(len(commands) for cog, commands in list(cog_commands.items())[4:])
                main_embed.add_field(
                    name='📦 Additional Cogs',
                    value=f'*{remaining_cogs} more cogs with {remaining_commands} additional commands*',
                    inline=False
                )
            
            main_embed.set_footer(
                text=f'Total: {total_commands} prefix commands • 🔇 = Hidden • Use !help for user help',
                icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
            )
            
            await ctx.send(embed=main_embed)
                        
        except Exception as e:
            error_embed = discord.Embed(
                title='❌ List Commands Failed',
                description='An error occurred while listing prefix commands',
                color=0xff4444
            )
            error_embed.add_field(
                name='🔍 Error Details',
                value=f'```{str(e)[:1000]}```',
                inline=False
            )
            error_embed.set_footer(text='Please try again or contact support')
            await ctx.send(embed=error_embed)
            print(f"Error listing commands: {e}")

    @app_commands.command(name='help', description='Show help for slash commands')
    async def help(self, ctx):
        embed = discord.Embed(
            title='🤖 Bot Help Center',
            description='📋 **Available Slash Commands**\n\n*Use `/` followed by the command name to execute*',
            color=0x00ff88
        )
        admin_commands = []
        for command in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input):
            if command.root_parent is not None and command.root_parent.name == "admin" and not isinstance(command, discord.app_commands.Group):
                admin_commands.append(f'🔧 `/{command.qualified_name}`\n└ {command.description}')
        if admin_commands:
            embed.add_field(
                name='🛡️ **Admin Commands**',
                value='*Restricted to server administrators*\n\n' + '\n\n'.join(admin_commands),
                inline=False
            )
        user_commands = []
        for command in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input):
            if command.root_parent is None and not isinstance(command, discord.app_commands.Group):
                user_commands.append(f'👤 `/{command.qualified_name}`\n└ {command.description}')
            elif command.root_parent is not None and command.root_parent.name != "admin" and not isinstance(command, discord.app_commands.Group):
                user_commands.append(f'👤 `/{command.qualified_name}`\n└ {command.description}')
        if user_commands:
            embed.add_field(
                name='🌟 **User Commands**',
                value='*Available to all users*\n\n' + '\n\n'.join(user_commands),
                inline=False
            )
        if not admin_commands and not user_commands:
            embed.add_field(
                name='❌ No Commands Found',
                value='No slash commands are currently available.',
                inline=False
            )
        embed.set_footer(
            text='💡 Tip: Commands are synced automatically • Need more help? Contact an admin',
            icon_url=self.bot.user.avatar.url if self.bot.user.avatar else None
        )
        embed.set_thumbnail(url=self.bot.user.avatar.url if self.bot.user.avatar else None)
        await ctx.response.send_message(embed=embed)
    
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