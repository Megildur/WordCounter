"""`SectionMenu`, the base of the /settings menu.

One message with a select to switch between sections, a body that changes with the section, confirmation steps and a Close button. A subclass lists its `sections` and writes a `_populate_<key>_tab` method for each key (see `SettingsMenuView` in `cogs/counter_cmds.py`).
"""

from __future__ import annotations
from typing import List, Optional, Sequence, Tuple
import discord
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, NEUTRAL_COLOR, AuthorOnlyView, Callback, create_v2_view, make_button

# (key, label, description) of one section, as shown in the section select.
Section = Tuple[str, str, str]


class SectionMenu(AuthorOnlyView):
    """A menu in one message that is rebuilt from scratch on every change.

    `build_ui` clears the view and draws the header, an optional status banner, the section select, the active section and the bottom buttons. Handlers change `active_tab`, `pending_confirmation` or `status_banner`, then call `refresh_and_edit`. Only `author_id` can use it.
    """
    sections: Sequence[Section] = ()
    title = ""
    closed_hint = ""

    def __init__(self, bot: commands.Bot, guild: discord.Guild, author_id: int, *, denied_message: str) -> None:
        super().__init__(author_id=author_id, denied_message=denied_message, timeout=300.0)
        self.bot = bot
        self.guild = guild
        self.active_tab = self.sections[0][0]
        self.pending_confirmation: Optional[str] = None
        self.status_banner: Optional[Tuple[str, discord.Colour]] = None

    async def load_state(self) -> None:
        return None

    def description(self) -> str:
        return ""

    def build_ui(self) -> None:
        self.clear_items()
        container = discord.ui.Container(accent_colour=self.status_banner[1] if self.status_banner else BRAND_COLOR)

        header = discord.ui.TextDisplay(f"### {self.title}\n{self.description()}")
        if self.guild.icon:
            container.add_item(discord.ui.Section(header, accessory=discord.ui.Thumbnail(self.guild.icon.url)))
        else:
            container.add_item(header)

        if self.status_banner:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(self.status_banner[0]))

        navigation = discord.ui.Select(
            placeholder="Go to a section",
            options=[
                discord.SelectOption(label=label, value=value, description=description, default=self.active_tab == value)
                for value, label, description in self.sections
            ],
        )
        navigation.callback = self._on_tab_select
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(navigation))
        container.add_item(discord.ui.Separator())

        # Each section key needs a matching _populate_<key>_tab method on the subclass.
        getattr(self, f"_populate_{self.active_tab}_tab")(container)

        bottom = []
        if self.active_tab != self.sections[0][0]:
            bottom.append(make_button("Back to overview", discord.ButtonStyle.primary, self._goto(self.sections[0][0])))
        bottom.append(make_button("Close", discord.ButtonStyle.secondary, self._on_close_menu))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(*bottom))
        self.add_item(container)

    def _confirmation(
        self, container: discord.ui.Container, text: str, confirm_label: str, style: discord.ButtonStyle, on_confirm: Callback
    ) -> None:
        """Adds a question with a confirm button and a Cancel button. Callers return straight after it, so the section's normal buttons stay hidden while the question is open."""
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(text))
        container.add_item(
            discord.ui.ActionRow(
                make_button(confirm_label, style, on_confirm),
                make_button("Cancel", discord.ButtonStyle.secondary, self._cancel_confirmation),
            )
        )

    def _goto(self, tab: str) -> Callback:
        async def callback(interaction: discord.Interaction) -> None:
            self.active_tab = tab
            self.pending_confirmation = None
            self.status_banner = None
            await self.refresh_and_edit(interaction)

        return callback

    def _prompt(self, confirmation: str) -> Callback:
        """Returns a button callback that sets the pending confirmation and redraws, so the section shows that question."""
        async def callback(interaction: discord.Interaction) -> None:
            self.pending_confirmation = confirmation
            self.status_banner = None
            await self.refresh_and_edit(interaction)

        return callback

    async def _on_tab_select(self, interaction: discord.Interaction) -> None:
        values = interaction.data.get("values") or [self.sections[0][0]]
        await self._goto(values[0])(interaction)

    async def _cancel_confirmation(self, interaction: discord.Interaction) -> None:
        self.pending_confirmation = None
        self.status_banner = ("Cancelled.", NEUTRAL_COLOR)
        await self.refresh_and_edit(interaction)

    def _selected_ids(self, interaction: discord.Interaction) -> List[int]:
        """Returns the ids picked in a channel, user or role select, taken from the raw interaction data because the select itself is out of reach of the view's callbacks."""
        return [int(value) for value in interaction.data.get("values", [])]

    async def refresh_and_edit(self, interaction: discord.Interaction) -> None:
        """Redraws the menu in place: defers if the handler hasn't answered yet, reloads the state from the database, rebuilds the items and edits the original response.

        Handlers that write to the database defer first themselves, because the write can take longer than the 3 seconds Discord gives for a reply.
        """
        if not interaction.response.is_done():
            await interaction.response.defer()
        await self.load_state()
        self.build_ui()
        await interaction.edit_original_response(view=self)

    async def _on_close_menu(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.defer()
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:
            # If the delete is refused, replace the menu with a short closed notice so it doesn't stay on screen as a dead menu.
            await interaction.edit_original_response(view=create_v2_view(f"{self.title} closed", self.closed_hint, color=NEUTRAL_COLOR))
