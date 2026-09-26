import calendar
import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Any, Tuple
from cogs.keyword import Keyword
from cogs.attachments import Attachments
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    create_v2_container,
    create_v2_view,
    error_view,
    check_channel_with_config,
    count_emojis,
)


class UserStatsView(discord.ui.LayoutView):
    def __init__(
        self,
        bot: commands.Bot,
        guild: discord.Guild,
        target_user: discord.Member,
        author_id: int,
        overall_stats: Tuple[int, int, int, int, List[Tuple[str, int]]],
        monthly_records: List[Dict[str, Any]],
    ) -> None:
        super().__init__(timeout=180.0)
        self.bot = bot
        self.guild = guild
        self.target_user = target_user
        self.author_id = author_id
        self.overall_words, self.overall_messages, self.overall_attachments, self.overall_emojis, self.overall_keywords = overall_stats
        self.monthly_records = monthly_records
        self.current_index = 0
        self.total_months = len(monthly_records)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=error_view("Only the user who opened these stats can navigate."),
                ephemeral=True,
            )
            return False
        return True

    def build(self) -> None:
        self.clear_items()
        avatar_url = self.target_user.display_avatar.url if self.target_user.display_avatar else None
        footer_text = self.guild.name if self.guild else None

        fields: List[Tuple[str, str]] = [
            ("💬 Total Message Count", f"{self.overall_messages:,}"),
            ("📎 Total Attachment Count", f"{self.overall_attachments:,}"),
            ("😀 Total Emoji Count", f"{self.overall_emojis:,}"),
        ]
        if self.overall_keywords:
            for kw, kw_cnt in self.overall_keywords:
                fields.append((f"🔑 Keyword: {kw}", f"Said **{kw_cnt:,}** times."))

        if self.monthly_records:
            cur_month = self.monthly_records[self.current_index]
            y = cur_month["year"]
            m = cur_month["month"]
            m_name = calendar.month_name[m]

            m_summary_lines = [
                f"• **Words:** {cur_month['words']:,}",
                f"• **Messages:** {cur_month['messages']:,}",
                f"• **Attachments:** {cur_month['attachments']:,}",
                f"• **Emojis:** {cur_month.get('emojis', 0):,}",
            ]
            if cur_month.get("keywords"):
                kw_parts = [f"`{k}`: {v:,}" for k, v in cur_month["keywords"].items() if v > 0]
                if kw_parts:
                    m_summary_lines.append(f"• **Keywords:** {', '.join(kw_parts)}")

            fields.append((
                f"📅 {m_name} {y} Totals ({self.current_index + 1}/{self.total_months})",
                "\n".join(m_summary_lines),
            ))

            chan_lines = []
            sorted_chans = sorted(
                cur_month["channels"].items(),
                key=lambda x: x[1]["words"],
                reverse=True,
            )
            for cid, c_data in sorted_chans:
                line = f"• <#{cid}>: **{c_data['words']:,}** words • **{c_data['messages']:,}** msgs • **{c_data['attachments']:,}** atts • **{c_data.get('emojis', 0):,}** emojis"
                if c_data.get("keywords"):
                    c_kw_parts = [f"`{k}`: {v:,}" for k, v in c_data["keywords"].items() if v > 0]
                    if c_kw_parts:
                        line += f"\n  ↳ *Keywords:* {', '.join(c_kw_parts)}"
                chan_lines.append(line)

            if chan_lines:
                fields.append((f"📍 Channels in {m_name} {y}", "\n".join(chan_lines)))

            footer_text = f"Viewing month {self.current_index + 1} of {self.total_months} (Newest to Oldest) • {footer_text}" if footer_text else f"Viewing month {self.current_index + 1} of {self.total_months}"

        container = create_v2_container(
            title="📊 User Stats",
            description=f"{self.target_user.mention} has said **{self.overall_words:,}** words in this server!",
            fields=fields,
            footer=footer_text,
            thumbnail_url=avatar_url,
            color=BRAND_COLOR,
        )

        if self.monthly_records and self.total_months > 1:
            container.add_item(discord.ui.Separator())
            container.add_item(self._build_select_row())
            container.add_item(self._build_pagination_row())

        self.add_item(container)

    def _build_select_row(self) -> discord.ui.ActionRow:
        select = discord.ui.Select(
            placeholder="Select Month / Year...",
            min_values=1,
            max_values=1,
        )
        for idx, item in enumerate(self.monthly_records[:25]):
            m_label = f"{calendar.month_name[item['month']]} {item['year']}"
            m_desc = f"{item['words']:,} words • {item['messages']:,} msgs • {item.get('emojis', 0):,} emojis"
            select.add_option(
                label=m_label,
                value=str(idx),
                description=m_desc[:100],
                default=(idx == self.current_index),
            )

        async def _on_select(interaction: discord.Interaction) -> None:
            if select.values:
                self.current_index = int(select.values[0])
            await self.refresh_and_edit(interaction)

        select.callback = _on_select
        return discord.ui.ActionRow(select)

    def _build_pagination_row(self) -> discord.ui.ActionRow:
        btn_newer = discord.ui.Button(
            label="◀️ Newer",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_index <= 0),
        )

        async def _on_newer(interaction: discord.Interaction) -> None:
            if self.current_index > 0:
                self.current_index -= 1
            await self.refresh_and_edit(interaction)

        btn_newer.callback = _on_newer

        btn_indicator = discord.ui.Button(
            label=f"{self.current_index + 1}/{self.total_months}",
            style=discord.ButtonStyle.primary,
            disabled=True,
        )

        async def _on_noop(interaction: discord.Interaction) -> None:
            await interaction.response.defer()

        btn_indicator.callback = _on_noop

        btn_older = discord.ui.Button(
            label="Older ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=(self.current_index >= self.total_months - 1),
        )

        async def _on_older(interaction: discord.Interaction) -> None:
            if self.current_index < self.total_months - 1:
                self.current_index += 1
            await self.refresh_and_edit(interaction)

        btn_older.callback = _on_older

        return discord.ui.ActionRow(btn_newer, btn_indicator, btn_older)

    async def refresh_and_edit(self, interaction: discord.Interaction) -> None:
        self.build()
        await interaction.response.edit_message(view=self)


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

        words, messages_cnt, attachments_cnt, emojis_cnt, kresult = await self.bot.db.get_user_full_stats(
            interaction.guild_id, user.id
        )

        if not kresult and not words and not attachments_cnt and not messages_cnt and not emojis_cnt:
            await interaction.response.send_message(
                view=error_view('This user has not said any words in this server!'),
                ephemeral=True,
            )
            return

        monthly_records = await self.bot.db.get_user_monthly_breakdown(interaction.guild_id, user.id)
        view = UserStatsView(
            bot=self.bot,
            guild=interaction.guild,
            target_user=user,
            author_id=interaction.user.id,
            overall_stats=(words, messages_cnt, attachments_cnt, emojis_cnt, kresult),
            monthly_records=monthly_records,
        )
        view.build()
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
        emoji_count = count_emojis(message.content)
        year = message.created_at.year
        month = message.created_at.month
        await self.update_count(message.guild, message.author, target_channel_id, word_count, year, month)
        await self.bot.db.add_message_count(message.guild.id, message.author.id, target_channel_id)
        if emoji_count > 0:
            await self.bot.db.add_emoji_count(message.guild.id, message.author.id, target_channel_id, emoji_count)
        await self.bot.db.record_monthly_activity(
            message.guild.id, message.author.id, target_channel_id, year, month, messages=1, emojis=emoji_count
        )

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
        emoji_count = count_emojis(message.content)
        year = message.created_at.year
        month = message.created_at.month
        await self.remove_count(message.guild, message.author, target_channel_id, word_count, year, month)
        await self.bot.db.remove_message_count(message.guild.id, message.author.id, target_channel_id)
        if emoji_count > 0:
            await self.bot.db.remove_emoji_count(message.guild.id, message.author.id, target_channel_id, emoji_count)
        await self.bot.db.remove_monthly_activity(
            message.guild.id, message.author.id, target_channel_id, year, month, messages=1, emojis=emoji_count
        )

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
        old_emoji_count = count_emojis(before.content)
        new_emoji_count = count_emojis(after.content)
        year = before.created_at.year
        month = before.created_at.month
        await self.find_dif(before.guild, before.author, target_channel_id, old_msg_count, new_msg_count, year, month)
        if old_emoji_count > new_emoji_count:
            diff = old_emoji_count - new_emoji_count
            await self.bot.db.remove_emoji_count(before.guild.id, before.author.id, target_channel_id, diff)
            await self.bot.db.remove_monthly_activity(
                before.guild.id, before.author.id, target_channel_id, year, month, emojis=diff
            )
        elif new_emoji_count > old_emoji_count:
            diff = new_emoji_count - old_emoji_count
            await self.bot.db.add_emoji_count(before.guild.id, before.author.id, target_channel_id, diff)
            await self.bot.db.record_monthly_activity(
                before.guild.id, before.author.id, target_channel_id, year, month, emojis=diff
            )

    async def find_dif(self, guild, user, channel_id, old_msg_count, new_msg_count, year=None, month=None) -> None:
        if old_msg_count > new_msg_count:
            count = old_msg_count - new_msg_count
            await self.remove_count(guild, user, channel_id, count, year, month)
        elif old_msg_count < new_msg_count:
            count = new_msg_count - old_msg_count
            await self.update_count(guild, user, channel_id, count, year, month)

    async def update_count(self, guild, user, channel_id, count, year=None, month=None) -> None:
        await self.bot.db.update_word_count(guild.id, user.id, channel_id, count)
        if year is not None and month is not None:
            await self.bot.db.record_monthly_activity(guild.id, user.id, channel_id, year, month, words=count)

    async def remove_count(self, guild, user, channel_id, count, year=None, month=None) -> None:
        await self.bot.db.remove_word_count(guild.id, user.id, channel_id, count)
        if year is not None and month is not None:
            await self.bot.db.remove_monthly_activity(guild.id, user.id, channel_id, year, month, words=count)


async def setup(bot) -> None:
    await bot.add_cog(Counter(bot))