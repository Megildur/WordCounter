import random
import discord
from discord.ext import commands, tasks

class Status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cycle.start()

    def cog_unload(self):
        self.cycle.cancel()

    @tasks.loop(hours=1.0)
    async def cycle(self):
        try:
            server_count = len(self.bot.guilds)
            user_count = len(self.bot.users)

            presences = [
                discord.Activity(type=discord.ActivityType.listening, name="your commands", state="Awaiting slash commands - Ready!"),
                discord.Activity(type=discord.ActivityType.watching, name="your messages", state="Reading chat - Filtering spam"),
                discord.Activity(type=discord.ActivityType.listening, name=f"{server_count} servers", state="Global scope - Online"),
                discord.Activity(type=discord.ActivityType.listening, name="your messages", state="Parsing arguments - Active"),
                discord.Activity(type=discord.ActivityType.watching, name=f"over {server_count} servers", state="Server management - Uptime: 100%"),
                discord.Activity(type=discord.ActivityType.listening, name="your messages", state="Awaiting prefix commands - Listening..."),
                discord.Activity(type=discord.ActivityType.watching, name=f"over {user_count} users", state="User moderation - Keeping the peace"),
                discord.Activity(type=discord.ActivityType.listening, name=f"{user_count} users", state="Global user base - Processing"),
                
                discord.Activity(type=discord.ActivityType.watching, name="the Discord Developer Portal", state="Reading docs - Updating API"),
                
                discord.CustomActivity(name=f"Currently active in {server_count} guilds 🚀"),
                discord.CustomActivity(name="Recharging my batteries 🔋"),
                discord.CustomActivity(name="Beep boop 🤖")
            ]
            
            statuses = [discord.Status.online, discord.Status.idle, discord.Status.do_not_disturb]
            
            chosen_presence = random.choice(presences)
            chosen_status = random.choice(statuses)
            
            await self.bot.change_presence(activity=chosen_presence, status=chosen_status)
            
            if isinstance(chosen_presence, discord.CustomActivity):
                print(f"Changed status to: [{chosen_status}] Custom Status: {chosen_presence.name}")
            else:
                print(f"Changed status to: [{chosen_status}] {chosen_presence.type.name.capitalize()} {chosen_presence.name}")
                
        except Exception as e:
            print(f'Error updating status: {e}')

    @cycle.before_loop
    async def before_cycle(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        print(f'Logged in as {self.bot.user.name} (ID: {self.bot.user.id})')

async def setup(bot):
   await bot.add_cog(Status(bot))

