"""Puts avatars, names and numbers onto the pre-drawn card art in `assets/cards/` with Pillow.

Draws the leaderboard podium, the /stats card and the swear jar card, and returns PNG bytes. The art itself is drawn by a script in the Botzilla project, and the layout constants below (PODIUM_BLOCKS, STATS_*, JAR_PANEL) are pixel positions on that art, so changing the art means updating them. Drawing is synchronous, so callers run it in a thread with `asyncio.to_thread`.
"""

from __future__ import annotations
import asyncio
import io
import logging
import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple
import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"
CARDS = ASSETS / "cards"
FONTS = ASSETS / "fonts"
# The art is small pixel art, 240 pixels wide. Positions below are in those pixels and are multiplied by SCALE for the final picture, and the podium file is already stored at that size.
SCALE = 4
INK = (8, 13, 26)
NAVY = (29, 51, 91)
WHITE = (255, 255, 255)
SOFT = (203, 213, 225)
ORANGE_LIT = (255, 176, 102)
GOLD = (253, 230, 138)
PINK = (252, 165, 165)
AVATAR_WAIT = 1.5

PODIUM_FILE = "wordcounter_podium.png"
STATS_FILE = "wordcounter_stats.png"
JAR_FILE = "wordcounter_jar.png"

# Place to (left edge, top edge, right edge, frame colour) of that block on the podium art.
PODIUM_BLOCKS = {1: (96, 58, 144, (245, 184, 46)), 2: (44, 64, 92, (201, 211, 224)), 3: (148, 70, 196, (217, 131, 58))}

# On the stats art: the avatar as (x, y, size), the name's (x, y), the first flip counter digit and the step between digits, and the three chips for messages, attachments and emojis.
STATS_AVATAR = (12, 12, 40)
STATS_NAME = (60, 12)
STATS_CARD_X, STATS_CARD_Y, STATS_CARD_STEP = 64, 49, 11
STATS_DIGITS = 8
STATS_CHIPS = ((60, 80), (120, 80), (180, 80))
JAR_LEVELS = 9
# The panel on the jar art that holds the text, as (left, top, right, bottom).
JAR_PANEL = (70, 8, 232, 92)

# Whether the font has a glyph for a character, per (bold, character). Stops growing at 5000 entries.
_glyphs: Dict[Tuple[bool, str], bool] = {}


@lru_cache(maxsize=32)
def font(bold: bool, size: int) -> ImageFont.FreeTypeFont:
    path = FONTS / ("Poppins-Bold.ttf" if bold else "Poppins-Regular.ttf")
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        log.warning("Font %s is missing; using Pillow's default font", path)
        return ImageFont.load_default(size=size)


def _glyph(face: ImageFont.FreeTypeFont, char: str) -> Tuple[Tuple[int, int], bytes]:
    left, top, right, bottom = face.getbbox(char)
    size = (max(right - left, 0), max(bottom - top, 0))
    if size[0] == 0 or size[1] == 0:
        return (0, 0), b""
    image = Image.new("L", size, 0)
    ImageDraw.Draw(image).text((-left, -top), char, font=face, fill=255)
    return size, image.tobytes()


def _has_glyph(bold: bool, char: str) -> bool:
    cached = _glyphs.get((bold, char))
    if cached is not None:
        return cached
    if char.isspace():
        result = True
    else:
        face = font(bold, 48)
        pixels = _glyph(face, char)
        # A character the font lacks is drawn as the same placeholder box that U+FFFF gets, so a glyph that matches that box counts as missing.
        result = pixels[0] != (0, 0) and pixels != _glyph(face, "￿")
    if len(_glyphs) < 5000:
        _glyphs[(bold, char)] = result
    return result


def drawable(text: str, bold: bool = True) -> str:
    """Returns the text without characters the card font can't draw, such as emoji, with runs of spaces collapsed. The result can be empty, and the callers that draw a name then use a default."""
    kept = "".join(char for char in text if _has_glyph(bold, char))
    return re.sub(r"\s+", " ", kept).strip()


def fit_font(text: str, bold: bool, start: int, minimum: int, width: int) -> Tuple[ImageFont.FreeTypeFont, str]:
    """Returns (font, text) so the text fits in `width` pixels: tries sizes from `start` down to `minimum` in steps of 4, then cuts the text and adds "..." at the smallest size."""
    size = start
    while size > minimum:
        face = font(bold, size)
        if face.getlength(text) <= width:
            return face, text
        size -= 4
    face = font(bold, minimum)
    if face.getlength(text) <= width:
        return face, text
    while text and face.getlength(text + "...") > width:
        text = text[:-1]
    return face, text.rstrip() + "..."


def square_picture(data: Optional[bytes], size: int) -> Image.Image:
    """Crops an avatar to a size by size square, using the first frame of an animated one. A missing or unreadable picture becomes a plain navy tile."""
    if data:
        try:
            with Image.open(io.BytesIO(data)) as picture:
                picture.seek(0)
                return ImageOps.fit(picture.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
        except Exception:
            log.debug("Could not read an avatar", exc_info=True)
    return Image.new("RGBA", (size, size), NAVY + (255,))


def png_bytes(card: Image.Image) -> bytes:
    out = io.BytesIO()
    card.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def scaled(name: str) -> Image.Image:
    """Loads a card from `assets/cards/` and enlarges it by SCALE without smoothing, so the pixels stay sharp."""
    with Image.open(CARDS / name) as small:
        small = small.convert("RGBA")
    return small.resize((small.width * SCALE, small.height * SCALE), Image.Resampling.NEAREST)


def picture_file(data: bytes, filename: str) -> discord.File:
    return discord.File(io.BytesIO(data), filename=filename)


def gallery(filename: str, description: str) -> discord.ui.MediaGallery:
    """A media gallery showing a picture attached to the same message, found by filename. The description is its alt text, cut to 1024 characters."""
    return discord.ui.MediaGallery(discord.MediaGalleryItem(f"attachment://{filename}", description=description[:1024]))


async def avatar(user: Optional[discord.abc.User], size: int = 128) -> Optional[bytes]:
    """Returns the bytes of a user's avatar image (a GIF for an animated one, otherwise PNG), or None when there is no user, Discord answers with an HTTP error, or the download takes longer than AVATAR_WAIT seconds. A connection error is raised. The card draws a plain tile when it gets None."""
    if user is None:
        return None
    try:
        return await asyncio.wait_for(user.display_avatar.replace(size=size, static_format="png").read(), AVATAR_WAIT)
    except (asyncio.TimeoutError, discord.HTTPException, ValueError):
        return None


def _label(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str, face: ImageFont.FreeTypeFont, fill, anchor: str) -> None:
    draw.text(xy, text, font=face, fill=fill, anchor=anchor, stroke_width=2, stroke_fill=INK)


def podium(entries: Sequence[Tuple[str, Optional[bytes], str]]) -> bytes:
    """Draws the top three onto the podium art and returns PNG bytes. `entries` holds (name, avatar bytes or None, caption) in rank order, and only the first three are used."""
    with Image.open(CARDS / "podium.png") as base:
        card = base.convert("RGBA")
    draw = ImageDraw.Draw(card)
    size = 28
    for place, (name, face, caption) in enumerate(entries[:3], start=1):
        left, top, right, color = PODIUM_BLOCKS[place]
        middle = (left + right) // 2
        x, y = (middle - size // 2) * SCALE, (top - size - 3) * SCALE
        edge = 2 * SCALE
        frame = Image.new("RGBA", (size * SCALE + 2 * edge, size * SCALE + 2 * edge), color)
        frame_draw = ImageDraw.Draw(frame)
        frame_draw.rectangle([0, 0, frame.width - 1, frame.height - 1], outline=INK, width=SCALE)
        light = tuple(min(255, round(c + (255 - c) * 0.45)) for c in color)
        frame_draw.rectangle([SCALE, SCALE, frame.width - 1 - SCALE, SCALE * 2 - 1], fill=light)
        card.alpha_composite(frame, (x - edge, y - edge))
        card.alpha_composite(square_picture(face, size * SCALE), (x, y))
        width = (right - left + 8) * SCALE
        label_font, shown = fit_font(drawable(name) or "Member", True, 24, 14, width)
        caption_font, caption = fit_font(drawable(caption, False), False, 18, 12, width)
        label_y = top - size - 6
        _label(draw, (middle * SCALE, (label_y - 8) * SCALE), shown, label_font, WHITE, "ms")
        _label(draw, (middle * SCALE, (label_y - 1) * SCALE), caption, caption_font, SOFT, "ms")
    return png_bytes(card)


def stats_card(name: str, server: str, face: Optional[bytes], words: int, messages: int, attachments: int, emojis: int) -> bytes:
    """Draws a member's stats card and returns PNG bytes: their avatar, name and the server's name, the word count on the flip counter, and chips for messages, attachments and emojis."""
    with Image.open(CARDS / "stats.png") as base:
        small = base.convert("RGBA")
    with Image.open(CARDS / "digits.png") as sheet:
        digits = sheet.convert("RGBA")
    # The flip counter has STATS_DIGITS digits, so a bigger word count shows as all nines.
    shown = str(min(words, 10 ** STATS_DIGITS - 1)).rjust(STATS_DIGITS, "0")
    for i, digit in enumerate(shown):
        tile = digits.crop((int(digit) * 10, 0, int(digit) * 10 + 9, 13))
        small.alpha_composite(tile, (STATS_CARD_X + STATS_CARD_STEP * i, STATS_CARD_Y))
    card = small.resize((small.width * SCALE, small.height * SCALE), Image.Resampling.NEAREST)
    x, y, size = STATS_AVATAR
    card.alpha_composite(square_picture(face, size * SCALE), (x * SCALE, y * SCALE))
    draw = ImageDraw.Draw(card)
    nx, ny = STATS_NAME
    width = (232 - nx) * SCALE
    name_font, name = fit_font(drawable(name) or "Member", True, 44, 24, width)
    _label(draw, (nx * SCALE, ny * SCALE), name, name_font, WHITE, "lt")
    server_font, server = fit_font(drawable(server, False) or "this server", False, 26, 18, width)
    _label(draw, (nx * SCALE, (ny + 15) * SCALE), server, server_font, SOFT, "lt")
    for (ix, iy), value in zip(STATS_CHIPS, (messages, attachments, emojis)):
        number_font, number = fit_font(f"{value:,}", True, 36, 20, 38 * SCALE)
        _label(draw, ((ix + 21) * SCALE, (iy + 9) * SCALE), number, number_font, WHITE, "lm")
    return png_bytes(card)


def jar_level(units: int) -> int:
    """Picks which jar picture (0 to JAR_LEVELS - 1) fits an amount of units: 0 for none, then one level for each doubling, so it fills fast at first and tops out at the last level."""
    if units <= 0:
        return 0
    return max(1, min(JAR_LEVELS - 1, math.ceil(math.log2(units + 1))))


def jar_card(name: str, face: Optional[bytes], headline: str, lines: Sequence[str], debt: bool, units: int) -> bytes:
    """Draws a member's swear jar card and returns PNG bytes: the debt or saved jar picture at the level for `units`, their avatar and name, a headline and up to three detail lines."""
    card = scaled(f"jar_{'debt' if debt else 'saved'}_{jar_level(units)}.png")
    left, top, right, bottom = JAR_PANEL
    size = 24
    ax, ay = (left + 6) * SCALE, (top + 6) * SCALE
    card.alpha_composite(square_picture(face, size * SCALE), (ax, ay))
    draw = ImageDraw.Draw(card)
    draw.rectangle([ax - SCALE, ay - SCALE, ax + size * SCALE + SCALE - 1, ay + size * SCALE + SCALE - 1], outline=INK, width=SCALE)
    text_x = left + 6 + size + 6
    width = (right - 6 - text_x) * SCALE
    name_font, name = fit_font(drawable(name) or "Member", True, 36, 22, width)
    _label(draw, (text_x * SCALE, (top + 7) * SCALE), name, name_font, WHITE, "lt")
    head_font, headline = fit_font(drawable(headline), True, 44, 26, width)
    _label(draw, (text_x * SCALE, (top + 19) * SCALE), headline, head_font, PINK if debt else GOLD, "lt")
    line_width = (right - left - 12) * SCALE
    for k, line in enumerate(lines[:3]):
        line_font, line = fit_font(drawable(line, False), False, 26, 18, line_width)
        _label(draw, ((left + 6) * SCALE, (top + 40 + k * 11) * SCALE), line, line_font, SOFT, "lt")
    return png_bytes(card)
