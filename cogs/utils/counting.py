"""What counts as a word, an attachment, an emoji and a keyword hit in one message.

Pure counting with no database access, shared by the live counter (`cogs/counter.py`) and `/analyze_chat`, so a message counted live and one counted from history give the same numbers.
"""

from __future__ import annotations
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Iterable
import discord

CUSTOM_EMOJI_PATTERN = re.compile(r'<a?:[a-zA-Z0-9_]{2,32}:\d+>')

# One match is one emoji: a base character with any skin tone, variation selector or joined (zero-width joiner) parts, or a flag made of two regional indicators.
UNICODE_EMOJI_PATTERN = re.compile(
    r'(?:'
    r'[\U0001F1E6-\U0001F1FF]{2}'
    r'|[\U0001F600-\U0001F64F]'
    r'|[\U0001F300-\U0001F5FF]'
    r'|[\U0001F680-\U0001F6FF]'
    r'|[\U0001F700-\U0001F77F]'
    r'|[\U0001F780-\U0001F7FF]'
    r'|[\U0001F800-\U0001F8FF]'
    r'|[\U0001F900-\U0001F9FF]'
    r'|[\U0001FA00-\U0001FA6F]'
    r'|[\U0001FA70-\U0001FAFF]'
    r'|[\U00002600-\U000026FF]'
    r'|[\U00002700-\U000027BF]'
    r'|[\U00002300-\U000023FF]'
    r'|[\U00002B50\U00002B55\U0000203C\U00002049\U00002139\U00002122\U00003030\U0000303D\U000000A9\U000000AE]'
    r')(?:[\U0001F3FB-\U0001F3FF︎️]|‍(?:[\U0001F000-\U0001FAFF☀-➿][\U0001F3FB-\U0001F3FF︎️]*))*'
)

LINK_PREFIXES = ("http://", "https://")


def count_emojis(text: str) -> int:
    if not text:
        return 0
    custom = len(CUSTOM_EMOJI_PATTERN.findall(text))
    unicode = len(UNICODE_EMOJI_PATTERN.findall(CUSTOM_EMOJI_PATTERN.sub('', text)))
    return custom + unicode


def count_links(text: str) -> int:
    return sum(1 for word in text.split() if word.strip('<>()"\'').startswith(LINK_PREFIXES))


@lru_cache(maxsize=4096)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    # Every message is checked against every keyword, so each pattern is compiled once and cached. Matching is by whole word, so `gg` skips `eggs`, and a keyword that starts or ends with a symbol only matches next to a letter or digit.
    return re.compile(r"\b" + re.escape(keyword.lower()) + r"\b")


def count_keywords(text: str, keywords: Iterable[str]) -> Dict[str, int]:
    """Returns {keyword: hits} for each keyword that appears at least once, matched as a whole word without regard to case."""
    if not text:
        return {}
    lowered = text.lower()
    counts: Dict[str, int] = {}
    for keyword in keywords:
        hits = len(_keyword_pattern(keyword).findall(lowered))
        if hits:
            counts[keyword] = hits
    return counts


@dataclass(frozen=True, slots=True)
class MessageStats:
    """The counts one message adds.

    `words` is the number of whitespace-separated pieces, so a link or an emoji on its own is a word. `attachments` is uploaded files plus stickers plus links in the text. `keywords` maps each keyword found to its hit count.
    """
    words: int
    attachments: int
    emojis: int
    keywords: Dict[str, int]

    @classmethod
    def measure(cls, content: str, file_count: int, keywords: Iterable[str]) -> MessageStats:
        """Measures raw text with the number of files and stickers given separately, for callers that have message data but no `discord.Message`."""
        content = content or ""
        return cls(
            words=len(content.split()),
            # Links count as attachments, the same as uploaded files and stickers.
            attachments=file_count + count_links(content),
            emojis=count_emojis(content),
            keywords=count_keywords(content, keywords),
        )

    @classmethod
    def of(cls, message: discord.Message, keywords: Iterable[str]) -> MessageStats:
        return cls.measure(message.content, len(message.attachments) + len(message.stickers), keywords)
