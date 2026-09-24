import discord
from discord.ext import commands
from discord import app_commands
from cogs.utils.components import (
    SUCCESS_COLOR,
    ERROR_COLOR,
    WARNING_COLOR,
    create_v2_view,
    error_view,
)

allowed_guilds = [1406313376279298088]


class SyncCog(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        print("SyncCog loaded")

    @commands.command(name='sync', description='Syncs the bot', hidden=True)
    @commands.is_owner()
    async def sync(self, ctx) -> None:
        try:
            initial_view = create_v2_view(
                title='🔄 Command Sync',
                description='Starting global command synchronization...',
                footer='This may take a few moments',
                color=WARNING_COLOR,
            )
            await ctx.send(view=initial_view)
            synced = await self.bot.tree.sync(guild=None)

            fields = []
            if synced:
                command_list = '\n'.join([f'• `/{command.name}`' for command in synced])
                fields.append(
                    (
                        '📝 Synced Commands',
                        command_list
                        if len(command_list) < 1024
                        else f'{command_list[:1000]}...\n*+{len(synced) - command_list[:1000].count("•")} more*',
                    )
                )

            success_view = create_v2_view(
                title='✅ Sync Successful',
                description=f'**{len(synced)} commands** have been synchronized globally.',
                fields=fields,
                footer='All commands are now available as slash commands • Sync completed',
                thumbnail_url=self.bot.user.display_avatar.url if self.bot.user else None,
                color=SUCCESS_COLOR,
            )
            await ctx.send(view=success_view)
            print(f"Synced {len(synced)} commands globally")
            for command in synced:
                print(f"  - {command.name}")

        except discord.HTTPException as e:
            err_view = create_v2_view(
                title='❌ HTTP Error',
                description='Failed to sync commands due to an HTTP error.',
                fields=[('🔍 Error Details', f'```{str(e)[:1000]}```')],
                footer='Try again later or contact support',
                color=ERROR_COLOR,
            )
            await ctx.send(view=err_view)
            print(f"HTTP Error during sync: {e}")

        except Exception as e:
            err_view = create_v2_view(
                title='⚠️ Unexpected Error',
                description='An unexpected error occurred during sync.',
                fields=[('🔍 Error Details', f'```{str(e)[:1000]}```')],
                footer='Please report this error to the developer',
                color=ERROR_COLOR,
            )
            await ctx.send(view=err_view)
            print(f"Unexpected error during sync: {e}")

    @commands.command(name='clear', description='Clears all commands from the tree', hidden=True)
    @commands.is_owner()
    async def clear(self, ctx) -> None:
        try:
            initial_view = create_v2_view(
                title='🗑️ Clearing Commands',
                description='Removing all commands from the command tree...',
                footer='This will remove all slash commands',
                color=WARNING_COLOR,
            )
            await ctx.send(view=initial_view)
            before_count = len(ctx.bot.tree.get_commands())
            ctx.bot.tree.clear_commands(guild=None)

            if before_count > 0:
                fields = [
                    (
                        '📊 Summary',
                        f'• **{before_count}** commands removed\n• Command tree is now empty\n• Users will no longer see slash commands',
                    )
                ]
            else:
                fields = [('ℹ️ Note', 'Command tree was already empty')]

            success_view = create_v2_view(
                title='🧹 Commands Cleared',
                description=f'Successfully removed **{before_count} commands** from the command tree.',
                fields=fields,
                footer='Use !wcsync to re-add commands to the tree',
                thumbnail_url=self.bot.user.display_avatar.url if self.bot.user else None,
                color=SUCCESS_COLOR,
            )
            await ctx.send(view=success_view)
            print(f"Cleared {before_count} commands from tree")

        except Exception as e:
            err_view = create_v2_view(
                title='❌ Clear Failed',
                description='An error occurred while clearing commands.',
                fields=[('🔍 Error Details', f'```{str(e)[:1000]}```')],
                footer='Please try again or contact support',
                color=ERROR_COLOR,
            )
            await ctx.send(view=err_view)
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
                        'hidden': command.hidden,
                    })
            cog_commands = {}
            for cog_name, cog in self.bot.cogs.items():
                cog_command_list = []
                for command in cog.get_commands():
                    cog_command_list.append({
                        'name': command.name,
                        'description': command.description or command.brief or 'No description',
                        'aliases': command.aliases,
                        'hidden': command.hidden,
                    })
                if cog_command_list:
                    cog_commands[cog_name] = cog_command_list

            total_commands = len(prefix_commands) + sum(len(cmds) for cmds in cog_commands.values())
            if total_commands == 0:
                empty_view = create_v2_view(
                    title='📋 Prefix Commands Status',
                    description='🚫 **No prefix commands found**',
                    fields=[('💡 Note', 'No prefix commands are currently loaded in any cogs')],
                    footer='Only showing prefix commands (not slash commands)',
                    color=WARNING_COLOR,
                )
                await ctx.send(view=empty_view)
                return

            fields = []
            if prefix_commands:
                command_list = '\n'.join([
                    f'🔸 `!wc{cmd["name"]}` - {cmd["description"][:45]}{"..." if len(cmd["description"]) > 45 else ""}'
                    + (' 🔇' if cmd["hidden"] else '')
                    + (f'\n    └ *Aliases: {", ".join([f"`{alias}`" for alias in cmd["aliases"]])}*' if cmd["aliases"] else '')
                    for cmd in prefix_commands[:8]
                ])
                if len(prefix_commands) > 8:
                    command_list += f'\n*...and {len(prefix_commands) - 8} more*'
                fields.append((f'🎯 Standalone Commands ({len(prefix_commands)})', command_list))

            for cog_name, commands_list in list(cog_commands.items())[:4]:
                command_list = '\n'.join([
                    f'🔹 `!wc{cmd["name"]}` - {cmd["description"][:40]}{"..." if len(cmd["description"]) > 40 else ""}'
                    + (' 🔇' if cmd["hidden"] else '')
                    + (f'\n    └ *Aliases: {", ".join([f"`{alias}`" for alias in cmd["aliases"]])}*' if cmd["aliases"] else '')
                    for cmd in commands_list[:6]
                ])
                if len(commands_list) > 6:
                    command_list += f'\n*...and {len(commands_list) - 6} more*'
                fields.append((f'⚙️ {cog_name} Cog ({len(commands_list)})', command_list))

            if len(cog_commands) > 4:
                remaining_cogs = len(cog_commands) - 4
                remaining_commands = sum(len(cmds) for _, cmds in list(cog_commands.items())[4:])
                fields.append(('📦 Additional Cogs', f'*{remaining_cogs} more cogs with {remaining_commands} additional commands*'))

            main_view = create_v2_view(
                title='📋 Prefix Commands Overview',
                description=f'**{total_commands} prefix commands** currently loaded\n\n*These are traditional `!wc` commands, not slash commands*',
                fields=fields,
                footer=f'Total: {total_commands} prefix commands • 🔇 = Hidden • Use /help for user help',
                thumbnail_url=self.bot.user.display_avatar.url if self.bot.user else None,
                color=SUCCESS_COLOR,
            )
            await ctx.send(view=main_view)

        except Exception as e:
            err_view = create_v2_view(
                title='❌ List Commands Failed',
                description='An error occurred while listing prefix commands.',
                fields=[('🔍 Error Details', f'```{str(e)[:1000]}```')],
                footer='Please try again or contact support',
                color=ERROR_COLOR,
            )
            await ctx.send(view=err_view)
            print(f"Error listing commands: {e}")

    @app_commands.command(name='help', description='Show help for slash commands')
    async def help(self, interaction: discord.Interaction) -> None:
        admin_commands = []
        user_commands = []

        for command in self.bot.tree.walk_commands(type=discord.AppCommandType.chat_input):
            if isinstance(command, discord.app_commands.Group):
                continue
            default_perms = getattr(command, 'default_permissions', None) or (
                getattr(command.root_parent, 'default_permissions', None) if command.root_parent else None
            )
            if default_perms is not None and default_perms.value != 0:
                admin_commands.append(f'🔧 `/{command.qualified_name}`\n└ {command.description}')
            else:
                user_commands.append(f'👤 `/{command.qualified_name}`\n└ {command.description}')

        fields = []
        if admin_commands:
            fields.append(
                (
                    '🛡️ Admin & Moderation Commands',
                    '*Restricted to server moderators/administrators*\n\n' + '\n\n'.join(admin_commands),
                )
            )
        if user_commands:
            fields.append(
                (
                    '🌟 User Commands',
                    '*Available to all users*\n\n' + '\n\n'.join(user_commands),
                )
            )
        if not admin_commands and not user_commands:
            fields.append(('❌ No Commands Found', 'No slash commands are currently available.'))

        help_view = create_v2_view(
            title='🤖 Bot Help Center',
            description='📋 **Available Slash Commands**\n\n*Use `/` followed by the command name to execute*',
            fields=fields,
            footer='💡 Tip: Use /settings to configure channels, categories & keywords',
            thumbnail_url=self.bot.user.display_avatar.url if self.bot.user else None,
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(view=help_view)

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        if ctx.guild and ctx.guild.id not in allowed_guilds and isinstance(error, commands.NotOwner):
            print(f"Error: this user tried to use an owner command in another guild {ctx.author.name} in {ctx.guild.name}:{ctx.guild.id}")
            return
        if ctx.guild and ctx.guild.id not in allowed_guilds:
            print(f"Error: this user tried to use a command in another guild {ctx.author.name} in {ctx.guild.name}:{ctx.guild.id}")
            return
        if isinstance(error, commands.CommandNotFound):
            await ctx.send(view=error_view("Invalid command. Use `/help` for a list of available commands."))
        elif isinstance(error, commands.MissingPermissions):
            await ctx.send(view=error_view("You don't have the required permissions to use this command."))
        elif isinstance(error, commands.NotOwner):
            print(f"Error: this user tried to use an owner command {ctx.author.name}")
            await ctx.send(view=error_view("You cannot use this command because you are not the owner of this bot."))
        else:
            print(f"Error: {error}")
            await ctx.send(view=error_view(f"An error occurred: `{error}`"))


async def setup(bot) -> None:
    await bot.add_cog(SyncCog(bot))