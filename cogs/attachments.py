import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
import itertools

class Attachments(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        async with aiosqlite.connect('attachments_channels.db') as db:
            await db.execute(
                '''CREATE TABLE IF NOT EXISTS attachments_channels (
            guild_id INTEGER,
            channel_id INTEGER,
            user_id INTEGER,
            count INTEGER,
            PRIMARY KEY (guild_id, channel_id, user_id)
            )
        ''')
            await db.commit()
        async with aiosqlite.connect('attachments_users.db') as db:
            await db.execute(
                '''CREATE TABLE IF NOT EXISTS attachments_users (
            guild_id INTEGER,
            user_id INTEGER,
            count INTEGER,
            PRIMARY KEY (guild_id, user_id)
            )
        ''')
            await db.commit()

    async def attachment_message(self, message, result) -> None:
        attachment_count = len(message.attachments)
        link_count = sum(1 for word in message.content.split() if word.startswith(('http://', 'https://')))
        total_attachments = attachment_count + link_count
        if total_attachments > 0:
            for channel_id in result:
                if message.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or message.channel.id in channel_id:
                        await self.at_add(message.guild.id, message.channel.id, message.author.id, attachment_count)
                else:
                    if 1 in channel_id or message.channel.parent_id in channel_id:
                        await self.at_add(message.guild.id, message.channel.parent_id, message.author.id, attachment_count)

    async def attachment_message_delete(self, message, result) -> None:
        attachment_count = len(message.attachments)
        link_count = sum(1 for word in message.content.split() if word.startswith(('http://', 'https://')))
        total_attachments = attachment_count + link_count
        if total_attachments > 0:
            for channel_id in result:
                if message.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or message.channel.id in channel_id:
                        await self.at_delete(message.guild.id, message.channel.id, message.author.id, total_attachments)
                else:
                    if 1 in channel_id or message.channel.parent_id in channel_id:
                        await self.at_delete(message.guild.id, message.channel.parent_id, message.author.id, total_attachments)
        
    async def attachment_message_edit(self, before, after, result) -> None:
        before_attachment_count = len(before.message.attachments)
        after_attachment_count = len(after.message.attachments)
        before_link_count = sum(1 for word in before.message.content.split() if word.startswith(('http://', 'https://')))
        after_link_count = sum(1 for word in after.message.content.split() if word.startswith(('http://', 'https://')))
        before_count = before_attachment_count + before_link_count
        after_count = after_attachment_count + after_link_count
        total_attachments = before_attachment_count + after_attachment_count + before_link_count + after_link_count
        if total_attachments > 0:
            for channel_id in result:
                if before.message.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or before.message.channel.id in channel_id:
                        await self.find_dif(before.message.guild.id, before.message.channel.id, before.message.author.id, before_count, after_count)
                else:
                    if 1 in channel_id or before.message.channel.parent_id in channel_id:
                        await self.find_dif(before.message.guild.id, before.message.channel.parent_id, before.message.author.id, before_count, after_count)

    async def find_dif(self, guild_id, channel_id, user_id, before_count, after_count) -> None:
        if before_count > after_count:
            await self.at_delete(guild_id, channel_id, user_id, before_count - after_count)
        elif before_count < after_count:
            await self.at_add(guild_id, channel_id, user_id, after_count - before_count)
        elif before_count == after_count:
            return

    async def at_add(self, guild_id, channel_id, user_id, count) -> None:
        async with aiosqlite.connect('attachments_channels.db') as db:
            async with db.execute('SELECT count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?', (guild_id, channel_id, user_id)) as cursor:
                result = await cursor.fetchone()
                if result is None:
                    await db.execute('INSERT INTO attachments_channels (guild_id, channel_id, user_id, count) VALUES (?, ?, ?, ?)', (guild_id, channel_id, user_id, count))
                else:
                    await db.execute('UPDATE attachments_channels SET count = count + ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?', (count + result, guild_id, channel_id, user_id))
                await db.commit()
        async with aiosqlite.connect('attachments_users.db') as db:
            async with db.execute('SELECT count FROM attachments_users WHERE guild_id = ? AND user_id = ?', (guild_id, user_id)) as cursor:
                result = await cursor.fetchone()
                if result is None:
                    await db.execute('INSERT INTO attachments_users (guild_id, user_id, count) VALUES (?, ?, ?)', (guild_id, user_id, count))
                else:
                    await db.execute('UPDATE attachments_users SET count = count + ? WHERE guild_id = ? AND user_id = ?', (count + result, guild_id, user_id))
                await db.commit()

    async def at_delete(self, guild_id, channel_id, user_id, count) -> None:
        async with aiosqlite.connect('attachments_channels.db') as db:
            async with db.execute('SELECT count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? AND user_id = ?', (guild_id, channel_id, user_id)) as cursor:
                result = await cursor.fetchone()
                if result is None:
                    return
                else:
                    await db.execute('UPDATE attachments_channels SET count = count - ? WHERE guild_id = ? AND channel_id = ? AND user_id = ?', (count - result, guild_id, channel_id, user_id))
                    await db.commit()
        async with aiosqlite.connect('attachments_users.db') as db:
            async with db.execute('SELECT count FROM attachments_users WHERE guild_id = ? AND user_id = ?', (guild_id, user_id)) as cursor:
                result = await cursor.fetchone()
                if result is None:
                    return
                else:
                    await db.execute('UPDATE attachments_users SET count = count - ? WHERE guild_id = ? AND user_id = ?', (count - result, guild_id, user_id))
                    await db.commit()

    attachment = app_commands.Group(name='attachment', description='Attachment commands')

    @attachment.command(name='leaderboard', description='Shows the attachment leaderboard')
    @app_commands.describe(channel=f'The channel to show the leaderboard for')
    async def attachment_leaderboard(self, interaction: discord.Interaction, channel: discord.TextChannel = None) -> None:
        if channel is None:
            async with aiosqlite.connect('attachments_users.db') as db:
                async with db.execute('SELECT user_id, count FROM attachments_users ORDER BY count DESC LIMIT 10') as cursor:
                    result = await cursor.fetchall()
                    if result:
                        embed = discord.Embed(title='Attachment Leaderboard', color=discord.Color.blue())
                        for index, (user_id, count) in enumerate(result, start=1):
                            user = interaction.guild.get_member(user_id)
                            if user:
                                embed.add_field(name=f'{index}. {user.display_name}', value=f'{count} attachments', inline=False)
                        await interaction.response.send_message(embed=embed)
                    else:
                        embed = discord.Embed(title='Attachment Leaderboard', description='No users found.', color=discord.Color.from_str('#af2202'))
                        await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            async with aiosqlite.connect('attachments_channels.db') as db:
                async with db.execute('SELECT user_id, count FROM attachments_channels WHERE guild_id = ? AND channel_id = ? ORDER BY count DESC LIMIT 10', (interaction.guild.id, channel.id)) as cursor:
                    result = await cursor.fetchall()
                    if result:
                        embed = discord.Embed(title='Attachment Leaderboard', color=discord.Color.blue())
                        for index, (user_id, count) in enumerate(result, start=1):
                            user = interaction.guild.get_member(user_id)
                            if user:
                                embed.add_field(name=f'{index}. {user.display_name}', value=f'{count} attachments', inline=False)
                        await interaction.response.send_message(embed=embed)
                    else:
                        embed = discord.Embed(title='Attachment Leaderboard', description='No users found.', color=discord.Color.from_str('#af2202'))
                        await interaction.response.send_message(embed=embed, ephemeral=True)
        
async def setup(bot) -> None:
    await bot.add_cog(Attachments(bot))
    print('Attachments cog loaded')