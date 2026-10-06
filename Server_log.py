from __future__ import annotations
import logging
import discord
from discord.ext import commands
from cogs.utils.components import ERROR_COLOR, SUCCESS_COLOR, create_v2_view
from cogs.utils.config import WEBHOOK_URL, daily_log_handler
from cogs.utils.http import get_session


async def owner_display_name(bot: commands.Bot, guild: discord.Guild) -> str:
    owner = guild.owner or bot.get_user(guild.owner_id or 0)
    if owner is None and guild.owner_id:
        try:
            owner = await bot.fetch_user(guild.owner_id)
        except discord.HTTPException:
            return "Unknown"
    return (owner.global_name or owner.name) if owner else "Unknown"


def guild_log_view(bot: commands.Bot, guild: discord.Guild, owner: str, joined: bool) -> discord.ui.LayoutView:
    return create_v2_view(
        "Joined server" if joined else "Left server",
        f"**{guild.name}**\n-# {guild.id}",
        fields=[("Owner", owner), ("Members", f"{guild.member_count or 0:,}")],
        footer=f"Now in {len(bot.guilds):,} servers",
        thumbnail_url=guild.icon.url if guild.icon else None,
        color=SUCCESS_COLOR if joined else ERROR_COLOR,
    )


class ServerJoinLogger(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.logger = logging.getLogger("bot_server_joins")
        self.logger.setLevel(logging.INFO)
        self.handler = daily_log_handler("bot_server_joins.log")
        self.handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        self.logger.addHandler(self.handler)

    async def cog_unload(self) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.close()

    async def send_webhook_message(self, view: discord.ui.LayoutView) -> None:
        if not WEBHOOK_URL:
            return
        try:
            await discord.Webhook.from_url(WEBHOOK_URL, session=get_session()).send(view=view)
        except (discord.HTTPException, ValueError) as error:
            self.logger.error("Webhook message failed: %s", error)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.logger.info("Guild join: %s (%s), owner %s (%s)", guild.name, guild.id, guild.owner, guild.owner_id)
        await self.send_webhook_message(guild_log_view(self.bot, guild, await owner_display_name(self.bot, guild), joined=True))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.logger.info("Guild remove: %s (%s)", guild.name, guild.id)
        await self.send_webhook_message(guild_log_view(self.bot, guild, await owner_display_name(self.bot, guild), joined=False))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerJoinLogger(bot))
