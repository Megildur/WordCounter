"""The /keyword list command.

Reads a server's tracked keywords from the `keyword` table through `bot.db.get_keywords` and shows them 30 to a page with the shared paginator. Staff change the list in /settings (`cogs/counter_cmds.py`).
"""

from __future__ import annotations
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, SERVER_ONLY_MESSAGE, create_v2_container, error_view, private_followup
from paginator import ButtonPaginator

KEYWORDS_PER_PAGE = 30


class Keyword(commands.Cog):
    keyword = app_commands.Group(name="keyword", description="Keyword viewing commands")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @keyword.command(
        name="list",
        description="View the keywords watched in the server",
        extras={
            "details": "Lists the keywords this server counts, 30 a page. Staff add and remove them in /settings, and the keywords tab of /leaderboard shows who says each one most.",
        },
    )
    async def keyword_list(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            await interaction.response.send_message(view=error_view(SERVER_ONLY_MESSAGE), ephemeral=True)
            return
        # The list is public, so deferring first costs nothing and keeps the database read inside Discord's 3 second window.
        await interaction.response.defer()
        keywords = await self.bot.db.get_keywords(interaction.guild_id)
        if not keywords:
            await private_followup(interaction, error_view("No keywords are being tracked here yet. Add some in `/settings`."))
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
