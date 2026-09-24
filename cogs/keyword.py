import discord
from discord.ext import commands
from discord import app_commands
from collections import defaultdict
from typing import Optional
from paginator import ButtonPaginator
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_container,
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
            if message.channel.type == discord.ChannelType.public_thread
            else message.channel.id
        )
        words = message.content.lower().split()
        for kw in keywords:
            word_count = words.count(kw.lower())
            if word_count > 0:
                await self.update_kw(kw, word_count, message.guild.id, eff_channel_id, message.author.id)

    async def keyword_delete(self, message, result, target_channel_id: Optional[int] = None) -> None:
        keywords = await self.bot.db.get_keywords(message.guild.id)
        if not keywords:
            return
        eff_channel_id = target_channel_id or (
            message.channel.parent_id
            if message.channel.type == discord.ChannelType.public_thread
            else message.channel.id
        )
        words = message.content.lower().split()
        for kw in keywords:
            word_count = words.count(kw.lower())
            if word_count > 0:
                await self.remove_kw(kw, word_count, message.guild.id, eff_channel_id, message.author.id)

    async def keyword_edit(self, before, after, result, target_channel_id: Optional[int] = None) -> None:
        keywords = await self.bot.db.get_keywords(before.guild.id)
        if not keywords:
            return
        eff_channel_id = target_channel_id or (
            before.channel.parent_id
            if before.channel.type == discord.ChannelType.public_thread
            else before.channel.id
        )
        bwords = before.content.lower().split()
        awords = after.content.lower().split()
        for kw in keywords:
            bword_count = bwords.count(kw.lower())
            aword_count = awords.count(kw.lower())
            if (bword_count > 0 or aword_count > 0) and bword_count != aword_count:
                await self.find_dif(kw, bword_count, aword_count, before.guild.id, eff_channel_id, before.author.id)

    async def find_dif(self, keyword, bword_count, aword_count, guild_id, channel_id, user_id) -> None:
        if bword_count > aword_count:
            await self.remove_kw(keyword, bword_count - aword_count, guild_id, channel_id, user_id)
        elif aword_count > bword_count:
            await self.update_kw(keyword, aword_count - bword_count, guild_id, channel_id, user_id)

    async def remove_kw(self, keyword, word_count, guild_id, channel_id, user_id) -> None:
        await self.bot.db.remove_keyword_count(keyword, word_count, guild_id, channel_id, user_id)

    async def update_kw(self, word, count, guild_id, channel_id, user_id) -> None:
        await self.bot.db.update_keyword_count(word, count, guild_id, channel_id, user_id)

    keyword = app_commands.Group(name='keyword', description='Keyword leaderboard and viewing commands')

    @keyword.command(name='leaderboard', description='View the keyword leaderboard')
    @app_commands.describe(channel='Optional channel to filter the keyword leaderboard by')
    async def keyword_leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(
                view=error_view('This command can only be used in a server!'),
                ephemeral=True,
            )
            return

        result = await self.bot.db.get_keyword_leaderboard(
            interaction.guild_id, channel.id if channel else None
        )

        if not result:
            await interaction.response.send_message(
                view=error_view('No keyword usage has been recorded yet! Use `/settings` to configure tracked keywords.'),
                ephemeral=True,
            )
            return

        kw_map = defaultdict(lambda: defaultdict(int))
        for kw, cnt, uid in result:
            kw_map[kw][uid] += cnt

        keyword_data = []
        for kw in sorted(kw_map.keys()):
            users_sorted = sorted(kw_map[kw].items(), key=lambda x: x[1], reverse=True)
            valid_users = []
            for user_id, count in users_sorted:
                user = interaction.guild.get_member(user_id)
                if user:
                    valid_users.append((user, count))
            if valid_users:
                keyword_data.append((kw, valid_users))

        if not keyword_data:
            await interaction.response.send_message(
                view=error_view('No active users found with keywords.'),
                ephemeral=True,
            )
            return

        containers = []
        keywords_per_page = 5
        total_pages = (len(keyword_data) + keywords_per_page - 1) // keywords_per_page
        subtitle = (
            f'Top keyword usage in {channel.mention}'
            if channel
            else 'Top keyword usage by users in the server'
        )

        for page_num in range(total_pages):
            start_idx = page_num * keywords_per_page
            end_idx = min(start_idx + keywords_per_page, len(keyword_data))
            page_data = keyword_data[start_idx:end_idx]

            fields = []
            for kw, users in page_data:
                user_list = []
                for i, (user, count) in enumerate(users[:10]):
                    if i == 0:
                        user_list.append(f"🥇 **{user.display_name}**: {count:,}")
                    elif i == 1:
                        user_list.append(f"🥈 **{user.display_name}**: {count:,}")
                    elif i == 2:
                        user_list.append(f"🥉 **{user.display_name}**: {count:,}")
                    else:
                        user_list.append(f"**{i + 1}. {user.display_name}**: {count:,}")
                fields.append((f'🔑 Keyword: "{kw}"', '\n'.join(user_list) if user_list else 'No users found'))

            container = create_v2_container(
                title='🔤 Keyword Leaderboard',
                description=subtitle,
                fields=fields,
                footer=f"Page {page_num + 1}/{total_pages} • Total keywords: {len(keyword_data)}",
                color=BRAND_COLOR,
            )
            containers.append(container)

        paginator = ButtonPaginator.create_standard_paginator(
            containers,
            author_id=interaction.user.id,
            timeout=180.0,
        )
        await paginator.start(interaction)

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