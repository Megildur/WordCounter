"""The shared paged-menu helper, `ButtonPaginator`.

Shows pages as Components v2 containers under a Previous, page counter and Next row, and only lets the person who opened it press the buttons. Cogs subclass it and override `format_page`, `extra_rows` or `get_page_kwargs`; /help, /stats user, /leaderboard, /keyword list and the owner blacklist list all use it.
"""

from __future__ import annotations
from typing import (
    Any,
    Dict,
    Generic,
    List,
    Optional,
    TYPE_CHECKING,
    Sequence,
    TypeVar,
    Union,
)

import discord
from discord.abc import Messageable
from discord.ext import commands
from cogs.utils.components import BRAND_COLOR, AuthorOnlyView, make_button

if TYPE_CHECKING:
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


class ButtonPaginator(Generic[PageT_co], AuthorOnlyView):
    """A message that pages through `pages`, with `per_page` of them shown at a time.

    A page can be a container, a string, a file or attachment, or a list of those. `format_page` turns a page into what is shown, and `extra_rows` adds rows such as a select or link buttons under the navigation row. Build it, then call `start` with the interaction or channel to send to.
    """
    message: Optional[Union[discord.Message, discord.WebhookMessage]] = None
    previous_label = "Previous"
    next_label = "Next"

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
        super().__init__(
            author_id=author_id,
            denied_message="Only the person who opened this can use these buttons.",
            timeout=timeout,
        )
        self.current_page: int = 0
        self.per_page: int = per_page
        self.loop: bool = loop
        self.custom_buttons: Optional[List[discord.ui.Button]] = custom_buttons
        self.set_pages(pages)

    def set_pages(self, pages: Sequence[PageT_co]) -> None:
        self.pages: Any = pages
        total_pages, left_over = divmod(len(pages), self.per_page)
        # There is always at least one page, so an empty list still shows a "Nothing to show" message.
        self.max_pages: int = max(1, total_pages + bool(left_over))
        self.current_page = min(self.current_page, self.max_pages - 1)

    def extra_rows(self) -> List[discord.ui.ActionRow]:
        if not self.custom_buttons:
            return []
        return [discord.ui.ActionRow(*self.custom_buttons[:5])]

    def _navigation_row(self) -> discord.ui.ActionRow:
        at_start = not self.loop and self.current_page == 0
        at_end = not self.loop and self.current_page >= self.max_pages - 1
        return discord.ui.ActionRow(
            make_button(self.previous_label, callback=self._previous_callback, disabled=at_start),
            make_button(f"{self.current_page + 1}/{self.max_pages}", callback=self._indicator_callback, disabled=True),
            make_button(self.next_label, callback=self._next_callback, disabled=at_end),
        )

    async def _previous_callback(self, interaction: Interaction) -> None:
        if self.loop:
            self.current_page = self.max_pages - 1 if self.current_page <= 0 else self.current_page - 1
        else:
            self.current_page = max(0, self.current_page - 1)
        await self.update_page(interaction)

    async def _next_callback(self, interaction: Interaction) -> None:
        if self.loop:
            self.current_page = 0 if self.current_page >= self.max_pages - 1 else self.current_page + 1
        else:
            self.current_page = min(self.max_pages - 1, self.current_page + 1)
        await self.update_page(interaction)

    async def _indicator_callback(self, interaction: Interaction) -> None:
        await interaction.response.defer()

    def stop(self) -> None:
        self.message = None
        super().stop()

    def get_page(self, page_number: int) -> Union[PageT_co, Sequence[PageT_co]]:
        if page_number < 0 or page_number >= self.max_pages:
            self.current_page = 0
            page_number = 0
        if not self.pages:
            return []
        if self.per_page == 1:
            return self.pages[page_number]
        base = page_number * self.per_page
        return self.pages[base : base + self.per_page]

    def format_page(self, page: Union[PageT_co, Sequence[PageT_co]]) -> Union[PageT_co, Sequence[PageT_co]]:
        return page

    def _clone_container(self, container: discord.ui.Container) -> discord.ui.Container:
        """Copies a page, because the navigation row is added to the copy and the stored page must stay clean to be shown again."""
        cloned = discord.ui.Container(accent_colour=container.accent_colour, spoiler=container.spoiler)
        for child in container.children:
            cloned.add_item(child.copy())
        return cloned

    async def _extract_containers_and_files(
        self, page_item: Any, containers: List[discord.ui.Container], files: List[discord.File]
    ) -> None:
        if isinstance(page_item, discord.ui.Container):
            containers.append(self._clone_container(page_item))
        elif isinstance(page_item, str):
            containers.append(discord.ui.Container(discord.ui.TextDisplay(page_item), accent_colour=BRAND_COLOR))
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
        """Builds the keyword arguments to send or edit with: this view, rebuilt around the page, plus any files.

        The navigation row and `extra_rows` go at the bottom of the last container. A page that formats to a dict is used as the arguments as they are.
        """
        formatted_page = page if skip_formatting else await discord.utils.maybe_coroutine(self.format_page, page)
        if isinstance(formatted_page, dict):
            return formatted_page

        self.clear_items()
        containers: List[discord.ui.Container] = []
        files: List[discord.File] = []
        await self._extract_containers_and_files(formatted_page, containers, files)
        if not containers:
            containers.append(
                discord.ui.Container(discord.ui.TextDisplay("Nothing to show."), accent_colour=BRAND_COLOR)
            )

        rows = [self._navigation_row()] if self.max_pages > 1 else []
        rows.extend(self.extra_rows())
        target_container = containers[-1]
        if rows:
            target_container.add_item(discord.ui.Separator())
            for row in rows:
                target_container.add_item(row)

        for container in containers:
            self.add_item(container)

        kwargs: Dict[str, Any] = {"view": self}
        if files:
            kwargs["files"] = files
        return kwargs

    async def update_page(self, interaction: Interaction) -> None:
        if self.message is None:
            self.message = interaction.message

        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        self.reset_files(kwargs)
        # An edit takes new files as `attachments`, where a send takes `files`.
        if "files" in kwargs:
            kwargs["attachments"] = kwargs.pop("files")
        await interaction.response.edit_message(**kwargs)

    def reset_files(self, page_kwargs: dict[str, Any]) -> None:
        """Rewinds the files in the arguments, because a discord.File is used up once it has been sent."""
        for file in page_kwargs.get("files", []):
            file.reset()

    async def start(
        self, obj: Union[Interaction, Messageable], **send_kwargs: Any
    ) -> Optional[Union[discord.Message, discord.WebhookMessage]]:
        kwargs = await self.get_page_kwargs(self.get_page(self.current_page))
        # One page and no extra rows leaves nothing to press, so stop listening now instead of waiting for the timeout.
        if self.max_pages < 2 and not self.extra_rows():
            self.stop()

        self.reset_files(kwargs)
        if isinstance(obj, discord.Interaction):
            # A deferred or already answered interaction can only be followed up, and wait=True returns the message.
            if obj.response.is_done():
                self.message = await obj.followup.send(**kwargs, **send_kwargs, wait=True)
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
    ) -> ButtonPaginator:
        return cls(pages, author_id=author_id, timeout=timeout, per_page=per_page, loop=loop)
