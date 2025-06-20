import os
import discord
from discord import app_commands
from discord.ext import commands
from discord.errors import HTTPException
from cogs.utils.chat_analyzer import process_chat_history
from datetime import datetime
import io

class AnalyzeChat(commands.Cog):
    def __init__(self, bot) -> None:
        self.bot = bot
        print('AnalyzeChat cog loaded')
        
    @app_commands.command(name="analyze_chat", description="Analyze chat history from an HTML file")
    @app_commands.describe(
        file="The HTML file containing chat history",
        start_date="Start date for analysis (DD-MM-YYYY)",
        end_date="End date for analysis (DD-MM-YYYY)"
    )
    async def analyze_chat(self, interaction: discord.Interaction, file: discord.Attachment, start_date: str = None, end_date: str = None):
        await interaction.response.defer(thinking=True)

        if not file.filename.endswith('.html'):
            await interaction.followup.send("Please upload an HTML file.")
            return

        try:
            content = await file.read()

            start_date_obj = datetime.strptime(start_date, "%d-%m-%Y") if start_date else None
            end_date_obj = datetime.strptime(end_date, "%d-%m-%Y") if end_date else None

            progress_updates, result, user_ids = process_chat_history(content, start_date_obj, end_date_obj)

            # Send initial status message
            status_message = await interaction.followup.send("Processing chat history...")

            # Update status message with progress
            for update in progress_updates:
                await status_message.edit(content=update)

            # Create an embed with the analysis results
            embed = discord.Embed(title="Chat Analysis Results", color=discord.Color.from_str('#af2202'))

            # Split the result into sections
            sections = result.split('\n\n')

            # Add fields to the embed
            user_stats = []
            for section in sections:
                if section.startswith("Total Messages"):
                    embed.add_field(name="Overview", value=section, inline=False)
                elif section.startswith("<@"):
                    lines = section.split('\n')
                    if "words" in lines[0]:
                        embed.add_field(name="Top 10 Users by Word Count:", value='\n'.join(lines), inline=False)
                    elif "messages" in lines[0]:
                        embed.add_field(name="Top 10 Users by Messages Sent:", value='\n'.join(lines), inline=False)
                    elif "attachments" in lines[0]:
                        embed.add_field(name="Top 10 Users by Attachments Sent:", value='\n'.join(lines), inline=False)
                elif section.startswith("User:"):
                    user_stats.append(section)

            try:
                # Try to send the full embed with user stats
                for stats in user_stats:
                    user_id = stats.split('\n')[0].replace("User: <@", "").replace(">", "")
                    embed.add_field(name=f"Stats for <@{user_id}>", value='\n'.join(stats.split('\n')[1:]), inline=False)

                await interaction.followup.send(embed=embed)
            except HTTPException as he:
                # If the embed is too large, remove user stats and create a separate file
                embed.clear_fields()
                for section in sections:
                    if section.startswith("Total Messages"):
                        embed.add_field(name="Overview", value=section, inline=False)
                    elif section.startswith("<@"):
                        lines = section.split('\n')
                        if "words" in lines[0]:
                            embed.add_field(name="Top 10 Users by Word Count:", value='\n'.join(lines), inline=False)
                        elif "messages" in lines[0]:
                            embed.add_field(name="Top 10 Users by Messages Sent:", value='\n'.join(lines), inline=False)
                        elif "attachments" in lines[0]:
                            embed.add_field(name="Top 10 Users by Attachments Sent:", value='\n'.join(lines), inline=False)

                # Create a file with user stats, including usernames
                user_stats_content = []
                for stats in user_stats:
                    user_id = stats.split('\n')[0].replace("User: <@", "").replace(">", "")
                    user_name = user_ids.get(user_id, "Unknown")
                    stats_lines = stats.split('\n')
                    stats_lines.insert(1, f"Username: {user_name}")
                    user_stats_content.append('\n'.join(stats_lines))

                user_stats_file = discord.File(io.StringIO('\n\n'.join(user_stats_content)), filename="user_stats.txt")

                await interaction.followup.send("The analysis results are too long to display in a single message. Please see the attached file for user stats.", embed=embed, file=user_stats_file)

        except ValueError as ve:
            await interaction.followup.send(f"Invalid date format: {str(ve)}")
        except HTTPException as he:
            await interaction.followup.send(f"An error occurred while sending the message: {str(he)}")
        except Exception as e:
            await interaction.followup.send(f"An error occurred: {str(e)}")

async def setup(bot):
    await bot.add_cog(AnalyzeChat(bot))