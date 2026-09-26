from __future__ import annotations
import asyncio
import logging
import re
from collections import defaultdict
from typing import Dict, Any, List, Tuple, Optional, Set
import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route
from cogs.utils.database import WordCounterDatabase
from cogs.utils.components import (
    BRAND_COLOR,
    SUCCESS_COLOR,
    WARNING_COLOR,
    create_v2_view,
    error_view,
    check_channel_with_config,
)

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Converts duration in seconds to a human-readable format.
    Converts values >= 60 seconds into minutes (and hours if >= 3600).
    """
    total_sec = int(round(seconds))
    if total_sec < 60:
        return f"{total_sec}s"
    hours, remainder = divmod(total_sec, 3600)
    minutes, rem_sec = divmod(remainder, 60)
    if hours > 0:
        if rem_sec > 0:
            return f"{hours}h {minutes}m {rem_sec}s"
        return f"{hours}h {minutes}m"
    if rem_sec == 0:
        return f"{minutes}m"
    return f"{minutes}m {rem_sec}s"


def compute_server_remaining_time(
    elapsed: float,
    idx: int,
    page_num: int,
    total_pages: int,
    total_pending: int,
) -> float:
    """Calculates dynamically updating estimated remaining time for whole server analysis."""
    if total_pending <= 0:
        return 0.0

    current_fraction = (page_num / max(1, total_pages)) if total_pages > 0 else 0.0
    processed_count = (idx - 1) + current_fraction
    remaining_members = total_pending - processed_count

    if remaining_members <= 0:
        return 0.0

    if processed_count >= 1.0 and elapsed > 0:
        avg_time_per_member = elapsed / processed_count
        return max(0.0, avg_time_per_member * remaining_members)
    else:
        # Initial estimate before 1st member is fully processed
        rem_current_pages = max(0, total_pages - page_num)
        rem_other_members = max(0, total_pending - idx)
        return float(rem_current_pages * 5.0 + rem_other_members * 6.0)


async def _safe_edit_message(message: discord.Message | discord.WebhookMessage, **kwargs) -> None:
    """Safely edits a message, falling back to channel.fetch_message if webhook token expired."""
    try:
        await message.edit(**kwargs)
    except discord.HTTPException:
        if hasattr(message, "channel") and message.channel is not None:
            try:
                chan_msg = await message.channel.fetch_message(message.id)
                await chan_msg.edit(**kwargs)
            except Exception as e:
                logger.error(f"Failed to edit message via channel fetch: {e}")
        else:
            logger.error("Failed to edit message and no channel reference available.")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")


class AnalyzeConfirmView(discord.ui.LayoutView):
    """Confirmation modal layout view warning the user that keywords must be set in /settings first."""

    def __init__(
        self,
        cog: "AnalyzeChat",
        author_id: int,
        target: Optional[discord.Member] = None,  # None means whole server
        keywords: Optional[List[str]] = None,
        eligible_count: int = 1,
    ) -> None:
        super().__init__(timeout=120.0)
        self.cog = cog
        self.author_id = author_id
        self.target = target
        self.keywords = keywords or []
        self.eligible_count = eligible_count
        self.confirmed: bool = False
        self._build_ui()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                view=error_view("Only the administrator who initiated this analysis can confirm it."),
                ephemeral=True,
            )
            return False
        return True

    def _build_ui(self) -> None:
        self.clear_items()

        if self.target is not None:
            scope_desc = f"**Target Member:** {self.target.mention}"
        else:
            scope_desc = (
                f"**Target Scope:** Entire Server (**{self.eligible_count:,}** non-bot members)\n"
                f"-# Members already analyzed will be automatically skipped."
            )

        if self.keywords:
            kw_preview = ", ".join(f"`{k}`" for k in self.keywords[:15])
            if len(self.keywords) > 15:
                kw_preview += f" *...and {len(self.keywords) - 15} more*"
            kw_section = f"✅ **Currently Tracked Keywords ({len(self.keywords)}):**\n{kw_preview}"
        else:
            kw_section = (
                "⚠️ **No Tracked Keywords Configured!**\n"
                "Keywords must be configured in `/settings` first in order to be counted in historical messages. "
                "If you proceed now, words, messages, and attachments will be counted, but no keywords will be tallied."
            )

        desc = (
            f"**Important Notice:** Keywords have to be set first in `/settings` to be counted during retroactive analysis.\n\n"
            f"{kw_section}\n\n"
            f"{scope_desc}\n\n"
            f"Do you want to proceed with retroactive analysis?"
        )

        container = discord.ui.Container(accent_colour=WARNING_COLOR)
        container.add_item(discord.ui.TextDisplay("### ⚠️ Confirm Retroactive Analysis"))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(desc))

        btn_confirm = discord.ui.Button(
            label="✅ Confirm & Proceed",
            style=discord.ButtonStyle.success,
        )
        btn_confirm.callback = self._on_confirm

        btn_cancel = discord.ui.Button(
            label="✖️ Cancel",
            style=discord.ButtonStyle.secondary,
        )
        btn_cancel.callback = self._on_cancel

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(btn_confirm, btn_cancel))
        self.add_item(container)

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        self.confirmed = True
        self.stop()
        init_view = create_v2_view(
            title="⏳ Initializing Retroactive Analysis...",
            description="Preparing search parameters and contacting Discord Search API...",
            color=WARNING_COLOR,
        )
        await interaction.response.edit_message(view=init_view)
        msg = interaction.message

        if self.target is not None:
            await self.cog._run_retroactive_analysis(interaction, self.target, status_msg=msg)
        else:
            await self.cog._run_whole_server_analysis(interaction, status_msg=msg)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.confirmed = False
        self.stop()
        cancel_view = create_v2_view(
            title="✖️ Analysis Cancelled",
            description="Retroactive chat analysis was cancelled. You can configure keywords in `/settings` at any time.",
            color=BRAND_COLOR,
        )
        await interaction.response.edit_message(view=cancel_view)

    async def on_timeout(self) -> None:
        self.stop()


class AnalyzeChat(commands.Cog):
    """
    Retroactively sweeps historical messages before the bot joined the server
    using Discord's Guild Message Search API (/guilds/{guild_id}/messages/search),
    integrating directly with WordCounterDatabase and Components V2 UI.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.running_guilds: Set[int] = set()
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
        self,
        interaction: discord.Interaction,
        target: discord.Member,
        status_msg: discord.Message | discord.WebhookMessage,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        self.running_guilds.add(guild.id)
        try:
            # 1. Double check if user has already been analyzed in this guild
            if await self.bot.db.is_user_analyzed(guild.id, target.id):
                await _safe_edit_message(
                    status_msg,
                    view=error_view(
                        f"{target.mention} has already been retroactively analyzed in this server.\n"
                        f"-# An administrator can unlock re-analysis for this user in `/settings` → **Data & Reset Tools**."
                    ),
                )
                return

            # 2. Load server tracking config & watched keywords from WordCounterDatabase
            watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(guild.id)
            keyword_list: List[str] = await self.bot.db.get_keywords(guild.id)

            # 3. Generate a Snowflake ID for when the bot joined to prevent double-counting
            bot_join_time = guild.me.joined_at if guild.me else None
            if bot_join_time is not None:
                max_id_snowflake = discord.utils.time_snowflake(bot_join_time)
            else:
                max_id_snowflake = discord.utils.time_snowflake(discord.utils.utcnow())

            try:
                data = await self._fetch_search_page(
                    guild.id, target.id, max_id_snowflake, offset=0
                )
            except discord.Forbidden:
                await _safe_edit_message(
                    status_msg,
                    view=error_view("I lack the `Read Message History` permission to perform this search."),
                )
                return
            except discord.HTTPException as e:
                await _safe_edit_message(
                    status_msg,
                    view=error_view(f"An API error occurred while querying Discord Search: `{e.status} - {e.text}`"),
                )
                return

            total_historical_messages = data.get("total_results", 0)

            if total_historical_messages == 0:
                await self.bot.db.mark_user_analyzed(guild.id, target.id)
                await _safe_edit_message(
                    status_msg,
                    view=create_v2_view(
                        title="🔍 Retroactive Analysis Complete",
                        description=f"No historical messages were found for {target.mention} prior to the bot joining.",
                        color=WARNING_COLOR,
                    ),
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
                    f"**Estimated Remaining:** `{format_duration(estimated_time)}` ({total_pages} page(s))"
                ),
                thumbnail_url=avatar_url,
                footer="Do not dismiss • Using Discord Guild Message Search API",
                color=WARNING_COLOR,
            )
            await _safe_edit_message(status_msg, view=status_view)

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
            start_time = asyncio.get_event_loop().time()

            # 4. Sweep and Tally with 5.0s pacing cushion
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
                                    guild, raw_channel_id, watched_ids, ignored_ids
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

                # Update progress every 2 pages or for short sweeps
                if page_num % 2 == 0 or total_pages <= 4:
                    elapsed = asyncio.get_event_loop().time() - start_time
                    remaining_pages = max(0, total_pages - page_num)
                    est_remaining = remaining_pages * 5.0
                    prog_view = create_v2_view(
                        title="⏳ Retroactive Deep-Sweep in Progress",
                        description=(
                            f"**Target:** {target.mention}\n"
                            f"**Progress:** `{min(offset, total_historical_messages):,} / {total_historical_messages:,}` messages scanned\n"
                            f"**Tallied So Far:** `{counted_messages:,}` messages • `{total_words:,}` words • `{total_attachments:,}` attachments\n\n"
                            f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`"
                        ),
                        thumbnail_url=avatar_url,
                        footer=f"Page {page_num}/{total_pages} • Pacing 5.0s per request",
                        color=WARNING_COLOR,
                    )
                    await _safe_edit_message(status_msg, view=prog_view)

                # Strict 5.0s anti-ratelimit cushion
                await asyncio.sleep(5.0)

                try:
                    data = await self._fetch_search_page(
                        guild.id, target.id, max_id_snowflake, offset=offset
                    )
                except Exception as e:
                    logger.error(f"Failed offset {offset} during retroactive sweep: {e}")
                    break

            # 5. Save everything back to WordCounterDatabase under asyncio.Lock + WAL
            await self.bot.db.save_retroactive_analysis(
                guild_id=guild.id,
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

            total_duration = asyncio.get_event_loop().time() - start_time
            fields = [
                ("💬 Old Messages Added", f"**{counted_messages:,}** *(of {total_historical_messages:,} indexed)*"),
                ("📝 Words Found & Added", f"**{total_words:,}**"),
                ("📎 Attachments Found & Added", f"**{total_attachments:,}**"),
            ]
            if keyword_list:
                kw_lines = [f"• **{kw}**: `{cnt:,}`" for kw, cnt in keyword_counts.items()]
                fields.append(("🔑 Tracked Keywords Added", "\n".join(kw_lines)))
            fields.append(("⏱️ Total Duration", f"`{format_duration(total_duration)}`"))

            complete_view = create_v2_view(
                title="✅ Retroactive Sync Complete",
                description=f"Successfully swept and added historical data for {target.mention} to the server database!",
                fields=fields,
                thumbnail_url=avatar_url,
                footer="Data retrieved via Discord Guild Search API • User marked as analyzed",
                color=SUCCESS_COLOR,
            )
            await _safe_edit_message(status_msg, view=complete_view)

        finally:
            self.running_guilds.discard(guild.id)

    async def _run_whole_server_analysis(
        self,
        interaction: discord.Interaction,
        status_msg: discord.Message | discord.WebhookMessage,
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return

        self.running_guilds.add(guild.id)
        try:
            # 1. Ensure guild members are fully cached
            if not guild.chunked:
                try:
                    await guild.chunk()
                except Exception as e:
                    logger.warning(f"Could not chunk guild {guild.id}: {e}")

            all_eligible_members = [m for m in guild.members if not m.bot]
            already_analyzed_ids = await self.bot.db.get_analyzed_users(guild.id)
            pending_members = [m for m in all_eligible_members if m.id not in already_analyzed_ids]
            skipped_already_count = len(all_eligible_members) - len(pending_members)

            if not pending_members:
                await _safe_edit_message(
                    status_msg,
                    view=create_v2_view(
                        title="🔍 Whole Server Analysis Complete",
                        description=f"All **{len(all_eligible_members):,}** non-bot members in this server have already been analyzed!",
                        footer="Use /settings → Data & Reset Tools to unlock re-analysis for specific users if needed",
                        color=SUCCESS_COLOR,
                    ),
                )
                return

            # 2. Snowflake cutoff for bot join time
            bot_join_time = guild.me.joined_at if guild.me else None
            if bot_join_time is not None:
                max_id_snowflake = discord.utils.time_snowflake(bot_join_time)
            else:
                max_id_snowflake = discord.utils.time_snowflake(discord.utils.utcnow())

            # 3. Server tracking config & watched keywords
            watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(guild.id)
            keyword_list: List[str] = await self.bot.db.get_keywords(guild.id)

            total_eligible = len(all_eligible_members)
            analyzed_count = 0
            skipped_no_messages_count = 0
            grand_total_words = 0
            grand_total_messages = 0
            grand_total_attachments = 0
            grand_keywords: Dict[str, int] = {k: 0 for k in keyword_list}

            start_time = asyncio.get_event_loop().time()

            # 4. Process each pending member one by one
            for idx, target in enumerate(pending_members, start=1):
                current_total_idx = skipped_already_count + idx
                elapsed = asyncio.get_event_loop().time() - start_time
                est_remaining = compute_server_remaining_time(
                    elapsed=elapsed,
                    idx=idx,
                    page_num=0,
                    total_pages=0,
                    total_pending=len(pending_members),
                )

                # Update status message before sweeping each member
                status_view = create_v2_view(
                    title="⏳ Whole Server Retroactive Deep-Sweep",
                    description=(
                        f"**Current Member ({current_total_idx}/{total_eligible}):** {target.mention}\n"
                        f"**Members Analyzed:** `{analyzed_count}` | **Skipped (Already Done):** `{skipped_already_count}`\n\n"
                        f"**Server Totals Added So Far:**\n"
                        f"• 💬 Messages: `{grand_total_messages:,}`\n"
                        f"• 📝 Words: `{grand_total_words:,}`\n"
                        f"• 📎 Attachments: `{grand_total_attachments:,}`\n\n"
                        f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`"
                    ),
                    thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                    footer=f"Member {current_total_idx}/{total_eligible} • Pacing 5.0s per request",
                    color=WARNING_COLOR,
                )
                await _safe_edit_message(status_msg, view=status_view)

                try:
                    data = await self._fetch_search_page(
                        guild.id, target.id, max_id_snowflake, offset=0
                    )
                except discord.Forbidden:
                    await _safe_edit_message(
                        status_msg,
                        view=error_view("I lack the `Read Message History` permission to perform searches in this server."),
                    )
                    return
                except Exception as e:
                    logger.error(f"Error querying search for user {target.id}: {e}")
                    continue

                total_user_messages = data.get("total_results", 0)

                if total_user_messages == 0:
                    await self.bot.db.mark_user_analyzed(guild.id, target.id)
                    skipped_no_messages_count += 1
                    await asyncio.sleep(1.0)
                    continue

                user_words = 0
                user_attachments = 0
                user_messages = 0
                user_keywords: Dict[str, int] = {k: 0 for k in keyword_list}

                channel_words: Dict[int, int] = defaultdict(int)
                channel_messages: Dict[int, int] = defaultdict(int)
                channel_attachments: Dict[int, int] = defaultdict(int)
                channel_keywords: Dict[Tuple[int, str], int] = defaultdict(int)

                offset = 0
                page_num = 0
                total_pages = (total_user_messages + 24) // 25

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
                                        guild, raw_channel_id, watched_ids, ignored_ids
                                    )
                                    if not is_watched:
                                        continue
                                else:
                                    eff_channel_id = raw_channel_id

                                content = msg.get("content", "") or ""
                                user_messages += 1
                                channel_messages[eff_channel_id] += 1

                                if content:
                                    words = content.split()
                                    w_len = len(words)
                                    user_words += w_len
                                    channel_words[eff_channel_id] += w_len

                                    content_lower = content.lower()
                                    for kw in keyword_list:
                                        matches = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
                                        if matches > 0:
                                            user_keywords[kw] += matches
                                            channel_keywords[(eff_channel_id, kw)] += matches

                                att_len = len(msg.get("attachments", []))
                                link_len = sum(
                                    1 for w in content.split() if w.startswith(("http://", "https://"))
                                )
                                msg_att_total = att_len + link_len
                                if msg_att_total > 0:
                                    user_attachments += msg_att_total
                                    channel_attachments[eff_channel_id] += msg_att_total

                    offset += 25
                    page_num += 1
                    if offset >= total_user_messages:
                        break

                    # Update progress every 2 pages if member has multiple pages or for short sweeps
                    if page_num % 2 == 0 or total_pages <= 4:
                        elapsed = asyncio.get_event_loop().time() - start_time
                        est_remaining = compute_server_remaining_time(
                            elapsed=elapsed,
                            idx=idx,
                            page_num=page_num,
                            total_pages=total_pages,
                            total_pending=len(pending_members),
                        )
                        prog_view = create_v2_view(
                            title="⏳ Whole Server Retroactive Deep-Sweep",
                            description=(
                                f"**Current Member ({current_total_idx}/{total_eligible}):** {target.mention}\n"
                                f"**Scanning Member Messages:** `{min(offset, total_user_messages):,} / {total_user_messages:,}` (Page {page_num}/{total_pages})\n"
                                f"**Members Analyzed:** `{analyzed_count}` | **Skipped (Already Done):** `{skipped_already_count}`\n\n"
                                f"**Server Totals Added So Far:**\n"
                                f"• 💬 Messages: `{grand_total_messages + user_messages:,}`\n"
                                f"• 📝 Words: `{grand_total_words + user_words:,}`\n"
                                f"• 📎 Attachments: `{grand_total_attachments + user_attachments:,}`\n\n"
                                f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`"
                            ),
                            thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                            footer=f"Member {current_total_idx}/{total_eligible} • Pacing 5.0s per request",
                            color=WARNING_COLOR,
                        )
                        await _safe_edit_message(status_msg, view=prog_view)

                    # 5.0s anti-ratelimit cushion
                    await asyncio.sleep(5.0)

                    try:
                        data = await self._fetch_search_page(
                            guild.id, target.id, max_id_snowflake, offset=offset
                        )
                    except Exception as e:
                        logger.error(f"Failed offset {offset} for member {target.id}: {e}")
                        break

                # Save member's stats to DB
                await self.bot.db.save_retroactive_analysis(
                    guild_id=guild.id,
                    user_id=target.id,
                    total_words=user_words,
                    counted_messages=user_messages,
                    total_attachments=user_attachments,
                    keyword_counts=user_keywords,
                    channel_words=channel_words,
                    channel_messages=channel_messages,
                    channel_attachments=channel_attachments,
                    channel_keywords=channel_keywords,
                )

                analyzed_count += 1
                grand_total_words += user_words
                grand_total_messages += user_messages
                grand_total_attachments += user_attachments
                for kw, cnt in user_keywords.items():
                    grand_keywords[kw] += cnt

                # Update progress after completing a member
                elapsed = asyncio.get_event_loop().time() - start_time
                est_remaining = compute_server_remaining_time(
                    elapsed=elapsed,
                    idx=idx + 1,
                    page_num=0,
                    total_pages=0,
                    total_pending=len(pending_members),
                )
                prog_view = create_v2_view(
                    title="⏳ Whole Server Retroactive Deep-Sweep",
                    description=(
                        f"**Completed Member ({current_total_idx}/{total_eligible}):** {target.mention}\n"
                        f"**Members Analyzed:** `{analyzed_count}` | **Skipped (Already Done):** `{skipped_already_count}`\n\n"
                        f"**Server Totals Added:**\n"
                        f"• 💬 Messages: `{grand_total_messages:,}`\n"
                        f"• 📝 Words: `{grand_total_words:,}`\n"
                        f"• 📎 Attachments: `{grand_total_attachments:,}`\n\n"
                        f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`"
                    ),
                    footer=f"Overall Progress: {current_total_idx}/{total_eligible} members ({int(current_total_idx / total_eligible * 100)}%)",
                    color=WARNING_COLOR,
                )
                await _safe_edit_message(status_msg, view=prog_view)

                # 5.0s pacing delay between members
                if idx < len(pending_members):
                    await asyncio.sleep(5.0)

            # 5. Analysis complete summary
            total_duration = asyncio.get_event_loop().time() - start_time
            fields = [
                (
                    "👥 Server Members Summary",
                    f"**{total_eligible:,}** total non-bot members\n"
                    f"• **{analyzed_count:,}** analyzed with messages\n"
                    f"• **{skipped_no_messages_count:,}** had no prior messages\n"
                    f"• **{skipped_already_count:,}** were previously analyzed",
                ),
                ("💬 Historical Messages Added", f"**{grand_total_messages:,}**"),
                ("📝 Historical Words Added", f"**{grand_total_words:,}**"),
                ("📎 Historical Attachments Added", f"**{grand_total_attachments:,}**"),
            ]
            if keyword_list:
                kw_lines = [f"• **{kw}**: `{cnt:,}`" for kw, cnt in grand_keywords.items()]
                fields.append(("🔑 Tracked Keywords Added", "\n".join(kw_lines)))
            fields.append(("⏱️ Total Elapsed Time", f"`{format_duration(total_duration)}`"))

            complete_view = create_v2_view(
                title="✅ Whole Server Retroactive Sync Complete",
                description="Successfully analyzed all server members and integrated historical data into the server database!",
                fields=fields,
                footer="Data retrieved via Discord Guild Search API • All eligible members marked as analyzed",
                color=SUCCESS_COLOR,
            )
            await _safe_edit_message(status_msg, view=complete_view)

        finally:
            self.running_guilds.discard(guild.id)

    analyze_chat = app_commands.Group(
        name="analyze_chat",
        description="Retroactively analyze historical messages sent before the bot joined the server",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @analyze_chat.command(
        name="single_user",
        description="Retroactively analyze a single user's chat history before the bot joined",
    )
    @app_commands.describe(target="The server member to retroactively analyze")
    async def single_user(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                view=error_view("This command can only be used inside a server."),
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                view=error_view("You need the `Manage Server` permission to use this command."),
                ephemeral=True,
            )
            return

        if target.bot:
            await interaction.response.send_message(
                view=error_view("Bots cannot be retroactively analyzed for word counts."),
                ephemeral=True,
            )
            return

        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            await interaction.response.send_message(
                view=error_view("Word counting is not enabled on this server! Use `/settings` to enable it first."),
                ephemeral=True,
            )
            return

        if interaction.guild.id in self.running_guilds:
            await interaction.response.send_message(
                view=error_view("A retroactive analysis is already running in this server. Please wait for it to complete."),
                ephemeral=True,
            )
            return

        if await self.bot.db.is_user_analyzed(interaction.guild.id, target.id):
            await interaction.response.send_message(
                view=error_view(
                    f"{target.mention} has already been retroactively analyzed in this server.\n"
                    f"-# An administrator can unlock re-analysis for this user in `/settings` → **Data & Reset Tools**."
                ),
                ephemeral=True,
            )
            return

        keywords = await self.bot.db.get_keywords(interaction.guild.id)
        confirm_view = AnalyzeConfirmView(
            cog=self,
            author_id=interaction.user.id,
            target=target,
            keywords=keywords,
            eligible_count=1,
        )
        await interaction.response.send_message(view=confirm_view)

    @analyze_chat.command(
        name="whole_server",
        description="Retroactively analyze all server members' chat history before the bot joined",
    )
    async def whole_server(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                view=error_view("This command can only be used inside a server."),
                ephemeral=True,
            )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                view=error_view("You need the `Manage Server` permission to use this command."),
                ephemeral=True,
            )
            return

        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            await interaction.response.send_message(
                view=error_view("Word counting is not enabled on this server! Use `/settings` to enable it first."),
                ephemeral=True,
            )
            return

        if interaction.guild.id in self.running_guilds:
            await interaction.response.send_message(
                view=error_view("A retroactive analysis is already running in this server. Please wait for it to complete."),
                ephemeral=True,
            )
            return

        if not interaction.guild.chunked:
            try:
                await interaction.guild.chunk()
            except Exception as e:
                logger.warning(f"Could not chunk guild {interaction.guild.id}: {e}")

        all_eligible = [m for m in interaction.guild.members if not m.bot]
        keywords = await self.bot.db.get_keywords(interaction.guild.id)

        confirm_view = AnalyzeConfirmView(
            cog=self,
            author_id=interaction.user.id,
            target=None,
            keywords=keywords,
            eligible_count=len(all_eligible),
        )
        await interaction.response.send_message(view=confirm_view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyzeChat(bot))