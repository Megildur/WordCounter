from __future__ import annotations
import asyncio
import contextlib
import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Callable, Dict, Iterable, List, Optional, Set, Tuple, Union
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route
from cogs.utils.components import (
    NEUTRAL_COLOR,
    SUCCESS_COLOR,
    TRACKING_OFF_MESSAGE,
    WARNING_COLOR,
    AuthorOnlyView,
    activity_line,
    check_channel_with_config,
    create_v2_container,
    create_v2_view,
    error_view,
    keyword_summary,
    make_button,
    manager_check_error,
)
from cogs.utils.counting import MessageStats

log = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 25
SEARCH_INTERVAL = 2.0
SEARCH_MAX_FAILURES = 5
SEARCH_RECHECKS = 2
INDEX_WAIT_LIMIT = 120.0
CRAWL_CONCURRENCY = 4
CRAWL_ATTEMPTS = 3
CHECKPOINT_INTERVAL = 60.0
PROGRESS_INTERVAL = 8.0
LIST_LIMIT = 15

NETWORK_ERRORS = (discord.HTTPException, aiohttp.ClientError, OSError, asyncio.TimeoutError)
HISTORY_CHANNEL_TYPES = (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)
TRACKABLE_TYPES = (*HISTORY_CHANNEL_TYPES, discord.ForumChannel)
COUNTED_MESSAGE_TYPES = frozenset(
    kind.value
    for kind in (
        discord.MessageType.default,
        discord.MessageType.reply,
        discord.MessageType.chat_input_command,
        discord.MessageType.context_menu_command,
        discord.MessageType.thread_starter_message,
        discord.MessageType.poll_result,
    )
)

HistoryChannel = Union[discord.TextChannel, discord.VoiceChannel, discord.StageChannel, discord.Thread]


class SearchUnavailable(Exception):
    pass


class AnalysisCancelled(Exception):
    pass


def format_duration(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def mention_list(channel_ids: Iterable[int]) -> str:
    ids = list(dict.fromkeys(channel_ids))
    text = ", ".join(f"<#{cid}>" for cid in ids[:LIST_LIMIT])
    if len(ids) > LIST_LIMIT:
        text += f" and {len(ids) - LIST_LIMIT} more"
    return text


def can_read(channel: Union[discord.abc.GuildChannel, discord.Thread], me: discord.Member) -> bool:
    try:
        perms = channel.permissions_for(me)
    except discord.ClientException:
        return False
    if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) and not perms.connect:
        return False
    return perms.view_channel and perms.read_message_history


class Tally:
    __slots__ = ("words", "messages", "attachments", "emojis", "keywords", "monthly", "monthly_keywords")

    def __init__(self) -> None:
        self.words = self.messages = self.attachments = self.emojis = 0
        self.keywords: Dict[str, int] = defaultdict(int)
        self.monthly: Dict[Tuple[int, int, int], List[int]] = defaultdict(lambda: [0, 0, 0, 0])
        self.monthly_keywords: Dict[Tuple[int, str, int, int], int] = defaultdict(int)

    def add(self, channel_id: int, created_at: datetime, stats: MessageStats) -> None:
        self.messages += 1
        self.words += stats.words
        self.attachments += stats.attachments
        self.emojis += stats.emojis
        month = self.monthly[(channel_id, created_at.year, created_at.month)]
        month[0] += stats.words
        month[1] += 1
        month[2] += stats.attachments
        month[3] += stats.emojis
        for keyword, count in stats.keywords.items():
            self.keywords[keyword] += count
            self.monthly_keywords[(channel_id, keyword, created_at.year, created_at.month)] += count

    @property
    def activity(self) -> Tuple[Dict[Tuple[int, int, int], List[int]], Dict[Tuple[int, str, int, int], int]]:
        return self.monthly, self.monthly_keywords

    def totals(self) -> Dict[str, Any]:
        return {
            "messages": self.messages,
            "words": self.words,
            "attachments": self.attachments,
            "emojis": self.emojis,
            "keywords": dict(self.keywords),
        }

    @classmethod
    def from_totals(cls, data: Dict[str, Any]) -> Tally:
        tally = cls()
        for name in ("messages", "words", "attachments", "emojis"):
            setattr(tally, name, int(data.get(name, 0)))
        tally.keywords.update(data.get("keywords", {}))
        return tally


def combine(tallies: Iterable[Tally]) -> Tally:
    total = Tally()
    for tally in tallies:
        total.messages += tally.messages
        total.words += tally.words
        total.attachments += tally.attachments
        total.emojis += tally.emojis
        for keyword, count in tally.keywords.items():
            total.keywords[keyword] += count
    return total


class LiveCoverage:
    def __init__(
        self,
        periods: List[Tuple[int, Optional[int]]],
        resets: Dict[Tuple[int, int], int],
        command_cutoff: int,
    ) -> None:
        self.periods = periods
        self.resets = resets
        self._reset_before: Dict[Tuple[int, int], int] = {}
        open_starts = [start for start, end in periods if end is None]
        self.tracking_since = min(open_starts) if open_starts else None
        if self.tracking_since is None:
            self.scan_before = command_cutoff
        else:
            self.scan_before = min(command_cutoff, max([self.tracking_since, *resets.values()]))

    def counts(self, user_id: int, channel_id: int, message_id: int) -> bool:
        key = (user_id, channel_id)
        reset_before = self._reset_before.get(key)
        if reset_before is None:
            reset_before = self._reset_before[key] = max(
                self.resets.get((0, 0), 0),
                self.resets.get((user_id, 0), 0),
                self.resets.get((0, channel_id), 0),
                self.resets.get((user_id, channel_id), 0),
            )
        if message_id < reset_before:
            return True
        return not any(start <= message_id and (end is None or message_id < end) for start, end in self.periods)


@dataclass
class Progress:
    started: float = field(default_factory=time.monotonic)
    note: str = ""
    found: int = 0
    total: int = 0
    pages: int = 0
    channels_done: int = 0
    channels_total: int = 0
    read: int = 0
    active: Set[int] = field(default_factory=set)
    unreadable: List[int] = field(default_factory=list)
    failed: List[int] = field(default_factory=list)
    used_history: bool = False

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started


class ChannelScope:
    def __init__(self, guild: discord.Guild, watched: Set[int], ignored: Set[int]) -> None:
        self.guild = guild
        self.watched = watched
        self.ignored = ignored
        self.thread_parents: Dict[int, int] = {thread.id: thread.parent_id for thread in guild.threads}
        self._resolved: Dict[int, Optional[int]] = {}

    def check(self, channel: Union[discord.abc.GuildChannel, discord.Thread]) -> Tuple[bool, int]:
        return check_channel_with_config(self.guild, channel, self.watched, self.ignored)

    def learn_threads(self, threads: Iterable[Dict[str, Any]]) -> None:
        for thread in threads:
            if thread.get("id") and thread.get("parent_id"):
                self.thread_parents[int(thread["id"])] = int(thread["parent_id"])

    async def resolve(self, channel_id: int) -> Optional[int]:
        if channel_id in self._resolved:
            return self._resolved[channel_id]
        if channel_id not in self.thread_parents and self.guild.get_channel_or_thread(channel_id) is None:
            try:
                channel = await self.guild.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
            if isinstance(channel, discord.Thread):
                self.thread_parents[channel_id] = channel.parent_id
        tracked, effective_id = check_channel_with_config(
            self.guild, channel_id, self.watched, self.ignored, self.thread_parents
        )
        self._resolved[channel_id] = effective_id if tracked else None
        return self._resolved[channel_id]


class StatusMessage:
    def __init__(
        self,
        message: Optional[Union[discord.Message, discord.PartialMessage]],
        interaction: Optional[discord.Interaction] = None,
    ) -> None:
        self.message = message
        self.interaction = interaction

    @property
    def ids(self) -> Tuple[Optional[int], Optional[int]]:
        if self.message is None:
            return None, None
        return self.message.channel.id, self.message.id

    async def show(self, view: discord.ui.LayoutView) -> None:
        error: Optional[Exception] = None
        if self.message is not None:
            try:
                await self.message.edit(view=view)
                return
            except discord.HTTPException as exc:
                error = exc
        if self.interaction is not None:
            try:
                await self.interaction.edit_original_response(view=view)
                return
            except discord.HTTPException as exc:
                error = exc
        if error is not None:
            log.warning("Could not update analysis status message: %s", error)


class CrawlRun:
    def __init__(self, guild_id: int, record: Dict[str, Any], coverage: LiveCoverage) -> None:
        state = json.loads(record["totals"] or "{}")
        self.guild_id = guild_id
        self.kind: str = record["kind"]
        self.authors: Set[int] = record["members"]
        self.scan_before: int = record["cutoff"]
        self.coverage = coverage
        self.tallies: Dict[int, Tally] = {}
        self.totals = Tally.from_totals(state)
        self.credited: Set[int] = set(state.get("credited", []))
        self.cursors: Dict[int, int] = {
            cid: before for cid, (before, status) in record["channels"].items() if status == "reading" and before
        }
        self.finished: Dict[int, str] = {
            cid: status for cid, (_, status) in record["channels"].items() if status != "reading"
        }

    def tally(self, user_id: int) -> Tally:
        tally = self.tallies.get(user_id)
        if tally is None:
            tally = self.tallies[user_id] = Tally()
        return tally

    def flush(self) -> Dict[int, Any]:
        flushed, self.tallies = self.tallies, {}
        self.totals = combine([self.totals, *flushed.values()])
        self.credited.update(user_id for user_id, tally in flushed.items() if tally.messages)
        return {user_id: tally.activity for user_id, tally in flushed.items() if tally.messages}

    def live_totals(self) -> Tally:
        return combine([self.totals, *self.tallies.values()])

    def credited_count(self) -> int:
        return len(self.credited | {user_id for user_id, tally in self.tallies.items() if tally.messages})

    def state(self) -> str:
        return json.dumps({**self.totals.totals(), "credited": sorted(self.credited)})


class AnalyzeConfirmView(AuthorOnlyView):
    def __init__(
        self,
        cog: AnalyzeChat,
        author_id: int,
        keywords: List[str],
        coverage: LiveCoverage,
        *,
        target: Optional[discord.Member] = None,
        pending: int = 0,
        already_done: int = 0,
    ) -> None:
        super().__init__(
            author_id=author_id,
            denied_message="Only the person who ran this command can use these buttons.",
            timeout=120.0,
        )
        self.cog = cog
        self.target = target
        self.message: Optional[discord.Message] = None

        if keywords:
            keyword_text = ", ".join(f"`{k}`" for k in keywords[:LIST_LIMIT])
            if len(keywords) > LIST_LIMIT:
                keyword_text += f" and {len(keywords) - LIST_LIMIT} more"
        else:
            keyword_text = (
                "None set. Keywords only count in old messages if they exist before you start, "
                "so add them in `/settings` first if you want them."
            )

        if target is not None:
            title = "Count past messages"
            description = (
                f"Reads {target.mention}'s older messages and adds them to their stats. "
                "Each member can only be analyzed once."
            )
            fields = [("Keywords", keyword_text)]
            footer = "Usually takes a few minutes."
        else:
            title = "Count past messages for the whole server"
            description = "Reads every tracked channel and thread and adds older messages to each member's stats."
            members = f"{pending:,} to analyze"
            if already_done:
                members += f", {already_done:,} already done and skipped"
            fields = [("Members", members), ("Keywords", keyword_text)]
            footer = "Big servers can take a while. Progress is saved, so a restart picks up where it left off."

        if coverage.tracking_since is not None:
            since = int(discord.utils.snowflake_time(coverage.tracking_since).timestamp())
            description += f"\n-# Tracking started <t:{since}:D>. Messages counted live since then are skipped."

        self.add_item(
            create_v2_container(
                title,
                description,
                fields=fields,
                footer=footer,
                color=WARNING_COLOR,
                action_rows=[
                    discord.ui.ActionRow(
                        make_button("Start", discord.ButtonStyle.success, self._on_start),
                        make_button("Cancel", discord.ButtonStyle.secondary, self._on_cancel),
                    )
                ],
            )
        )

    async def _on_start(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=create_v2_view("Starting", "Getting the channel list ready.", color=WARNING_COLOR)
        )
        if self.target is not None:
            await self.cog.run_member(interaction, self.target)
        else:
            await self.cog.run_server(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            view=create_v2_view("Analysis cancelled", "Nothing was changed.", color=NEUTRAL_COLOR)
        )

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        with contextlib.suppress(discord.HTTPException):
            await self.message.edit(
                view=create_v2_view(
                    "Confirmation expired",
                    "Nothing was changed. Run the command again to start.",
                    color=NEUTRAL_COLOR,
                )
            )


class AnalyzeChat(commands.Cog):
    analyze_chat = app_commands.Group(
        name="analyze_chat",
        description="Count messages sent before tracking started",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.running_guilds: Set[int] = set()
        self.search_lock = asyncio.Lock()
        self.last_search = 0.0
        self.crawl_slots = asyncio.Semaphore(CRAWL_CONCURRENCY)
        self._started_up = False

    async def cog_load(self) -> None:
        if self.bot.is_ready():
            asyncio.create_task(self._startup())

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._startup()

    async def _startup(self) -> None:
        if self._started_up:
            return
        self._started_up = True
        for guild in self.bot.guilds:
            if guild.me is not None and guild.me.joined_at is not None:
                await self.bot.db.ensure_tracking_period(guild.id, discord.utils.time_snowflake(guild.me.joined_at))
        for guild_id in await self.bot.db.list_analysis_runs():
            guild = self.bot.get_guild(guild_id)
            if guild is not None and guild_id not in self.running_guilds:
                log.info("Resuming interrupted analysis in guild %s", guild_id)
                asyncio.create_task(self._resume(guild))

    async def is_busy(self, guild_id: int) -> bool:
        return guild_id in self.running_guilds or await self.bot.db.has_analysis_run(guild_id)

    async def _resume(self, guild: discord.Guild) -> None:
        record = await self.bot.db.get_analysis_run(guild.id)
        if record is None:
            return
        channel = guild.get_channel_or_thread(record["status_channel_id"] or 0)
        message = None
        if channel is not None and record["status_message_id"] and hasattr(channel, "get_partial_message"):
            message = channel.get_partial_message(record["status_message_id"])
        await self._run_crawl(guild, StatusMessage(message))

    async def _blocked_reason(self, interaction: discord.Interaction) -> Optional[str]:
        reason = manager_check_error(interaction)
        if reason:
            return reason
        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            return TRACKING_OFF_MESSAGE
        if interaction.guild.id in self.running_guilds:
            return "An analysis is already running in this server. Wait for it to finish."
        return None

    async def _send(self, interaction: discord.Interaction, view: discord.ui.LayoutView) -> discord.Message:
        if interaction.response.is_done():
            return await interaction.followup.send(view=view, wait=True)
        await interaction.response.send_message(view=view)
        return await interaction.original_response()

    async def _resume_interrupted(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.db.has_analysis_run(interaction.guild.id):
            return False
        sent = await self._send(
            interaction,
            create_v2_view(
                "Resuming",
                "An earlier analysis in this server was interrupted. Picking it up where it stopped.",
                color=WARNING_COLOR,
            ),
        )
        message = interaction.channel.get_partial_message(sent.id)
        await self.bot.db.set_analysis_run_message(interaction.guild.id, message.channel.id, message.id)
        await self._run_crawl(interaction.guild, StatusMessage(message, interaction))
        return True

    async def _pending_members(self, guild: discord.Guild) -> Tuple[List[discord.Member], int]:
        if not guild.chunked:
            try:
                await guild.chunk()
            except (discord.ClientException, asyncio.TimeoutError) as error:
                log.warning("Could not load the member list for guild %s: %s", guild.id, error)
        analyzed = await self.bot.db.get_analyzed_users(guild.id)
        humans = [member for member in guild.members if not member.bot]
        pending = [member for member in humans if member.id not in analyzed]
        return pending, len(humans) - len(pending)

    async def _load_scope(self, guild: discord.Guild) -> Tuple[ChannelScope, List[str]]:
        watched, ignored = await self.bot.db.get_guild_tracking_config(guild.id)
        keywords = await self.bot.db.get_keywords(guild.id)
        return ChannelScope(guild, watched, ignored), keywords

    async def _coverage(self, guild_id: int, command_cutoff: int) -> LiveCoverage:
        periods, resets = await self.bot.db.get_count_history(guild_id)
        return LiveCoverage(periods, resets, command_cutoff)

    @contextlib.asynccontextmanager
    async def _live_status(
        self, status: StatusMessage, render: Callable[[], discord.ui.LayoutView]
    ) -> AsyncGenerator[None, None]:
        async def refresh() -> None:
            while True:
                await status.show(render())
                await asyncio.sleep(PROGRESS_INTERVAL)

        task = asyncio.create_task(refresh())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _search(self, guild_id: int, author_id: int, before: int, progress: Progress) -> Dict[str, Any]:
        route = Route("GET", "/guilds/{guild_id}/messages/search", guild_id=guild_id)
        params = {
            "author_id": author_id,
            "max_id": before,
            "sort_by": "timestamp",
            "sort_order": "desc",
            "limit": SEARCH_PAGE_SIZE,
            "include_nsfw": "true",
        }
        waited = 0.0
        failures = 0
        while True:
            try:
                async with self.search_lock:
                    delay = self.last_search + SEARCH_INTERVAL - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    try:
                        data = await self.bot.http.request(route, params=params)
                    finally:
                        self.last_search = time.monotonic()
            except discord.HTTPException as error:
                if 400 <= error.status < 500 and error.status != 429:
                    raise SearchUnavailable(f"search was rejected ({error.status}): {error.text}") from error
                failures += 1
                if failures >= SEARCH_MAX_FAILURES:
                    raise SearchUnavailable(f"search kept failing: {error}") from error
                await asyncio.sleep(min(60.0, 5.0 * 2**failures))
                continue
            except NETWORK_ERRORS as error:
                failures += 1
                if failures >= SEARCH_MAX_FAILURES:
                    raise SearchUnavailable(f"search kept failing: {error}") from error
                await asyncio.sleep(min(60.0, 5.0 * 2**failures))
                continue

            if isinstance(data, dict) and "messages" in data and not data.get("doing_deep_historical_index"):
                progress.note = ""
                return data

            delay = max(5.0, float(data.get("retry_after") or 0)) if isinstance(data, dict) else 5.0
            waited += delay
            if waited > INDEX_WAIT_LIMIT:
                raise SearchUnavailable("the search index is not ready")
            progress.note = "Discord is still indexing this server's messages. Waiting before trying again."
            await asyncio.sleep(delay)

    @staticmethod
    def _author_hits(data: Dict[str, Any], author_id: int, before: int) -> Dict[int, Dict[str, Any]]:
        author = str(author_id)
        hits: Dict[int, Dict[str, Any]] = {}
        for group in data.get("messages") or []:
            for payload in group:
                if payload.get("hit") is False or payload.get("author", {}).get("id") != author:
                    continue
                message_id = int(payload["id"])
                if message_id < before:
                    hits[message_id] = payload
        return hits

    async def _search_window(
        self,
        guild: discord.Guild,
        author_id: int,
        before: int,
        seen: Set[int],
        tally: Tally,
        coverage: LiveCoverage,
        scope: ChannelScope,
        keywords: List[str],
        progress: Progress,
    ) -> Tuple[Dict[int, Dict[str, Any]], int]:
        data = await self._search(guild.id, author_id, before, progress)
        progress.pages += 1
        if not progress.total:
            progress.total = int(data.get("total_results") or 0)
        scope.learn_threads(data.get("threads") or [])
        hits = self._author_hits(data, author_id, before)
        new = 0
        for message_id, payload in hits.items():
            if message_id in seen:
                continue
            seen.add(message_id)
            new += 1
            if payload.get("type", 0) not in COUNTED_MESSAGE_TYPES:
                continue
            channel_id = await scope.resolve(int(payload["channel_id"]))
            if channel_id is None or not coverage.counts(author_id, channel_id, message_id):
                continue
            files = len(payload.get("attachments") or []) + len(payload.get("sticker_items") or payload.get("stickers") or [])
            stats = MessageStats.measure(payload.get("content") or "", files, keywords)
            tally.add(channel_id, discord.utils.snowflake_time(message_id), stats)
        progress.found = len(seen)
        return hits, new

    async def _search_member(
        self,
        guild: discord.Guild,
        author_id: int,
        coverage: LiveCoverage,
        tally: Tally,
        scope: ChannelScope,
        keywords: List[str],
        progress: Progress,
    ) -> None:
        seen: Set[int] = set()
        starts = [coverage.scan_before]
        for sweep in range(1 + SEARCH_RECHECKS):
            short_windows: List[int] = []
            for cursor in starts:
                while True:
                    hits, new = await self._search_window(
                        guild, author_id, cursor, seen, tally, coverage, scope, keywords, progress
                    )
                    if len(hits) < SEARCH_PAGE_SIZE and len(seen) < progress.total:
                        short_windows.append(cursor)
                    if not hits or (sweep and not new):
                        break
                    cursor = min(hits)
            if not short_windows or len(seen) >= progress.total:
                return
            starts = short_windows

    async def _crawl_targets(self, guild: discord.Guild, scope: ChannelScope, progress: Progress) -> List[HistoryChannel]:
        me = guild.me
        targets: List[HistoryChannel] = []
        parents: List[Union[discord.TextChannel, discord.ForumChannel]] = []
        for channel in guild.channels:
            if not isinstance(channel, TRACKABLE_TYPES) or not scope.check(channel)[0]:
                continue
            if not can_read(channel, me):
                progress.unreadable.append(channel.id)
                continue
            if isinstance(channel, HISTORY_CHANNEL_TYPES):
                targets.append(channel)
            if isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
                parents.append(channel)

        threads: Dict[int, discord.Thread] = {thread.id: thread for thread in guild.threads}
        try:
            threads.update((thread.id, thread) for thread in await guild.active_threads())
        except discord.HTTPException as error:
            log.warning("Could not list active threads in guild %s: %s", guild.id, error)

        for parent in parents:
            listings: List[Dict[str, bool]] = [{}]
            if isinstance(parent, discord.TextChannel):
                listings.append({"private": True, "joined": not parent.permissions_for(me).manage_threads})
            for options in listings:
                try:
                    async for thread in parent.archived_threads(limit=None, **options):
                        threads[thread.id] = thread
                except discord.HTTPException as error:
                    log.warning("Could not list archived threads in channel %s: %s", parent.id, error)

        targets.extend(thread for thread in threads.values() if scope.check(thread)[0] and can_read(thread, me))
        return targets

    async def _crawl_channel(
        self,
        channel: HistoryChannel,
        run: CrawlRun,
        keywords: List[str],
        scope: ChannelScope,
        progress: Progress,
    ) -> None:
        effective_id = scope.check(channel)[1]
        before: discord.abc.Snowflake = discord.Object(id=run.cursors.get(channel.id, run.scan_before))
        outcome: Optional[str] = None
        async with self.crawl_slots:
            progress.active.add(channel.id)
            try:
                for attempt in range(1, CRAWL_ATTEMPTS + 1):
                    try:
                        async for message in channel.history(limit=None, before=before):
                            before = discord.Object(id=message.id)
                            run.cursors[channel.id] = message.id
                            progress.read += 1
                            author_id = message.author.id
                            if (
                                author_id not in run.authors
                                or message.is_system()
                                or not run.coverage.counts(author_id, effective_id, message.id)
                            ):
                                continue
                            run.tally(author_id).add(effective_id, message.created_at, MessageStats.of(message, keywords))
                        outcome = "done"
                        return
                    except (discord.Forbidden, discord.NotFound):
                        outcome = "unreadable"
                        progress.unreadable.append(channel.id)
                        return
                    except NETWORK_ERRORS as error:
                        log.warning("Reading channel %s failed (attempt %s/%s): %s", channel.id, attempt, CRAWL_ATTEMPTS, error)
                        if attempt == CRAWL_ATTEMPTS:
                            outcome = "failed"
                            progress.failed.append(channel.id)
                            return
                        await asyncio.sleep(5.0 * attempt)
            finally:
                progress.active.discard(channel.id)
                if outcome is not None:
                    run.cursors.pop(channel.id, None)
                    run.finished[channel.id] = outcome
                    progress.channels_done += 1

    async def _checkpoint(self, run: CrawlRun, progress: Progress) -> None:
        results = run.flush()
        saved = await self.bot.db.checkpoint_analysis_run(
            run.guild_id, results, dict(run.cursors), dict(run.finished), progress.read, run.state()
        )
        if not saved:
            raise AnalysisCancelled

    async def _crawl(
        self,
        guild: discord.Guild,
        run: CrawlRun,
        scope: ChannelScope,
        keywords: List[str],
        progress: Progress,
    ) -> None:
        progress.used_history = True
        progress.note = "Listing channels and threads."
        targets = await self._crawl_targets(guild, scope, progress)
        progress.note = ""
        for channel_id in progress.unreadable:
            run.finished.setdefault(channel_id, "unreadable")
        remaining = [channel for channel in targets if channel.id not in run.finished]
        progress.channels_total = len(targets)
        progress.channels_done = len(targets) - len(remaining)

        tasks = [
            asyncio.create_task(self._crawl_channel(channel, run, keywords, scope, progress))
            for channel in remaining
        ]
        crawl = asyncio.gather(*tasks)
        try:
            while not crawl.done():
                await asyncio.wait({crawl}, timeout=CHECKPOINT_INTERVAL)
                if not crawl.done():
                    await self._checkpoint(run, progress)
            crawl.result()
        finally:
            for task in tasks:
                task.cancel()
            if not crawl.done():
                crawl.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await crawl

    def _progress_view(
        self,
        guild: discord.Guild,
        kind: str,
        authors: Set[int],
        totals: Tally,
        credited: int,
        progress: Progress,
    ) -> discord.ui.LayoutView:
        lines: List[str] = []
        thumbnail = None
        if kind == "member":
            member_id = next(iter(authors))
            member = guild.get_member(member_id)
            thumbnail = member.display_avatar.url if member else None
            lines.append(f"<@{member_id}>")

        footer = f"Running for {format_duration(progress.elapsed)}"
        if progress.used_history:
            lines.append(f"Channels and threads: **{progress.channels_done:,}** of **{progress.channels_total:,}**")
            lines.append(f"Messages read: **{progress.read:,}**")
            if progress.active:
                lines.append(f"Reading now: {mention_list(list(progress.active)[:3])}")
        else:
            percent = f" ({progress.found * 100 // progress.total}%)" if progress.total else ""
            lines.append(f"Found **{progress.found:,}** of **{progress.total:,}** messages{percent}")
            if progress.pages >= 2 and progress.total > progress.found:
                pages_left = math.ceil((progress.total - progress.found) / SEARCH_PAGE_SIZE)
                footer += f" · about {format_duration(pages_left * progress.elapsed / progress.pages)} left"

        counted = activity_line(totals.words, totals.messages, totals.attachments, totals.emojis)
        lines.append(f"Counted for {credited:,} member(s): {counted}" if kind == "server" else counted)
        if progress.note:
            lines.append(f"-# {progress.note}")
        return create_v2_view(
            "Counting past messages",
            "\n".join(lines),
            thumbnail_url=thumbnail,
            footer=footer,
            color=WARNING_COLOR,
        )

    def _summary_view(
        self,
        guild: discord.Guild,
        kind: str,
        authors: Set[int],
        totals: Tally,
        credited: int,
        keywords: List[str],
        progress: Progress,
    ) -> discord.ui.LayoutView:
        thumbnail = None
        if kind == "member":
            member_id = next(iter(authors))
            member = guild.get_member(member_id)
            thumbnail = member.display_avatar.url if member else None
            description = f"<@{member_id}> is now marked as analyzed."
        else:
            description = f"{len(authors):,} members are now marked as analyzed. {credited:,} of them had past messages."

        fields = [("Added", activity_line(totals.words, totals.messages, totals.attachments, totals.emojis))]
        if keywords:
            fields.append(("Keywords", keyword_summary((k, totals.keywords.get(k, 0)) for k in keywords)))
        if progress.used_history:
            fields.append(("Read", f"{progress.read:,} messages across {progress.channels_total:,} channels and threads"))
        if progress.unreadable:
            fields.append((
                "Couldn't read",
                f"{mention_list(progress.unreadable)}\n-# The bot needs View Channel and Read Message History there, plus Connect for voice channels.",
            ))
        if progress.failed:
            fields.append((
                "Stopped early",
                f"{mention_list(progress.failed)}\n-# Discord kept returning errors for these, so some of their messages may be missing.",
            ))
        if not progress.used_history and progress.found < progress.total:
            fields.append((
                "Not returned by Discord",
                f"Search listed {progress.total:,} messages but returned {progress.found:,}. "
                "The gap is usually messages deleted during the run or in channels the bot can't see.",
            ))
        return create_v2_view(
            "Past messages added",
            description,
            fields=fields,
            thumbnail_url=thumbnail,
            footer=f"Took {format_duration(progress.elapsed)}",
            color=SUCCESS_COLOR,
        )

    async def _run_crawl(self, guild: discord.Guild, status: StatusMessage) -> None:
        self.running_guilds.add(guild.id)
        try:
            record = await self.bot.db.get_analysis_run(guild.id)
            if record is None:
                return
            scope, keywords = await self._load_scope(guild)
            run = CrawlRun(guild.id, record, await self._coverage(guild.id, record["cutoff"]))
            progress = Progress(
                started=time.monotonic() - max(0.0, time.time() - record["started_at"]),
                read=record["messages_read"],
                used_history=True,
            )
            progress.unreadable = [cid for cid, outcome in run.finished.items() if outcome == "unreadable"]
            progress.failed = [cid for cid, outcome in run.finished.items() if outcome == "failed"]
            log.info("Reading channel history for %s member(s) in guild %s", len(run.authors), guild.id)

            def render() -> discord.ui.LayoutView:
                return self._progress_view(guild, run.kind, run.authors, run.live_totals(), run.credited_count(), progress)

            async with self._live_status(status, render):
                await self._crawl(guild, run, scope, keywords, progress)
                results = run.flush()
                if not await self.bot.db.finish_analysis_run(guild.id, results, run.authors):
                    raise AnalysisCancelled

            log.info(
                "Finished guild %s: read %s messages in %s channels in %s",
                guild.id, progress.read, progress.channels_total, format_duration(progress.elapsed),
            )
            await status.show(
                self._summary_view(guild, run.kind, run.authors, run.totals, len(run.credited), keywords, progress)
            )
        except AnalysisCancelled:
            await status.show(
                create_v2_view(
                    "Analysis stopped",
                    "This server's data was reset while the analysis was running, so it was cancelled.",
                    color=NEUTRAL_COLOR,
                )
            )
        except Exception:
            log.exception("Analysis in guild %s failed", guild.id)
            await status.show(
                error_view(
                    "The analysis hit an unexpected error. Everything up to the last checkpoint is saved, and it "
                    "picks up from there when the bot restarts or you run the command again."
                )
            )
        finally:
            self.running_guilds.discard(guild.id)

    async def run_member(self, interaction: discord.Interaction, target: discord.Member) -> None:
        guild = interaction.guild
        status = StatusMessage(interaction.message, interaction)
        members = {target.id}
        saved = False
        self.running_guilds.add(guild.id)
        try:
            if await self.bot.db.is_user_analyzed(guild.id, target.id):
                await status.show(error_view(f"{target.mention} has already been analyzed in this server."))
                return

            scope, keywords = await self._load_scope(guild)
            coverage = await self._coverage(guild.id, discord.utils.time_snowflake(interaction.created_at))
            tally, progress = Tally(), Progress()
            log.info("Searching past messages of %s in guild %s", target.id, guild.id)

            def render() -> discord.ui.LayoutView:
                return self._progress_view(guild, "member", members, tally, int(bool(tally.messages)), progress)

            try:
                async with self._live_status(status, render):
                    await self._search_member(guild, target.id, coverage, tally, scope, keywords, progress)
            except SearchUnavailable as reason:
                log.info("Search unavailable in guild %s (%s); reading channel history instead", guild.id, reason)
                await self.bot.db.start_analysis_run(guild.id, "member", coverage.scan_before, *status.ids, members)
                await self._run_crawl(guild, status)
                return

            await self.bot.db.save_retroactive_results(guild.id, {target.id: tally.activity} if tally.messages else {}, members)
            saved = True
            await status.show(
                self._summary_view(guild, "member", members, tally, int(bool(tally.messages)), keywords, progress)
            )
        except Exception:
            log.exception("Analysis of %s in guild %s failed", target.id, guild.id)
            if saved:
                message = f"{target.mention}'s past messages were saved, but the summary couldn't be shown. Check `/stats user`."
            else:
                message = "The analysis stopped because of an unexpected error, so nothing was saved. You can run it again."
            await status.show(error_view(message))
        finally:
            self.running_guilds.discard(guild.id)

    async def run_server(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        status = StatusMessage(interaction.message, interaction)
        self.running_guilds.add(guild.id)
        try:
            pending, _ = await self._pending_members(guild)
            if not pending:
                await status.show(
                    create_v2_view("Nothing to do", "Every member here has already been analyzed.", color=NEUTRAL_COLOR)
                )
                return
            coverage = await self._coverage(guild.id, discord.utils.time_snowflake(interaction.created_at))
            await self.bot.db.start_analysis_run(
                guild.id, "server", coverage.scan_before, *status.ids, [member.id for member in pending]
            )
            await self._run_crawl(guild, status)
        finally:
            self.running_guilds.discard(guild.id)

    @analyze_chat.command(name="single_user", description="Count one member's past messages")
    @app_commands.describe(target="The member whose past messages to count")
    async def single_user(self, interaction: discord.Interaction, target: discord.Member) -> None:
        reason = await self._blocked_reason(interaction)
        if reason is None and target.bot:
            reason = "Bots aren't counted."
        if reason is None and await self.bot.db.is_user_analyzed(interaction.guild.id, target.id):
            reason = (
                f"{target.mention} has already been analyzed in this server.\n"
                "-# To run it again, unlock them in `/settings` → Data & Reset Tools."
            )
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return
        if await self._resume_interrupted(interaction):
            return

        keywords = await self.bot.db.get_keywords(interaction.guild.id)
        coverage = await self._coverage(interaction.guild.id, discord.utils.time_snowflake(interaction.created_at))
        view = AnalyzeConfirmView(self, interaction.user.id, keywords, coverage, target=target)
        view.message = await self._send(interaction, view)

    @analyze_chat.command(name="whole_server", description="Count every member's past messages")
    async def whole_server(self, interaction: discord.Interaction) -> None:
        reason = await self._blocked_reason(interaction)
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return
        if await self._resume_interrupted(interaction):
            return

        if not interaction.guild.chunked:
            await interaction.response.defer()
        pending, already_done = await self._pending_members(interaction.guild)
        if not pending:
            await self._send(
                interaction,
                create_v2_view("Nothing to do", "Every member here has already been analyzed.", color=NEUTRAL_COLOR),
            )
            return

        keywords = await self.bot.db.get_keywords(interaction.guild.id)
        coverage = await self._coverage(interaction.guild.id, discord.utils.time_snowflake(interaction.created_at))
        view = AnalyzeConfirmView(
            self, interaction.user.id, keywords, coverage, pending=len(pending), already_done=already_done
        )
        view.message = await self._send(interaction, view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnalyzeChat(bot))
