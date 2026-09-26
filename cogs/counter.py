import discord
from discord.ext import commands
from discord import app_commands
from cogs.keyword import Keyword
from cogs.attachments import Attachments
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_view,
    error_view,
    check_channel_with_config,
)


class Counter(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)
        self.ctx_menu = app_commands.ContextMenu(name='Message Word Count', callback=self.message_word_count)
        self.ctx_menu2 = app_commands.ContextMenu(name='User Stats', callback=self.user_stats)
        print('Counter cog loaded')
        self.bot.tree.add_command(self.ctx_menu)
        self.bot.tree.add_command(self.ctx_menu2)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)
        self.bot.tree.remove_command(self.ctx_menu2.name, type=self.ctx_menu2.type)

    async def message_word_count(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if interaction.guild_id is None or not await self.bot.db.has_tracking_enabled(interaction.guild_id):
            await interaction.response.send_message(
                view=error_view('Word count is not being recorded for this server!')
            )
            return
        if message.author.bot:
            await interaction.response.send_message(
                view=error_view('Bots cannot have word counts!')
            )
            return
        words = len(message.content.split())
        view = create_v2_view(
            title='📝 Message Word Count',
            description=f'{message.author.mention} has said **{words:,}** words in this message.',
            color=BRAND_COLOR,
        )
        await interaction.response.send_message(view=view)

    async def _send_user_stats(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if interaction.guild_id is None or not await self.bot.db.has_tracking_enabled(interaction.guild_id):
            await interaction.response.send_message(
                view=error_view('Word count is not being recorded for this server!')
            )
            return
        if user.bot:
            await interaction.response.send_message(
                view=error_view('Bots cannot have word counts!')
            )
            return

        words, messages_cnt, attachments_cnt, kresult = await self.bot.db.get_user_full_stats(
            interaction.guild_id, user.id
        )

        if not kresult and not words and not attachments_cnt and not messages_cnt:
            await interaction.response.send_message(
                view=error_view('This user has not said any words in this server!'),
                ephemeral=True,
            )
            return

        fields = [
            ('💬 Total Message Count', f'{messages_cnt:,}'),
            ('📎 Total Attachment Count', f'{attachments_cnt:,}'),
        ]
        if kresult:
            for keyword, kw_count in kresult:
                fields.append((f'🔑 Keyword: {keyword}', f'Said **{kw_count:,}** times.'))

        avatar_url = user.display_avatar.url if user.display_avatar else None
        footer_text = interaction.guild.name if interaction.guild else None

        view = create_v2_view(
            title='📊 User Stats',
            description=f'{user.mention} has said **{words:,}** words in this server!',
            fields=fields,
            footer=footer_text,
            thumbnail_url=avatar_url,
            color=BRAND_COLOR,
        )
        await interaction.response.send_message(view=view)

    async def user_stats(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self._send_user_stats(interaction, user)

    stats = app_commands.Group(name='stats', description='User statistics commands')

    @stats.command(name='user', description='Show detailed statistics for a user')
    @app_commands.describe(user='The user to show statistics for')
    async def user_stats_slash(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self._send_user_stats(interaction, user)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(message.guild.id)
        is_watched, target_channel_id = check_channel_with_config(
            message.guild, message.channel, watched_ids, ignored_ids
        )
        if not is_watched:
            return

        result = [(1,)]
        await Keyword(self.bot).keyword_message(message, result, target_channel_id=target_channel_id)
        await Attachments(self.bot).attachment_message(message, result, target_channel_id=target_channel_id)
        word_count = len(message.content.split())
        await self.update_count(message.guild, message.author, target_channel_id, word_count)
        await self.bot.db.add_message_count(message.guild.id, message.author.id, target_channel_id)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return
        watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(message.guild.id)
        is_watched, target_channel_id = check_channel_with_config(
            message.guild, message.channel, watched_ids, ignored_ids
        )
        if not is_watched:
            return

        result = [(1,)]
        await Keyword(self.bot).keyword_delete(message, result, target_channel_id=target_channel_id)
        await Attachments(self.bot).attachment_message_delete(message, result, target_channel_id=target_channel_id)
        word_count = len(message.content.split())
        await self.remove_count(message.guild, message.author, target_channel_id, word_count)
        await self.bot.db.remove_message_count(message.guild.id, message.author.id, target_channel_id)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        if before.author.bot or before.guild is None:
            return
        watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(before.guild.id)
        is_watched, target_channel_id = check_channel_with_config(
            before.guild, before.channel, watched_ids, ignored_ids
        )
        if not is_watched:
            return

        result = [(1,)]
        await Keyword(self.bot).keyword_edit(before, after, result, target_channel_id=target_channel_id)
        await Attachments(self.bot).attachment_message_edit(before, after, result, target_channel_id=target_channel_id)
        old_msg_count = len(before.content.split())
        new_msg_count = len(after.content.split())
        await self.find_dif(before.guild, before.author, target_channel_id, old_msg_count, new_msg_count)

    async def find_dif(self, guild, user, channel_id, old_msg_count, new_msg_count) -> None:
        if old_msg_count > new_msg_count:
            count = old_msg_count - new_msg_count
            await self.remove_count(guild, user, channel_id, count)
        elif old_msg_count < new_msg_count:
            count = new_msg_count - old_msg_count
            await self.update_count(guild, user, channel_id, count)

    async def update_count(self, guild, user, channel_id, count) -> None:
        await self.bot.db.update_word_count(guild.id, user.id, channel_id, count)

    async def remove_count(self, guild, user, channel_id, count) -> None:
        await self.bot.db.remove_word_count(guild.id, user.id, channel_id, count)


async def setup(bot) -> None:
    await bot.add_cog(Counter(bot))