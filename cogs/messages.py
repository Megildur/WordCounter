import discord
from discord.ext import commands
from cogs.utils.database import WordCounterDatabase


class Messages(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        if not hasattr(self.bot, "db"):
            self.bot.db = WordCounterDatabase(self.bot)

    async def cog_load(self) -> None:
        await self.bot.db.ensure_connected()

    async def add_msg(self, guild_id, user_id, channel_id) -> None:
        await self.bot.db.add_message_count(guild_id, user_id, channel_id)

    async def del_msg(self, guild_id, user_id, channel_id) -> None:
        await self.bot.db.remove_message_count(guild_id, user_id, channel_id)


async def setup(bot) -> None:
    await bot.add_cog(Messages(bot))
    print('Messages cog loaded')