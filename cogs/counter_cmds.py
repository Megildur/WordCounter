import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
from typing import Literal, Optional
from paginator import ButtonPaginator

class Counter_Cmds(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        print('Counter_Cmds cog loaded')

    count = app_commands.Group(name='count', description='Commands to manage the settings of the bot for the server', default_permissions=discord.Permissions(manage_guild=True))

    channel = app_commands.Group(name='channel', description='Commands to add/remove channels for recording word count.', parent=count)

    server = app_commands.Group(name='server', description='Commands to enable or disable the recording of the word count for the entire server.', parent=count)

    words = app_commands.Group(name='words', description='Commands to view the current word count stats of the server.')

    @server.command(name='set', description='Enable/disable the recording of the word count of every channel in the server')
    @app_commands.describe(
        action='Enable or disable the bot to record the word count of every channel in the server'
    )
    async def count_server(self, interaction, action: Literal['Enable', 'Disable']) -> None:
        if action == 'Enable':
            async with aiosqlite.connect('channels.db') as db:
                async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        await db.execute('INSERT INTO channels (guild_id, channel_id) VALUES (?, ?)', (interaction.guild_id, 1))
                        await db.commit()
                        embed = discord.Embed(title='Success', description='Word count will now be recorded on the whole server', color=discord.Color.green())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    else:
                        for channel_id in result:
                            if 1 in channel_id:
                                embed = discord.Embed(title='Error', description='Word count is already enabled on the whole server', color=discord.Color.red())
                                await interaction.response.send_message(embed=embed, ephemeral=True)
                            elif 1 not in channel_id:
                                embed = discord.Embed(title='Error', description='Word count is already enabled for specific channels. Would you like to change it to monitoring the entire server?', color=discord.Color.red())
                                view = EConfirmView(self.bot)
                                await interaction.response.send_message(view=view, embed=embed, ephemeral=True)
        if action == 'Disable':
            async with aiosqlite.connect('channels.db') as db:
                async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        embed = discord.Embed(title='Error', description='Word count is already disabled on the whole server', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    else:
                        for channel_id in result:
                            if 1 in channel_id:
                                await db.execute('DELETE FROM channels WHERE guild_id = ?', (interaction.guild_id,))
                                await db.commit()
                                async with aiosqlite.connect('ignore.db') as db:
                                     async with db.execute('SELECT channel_id FROM ignore WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                                        result = await cursor.fetchall()
                                        if result:
                                            await db.execute('DELETE FROM ignore WHERE guild_id = ?', (interaction.guild_id,))
                                            await db.commit()
                                embed = discord.Embed(title='Success', description='Word count will no longer be recorded on the whole server', color=discord.Color.green())
                                await interaction.response.send_message(embed=embed, ephemeral=True)
                            elif 1 not in channel_id:
                                embed = discord.Embed(title='Error', description='Word count is enabled for specific channels. Would you like to disable it for the entire server?', color=discord.Color.red())
                                view = DConfirmView(self.bot)
                                await interaction.response.send_message(view=view, embed=embed, ephemeral=True)
        
    @channel.command(name='set', description='Set channels to record the word count in.(Cant be used if count is enabled in the entire server.)')
    @app_commands.describe(channel='The channel to enable word count in')
    async def set_channel(self, interaction, channel: discord.TextChannel) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
                if channel.id in [row[0] for row in result]:
                    embed = discord.Embed(title='Error', description='Word count is alredy being recorded in this channel.', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                if 1 in [row[0] for row in result]:
                    embed = discord.Embed(title='Error', description='Word count is already enabled on the whole server.', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                if channel.id and 1 not in [row[0] for row in result]:
                    await db.execute('INSERT INTO channels (guild_id, channel_id) VALUES (?, ?)', (interaction.guild_id, channel.id))
                    await db.commit()
                    embed = discord.Embed(title='Success', description=f'word count is now being recorded in {channel.mention}!', color=discord.Color.green())
                    await interaction.response.send_message(embed=embed, ephemeral=True)

    @channel.command(name='remove', description='Remove channels from recording the word count.(Cant be used if count is enabled in entire server.)')
    @app_commands.describe(channel='The channel to remove from counting')
    async def remove_channel(self, interaction, channel: discord.TextChannel):
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
                if channel.id in [row[0] for row in result]:
                    await db.execute('DELETE FROM channels WHERE guild_id = ? AND channel_id = ?', (interaction.guild_id, channel.id))
                    await db.commit()
                    embed = discord.Embed(title='Success', description=f'The word count is no longer being recorded in {channel.mention}!', color=discord.Color.green())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                if 1 in [row[0] for row in result]:
                    embed = discord.Embed(title='Error', description='Word count is already enabled on the whole server.', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                if channel.id and 1 not in [row[0] for row in result]:
                    embed = discord.Embed(title='Error', description='Word count is not being recorded in this channel', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)

    @words.command(name='leaderboard', description='Shows the word count leaderboard of the server')
    @app_commands.describe(channel='The channel to show the leaderboard of members in')
    async def leaderboard(self, interaction, channel: Optional[discord.TextChannel] = None) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
                if not result:
                    embed = discord.Embed(title='Error', description='Word count is not enabled on this server', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
        
        if channel is None:
            async with aiosqlite.connect('server.db') as db:
                async with db.execute('SELECT user_id, count FROM server WHERE guild_id = ? ORDER BY count DESC', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        embed = discord.Embed(title='Error', description='No one has said any words in this server yet.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    
                    # Filter out users who are no longer in the guild
                    valid_results = []
                    for user_id, count in result:
                        user = interaction.guild.get_member(user_id)
                        if user is not None:
                            valid_results.append((user, count))
                    
                    if not valid_results:
                        embed = discord.Embed(title='Error', description='No active users found in this server.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    
                    # Create paginated embeds
                    embeds = []
                    users_per_page = 10
                    total_pages = (len(valid_results) + users_per_page - 1) // users_per_page
                    
                    for page_num in range(total_pages):
                        start_idx = page_num * users_per_page
                        end_idx = min(start_idx + users_per_page, len(valid_results))
                        page_data = valid_results[start_idx:end_idx]
                        
                        embed = discord.Embed(
                            title='📝 Word Count Leaderboard',
                            description='Top word contributors in the server',
                            color=discord.Color.from_str('#af2202')
                        )
                        
                        description = ""
                        for index, (user, count) in enumerate(page_data, start=start_idx + 1):
                            if index == 1:
                                description += f"🥇 **{user.display_name}** - {count:,} words\n"
                            elif index == 2:
                                description += f"🥈 **{user.display_name}** - {count:,} words\n"
                            elif index == 3:
                                description += f"🥉 **{user.display_name}** - {count:,} words\n"
                            else:
                                description += f"**{index}.** {user.display_name} - {count:,} words\n"
                        
                        embed.description = f"{embed.description}\n\n{description}"
                        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Total users: {len(valid_results)}")
                        embeds.append(embed)
                    
                    # Handle single page vs multiple pages
                    if len(embeds) == 1:
                        await interaction.response.send_message(embed=embeds[0])
                    else:
                        paginator = ButtonPaginator.create_standard_paginator(
                            embeds,
                            author_id=interaction.user.id,
                            timeout=180.0
                        )
                        await paginator.start(interaction)
        else:
            async with aiosqlite.connect('counter.db') as db:
                async with db.execute('SELECT user_id, count FROM counters WHERE guild_id = ? AND channel_id = ? ORDER BY count DESC', (interaction.guild_id, channel.id)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        embed = discord.Embed(title='Error', description='No one has said any words in this channel yet.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    
                    # Filter out users who are no longer in the guild
                    valid_results = []
                    for user_id, count in result:
                        user = interaction.guild.get_member(user_id)
                        if user is not None:
                            valid_results.append((user, count))
                    
                    if not valid_results:
                        embed = discord.Embed(title='Error', description='No active users found in this channel.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    
                    # Create paginated embeds for channel-specific leaderboard
                    embeds = []
                    users_per_page = 10
                    total_pages = (len(valid_results) + users_per_page - 1) // users_per_page
                    
                    for page_num in range(total_pages):
                        start_idx = page_num * users_per_page
                        end_idx = min(start_idx + users_per_page, len(valid_results))
                        page_data = valid_results[start_idx:end_idx]
                        
                        embed = discord.Embed(
                            title='📝 Word Count Leaderboard',
                            description=f'Top word contributors in {channel.mention}',
                            color=discord.Color.from_str('#af2202')
                        )
                        
                        description = ""
                        for index, (user, count) in enumerate(page_data, start=start_idx + 1):
                            if index == 1:
                                description += f"🥇 **{user.display_name}** - {count:,} words\n"
                            elif index == 2:
                                description += f"🥈 **{user.display_name}** - {count:,} words\n"
                            elif index == 3:
                                description += f"🥉 **{user.display_name}** - {count:,} words\n"
                            else:
                                description += f"**{index}.** {user.display_name} - {count:,} words\n"
                        
                        embed.description = f"{embed.description}\n\n{description}"
                        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Total users: {len(valid_results)}")
                        embeds.append(embed)
                    
                    # Handle single page vs multiple pages
                    if len(embeds) == 1:
                        await interaction.response.send_message(embed=embeds[0])
                    else:
                        paginator = ButtonPaginator.create_standard_paginator(
                            embeds,
                            author_id=interaction.user.id,
                            timeout=180.0
                        )
                        await paginator.start(interaction)

    @count.command(name='reset', description='Resets the word count of a user(for a single chanel or every channel) or for whole server')
    @app_commands.describe(user='The user to reset the word count of', channel='The channel to reset the word count of user in')
    async def reset_count(self, interaction, user: Optional[discord.Member] = None, channel: Optional[discord.TextChannel] = None) -> None:            
        if user is None:
            if channel is None:
                async with aiosqlite.connect('server.db') as db:
                    async with db.execute('SELECT user_id FROM server WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                        result = await cursor.fetchall()
                        if not result:
                            embed = discord.Embed(title='Error', description='No messages have been recorded in this server.', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        await db.execute('UPDATE server SET count = ? WHERE guild_id = ?', (0, interaction.guild_id))
                        await db.commit()
                async with aiosqlite.connect('counter.db') as db:
                    async with db.execute('SELECT user_id FROM counters WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                        result = await cursor.fetchall()  
                        if result:
                            await db.execute('UPDATE counters SET count = ? WHERE guild_id = ?', (0, interaction.guild_id))
                            await db.commit()
                embed = discord.Embed(title='Success', description='The word count of all users has been reset for the whole server!', color=discord.Color.green())
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                async with aiosqlite.connect('counter.db') as db:
                    async with db.execute('SELECT user_id FROM counters WHERE guild_id = ? AND channel_id = ?', (interaction.guild_id, channel.id)) as cursor:
                        result = await cursor.fetchall()
                        if not result:
                            embed = discord.Embed(title='Error', description=f'No messages have been recorded in {channel.mention} for this server.', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        await db.execute('UPDATE counters SET count = ? WHERE guild_id = ? AND channel_id = ?', (0, interaction.guild_id, channel.id))
                        await db.commit()
                embed = discord.Embed(title='Success', description=f'The word count of all users has been reset for {channel.mention}!', color=discord.Color.green())
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            if user.bot:
                embed = discord.Embed(title='Error', description='You cannot reset the word count of a bot.', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            if channel is None:
                async with aiosqlite.connect('server.db') as db:
                    async with db.execute('SELECT count FROM server WHERE guild_id = ? AND user_id = ?', (interaction.guild_id, user.id)) as cursor:
                        result = await cursor.fetchone()
                        if result is None:
                            embed = discord.Embed(title='Error', description=f'{user.mention} has no words recorded in this server.', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        await db.execute('UPDATE server SET count = ? WHERE guild_id = ? AND user_id = ?', (0, interaction.guild_id, user.id))
                        await db.commit()
                async with aiosqlite.connect('counter.db') as db:
                    await db.execute('UPDATE counters SET count = ? WHERE guild_id = ? AND user_id = ?', (0, interaction.guild_id, user.id))
                    await db.commit()
                embed = discord.Embed(title='Success', description=f'The word count of {user.name} has been reset!', color=discord.Color.green())
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                async with aiosqlite.connect('counter.db') as db:
                    async with db.execute('SELECT count FROM counters WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (interaction.guild_id, user.id, channel.id)) as cursor:
                        result = await cursor.fetchone()
                        if result is None:
                            embed = discord.Embed(title='Error', description=f'{user.mention} has no words recorded in {channel.mention}', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        await db.execute('UPDATE counters SET count = ? WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (0, interaction.guild_id, user.id, channel.id))
                        await db.commit()
                count = result[0]
                async with aiosqlite.connect('server.db') as db:
                    async with db.execute('SELECT count FROM server WHERE guild_id = ? AND user_id = ?', (interaction.guild_id, user.id)) as cursor:
                        results = await cursor.fetchone()
                        update = results[0] - count
                        await db.execute('UPDATE server SET count = ? WHERE guild_id = ? AND user_id = ?', (update, interaction.guild_id, user.id))
                        await db.commit()
                embed = discord.Embed(title='Success', description=f'The word count of {user.mention} has been reset for {channel.mention}!', color=discord.Color.green())
                await interaction.response.send_message(embed=embed, ephemeral=True)

    @server.command(name='settings', description='Shows the current settings for the server')
    async def current_settings(self, interaction, make_private: Literal['Yes'] = None) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
                if not result:
                    embed = discord.Embed(title='Error', description='Word count is not enabled on this server', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                for channel_id in result:
                    if 1 in channel_id:
                        embed = discord.Embed(title='Current Settings:', description='Word count is being recorded for the whole server.', color=discord.Color.from_str('#af2202'))
                        async with aiosqlite.connect('ignore.db') as db:
                            async with db.execute('SELECT channel_id FROM ignore WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                                iresult = await cursor.fetchall()
                                if iresult:
                                    channels = [f'<#{channel_id[0]}>' for channel_id in iresult]
                                    embed.add_field(name='Ignored Channels:', value=', '.join(channels), inline=False)
                    else:
                        channels = [f'<#{channel_id[0]}>' for channel_id in result]
                        embed = discord.Embed(title='Current Settings:', description="The following channels have the word count being recorded:\n" + "\n".join(channels), color=discord.Color.from_str('#af2202'))
                    if make_private is None:
                        await interaction.response.send_message(embed=embed)
                    elif make_private == 'Yes':
                        await interaction.response.send_message(embed=embed, ephemeral=True)

    @channel.command(name='ignore', description='Ignores a channel from word counting when set to whole server')
    @app_commands.describe(action='add or remove channel', channel='The channel to ignore')
    async def ignore_channel(self, interaction, action: Literal["Add", "Remove"], channel: discord.TextChannel) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
                if not result:
                    embed = discord.Embed(title='Error', description='Word count is not enabled on this server', color=discord.Color.red())
                    await interaction.response.send_message(embed=embed, ephemeral=True)
                    return
                if result:
                    for channel_id in result:
                        if 1 not in channel_id:
                            embed = discord.Embed(title='Error', description='Word count is not enabled for the whole server', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
        if action == 'Add':
            async with aiosqlite.connect('ignore.db') as db:
                async with db.execute('SELECT channel_id FROM ignore WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        await db.execute('INSERT INTO ignore (guild_id, channel_id) VALUES (?, ?)', (interaction.guild_id, channel.id))
                        await db.commit()
                        embed = discord.Embed(title='Success', description=f'The channel {channel.mention} has been added to the ignore list.', color=discord.Color.green())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                    else:
                        for channel_id in result:
                            if channel.id in channel_id:
                                embed = discord.Embed(title='Error', description=f'{channel.mention} is already being ignored.', color=discord.Color.red())
                                await interaction.response.send_message(embed=embed, ephemeral=True)
                                return
                            await db.execute('INSERT INTO ignore (guild_id, channel_id) VALUES (?, ?)', (interaction.guild_id, channel.id))
                            await db.commit()
                            embed = discord.Embed(title='Success', description=f'{channel.mention} has been added to the ignored channels.', color=discord.Color.green())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
        elif action == 'Remove':
            async with aiosqlite.connect('ignore.db') as db:
                async with db.execute('SELECT channel_id FROM ignore WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
                    if not result:
                        embed = discord.Embed(title='Error', description='There are no ignored channels.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                    for channel_id in result:
                        if channel.id in channel_id:
                            await db.execute('DELETE FROM ignore WHERE guild_id = ? AND channel_id = ?', (interaction.guild_id, channel.id))
                            await db.commit()
                            embed = discord.Embed(title='Success', description=f'{channel.mention} has been removed from the ignored channels.', color=discord.Color.green())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        embed = discord.Embed(title='Error', description=f'{channel.mention} is not being ignored.', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed, ephemeral=True)
                        return
                        
class EConfirmView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def econfirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM channels WHERE guild_id = ?', (interaction.guild_id,))
            await db.execute('INSERT INTO channels (guild_id, channel_id) VALUES (?, ?)', (interaction.guild_id, 1))
            await db.commit()
            embed = discord.Embed(title='Success', description='Word count is now being recorded for the whole server!', color=discord.Color.green())
            await interaction.response.edit_message(embed=embed, view=None)
            
    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def ecancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title='Cancelled', description='Word cout still being recorded only in the specified channels!', color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

class DConfirmView(discord.ui.View):
    def __init__(self, bot) -> None:
        super().__init__()
        self.bot = bot

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def dconfirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async with aiosqlite.connect('channels.db') as db:
            await db.execute('DELETE FROM channels WHERE guild_id = ?', (interaction.guild_id,))
            await db.commit()
            embed = discord.Embed(title='Success', description='Word count is no longer being recorded for the whole server!', color=discord.Color.green())
            await interaction.response.edit_message(embed=embed, view=None)
            
    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def dcancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        embed = discord.Embed(title='Cancelled', description='Word count is still being recorded only in the specified channels!', color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=None)

async def setup(bot) -> None:
    await bot.add_cog(Counter_Cmds(bot))