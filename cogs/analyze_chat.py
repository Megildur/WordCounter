from __future__ import annotations
import asyncio
from datetime import datetime
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
    count_emojis,
)

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
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
    analyzed_count: int = 0,
    total_pages_scanned: int = 0,
    active_servers: int = 1,
) -> float:
    if total_pending <= 0 or idx > total_pending:
        return 0.0

    current_sleep = 10.0 + (max(0, active_servers - 1) * 5.0)
    SEC_PER_PAGE = current_sleep + 0.5
    SEC_INTER_MEMBER = current_sleep
    SEC_ZERO_MEMBER = 2.0

    total_pages_done = total_pages_scanned + page_num
    if total_pages_done > 2 and elapsed > 0:
        measured_sec_per_page = elapsed / total_pages_done
        effective_sec_per_page = max(current_sleep, min(current_sleep + 25.0, (SEC_PER_PAGE * 0.3) + (measured_sec_per_page * 0.7)))
    else:
        effective_sec_per_page = SEC_PER_PAGE

    if total_pages > 0:
        remaining_pages = max(0, total_pages - page_num)
        current_member_time = remaining_pages * effective_sec_per_page
        future_members_count = max(0, total_pending - idx)
    else:
        current_member_time = 0.0
        future_members_count = max(0, total_pending - idx + 1)

    if future_members_count == 0:
        return float(max(0.0, current_member_time))

    completed_members = max(0, idx - 1)

    p_active = (analyzed_count + 1.2) / (completed_members + 3.0)
    p_active = min(1.0, max(0.05, p_active))

    effective_pages_scanned = total_pages_scanned + (total_pages if total_pages > 0 else 0)
    effective_analyzed = analyzed_count + (1 if total_pages > 0 else 0)
    avg_pages_active = (effective_pages_scanned + 4.0) / (effective_analyzed + 1.0)
    avg_pages_active = max(1.0, avg_pages_active)

    active_member_time = (avg_pages_active * effective_sec_per_page) + SEC_INTER_MEMBER
    inactive_member_time = SEC_ZERO_MEMBER
    expected_sec_per_member = (p_active * active_member_time) + ((1.0 - p_active) * inactive_member_time)
    expected_sec_per_member = max(3.5, expected_sec_per_member)

    future_members_time = future_members_count * expected_sec_per_member

    if total_pages > 0 and future_members_count > 0:
        current_member_time += SEC_INTER_MEMBER

    return float(max(0.0, current_member_time + future_members_time))


async def _safe_edit_message(message: discord.Message | discord.WebhookMessage, **kwargs) -> None:
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

    def __init__(
        self,
        cog: "AnalyzeChat",
        author_id: int,
        target: Optional[discord.Member] = None,
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

        time_warning = (
            "⏳ **Duration Notice (Takes a Long Time):**\n"
            "To prevent Discord search rate limits, this command uses safety pacing (10+ seconds per page of 25 messages, scaling by +5s per additional active server). "
            "Sweeping thousands of messages will take time."
        )

        desc = (
            f"**Important Notice:** Keywords have to be set first in `/settings` to be counted during retroactive analysis.\n\n"
            f"{kw_section}\n\n"
            f"{scope_desc}\n\n"
            f"{time_warning}\n\n"
            f"Do you want to proceed with retroactive analysis?"
        )

        container = discord.ui.Container(accent_colour=WARNING_COLOR)
        container.add_item(discord.ui.TextDisplay("### ⚠️ Confirm Retroactive Analysis (Takes a Long Time)"))
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


class MemberScanState:

    def __init__(self, target: discord.Member, total_user_messages: int, max_id: int) -> None:
        self.target = target
        self.total_user_messages = total_user_messages
        self.current_max_id = max_id
        self.oldest_target_msg_id: Optional[int] = None
        self.last_shifted_max_id: Optional[int] = max_id
        self.offset = 0
        self.page_num = 0
        self.total_pages = (total_user_messages + 24) // 25
        self.user_words = 0
        self.user_attachments = 0
        self.user_emojis = 0
        self.user_messages = 0
        self.user_keywords: Dict[str, int] = defaultdict(int)
        self.channel_words: Dict[int, int] = defaultdict(int)
        self.channel_messages: Dict[int, int] = defaultdict(int)
        self.channel_attachments: Dict[int, int] = defaultdict(int)
        self.channel_emojis: Dict[int, int] = defaultdict(int)
        self.channel_keywords: Dict[Tuple[int, str], int] = defaultdict(int)
        self.monthly_stats: Dict[Tuple[int, int, int], Dict[str, int]] = defaultdict(
            lambda: {"words": 0, "messages": 0, "attachments": 0, "emojis": 0}
        )
        self.monthly_keywords: Dict[Tuple[int, str, int, int], int] = defaultdict(int)
        self.passes_attempted = 0
        self.completed = False
        self.seen_msg_ids: Set[int] = set()
        self.deferred_empty_cursor: Optional[Tuple[int, int]] = None
        self.allow_advancing_blanks: bool = False
        self.consecutive_stalls: int = 0
        self.blank_pages: List[Tuple[int, int]] = []
        self.recorded_blank_cursors: Set[Tuple[int, int]] = set()


class AnalyzeChat(commands.Cog):

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.running_guilds: Set[int] = set()
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)
        print("AnalyzeChat cog loaded")

    def get_search_sleep_delay(self) -> float:
        active_count = max(1, len(self.running_guilds))
        return 10.0 + (max(0, active_count - 1) * 5.0)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def _fetch_search_page(
        self, guild_id: int, author_id: int, max_id: int, offset: int = 0, max_retries: int = 5
    ) -> Dict[str, Any]:
        route = Route("GET", "/guilds/{guild_id}/messages/search", guild_id=guild_id)
        params = {
            "author_id": author_id,
            "max_id": max_id,
            "offset": offset,
            "include_nsfw": "true",
        }
        retries = 0

        while True:
            try:
                data = await self.bot.http.request(route, params=params)

                if not isinstance(data, dict) or "messages" not in data or data.get("message") == "Indexing":
                    retry_after = data.get("retry_after", 5) if isinstance(data, dict) else 5
                    print(f"[ANALYSIS NOTICE] Discord search index building for guild {guild_id}. Waiting {retry_after}s...")
                    await asyncio.sleep(float(retry_after))
                    continue

                return data

            except discord.RateLimited as e:
                retry_after = max(10.0, float(getattr(e, "retry_after", 15.0)))
                retries += 1
                print(f"[ANALYSIS RATE LIMIT] Discord search rate limit hit. Waiting {retry_after:.1f}s (retry {retries}/{max_retries})...")
                await asyncio.sleep(retry_after)
                if retries >= max_retries:
                    raise e
                continue

            except discord.HTTPException as e:
                if e.status == 202:
                    print(f"[ANALYSIS NOTICE] HTTP 202: Search index building. Retrying in 5s...")
                    await asyncio.sleep(5.0)
                    continue

                if e.status == 429:
                    retry_after = 15.0
                    if hasattr(e, "response") and hasattr(e.response, "headers"):
                        hdr = e.response.headers.get("Retry-After")
                        if hdr:
                            try:
                                retry_after = max(10.0, float(hdr))
                            except Exception:
                                pass
                    retries += 1
                    print(f"[ANALYSIS RATE LIMIT] HTTP 429: Search rate limit hit. Waiting {retry_after:.1f}s (retry {retries}/{max_retries})...")
                    await asyncio.sleep(retry_after)
                    if retries >= max_retries:
                        raise e
                    continue

                if e.status in (500, 502, 503, 504, 524):
                    retries += 1
                    backoff = min(60.0, 5.0 * (2 ** (retries - 1)))
                    print(f"[ANALYSIS SERVER ERROR] HTTP {e.status}: Discord server error. Retrying in {backoff:.1f}s (retry {retries}/{max_retries})...")
                    await asyncio.sleep(backoff)
                    if retries >= max_retries:
                        raise e
                    continue

                if e.status == 400 and offset >= 4000:
                    print(f"[ANALYSIS NOTICE] Reached Discord search offset limit ({offset} messages) for member {author_id}.")
                    return {"messages": [], "total_results": offset}

                raise e

    async def _execute_member_scan(
        self,
        guild: discord.Guild,
        state: MemberScanState,
        watched_ids: Set[int],
        ignored_ids: Set[int],
        keyword_list: List[str],
        thread_parent_map: Dict[int, int],
        unknown_channel_ids: Optional[Set[int]] = None,
        on_progress: Optional[Any] = None,
    ) -> None:
        target = state.target
        total_user_messages = state.total_user_messages
        total_pages = state.total_pages

        if state.page_num >= total_pages or (total_user_messages > 0 and state.user_messages >= total_user_messages) or total_user_messages == 0:
            return

        try:
            data = await self._fetch_search_page(
                guild.id, target.id, state.current_max_id, offset=state.offset
            )
        except Exception as e:
            print(f"[ANALYSIS ERROR] '{guild.name}' -> '{target.display_name}': Initial search fetch failed: {e}")
            return

        while True:
            for th in data.get("threads", []):
                if isinstance(th, dict) and "id" in th and "parent_id" in th:
                    thread_parent_map[int(th["id"])] = int(th["parent_id"])

            messages_array = data.get("messages", [])

            if not messages_array:
                if state.page_num >= total_pages or (total_user_messages > 0 and state.user_messages >= total_user_messages):
                    print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Reached completion ({state.page_num}/{total_pages} pages, {state.user_messages:,}/{total_user_messages:,} messages). Member sweep complete.")
                    break

                attempt = 1
                while not messages_array and attempt < 4:
                    attempt += 1
                    print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Received empty page at page {state.page_num + 1}/{total_pages} (offset {state.offset}). Retrying ({attempt}/4)...")
                    await asyncio.sleep(self.get_search_sleep_delay())
                    try:
                        retry_data = await self._fetch_search_page(
                            guild.id, target.id, state.current_max_id, offset=state.offset
                        )
                        retry_messages = retry_data.get("messages", [])
                    except Exception as e:
                        print(f"[ANALYSIS ERROR] '{guild.name}' -> '{target.display_name}': Retry {attempt}/4 failed at offset {state.offset}: {e}")
                        retry_messages = []
                        retry_data = {}

                    if retry_messages:
                        print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Retry {attempt}/4 succeeded with {len(retry_messages)} message batches for page {state.page_num + 1}. Resuming scan.")
                        data = retry_data
                        messages_array = retry_messages
                        for th in data.get("threads", []):
                            if isinstance(th, dict) and "id" in th and "parent_id" in th:
                                thread_parent_map[int(th["id"])] = int(th["parent_id"])
                        break

                if not messages_array:
                    current_cursor = (state.current_max_id, state.offset)
                    if current_cursor not in state.recorded_blank_cursors:
                        state.recorded_blank_cursors.add(current_cursor)
                        state.blank_pages.append(current_cursor)

                    if state.allow_advancing_blanks or state.deferred_empty_cursor == current_cursor:
                        state.allow_advancing_blanks = True
                        state.deferred_empty_cursor = None
                        state.offset += 25
                        state.page_num += 1
                        scanned_display = min(state.page_num * 25, total_user_messages)
                        print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Page {state.page_num}/{total_pages} still empty after 4 tries. Advancing to next page (offset {state.offset})...")
                        if on_progress:
                            await on_progress(state)
                        if state.page_num >= total_pages or (total_user_messages > 0 and state.user_messages >= total_user_messages):
                            print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Reached completion ({state.page_num}/{total_pages} pages, {state.user_messages:,}/{total_user_messages:,} messages). Member sweep complete.")
                            break
                        if state.offset >= 5000:
                            if state.oldest_target_msg_id is not None:
                                new_max_id = state.oldest_target_msg_id - 1
                                if state.last_shifted_max_id is None or new_max_id < state.last_shifted_max_id:
                                    state.last_shifted_max_id = new_max_id
                                    state.current_max_id = new_max_id
                                    state.offset = 0
                                    print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Reached 5,000 message offset limit at page {state.page_num}/{total_pages}. Shifting search window cursor backward (max_id {state.current_max_id})...")
                            else:
                                break
                        await asyncio.sleep(self.get_search_sleep_delay())
                        try:
                            data = await self._fetch_search_page(
                                guild.id, target.id, state.current_max_id, offset=state.offset
                            )
                        except Exception as e:
                            print(f"[ANALYSIS ERROR] '{guild.name}' -> '{target.display_name}': Failed search query at offset {state.offset}: {e}")
                            break
                        continue
                    else:
                        state.deferred_empty_cursor = current_cursor
                        print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Discord returned 0 messages at page {state.page_num + 1} (offset {state.offset}) after 4 tries. Pausing member to circle back on next pass ({state.user_messages:,} messages counted).")
                        break

            found_any = False
            for hit in messages_array:
                for msg in hit:
                    if msg.get("author", {}).get("id") == str(target.id):
                        m_id_str = msg.get("id")
                        if not m_id_str:
                            continue
                        try:
                            m_id_int = int(m_id_str)
                        except Exception:
                            continue
                        if m_id_int <= 0:
                            continue
                        if state.oldest_target_msg_id is None or m_id_int < state.oldest_target_msg_id:
                            state.oldest_target_msg_id = m_id_int
                        if m_id_int in state.seen_msg_ids:
                            continue
                        state.seen_msg_ids.add(m_id_int)
                        found_any = True

                        raw_channel_id = int(msg.get("channel_id", 0))
                        if raw_channel_id not in thread_parent_map:
                            ch_obj = guild.get_channel_or_thread(raw_channel_id)
                            if ch_obj is not None:
                                p_id = getattr(ch_obj, "parent_id", None)
                                if p_id:
                                    thread_parent_map[raw_channel_id] = p_id
                            elif unknown_channel_ids is not None and raw_channel_id not in unknown_channel_ids:
                                try:
                                    fetched_ch = await guild.fetch_channel(raw_channel_id)
                                    if fetched_ch is not None:
                                        p_id = getattr(fetched_ch, "parent_id", None)
                                        if p_id:
                                            thread_parent_map[raw_channel_id] = p_id
                                except Exception:
                                    unknown_channel_ids.add(raw_channel_id)

                        if watched_ids:
                            is_watched, eff_channel_id = check_channel_with_config(
                                guild, raw_channel_id, watched_ids, ignored_ids, thread_parent_map=thread_parent_map
                            )
                            if not is_watched:
                                continue
                        else:
                            eff_channel_id = thread_parent_map.get(raw_channel_id, raw_channel_id)

                        ts_str = msg.get("timestamp")
                        if ts_str:
                            try:
                                dt = datetime.fromisoformat(ts_str)
                                msg_year = dt.year
                                msg_month = dt.month
                            except Exception:
                                msg_year = 2024
                                msg_month = 1
                        else:
                            msg_year = 2024
                            msg_month = 1

                        m_key = (eff_channel_id, msg_year, msg_month)
                        content = msg.get("content", "") or ""
                        state.user_messages += 1
                        state.channel_messages[eff_channel_id] += 1
                        state.monthly_stats[m_key]["messages"] += 1

                        if content:
                            words = content.split()
                            w_len = len(words)
                            state.user_words += w_len
                            state.channel_words[eff_channel_id] += w_len
                            state.monthly_stats[m_key]["words"] += w_len

                            e_cnt = count_emojis(content)
                            if e_cnt > 0:
                                state.user_emojis += e_cnt
                                state.channel_emojis[eff_channel_id] += e_cnt
                                state.monthly_stats[m_key]["emojis"] += e_cnt

                            content_lower = content.lower()
                            for kw in keyword_list:
                                matches = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
                                if matches > 0:
                                    state.user_keywords[kw] += matches
                                    state.channel_keywords[(eff_channel_id, kw)] += matches
                                    state.monthly_keywords[(eff_channel_id, kw, msg_year, msg_month)] += matches

                        sticker_len = len(msg.get("sticker_items", [])) + len(msg.get("stickers", []))
                        att_len = len(msg.get("attachments", [])) + sticker_len
                        link_len = sum(
                            1 for w in content.split() if w.strip('<>()"\'').startswith(("http://", "https://"))
                        )
                        msg_att_total = att_len + link_len
                        if msg_att_total > 0:
                            state.user_attachments += msg_att_total
                            state.channel_attachments[eff_channel_id] += msg_att_total
                            state.monthly_stats[m_key]["attachments"] += msg_att_total

            if found_any:
                state.allow_advancing_blanks = False
                state.deferred_empty_cursor = None
                current_cursor = (state.current_max_id, state.offset)
                if current_cursor in state.recorded_blank_cursors:
                    state.recorded_blank_cursors.discard(current_cursor)
                    if current_cursor in state.blank_pages:
                        state.blank_pages.remove(current_cursor)

            state.offset += 25
            state.page_num += 1

            if state.offset >= 5000 and state.oldest_target_msg_id is not None:
                new_max_id = state.oldest_target_msg_id - 1
                if state.last_shifted_max_id is None or new_max_id < state.last_shifted_max_id:
                    state.last_shifted_max_id = new_max_id
                    state.current_max_id = new_max_id
                    state.offset = 0
                    print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Reached 5,000 message offset limit. Shifting search window backward in time (page {state.page_num}/{total_pages})...")

            if state.page_num >= total_pages or (total_user_messages > 0 and state.user_messages >= total_user_messages):
                print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': Reached completion ({state.page_num}/{total_pages} pages, {state.user_messages:,}/{total_user_messages:,} messages). Member sweep complete.")
                break

            if on_progress and (state.page_num == 1 or state.page_num % 2 == 0 or total_pages <= 6):
                await on_progress(state)

            await asyncio.sleep(self.get_search_sleep_delay())

            try:
                data = await self._fetch_search_page(
                    guild.id, target.id, state.current_max_id, offset=state.offset
                )
            except Exception as e:
                print(f"[ANALYSIS ERROR] '{guild.name}' -> '{target.display_name}': Failed search query at offset {state.offset}: {e}")
                break

    async def _verify_blank_pages(
        self,
        guild: discord.Guild,
        state: MemberScanState,
        watched_ids: Set[int],
        ignored_ids: Set[int],
        keyword_list: List[str],
        thread_parent_map: Dict[int, int],
        unknown_channel_ids: Optional[Set[int]] = None,
        on_progress: Optional[Any] = None,
        max_passes: int = 1,
        pass_name: str = "Blank Verification",
    ) -> None:
        target = state.target
        total_recovered = 0

        for current_v_pass in range(1, max_passes + 1):
            if not state.blank_pages:
                break
            if state.total_user_messages > 0 and state.user_messages >= state.total_user_messages:
                state.blank_pages.clear()
                state.recorded_blank_cursors.clear()
                break

            blank_list = list(state.blank_pages)
            pass_recovered = 0
            print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': {pass_name} (Pass {current_v_pass}/{max_passes}) checking {len(blank_list)} blank page(s)...")

            for b_max_id, b_offset in blank_list:
                if state.total_user_messages > 0 and state.user_messages >= state.total_user_messages:
                    state.blank_pages.clear()
                    state.recorded_blank_cursors.clear()
                    break

                messages_array = []
                data = {}
                for attempt in range(1, 5):
                    await asyncio.sleep(self.get_search_sleep_delay())
                    try:
                        data = await self._fetch_search_page(guild.id, target.id, b_max_id, offset=b_offset)
                        messages_array = data.get("messages", [])
                        if messages_array:
                            break
                    except Exception as e:
                        print(f"[ANALYSIS ERROR] '{guild.name}' -> '{target.display_name}': {pass_name} (Pass {current_v_pass}/{max_passes}) attempt {attempt}/4 failed at offset {b_offset}: {e}")

                if not messages_array:
                    continue

                for th in data.get("threads", []):
                    if isinstance(th, dict) and "id" in th and "parent_id" in th:
                        thread_parent_map[int(th["id"])] = int(th["parent_id"])

                c_tuple = (b_max_id, b_offset)
                if c_tuple in state.recorded_blank_cursors:
                    state.recorded_blank_cursors.discard(c_tuple)
                if c_tuple in state.blank_pages:
                    state.blank_pages.remove(c_tuple)

                for hit in messages_array:
                    for msg in hit:
                        if msg.get("author", {}).get("id") == str(target.id):
                            m_id_str = msg.get("id")
                            if not m_id_str:
                                continue
                            try:
                                m_id_int = int(m_id_str)
                            except Exception:
                                continue
                            if m_id_int <= 0:
                                continue
                            if state.oldest_target_msg_id is None or m_id_int < state.oldest_target_msg_id:
                                state.oldest_target_msg_id = m_id_int
                            if m_id_int in state.seen_msg_ids:
                                continue
                            state.seen_msg_ids.add(m_id_int)
                            pass_recovered += 1
                            total_recovered += 1

                            raw_channel_id = int(msg.get("channel_id", 0))
                            if raw_channel_id not in thread_parent_map:
                                ch_obj = guild.get_channel_or_thread(raw_channel_id)
                                if ch_obj is not None:
                                    p_id = getattr(ch_obj, "parent_id", None)
                                    if p_id:
                                        thread_parent_map[raw_channel_id] = p_id
                                elif unknown_channel_ids is not None and raw_channel_id not in unknown_channel_ids:
                                    try:
                                        fetched_ch = await guild.fetch_channel(raw_channel_id)
                                        if fetched_ch is not None:
                                            p_id = getattr(fetched_ch, "parent_id", None)
                                            if p_id:
                                                thread_parent_map[raw_channel_id] = p_id
                                    except Exception:
                                        unknown_channel_ids.add(raw_channel_id)

                            if watched_ids:
                                is_watched, eff_channel_id = check_channel_with_config(
                                    guild, raw_channel_id, watched_ids, ignored_ids, thread_parent_map=thread_parent_map
                                )
                                if not is_watched:
                                    continue
                            else:
                                eff_channel_id = thread_parent_map.get(raw_channel_id, raw_channel_id)

                            ts_str = msg.get("timestamp")
                            if ts_str:
                                try:
                                    dt = datetime.fromisoformat(ts_str)
                                    msg_year = dt.year
                                    msg_month = dt.month
                                except Exception:
                                    msg_year = 2024
                                    msg_month = 1
                            else:
                                msg_year = 2024
                                msg_month = 1

                            m_key = (eff_channel_id, msg_year, msg_month)
                            content = msg.get("content", "") or ""
                            state.user_messages += 1
                            state.channel_messages[eff_channel_id] += 1
                            state.monthly_stats[m_key]["messages"] += 1

                            if content:
                                words = content.split()
                                w_len = len(words)
                                state.user_words += w_len
                                state.channel_words[eff_channel_id] += w_len
                                state.monthly_stats[m_key]["words"] += w_len

                                e_cnt = count_emojis(content)
                                if e_cnt > 0:
                                    state.user_emojis += e_cnt
                                    state.channel_emojis[eff_channel_id] += e_cnt
                                    state.monthly_stats[m_key]["emojis"] += e_cnt

                                content_lower = content.lower()
                                for kw in keyword_list:
                                    matches = len(re.findall(r"\b" + re.escape(kw.lower()) + r"\b", content_lower))
                                    if matches > 0:
                                        state.user_keywords[kw] += matches
                                        state.channel_keywords[(eff_channel_id, kw)] += matches
                                        state.monthly_keywords[(eff_channel_id, kw, msg_year, msg_month)] += matches

                            sticker_len = len(msg.get("sticker_items", [])) + len(msg.get("stickers", []))
                            att_len = len(msg.get("attachments", [])) + sticker_len
                            link_len = sum(
                                1 for w in content.split() if w.strip('<>()"\'').startswith(("http://", "https://"))
                            )
                            msg_att_total = att_len + link_len
                            if msg_att_total > 0:
                                state.user_attachments += msg_att_total
                                state.channel_attachments[eff_channel_id] += msg_att_total
                                state.monthly_stats[m_key]["attachments"] += msg_att_total

            if pass_recovered > 0:
                print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': {pass_name} (Pass {current_v_pass}/{max_passes}) recovered {pass_recovered:,} additional messages ({state.user_messages:,} total counted)!")
                if on_progress:
                    await on_progress(state)

        if total_recovered > 0:
            print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': {pass_name} finished — recovered {total_recovered:,} total messages ({state.user_messages:,} counted).")
        if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
            print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': {len(state.blank_pages)} page(s) remained empty after {max_passes} pass(es) of {pass_name}.")


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
            if await self.bot.db.is_user_analyzed(guild.id, target.id):
                await _safe_edit_message(
                    status_msg,
                    view=error_view(
                        f"{target.mention} has already been retroactively analyzed in this server.\n"
                        f"-# An administrator can unlock re-analysis for this user in `/settings` → **Data & Reset Tools**."
                    ),
                )
                return

            watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(guild.id)
            keyword_list: List[str] = await self.bot.db.get_keywords(guild.id)

            command_time = interaction.created_at if interaction.created_at else discord.utils.utcnow()
            max_id_snowflake = discord.utils.time_snowflake(command_time)

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
                        description=f"No historical messages were found for {target.mention} up to the command execution time.",
                        color=WARNING_COLOR,
                    ),
                )
                return

            total_pages = (total_historical_messages + 24) // 25
            current_sleep = self.get_search_sleep_delay()
            estimated_time = max(1, total_pages - 1) * (current_sleep + 0.5) if total_pages > 1 else current_sleep
            avatar_url = target.display_avatar.url if target.display_avatar else None

            print(f"\n[ANALYSIS ACTIVE] ⏳ Single-user analysis started in '{guild.name}' ({guild.id}) for '{target.display_name}' ({target.id}) (Pacing: {current_sleep:.1f}s, Active Servers: {len(self.running_guilds)})")

            status_view = create_v2_view(
                title="⏳ Retroactive Deep-Sweep in Progress (Takes a Long Time)",
                description=(
                    f"Found **{total_historical_messages:,}** historical messages for {target.mention} up to the command execution time.\n"
                    f"Sweeping history with strict **{current_sleep:.1f}s** anti-ratelimit pacing ({len(self.running_guilds)} active server(s))...\n\n"
                    f"**Estimated Remaining:** `{format_duration(estimated_time)}` ({total_pages} page(s))\n"
                    f"-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                ),
                thumbnail_url=avatar_url,
                footer=f"Do not dismiss • Pacing {current_sleep:.1f}s per request • {len(self.running_guilds)} active server(s)",
                color=WARNING_COLOR,
            )
            await _safe_edit_message(status_msg, view=status_view)

            start_time = asyncio.get_event_loop().time()
            thread_parent_map: Dict[int, int] = {}
            for th in guild.threads:
                thread_parent_map[th.id] = th.parent_id
            unknown_channel_ids: Set[int] = set()
            state = MemberScanState(target, total_historical_messages, max_id_snowflake)

            async def on_single_progress(s: MemberScanState) -> None:
                current_pacing = self.get_search_sleep_delay()
                active_servers = len(self.running_guilds)
                elapsed = asyncio.get_event_loop().time() - start_time
                remaining_pages = max(0, s.total_pages - s.page_num)
                measured_sec_per_page = (elapsed / s.page_num) if s.page_num > 0 else (current_pacing + 0.5)
                sec_per_page = max(current_pacing, min(current_pacing + 25.0, ((current_pacing + 0.5) * 0.3) + (measured_sec_per_page * 0.7)))
                est_remaining = remaining_pages * sec_per_page
                scanned_display = min(s.page_num * 25, s.total_user_messages)
                print(f"[ANALYSIS IN PROGRESS] '{guild.name}' -> '{target.display_name}': page {s.page_num}/{s.total_pages} (scanned {scanned_display:,}/{s.total_user_messages:,}, elapsed: {format_duration(elapsed)}, remaining: {format_duration(est_remaining)})")
                prog_view = create_v2_view(
                    title="⏳ Retroactive Deep-Sweep in Progress (Takes a Long Time)",
                    description=(
                        f"**Target:** {target.mention}\n"
                        f"**Progress:** `{scanned_display:,} / {s.total_user_messages:,}` messages scanned\n"
                        f"**Tallied So Far:** `{s.user_messages:,}` messages • `{s.user_words:,}` words • `{s.user_attachments:,}` attachments • `{s.user_emojis:,}` emojis\n\n"
                        f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`\n"
                        f"-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                    ),
                    thumbnail_url=avatar_url,
                    footer=f"Page {s.page_num}/{s.total_pages} • Pacing {current_pacing:.1f}s per request ({active_servers} active server(s))",
                    color=WARNING_COLOR,
                )
                await _safe_edit_message(status_msg, view=prog_view)

            await self._execute_member_scan(
                guild=guild,
                state=state,
                watched_ids=watched_ids,
                ignored_ids=ignored_ids,
                keyword_list=keyword_list,
                thread_parent_map=thread_parent_map,
                unknown_channel_ids=unknown_channel_ids,
                on_progress=on_single_progress,
            )

            current_pass = 1
            consecutive_stalls = 0
            while not (state.page_num >= state.total_pages or (state.total_user_messages > 0 and state.user_messages >= state.total_user_messages) or state.total_user_messages == 0):
                prev_messages = state.user_messages
                prev_pages = state.page_num
                current_pass += 1
                state.passes_attempted += 1
                scanned_display = min(state.page_num * 25, state.total_user_messages)
                print(f"[ANALYSIS RETRY] '{guild.name}' -> '{target.display_name}': Scanned {scanned_display:,}/{state.total_user_messages:,} messages ({state.user_messages:,} counted). Pausing 30s before pass {current_pass}...")
                pause_view = create_v2_view(
                    title=f"⏳ Retroactive Deep-Sweep — Retry Pass {current_pass} (Takes a Long Time)",
                    description=(
                        f"**Target:** {target.mention}\n"
                        f"**Progress:** `{scanned_display:,} / {state.total_user_messages:,}` messages scanned\n"
                        f"**Tallied So Far:** `{state.user_messages:,}` messages • `{state.user_words:,}` words • `{state.user_attachments:,}` attachments • `{state.user_emojis:,}` emojis\n\n"
                        f"-# ⏳ Resuming search from cursor (page {state.page_num + 1}/{state.total_pages}) after cooldown..."
                    ),
                    thumbnail_url=avatar_url,
                    footer=f"Retry Pass {current_pass} • Pacing {self.get_search_sleep_delay():.1f}s",
                    color=WARNING_COLOR,
                )
                await _safe_edit_message(status_msg, view=pause_view)
                await asyncio.sleep(30.0)

                if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
                    await self._verify_blank_pages(
                        guild=guild,
                        state=state,
                        watched_ids=watched_ids,
                        ignored_ids=ignored_ids,
                        keyword_list=keyword_list,
                        thread_parent_map=thread_parent_map,
                        unknown_channel_ids=unknown_channel_ids,
                        on_progress=on_single_progress,
                        max_passes=1,
                        pass_name=f"Preemptive Blank Check [Pass {current_pass}]",
                    )
                    if state.deferred_empty_cursor and state.deferred_empty_cursor not in state.recorded_blank_cursors:
                        if state.current_max_id == state.deferred_empty_cursor[0] and state.offset == state.deferred_empty_cursor[1]:
                            state.offset += 25
                            state.page_num += 1
                            state.deferred_empty_cursor = None

                    if state.page_num >= state.total_pages or (state.total_user_messages > 0 and state.user_messages >= state.total_user_messages):
                        break

                await self._execute_member_scan(
                    guild=guild,
                    state=state,
                    watched_ids=watched_ids,
                    ignored_ids=ignored_ids,
                    keyword_list=keyword_list,
                    thread_parent_map=thread_parent_map,
                    unknown_channel_ids=unknown_channel_ids,
                    on_progress=on_single_progress,
                )

                if state.user_messages == prev_messages and state.page_num == prev_pages:
                    consecutive_stalls += 1
                    if consecutive_stalls >= 3:
                        print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': No progress after 3 consecutive passes. Concluding scan with {state.user_messages:,} messages.")
                        break
                else:
                    consecutive_stalls = 0

            if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
                print(f"[ANALYSIS FINAL PASS] '{guild.name}' -> '{target.display_name}': Re-checking {len(state.blank_pages)} blank page(s) in 3 final verification passes...")
                await self._verify_blank_pages(
                    guild=guild,
                    state=state,
                    watched_ids=watched_ids,
                    ignored_ids=ignored_ids,
                    keyword_list=keyword_list,
                    thread_parent_map=thread_parent_map,
                    unknown_channel_ids=unknown_channel_ids,
                    on_progress=on_single_progress,
                    max_passes=3,
                    pass_name="Final Blank Verification",
                )

            await self.bot.db.save_retroactive_analysis(
                guild_id=guild.id,
                user_id=target.id,
                total_words=state.user_words,
                counted_messages=state.user_messages,
                total_attachments=state.user_attachments,
                keyword_counts=state.user_keywords,
                channel_words=state.channel_words,
                channel_messages=state.channel_messages,
                channel_attachments=state.channel_attachments,
                channel_keywords=state.channel_keywords,
                monthly_stats=state.monthly_stats,
                monthly_keywords=state.monthly_keywords,
                channel_emojis=state.channel_emojis,
                total_emojis=state.user_emojis,
            )

            total_duration = asyncio.get_event_loop().time() - start_time
            print(f"[ANALYSIS COMPLETED] ✅ Single-user analysis finished in '{guild.name}' for '{target.display_name}' in {format_duration(total_duration)} (Messages: {state.user_messages:,}, Words: {state.user_words:,}, Attachments: {state.user_attachments:,}, Emojis: {state.user_emojis:,})\n")
            fields = [
                ("💬 Old Messages Added", f"**{state.user_messages:,}** *(of {total_historical_messages:,} indexed)*"),
                ("📝 Words Found & Added", f"**{state.user_words:,}**"),
                ("📎 Attachments Found & Added", f"**{state.user_attachments:,}**"),
                ("😀 Emojis Found & Added", f"**{state.user_emojis:,}**"),
            ]
            if keyword_list:
                kw_lines = [f"• **{kw}**: `{cnt:,}`" for kw, cnt in state.user_keywords.items()]
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
            print(f"[ANALYSIS LOCK RELEASED] Analysis lock released for '{guild.name}' ({guild.id})")

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

            command_time = interaction.created_at if interaction.created_at else discord.utils.utcnow()
            max_id_snowflake = discord.utils.time_snowflake(command_time)

            watched_ids, ignored_ids = await self.bot.db.get_guild_tracking_config(guild.id)
            keyword_list: List[str] = await self.bot.db.get_keywords(guild.id)

            total_eligible = len(all_eligible_members)
            analyzed_count = 0
            skipped_no_messages_count = 0
            total_pages_scanned = 0
            grand_total_words = 0
            grand_total_messages = 0
            grand_total_attachments = 0
            grand_total_emojis = 0
            grand_keywords: Dict[str, int] = {k: 0 for k in keyword_list}

            start_time = asyncio.get_event_loop().time()
            thread_parent_map: Dict[int, int] = {}
            for th in guild.threads:
                thread_parent_map[th.id] = th.parent_id
            unknown_channel_ids: Set[int] = set()

            current_sleep = self.get_search_sleep_delay()
            active_servers = len(self.running_guilds)
            print(f"\n[ANALYSIS ACTIVE] ⏳ Whole-server analysis started in '{guild.name}' ({guild.id}) for {len(pending_members)} pending members ({total_eligible} total) (Pacing: {current_sleep:.1f}s, Active Servers: {active_servers})")

            deferred_members: List[MemberScanState] = []

            for idx, target in enumerate(pending_members, start=1):
                current_total_idx = skipped_already_count + idx
                total_skipped = skipped_already_count + skipped_no_messages_count
                elapsed = asyncio.get_event_loop().time() - start_time
                current_sleep = self.get_search_sleep_delay()
                active_servers = len(self.running_guilds)
                est_remaining = compute_server_remaining_time(
                    elapsed=elapsed,
                    idx=idx,
                    page_num=0,
                    total_pages=0,
                    total_pending=len(pending_members),
                    analyzed_count=analyzed_count,
                    total_pages_scanned=total_pages_scanned,
                    active_servers=active_servers,
                )

                print(f"[ANALYSIS ACTIVE] '{guild.name}' -> Processing member {current_total_idx}/{total_eligible}: '{target.display_name}' ({target.id}) (Elapsed: {format_duration(elapsed)}, Est. Remaining: {format_duration(est_remaining)})")

                status_view = create_v2_view(
                    title="⏳ Whole Server Retroactive Deep-Sweep (Takes a Long Time)",
                    description=(
                        f"**Current Member ({current_total_idx}/{total_eligible}):** {target.mention}\n"
                        f"**Members Analyzed:** `{analyzed_count}` | **Skipped:** `{total_skipped}`\n"
                        f"-# ⏩ Skipped breakdown: {skipped_no_messages_count} had no prior messages • {skipped_already_count} previously analyzed\n\n"
                        f"**Server Totals Added So Far:**\n"
                        f"• 💬 Messages: `{grand_total_messages:,}`\n"
                        f"• 📝 Words: `{grand_total_words:,}`\n"
                        f"• 📎 Attachments: `{grand_total_attachments:,}`\n"
                        f"• 😀 Emojis: `{grand_total_emojis:,}`\n\n"
                        f"**Elapsed Time:** `{format_duration(elapsed)}` • **Estimated Remaining:** `{format_duration(est_remaining)}`\n"
                        f"-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                    ),
                    thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                    footer=f"Member {current_total_idx}/{total_eligible} • Pacing {current_sleep:.1f}s per request ({active_servers} active server(s))",
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
                    total_skipped = skipped_already_count + skipped_no_messages_count
                    elapsed = asyncio.get_event_loop().time() - start_time
                    current_sleep = self.get_search_sleep_delay()
                    active_servers = len(self.running_guilds)
                    est_remaining = compute_server_remaining_time(
                        elapsed=elapsed,
                        idx=idx + 1,
                        page_num=0,
                        total_pages=0,
                        total_pending=len(pending_members),
                        analyzed_count=analyzed_count,
                        total_pages_scanned=total_pages_scanned,
                        active_servers=active_servers,
                    )
                    print(f"[ANALYSIS SKIPPED] '{guild.name}' -> Member {current_total_idx}/{total_eligible}: '{target.display_name}' has 0 historical messages")
                    skip_view = create_v2_view(
                        title="⏳ Whole Server Retroactive Deep-Sweep (Takes a Long Time)",
                        description=(
                            f"**Current Member ({current_total_idx}/{total_eligible}):** {target.mention} *(Skipped — 0 messages)*\n"
                            f"**Members Analyzed:** `{analyzed_count}` | **Skipped:** `{total_skipped}`\n"
                            f"-# ⏩ Skipped breakdown: {skipped_no_messages_count} had no prior messages • {skipped_already_count} previously analyzed\n\n"
                            f"**Server Totals Added So Far:**\n"
                            f"• 💬 Messages: `{grand_total_messages:,}`\n"
                            f"• 📝 Words: `{grand_total_words:,}`\n"
                            f"• 📎 Attachments: `{grand_total_attachments:,}`\n"
                            f"• 😀 Emojis: `{grand_total_emojis:,}`\n\n"
                            f"**Elapsed Time:** `{format_duration(elapsed)}`"
                            + (f" • **Estimated Remaining:** `{format_duration(est_remaining)}`" if idx < len(pending_members) else "")
                            + "\n-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                        ),
                        thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                        footer=f"Member {current_total_idx}/{total_eligible} • Skipped (no prior messages) • Pacing {current_sleep:.1f}s",
                        color=WARNING_COLOR,
                    )
                    await _safe_edit_message(status_msg, view=skip_view)
                    await asyncio.sleep(1.0)
                    continue

                state = MemberScanState(target, total_user_messages, max_id_snowflake)

                async def on_server_progress(s: MemberScanState) -> None:
                    el = asyncio.get_event_loop().time() - start_time
                    c_sleep = self.get_search_sleep_delay()
                    act_serv = len(self.running_guilds)
                    rem = compute_server_remaining_time(
                        elapsed=el,
                        idx=idx,
                        page_num=s.page_num,
                        total_pages=s.total_pages,
                        total_pending=len(pending_members),
                        analyzed_count=analyzed_count,
                        total_pages_scanned=total_pages_scanned + s.page_num,
                        active_servers=act_serv,
                    )
                    scanned = min(s.page_num * 25, s.total_user_messages)
                    print(f"[ANALYSIS IN PROGRESS] '{guild.name}' -> '{s.target.display_name}' ({current_total_idx}/{total_eligible}): page {s.page_num}/{s.total_pages} (scanned {scanned:,}/{s.total_user_messages:,}, elapsed: {format_duration(el)}, est. remaining: {format_duration(rem)})")
                    prog_v = create_v2_view(
                        title="⏳ Whole Server Retroactive Deep-Sweep (Takes a Long Time)",
                        description=(
                            f"**Current Member ({current_total_idx}/{total_eligible}):** {s.target.mention}\n"
                            f"**Scanning Member Messages:** `{scanned:,} / {s.total_user_messages:,}` (Page {s.page_num}/{s.total_pages})\n"
                            f"**Members Analyzed:** `{analyzed_count}` | **Skipped:** `{total_skipped}`\n"
                            f"-# ⏩ Skipped breakdown: {skipped_no_messages_count} had no prior messages • {skipped_already_count} previously analyzed\n\n"
                            f"**Server Totals Added So Far:**\n"
                            f"• 💬 Messages: `{grand_total_messages + s.user_messages:,}`\n"
                            f"• 📝 Words: `{grand_total_words + s.user_words:,}`\n"
                            f"• 📎 Attachments: `{grand_total_attachments + s.user_attachments:,}`\n"
                            f"• 😀 Emojis: `{grand_total_emojis + s.user_emojis:,}`\n\n"
                            f"**Elapsed Time:** `{format_duration(el)}` • **Estimated Remaining:** `{format_duration(rem)}`\n"
                            f"-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                        ),
                        thumbnail_url=s.target.display_avatar.url if s.target.display_avatar else None,
                        footer=f"Member {current_total_idx}/{total_eligible} • Page {s.page_num}/{s.total_pages} • Pacing {c_sleep:.1f}s per request ({act_serv} active server(s))",
                        color=WARNING_COLOR,
                    )
                    await _safe_edit_message(status_msg, view=prog_v)

                await self._execute_member_scan(
                    guild=guild,
                    state=state,
                    watched_ids=watched_ids,
                    ignored_ids=ignored_ids,
                    keyword_list=keyword_list,
                    thread_parent_map=thread_parent_map,
                    unknown_channel_ids=unknown_channel_ids,
                    on_progress=on_server_progress,
                )

                is_complete = (
                    state.page_num >= state.total_pages
                    or (state.total_user_messages > 0 and state.user_messages >= state.total_user_messages)
                    or state.total_user_messages == 0
                )

                if not is_complete:
                    deferred_members.append(state)
                    scanned_display = min(state.page_num * 25, state.total_user_messages)
                    print(f"[ANALYSIS DEFERRED] '{guild.name}' -> '{target.display_name}': Scanned {scanned_display:,}/{state.total_user_messages:,} messages ({state.user_messages:,} counted) before reaching blank index. Putting user aside to continue other members, will circle back for pass 2!")
                    def_view = create_v2_view(
                        title="⏳ Whole Server Retroactive Deep-Sweep (Takes a Long Time)",
                        description=(
                            f"**Current Member ({current_total_idx}/{total_eligible}):** {target.mention} *(Deferred for pass 2 — {max(0, state.total_user_messages - state.user_messages):,} messages remain)*\n"
                            f"**Members Analyzed:** `{analyzed_count}` | **Skipped:** `{total_skipped}` | **Deferred:** `{len(deferred_members)}`\n\n"
                            f"**Server Totals Added So Far:**\n"
                            f"• 💬 Messages: `{grand_total_messages:,}`\n"
                            f"• 📝 Words: `{grand_total_words:,}`\n\n"
                            f"-# ⏳ Pausing this member to process others, then circling back to sweep remaining messages!"
                        ),
                        thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                        footer=f"Member {current_total_idx}/{total_eligible} • Putting aside for pass 2",
                        color=WARNING_COLOR,
                    )
                    await _safe_edit_message(status_msg, view=def_view)
                    await asyncio.sleep(self.get_search_sleep_delay())
                    continue

                if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
                    print(f"[ANALYSIS FINAL PASS] '{guild.name}' -> '{target.display_name}': Re-checking {len(state.blank_pages)} blank page(s) in 3 final verification passes...")
                    await self._verify_blank_pages(
                        guild=guild,
                        state=state,
                        watched_ids=watched_ids,
                        ignored_ids=ignored_ids,
                        keyword_list=keyword_list,
                        thread_parent_map=thread_parent_map,
                        unknown_channel_ids=unknown_channel_ids,
                        on_progress=on_server_progress,
                        max_passes=3,
                        pass_name="Final Blank Verification",
                    )

                await self.bot.db.save_retroactive_analysis(
                    guild_id=guild.id,
                    user_id=target.id,
                    total_words=state.user_words,
                    counted_messages=state.user_messages,
                    total_attachments=state.user_attachments,
                    keyword_counts=state.user_keywords,
                    channel_words=state.channel_words,
                    channel_messages=state.channel_messages,
                    channel_attachments=state.channel_attachments,
                    channel_keywords=state.channel_keywords,
                    monthly_stats=state.monthly_stats,
                    monthly_keywords=state.monthly_keywords,
                    channel_emojis=state.channel_emojis,
                    total_emojis=state.user_emojis,
                )
                state.completed = True

                analyzed_count += 1
                total_pages_scanned += state.page_num
                grand_total_words += state.user_words
                grand_total_messages += state.user_messages
                grand_total_attachments += state.user_attachments
                grand_total_emojis += state.user_emojis
                for kw, cnt in state.user_keywords.items():
                    grand_keywords[kw] += cnt

                elapsed = asyncio.get_event_loop().time() - start_time
                current_sleep = self.get_search_sleep_delay()
                active_servers = len(self.running_guilds)
                est_remaining = compute_server_remaining_time(
                    elapsed=elapsed,
                    idx=idx + 1,
                    page_num=0,
                    total_pages=0,
                    total_pending=len(pending_members),
                    analyzed_count=analyzed_count,
                    total_pages_scanned=total_pages_scanned,
                    active_servers=active_servers,
                )
                print(f"[ANALYSIS MEMBER DONE] '{guild.name}' -> Finished '{target.display_name}' ({current_total_idx}/{total_eligible}) — Total so far: {grand_total_words:,} words, {grand_total_messages:,} msgs, {grand_total_attachments:,} atts, {grand_total_emojis:,} emojis")
                prog_view = create_v2_view(
                    title="⏳ Whole Server Retroactive Deep-Sweep (Takes a Long Time)",
                    description=(
                        f"**Completed Member ({current_total_idx}/{total_eligible}):** {target.mention}\n"
                        f"**Members Analyzed:** `{analyzed_count}` | **Skipped:** `{total_skipped}`" + (f" | **Deferred:** `{len(deferred_members)}`" if deferred_members else "") + "\n"
                        f"-# ⏩ Skipped breakdown: {skipped_no_messages_count} had no prior messages • {skipped_already_count} previously analyzed\n\n"
                        f"**Server Totals Added:**\n"
                        f"• 💬 Messages: `{grand_total_messages:,}`\n"
                        f"• 📝 Words: `{grand_total_words:,}`\n"
                        f"• 📎 Attachments: `{grand_total_attachments:,}`\n"
                        f"• 😀 Emojis: `{grand_total_emojis:,}`\n\n"
                        f"**Elapsed Time:** `{format_duration(elapsed)}`"
                        + (f" • **Estimated Remaining:** `{format_duration(est_remaining)}`" if (idx < len(pending_members) or deferred_members) else "")
                        + "\n-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                    ),
                    thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                    footer=f"Overall Progress: {current_total_idx}/{total_eligible} members ({int(current_total_idx / total_eligible * 100)}%)" + (f" • Pacing {current_sleep:.1f}s to next member ({active_servers} active server(s))" if idx < len(pending_members) else ""),
                    color=WARNING_COLOR if (idx < len(pending_members) or deferred_members) else SUCCESS_COLOR,
                )
                await _safe_edit_message(status_msg, view=prog_view)

                if idx < len(pending_members):
                    await asyncio.sleep(self.get_search_sleep_delay())

            pass_num = 1
            while deferred_members:
                pass_num += 1
                retry_queue = list(deferred_members)
                deferred_members.clear()
                print(f"\n[ANALYSIS PASS {pass_num}] '{guild.name}': Circling back to {len(retry_queue)} deferred member(s) with remaining messages...\n")

                if len(pending_members) <= 1:
                    print(f"[ANALYSIS NOTICE] Waiting 30s before circling back...")
                    await asyncio.sleep(30.0)

                for def_idx, state in enumerate(retry_queue, start=1):
                    state.passes_attempted += 1
                    target = state.target
                    prev_messages = state.user_messages
                    prev_pages = state.page_num
                    print(f"[ANALYSIS RETRY] '{guild.name}' -> Circling back to '{target.display_name}' (Pass {pass_num}, {def_idx}/{len(retry_queue)})...")

                    circle_view = create_v2_view(
                        title=f"⏳ Whole Server Deep-Sweep — Pass {pass_num} (Takes a Long Time)",
                        description=(
                            f"**Circling Back to Member ({def_idx}/{len(retry_queue)}):** {target.mention}\n"
                            f"**Messages Scanned So Far:** `{min(state.page_num * 25, state.total_user_messages):,} / {state.total_user_messages:,}`\n"
                            f"**Tallied So Far:** `{state.user_messages:,}` messages • `{state.user_words:,}` words\n\n"
                            f"-# 🔄 Resuming deep search from updated cursor/offset after cooldown..."
                        ),
                        thumbnail_url=target.display_avatar.url if target.display_avatar else None,
                        footer=f"Pass {pass_num} • Pacing {self.get_search_sleep_delay():.1f}s",
                        color=WARNING_COLOR,
                    )
                    await _safe_edit_message(status_msg, view=circle_view)

                    async def on_retry_progress(s: MemberScanState) -> None:
                        el = asyncio.get_event_loop().time() - start_time
                        c_sleep = self.get_search_sleep_delay()
                        scanned = min(s.page_num * 25, s.total_user_messages)
                        print(f"[ANALYSIS IN PROGRESS] '{guild.name}' -> '{s.target.display_name}' [Pass {pass_num}]: page {s.page_num}/{s.total_pages} (scanned {scanned:,}/{s.total_user_messages:,}, elapsed: {format_duration(el)})")
                        prog_v = create_v2_view(
                            title=f"⏳ Whole Server Deep-Sweep — Pass {pass_num}",
                            description=(
                                f"**Current Member ({def_idx}/{len(retry_queue)}):** {s.target.mention}\n"
                                f"**Scanning Member Messages:** `{scanned:,} / {s.total_user_messages:,}` (Page {s.page_num}/{s.total_pages})\n"
                                f"**Server Totals Added So Far:**\n"
                                f"• 💬 Messages: `{grand_total_messages + s.user_messages:,}`\n"
                                f"• 📝 Words: `{grand_total_words + s.user_words:,}`\n\n"
                                f"**Elapsed Time:** `{format_duration(el)}`\n"
                                f"-# ⏳ **Duration Notice:** Analysis runs with safety pacing to prevent Discord search rate limits."
                            ),
                            thumbnail_url=s.target.display_avatar.url if s.target.display_avatar else None,
                            footer=f"Pass {pass_num} • Member {def_idx}/{len(retry_queue)} • Page {s.page_num}/{s.total_pages} • Pacing {c_sleep:.1f}s",
                            color=WARNING_COLOR,
                        )
                        await _safe_edit_message(status_msg, view=prog_v)

                    if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
                        await self._verify_blank_pages(
                            guild=guild,
                            state=state,
                            watched_ids=watched_ids,
                            ignored_ids=ignored_ids,
                            keyword_list=keyword_list,
                            thread_parent_map=thread_parent_map,
                            unknown_channel_ids=unknown_channel_ids,
                            on_progress=on_retry_progress,
                            max_passes=1,
                            pass_name=f"Preemptive Blank Check [Pass {pass_num}]",
                        )
                        if state.deferred_empty_cursor and state.deferred_empty_cursor not in state.recorded_blank_cursors:
                            if state.current_max_id == state.deferred_empty_cursor[0] and state.offset == state.deferred_empty_cursor[1]:
                                state.offset += 25
                                state.page_num += 1
                                state.deferred_empty_cursor = None

                    is_complete = (
                        state.page_num >= state.total_pages
                        or (state.total_user_messages > 0 and state.user_messages >= state.total_user_messages)
                        or state.total_user_messages == 0
                    )

                    if not is_complete:
                        await self._execute_member_scan(
                            guild=guild,
                            state=state,
                            watched_ids=watched_ids,
                            ignored_ids=ignored_ids,
                            keyword_list=keyword_list,
                            thread_parent_map=thread_parent_map,
                            unknown_channel_ids=unknown_channel_ids,
                            on_progress=on_retry_progress,
                        )

                        is_complete = (
                            state.page_num >= state.total_pages
                            or (state.total_user_messages > 0 and state.user_messages >= state.total_user_messages)
                            or state.total_user_messages == 0
                        )

                    if not is_complete:
                        if state.user_messages == prev_messages and state.page_num == prev_pages:
                            state.consecutive_stalls += 1
                        else:
                            state.consecutive_stalls = 0

                        if state.consecutive_stalls < 3:
                            deferred_members.append(state)
                            continue
                        else:
                            print(f"[ANALYSIS NOTICE] '{guild.name}' -> '{target.display_name}': No progress after 3 consecutive passes. Concluding analysis for this member.")

                    if state.blank_pages and (state.total_user_messages == 0 or state.user_messages < state.total_user_messages):
                        print(f"[ANALYSIS FINAL PASS] '{guild.name}' -> '{target.display_name}': Re-checking {len(state.blank_pages)} blank page(s) in 3 final verification passes...")
                        await self._verify_blank_pages(
                            guild=guild,
                            state=state,
                            watched_ids=watched_ids,
                            ignored_ids=ignored_ids,
                            keyword_list=keyword_list,
                            thread_parent_map=thread_parent_map,
                            unknown_channel_ids=unknown_channel_ids,
                            on_progress=on_retry_progress,
                            max_passes=3,
                            pass_name="Final Blank Verification",
                        )

                    await self.bot.db.save_retroactive_analysis(
                        guild_id=guild.id,
                        user_id=target.id,
                        total_words=state.user_words,
                        counted_messages=state.user_messages,
                        total_attachments=state.user_attachments,
                        keyword_counts=state.user_keywords,
                        channel_words=state.channel_words,
                        channel_messages=state.channel_messages,
                        channel_attachments=state.channel_attachments,
                        channel_keywords=state.channel_keywords,
                        monthly_stats=state.monthly_stats,
                        monthly_keywords=state.monthly_keywords,
                        channel_emojis=state.channel_emojis,
                        total_emojis=state.user_emojis,
                    )
                    state.completed = True

                    analyzed_count += 1
                    total_pages_scanned += state.page_num
                    grand_total_words += state.user_words
                    grand_total_messages += state.user_messages
                    grand_total_attachments += state.user_attachments
                    grand_total_emojis += state.user_emojis
                    for kw, cnt in state.user_keywords.items():
                        grand_keywords[kw] += cnt

                    await asyncio.sleep(self.get_search_sleep_delay())

            for remaining_state in deferred_members:
                if not remaining_state.completed:
                    if remaining_state.blank_pages and (remaining_state.total_user_messages == 0 or remaining_state.user_messages < remaining_state.total_user_messages):
                        print(f"[ANALYSIS FINAL PASS] '{guild.name}' -> '{remaining_state.target.display_name}': Re-checking {len(remaining_state.blank_pages)} blank page(s) in 3 final verification passes...")
                        await self._verify_blank_pages(
                            guild=guild,
                            state=remaining_state,
                            watched_ids=watched_ids,
                            ignored_ids=ignored_ids,
                            keyword_list=keyword_list,
                            thread_parent_map=thread_parent_map,
                            unknown_channel_ids=unknown_channel_ids,
                            max_passes=3,
                            pass_name="Final Blank Verification",
                        )
                    await self.bot.db.save_retroactive_analysis(
                        guild_id=guild.id,
                        user_id=remaining_state.target.id,
                        total_words=remaining_state.user_words,
                        counted_messages=remaining_state.user_messages,
                        total_attachments=remaining_state.user_attachments,
                        keyword_counts=remaining_state.user_keywords,
                        channel_words=remaining_state.channel_words,
                        channel_messages=remaining_state.channel_messages,
                        channel_attachments=remaining_state.channel_attachments,
                        channel_keywords=remaining_state.channel_keywords,
                        monthly_stats=remaining_state.monthly_stats,
                        monthly_keywords=remaining_state.monthly_keywords,
                        channel_emojis=remaining_state.channel_emojis,
                        total_emojis=remaining_state.user_emojis,
                    )
                    remaining_state.completed = True
                    analyzed_count += 1
                    total_pages_scanned += remaining_state.page_num
                    grand_total_words += remaining_state.user_words
                    grand_total_messages += remaining_state.user_messages
                    grand_total_attachments += remaining_state.user_attachments
                    grand_total_emojis += remaining_state.user_emojis
                    for kw, cnt in remaining_state.user_keywords.items():
                        grand_keywords[kw] += cnt

            total_duration = asyncio.get_event_loop().time() - start_time
            print(f"\n[ANALYSIS COMPLETED] ✅ Whole-server analysis finished in '{guild.name}' in {format_duration(total_duration)}! Analyzed: {analyzed_count}, Skipped: {total_skipped}, Words: {grand_total_words:,}, Messages: {grand_total_messages:,}, Attachments: {grand_total_attachments:,}, Emojis: {grand_total_emojis:,}\n")
            total_skipped = skipped_already_count + skipped_no_messages_count
            fields = [
                (
                    "👥 Server Members Summary",
                    f"**{total_eligible:,}** total non-bot members\n"
                    f"• **{analyzed_count:,}** analyzed with messages\n"
                    f"• **{skipped_no_messages_count:,}** skipped (no prior messages)\n"
                    f"• **{skipped_already_count:,}** skipped (previously analyzed)\n"
                    f"• **Total Skipped:** `{total_skipped:,}`",
                ),
                ("💬 Historical Messages Added", f"**{grand_total_messages:,}**"),
                ("📝 Historical Words Added", f"**{grand_total_words:,}**"),
                ("📎 Historical Attachments Added", f"**{grand_total_attachments:,}**"),
                ("😀 Historical Emojis Added", f"**{grand_total_emojis:,}**"),
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
            print(f"[ANALYSIS LOCK RELEASED] Whole-server analysis lock released for '{guild.name}' ({guild.id})")

    analyze_chat = app_commands.Group(
        name="analyze_chat",
        description="Retroactively analyze messages up to command execution (Takes a long time; 10s+ safety pacing)",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    @analyze_chat.command(
        name="single_user",
        description="Analyze a member's chat history up to command execution (Takes a long time; 10s+ pacing)",
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
        description="Analyze all members' chat history up to command execution (Takes multiple hours; 10s+ pacing)",
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