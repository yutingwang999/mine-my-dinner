"""Per-conversation diner labels, and the LLM client hooks that hide saved friends' nicknames.

Saved friends' nicknames are replaced with "Diner N" labels in model-bound text (pre-hook)
and labels are restored in model output shown to the user (post-hook). One `LLMClient`
serves every thread, so the label table of the current call comes from a context variable.
"""

import re
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from minemydinner.extraction.normalize import all_terms
from minemydinner.models import Diner, Ingredient, Recipe

SELF_LABEL = "Diner 1"

# "Diner 2" in any case. Python's \b sees no boundary between a digit or letter and a CJK
# character (both are word characters), so ASCII lookarounds are used instead: "Diner 2也来"
# and "和Diner 2" still match.
LABEL_PATTERN = re.compile(r"(?<![A-Za-z0-9])diner\s+([0-9]+)(?![0-9])", re.IGNORECASE)

RESTORED = {
    "zh": {"self": "你", "guest": "客人", "other": "其他用餐者"},
    "en": {"self": "you", "guest": "a guest", "other": "another diner"},
}


def _label_number(label: str) -> int:
    return int(label.rsplit(" ", 1)[1])


@dataclass
class LabelTable:
    """Diner key ("diner:<id>", "guest:<n>") to "Diner N". It only grows within a thread."""

    labels: dict[str, str] = field(default_factory=dict)
    # Key to the name shown to the user: a guest's name; None for the user and unnamed guests.
    names: dict[str, str | None] = field(default_factory=dict)

    def label_for(self, key: str) -> str:
        """The key's label, assigning the next number when the key is new. "Diner 1" is
        reserved for the user, whose key is set by `from_state`."""
        label = self.labels.get(key)
        if label is None:
            label = f"Diner {self.highest_number() + 1}"
            self.labels[key] = label
        return label

    def highest_number(self) -> int:
        """The highest label number in use; at least 1, which is the user's."""
        return max((_label_number(label) for label in self.labels.values()), default=1)

    def key_for(self, label_number: int) -> str | None:
        wanted = f"Diner {label_number}"
        return next((key for key, label in self.labels.items() if label == wanted), None)

    def to_state(self) -> dict:
        return {"labels": dict(self.labels), "names": dict(self.names)}

    @classmethod
    def from_state(cls, data: dict | None, self_id: int | None) -> "LabelTable":
        data = data or {}
        table = cls(dict(data.get("labels") or {}), dict(data.get("names") or {}))
        if self_id is not None:
            self_key = f"diner:{self_id}"
            # The user's key always holds "Diner 1"; no other key may.
            table.labels = {
                key: label
                for key, label in table.labels.items()
                if key == self_key or label != SELF_LABEL
            }
            table.labels[self_key] = SELF_LABEL
        return table


@dataclass(frozen=True)
class Friend:
    diner_id: int
    nickname: str


def _is_cjk(char: str) -> bool:
    code = ord(char)
    return (
        0x3040 <= code <= 0x30FF  # kana
        or 0x3400 <= code <= 0x9FFF  # CJK ideographs
        or 0xAC00 <= code <= 0xD7AF  # hangul
        or 0xF900 <= code <= 0xFAFF  # CJK compatibility ideographs
        or 0x20000 <= code <= 0x2FA1F  # CJK extensions
    )


def _is_latin_word_char(char: str) -> bool:
    return char.isalnum() and not _is_cjk(char)


def _whole_word(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (before and _is_latin_word_char(before)) and not (
        after and _is_latin_word_char(after)
    )


def _occurrences(haystack: str, needle: str) -> Iterator[int]:
    """Start positions of every occurrence, overlapping ones included."""
    start = haystack.find(needle)
    while start != -1:
        yield start
        start = haystack.find(needle, start + 1)


def find_friends(
    text: str, friends: Sequence[Friend], food_terms: Collection[str]
) -> list[tuple[int, int, Friend]]:
    """Every (start, end, friend) occurrence of a saved nickname, in text order.

    Longest nickname first, and occurrences never overlap. Latin nicknames match whole
    words, ignoring case; CJK nicknames match as substrings. An occurrence that lies inside
    a longer food term (a dictionary name or alias, or a recipe title) found at that position
    is skipped, so "鱼" does not match inside "鱼香肉丝".
    """
    # Spans index both texts, so case is ignored only when folding keeps the length (it
    # does for CJK and ASCII).
    folded = text.casefold()
    if len(folded) != len(text):
        folded = text
    taken: list[tuple[int, int]] = []
    found: list[tuple[int, int, Friend]] = []
    for friend in sorted(friends, key=lambda item: len(item.nickname), reverse=True):
        nickname = friend.nickname.strip().casefold()
        if not nickname:
            continue
        latin = not any(_is_cjk(char) for char in nickname)
        covers = [
            (start, start + len(term))
            for term in (term.casefold() for term in food_terms)
            if len(term) > len(nickname) and nickname in term
            for start in _occurrences(folded, term)
        ]
        for start in _occurrences(folded, nickname):
            end = start + len(nickname)
            if latin and not _whole_word(text, start, end):
                continue
            if any(cover_start <= start and end <= cover_end for cover_start, cover_end in covers):
                continue
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            found.append((start, end, friend))
    return sorted(found, key=lambda item: item[0])


@dataclass
class LabelContext:
    table: LabelTable
    friends: list[Friend]
    food_terms: frozenset[str]
    locale: str
    restore: bool = True


current_labels: ContextVar[LabelContext | None] = ContextVar("current_labels", default=None)


@contextmanager
def labels_scope(ctx: LabelContext) -> Iterator[LabelContext]:
    """Set the label context for the model calls made inside the block."""
    token = current_labels.set(ctx)
    try:
        yield ctx
    finally:
        current_labels.reset(token)


def replace_nicknames(text: str) -> str:
    """Pre-hook: replace saved friends' nicknames with their labels, assigning new labels in
    the order friends are mentioned."""
    ctx = current_labels.get()
    if ctx is None or not ctx.friends:
        return text
    parts: list[str] = []
    position = 0
    for start, end, friend in find_friends(text, ctx.friends, ctx.food_terms):
        parts.append(text[position:start])
        parts.append(ctx.table.label_for(f"diner:{friend.diner_id}"))
        position = end
    parts.append(text[position:])
    return "".join(parts)


def restore_labels(text: str) -> str:
    """Post-hook: replace each "Diner N" with what the user should see."""
    ctx = current_labels.get()
    if ctx is None or not ctx.restore:
        return text
    words = RESTORED.get(ctx.locale, RESTORED["en"])
    nicknames = {f"diner:{friend.diner_id}": friend.nickname for friend in ctx.friends}

    def restored(match: re.Match[str]) -> str:
        number = int(match.group(1))
        if number == 1:
            return words["self"]
        key = ctx.table.key_for(number)
        if key in nicknames:
            return nicknames[key]
        if key is not None and key.startswith("guest:"):
            return ctx.table.names.get(key) or words["guest"]
        return words["other"]

    return LABEL_PATTERN.sub(restored, text)


def load_label_context(
    session: Session, table: LabelTable, locale: str, restore: bool = True
) -> LabelContext:
    """Saved friends (every diner but the user) and the food terms that guard against false
    nickname matches: every ingredient name and alias, personal aliases included, and every
    recipe title, longer than one character."""
    friends = [
        Friend(diner.id, diner.display_name)
        for diner in session.scalars(
            select(Diner).where(Diner.is_self.is_(False)).order_by(Diner.id)
        )
    ]
    terms = {term for entry in session.scalars(select(Ingredient)) for term in all_terms(entry)}
    terms.update(session.scalars(select(Recipe.title)))
    food_terms = frozenset(term for term in terms if len(term) > 1)
    return LabelContext(table, friends, food_terms, locale, restore)
