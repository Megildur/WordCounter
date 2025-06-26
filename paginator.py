
from __future__ import annotations
from typing import (
    Dict,
    Generic,
    List,
    Optional,
    TypeVar,
    Any,
    TYPE_CHECKING,
    Sequence,
    Union,
)

import discord
from discord.abc import Messageable
from discord.ext import commands

if TYPE_CHECKING:
    from typing_extensions import Self

    Interaction = discord.Interaction[Any]
    Context = commands.Context[Any]

Page = Union[
    str,
    Sequence[str],
    discord.Embed,
    Sequence[discord.Embed],
    discord.File,
    Sequence[discord.File],
    discord.Attachment,
    Sequence[discord.Attachment],
    dict[str, Any],
]

PageT_co = TypeVar("PageT_co", bound=Page, covariant=True)


class ButtonPaginator(Generic[PageT_co], discord.ui.View):
    """Universal paginator with page indicator for consistent navigation"""
    
    message: Optional[Union[discord.Message, discord.WebhookMessage]] = None

    def __init__(
        self,
        pages: Sequence[PageT_co],
        *,
        author_id: Optional[int] = None,
        timeout: Optional[float] = 180.0,
        per_page: int = 1,
        loop: bool = False,
        custom_buttons: Optional[List[discord.ui.Button]] = None,
    ) -> None:
        super().__init__(timeout=timeout)
        self.author_id: Optional[int] = author_id
        self.current_page: int = 0
        self.per_page: int = per_page
        self.pages: Any = pages
        self.loop: bool = loop
        
        total_pages, left_over = divmod(len(self.pages), self.per_page)
        if left_over:
            total_pages += 1

        self.max_pages: int = total_pages
        self._page_kwargs: Dict[str, Any] = {"content": None, "embeds": [], "files": [], "view": self}
        
        # Clear default items and add buttons based on configuration
        self.clear_items()
        self._setup_buttons(custom_buttons)

    def _setup_buttons(self, custom_buttons: Optional[List[discord.ui.Button]] = None) -> None:
        """Setup buttons with consistent page indicator layout"""
        if self.max_pages <= 1:
            # No pagination needed - just add custom buttons if any
            if custom_buttons:
                for button in custom_buttons:
                    self.add_item(button)
            return
            
        # Standard layout with page indicator
        self.add_item(self._create_previous_button())
        self.add_item(self._create_page_indicator())
        self.add_item(self._create_next_button())
            
        # Add any custom buttons on a new row
        if custom_buttons:
            for button in custom_buttons:
                button.row = 1  # Move custom buttons to second row
                self.add_item(button)

    def _create_previous_button(self) -> discord.ui.Button:
        """Create previous button with consistent styling"""
        button = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            disabled=not self.loop and self.current_page == 0
        )
        button.callback = self._previous_callback
        return button

    def _create_next_button(self) -> discord.ui.Button:
        """Create next button with consistent styling"""
        button = discord.ui.Button(
            label="Next ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=not self.loop and self.current_page == self.max_pages - 1
        )
        button.callback = self._next_callback
        return button

    def _create_page_indicator(self) -> discord.ui.Button:
        """Create page indicator button"""
        button = discord.ui.Button(
            label=f"Page {self.current_page + 1}/{self.max_pages}",
            style=discord.ButtonStyle.primary,
            disabled=True
        )
        button.callback = self._indicator_callback
        return button

    async def _previous_callback(self, interaction: Interaction) -> None:
        """Handle previous button click"""
        if self.loop:
            self.current_page = self.max_pages - 1 if self.current_page <= 0 else self.current_page - 1
        else:
            if self.current_page > 0:
                self.current_page -= 1
        await self.update_page(interaction)

    async def _next_callback(self, interaction: Interaction) -> None:
        """Handle next button click"""
        if self.loop:
            self.current_page = 0 if self.current_page >= self.max_pages - 1 else self.current_page + 1
        else:
            if self.current_page < self.max_pages - 1:
                self.current_page += 1
        await self.update_page(interaction)

    async def _indicator_callback(self, interaction: Interaction) -> None:
        """Handle page indicator click (does nothing)"""
        await interaction.response.defer()

    def stop(self) -> None:
        self.message = None
        super().stop()

    async def interaction_check(self, interaction: Interaction) -> bool:
        if not self.author_id:
            return True

        if self.author_id != interaction.user.id:
            await interaction.response.send_message("You cannot interact with this menu.", ephemeral=True)
            return False

        return True

    def get_page(self, page_number: int) -> Union[PageT_co, Sequence[PageT_co]]:
        if page_number < 0 or page_number >= self.max_pages:
            self.current_page = 0
            return self.pages[self.current_page]

        if self.per_page == 1:
            return self.pages[page_number]
        else:
            base = page_number * self.per_page
            return self.pages[base : base + self.per_page]

    def format_page(self, page: Union[PageT_co, Sequence[PageT_co]]) -> Union[PageT_co, Sequence[PageT_co]]:
        return page

    async def get_page_kwargs(
        self, page: Union[PageT_co, Sequence[PageT_co]], skip_formatting: bool = False
    ) -> Dict[str, Any]:
        formatted_page: Union[PageT_co, Sequence[PageT_co]]
        if not skip_formatting:
            self._page_kwargs = {"content": None, "embeds": [], "files": [], "view": self}
            formatted_page = await discord.utils.maybe_coroutine(self.format_page, page)
        else:
            formatted_page = page

        if isinstance(formatted_page, str):
            content = self._page_kwargs["content"]
            if content is None:
                self._page_kwargs["content"] = formatted_page
            else:
                self._page_kwargs["content"] = f"{content}\n{formatted_page}"
        elif isinstance(formatted_page, discord.Embed):
            self._page_kwargs["embeds"].append(formatted_page)
        elif isinstance(formatted_page, (discord.File, discord.Attachment)):
            if isinstance(formatted_page, discord.Attachment):
                formatted_page = await formatted_page.to_file()

            self._page_kwargs["files"].append(formatted_page)
        elif isinstance(formatted_page, (tuple, list)):
            for item in formatted_page:
                await self.get_page_kwargs(item, skip_formatting=True)
        elif isinstance(formatted_page, dict):
            return formatted_page
        else:
            raise TypeError("Page content must be one of str, discord.Embed, list[discord.Embed], or dict")

        return self._page_kwargs

    def update_buttons(self) -> None:
        """Update button states and page indicator"""
        if self.max_pages <= 1:
            return
            
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                if "Previous" in item.label:
                    item.disabled = not self.loop and self.current_page == 0
                elif "Next" in item.label:
                    item.disabled = not self.loop and self.current_page == self.max_pages - 1
                elif item.disabled and "Page" in item.label:
                    # Update page indicator
                    item.label = f"Page {self.current_page + 1}/{self.max_pages}"

    async def update_page(self, interaction: Interaction) -> None:
        if self.message is None:
            self.message = interaction.message

        self.update_buttons()
        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        self.reset_files(kwargs)
        kwargs["attachments"] = kwargs.pop("files", [])
        await interaction.response.edit_message(**kwargs)

    def reset_files(self, page_kwargs: dict[str, Any]) -> None:
        files: List[discord.File] = page_kwargs.get("files", [])
        if not files:
            return

        for file in files:
            file.reset()

    async def start(
        self, obj: Union[Interaction, Messageable], **send_kwargs: Any
    ) -> Optional[Union[discord.Message, discord.WebhookMessage]]:
        self.update_buttons()
        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        if self.max_pages < 2:
            self.stop()
            del kwargs["view"]

        self.reset_files(kwargs)
        if isinstance(obj, discord.Interaction):
            if obj.response.is_done():
                self.message = await obj.followup.send(**kwargs, **send_kwargs)
            else:
                await obj.response.send_message(**kwargs, **send_kwargs)
                self.message = await obj.original_response()

        elif isinstance(obj, Messageable):
            self.message = await obj.send(**kwargs, **send_kwargs)
        else:
            raise TypeError(f"Expected Interaction or Messageable, got {obj.__class__.__name__}")

        return self.message

    @classmethod
    def create_welcome_paginator(
        cls,
        pages: Sequence[PageT_co],
        *,
        author_id: Optional[int] = None,
        timeout: Optional[float] = 300.0,
        start_button_callback: Optional[callable] = None,
    ) -> "ButtonPaginator":
        """Factory method for welcome-style paginator with start button"""
        
        # Create start button
        start_button = discord.ui.Button(
            label="🎮 Start Playing!",
            style=discord.ButtonStyle.green
        )
        
        if start_button_callback:
            start_button.callback = start_button_callback
        else:
            async def default_start_callback(interaction: Interaction):
                from bot.cogs.economy.embeds import EmbedBuilder
                embed = EmbedBuilder.success_embed(
                    interaction.user,
                    "Ready to Start!",
                    "Welcome to the economy system! Use `/economy daily` to claim your first reward!"
                )
                await interaction.response.edit_message(embed=embed, view=None)
            start_button.callback = default_start_callback
        
        return cls(
            pages,
            author_id=author_id,
            timeout=timeout,
            loop=False,
            custom_buttons=[start_button]
        )

    @classmethod
    def create_standard_paginator(
        cls,
        pages: Sequence[PageT_co],
        *,
        author_id: Optional[int] = None,
        timeout: Optional[float] = 180.0,
        per_page: int = 1,
        loop: bool = False,
    ) -> "ButtonPaginator":
        """Factory method for standard paginator without custom buttons"""
        return cls(
            pages,
            author_id=author_id,
            timeout=timeout,
            per_page=per_page,
            loop=loop
        )
