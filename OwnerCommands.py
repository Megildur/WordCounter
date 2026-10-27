from __future__ import annotations
import asyncio
import io
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Literal, Optional, Tuple
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils import blacklist, stored_data
from cogs.utils.components import (
    BRAND_COLOR, ERROR_COLOR, NEUTRAL_COLOR, SUCCESS_COLOR, WARNING_COLOR, AuthorOnlyView,
    create_v2_container, create_v2_view, error_view, make_button,
)
from cogs.utils.config import ALLOWED_GUILD_IDS
from paginator import ButtonPaginator

log = logging.getLogger(__name__)

ALLOWED_GUILDS = [discord.Object(id=guild_id) for guild_id in ALLOWED_GUILD_IDS]
Kind = Literal["User", "Server"]
NO_PINGS = discord.AllowedMentions.none()
LISTED_TABLES = 40
BLACKLIST_PAGE = 10
BAD_ID = (
    "That isn't a Discord ID. Turn on Developer Mode in Discord's settings, then right-click "
    "the user or server and pick Copy ID."
)


def snowflake(raw: str) -> Optional[int]:
    digits = raw.strip().strip("<@!#&>")
    if not digits.isdigit() or not 15 <= len(digits) <= 20:
        return None
    value = int(digits)
    return value if value < 2 ** 64 else None


def plural(count: int, word: str) -> str:
    return f"{count:,} {word}{'' if count == 1 else 's'}"


@dataclass(frozen=True)
class Target:
    id: int
    kind: str
    name: Optional[str]

    @property
    def what(self) -> str:
        return "server" if self.kind == blacklist.SERVER else "user"

    @property
    def label(self) -> str:
        if self.name:
            return f"{self.what} **{discord.utils.escape_markdown(self.name)}** (`{self.id}`)"
        return f"{self.what} `{self.id}`"

    @property
    def plain(self) -> str:
        return f"{self.what} {self.name} ({self.id})" if self.name else f"{self.what} {self.id}"


def found_lines(found: stored_data.Found) -> str:
    lines = [f"- `{table.name}`: {len(table.rows):,}" for table in found.tables]
    if len(lines) > LISTED_TABLES:
        lines = lines[:LISTED_TABLES] + [f"- and {plural(len(lines) - LISTED_TABLES, 'more table')}"]
    return "\n".join(lines)


def deleted_text(removed: Dict[str, int]) -> str:
    rows = sum(removed.values())
    if not rows:
        return "There were no rows left to delete."
    return f"Deleted {plural(rows, 'row')} from {plural(len(removed), 'table')}."


class DataConfirmView(AuthorOnlyView):
    def __init__(
        self, bot: commands.Bot, author_id: int, target: Target, found: stored_data.Found,
        *, ban: bool = False, reason: Optional[str] = None,
    ) -> None:
        super().__init__(author_id=author_id, timeout=600)
        self.bot, self.target, self.found, self.ban, self.reason = bot, target, found, ban, reason
        self.interaction: Optional[discord.Interaction] = None
        who = f"{target.label[0].upper()}{target.label[1:]}"
        if ban:
            title = f"Blacklist this {target.what}?"
            if target.kind == blacklist.SERVER:
                does = (
                    "WordCounter stays in the server but ignores everything that happens there, and answers its "
                    "commands and buttons with a notice that the server is banned."
                )
            else:
                does = (
                    "WordCounter stops counting their messages in every server, ignores everything they do, and "
                    "answers their commands and buttons with a notice that they're banned."
                )
            description = f"{who}.\n{does}"
            if reason:
                description += f"\n**Reason:** {discord.utils.escape_markdown(reason)}"
            if found.empty:
                description += "\n**Their data:** nothing stored."
                confirm = "Blacklist"
            else:
                description += f"\n**Their data:** {stored_data.summary(found)}, deleted with the ban."
                confirm = "Blacklist and delete data"
        else:
            title = f"Delete everything stored about this {target.what}?"
            description = f"WordCounter stores {stored_data.summary(found)} for {target.label}."
            confirm = "Delete everything"
        if not found.empty:
            description += "\n" + found_lines(found)
        footer = "This can't be undone." + (" Lifting the ban later doesn't bring the data back." if ban else "")
        self.confirm_button = make_button(confirm, discord.ButtonStyle.danger, self._confirm)
        self.cancel_button = make_button("Cancel", callback=self._cancel)
        self.add_item(create_v2_container(
            title, description, footer=footer, color=ERROR_COLOR,
            action_rows=[discord.ui.ActionRow(self.confirm_button, self.cancel_button)],
        ))

    async def _confirm(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self.stop()
        if self.ban:
            await self.bot.db.add_to_blacklist(self.target.id, self.target.kind, self.reason, interaction.user.id)
            self.bot.blacklisted.add(self.target.id)
        removed = await self.bot.db.delete_by_id(self.target.id)
        jar = self.bot.get_cog("SwearJar")
        if jar is not None:
            jar.forget(self.target.id)
        log.info("%s %s: deleted %s", "Blacklisted" if self.ban else "Deleted the data of", self.target.plain, removed)
        if self.ban:
            who = f"{self.target.label[0].upper()}{self.target.label[1:]}"
            view = create_v2_view("Blacklisted", f"{who} can't use WordCounter any more.\n{deleted_text(removed)}", color=SUCCESS_COLOR)
        else:
            view = create_v2_view("Data deleted", f"For {self.target.label}: {deleted_text(removed)}", color=SUCCESS_COLOR)
        await interaction.edit_original_response(view=view)

    async def _cancel(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(view=create_v2_view("Cancelled", "Nothing was changed.", color=NEUTRAL_COLOR))

    async def on_timeout(self) -> None:
        if self.interaction is None:
            return
        self.confirm_button.disabled = self.cancel_button.disabled = True
        try:
            await self.interaction.edit_original_response(view=self)
        except discord.HTTPException as error:
            log.debug("Could not disable a timed-out confirm panel: %s", error)


def code_list(names: List[str], limit: int = 1500) -> str:
    text = ", ".join(f"`{name}`" for name in names)
    return text if len(text) <= limit else text[:limit].rsplit(",", 1)[0] + ", …"


@app_commands.guilds(*ALLOWED_GUILDS)
@app_commands.default_permissions(administrator=True)
class OwnerCog(commands.GroupCog, group_name="owner"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def all_extensions(self) -> List[str]:
        found = {f"cogs.{name[:-3]}" for name in os.listdir("cogs") if name.endswith(".py")}
        return sorted(found | set(self.bot.extensions))

    @app_commands.command(name="sync", description="Sync slash commands")
    async def sync(self, interaction: discord.Interaction, sync_type: Literal["Global", "Guild"]) -> None:
        await interaction.response.defer()
        await interaction.followup.send(
            view=create_v2_view(f"Syncing commands ({sync_type})", "This can take a few seconds.", color=WARNING_COLOR)
        )
        try:
            if sync_type == "Global":
                synced = await self.bot.tree.sync()
                target = "globally"
            else:
                synced = []
                for guild in ALLOWED_GUILDS:
                    synced = await self.bot.tree.sync(guild=guild)
                target = f"to {len(ALLOWED_GUILDS)} allowed server(s)"
        except discord.HTTPException as error:
            log.warning("Command sync failed: %s", error)
            await interaction.edit_original_response(
                view=create_v2_view("Sync failed", f"```{str(error)[:1000]}```", color=ERROR_COLOR)
            )
            return

        log.info("Synced %s commands %s: %s", len(synced), target, ", ".join(c.name for c in synced))
        await interaction.edit_original_response(
            view=create_v2_view(
                "Commands synced",
                f"Synced **{len(synced)}** command(s) {target}.\n\n{code_list([c.name for c in synced]) or 'No commands found.'}",
                color=SUCCESS_COLOR,
            )
        )

    @app_commands.command(name="sync_clear", description="Remove every command from the tree")
    async def sync_clear(self, interaction: discord.Interaction, clear_type: Literal["Global", "Guild"]) -> None:
        await interaction.response.defer()
        if clear_type == "Global":
            removed = len(self.bot.tree.get_commands())
            self.bot.tree.clear_commands(guild=None)
            target = "globally"
        else:
            removed = len(self.bot.tree.get_commands(guild=interaction.guild))
            self.bot.tree.clear_commands(guild=interaction.guild)
            target = f"from **{interaction.guild.name}**"
        log.info("Cleared %s commands %s", removed, target.replace("**", ""))
        await interaction.followup.send(
            view=create_v2_view(
                "Commands cleared",
                f"Removed **{removed}** command(s) {target}. Nobody will see them until you sync again.",
                footer="Run /owner sync to add them back",
                color=SUCCESS_COLOR,
            )
        )

    @app_commands.command(name="ext", description="Load, unload or reload an extension")
    async def ext(self, interaction: discord.Interaction, action: Literal["load", "unload", "reload"], extension: str) -> None:
        await interaction.response.defer()
        handlers = {
            "load": self.bot.load_extension,
            "unload": self.bot.unload_extension,
            "reload": self.bot.reload_extension,
        }
        try:
            await handlers[action](extension)
        except commands.ExtensionAlreadyLoaded:
            problem = "It's already loaded."
        except commands.ExtensionNotLoaded:
            problem = "It isn't loaded."
        except commands.ExtensionNotFound:
            problem = "No extension with that name exists."
        except commands.ExtensionFailed as error:
            problem = f"It failed during setup.\n```{error}```"
        else:
            problem = ""

        if problem:
            view = create_v2_view(f"Couldn't {action} `{extension}`", problem, color=ERROR_COLOR)
        else:
            view = create_v2_view(f"{action.capitalize()}ed `{extension}`", "Done.", color=WARNING_COLOR if action == "unload" else SUCCESS_COLOR)
        await interaction.followup.send(view=view)

    @ext.autocomplete("extension")
    async def ext_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name=name, value=name)
            for name in self.all_extensions()
            if current.lower() in name.lower()
        ][:25]

    @app_commands.command(name="cogs", description="Reload every loaded extension and load any new cogs")
    async def cogs(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        results = {"Reloaded": [], "Loaded": [], "Failed": []}
        extensions = self.all_extensions()
        for name in extensions:
            try:
                if name in self.bot.extensions:
                    await self.bot.reload_extension(name)
                    results["Reloaded"].append(name)
                else:
                    await self.bot.load_extension(name)
                    results["Loaded"].append(name)
            except commands.ExtensionError as error:
                log.warning("Extension %s failed: %s", name, error)
                results["Failed"].append(name)

        body = "\n\n".join(f"**{label}**\n{code_list(names)}" for label, names in results.items() if names)
        await interaction.followup.send(
            view=create_v2_view(
                "Extensions",
                body or "Nothing to do.",
                footer=f"Processed {len(extensions)} extension(s)",
                color=ERROR_COLOR if results["Failed"] else SUCCESS_COLOR,
            )
        )


    async def _owner_only(self, interaction: discord.Interaction) -> bool:
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.response.send_message(view=error_view("Only the bot owner can use this.", title="Owner only"), ephemeral=True)
        return False

    async def _user_name(self, user_id: int) -> Optional[str]:
        user = self.bot.get_user(user_id)
        if user is not None:
            return user.name
        try:
            return (await self.bot.fetch_user(user_id)).name
        except discord.NotFound:
            return None
        except discord.HTTPException:
            return ""

    async def _target(self, kind: str, raw: str) -> Tuple[Optional[Target], str]:
        target_id = snowflake(raw)
        if target_id is None:
            return None, BAD_ID
        if self.bot.user is not None and target_id == self.bot.user.id:
            return None, "That's WordCounter's own ID."
        if kind == "Server":
            guild = self.bot.get_guild(target_id)
            if guild is None and await self._user_name(target_id):
                return None, "That ID is a user's. Pick **User** instead."
            return Target(target_id, blacklist.SERVER, guild.name if guild else None), ""
        name = await self._user_name(target_id)
        if name is None:
            return None, "No Discord user has that ID. If it's a server's, pick **Server** instead."
        return Target(target_id, blacklist.USER, name or None), ""

    @app_commands.command(name="delete_data", description="Delete everything WordCounter stores about a user or server")
    @app_commands.describe(kind="Whether the ID is a user's or a server's", target_id="The user's or server's ID")
    @app_commands.rename(target_id="id")
    async def delete_data(self, interaction: discord.Interaction, kind: Kind, target_id: str) -> None:
        if not await self._owner_only(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        target, problem = await self._target(kind, target_id)
        if target is None:
            await interaction.followup.send(view=error_view(problem, title="Can't use that ID"), ephemeral=True)
            return
        found = await self.bot.db.find_by_id(target.id)
        if found.empty:
            view = create_v2_view("Nothing stored", f"WordCounter has nothing stored about {target.label}.", color=NEUTRAL_COLOR)
            await interaction.followup.send(view=view, ephemeral=True)
            return
        view = DataConfirmView(self.bot, interaction.user.id, target, found)
        await interaction.followup.send(view=view, ephemeral=True)
        view.interaction = interaction

    @app_commands.command(name="export_data", description="Post a text file of everything WordCounter stores about a user or server")
    @app_commands.describe(kind="Whether the ID is a user's or a server's", target_id="The user's or server's ID")
    @app_commands.rename(target_id="id")
    async def export_data(self, interaction: discord.Interaction, kind: Kind, target_id: str) -> None:
        if not await self._owner_only(interaction):
            return
        if not interaction.app_permissions.attach_files:
            await interaction.response.send_message(
                view=error_view("WordCounter needs **Attach Files** in this channel to post the export.", title="Missing permission"),
                ephemeral=True,
            )
            return
        target, problem = await self._target(kind, target_id)
        if target is None:
            await interaction.response.send_message(view=error_view(problem, title="Can't use that ID"), ephemeral=True)
            return
        await interaction.response.defer()
        found = await self.bot.db.find_by_id(target.id)
        if found.empty:
            view = create_v2_view("Nothing stored", f"WordCounter has nothing stored about {target.label}.", color=NEUTRAL_COLOR)
            await interaction.followup.send(view=view, allowed_mentions=NO_PINGS)
            return
        heading = f"Everything WordCounter stores about {target.plain}"
        stem = f"wordcounter-{target.what}-{target.id}"
        packed = await asyncio.to_thread(lambda: stored_data.pack(stored_data.report(found, heading), stem))
        if packed is None:
            view = create_v2_view("Too big to upload", "Even zipped, the export is larger than Discord lets WordCounter upload.", color=ERROR_COLOR)
            await interaction.followup.send(view=view, allowed_mentions=NO_PINGS)
            return
        filename, data = packed
        summary = stored_data.summary(found)
        note = "Every row with its columns, table by table." + (" Zipped to fit Discord's upload limit." if filename.endswith(".zip") else "")
        view = discord.ui.LayoutView()
        view.add_item(discord.ui.Container(
            discord.ui.TextDisplay(f"### Stored data\n{summary[:1].upper()}{summary[1:]} for {target.label}."),
            discord.ui.File(f"attachment://{filename}"),
            discord.ui.TextDisplay(f"-# {note}"),
            accent_colour=BRAND_COLOR,
        ))
        await interaction.followup.send(view=view, file=discord.File(io.BytesIO(data), filename=filename), allowed_mentions=NO_PINGS)
        log.info("Exported the data of %s: %s", target.plain, summary)

    @app_commands.command(name="blacklist", description="Ban a user or server from using WordCounter, lift a ban, or list bans")
    @app_commands.describe(
        action="Add a ban (deletes their data), remove one, or list them",
        kind="For Add: whether the ID is a user's or a server's",
        target_id="For Add and Remove: the user's or server's ID",
        reason="For Add: kept with the ban, for you",
    )
    @app_commands.rename(target_id="id")
    async def blacklist(
        self,
        interaction: discord.Interaction,
        action: Literal["Add", "Remove", "List"],
        kind: Optional[Kind] = None,
        target_id: Optional[str] = None,
        reason: Optional[app_commands.Range[str, 1, 500]] = None,
    ) -> None:
        if not await self._owner_only(interaction):
            return
        if action == "List":
            await self._blacklist_list(interaction)
            return
        if not target_id:
            await interaction.response.send_message(
                view=error_view(f"To {action.lower()} a ban, give the user's or server's ID in **id**.", title="Add the ID"), ephemeral=True,
            )
            return
        if action == "Remove":
            await self._blacklist_remove(interaction, target_id)
            return
        if kind is None:
            await interaction.response.send_message(
                view=error_view("Pick in **kind** whether the ID is a user's or a server's.", title="User or server?"), ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        target, problem = await self._target(kind, target_id)
        if target is not None:
            problem = await self._protected(target)
        if problem:
            await interaction.followup.send(view=error_view(problem, title="Can't blacklist that"), ephemeral=True)
            return
        entry = await self.bot.db.blacklist_entry(target.id)
        if entry is not None:
            who = f"{target.label[0].upper()}{target.label[1:]}"
            view = create_v2_view("Already blacklisted", f"{who} has been blacklisted since <t:{int(entry.added_at)}:D>.", color=NEUTRAL_COLOR)
            await interaction.followup.send(view=view, ephemeral=True)
            return
        found = await self.bot.db.find_by_id(target.id)
        view = DataConfirmView(self.bot, interaction.user.id, target, found, ban=True, reason=reason)
        await interaction.followup.send(view=view, ephemeral=True)
        view.interaction = interaction

    async def _protected(self, target: Target) -> str:
        if target.kind == blacklist.USER and await self.bot.is_owner(discord.Object(id=target.id)):
            return "That's an owner of WordCounter."
        if target.kind == blacklist.SERVER and target.id in ALLOWED_GUILD_IDS:
            return "That server is in `ALLOWED_GUILDS`, where the owner commands live."
        return ""

    async def _blacklist_remove(self, interaction: discord.Interaction, raw: str) -> None:
        target_id = snowflake(raw)
        if target_id is None:
            await interaction.response.send_message(view=error_view(BAD_ID, title="Can't use that ID"), ephemeral=True)
            return
        entry = await self.bot.db.blacklist_entry(target_id)
        if entry is None:
            await interaction.response.send_message(
                view=create_v2_view("Not blacklisted", f"`{target_id}` isn't on the blacklist.", color=NEUTRAL_COLOR), ephemeral=True,
            )
            return
        await self.bot.db.remove_from_blacklist(target_id)
        self.bot.blacklisted.discard(target_id)
        log.info("Lifted the blacklist on %s %s", entry.kind, target_id)
        view = create_v2_view(
            "Ban lifted",
            f"The {entry.kind} `{target_id}` can use WordCounter again. The data deleted with the ban doesn't come back.",
            color=SUCCESS_COLOR,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    async def _blacklist_list(self, interaction: discord.Interaction) -> None:
        entries = await self.bot.db.blacklist_entries()
        if not entries:
            await interaction.response.send_message(
                view=create_v2_view("Nobody is blacklisted", "Every user and server can use WordCounter.", color=NEUTRAL_COLOR), ephemeral=True,
            )
            return
        lines = []
        for entry in entries:
            found = self.bot.get_guild(entry.target_id) if entry.kind == blacklist.SERVER else self.bot.get_user(entry.target_id)
            line = f"**{entry.kind.capitalize()}** `{entry.target_id}`"
            if getattr(found, "name", None):
                line += f" {discord.utils.escape_markdown(found.name)}"
            line += f" · since <t:{int(entry.added_at)}:D>"
            if entry.reason:
                line += f"\n-# {discord.utils.escape_markdown(entry.reason)}"
            lines.append(line)
        count = "1 user or server can't" if len(entries) == 1 else f"{len(entries):,} users and servers can't"
        pages = [
            create_v2_container("Blacklist", f"{count} use WordCounter.\n\n" + "\n".join(lines[start:start + BLACKLIST_PAGE]), color=BRAND_COLOR)
            for start in range(0, len(lines), BLACKLIST_PAGE)
        ]
        await ButtonPaginator(pages, author_id=interaction.user.id).start(interaction, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OwnerCog(bot))
