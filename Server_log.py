import discord
from discord.ext import commands
import logging
import aiohttp
import os
from dotenv import load_dotenv

load_dotenv()

class webhook_container(discord.ui.LayoutView):
    def __init__(self, bot, guild, action: str):
        super().__init__()
        self.bot = bot
        
        if guild.icon:
            icon = guild.icon.url
        else:
            icon = None

        color = discord.Colour.green() if action == "Joined Server" else discord.Colour.red()

        container = discord.ui.Container(
            discord.ui.Section(
                discord.ui.TextDisplay(content=f"### **{action}:** \n**{guild.name}** \n*({guild.id})*"),
                accessory=discord.ui.Thumbnail(media=icon)
            ),
            discord.ui.Separator(),
            discord.ui.TextDisplay(content=f"**Owner:** {guild.owner.name} \n**Member Count:** {guild.member_count}"),
            discord.ui.Separator(spacing=discord.SeparatorSpacing.large),
            discord.ui.TextDisplay(f"-# Total Guilds: {len(self.bot.guilds)}"),
            accent_colour=color
        )
        self.add_item(container)

class ServerJoinLogger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = logging.getLogger("bot_server_joins")
        self.logger.setLevel(logging.INFO)
        self.handler = logging.FileHandler(filename="bot_server_joins.log", encoding="utf-8", mode="a")
        self.handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
        self.logger.addHandler(self.handler)
        self.session = aiohttp.ClientSession()

    async def send_webhook_message(self, webhook_url, view: discord.ui.LayoutView):
        webhook = discord.Webhook.from_url(webhook_url, session=self.session)
        try:
            await webhook.send(view=view)
            self.logger.info("Webhook message sent successfully")
        except Exception as e:
            self.logger.error(f"Exception occurred: {str(e)}")
      
    @commands.Cog.listener()
    async def on_guild_join(self, guild):
        self.logger.info(f"Guild Join: {guild.name} ({guild.id})\nOwner: {guild.owner.name} ({guild.owner.id})")
        webhook_url = str(os.getenv('BOT_WEBHOOK_URL'))
        view = webhook_container(self.bot, guild, "Joined Server")
        await self.send_webhook_message(webhook_url, view)

    @commands.Cog.listener()
    async def on_guild_remove(self, guild):
        self.logger.info(f"Guild Remove: {guild.name} ({guild.id})")
        webhook_url = str(os.getenv('BOT_WEBHOOK_URL'))
        view = webhook_container(self.bot, guild, "Left Server")
        await self.send_webhook_message(webhook_url, view)

    async def cog_unload(self):
        await self.session.close()

async def setup(bot):
    await bot.add_cog(ServerJoinLogger(bot))