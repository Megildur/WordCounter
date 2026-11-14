"""Finds swears in message text for the swear jar.

A server's word list (the defaults in swear_words.py plus its changes in jar_words) is compiled into a SwearMatcher. Text is cleaned first (lowercase, no accents, lookalike letters and leetspeak mapped, links and mentions dropped), and spaced-out spellings are matched too, so sh!t and f.u.c.k are still caught. Nothing here uses Discord or the database: the swear jar cog, /analyze_chat and the settings pages call it.
"""

from __future__ import annotations
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from itertools import groupby
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from cogs.utils.swear_words import LANGUAGE_COLUMNS, LANGUAGES, SAFE_WORDS

# The most swears one message can add to the fines and to the swear counts. fine_for keeps the priciest ones.
MAX_FINED_PER_MESSAGE = 3
WORD_LIMIT = 40

LEET = {"@": "a", "4": "a", "$": "s", "5": "s", "1": "i", "!": "i", "|": "i", "3": "e", "0": "o", "7": "t"}
# Cyrillic lookalikes and special Latin letters that accent stripping doesn't break down, mapped to plain Latin ones so a word can't hide behind them.
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ł": "l", "ß": "ss", "ø": "o", "æ": "ae", "đ": "d",
})
INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
# Custom emoji, mentions, channel links, timestamps and URLs. They are removed before matching so a name inside one isn't fined.
NOISE = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>|<t:\d+(?::\w)?>|https?://\S+")
EDGE = "\"'`()[]{}<>.,;:?!~"
SEPARATOR = "[ ._-]"
CUSTOM_WORD = re.compile(r"[^\W\d_][\w' -]*\*?", re.UNICODE)

Override = Tuple[str, int, bool]


@dataclass(frozen=True)
class Entry:
    """One word on the list.

    `key` is the word as typed, with its marker, and is what jar_words stores. `word` is the key without the marker, `kind` is exact, prefix, anywhere or spelling, and `letters` is what gets matched (folded, except for spelling entries).
    """
    key: str
    word: str
    kind: str
    letters: str
    tier: int
    language: str = ""


def fold(text: str) -> str:
    """Lowercases text, strips accents and zero-width characters, and maps lookalike letters to plain Latin ones."""
    text = text.translate(INVISIBLE).lower().translate(CONFUSABLES)
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def normalize(text: str) -> str:
    """Cleans message text for matching and returns its words joined by single spaces.

    Mentions, custom emoji, timestamps and links are dropped, text is folded and punctuation is trimmed from the edges of each word. Leetspeak is mapped to letters, but a * is left alone so that censored words like f*ck can be recognised in find.
    """
    words = []
    for token in fold(NOISE.sub(" ", text)).split():
        token = token.strip(EDGE)
        # A token with no letters in it, like a price or a number, is left alone so its digits aren't turned into letters.
        if any("a" <= char <= "z" for char in token):
            token = "".join(LEET.get(char, char) for char in token)
        if token:
            words.append(token)
    return " ".join(words)


def parse_entry(raw: str, tier: int) -> Entry:
    """Reads one list entry into an Entry. The marker decides what it matches.

    - A leading ~ matches inside longer words.
    - A leading = matches that exact spelling, accents included.
    - A trailing * matches the word and anything that continues it.
    - A plain word matches itself, with an optional s or es.
    """
    key = raw.strip().lower()
    if key.startswith("~"):
        kind, word = "anywhere", key[1:]
    elif key.startswith("="):
        kind, word = "spelling", key[1:]
    elif key.endswith("*"):
        kind, word = "prefix", key[:-1]
    else:
        kind, word = "exact", key
    letters = word if kind == "spelling" else " ".join(fold(word).split())
    return Entry(key, word, kind, letters, tier)


def _core(letters: str) -> str:
    """The pattern for a word's letters, each one allowed to repeat ("fuuuck"). A run of the same letter, like the two o's in "poop", is one `o{2,}` and not `o+o+`: both match the same text, but `o+o+o+o+o+` can take forever on a long run of o's, which would freeze the bot, and a word a server's managers add can have any run in it."""
    parts = []
    for char, run in groupby(letters):
        size = len(list(run))
        unit = r"\s" if char == " " else re.escape(char)
        parts.append(f"{unit}+" if size == 1 else f"{unit}{{{size},}}")
    return "".join(parts)


def entry_pattern(entry: Entry) -> str:
    if entry.kind == "spelling":
        return re.escape(entry.letters)
    core = _core(entry.letters)
    if entry.kind == "exact":
        main = core + "(?:e?s)?"
    elif entry.kind == "prefix":
        main = core + "[a-z]*"
    else:
        main = "[a-z]*" + core + "[a-z]*"
    # Single words of three or more letters also match when spaced out, like f u c k or f.u.c.k.
    if " " not in entry.letters and len(entry.letters) >= 3:
        main = f"(?:{main}|{SEPARATOR.join(re.escape(char) for char in entry.letters)})"
    return main


def _same(part: str, letters: str) -> bool:
    """Returns True if `part` fits `letters` one for one, with * standing for any letter. The first character must be real, and so must at least two characters in all."""
    return (
        len(part) == len(letters)
        and part[0] != "*"
        and sum(char != "*" for char in part) >= 2
        and all(a == "*" or a == b for a, b in zip(part, letters))
    )


def _wild_match(token: str, entry: Entry) -> bool:
    """Matches a censored token such as f*ck against an entry the same way the entry's kind would match plain text."""
    size = len(entry.letters)
    if entry.kind == "exact":
        return any(token.endswith(suffix) and _same(token[: len(token) - len(suffix)], entry.letters) for suffix in ("", "s", "es"))
    if entry.kind == "prefix":
        return len(token) >= size and _same(token[:size], entry.letters)
    return any(_same(token[start:start + size], entry.letters) for start in range(len(token) - size + 1))


def _without_overlaps(spans: List[Tuple[int, int, int]]) -> List[int]:
    """Takes (start, end, tier) matches and returns the tiers of the ones kept. A match that overlaps the last kept one doesn't count again and only raises its tier if it is higher, so a word caught by two patterns is counted once."""
    chosen: List[Tuple[int, int, int]] = []
    for start, end, tier in sorted(spans, key=lambda span: (span[0], -span[2])):
        if chosen and start < chosen[-1][1]:
            if tier > chosen[-1][2]:
                chosen[-1] = (chosen[-1][0], max(end, chosen[-1][1]), tier)
            continue
        chosen.append((start, end, tier))
    return [tier for _, _, tier in chosen]


class SwearMatcher:
    """The compiled word list for one server.

    `find` returns one tier per swear in a text. It doesn't apply the cap of MAX_FINED_PER_MESSAGE, which fine_for does. Build it with matcher_for, which caches.
    """
    def __init__(self, entries: Sequence[Entry]) -> None:
        self.entries = tuple(entries)
        normal: Dict[int, List[str]] = defaultdict(list)
        spelled: Dict[int, List[str]] = defaultdict(list)
        for entry in self.entries:
            (spelled if entry.kind == "spelling" else normal)[entry.tier].append(entry_pattern(entry))
        # One regex per tier. The lookarounds stop a match from starting or ending inside a longer word.
        self.patterns = {tier: re.compile(rf"(?<![a-z0-9])(?:{'|'.join(parts)})(?![a-z0-9])") for tier, parts in normal.items()}
        # Spelling entries depend on accents, so they are matched on the raw text instead and get their own patterns.
        self.spelled = {tier: re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)") for tier, parts in spelled.items()}
        # The entries a censored token like f*ck can match: single words that aren't spelling entries.
        self.starrable = tuple(entry for entry in self.entries if entry.kind != "spelling" and " " not in entry.letters)

    def find(self, text: str) -> List[int]:
        if not text:
            return []
        normalized = normalize(text)
        # Every pattern hit except exact SAFE_WORDS, harmless words that only contain or start with a listed word.
        spans = [
            (match.start(), match.end(), tier)
            for tier, pattern in self.patterns.items()
            for match in pattern.finditer(normalized)
            if match.group(0) not in SAFE_WORDS
        ]
        tiers = _without_overlaps(spans)
        if self.spelled:
            raw = NOISE.sub(" ", text.translate(INVISIBLE).lower())
            for tier, pattern in self.spelled.items():
                tiers.extend([tier] * len(pattern.findall(raw)))
        # Censored words like f*ck are checked token by token, because the patterns don't treat a * as a letter.
        for token in normalized.split():
            token = token.strip("*")
            if "*" in token and token.replace("*", "").isalpha():
                tier = self._star_tier(token)
                if tier is not None:
                    tiers.append(tier)
        return tiers

    def _star_tier(self, token: str) -> Optional[int]:
        matches = [entry.tier for entry in self.starrable if _wild_match(token, entry)]
        return min(matches) if matches else None


def fine_for(tiers: Iterable[int], prices: Mapping[int, int]) -> Tuple[int, int]:
    """Returns (how many swears are fined, their total price in units) for the tiers found in one message.

    Only the MAX_FINED_PER_MESSAGE priciest swears count. `prices` maps each tier to its price in units.
    """
    counted = sorted((prices[tier] for tier in tiers), reverse=True)[:MAX_FINED_PER_MESSAGE]
    return len(counted), sum(counted)


DEFAULT_ENTRIES: Tuple[Entry, ...] = tuple(
    replace(parse_entry(raw, tier), language=code)
    for code, (_, words) in LANGUAGES.items()
    for tier, raws in words.items()
    for raw in raws
)
DEFAULT_KEYS = {entry.key for entry in DEFAULT_ENTRIES}
DEFAULT_LANGUAGE = {entry.key: entry.language for entry in DEFAULT_ENTRIES}
ALL_LANGUAGES: Tuple[str, ...] = tuple(LANGUAGES)


def languages_for(settings: Mapping[str, Any]) -> Tuple[str, ...]:
    return tuple(code for code, column in LANGUAGE_COLUMNS.items() if settings.get(column))


def build_entries(overrides: Iterable[Override], languages: Iterable[str] = ALL_LANGUAGES) -> List[Entry]:
    """Returns the entries a server's matcher is built from.

    `overrides` are the server's jar_words rows as (key, tier, removed). A removed row hides a default word. Any other row moves a default word to another tier, or adds a word of the server's own. Default words of languages that are turned off are left out.
    """
    allowed = set(languages)
    removed = {key for key, _, gone in overrides if gone}
    tiers = {key: tier for key, tier, gone in overrides if not gone}
    entries = [
        replace(entry, tier=tiers.pop(entry.key, entry.tier))
        for entry in DEFAULT_ENTRIES
        if entry.key not in removed and entry.language in allowed
    ]
    entries.extend(parse_entry(key, tier) for key, tier in tiers.items() if key not in DEFAULT_LANGUAGE)
    return entries


@lru_cache(maxsize=64)
def matcher_for(overrides: Tuple[Override, ...], languages: Tuple[str, ...] = ALL_LANGUAGES) -> SwearMatcher:
    """Returns the matcher for a word list, built once and kept for the 64 most recently used lists. `overrides` is a tuple so it can be a cache key."""
    return SwearMatcher(build_entries(overrides, languages))


def default_keys_for(word: str) -> List[str]:
    """Returns the keys of the default entries that spell the same as `word`, ignoring accents and the ~ = * markers. Empty if it isn't a default word."""
    folded = " ".join(fold(word.strip().lstrip("~=").rstrip("*")).split())
    return [entry.key for entry in DEFAULT_ENTRIES if " ".join(fold(entry.word).split()) == folded]


def custom_key(word: str) -> Optional[str]:
    """Returns a server's own word cleaned up as a list key, or None if it is empty, longer than WORD_LIMIT or has characters a word can't have."""
    cleaned = " ".join(word.strip().lower().split())
    if not cleaned or len(cleaned) > WORD_LIMIT or not CUSTOM_WORD.fullmatch(cleaned):
        return None
    return cleaned

