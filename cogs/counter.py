from __future__ import annotations
import calendar
from typing import Any, Dict, List, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import (
    BRAND_COLOR,
    TRACKING_OFF_MESSAGE,
    activity_line,
    check_channel_with_config,
    create_v2_container,
    create_v2_view,
    error_view,
    keyword_summary,
)
from cogs.utils.counting import MessageStats
from paginator import ButtonPaginator

MonthRecord = Dict[str, Any]
OverallStats = Tuple[int, int, int, int, List[Tuple[str, int]]]


class UserStatsPaginator(ButtonPaginator):
    previous_label = "Newer"
    next_label = "Older"

    def __init__(
        self,
        guild: discord.Guild,
        member: discord.Member,
        author_id: int,
        overall: OverallStats,
        months: List[MonthRecord],
    ) -> None:
        super().__init__(months or [None], author_id=author_id)
        self.denied_message = "Only the person who opened these stats can change the month."
        self.guild = guild
        self.member = member
        self.overall = overall
        self.months = months

    def format_page(self, month: Optional[MonthRecord]) -> discord.ui.Container:
        words, messages, attachments, emojis, keywords = self.overall
        fields: List[Tuple[str, str]] = [("All time", activity_line(words, messages, attachments, emojis))]
        if keywords:
            fields.append(("Keywords", keyword_summary(keywords)))

        footer = self.guild.name
        if month is not None:
            label = f"{calendar.month_name[month['month']]} {month['year']}"
            summary = [activity_line(month["words"], month["messages"], month["attachments"], month.get("emojis", 0))]
            month_keywords = [(k, v) for k, v in month.get("keywords", {}).items() if v > 0]
            if month_keywords:
                summary.append(f"Keywords: {keyword_summary(month_keywords)}")
            fields.append((label, "\n".join(summary)))

            channel_lines = []
            for channel_id, data in sorted(month["channels"].items(), key=lambda item: item[1]["words"], reverse=True):
                line = f"<#{channel_id}>: " + activity_line(
                    data["words"], data["messages"], data["attachments"], data.get("emojis", 0), bold=False
                )
                channel_keywords = [(k, v) for k, v in data.get("keywords", {}).items() if v > 0]
                if channel_keywords:
                    line += f"\n-# Keywords: {keyword_summary(channel_keywords)}"
                channel_lines.append(line)
            if channel_lines:
                fields.append((f"Channels in {label}", "\n".join(channel_lines)))
            footer = f"Month {self.current_page + 1} of {len(self.months)}, newest first · {self.guild.name}"

        return create_v2_container(
            title="User stats",
            description=f"{self.member.mention} has written **{words:,}** words in this server.",
            fields=fields,
            footer=footer,
            thumbnail_url=self.member.display_avatar.url,
            color=BRAND_COLOR,
        )

    def extra_rows(self) -> List[discord.ui.ActionRow]:
        if len(self.months) < 2:
            return []
        first = max(0, min(self.current_page - 12, len(self.months) - 25))
        select = discord.ui.Select(placeholder="Jump to a month", min_values=1, max_values=1)
        for index in range(first, min(first + 25, len(self.months))):
            month = self.months[index]
            select.add_option(
                label=f"{calendar.month_name[month['month']]} {month['year']}",
                value=str(index),
                description=f"{month['words']:,} words · {month['messages']:,} messages"[:100],
                default=index == self.current_page,
            )

        async def on_select(interaction: discord.Interaction) -> None:
            self.current_page = int(select.values[0])
            await self.update_page(interaction)

        select.callback = on_select
        return [discord.ui.ActionRow(select)]


class Counter(commands.Cog):
    stats = app_commands.Group(name="stats", description="User statistics commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.word_count_menu = app_commands.ContextMenu(name="Message Word Count", callback=self.message_word_count)
        self.user_stats_menu = app_commands.ContextMenu(name="User Stats", callback=self.user_stats)
        self.bot.tree.add_command(self.word_count_menu)
        self.bot.tree.add_command(self.user_stats_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.word_count_menu.name, type=self.word_count_menu.type)
        self.bot.tree.remove_command(self.user_stats_menu.name, type=self.user_stats_menu.type)

    async def message_word_count(self, interaction: discord.Interaction, message: discord.Message) -> None:
        if interaction.guild_id is None or not await self.bot.db.has_tracking_enabled(interaction.guild_id):
            await interaction.response.send_message(view=error_view(TRACKING_OFF_MESSAGE))
            return
        if message.author.bot:
            await interaction.response.send_message(view=error_view("Bot messages aren't counted."))
            return
        words = len(message.content.split())
        await interaction.response.send_message(
            view=create_v2_view(
                "Message word count",
                f"This message from {message.author.mention} has **{words:,}** words.",
                color=BRAND_COLOR,
            )
        )

    async def _send_user_stats(self, interaction: discord.Interaction, user: discord.Member) -> None:
        if interaction.guild_id is None or not await self.bot.db.has_tracking_enabled(interaction.guild_id):
            await interaction.response.send_message(view=error_view(TRACKING_OFF_MESSAGE))
            return
        if user.bot:
            await interaction.response.send_message(view=error_view("Bots don't have stats."))
            return

        overall = await self.bot.db.get_user_full_stats(interaction.guild_id, user.id)
        if not any(overall):
            await interaction.response.send_message(
                view=error_view(f"{user.mention} has no recorded activity in this server yet."),
                ephemeral=True,
            )
            return

        months = await self.bot.db.get_user_monthly_breakdown(interaction.guild_id, user.id)
        await UserStatsPaginator(interaction.guild, user, interaction.user.id, overall, months).start(interaction)

    async def user_stats(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self._send_user_stats(interaction, user)

    @stats.command(name="user", description="Show detailed statistics for a user")
    @app_commands.describe(user="The user to show statistics for")
    async def user_stats_slash(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await self._send_user_stats(interaction, user)

    async def _tracked_channel(self, message: discord.Message) -> Optional[int]:
        if message.author.bot or message.guild is None:
            return None
        watched, ignored = await self.bot.db.get_guild_tracking_config(message.guild.id)
        tracked, channel_id = check_channel_with_config(message.guild, message.channel, watched, ignored)
        return channel_id if tracked else None

    async def _apply(self, message: discord.Message, channel_id: int, **changes: Any) -> None:
        await self.bot.db.apply_activity(
            message.guild.id,
            message.author.id,
            channel_id,
            message.created_at.year,
            message.created_at.month,
            **changes,
        )

    async def _record(self, message: discord.Message, sign: int) -> None:
        channel_id = await self._tracked_channel(message)
        if channel_id is None:
            return
        stats = MessageStats.of(message, await self.bot.db.get_keywords(message.guild.id))
        await self._apply(
            message,
            channel_id,
            words=sign * stats.words,
            messages=sign,
            attachments=sign * stats.attachments,
            emojis=sign * stats.emojis,
            keywords={keyword: sign * count for keyword, count in stats.keywords.items()},
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._record(message, 1)

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message) -> None:
        await self._record(message, -1)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message) -> None:
        channel_id = await self._tracked_channel(before)
        if channel_id is None:
            return
        keywords = await self.bot.db.get_keywords(before.guild.id)
        old, new = MessageStats.of(before, keywords), MessageStats.of(after, keywords)
        await self._apply(
            before,
            channel_id,
            words=new.words - old.words,
            attachments=new.attachments - old.attachments,
            emojis=new.emojis - old.emojis,
            keywords={k: new.keywords.get(k, 0) - old.keywords.get(k, 0) for k in old.keywords.keys() | new.keywords.keys()},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Counter(bot))
