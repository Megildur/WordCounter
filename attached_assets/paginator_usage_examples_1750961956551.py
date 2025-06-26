
"""
ButtonPaginator Usage Examples
Demonstrates correct implementation patterns based on existing codebase usage
"""

import discord
from discord.ext import commands
from discord import app_commands
from typing import List, Optional
from bot.utils.paginator import ButtonPaginator


class PaginatorExamples(commands.Cog):
    """Examples of correct ButtonPaginator implementation"""
    
    def __init__(self, bot):
        self.bot = bot

    # Example 1: Basic Standard Paginator (like starboard.py)
    @app_commands.command(name="example_basic", description="Basic paginator example")
    async def example_basic_paginator(self, interaction: discord.Interaction):
        """Basic paginator with simple embeds"""
        
        # Create multiple pages of content
        embeds = []
        for i in range(5):
            embed = discord.Embed(
                title=f"Page {i + 1}",
                description=f"This is the content for page {i + 1}",
                color=discord.Color.blue()
            )
            embed.add_field(
                name="Example Field",
                value=f"Some example content for page {i + 1}",
                inline=False
            )
            embed.set_footer(text=f"Page {i + 1} of 5")
            embeds.append(embed)

        # Create and start paginator
        paginator = ButtonPaginator.create_standard_paginator(
            embeds, 
            author_id=interaction.user.id,
            timeout=180.0
        )
        
        await paginator.start(interaction)

    # Example 2: Welcome-Style Paginator (like economy embeds)
    @app_commands.command(name="example_welcome", description="Welcome-style paginator example")
    async def example_welcome_paginator(self, interaction: discord.Interaction):
        """Welcome paginator with custom start button"""
        
        # Create welcome pages (similar to economy welcome embeds)
        embeds = []
        
        # Page 1: Introduction
        embed1 = discord.Embed(
            title="🎉 Welcome to Our System!",
            description="Welcome! This is a multi-page introduction to our features.",
            color=discord.Color.green()
        )
        embed1.add_field(
            name="📋 What You'll Learn",
            value="• Basic commands and features\n• How to get started\n• Pro tips and strategies",
            inline=False
        )
        embed1.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else interaction.user.default_avatar.url)
        embeds.append(embed1)

        # Page 2: Getting Started
        embed2 = discord.Embed(
            title="🚀 Getting Started",
            description="Here's how to begin your journey:",
            color=discord.Color.blue()
        )
        embed2.add_field(
            name="⚡ Quick Start Commands",
            value="• `/help` - View all commands\n• `/profile` - Check your profile\n• `/settings` - Configure preferences",
            inline=False
        )
        embed2.add_field(
            name="💡 Pro Tip",
            value="Start with the basic commands to familiarize yourself!",
            inline=False
        )
        embeds.append(embed2)

        # Page 3: Advanced Features
        embed3 = discord.Embed(
            title="⚡ Advanced Features",
            description="Once you're comfortable, try these advanced features:",
            color=discord.Color.purple()
        )
        embed3.add_field(
            name="🔧 Advanced Commands",
            value="• Custom configurations\n• Advanced settings\n• Premium features",
            inline=False
        )
        embed3.set_footer(text="Ready to start? Click the button below!")
        embeds.append(embed3)

        # Custom start button callback
        async def start_callback(button_interaction: discord.Interaction):
            success_embed = discord.Embed(
                title="✅ Welcome Complete!",
                description="You're all set! Use `/help` to see all available commands.",
                color=discord.Color.green()
            )
            success_embed.set_thumbnail(url=button_interaction.user.avatar.url if button_interaction.user.avatar else button_interaction.user.default_avatar.url)
            await button_interaction.response.edit_message(embed=success_embed, view=None)

        # Create welcome paginator with custom callback
        paginator = ButtonPaginator.create_welcome_paginator(
            embeds,
            author_id=interaction.user.id,
            timeout=300.0,
            start_button_callback=start_callback
        )
        
        await paginator.start(interaction)

    # Example 3: Leaderboard/Ranking Paginator (like invites)
    @app_commands.command(name="example_leaderboard", description="Leaderboard paginator example")
    async def example_leaderboard_paginator(self, interaction: discord.Interaction):
        """Paginated leaderboard similar to invite rankings"""
        
        # Simulate leaderboard data
        fake_data = [
            (f"User{i}", 1000 - i * 50, i + 1) for i in range(50)  # (name, score, rank)
        ]
        
        embeds = []
        users_per_page = 10
        
        for page_num in range(0, len(fake_data), users_per_page):
            embed = discord.Embed(
                title="🏆 Leaderboard Rankings",
                description="",
                color=discord.Color.gold()
            )
            
            page_users = fake_data[page_num:page_num + users_per_page]
            description = ""
            
            for user_name, score, rank in page_users:
                if rank == 1:
                    description += f"🥇 **{user_name}** - {score:,} points\n"
                elif rank == 2:
                    description += f"🥈 **{user_name}** - {score:,} points\n"
                elif rank == 3:
                    description += f"🥉 **{user_name}** - {score:,} points\n"
                else:
                    description += f"**{rank}.** {user_name} - {score:,} points\n"
            
            embed.description = description
            embed.set_footer(text=f"Page {(page_num // users_per_page) + 1}/{(len(fake_data) + users_per_page - 1) // users_per_page} • Total users: {len(fake_data)}")
            embeds.append(embed)

        # Handle single page vs multiple pages
        if len(embeds) == 1:
            await interaction.response.send_message(embed=embeds[0])
        else:
            paginator = ButtonPaginator.create_standard_paginator(
                embeds, 
                author_id=interaction.user.id,
                timeout=180.0
            )
            await paginator.start(interaction)

    # Example 4: Help System Paginator (like help.py)
    @app_commands.command(name="example_help", description="Help system paginator example")
    async def example_help_paginator(self, interaction: discord.Interaction, category: str = None):
        """Complex help system with category navigation"""
        
        # Create comprehensive help embeds
        embeds = self._create_help_embeds()
        
        if not embeds:
            await interaction.response.send_message("Error: Could not load help information.", ephemeral=True)
            return

        # Category mapping for direct navigation
        category_map = {
            "main": 0,
            "commands": 1,
            "features": 2,
            "advanced": 3,
        }

        # Start at specific category if requested
        start_page = category_map.get(category, 0) if category else 0
        
        paginator = ButtonPaginator.create_standard_paginator(
            embeds,
            author_id=interaction.user.id,
            timeout=300.0,
            loop=False
        )
        
        # Set starting page
        paginator.current_page = start_page
        
        await paginator.start(interaction)

    def _create_help_embeds(self) -> List[discord.Embed]:
        """Create help embeds similar to help.py structure"""
        embeds = []
        
        # Main overview
        embed1 = discord.Embed(
            title="🤖 Bot Help & Command Guide",
            description="Welcome to the comprehensive help system! Use the navigation buttons to browse through different sections.",
            color=discord.Color.blue()
        )
        embed1.add_field(
            name="📋 Available Sections",
            value="• **Basic Commands** - Essential commands to get started\n"
                  "• **Key Features** - Main functionality overview\n"
                  "• **Advanced Usage** - Power user features",
            inline=False
        )
        embeds.append(embed1)

        # Commands section
        embed2 = discord.Embed(
            title="⚡ Basic Commands",
            description="Essential commands every user should know:",
            color=discord.Color.green()
        )
        embed2.add_field(
            name="🔧 Core Commands",
            value="• `/help` - Show this help menu\n"
                  "• `/profile` - View your profile\n"
                  "• `/settings` - Adjust your preferences",
            inline=False
        )
        embeds.append(embed2)

        # Features section
        embed3 = discord.Embed(
            title="🎯 Key Features",
            description="Overview of main bot functionality:",
            color=discord.Color.purple()
        )
        embed3.add_field(
            name="✨ Main Features",
            value="• Comprehensive command system\n"
                  "• User customization options\n"
                  "• Advanced automation tools",
            inline=False
        )
        embeds.append(embed3)

        # Advanced section
        embed4 = discord.Embed(
            title="🚀 Advanced Usage",
            description="Power user features and advanced configurations:",
            color=discord.Color.red()
        )
        embed4.add_field(
            name="⚙️ Advanced Options",
            value="• Custom command configurations\n"
                  "• Automation setups\n"
                  "• Integration options",
            inline=False
        )
        embeds.append(embed4)

        return embeds

    # Example 5: Custom Button Paginator
    @app_commands.command(name="example_custom", description="Custom button paginator example")
    async def example_custom_paginator(self, interaction: discord.Interaction):
        """Paginator with custom buttons"""
        
        embeds = []
        for i in range(3):
            embed = discord.Embed(
                title=f"Custom Page {i + 1}",
                description=f"This page has custom functionality",
                color=discord.Color.orange()
            )
            embeds.append(embed)

        # Create custom buttons
        async def custom_action_callback(button_interaction: discord.Interaction):
            await button_interaction.response.send_message("Custom action triggered!", ephemeral=True)

        custom_button = discord.ui.Button(
            label="🎯 Custom Action",
            style=discord.ButtonStyle.success
        )
        custom_button.callback = custom_action_callback

        # Create paginator with custom button
        paginator = ButtonPaginator(
            embeds,
            author_id=interaction.user.id,
            timeout=180.0,
            custom_buttons=[custom_button]
        )
        
        await paginator.start(interaction)


async def setup(bot):
    """Setup function for the cog"""
    await bot.add_cog(PaginatorExamples(bot))


# Additional utility functions for common paginator patterns

def create_embed_pages(data: List[dict], title: str, items_per_page: int = 10) -> List[discord.Embed]:
    """
    Utility function to create paginated embeds from data
    
    Args:
        data: List of dictionaries containing page data
        title: Base title for all embeds
        items_per_page: Number of items per page
    
    Returns:
        List of discord.Embed objects
    """
    embeds = []
    total_pages = (len(data) + items_per_page - 1) // items_per_page
    
    for page_num in range(total_pages):
        start_idx = page_num * items_per_page
        end_idx = min(start_idx + items_per_page, len(data))
        page_data = data[start_idx:end_idx]
        
        embed = discord.Embed(
            title=f"{title} - Page {page_num + 1}",
            color=discord.Color.blue()
        )
        
        description = ""
        for item in page_data:
            description += f"• {item.get('name', 'Unknown')}: {item.get('value', 'N/A')}\n"
        
        embed.description = description
        embed.set_footer(text=f"Page {page_num + 1}/{total_pages} • Total items: {len(data)}")
        embeds.append(embed)
    
    return embeds


def create_simple_text_pages(content: str, max_length: int = 2000) -> List[str]:
    """
    Utility function to split long text into pages
    
    Args:
        content: Long text content to split
        max_length: Maximum characters per page
    
    Returns:
        List of text strings for pagination
    """
    if len(content) <= max_length:
        return [content]
    
    pages = []
    words = content.split()
    current_page = ""
    
    for word in words:
        if len(current_page + word + " ") > max_length:
            if current_page:
                pages.append(current_page.strip())
                current_page = word + " "
            else:
                # Single word is too long, force split
                pages.append(word[:max_length])
                current_page = word[max_length:] + " "
        else:
            current_page += word + " "
    
    if current_page.strip():
        pages.append(current_page.strip())
    
    return pages
