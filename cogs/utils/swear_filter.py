from __future__ import annotations
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from cogs.utils.swear_words import DEFAULT_WORDS, SAFE_WORDS

MAX_FINED_PER_MESSAGE = 3
WORD_LIMIT = 40

LEET = {"@": "a", "4": "a", "$": "s", "5": "s", "1": "i", "!": "i", "|": "i", "3": "e", "0": "o", "7": "t"}
CONFUSABLES = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "к": "k", "і": "i", "ј": "j",
    "ѕ": "s", "ԁ": "d", "ł": "l", "ß": "ss", "ø": "o", "æ": "ae", "đ": "d",
})
INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))
NOISE = re.compile(r"<a?:\w+:\d+>|<[@#][!&]?\d+>|<t:\d+(?::\w)?>|https?://\S+")
EDGE = "\"'`()[]{}<>.,;:?!~"
SEPARATOR = "[ ._-]"
CUSTOM_WORD = re.compile(r"[^\W\d_][\w' -]*\*?", re.UNICODE)

Override = Tuple[str, int, bool]


@dataclass(frozen=True)
class Entry:
    key: str
    word: str
    kind: str
    letters: str
    tier: int


def fold(text: str) -> str:
    text = text.translate(INVISIBLE).lower().translate(CONFUSABLES)
    return "".join(char for char in unicodedata.normalize("NFKD", text) if not unicodedata.combining(char))


def normalize(text: str) -> str:
    words = []
    for token in fold(NOISE.sub(" ", text)).split():
        token = token.strip(EDGE)
        if any("a" <= char <= "z" for char in token):
            token = "".join(LEET.get(char, char) for char in token)
        if token:
            words.append(token)
    return " ".join(words)


def parse_entry(raw: str, tier: int) -> Entry:
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
    return "".join(r"\s+" if char == " " else re.escape(char) + "+" for char in letters)


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
    if " " not in entry.letters and len(entry.letters) >= 3:
        main = f"(?:{main}|{SEPARATOR.join(re.escape(char) for char in entry.letters)})"
    return main


def _same(part: str, letters: str) -> bool:
    return (
        len(part) == len(letters)
        and part[0] != "*"
        and sum(char != "*" for char in part) >= 2
        and all(a == "*" or a == b for a, b in zip(part, letters))
    )


def _wild_match(token: str, entry: Entry) -> bool:
    size = len(entry.letters)
    if entry.kind == "exact":
        return any(token.endswith(suffix) and _same(token[: len(token) - len(suffix)], entry.letters) for suffix in ("", "s", "es"))
    if entry.kind == "prefix":
        return len(token) >= size and _same(token[:size], entry.letters)
    return any(_same(token[start:start + size], entry.letters) for start in range(len(token) - size + 1))


def _without_overlaps(spans: List[Tuple[int, int, int]]) -> List[int]:
    chosen: List[Tuple[int, int, int]] = []
    for start, end, tier in sorted(spans, key=lambda span: (span[0], -span[2])):
        if chosen and start < chosen[-1][1]:
            if tier > chosen[-1][2]:
                chosen[-1] = (chosen[-1][0], max(end, chosen[-1][1]), tier)
            continue
        chosen.append((start, end, tier))
    return [tier for _, _, tier in chosen]


class SwearMatcher:
    def __init__(self, entries: Sequence[Entry]) -> None:
        self.entries = tuple(entries)
        normal: Dict[int, List[str]] = defaultdict(list)
        spelled: Dict[int, List[str]] = defaultdict(list)
        for entry in self.entries:
            (spelled if entry.kind == "spelling" else normal)[entry.tier].append(entry_pattern(entry))
        self.patterns = {tier: re.compile(rf"(?<![a-z0-9])(?:{'|'.join(parts)})(?![a-z0-9])") for tier, parts in normal.items()}
        self.spelled = {tier: re.compile(rf"(?<!\w)(?:{'|'.join(parts)})(?!\w)") for tier, parts in spelled.items()}
        self.starrable = tuple(entry for entry in self.entries if entry.kind != "spelling" and " " not in entry.letters)

    def find(self, text: str) -> List[int]:
        if not text:
            return []
        normalized = normalize(text)
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
    counted = sorted((prices[tier] for tier in tiers), reverse=True)[:MAX_FINED_PER_MESSAGE]
    return len(counted), sum(counted)


DEFAULT_ENTRIES: Tuple[Entry, ...] = tuple(
    parse_entry(raw, tier) for tier, words in DEFAULT_WORDS.items() for raw in words
)
DEFAULT_KEYS = {entry.key for entry in DEFAULT_ENTRIES}


def build_entries(overrides: Iterable[Override]) -> List[Entry]:
    removed = {key for key, _, gone in overrides if gone}
    tiers = {key: tier for key, tier, gone in overrides if not gone}
    entries = [replace(entry, tier=tiers.pop(entry.key, entry.tier)) for entry in DEFAULT_ENTRIES if entry.key not in removed]
    entries.extend(parse_entry(key, tier) for key, tier in tiers.items())
    return entries


@lru_cache(maxsize=64)
def matcher_for(overrides: Tuple[Override, ...]) -> SwearMatcher:
    return SwearMatcher(build_entries(overrides))


def default_keys_for(word: str) -> List[str]:
    folded = " ".join(fold(word.strip().lstrip("~=").rstrip("*")).split())
    return [entry.key for entry in DEFAULT_ENTRIES if " ".join(fold(entry.word).split()) == folded]


def custom_key(word: str) -> Optional[str]:
    cleaned = " ".join(word.strip().lower().split())
    if not cleaned or len(cleaned) > WORD_LIMIT or not CUSTOM_WORD.fullmatch(cleaned):
        return None
    return cleaned

