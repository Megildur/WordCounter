import discord
from discord.ext import commands
from dotenv import load_dotenv
import os
import sys
import asyncio
import logging
import traceback
from cogs.utils.database import WordCounterDatabase

# Ensure console supports utf-8 characters and emojis without encoding crashes
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

load_dotenv()

# Setup root logging to output to both console (stdout) and discord.log
log_formatter = logging.Formatter(
    fmt='[%(asctime)s] [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

file_handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='a')
file_handler.setFormatter(log_formatter)
file_handler.setLevel(logging.INFO)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(log_formatter)
console_handler.setLevel(logging.INFO)

root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers.clear()
root_logger.addHandler(file_handler)
root_logger.addHandler(console_handler)

intents = discord.Intents.all()


class MyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix='!wc', intents=intents)
        self.db = WordCounterDatabase(self)

    async def setup_hook(self) -> None:
        await self.db.connect()

        # Capture any unhandled background task / asyncio loop exceptions
        loop = asyncio.get_running_loop()

        def handle_async_exception(loop, context):
            msg = context.get('message')
            exc = context.get('exception')
            if exc:
                logging.error(f"Unhandled background process exception: {msg or exc}", exc_info=exc)
                tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                print(f"\n[ERROR] Unhandled background process exception: {msg or exc}\n{tb}\n", flush=True)
            else:
                logging.error(f"Unhandled background process context: {context}")
                print(f"\n[ERROR] Unhandled background process context: {context}\n", flush=True)

        loop.set_exception_handler(handle_async_exception)

        self.tree.allowed_installs = discord.app_commands.AppInstallationType(guild=True, user=False)
        self.tree.allowed_contexts = discord.app_commands.AppCommandContext(guild=True, dm_channel=False, private_channel=False)
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
    # log_handler=None allows discord.py logs to propagate to our dual console/file root logger
    bot.run(API_TOKEN, log_handler=None)