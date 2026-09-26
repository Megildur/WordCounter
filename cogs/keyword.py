import re
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_view,
    error_view,
)


class Keyword(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def keyword_message(self, message, result, target_channel_id: Optional[int] = None) -> None:
        keywords = await self.bot.db.get_keywords(message.guild.id)
        if not keywords:
            return
        eff_channel_id = target_channel_id or (
            message.channel.parent_id
            if isinstance(message.channel, discord.Thread)
            else message.channel.id
        )
        content_lower = message.content.lower()
        year = message.created_at.year
        month = message.created_at.month
        for kw in keywords:
            word_count = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
            if word_count > 0:
                await self.update_kw(kw, word_count, message.guild.id, eff_channel_id, message.author.id, year, month)

    async def keyword_delete(self, message, result, target_channel_id: Optional[int] = None) -> None:
        keywords = await self.bot.db.get_keywords(message.guild.id)
        if not keywords:
            return
        eff_channel_id = target_channel_id or (
            message.channel.parent_id
            if isinstance(message.channel, discord.Thread)
            else message.channel.id
        )
        content_lower = message.content.lower()
        year = message.created_at.year
        month = message.created_at.month
        for kw in keywords:
            word_count = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
            if word_count > 0:
                await self.remove_kw(kw, word_count, message.guild.id, eff_channel_id, message.author.id, year, month)

    async def keyword_edit(self, before, after, result, target_channel_id: Optional[int] = None) -> None:
        keywords = await self.bot.db.get_keywords(before.guild.id)
        if not keywords:
            return
        eff_channel_id = target_channel_id or (
            before.channel.parent_id
            if isinstance(before.channel, discord.Thread)
            else before.channel.id
        )
        b_content_lower = before.content.lower()
        a_content_lower = after.content.lower()
        year = before.created_at.year
        month = before.created_at.month
        for kw in keywords:
            bword_count = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", b_content_lower))
            aword_count = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", a_content_lower))
            if (bword_count > 0 or aword_count > 0) and bword_count != aword_count:
                await self.find_dif(kw, bword_count, aword_count, before.guild.id, eff_channel_id, before.author.id, year, month)

    async def find_dif(self, keyword, bword_count, aword_count, guild_id, channel_id, user_id, year=None, month=None) -> None:
        if bword_count > aword_count:
            await self.remove_kw(keyword, bword_count - aword_count, guild_id, channel_id, user_id, year, month)
        elif aword_count > bword_count:
            await self.update_kw(keyword, aword_count - bword_count, guild_id, channel_id, user_id, year, month)

    async def remove_kw(self, keyword, word_count, guild_id, channel_id, user_id, year=None, month=None) -> None:
        await self.bot.db.remove_keyword_count(keyword, word_count, guild_id, channel_id, user_id)
        if year is not None and month is not None:
            await self.bot.db.remove_monthly_keyword(guild_id, user_id, channel_id, keyword, year, month, word_count)

    async def update_kw(self, word, count, guild_id, channel_id, user_id, year=None, month=None) -> None:
        await self.bot.db.update_keyword_count(word, count, guild_id, channel_id, user_id)
        if year is not None and month is not None:
            await self.bot.db.record_monthly_keyword(guild_id, user_id, channel_id, word, year, month, count)

    keyword = app_commands.Group(name='keyword', description='Keyword viewing commands')

    @keyword.command(name='list', description='View the keywords watched in the server')
    async def keyword_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        keywords = await self.bot.db.get_keywords(interaction.guild_id)
        if keywords:
            kw_lines = '\n'.join([f"• `{kw}`" for kw in keywords])
            view = create_v2_view(
                title='🔑 Tracked Keywords',
                description=f'The following **{len(keywords)}** keyword(s) are currently watched in this server:\n\n{kw_lines}',
                footer='Use /settings to add, edit, or remove watched keywords',
                color=BRAND_COLOR,
            )
            await interaction.response.send_message(view=view)
        else:
            await interaction.response.send_message(
                view=error_view('No keywords have been added to the server. Use `/settings` to add keywords!'),
                ephemeral=True,
            )


async def setup(bot) -> None:
    await bot.add_cog(Keyword(bot))
    print('Keyword cog loaded')