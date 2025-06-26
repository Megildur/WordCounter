import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
from paginator import ButtonPaginator
import itertools

class Messages(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        async with aiosqlite.connect('message_channels.db') as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS message_channels (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                messages INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id, channel_id)
                )
            ''')
            await db.commit()
        async with aiosqlite.connect('message_user.db') as db:
            await db.execute('''CREATE TABLE IF NOT EXISTS message_user (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                messages INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
                )
            ''')
            await db.commit()

    async def add_msg(self, guild_id, user_id, channel_id) -> None:
        async with aiosqlite.connect('message_channels.db') as db:
            async with db.execute('SELECT messages FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (guild_id, user_id, channel_id)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    await db.execute('INSERT INTO message_channels (guild_id, user_id, channel_id, messages) VALUES (?, ?, ?, ?)', (guild_id, user_id, channel_id, 1))
                else:
                    await db.execute('UPDATE message_channels SET messages = messages + 1 WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (guild_id, user_id, channel_id))
                await db.commit()
        async with aiosqlite.connect('message_user.db') as db:
            async with db.execute('SELECT messages FROM message_user WHERE guild_id = ? AND user_id = ?', (guild_id, user_id)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    await db.execute('INSERT INTO message_user (guild_id, user_id, messages) VALUES (?, ?, ?)', (guild_id, user_id, 1))
                else:
                    await db.execute('UPDATE message_user SET messages = messages + 1 WHERE guild_id = ? AND user_id = ?', (guild_id, user_id))
                await db.commit()

    async def del_msg(self, guild_id, user_id, channel_id) -> None:
        async with aiosqlite.connect('message_channels.db') as db:
            async with db.execute('SELECT messages FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (guild_id, user_id, channel_id)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return
                if row[0] == 1:
                    await db.execute('DELETE FROM message_channels WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (guild_id, user_id, channel_id))
                else:
                    await db.execute('UPDATE message_channels SET messages = messages - 1 WHERE guild_id = ? AND user_id = ? AND channel_id = ?', (guild_id, user_id, channel_id))
                await db.commit()
        async with aiosqlite.connect('message_user.db') as db:
            async with db.execute('SELECT messages FROM message_user WHERE guild_id = ? AND user_id = ?', (guild_id, user_id)) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return
                if row[0] == 1:
                    await db.execute('DELETE FROM message_user WHERE guild_id = ? AND user_id = ?', (guild_id, user_id))
                else:
                    await db.execute('UPDATE message_user SET messages = messages - 1 WHERE guild_id = ? AND user_id = ?', (guild_id, user_id))
                await db.commit()

    message = app_commands.Group(name='message', description='Message commands')

    @message.command(name='leaderboard', description='Shows the message leaderboard')
    @app_commands.describe(channel='The channel to show the leaderboard for')
    async def message_leaderboard(self, interaction: discord.Interaction, channel: discord.TextChannel = None) -> None:
        if channel is None:
            async with aiosqlite.connect('message_user.db') as db:
                async with db.execute('SELECT user_id, messages FROM message_user WHERE guild_id = ? ORDER BY messages DESC', (interaction.guild_id,)) as cursor:
                    rows = await cursor.fetchall()
                    if not rows:
                        embed = discord.Embed(title='Message Leaderboard', description='No messages have been sent yet!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        return
                    
                    # Filter and prepare user data
                    valid_results = []
                    for user_id, messages in rows:
                        user = interaction.guild.get_member(user_id)
                        if user is None:
                            try:
                                user = await self.bot.fetch_user(user_id)
                            except:
                                continue
                        if user:
                            valid_results.append((user, messages))
                    
                    if not valid_results:
                        embed = discord.Embed(title='Message Leaderboard', description='No active users found!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
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
                            title='💬 Message Leaderboard',
                            description='Top message contributors in the server',
                            color=discord.Color.from_str('#af2202')
                        )
                        
                        description = ""
                        for index, (user, messages) in enumerate(page_data, start=start_idx + 1):
                            display_name = user.display_name if hasattr(user, 'display_name') else user.name
                            if index == 1:
                                description += f"🥇 **{display_name}** - {messages:,} messages\n"
                            elif index == 2:
                                description += f"🥈 **{display_name}** - {messages:,} messages\n"
                            elif index == 3:
                                description += f"🥉 **{display_name}** - {messages:,} messages\n"
                            else:
                                description += f"**{index}.** {display_name} - {messages:,} messages\n"
                        
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
            async with aiosqlite.connect('message_channels.db') as db:
                async with db.execute('SELECT user_id, messages FROM message_channels WHERE guild_id = ? AND channel_id = ? ORDER BY messages DESC', (interaction.guild_id, channel.id)) as cursor:
                    rows = await cursor.fetchall()
                    if not rows:
                        embed = discord.Embed(title='Message Leaderboard', description='No messages have been sent yet!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        return
                    
                    # Filter and prepare user data
                    valid_results = []
                    for user_id, messages in rows:
                        user = interaction.guild.get_member(user_id)
                        if user is None:
                            try:
                                user = await self.bot.fetch_user(user_id)
                            except:
                                continue
                        if user:
                            valid_results.append((user, messages))
                    
                    if not valid_results:
                        embed = discord.Embed(title='Message Leaderboard', description='No active users found!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
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
                            title='💬 Message Leaderboard',
                            description=f'Top message contributors in {channel.mention}',
                            color=discord.Color.from_str('#af2202')
                        )
                        
                        description = ""
                        for index, (user, messages) in enumerate(page_data, start=start_idx + 1):
                            display_name = user.display_name if hasattr(user, 'display_name') else user.name
                            if index == 1:
                                description += f"🥇 **{display_name}** - {messages:,} messages\n"
                            elif index == 2:
                                description += f"🥈 **{display_name}** - {messages:,} messages\n"
                            elif index == 3:
                                description += f"🥉 **{display_name}** - {messages:,} messages\n"
                            else:
                                description += f"**{index}.** {display_name} - {messages:,} messages\n"
                        
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

async def setup(bot) -> None:
    await bot.add_cog(Messages(bot))
    print('Messages cog loaded')