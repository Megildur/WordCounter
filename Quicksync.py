"""!wcquicksync, a text command for administrators that pushes the /owner commands to every server in ALLOWED_GUILDS, since /owner sync can't be used before they are there."""

from __future__ import annotations
import logging
import discord
from discord.ext import commands
from cogs.utils.components import SUCCESS_COLOR, WARNING_COLOR, create_v2_view, error_view, is_member_timed_out
from cogs.utils.config import ALLOWED_GUILD_IDS

log = logging.getLogger(__name__)


class QuickSync(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.command(name="quicksync", hidden=True)
    async def quicksync(self, ctx: commands.Context) -> None:
        # A hidden command stays quiet unless the member is an active Administrator in an allowed server.
        if not (
            isinstance(ctx.author, discord.Member)
            and ctx.author.guild_permissions.administrator
            and not is_member_timed_out(ctx.author)
        ):
            return
        if not ALLOWED_GUILD_IDS or ctx.guild is None or ctx.guild.id not in ALLOWED_GUILD_IDS:
            return
        if ctx.guild.me is not None and not ctx.channel.permissions_for(ctx.guild.me).send_messages:
            return

        count = len(ALLOWED_GUILD_IDS)
        status = await ctx.send(
            view=create_v2_view("Syncing commands", f"Syncing to {count} allowed server(s).", color=WARNING_COLOR),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            # A guild sync only sends the commands registered to that guild, the /owner group. Global commands are synced with /owner sync.
            for guild_id in ALLOWED_GUILD_IDS:
                await self.bot.tree.sync(guild=discord.Object(id=guild_id))
        except Exception:
            log.exception("Quicksync failed")
            await status.edit(view=error_view("Sync failed. The error is in the log."))
            return
        await status.edit(view=create_v2_view("Commands synced", f"Synced to {count} allowed server(s).", color=SUCCESS_COLOR))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(QuickSync(bot))
