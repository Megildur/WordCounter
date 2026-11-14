"""The /analyze_chat commands: count messages that were sent before tracking started or while it was off, and past swears for the swear jar.

One member can be counted with Discord's message search. A whole-server run, a swear run, or a member whose search isn't available reads every channel's history instead. That crawl runs in the background, saves a checkpoint to MySQL every CHECKPOINT_INTERVAL seconds (the analysis_runs tables in database.py) and resumes after a restart.

Messages the live counters already counted are skipped through `LiveCoverage`, so nothing is counted twice.
"""

from __future__ import annotations
import asyncio
import contextlib
import functools
import json
import logging
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, AsyncGenerator, Callable, Coroutine, Dict, Iterable, List, Optional, Set, Tuple, Union
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
    private_followup,
)
from cogs.utils.counting import MessageStats
from cogs.utils.jar import JAR_OFF_MESSAGE, jar_checks
from cogs.utils.swear_filter import MAX_FINED_PER_MESSAGE, SwearMatcher, languages_for, matcher_for

log = logging.getLogger(__name__)

# Results asked for per search request. A page with fewer hits than this may have been cut short.
SEARCH_PAGE_SIZE = 25
# The least time between two search requests, shared by every server through the cog's search_lock.
SEARCH_INTERVAL = 6.0
SEARCH_MAX_FAILURES = 5
# Extra passes over the windows that came back short while the search's own total says more messages exist.
SEARCH_RECHECKS = 2
# The longest wait, in seconds and per search request, for Discord to finish indexing a server before search is given up.
INDEX_WAIT_LIMIT = 120.0
# Channels read at the same time, across all servers.
CRAWL_CONCURRENCY = 4
CRAWL_ATTEMPTS = 3
CHECKPOINT_INTERVAL = 60.0
UNLOAD_WAIT = 10.0
# Seconds between edits of the progress message.
PROGRESS_INTERVAL = 8.0
LIST_LIMIT = 15

# Errors treated as temporary. Forbidden and NotFound are HTTPException subclasses too, so code that handles both catches those first.
NETWORK_ERRORS = (discord.HTTPException, aiohttp.ClientError, OSError, asyncio.TimeoutError)
HISTORY_CHANNEL_TYPES = (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)
# Forum channels have no messages of their own, only posts (threads), so they are searched for threads and never read directly.
TRACKABLE_TYPES = (*HISTORY_CHANNEL_TYPES, discord.ForumChannel)
# Message types that count, as the raw numbers a search result carries. Joins, pins, boosts and other system messages don't. The history crawl gets the same effect from `message.is_system()`.
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
    """Message search can't be used for this server: it was rejected, kept failing, or its index isn't ready. `run_member` then reads channel history instead."""
    pass


class AnalysisCancelled(Exception):
    """The run's rows are gone from the database, because the server's data was reset while it ran. A checkpoint or the final write raises it to stop the crawl."""
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
    """Returns whether the bot can read the channel's history: View Channel and Read Message History, plus Connect for voice and stage channels."""
    try:
        perms = channel.permissions_for(me)
    except discord.ClientException:
        # A thread whose parent channel isn't cached can't be checked.
        return False
    if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) and not perms.connect:
        return False
    return perms.view_channel and perms.read_message_history


class Tally:
    """Running counts for one member's messages. `monthly` is keyed by (channel_id, year, month) and holds [words, messages, attachments, emojis], and `monthly_keywords` by (channel_id, keyword, year, month). `activity` is the pair the database saves."""
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
    """Adds up the totals of several tallies. The monthly breakdown isn't carried over, because it is written to the database at each checkpoint instead of being kept."""
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
    """Decides which old messages still need counting, so nothing the live counters already counted is counted again.

    `periods` are the (start, end) snowflake spans in which messages were counted live, with end None while one is still open. `resets` maps (user_id, channel_id) to the snowflake of a reset, with 0 meaning all users or all channels. `command_cutoff` is the snowflake of the moment the command ran, or the run's saved cutoff when a saved run is rebuilt.

    `scan_before` is where a crawl starts reading, working back in time. It is the command's moment, or an earlier one when live counting has been on since then, because newer messages were counted live.
    """
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
            # Everything after the later of the tracking start and the newest reset was counted live, so only older messages need reading.
            self.scan_before = min(command_cutoff, max([self.tracking_since, *resets.values()]))

    def counts(self, user_id: int, channel_id: int, message_id: int) -> bool:
        """Returns whether the analysis should count this message, which is when it is older than a reset or falls outside every tracking period. Swear runs pass user_id 0 and have no reset markers."""
        key = (user_id, channel_id)
        reset_before = self._reset_before.get(key)
        if reset_before is None:
            # A reset can cover everyone, one member, one channel, or one member in one channel, so take the newest one that applies. The answer is cached per key because this runs for every message read.
            reset_before = self._reset_before[key] = max(
                self.resets.get((0, 0), 0),
                self.resets.get((user_id, 0), 0),
                self.resets.get((0, channel_id), 0),
                self.resets.get((user_id, channel_id), 0),
            )
        # A reset may have wiped the count of any message older than it, so those are counted again even inside a tracking period.
        if message_id < reset_before:
            return True
        return not any(start <= message_id and (end is None or message_id < end) for start, end in self.periods)


@dataclass
class Progress:
    """The numbers the progress card shows. `found` and `total` are for the search path. `read` and the channel counts are for the history crawl. `unreadable` and `failed` hold channel IDs listed in the summary."""
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
    """Decides which channels count and which channel a message is credited to, where a thread counts toward its parent. With `jar_rules` it adds the swear jar's channel rules on top of the tracking settings."""
    def __init__(
        self,
        guild: discord.Guild,
        watched: Set[int],
        ignored: Set[int],
        jar_rules: Optional[Dict[int, str]] = None,
        skip_age_restricted: bool = False,
    ) -> None:
        self.guild = guild
        self.watched = watched
        self.ignored = ignored
        self.jar_rules = jar_rules
        self.skip_age_restricted = skip_age_restricted
        self.thread_parents: Dict[int, int] = {thread.id: thread.parent_id for thread in guild.threads}
        self._resolved: Dict[int, Optional[int]] = {}

    def check(self, channel: Union[discord.abc.GuildChannel, discord.Thread]) -> Tuple[bool, int]:
        if self.jar_rules is not None:
            return self.check_jar(channel)
        return check_channel_with_config(self.guild, channel, self.watched, self.ignored)

    def learn_threads(self, threads: Iterable[Dict[str, Any]]) -> None:
        for thread in threads:
            if thread.get("id") and thread.get("parent_id"):
                self.thread_parents[int(thread["id"])] = int(thread["parent_id"])

    def check_jar(self, channel: Union[discord.abc.GuildChannel, discord.Thread]) -> Tuple[bool, int]:
        return jar_checks(self.guild, channel, self.watched, self.ignored, self.jar_rules, self.skip_age_restricted, self.thread_parents)

    async def resolve(self, channel_id: int) -> Optional[int]:
        """Returns the channel a search hit is credited to, or None when it isn't tracked. Answers are cached, since a search returns many hits from the same channel."""
        if channel_id in self._resolved:
            return self._resolved[channel_id]
        # A hit can come from a thread the bot hasn't cached, such as an archived one, so look it up once to learn its parent.
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
    """The message that shows progress, saved with the run so a restart can edit the same one. `show` edits the message and falls back to the interaction's original response. After a restart only the message is known, so `interaction` is None."""
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
        """Edits the status message. An HTTP error from the edit is logged, not raised, so a deleted message can't stop an analysis."""
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
    """The in-memory state of one crawl, rebuilt from the saved run record when it resumes.

    `authors` are the member IDs being counted. `tallies` hold counts not saved yet: `flush` hands them over and folds them into `totals`. `cursors` maps channel_id to the oldest message read so far in channels still being read, and `finished` maps channel_id to its final status (done, unreadable or failed).
    """
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
        self.matcher: Optional[SwearMatcher] = None
        self.swears: Dict[int, Dict[int, int]] = defaultdict(lambda: defaultdict(int))
        self.swear_total = int(state.get("swears", 0))
        self.swear_members: Set[int] = set(state.get("swear_members", []))

    @property
    def counts_swears(self) -> bool:
        return self.kind == "swears"

    def add_swears(self, user_id: int, channel_id: int, content: str) -> None:
        found = self.matcher.find(content) if self.matcher else []
        if found:
            # A message counts at most MAX_FINED_PER_MESSAGE swears, the same cap the live swear jar uses.
            self.swears[user_id][channel_id] += min(len(found), MAX_FINED_PER_MESSAGE)

    def flush_swears(self) -> Dict[int, Dict[int, int]]:
        flushed, self.swears = self.swears, defaultdict(lambda: defaultdict(int))
        for user_id, channels in flushed.items():
            self.swear_total += sum(channels.values())
            self.swear_members.add(user_id)
        return {user_id: dict(channels) for user_id, channels in flushed.items()}

    def live_swears(self) -> Tuple[int, int]:
        pending = {user_id: sum(channels.values()) for user_id, channels in self.swears.items()}
        return self.swear_total + sum(pending.values()), len(self.swear_members | set(pending))

    def tally(self, user_id: int) -> Tally:
        tally = self.tallies.get(user_id)
        if tally is None:
            tally = self.tallies[user_id] = Tally()
        return tally

    def flush(self) -> Dict[int, Any]:
        """Hands over the counts not saved yet as {user_id: activity} and folds them into `totals`, so `state()` taken afterwards includes them. Members with no counted message are left out."""
        flushed, self.tallies = self.tallies, {}
        self.totals = combine([self.totals, *flushed.values()])
        self.credited.update(user_id for user_id, tally in flushed.items() if tally.messages)
        return {user_id: tally.activity for user_id, tally in flushed.items() if tally.messages}

    def live_totals(self) -> Tally:
        return combine([self.totals, *self.tallies.values()])

    def credited_count(self) -> int:
        return len(self.credited | {user_id for user_id, tally in self.tallies.items() if tally.messages})

    def state(self) -> str:
        """The running totals as the JSON saved in analysis_runs.totals, which `__init__` reads back on a resume."""
        return json.dumps({
            **self.totals.totals(),
            "credited": sorted(self.credited),
            "swears": self.swear_total,
            "swear_members": sorted(self.swear_members),
        })


class AnalyzeConfirmView(AuthorOnlyView):
    """The Start and Cancel card shown before an analysis. Start hands over to the cog's `run_swears`, `run_member` or `run_server`, and that card is then edited into the progress card."""
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
        swears: bool = False,
    ) -> None:
        super().__init__(
            author_id=author_id,
            denied_message="Only the person who ran this command can use these buttons.",
            timeout=120.0,
        )
        self.cog = cog
        self.target = target
        self.swears = swears
        self.message: Optional[discord.Message] = None

        if keywords:
            keyword_text = ", ".join(f"`{k}`" for k in keywords[:LIST_LIMIT])
            if len(keywords) > LIST_LIMIT:
                keyword_text += f" and {len(keywords) - LIST_LIMIT} more"
        else:
            keyword_text = "None set. Keywords only count in old messages if they exist before you start, so add them in `/settings` first if you want them."

        if swears:
            title = "Count past swears"
            description = "Reads every channel the swear jar listens in and adds past swears to the Swears leaderboard. Past swears aren't fined, and swears the jar already counted are skipped."
            fields = [("Words", "The swear jar's word list for this server, including your changes.")]
            footer = "Big servers can take a while. Progress is saved, so a restart picks up where it left off."
        elif target is not None:
            title = "Count past messages"
            description = f"Reads {target.mention}'s older messages and adds them to their stats. Each member can only be analyzed once."
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
            started = "The swear jar started" if swears else "Tracking started"
            description += f"\n-# {started} <t:{since}:D>. Messages counted live since then are skipped."

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
        # Stopping the view keeps on_timeout from firing later and overwriting the progress card.
        self.stop()
        # The button press must be answered within 3 seconds, and the run takes far longer, so edit the card first and start the run after.
        await interaction.response.edit_message(
            view=create_v2_view("Starting", "Getting the channel list ready.", color=WARNING_COLOR)
        )
        if self.swears:
            await self.cog.run_swears(interaction)
        elif self.target is not None:
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


def tracked(method):
    """Registers the task that runs an analysis in `self._tasks` for the whole call, so `cog_unload` can cancel it even while it is still loading members or saving the run. A call made after the cog was unloaded is refused by cancelling it."""
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        if self._closed:
            raise asyncio.CancelledError
        task = asyncio.current_task()
        added = task is not None and task not in self._tasks
        if added:
            self._tasks.add(task)
        try:
            return await method(self, *args, **kwargs)
        finally:
            if added:
                self._tasks.discard(task)
    return wrapper


class AnalyzeChat(commands.Cog):
    # Discord's default permission only, which server admins can change, so every command also checks Manage Server itself.
    analyze_chat = app_commands.Group(
        name="analyze_chat",
        description="Count messages sent before tracking started",
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Servers with an analysis running in this process, whether it is searching or reading history. Other cogs read it through server_settings.analysis_state.
        self.running_guilds: Set[int] = set()
        # Search requests from every server go through one lock and one clock, so they stay SEARCH_INTERVAL apart.
        self.search_lock = asyncio.Lock()
        self.last_search = 0.0
        self.crawl_slots = asyncio.Semaphore(CRAWL_CONCURRENCY)
        # on_ready can fire again after a reconnect, and start-up must only run once.
        self._started_up = False
        # References to background tasks, because asyncio only keeps weak ones and could drop a task mid-run.
        self._tasks: Set[asyncio.Task] = set()
        self._closed = False

    async def cog_load(self) -> None:
        # When the cog is loaded after the bot is already ready, on_ready won't fire for it, so start up now.
        if self.bot.is_ready():
            self._spawn(self._startup())

    async def cog_unload(self) -> None:
        # Cancels every analysis, whether this cog spawned it or a button press is running it, even one still loading members, and refuses new ones. Then it waits for them to stop. What a crawl counted since its last checkpoint is read again when the run resumes. Without the wait, a reloaded cog could resume the run while the old crawl is still saving, and count a message twice.
        self._closed = True
        pending = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.wait(pending, timeout=UNLOAD_WAIT)

    def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self._startup()

    async def _startup(self) -> None:
        if self._started_up:
            return
        self._started_up = True
        # A server with tracking on needs an open tracking period, because the crawl skips messages inside one as already counted live. One is opened here, starting when the bot joined, if it has none.
        for guild in self.bot.guilds:
            if guild.me is not None and guild.me.joined_at is not None:
                await self.bot.db.ensure_tracking_period(guild.id, discord.utils.time_snowflake(guild.me.joined_at))
        for guild_id in await self.bot.db.list_analysis_runs():
            guild = self.bot.get_guild(guild_id)
            if guild is not None and guild_id not in self.running_guilds:
                log.info("Resuming interrupted analysis in guild %s", guild_id)
                # Spawned instead of awaited: a crawl can run for hours, and each server's run must go on alongside the others.
                self._spawn(self._resume(guild))

    async def is_busy(self, guild_id: int) -> bool:
        return guild_id in self.running_guilds or await self.bot.db.has_analysis_run(guild_id)

    async def _resume(self, guild: discord.Guild) -> None:
        """Continues an interrupted run after a restart. Only the saved message IDs survive a restart, so progress is shown by editing that message."""
        record = await self.bot.db.get_analysis_run(guild.id)
        if record is None:
            return
        channel = guild.get_channel_or_thread(record["status_channel_id"] or 0)
        message = None
        if channel is not None and record["status_message_id"] and hasattr(channel, "get_partial_message"):
            message = channel.get_partial_message(record["status_message_id"])
        await self._run_crawl(guild, StatusMessage(message))

    async def _blocked_reason(self, interaction: discord.Interaction) -> Optional[str]:
        """Returns why the command can't run (not a manager, tracking is off, or an analysis is already running), or None when it can."""
        reason = manager_check_error(interaction)
        if reason:
            return reason
        if not await self.bot.db.has_tracking_enabled(interaction.guild.id):
            return TRACKING_OFF_MESSAGE
        if interaction.guild.id in self.running_guilds:
            return "An analysis is already running in this server. Wait for it to finish."
        return None

    async def _begin(self, interaction: discord.Interaction) -> bool:
        """The start of every analysis command. Someone who isn't a manager is turned away at once, with no database read. Everyone else's interaction is deferred, because the checks that follow read the database and can wait behind a running analysis' saves, which would push the first answer past Discord's 3 seconds. Returns False after refusing."""
        reason = manager_check_error(interaction)
        if reason:
            await interaction.response.send_message(view=error_view(reason), ephemeral=True)
            return False
        await interaction.response.defer()
        return True

    async def _send(self, interaction: discord.Interaction, view: discord.ui.LayoutView) -> discord.Message:
        """Replies to the command and returns the sent message. An interaction that was already deferred can only be answered with a follow-up."""
        if interaction.response.is_done():
            return await interaction.followup.send(view=view, wait=True)
        await interaction.response.send_message(view=view)
        return await interaction.original_response()

    @tracked
    async def _resume_interrupted(self, interaction: discord.Interaction) -> bool:
        """Every command calls this after its checks and before starting a run. When a run was interrupted in this server it is continued here, with a new progress message, and True is returned so the command doesn't start another."""
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
        # Saved before the run starts, so a restart edits this message and not the old one.
        await self.bot.db.set_analysis_run_message(interaction.guild.id, message.channel.id, message.id)
        await self._run_crawl(interaction.guild, StatusMessage(message, interaction))
        return True

    async def _pending_members(self, guild: discord.Guild) -> Tuple[List[discord.Member], int]:
        """Returns the members still to analyze and how many humans were skipped as already analyzed. Bots and banned members are never counted."""
        # Every member has to be cached to know who is pending. Loading a big server's list can take longer than 3 seconds, which is why `whole_server` defers first.
        if not guild.chunked:
            try:
                await guild.chunk()
            except (discord.ClientException, asyncio.TimeoutError) as error:
                log.warning("Could not load the member list for guild %s: %s", guild.id, error)
        analyzed = await self.bot.db.get_analyzed_users(guild.id)
        humans = [member for member in guild.members if not member.bot and member.id not in self.bot.blacklisted]
        pending = [member for member in humans if member.id not in analyzed]
        return pending, len(humans) - len(pending)

    async def _load_scope(self, guild: discord.Guild) -> Tuple[ChannelScope, List[str]]:
        watched, ignored = await self.bot.db.get_guild_tracking_config(guild.id)
        keywords = await self.bot.db.get_keywords(guild.id)
        return ChannelScope(guild, watched, ignored), keywords

    async def _load_jar_scope(self, guild: discord.Guild) -> ChannelScope:
        watched, ignored = await self.bot.db.get_guild_tracking_config(guild.id)
        settings = await self.bot.db.jar_settings(guild.id)
        rules = await self.bot.db.jar_channel_rules(guild.id)
        return ChannelScope(guild, watched, ignored, rules, bool(settings["skip_age_restricted"]))

    async def _coverage(self, guild_id: int, command_cutoff: int) -> LiveCoverage:
        periods, resets = await self.bot.db.get_count_history(guild_id)
        return LiveCoverage(periods, resets, command_cutoff)

    @contextlib.asynccontextmanager
    async def _live_status(
        self, status: StatusMessage, render: Callable[[], discord.ui.LayoutView]
    ) -> AsyncGenerator[None, None]:
        """Edits the progress message every PROGRESS_INTERVAL seconds while the block runs. `render` builds a fresh view each time. The refresh task is stopped however the block ends."""
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
        """Asks Discord's message search for one page of a member's messages older than the snowflake `before`, newest first, and returns the response. It raises SearchUnavailable when the request is rejected, keeps failing, or the search index isn't ready."""
        # discord.py has no method for searching a server's messages, so the request is made by hand through its HTTP client.
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
                        # Set even when the request fails, so failed attempts are spaced out too.
                        self.last_search = time.monotonic()
            except discord.HTTPException as error:
                # A client error other than 429 won't fix itself, so give up on search. Rate limits and server errors are retried with a growing delay.
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

            # A response without a `messages` field, or with doing_deep_historical_index set, means Discord is still building the index. Wait and ask again.
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
        """Returns {message_id: payload} for the real hits in a search response that were written by `author_id` and are older than `before`."""
        author = str(author_id)
        hits: Dict[int, Dict[str, Any]] = {}
        # Each group can hold context messages around a hit, and those are marked hit False.
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
        """Fetches one search page and tallies the hits not seen before. Returns the page's hits and how many were new. `seen` holds the message IDs already handled, so windows that overlap don't count a message twice."""
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
        """Counts one member's past messages with search, paging back from `coverage.scan_before`. A page that comes back short while the search's total says more exist is searched again, up to SEARCH_RECHECKS more passes."""
        seen: Set[int] = set()
        starts = [coverage.scan_before]
        for sweep in range(1 + SEARCH_RECHECKS):
            short_windows: List[int] = []
            for cursor in starts:
                while True:
                    hits, new = await self._search_window(
                        guild, author_id, cursor, seen, tally, coverage, scope, keywords, progress
                    )
                    # Remember where this window started, so a later pass can search it again.
                    if len(hits) < SEARCH_PAGE_SIZE and len(seen) < progress.total:
                        short_windows.append(cursor)
                    # In a recheck pass a window stops as soon as a page brings nothing new.
                    if not hits or (sweep and not new):
                        break
                    cursor = min(hits)
            if not short_windows or len(seen) >= progress.total:
                return
            starts = short_windows

    async def _crawl_targets(self, guild: discord.Guild, scope: ChannelScope, progress: Progress) -> List[HistoryChannel]:
        """Lists every channel and thread to read: the tracked channels the bot can read and their active and archived threads. Tracked channels it can't read are added to `progress.unreadable`."""
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

        # The cache only has threads the bot has seen, so ask Discord for the active ones too. Archived threads are listed per channel below.
        threads: Dict[int, discord.Thread] = {thread.id: thread for thread in guild.threads}
        try:
            threads.update((thread.id, thread) for thread in await guild.active_threads())
        except discord.HTTPException as error:
            log.warning("Could not list active threads in guild %s: %s", guild.id, error)

        for parent in parents:
            listings: List[Dict[str, bool]] = [{}]
            if isinstance(parent, discord.TextChannel):
                # Private archived threads are only all listed with Manage Threads. Without it Discord lists the ones the bot has joined.
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
        """Reads one channel's history from its saved cursor, or from `run.scan_before` when it has none, back to its first message, and tallies into `run`. The outcome is done, unreadable or failed. A cancel leaves it unset, so the channel isn't marked finished and resumes from its last checkpointed cursor."""
        # Threads are credited to their parent channel.
        effective_id = scope.check(channel)[1]
        before: discord.abc.Snowflake = discord.Object(id=run.cursors.get(channel.id, run.scan_before))
        outcome: Optional[str] = None
        async with self.crawl_slots:
            progress.active.add(channel.id)
            try:
                for attempt in range(1, CRAWL_ATTEMPTS + 1):
                    try:
                        async for message in channel.history(limit=None, before=before):
                            # The cursor moves and the message is tallied with no await in between, so a checkpoint never sees one without the other. A retry also continues from here.
                            before = discord.Object(id=message.id)
                            run.cursors[channel.id] = message.id
                            progress.read += 1
                            author_id = message.author.id
                            if author_id in self.bot.blacklisted:
                                continue
                            if run.counts_swears:
                                if not message.author.bot and not message.is_system() and run.coverage.counts(0, effective_id, message.id):
                                    run.add_swears(author_id, effective_id, message.content)
                                continue
                            # Only the members this run is analyzing are counted. Anyone analyzed earlier isn't in `authors`, so they aren't counted twice.
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
                        # Access was lost or the channel was deleted during the run. It isn't retried.
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
                # A cancelled task leaves outcome as None, so the channel isn't marked finished and resumes from its last checkpointed cursor.
                if outcome is not None:
                    run.cursors.pop(channel.id, None)
                    run.finished[channel.id] = outcome
                    progress.channels_done += 1

    async def _checkpoint(self, run: CrawlRun, progress: Progress) -> None:
        """Saves the crawl's progress. Raises AnalysisCancelled when the run was deleted, which stops the crawl."""
        # flush() folds the unsaved counts into the totals, so it has to come before state() is read. The cursors and finished channels are copied in the same call, with no await in between, so they match the counts saved with them.
        results = run.flush()
        swears = run.flush_swears()
        saved = await self.bot.db.checkpoint_analysis_run(
            run.guild_id, results, dict(run.cursors), dict(run.finished), progress.read, run.state(), swears
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
        """Reads every target channel and thread in parallel and saves a checkpoint every CHECKPOINT_INTERVAL seconds until they are all done. The caller makes the final write."""
        progress.used_history = True
        progress.note = "Listing channels and threads."
        targets = await self._crawl_targets(guild, scope, progress)
        progress.note = ""
        # Unreadable channels are recorded as finished, so a resumed run skips them.
        for channel_id in progress.unreadable:
            run.finished.setdefault(channel_id, "unreadable")
        # Channels finished before a restart are skipped. The ones still being read continue from their saved cursor.
        remaining = [channel for channel in targets if channel.id not in run.finished]
        progress.channels_total = len(targets)
        progress.channels_done = len(targets) - len(remaining)

        tasks = [
            asyncio.create_task(self._crawl_channel(channel, run, keywords, scope, progress))
            for channel in remaining
        ]
        crawl = asyncio.gather(*tasks)
        try:
            # Wake every CHECKPOINT_INTERVAL seconds to save. Unlike wait_for, asyncio.wait doesn't cancel the crawl when its timeout passes.
            while not crawl.done():
                await asyncio.wait({crawl}, timeout=CHECKPOINT_INTERVAL)
                if not crawl.done():
                    await self._checkpoint(run, progress)
            crawl.result()
        finally:
            # When a checkpoint raises, such as AnalysisCancelled, or the run itself is cancelled, the channel tasks still reading are stopped here.
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
        swears: Optional[Tuple[int, int]] = None,
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

        if swears is not None:
            lines.append(f"Past swears found: **{swears[0]:,}** from {swears[1]:,} member(s)")
        else:
            counted = activity_line(totals.words, totals.messages, totals.attachments, totals.emojis)
            lines.append(f"Counted for {credited:,} member(s): {counted}" if kind == "server" else counted)
        if progress.note:
            lines.append(f"-# {progress.note}")
        return create_v2_view(
            "Counting past swears" if swears is not None else "Counting past messages",
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
        swears: Optional[Tuple[int, int]] = None,
    ) -> discord.ui.LayoutView:
        thumbnail = None
        if swears is not None:
            description = f"Added **{swears[0]:,}** past swears from {swears[1]:,} member(s) to the Swears leaderboard. Nobody was fined."
            fields = []
        elif kind == "member":
            member_id = next(iter(authors))
            member = guild.get_member(member_id)
            thumbnail = member.display_avatar.url if member else None
            description = f"<@{member_id}> is now marked as analyzed."
        else:
            description = f"{len(authors):,} members are now marked as analyzed. {credited:,} of them had past messages."

        if swears is None:
            fields = [("Added", activity_line(totals.words, totals.messages, totals.attachments, totals.emojis))]
        if keywords and swears is None:
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
                f"Search listed {progress.total:,} messages but returned {progress.found:,}. The gap is usually messages deleted during the run or in channels the bot can't see.",
            ))
        return create_v2_view(
            "Past swears added" if swears is not None else "Past messages added",
            description,
            fields=fields,
            thumbnail_url=thumbnail,
            footer=f"Took {format_duration(progress.elapsed)}",
            color=SUCCESS_COLOR,
        )

    @tracked
    async def _run_crawl(self, guild: discord.Guild, status: StatusMessage) -> None:
        """Runs the saved analysis for a server to the end and shows the result. Everything is read from the saved run record, so a new run and a resume after a restart take the same path."""
        self.running_guilds.add(guild.id)
        try:
            record = await self.bot.db.get_analysis_run(guild.id)
            if record is None:
                return
            if record["kind"] == "swears":
                # A swear run skips spans whose swears are already counted, live or by an earlier run (jar_coverage), and has no reset markers.
                scope, keywords = await self._load_jar_scope(guild), []
                coverage = LiveCoverage(await self.bot.db.jar_coverage(guild.id), {}, record["cutoff"])
            else:
                scope, keywords = await self._load_scope(guild)
                coverage = await self._coverage(guild.id, record["cutoff"])
            run = CrawlRun(guild.id, record, coverage)
            if run.counts_swears:
                overrides = tuple(await self.bot.db.jar_word_overrides(guild.id))
                languages = languages_for(await self.bot.db.jar_settings(guild.id))
                # Built in a thread so it can't hold up the event loop.
                run.matcher = await asyncio.to_thread(matcher_for, overrides, languages)
            # The clock starts where the run first began, so a resumed run shows its full running time.
            progress = Progress(
                started=time.monotonic() - max(0.0, time.time() - record["started_at"]),
                read=record["messages_read"],
                used_history=True,
            )
            progress.unreadable = [cid for cid, outcome in run.finished.items() if outcome == "unreadable"]
            progress.failed = [cid for cid, outcome in run.finished.items() if outcome == "failed"]
            if run.counts_swears:
                log.info("Reading channel history for past swears in guild %s", guild.id)
            else:
                log.info("Reading channel history for %s member(s) in guild %s", len(run.authors), guild.id)

            def render() -> discord.ui.LayoutView:
                swears = run.live_swears() if run.counts_swears else None
                return self._progress_view(guild, run.kind, run.authors, run.live_totals(), run.credited_count(), progress, swears)

            async with self._live_status(status, render):
                await self._crawl(guild, run, scope, keywords, progress)
                # Whatever is still unsaved goes in with the final write, which also marks the members analyzed. A swear run records the span it read as covered too, so later runs skip it.
                results = run.flush()
                swear_counts = run.flush_swears()
                finished = await self.bot.db.finish_analysis_run(
                    guild.id,
                    results,
                    run.authors,
                    swear_counts,
                    run.scan_before if run.counts_swears else None,
                )
                if not finished:
                    raise AnalysisCancelled

            log.info(
                "Finished guild %s: read %s messages in %s channels in %s",
                guild.id, progress.read, progress.channels_total, format_duration(progress.elapsed),
            )
            swears = (run.swear_total, len(run.swear_members)) if run.counts_swears else None
            await status.show(
                self._summary_view(guild, run.kind, run.authors, run.totals, len(run.credited), keywords, progress, swears)
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
            # The run stays saved, so it resumes from its last checkpoint.
            log.exception("Analysis in guild %s failed", guild.id)
            await status.show(
                error_view("The analysis hit an unexpected error. Everything up to the last checkpoint is saved, and it picks up from there when the bot restarts or you run the command again.")
            )
        finally:
            self.running_guilds.discard(guild.id)

    @tracked
    async def run_member(self, interaction: discord.Interaction, target: discord.Member) -> None:
        """Counts one member, trying message search first. When search isn't available it saves a run and hands over to the history crawl. With search nothing is written until it finishes."""
        guild = interaction.guild
        status = StatusMessage(interaction.message, interaction)
        members = {target.id}
        # Whether the results were already written, which decides what the error message says.
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
                # Saving a run first lets the history crawl checkpoint and resume like a server run.
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

    @tracked
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
            # The members to count are saved with the run, so a resume after a restart covers the same people.
            await self.bot.db.start_analysis_run(
                guild.id, "server", coverage.scan_before, *status.ids, [member.id for member in pending]
            )
            await self._run_crawl(guild, status)
        finally:
            self.running_guilds.discard(guild.id)

    @tracked
    async def run_swears(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        status = StatusMessage(interaction.message, interaction)
        self.running_guilds.add(guild.id)
        try:
            coverage = LiveCoverage(
                await self.bot.db.jar_coverage(guild.id), {}, discord.utils.time_snowflake(interaction.created_at)
            )
            await self.bot.db.start_analysis_run(guild.id, "swears", coverage.scan_before, *status.ids, [])
            await self._run_crawl(guild, status)
        finally:
            self.running_guilds.discard(guild.id)

    @analyze_chat.command(
        name="swears",
        description="Add past swears to the Swears leaderboard (nobody is fined)",
        extras={
            "details": "Reads past messages for swears and adds them to the Swears leaderboard. Nobody is fined, and wallets, debts and streaks don't change.",
            "user_perms": (
                "Manage Server"
            ),
            "bot_perms": (
                "Read Message History in the channels it counts"
            ),
        },
    )
    async def swears(self, interaction: discord.Interaction) -> None:
        if not await self._begin(interaction):
            return
        reason = None
        if not (await self.bot.db.jar_settings(interaction.guild.id))["enabled"]:
            reason = JAR_OFF_MESSAGE
        elif interaction.guild.id in self.running_guilds:
            reason = "An analysis is already running in this server. Wait for it to finish."
        if reason:
            await private_followup(interaction, error_view(reason))
            return
        if await self._resume_interrupted(interaction):
            return
        coverage = LiveCoverage(
            await self.bot.db.jar_coverage(interaction.guild.id), {}, discord.utils.time_snowflake(interaction.created_at)
        )
        view = AnalyzeConfirmView(self, interaction.user.id, [], coverage, swears=True)
        view.message = await self._send(interaction, view)

    @analyze_chat.command(
        name="single_user",
        description="Count one member's past messages",
        extras={
            "details": "Counts one member's past messages using Discord's message search, usually in a few minutes. If search isn't available for the server, it reads channel history instead. Messages already counted live are skipped and gaps when tracking was off are filled in, so nothing is counted twice, and each member is analyzed once (Allow re-analysis in /settings clears them first to redo it). Set keywords in /settings first if they should count in old messages. A progress card shows how far it's got.",
            "user_perms": (
                "Manage Server"
            ),
            "bot_perms": (
                "Read Message History in the channels it counts"
            ),
        },
    )
    @app_commands.describe(target="The member whose past messages to count")
    async def single_user(self, interaction: discord.Interaction, target: discord.Member) -> None:
        if not await self._begin(interaction):
            return
        reason = await self._blocked_reason(interaction)
        if reason is None and target.bot:
            reason = "Bots aren't counted."
        if reason is None and target.id in self.bot.blacklisted:
            reason = f"{target.mention} is banned from using WordCounter, so their messages aren't counted."
        if reason is None and await self.bot.db.is_user_analyzed(interaction.guild.id, target.id):
            reason = (
                f"{target.mention} has already been analyzed in this server.\n"
                "-# To run it again, unlock them in `/settings` → Data & Reset Tools."
            )
        if reason:
            await private_followup(interaction, error_view(reason))
            return
        if await self._resume_interrupted(interaction):
            return

        keywords = await self.bot.db.get_keywords(interaction.guild.id)
        coverage = await self._coverage(interaction.guild.id, discord.utils.time_snowflake(interaction.created_at))
        view = AnalyzeConfirmView(self, interaction.user.id, keywords, coverage, target=target)
        view.message = await self._send(interaction, view)

    @analyze_chat.command(
        name="whole_server",
        description="Count every member's past messages",
        extras={
            "details": "Reads every tracked channel, voice chat and thread, active and archived, once, and credits each past message to its author, for every member who hasn't been analyzed yet. Messages already counted live are skipped, so nothing is counted twice. Progress is saved every minute, so if WordCounter restarts mid-run it picks up where it stopped on its own. Channels it can't read are listed at the end so you know what was left out. One analysis runs in a server at a time.",
            "user_perms": (
                "Manage Server"
            ),
            "bot_perms": (
                "Read Message History in the channels it counts"
            ),
        },
    )
    async def whole_server(self, interaction: discord.Interaction) -> None:
        if not await self._begin(interaction):
            return
        reason = await self._blocked_reason(interaction)
        if reason:
            await private_followup(interaction, error_view(reason))
            return
        if await self._resume_interrupted(interaction):
            return

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
