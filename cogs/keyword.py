from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, create_v2_container, error_view
from paginator import ButtonPaginator

KEYWORDS_PER_PAGE = 30


class Keyword(commands.Cog):
    keyword = app_commands.Group(name="keyword", description="Keyword viewing commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @keyword.command(name="list", description="View the keywords watched in the server")
    async def keyword_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            return
        keywords = await self.bot.db.get_keywords(interaction.guild_id)
        if not keywords:
            await interaction.response.send_message(
                view=error_view("No keywords are being tracked here yet. Add some in `/settings`."),
                ephemeral=True,
            )
            return

        pages = [
            create_v2_container(
                title="Tracked keywords",
                description=f"{len(keywords)} keyword(s) are counted in this server:\n\n"
                + "\n".join(f"- `{kw}`" for kw in keywords[start:start + KEYWORDS_PER_PAGE]),
                footer="Admins can change these in /settings",
                color=BRAND_COLOR,
            )
            for start in range(0, len(keywords), KEYWORDS_PER_PAGE)
        ]
        await ButtonPaginator(pages, author_id=interaction.user.id).start(interaction)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Keyword(bot))
