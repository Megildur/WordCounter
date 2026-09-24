import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional
from paginator import ButtonPaginator
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    INFO_COLOR,
    create_v2_container,
    error_view,
)


class Attachments(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def attachment_message(self, message, result, target_channel_id: Optional[int] = None) -> None:
        attachment_count = len(message.attachments)
        link_count = sum(1 for word in message.content.split() if word.startswith(('http://', 'https://')))
        total_attachments = attachment_count + link_count
        if total_attachments > 0:
            eff_channel_id = target_channel_id or (
                message.channel.parent_id
                if message.channel.type == discord.ChannelType.public_thread
                else message.channel.id
            )
            await self.at_add(message.guild.id, eff_channel_id, message.author.id, total_attachments)

    async def attachment_message_delete(self, message, result, target_channel_id: Optional[int] = None) -> None:
        attachment_count = len(message.attachments)
        link_count = sum(1 for word in message.content.split() if word.startswith(('http://', 'https://')))
        total_attachments = attachment_count + link_count
        if total_attachments > 0:
            eff_channel_id = target_channel_id or (
                message.channel.parent_id
                if message.channel.type == discord.ChannelType.public_thread
                else message.channel.id
            )
            await self.at_delete(message.guild.id, eff_channel_id, message.author.id, total_attachments)

    async def attachment_message_edit(self, before, after, result, target_channel_id: Optional[int] = None) -> None:
        before_attachment_count = len(before.attachments)
        after_attachment_count = len(after.attachments)
        before_link_count = sum(1 for word in before.content.split() if word.startswith(('http://', 'https://')))
        after_link_count = sum(1 for word in after.content.split() if word.startswith(('http://', 'https://')))
        before_count = before_attachment_count + before_link_count
        after_count = after_attachment_count + after_link_count
        if (before_count + after_count) > 0:
            eff_channel_id = target_channel_id or (
                before.channel.parent_id
                if before.channel.type == discord.ChannelType.public_thread
                else before.channel.id
            )
            await self.find_dif(before.guild.id, eff_channel_id, before.author.id, before_count, after_count)

    async def find_dif(self, guild_id, channel_id, user_id, before_count, after_count) -> None:
        if before_count > after_count:
            await self.at_delete(guild_id, channel_id, user_id, before_count - after_count)
        elif before_count < after_count:
            await self.at_add(guild_id, channel_id, user_id, after_count - before_count)

    async def at_add(self, guild_id, channel_id, user_id, count) -> None:
        await self.bot.db.add_attachment_count(guild_id, channel_id, user_id, count)

    async def at_delete(self, guild_id, channel_id, user_id, count) -> None:
        await self.bot.db.remove_attachment_count(guild_id, channel_id, user_id, count)

    attachment = app_commands.Group(name='attachment', description='Attachment commands')

    @attachment.command(name='leaderboard', description='Shows the attachment leaderboard')
    @app_commands.describe(channel='The channel to show the leaderboard for')
    async def attachment_leaderboard(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None) -> None:
        result = await self.bot.db.get_attachment_leaderboard(
            interaction.guild.id, channel.id if channel else None
        )
        subtitle = (
            f'Top attachment contributors in {channel.mention}'
            if channel
            else 'Top attachment contributors in the server'
        )

        if not result:
            await interaction.response.send_message(
                view=error_view('No users found.', title='🏆 Attachment Leaderboard'),
                ephemeral=True,
            )
            return

        valid_results = []
        for user_id, count in result:
            user = interaction.guild.get_member(user_id)
            if user:
                valid_results.append((user, count))

        if not valid_results:
            await interaction.response.send_message(
                view=error_view('No active users found.', title='🏆 Attachment Leaderboard'),
                ephemeral=True,
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
            for index, (user, count) in enumerate(page_data, start=start_idx + 1):
                if index == 1:
                    lines.append(f"🥇 **{user.display_name}** - {count:,} attachments")
                elif index == 2:
                    lines.append(f"🥈 **{user.display_name}** - {count:,} attachments")
                elif index == 3:
                    lines.append(f"🥉 **{user.display_name}** - {count:,} attachments")
                else:
                    lines.append(f"**{index}.** {user.display_name} - {count:,} attachments")

            container = create_v2_container(
                title='🏆 Attachment Leaderboard',
                description=f"{subtitle}\n\n" + "\n".join(lines),
                footer=f"Page {page_num + 1}/{total_pages} • Total users: {len(valid_results)}",
                color=INFO_COLOR,
            )
            containers.append(container)

        paginator = ButtonPaginator.create_standard_paginator(
            containers,
            author_id=interaction.user.id,
            timeout=180.0,
        )
        await paginator.start(interaction)


async def setup(bot) -> None:
    await bot.add_cog(Attachments(bot))
    print('Attachments cog loaded')