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
from cogs.utils.components import BRAND_COLOR, create_v2_container

if TYPE_CHECKING:
    from typing_extensions import Self

    Interaction = discord.Interaction[Any]
    Context = commands.Context[Any]

Page = Union[
    discord.ui.Container,
    Sequence[discord.ui.Container],
    str,
    Sequence[str],
    discord.File,
    Sequence[discord.File],
    discord.Attachment,
    Sequence[discord.Attachment],
    dict[str, Any],
]

PageT_co = TypeVar("PageT_co", bound=Page, covariant=True)


class ButtonPaginator(Generic[PageT_co], discord.ui.LayoutView):
    """Universal Components V2 paginator using discord.ui.LayoutView and discord.ui.Container."""

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
        self.custom_buttons: Optional[List[discord.ui.Button]] = custom_buttons

        total_pages, left_over = divmod(len(self.pages), self.per_page)
        if left_over:
            total_pages += 1

        self.max_pages: int = max(1, total_pages)
        self._files: List[discord.File] = []

    def _create_previous_button(self) -> discord.ui.Button:
        """Create previous button with consistent styling"""
        button = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            disabled=not self.loop and self.current_page == 0,
        )
        button.callback = self._previous_callback
        return button

    def _create_next_button(self) -> discord.ui.Button:
        """Create next button with consistent styling"""
        button = discord.ui.Button(
            label="Next ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=not self.loop and self.current_page >= self.max_pages - 1,
        )
        button.callback = self._next_callback
        return button

    def _create_page_indicator(self) -> discord.ui.Button:
        """Create page indicator button"""
        button = discord.ui.Button(
            label=f"Page {self.current_page + 1}/{self.max_pages}",
            style=discord.ButtonStyle.primary,
            disabled=True,
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
            await interaction.response.send_message(
                view=create_v2_container_view("You cannot interact with this menu."),
                ephemeral=True,
            )
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

    def _clone_container(self, container: discord.ui.Container) -> discord.ui.Container:
        """Clones a discord.ui.Container so navigation buttons can be added cleanly per render."""
        cloned = discord.ui.Container(
            accent_colour=container.accent_colour,
            spoiler=container.spoiler,
        )
        for child in container.children:
            cloned.add_item(child.copy())
        return cloned

    async def _extract_containers_and_files(
        self, page_item: Any, containers: List[discord.ui.Container], files: List[discord.File]
    ) -> None:
        if isinstance(page_item, discord.ui.Container):
            containers.append(self._clone_container(page_item))
        elif isinstance(page_item, str):
            containers.append(
                discord.ui.Container(
                    discord.ui.TextDisplay(page_item),
                    accent_colour=BRAND_COLOR,
                )
            )
        elif isinstance(page_item, (discord.File, discord.Attachment)):
            if isinstance(page_item, discord.Attachment):
                page_item = await page_item.to_file()
            files.append(page_item)
        elif isinstance(page_item, (tuple, list)):
            for sub in page_item:
                await self._extract_containers_and_files(sub, containers, files)
        else:
            raise TypeError(
                f"Page content must be a discord.ui.Container, str, or sequence of Containers, got {type(page_item)!r}"
            )

    async def get_page_kwargs(
        self, page: Union[PageT_co, Sequence[PageT_co]], skip_formatting: bool = False
    ) -> Dict[str, Any]:
        if not skip_formatting:
            formatted_page = await discord.utils.maybe_coroutine(self.format_page, page)
        else:
            formatted_page = page

        if isinstance(formatted_page, dict):
            return formatted_page

        self.clear_items()
        containers: List[discord.ui.Container] = []
        files: List[discord.File] = []
        await self._extract_containers_and_files(formatted_page, containers, files)

        if not containers:
            containers.append(
                discord.ui.Container(
                    discord.ui.TextDisplay("No content to display."),
                    accent_colour=BRAND_COLOR,
                )
            )

        # Attach pagination controls inside the last container on the page
        target_container = containers[-1]
        if self.max_pages > 1 or self.custom_buttons:
            target_container.add_item(discord.ui.Separator())

        if self.max_pages > 1:
            nav_row = discord.ui.ActionRow(
                self._create_previous_button(),
                self._create_page_indicator(),
                self._create_next_button(),
            )
            target_container.add_item(nav_row)

        if self.custom_buttons:
            custom_row = discord.ui.ActionRow(*self.custom_buttons[:5])
            target_container.add_item(custom_row)

        for c in containers:
            self.add_item(c)

        kwargs: Dict[str, Any] = {"view": self}
        if files:
            kwargs["files"] = files
        return kwargs

    async def update_page(self, interaction: Interaction) -> None:
        if self.message is None:
            self.message = interaction.message

        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        self.reset_files(kwargs)
        if "files" in kwargs:
            kwargs["attachments"] = kwargs.pop("files")
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
        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        if self.max_pages < 2 and not self.custom_buttons:
            self.stop()

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
    def create_standard_paginator(
        cls,
        pages: Sequence[PageT_co],
        *,
        author_id: Optional[int] = None,
        timeout: Optional[float] = 180.0,
        per_page: int = 1,
        loop: bool = False,
    ) -> "ButtonPaginator":
        """Factory method for standard V2 Container paginator"""
        return cls(
            pages,
            author_id=author_id,
            timeout=timeout,
            per_page=per_page,
            loop=loop,
        )


def create_v2_container_view(message: str) -> discord.ui.LayoutView:
    v = discord.ui.LayoutView()
    v.add_item(create_v2_container("❌ Notice", message))
    v.stop()
    return v
