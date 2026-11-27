"""The live stats panel: /owner stats_panel posts it in a channel, and a loop edits it every few minutes.

The owner picks the channel (one panel per server). The loop reads every figure from memory and the database totals, draws the card once and edits each panel's message with it, and stops the pass at the first 429. A panel whose message was deleted is posted again in the same channel. One whose channel is deleted (also while the bot was offline, noticed once it's back, unless Discord has the server unavailable), or whose server the bot has left, is forgotten.
"""

from __future__ import annotations
import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Optional, Set, Tuple
import discord
from discord.ext import commands, tasks
from cogs.utils import cards, people
from cogs.utils.components import BRAND_COLOR, ERROR_COLOR, NEUTRAL_COLOR, create_v2_view
from cogs.utils.mysql import DatabaseError
from web.pages import command_reference

log = logging.getLogger(__name__)

UPDATE_MINUTES = 5
# The card is a file, so its channel needs Attach Files too.
PANEL_NEEDS = ("view_channel", "send_messages", "attach_files")
NEEDS_NAMES = {"view_channel": "View Channel", "send_messages": "Send Messages", "attach_files": "Attach Files"}


@dataclass(frozen=True)
class Figures:
    servers: int
    people: int  # each person once, however many servers they share with the bot, bots left out
    people_complete: bool  # false while some server's member list isn't fully loaded, so `people` is a floor
    commands: int  # slash commands, the same count as the home page
    words: Optional[int]  # None while the database can't be read
    messages: Optional[int]
    latency_ms: Optional[int]  # to Discord's gateway, None while it's disconnected
    started_at: Optional[int]  # when the bot started, in Unix seconds


def short(number: Optional[int]) -> str:
    """A count with thousands separators, written short from a billion up, or a dash when it isn't known."""
    if number is None:
        return "–"
    for size, suffix in ((10 ** 12, "T"), (10 ** 9, "B")):
        if number >= size:
            return f"{number / size:.1f}{suffix}"
    return f"{number:,}"


def latency_text(latency_ms: Optional[int]) -> str:
    return f"{latency_ms} ms" if latency_ms is not None else "offline"


def up_for(seconds: int) -> str:
    """How long, to the minute: "12 minutes", "3 hours 4 minutes". Written here at every update instead of by Discord's relative time, which the app only redraws now and then."""
    seconds = max(60, round(seconds / 60) * 60)
    parts = []
    for size, name in ((604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute")):
        count, seconds = divmod(seconds, size)
        if count:
            parts.append(f"{count} {name}{'s' if count != 1 else ''}")
        if len(parts) == 2:
            break
    return " ".join(parts)


def missing_in(channel: discord.abc.GuildChannel) -> Tuple[str, ...]:
    """The names of what the bot lacks in `channel` to post the panel, or nothing."""
    permissions = channel.permissions_for(channel.guild.me)
    return tuple(NEEDS_NAMES[name] for name in PANEL_NEEDS if not getattr(permissions, name))


def tiles_of(figures: Figures) -> Tuple[Tuple[str, str, str], ...]:
    """(label, value, caption) for the card's six plaques, in reading order."""
    return (
        ("Servers", short(figures.servers), "WordCounter is in"),
        ("Unique users", short(figures.people), "each person once" if figures.people_complete else "each person, at least"),
        ("Commands", short(figures.commands), "slash commands"),
        ("Words", short(figures.words), "counted so far"),
        ("Messages", short(figures.messages), "counted so far"),
        ("Latency", latency_text(figures.latency_ms), "to Discord"),
    )


def panel_view(figures: Figures, updated_at: int) -> discord.ui.LayoutView:
    """The message: the card on top, the same numbers in text under it."""
    shown_people = short(figures.people) if figures.people_complete else f"at least {short(figures.people)}"
    lines = [
        f"**Servers:** {short(figures.servers)}",
        f"**Unique users:** {shown_people}",
        f"**Commands:** {short(figures.commands)} slash commands",
        f"**Words counted:** {short(figures.words)}",
        f"**Messages counted:** {short(figures.messages)}",
        f"**Latency:** {latency_text(figures.latency_ms)}",
    ]
    if figures.started_at:
        lines.append(f"**Online for:** {up_for(updated_at - figures.started_at)}, since <t:{figures.started_at}:F>")
    description = f"WordCounter's live numbers: {short(figures.servers)} servers, {short(figures.people)} unique users, {short(figures.words)} words counted. The flip counter floats above Earth beside six lit plaques."
    view = discord.ui.LayoutView(timeout=None)
    view.add_item(discord.ui.Container(
        discord.ui.TextDisplay("## WordCounter live stats"),
        cards.gallery(cards.BOT_STATS_FILE, description),
        discord.ui.TextDisplay("\n".join(lines)),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"-# Updated <t:{updated_at}:R>, and again every {UPDATE_MINUTES} minutes."),
        accent_colour=BRAND_COLOR,
    ))
    return view


class StatsPanel(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Held per panel mutation or edit so channel/guild events or /owner stats_panel can run between panels during a pass.
        self.lock = asyncio.Lock()
        self._mutated: Set[int] = set()
        self.updates.start()

    def cog_unload(self) -> None:
        self.updates.cancel()

    async def figures(self) -> Figures:
        count, complete = people.unique_users(self.bot)
        try:
            messages, words = await self.bot.db.get_global_totals()
        except (DatabaseError, OSError) as error:
            # With the database down the panel still updates, with a dash where the totals go.
            log.warning("Could not read the totals for the stats panel: %s", error)
            messages = words = None
        latency = self.bot.latency
        return Figures(
            servers=len(self.bot.guilds),
            people=count,
            people_complete=complete,
            commands=len(command_reference(self.bot)["slash"]),
            words=words,
            messages=messages,
            latency_ms=round(latency * 1000) if math.isfinite(latency) else None,
            started_at=int(self.bot.started_at) if getattr(self.bot, "started_at", None) else None,
        )

    async def _draw(self) -> Tuple[Figures, bytes]:
        """The numbers as they are now, and the card that shows them."""
        now = await self.figures()
        return now, await asyncio.to_thread(cards.bot_stats_card, tiles_of(now))

    async def _send(self, channel: discord.abc.Messageable, now: Figures, picture: bytes) -> discord.Message:
        return await channel.send(view=panel_view(now, int(time.time())), file=cards.picture_file(picture, cards.BOT_STATS_FILE))

    async def _delete_message(self, channel: Optional[discord.abc.GuildChannel], message_id: int) -> None:
        if channel is None:
            return
        try:
            await channel.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException:
            log.debug("Could not delete the stats panel message %s", message_id, exc_info=True)

    @staticmethod
    def _retry_after(error: discord.HTTPException) -> str:
        """The Retry-After header on Discord's refusal, for the log. discord.py waits out an ordinary rate limit by itself, so a 429 that reaches the code is Discord blocking this machine's address for a while, and that header is the only hint how long."""
        headers = getattr(getattr(error, "response", None), "headers", None) or {}
        return str(headers.get("Retry-After", "not sent"))

    async def refresh(self, panel: Tuple[int, int, int], now: Figures, picture: bytes) -> bool:
        """Show the current numbers on a panel, posting it again when its message is gone. False when Discord answered 429, so the pass stops instead of asking again."""
        guild_id, channel_id, message_id = panel
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            # The bot left while it was offline, so no event said so.
            await self.bot.db.delete_stats_panel(guild_id)
            return True
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            if self.bot.is_ready() and not guild.unavailable and guild.get_channel(channel_id) is None:
                # The channel was deleted while the bot was offline, so no event removed the row. A server Discord has unavailable (an outage) may have no channels loaded at all, so its panel is left alone.
                await self.bot.db.delete_stats_panel_channel(channel_id)
            else:
                log.debug("The stats panel's channel %s isn't visible; leaving it for now", channel_id)
            return True
        try:
            await channel.get_partial_message(message_id).edit(
                view=panel_view(now, int(time.time())), attachments=[cards.picture_file(picture, cards.BOT_STATS_FILE)],
            )
            return True
        except discord.NotFound:
            pass
        except discord.HTTPException as error:
            log.warning("Could not update the stats panel in channel %s (status %s, Retry-After: %s)", channel_id, error.status, self._retry_after(error), exc_info=True)
            return error.status != 429
        missing = missing_in(channel)
        if missing:
            log.warning("Can't post the stats panel in channel %s: it needs %s", channel_id, ", ".join(missing))
            return True
        try:
            message = await self._send(channel, now, picture)
        except discord.HTTPException as error:
            log.warning("Could not post the stats panel in channel %s (status %s, Retry-After: %s)", channel_id, error.status, self._retry_after(error), exc_info=True)
            return error.status != 429
        try:
            await self.bot.db.set_stats_panel_message(guild_id, channel_id, message.id)
        except Exception:
            # A copy nobody edits would stay in the channel with frozen numbers, and the next pass would post another.
            log.warning("Could not save the reposted stats panel in channel %s", channel_id, exc_info=True)
            await self._delete_message(channel, message.id)
        return True

    @tasks.loop(minutes=UPDATE_MINUTES)
    async def updates(self) -> None:
        # discord.py ends a loop for good on a database error, so nothing is allowed to escape this body.
        try:
            async with self.lock:
                panels = await self.bot.db.stats_panels()
                self._mutated.clear()
            if not panels:
                return
            now, picture = await self._draw()
            for panel in panels:
                try:
                    async with self.lock:
                        if panel[0] in self._mutated:
                            current = await self.bot.db.stats_panel(panel[0])
                            if current is None:
                                continue
                            panel = current
                        if not await self.refresh(panel, now, picture):
                            break
                except Exception:
                    log.exception("The stats panel in channel %s failed to update", panel[1])
        except Exception:
            log.exception("The stats panel update failed and will run again at its next turn")

    @updates.before_loop
    async def before_updates(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        # Waits for a post or a move in progress, so a channel deleted meanwhile doesn't get a row saved after it.
        async with self.lock:
            try:
                await self.bot.db.delete_stats_panel_channel(channel.id)
                self._mutated.add(channel.guild.id)
            except Exception:
                log.warning("Could not forget the stats panel of deleted channel %s", channel.id, exc_info=True)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        async with self.lock:
            try:
                await self.bot.db.delete_stats_panel(guild.id)
                self._mutated.add(guild.id)
            except Exception:
                log.warning("Could not forget the stats panel of server %s", guild.id, exc_info=True)

    async def owner_panel(self, interaction: discord.Interaction, channel: Optional[discord.TextChannel], remove: bool) -> None:
        """Post the panel in `channel`, move it there, take it down, or say where it is."""
        guild = interaction.guild
        if guild is None:
            return
        if remove and channel is not None:
            await interaction.response.send_message(
                view=create_v2_view("Pick one", "Use **channel** to post or move the panel, or **remove** to take it down, not both.", color=NEUTRAL_COLOR), ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        async with self.lock:
            try:
                current = await self.bot.db.stats_panel(guild.id)
                if remove:
                    await self._remove(interaction, current)
                elif channel is None:
                    await self._where(interaction, current)
                else:
                    await self._post(interaction, current, channel)
            except (DatabaseError, OSError):
                log.warning("The stats panel command couldn't use the database", exc_info=True)
                await interaction.followup.send(view=create_v2_view("Couldn't do that", "The database didn't answer. Try again in a moment.", color=ERROR_COLOR), ephemeral=True)

    async def _say(self, interaction: discord.Interaction, title: str, body: str, color: discord.Colour = BRAND_COLOR) -> None:
        await interaction.followup.send(view=create_v2_view(title, body, color=color), ephemeral=True)

    async def _where(self, interaction: discord.Interaction, current: Optional[Tuple[int, int, int]]) -> None:
        if current is None:
            await self._say(interaction, "No stats panel here", "Pick a channel in **channel** to post one.", NEUTRAL_COLOR)
            return
        guild_id, channel_id, message_id = current
        link = f"https://discord.com/channels/{guild_id}/{channel_id}/{message_id}"
        await self._say(
            interaction, "Stats panel",
            f"It's in <#{channel_id}> ([the message](<{link}>)) and updates every {UPDATE_MINUTES} minutes. Pick another channel in **channel** to move it, or turn on **remove** to take it down.",
            NEUTRAL_COLOR,
        )

    async def _remove(self, interaction: discord.Interaction, current: Optional[Tuple[int, int, int]]) -> None:
        if current is None:
            await self._say(interaction, "Nothing to remove", "This server has no stats panel.", NEUTRAL_COLOR)
            return
        await self.bot.db.delete_stats_panel(current[0])
        self._mutated.add(current[0])
        await self._delete_message(self.bot.get_channel(current[1]), current[2])
        await self._say(interaction, "Stats panel removed", "It's deleted and won't be posted again.")

    async def _post(self, interaction: discord.Interaction, current: Optional[Tuple[int, int, int]], channel: discord.TextChannel) -> None:
        missing = missing_in(channel)
        if missing:
            await self._say(interaction, "Can't post there", f"WordCounter needs {' and '.join(f'**{name}**' for name in missing)} in {channel.mention}.", ERROR_COLOR)
            return
        now, picture = await self._draw()
        try:
            message = await self._send(channel, now, picture)
        except discord.HTTPException:
            log.warning("Could not post the stats panel in channel %s", channel.id, exc_info=True)
            await self._say(interaction, "Can't post there", "Discord didn't accept the message. Check WordCounter's permissions in the channel.", ERROR_COLOR)
            return
        try:
            await self.bot.db.save_stats_panel(channel.guild.id, channel.id, message.id)
            self._mutated.add(channel.guild.id)
        except Exception:
            # A panel nobody updates is worse than saying it failed, and the old one, if there was one, is still there.
            log.warning("Could not save the stats panel", exc_info=True)
            await self._delete_message(channel, message.id)
            await self._say(interaction, "Couldn't save it", "WordCounter took the new panel down again. The old one, if there was one, is still there. Try again in a moment.", ERROR_COLOR)
            return
        if current is not None:
            await self._delete_message(self.bot.get_channel(current[1]), current[2])
        verb = "Moved to" if current is not None and current[1] != channel.id else "Posted in"
        await self._say(interaction, "Stats panel is live", f"{verb} {channel.mention}. It updates every {UPDATE_MINUTES} minutes.")


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatsPanel(bot))
