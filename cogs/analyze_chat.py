from __future__ import annotations
import asyncio
import logging
import re
from collections import defaultdict
from typing import Dict, Any, List, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    SUCCESS_COLOR,
    WARNING_COLOR,
    create_v2_view,
    error_view,
    check_channel_with_config,
)

logger = logging.getLogger(__name__)


class AnalyzeChat(commands.Cog):
    """
    Retroactively sweeps a user's historical messages before the bot joined the server
    using Discord's Guild Message Search API (/guilds/{guild_id}/messages/search),
    integrating directly with WordCounterDatabase (asyncio.Lock + WAL) and Components V2 UI.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)
        print("AnalyzeChat cog loaded")

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def _fetch_search_page(
        self, guild_id: int, author_id: int, max_id: int, offset: int = 0
    ) -> Dict[str, Any]:
        """
        Paginates Discord's guild search API restricted to messages sent BEFORE the bot joined (max_id).
        Routes through discord.py's internal HTTP client to leverage automatic 429 bucket handling.
        """
        route = Route("GET", "/guilds/{guild_id}/messages/search", guild_id=guild_id)
        params = {
            "author_id": author_id,
            "max_id": max_id,
            "offset": offset,
        }

        while True:
            try:
                data = await self.bot.http.request(route, params=params)

                # Handle the 202 Indexing delay when a guild's search index is warming up
                if not isinstance(data, dict) or "messages" not in data or data.get("message") == "Indexing":
                    retry_after = data.get("retry_after", 5) if isinstance(data, dict) else 5
                    logger.info(f"Search index building for guild {guild_id}. Waiting {retry_after}s...")
                    await asyncio.sleep(float(retry_after))
                    continue

                return data

            except discord.HTTPException as e:
                if e.status == 202:
                    logger.info("HTTP 202: Search index building. Retrying in 5s...")
                    await asyncio.sleep(5.0)
                    continue
                raise e

    async def _run_retroactive_analysis(
        self, interaction: discord.Interaction, target: discord.Member
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                view=error_view("This command can only be used in a server."),
                ephemeral=True,
            )
            return

        if target.bot:
            await interaction.response.send_message(
                view=error_view("Bots cannot be retroactively analyzed for word counts."),
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True)

        # 1. Check if user has already been analyzed in this guild
        if await self.bot.db.is_user_analyzed(interaction.guild.id, target.id):
            await interaction.followup.send(
                view=error_view(
                    f"{target.mention} has already been retroactively analyzed in this server.\n"
                    f"-# An administrator can unlock re-analysis for this user in `/settings` → **Data & Reset Tools**."
                )
            )
            return

        # 2. Load server tracking config & watched keywords from WordCounterDatabase
        watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(interaction.guild.id)
        keyword_list: List[str] = await self.bot.db.get_keywords(interaction.guild.id)

        # 3. Generate a Snowflake ID for when the bot joined to prevent double-counting
        bot_join_time = interaction.guild.me.joined_at if interaction.guild.me else None
        if bot_join_time is not None:
            max_id_snowflake = discord.utils.time_snowflake(bot_join_time)
        else:
            max_id_snowflake = discord.utils.time_snowflake(discord.utils.utcnow())

        try:
            data = await self._fetch_search_page(
                interaction.guild.id, target.id, max_id_snowflake, offset=0
            )
        except discord.Forbidden:
            await interaction.followup.send(
                view=error_view("I lack the `Read Message History` permission to perform this search.")
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                view=error_view(f"An API error occurred while querying Discord Search: `{e.status} - {e.text}`")
            )
            return

        total_historical_messages = data.get("total_results", 0)

        if total_historical_messages == 0:
            await self.bot.db.mark_user_analyzed(interaction.guild.id, target.id)
            await interaction.followup.send(
                view=create_v2_view(
                    title="🔍 Retroactive Analysis Complete",
                    description=f"No historical messages were found for {target.mention} prior to the bot joining.",
                    color=WARNING_COLOR,
                )
            )
            return

        total_pages = (total_historical_messages + 24) // 25
        estimated_time = max(0, (total_pages - 1)) * 5.0
        avatar_url = target.display_avatar.url if target.display_avatar else None

        status_view = create_v2_view(
            title="⏳ Retroactive Deep-Sweep in Progress",
            description=(
                f"Found **{total_historical_messages:,}** historical messages for {target.mention} prior to the bot joining.\n"
                f"Sweeping history with strict **5.0s** anti-ratelimit pacing...\n\n"
                f"**Estimated Time:** `{estimated_time:.1f}s` ({total_pages} page(s))"
            ),
            thumbnail_url=avatar_url,
            footer="Do not dismiss • Using Discord Guild Message Search API",
            color=WARNING_COLOR,
        )
        status_msg = await interaction.followup.send(view=status_view, wait=True)

        total_words = 0
        total_attachments = 0
        counted_messages = 0
        keyword_counts: Dict[str, int] = {k: 0 for k in keyword_list}

        # Per-channel accumulators so channel leaderboards also receive the retroactive data
        channel_words: Dict[int, int] = defaultdict(int)
        channel_messages: Dict[int, int] = defaultdict(int)
        channel_attachments: Dict[int, int] = defaultdict(int)
        channel_keywords: Dict[Tuple[int, str], int] = defaultdict(int)

        offset = 0
        page_num = 0

        # 4. Sweep and Tally with 5.0s aggressive sleep cushion
        while True:
            messages_array = data.get("messages", [])
            if not messages_array:
                break

            for hit in messages_array:
                for msg in hit:
                    if msg.get("author", {}).get("id") == str(target.id):
                        raw_channel_id = int(msg.get("channel_id", 0))
                        if watched_ids:
                            is_watched, eff_channel_id = check_channel_with_config(
                                interaction.guild, raw_channel_id, watched_ids, ignored_ids
                            )
                            if not is_watched:
                                continue
                        else:
                            eff_channel_id = raw_channel_id

                        content = msg.get("content", "") or ""
                        counted_messages += 1
                        channel_messages[eff_channel_id] += 1

                        # 1. Tally Words
                        if content:
                            words = content.split()
                            w_len = len(words)
                            total_words += w_len
                            channel_words[eff_channel_id] += w_len

                            # 2. Tally Keywords
                            content_lower = content.lower()
                            for kw in keyword_list:
                                matches = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
                                if matches > 0:
                                    keyword_counts[kw] += matches
                                    channel_keywords[(eff_channel_id, kw)] += matches

                        # 3. Tally Attachments & Links
                        att_len = len(msg.get("attachments", []))
                        link_len = sum(
                            1 for w in content.split() if w.startswith(("http://", "https://"))
                        )
                        msg_att_total = att_len + link_len
                        if msg_att_total > 0:
                            total_attachments += msg_att_total
                            channel_attachments[eff_channel_id] += msg_att_total

            offset += 25
            page_num += 1
            if offset >= total_historical_messages:
                break

            # Update progress every 2 pages
            if page_num % 2 == 0:
                try:
                    prog_view = create_v2_view(
                        title="⏳ Retroactive Deep-Sweep in Progress",
                        description=(
                            f"**Target:** {target.mention}\n"
                            f"**Progress:** `{min(offset, total_historical_messages):,} / {total_historical_messages:,}` messages scanned\n"
                            f"**Tallied So Far:** `{counted_messages:,}` messages • `{total_words:,}` words • `{total_attachments:,}` attachments"
                        ),
                        thumbnail_url=avatar_url,
                        footer=f"Page {page_num}/{total_pages} • Pacing 5.0s per request",
                        color=WARNING_COLOR,
                    )
                    await status_msg.edit(view=prog_view)
                except Exception:
                    pass

            # Aggressive 5.0s anti-ratelimit cushion
            await asyncio.sleep(5.0)

            try:
                data = await self._fetch_search_page(
                    interaction.guild.id, target.id, max_id_snowflake, offset=offset
                )
            except Exception as e:
                logger.error(f"Failed offset {offset} during retroactive sweep: {e}")
                break

        # 5. Save everything back to WordCounterDatabase under asyncio.Lock + WAL
        await self.bot.db.save_retroactive_analysis(
            guild_id=interaction.guild.id,
            user_id=target.id,
            total_words=total_words,
            counted_messages=counted_messages,
            total_attachments=total_attachments,
            keyword_counts=keyword_counts,
            channel_words=channel_words,
            channel_messages=channel_messages,
            channel_attachments=channel_attachments,
            channel_keywords=channel_keywords,
        )

        fields = [
            ("💬 Old Messages Added", f"**{counted_messages:,}** *(of {total_historical_messages:,} indexed)*"),
            ("📝 Words Found & Added", f"**{total_words:,}**"),
            ("📎 Attachments Found & Added", f"**{total_attachments:,}**"),
        ]
        if keyword_list:
            kw_lines = [f"• **{kw}**: `{cnt:,}`" for kw, cnt in keyword_counts.items()]
            fields.append(("🔑 Tracked Keywords Added", "\n".join(kw_lines)))

        complete_view = create_v2_view(
            title="✅ Retroactive Sync Complete",
            description=f"Successfully swept and added historical data for {target.mention} to the server database!",
            fields=fields,
            thumbnail_url=avatar_url,
            footer="Data retrieved via Discord Guild Search API • User marked as analyzed",
            color=SUCCESS_COLOR,
        )
        await status_msg.edit(view=complete_view)

    @app_commands.command(
        name="analyze",
        description="Retroactively count a user's messages, words, attachments & keywords before the bot joined",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(target="The server member to retroactively analyze")
    async def analyze_slash(self, interaction: discord.Interaction, target: discord.Member) -> None:
        await self._run_retroactive_analysis(interaction, target)

    @app_commands.command(
        name="analyze_chat",
        description="Retroactively count a user's messages, words, attachments & keywords before the bot joined",
    )
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(target="The server member to retroactively analyze")
    async def analyze_chat_slash(self, interaction: discord.Interaction, target: discord.Member) -> None:
        await self._run_retroactive_analysis(interaction, target)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyzeChat(bot))