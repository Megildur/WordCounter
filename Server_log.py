from __future__ import annotations
import logging
import aiohttp
import discord
from discord.ext import commands
from cogs.utils.components import ERROR_COLOR, SUCCESS_COLOR, create_v2_view
from cogs.utils.config import WEBHOOK_URL, daily_log_handler


def guild_log_view(bot: commands.Bot, guild: discord.Guild, joined: bool) -> discord.ui.LayoutView:
    owner = guild.owner.name if guild.owner else f"<@{guild.owner_id}>"
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
        self.session = aiohttp.ClientSession()

    async def cog_unload(self) -> None:
        self.logger.removeHandler(self.handler)
        self.handler.close()
        await self.session.close()

    async def send_webhook_message(self, view: discord.ui.LayoutView) -> None:
        if not WEBHOOK_URL:
            return
        try:
            await discord.Webhook.from_url(WEBHOOK_URL, session=self.session).send(view=view)
        except (discord.HTTPException, ValueError) as error:
            self.logger.error("Webhook message failed: %s", error)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild) -> None:
        self.logger.info("Guild join: %s (%s), owner %s (%s)", guild.name, guild.id, guild.owner, guild.owner_id)
        await self.send_webhook_message(guild_log_view(self.bot, guild, joined=True))

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild) -> None:
        self.logger.info("Guild remove: %s (%s)", guild.name, guild.id)
        await self.send_webhook_message(guild_log_view(self.bot, guild, joined=False))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ServerJoinLogger(bot))
