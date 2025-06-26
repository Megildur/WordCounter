import discord
from discord.ext import commands
from discord import app_commands
import aiosqlite
from typing import Optional
import itertools

class Keyword(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        async with aiosqlite.connect('keyword.db') as db:
          await db.execute('''CREATE TABLE IF NOT EXISTS keyword (
              guild_id INTEGER NOT NULL,
              keyword TEXT NOT NULL,
              PRIMARY KEY (guild_id, keyword)
              )'''
          )
          await db.commit()

        async with aiosqlite.connect('keyword_channel.db') as db:
          await db.execute('''CREATE TABLE IF NOT EXISTS keyword_channel (
              guild_id INTEGER NOT NULL,
              channel_id INTEGER NOT NULL,
              keyword TEXT NOT NULL,
              user_id INTEGER NOT NULL,
              count INTEGER NOT NULL,
              PRIMARY KEY (guild_id, channel_id, keyword, user_id)
              )'''
          )
          await db.commit()

        async with aiosqlite.connect('keyword_user.db') as db:
          await db.execute('''CREATE TABLE IF NOT EXISTS keyword_user (
              guild_id INTEGER NOT NULL,
              user_id INTEGER NOT NULL,
              keyword TEXT NOT NULL,
              count INTEGER NOT NULL,
              PRIMARY KEY (guild_id, user_id, keyword)
              )'''
          )
          await db.commit()
            
    async def keyword_message(self, message, result) -> None:
            async with aiosqlite.connect('keyword.db') as db:
                async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (message.guild.id,)) as cursor:
                    kresult = await cursor.fetchall()
            for channel_id in result:
                if message.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or message.channel.id in channel_id:
                        for row in kresult:
                            words = message.content.lower().split()
                            word_count = words.count(row[0].lower())
                            if word_count > 0:
                                await self.update_kw(row[0], word_count, message.guild.id, message.channel.id, message.author.id)
                else:
                    if 1 in channel_id or message.channel.parent_id in channel_id:
                        for row in kresult:
                            words = message.content.lower().split()
                            word_count = words.count(row[0].lower())
                            if word_count > 0:
                                await self.update_kw(row[0], word_count, message.guild.id, message.channel.parent_id, message.author.id)

    async def keyword_delete(self, message, result) -> None:
        async with aiosqlite.connect('keyword.db') as db:
            async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (message.guild.id,)) as cursor:
                kresult = await cursor.fetchall()
            for channel_id in result:
                if message.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or message.channel.id in channel_id:
                        for row in kresult:
                            words = message.content.lower().split()
                            word_count = words.count(row[0].lower())
                            if word_count > 0:
                                await self.remove_kw(row[0], word_count, message.guild.id, message.channel.id, message.author.id)
                else:
                    if 1 in channel_id or message.channel.parent_id in channel_id:
                        for row in kresult:
                            words = message.content.lower().split()
                            word_count = words.count(row[0].lower())
                            if word_count > 0:
                                await self.remove_kw(row[0], word_count, message.guild.id, message.channel.parent_id, message.author.id)

    async def keyword_edit(self, before, after, result) -> None:
        async with aiosqlite.connect('keyword.db') as db:
            async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (before.guild.id,)) as cursor:
                kresult = await cursor.fetchall()
            for channel_id in result:
                if before.channel.type != discord.ChannelType.public_thread:
                    if 1 in channel_id or before.channel.id in channel_id:
                        for row in kresult:
                            bwords = before.content.lower().split()
                            bword_count = bwords.count(row[0].lower())
                            awords = after.content.lower().split()
                            aword_count = awords.count(row[0].lower())
                            if bword_count > 0 or aword_count > 0 and bword_count != aword_count:
                                await self.find_dif(row[0], bword_count, aword_count, before.guild.id, before.channel.id, before.author.id)
                else:
                    if 1 in channel_id or before.channel.parent_id in channel_id:
                        for row in kresult:
                            bwords = before.content.lower().split()
                            bword_count = bwords.count(row[0].lower())
                            awords = after.content.lower().split()
                            aword_count = awords.count(row[0].lower())
                            if bword_count > 0 or aword_count > 0 and bword_count != aword_count:
                                await self.find_dif(row[0], bword_count, aword_count, before.guild.id, before.channel.parent_id, before.author.id)

    async def find_dif(self, keyword, bword_count, aword_count, guild_id, channel_id, user_id) -> None:
        if bword_count > aword_count:
            await self.remove_kw(keyword, bword_count - aword_count, guild_id, channel_id, user_id)
        elif aword_count > bword_count:
            await self.update_kw(keyword, aword_count - bword_count, guild_id, channel_id, user_id)

    async def remove_kw(self, keyword, word_count, guild_id, channel_id, user_id) -> None:
        async with aiosqlite.connect('keyword_channel.db') as db:
            async with db.execute('SELECT count FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?', (guild_id, channel_id, keyword, user_id)) as cursor:
                result = await cursor.fetchone()
                if result:
                    count = result[0]
                    if count > 0:
                        await db.execute('UPDATE keyword_channel SET count = ? WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?', (count - word_count, guild_id, channel_id, keyword, user_id))
                        await db.commit()
        async with aiosqlite.connect('keyword_user.db') as db:
            async with db.execute('SELECT count FROM keyword_user WHERE guild_id = ? AND user_id = ? AND keyword = ?', (guild_id, user_id, keyword)) as cursor:
                result = await cursor.fetchone()
            if result:
                count = result[0]
                if count > 0:
                    await db.execute('UPDATE keyword_user SET count = ? WHERE guild_id = ? AND user_id = ? AND keyword = ?', (count - word_count, guild_id, user_id, keyword))
                    await db.commit()

    async def update_kw(self, word, count, guild_id, channel_id, user_id) -> None:
        async with aiosqlite.connect('keyword_channel.db') as db:
            async with db.execute('SELECT count FROM keyword_channel WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?', (guild_id, channel_id, word, user_id)) as cursor:
                result = await cursor.fetchone()
                if result:
                    await db.execute('UPDATE keyword_channel SET count = ? WHERE guild_id = ? AND channel_id = ? AND keyword = ? AND user_id = ?', (count + result[0], guild_id, channel_id, word, user_id))
                else:
                    await db.execute('INSERT INTO keyword_channel (guild_id, channel_id, keyword, user_id, count) VALUES (?, ?, ?, ?, ?)', (guild_id, channel_id, word, user_id, count))
                await db.commit()
        async with aiosqlite.connect('keyword_user.db') as db:
            async with db.execute('SELECT count FROM keyword_user WHERE guild_id = ? AND user_id = ? AND keyword = ?', (guild_id, user_id, word)) as cursor:
                result = await cursor.fetchone()
                if result:
                    await db.execute('UPDATE keyword_user SET count = ? WHERE guild_id = ? AND user_id = ? AND keyword = ?', (count + result[0], guild_id, user_id, word))
                else:
                    await db.execute('INSERT INTO keyword_user (guild_id, user_id, keyword, count) VALUES (?, ?, ?, ?)', (guild_id, user_id, word, count))
                await db.commit()

    keyword = app_commands.Group(name='keyword', description='Keyword commands')

    @keyword.command(name='add', description='Add a keyword to the server(will record only word count channels')
    @app_commands.describe(keyword='The keyword to add')
    @app_commands.default_permissions(manage_guild=True)
    async def add_keyword(self, interaction: discord.Interaction, keyword: str) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
        if not result:
            embed = discord.Embed(title='Error', description='Word count is not being recorded for this server!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        async with aiosqlite.connect('keyword.db') as db:
            async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                kresult = await cursor.fetchall()
                if kresult:
                    for kw in kresult:
                        if keyword in kw:
                            embed = discord.Embed(title='Error', description=f'The keyword {keyword} already exists in the server', color=discord.Color.red())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                        else:
                            await db.execute('INSERT INTO keyword (guild_id, keyword) VALUES (?, ?)', (interaction.guild_id, keyword))
                            await db.commit()
                            embed = discord.Embed(title='Success', description=f'The keyword {keyword} has been added to the server', color=discord.Color.green())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                else:
                    await db.execute('INSERT INTO keyword (guild_id, keyword) VALUES (?, ?)', (interaction.guild_id, keyword))
                    await db.commit()
                    embed = discord.Embed(title='Success', description=f'The keyword {keyword} has been added to the server', color=discord.Color.green())
                    await interaction.response.send_message(embed=embed, ephemeral=True)

    @keyword.command(name='remove', description='Remove a keyword from the server')
    @app_commands.describe(keyword='The keyword to remove')
    @app_commands.default_permissions(manage_guild=True)
    async def remove_keyword(self, interaction: discord.Interaction, keyword: str) -> None:
        async with aiosqlite.connect('channels.db') as db:
            async with db.execute('SELECT channel_id FROM channels WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
        if not result:
            embed = discord.Embed(title='Error', description='Word count is not being recorded for this server!', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        async with aiosqlite.connect('keyword.db') as db:
            async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                kresult = await cursor.fetchall()
                if kresult:
                    for kw in kresult:
                        if keyword in kw:
                            await db.execute('DELETE FROM keyword WHERE guild_id = ? AND keyword = ?', (interaction.guild_id, keyword))
                            await db.commit()
                            embed = discord.Embed(title='Success', description=f'The keyword {keyword} has been removed from the server', color=discord.Color.green())
                            await interaction.response.send_message(embed=embed, ephemeral=True)
                            return
                else:
                    embed = discord.Embed(title='Error', description=f'The keyword {keyword} does not exist in the server', color=discord.Color.red())
                    await     interaction.response.send_message(embed=embed, ephemeral=True)

    @keyword.command(name='leaderboard', description='View the keyword leaderboard')
    async def keyword_leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if channel != None:
            async with aiosqlite.connect('keyword_channel.db') as db:
                async with db.execute('SELECT keyword, count, user_id FROM keyword_channel WHERE guild_id = ? AND channel_id = ?', (interaction.guild_id, channel.id)) as cursor:
                    result = await cursor.fetchall()
            if result:
                embed = discord.Embed(title='Keyword Leaderboard', color=discord.Color.green(), description=f'The keyword leaderboard for {channel.mention}')
                grouped_results = itertools.groupby(sorted(result, key=lambda x: x[0]), lambda x: x[0])
                for keyword, group in grouped_results:
                    users = sorted([(user_id, count) for _, count, user_id in group], key=lambda x: x[1], reverse=True)
                    embed.add_field(name=f'Keyword: {keyword}', value=f'{", ".join([f"<@{user_id}>: {count}" for user_id, count in users])}')
                await interaction.response.send_message(embed=embed)
            else:
                embed = discord.Embed(title='Error', description='No keywords have been added to the server', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            async with aiosqlite.connect('keyword_channel.db') as db:
                async with db.execute('SELECT keyword, count, user_id FROM keyword_channel WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                    result = await cursor.fetchall()
            if result:
                embed = discord.Embed(title='Keyword Leaderboard', color=discord.Color.from_str('#af2202'), description='The keyword leaderboard for this server')
                grouped_results = itertools.groupby(sorted(result, key=lambda x: x[0]), lambda x: x[0])
                for keyword, group in grouped_results:
                    users = sorted([(user_id, count) for _, count, user_id in group], key=lambda x: x[1], reverse=True)
                    embed.add_field(name=f'Keyword: {keyword}', value=f'{", ".join([f"<@{user_id}>: {count}" for user_id, count in users])}')
                await interaction.response.send_message(embed=embed)
            else:
                embed = discord.Embed(title='Error', description='No keywords have been added to the server', color=discord.Color.red())
                await interaction.response.send_message(embed=embed, ephemeral=True)

    @keyword.command(name='list', description='View the keywords in the server')
    async def keyword_list(self, interaction: discord.Interaction) -> None:
        async with aiosqlite.connect('keyword.db') as db:
            async with db.execute('SELECT keyword FROM keyword WHERE guild_id = ?', (interaction.guild_id,)) as cursor:
                result = await cursor.fetchall()
        if result:
            embed = discord.Embed(title='Keywords', color=discord.Color.from_str('#af2202'), description='The keywords in this server')
            for row in result:
                embed.add_field(name=row[0], value='\u200b', inline=False)
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(title='Error', description='No keywords have been added to the server', color=discord.Color.red())
            await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot) -> None:
    await bot.add_cog(Keyword(bot))
    print('Keyword cog loaded')