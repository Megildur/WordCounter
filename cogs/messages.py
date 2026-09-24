import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
from paginator import ButtonPaginator
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_container,
    error_view,
)


class Messages(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def add_msg(self, guild_id, user_id, channel_id) -> None:
        await self.bot.db.add_message_count(guild_id, user_id, channel_id)

    async def del_msg(self, guild_id, user_id, channel_id) -> None:
        await self.bot.db.remove_message_count(guild_id, user_id, channel_id)

    message = app_commands.Group(name='message', description='Message commands')

    @message.command(name='leaderboard', description='Shows the message leaderboard')
    @app_commands.describe(channel='The channel to show the leaderboard for')
    async def message_leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        rows = await self.bot.db.get_message_leaderboard(
            interaction.guild.id, channel.id if channel else None
        )
        subtitle = (
            f'Top message contributors in {channel.mention}'
            if channel
            else 'Top message contributors in the server'
        )

        if not rows:
            await interaction.response.send_message(
                view=error_view('No messages have been sent yet!', title='💬 Message Leaderboard')
            )
            return

        valid_results = []
        for user_id, messages_cnt in rows:
            user = interaction.guild.get_member(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except Exception:
                    continue
            if user:
                valid_results.append((user, messages_cnt))

        if not valid_results:
            await interaction.response.send_message(
                view=error_view('No active users found!', title='💬 Message Leaderboard')
            )
            return

        containers = []
        users_per_page = 10
        total_pages = (len(valid_results) + users_per_page - 1) // users_per_page

        for page_num in range(total_pages):
            start_idx = page_num * users_per_page
            end_idx = min(start_idx + users_per_page, len(valid_results))
            page_data = valid_results[start_idx:end_idx]

            lines = []
            for index, (user, messages_cnt) in enumerate(page_data, start=start_idx + 1):
                display_name = user.display_name if hasattr(user, 'display_name') else user.name
                if index == 1:
                    lines.append(f"🥇 **{display_name}** - {messages_cnt:,} messages")
                elif index == 2:
                    lines.append(f"🥈 **{display_name}** - {messages_cnt:,} messages")
                elif index == 3:
                    lines.append(f"🥉 **{display_name}** - {messages_cnt:,} messages")
                else:
                    lines.append(f"**{index}.** {display_name} - {messages_cnt:,} messages")

            container = create_v2_container(
                title='💬 Message Leaderboard',
                description=f"{subtitle}\n\n" + "\n".join(lines),
                footer=f"Page {page_num + 1}/{total_pages} • Total users: {len(valid_results)}",
                color=BRAND_COLOR,
            )
            containers.append(container)

        paginator = ButtonPaginator.create_standard_paginator(
            containers,
            author_id=interaction.user.id,
            timeout=180.0,
        )
        await paginator.start(interaction)


async def setup(bot) -> None:
    await bot.add_cog(Messages(bot))
    print('Messages cog loaded')