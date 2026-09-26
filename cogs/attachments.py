import discord
from discord.ext import commands
from typing import Optional
from cogs.utils.database import WordCounterDatabase


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


async def setup(bot) -> None:
    await bot.add_cog(Attachments(bot))
    print('Attachments cog loaded')