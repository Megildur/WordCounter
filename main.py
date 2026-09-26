import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import logging
from cogs.utils.database import WordCounterDatabase

load_dotenv()

intents = discord.Intents.all()

handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')


class MyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix='!wc', intents=intents)
        self.db = WordCounterDatabase(self)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.load_extension('Quicksync')
        await self.load_extension('Status')
        await self.load_extension('Errors')
        await self.load_extension('Server_log')
        await self.load_extension('OwnerCommands')
        for filename in os.listdir('cogs'):
            if filename.endswith('.py'):
                cog_name = filename[:-3]
                await self.load_extension(f'cogs.{cog_name}')

    async def close(self) -> None:
        await self.db.close()
        await super().close()


bot = MyBot()

if __name__ == '__main__':
    API_TOKEN = str(os.getenv('API_TOKEN'))
    bot.run(API_TOKEN, log_handler=handler, log_level=logging.ERROR)