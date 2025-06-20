import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
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
                async with db.execute('SELECT user_id, messages FROM message_user ORDER BY messages DESC') as cursor:
                    rows = await cursor.fetchall()
                    if not rows:
                        embed = discord.Embed(title='Message Leaderboard', description='No messages have been sent yet!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        return
                    embed = discord.Embed(title='Message Leaderboard', color=discord.Color.from_str('#af2202'))
                    for i, row in enumerate(rows):
                        user = interaction.guild.get_member(row[0])
                        if user is None:
                            user = await self.bot.fetch_user(row[0])
                        embed.add_field(name=f'{i+1}. {user.name}', value=f'{row[1]} messages', inline=False)
                    await interaction.response.send_message(embed=embed)
        else:
            async with aiosqlite.connect('message_channels.db') as db:
                async with db.execute('SELECT user_id, messages FROM message_channels WHERE guild_id = ? AND channel_id = ? ORDER BY messages DESC', (interaction.guild_id, channel.id)) as cursor:
                    rows = await cursor.fetchall()
                    if not rows:
                        embed = discord.Embed(title='Message Leaderboard', description='No messages have been sent yet!', color=discord.Color.red())
                        await interaction.response.send_message(embed=embed)
                        return
                    embed = discord.Embed(title='Message Leaderboard', color=discord.Color.from_str('#af2202'))
                    for i, row in enumerate(rows):
                        user = interaction.guild.get_member(row[0])
                        if user is None:
                            user = await self.bot.fetch_user(row[0])
                        embed.add_field(name=f'{i+1}. {user.name}', value=f'{row[1]} messages', inline=False)
                    await interaction.response.send_message(embed=embed)

async def setup(bot) -> None:
    await bot.add_cog(Messages(bot))
    print('Messages cog loaded')