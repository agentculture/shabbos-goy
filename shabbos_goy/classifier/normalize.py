"""Hebrew text normalisation for the classifier.

Everything the rules match on goes through :func:`normalize` first, so the
lexicon can be written in one plain spelling. Three transformations matter
for this agent in particular:

1. **Niqqud and cantillation are stripped** (U+0591..U+05C7). The TTS side of
   the speech stack adds niqqud for Chatterbox, and text fixtures may be
   typed either way; the classifier must not care.
2. **Final letters are folded** to their base form, so a word is spelled the
   same whether it ends a sentence or not, and so an ASR that emits the
   non-final form mid-word is still understood.
3. **A detached one-letter prefix is re-attached.** The transcript joiner
   joins pause-split halves *with a space*, so one spoken sentence can reach
   the classifier as "הלוואי ש היה קר" rather than "הלוואי שהיה קר". A
   standalone prefix letter followed by another word is therefore glued back
   on before any matching happens.

The question mark is removed like all other punctuation, but is recorded
first: "is this a question" is one of the rules' inputs, and a question is
never a hint.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

__all__ = ["Normalized", "normalize", "stem_candidates", "PREFIX_LETTERS"]

#: One-letter Hebrew prefixes (conjunction, definite article, prepositions,
#: and the ש- relativiser) that a pause can detach from their host word.
PREFIX_LETTERS = frozenset("שהובלכמ")

_FINALS = {
    "ך": "כ",  # ך -> כ
    "ם": "מ",  # ם -> מ
    "ן": "נ",  # ן -> נ
    "ף": "פ",  # ף -> פ
    "ץ": "צ",  # ץ -> צ
}

_QUESTION_MARKS = ("?", "؟", "？")

# Hebrew punctuation that carries no meaning for matching: geresh, gershayim,
# maqaf, sof pasuq, plus the usual Latin punctuation an ASR emits.
_PUNCTUATION = set("׳״־׃.,!;:-–—\"'`()[]{}/\\…“”")


@dataclass(frozen=True)
class Normalized:
    """The normalised forms the rules match against.

    ``text`` is the canonical spelling (prefixes re-attached). ``stem_text``
    is the same sentence with a single leading prefix letter removed from
    every long-enough word, so a multi-word phrase such as "אף אחד לא" is
    still found inside "חבל שאף אחד לא ...". ``candidates`` holds, per token,
    every successive prefix-stripped form, for single-word lexicon lookups.
    """

    text: str
    tokens: tuple[str, ...]
    stem_text: str
    candidates: tuple[frozenset[str], ...]
    has_question_mark: bool

    def has_token(self, *words: str) -> bool:
        """True if any of ``words`` matches a token, ignoring prefix letters."""
        return any(word in bucket for bucket in self.candidates for word in words)

    def token_index(self, *words: str) -> int | None:
        """Index of the first token matching any of ``words``, or ``None``."""
        for index, bucket in enumerate(self.candidates):
            if any(word in bucket for word in words):
                return index
        return None

    def has_phrase(self, *phrases: str) -> bool:
        """True if any of ``phrases`` (one or more words) occurs in the text."""
        padded_text = f" {self.text} "
        padded_stems = f" {self.stem_text} "
        return any(
            f" {phrase} " in padded_text or f" {phrase} " in padded_stems for phrase in phrases
        )

    def phrase_span(self, *phrases: str) -> tuple[int, int] | None:
        """Token span ``(start, end)`` of the first matching phrase, if any."""
        for phrase in phrases:
            words = phrase.split()
            width = len(words)
            for start in range(0, len(self.tokens) - width + 1):
                window = self.tokens[start : start + width]
                stripped = tuple(_strip_one(word) for word in window)
                if list(window) == words or list(stripped) == words:
                    return (start, start + width)
        return None


def _strip_one(token: str) -> str:
    if len(token) > 2 and token[0] in PREFIX_LETTERS:
        return token[1:]
    return token


def stem_candidates(token: str) -> frozenset[str]:
    """Every prefix-stripped form of ``token``, down to three letters.

    The floor is three letters on purpose. Stripping further manufactures
    two-letter words that are not there: "בוקר" (morning) would yield "קר"
    (cold) and a good morning would read as a hint to warm the room. A
    lexicon word of two letters -- "חם", "קר" -- is therefore only ever
    matched when the speaker actually said it on its own, which is how these
    words are used.
    """
    forms = {token}
    current = token
    while len(current) > 3 and current[0] in PREFIX_LETTERS:
        current = current[1:]
        forms.add(current)
    return frozenset(forms)


def normalize(raw: str | None) -> Normalized:
    """Normalise ``raw`` into the forms the rules match against."""
    text = unicodedata.normalize("NFC", raw or "")
    has_question = any(mark in text for mark in _QUESTION_MARKS)

    cleaned_chars = []
    for char in text:
        if "֑" <= char <= "ׇ":
            continue  # niqqud / te'amim
        if char in _QUESTION_MARKS or char in _PUNCTUATION:
            cleaned_chars.append(" ")
            continue
        cleaned_chars.append(_FINALS.get(char, char))
    cleaned = "".join(cleaned_chars).lower()

    raw_tokens = cleaned.split()
    tokens: list[str] = []
    index = 0
    while index < len(raw_tokens):
        token = raw_tokens[index]
        if len(token) == 1 and token in PREFIX_LETTERS and index + 1 < len(raw_tokens):
            tokens.append(token + raw_tokens[index + 1])
            index += 2
            continue
        tokens.append(token)
        index += 1

    return Normalized(
        text=" ".join(tokens),
        tokens=tuple(tokens),
        stem_text=" ".join(_strip_one(token) for token in tokens),
        candidates=tuple(stem_candidates(token) for token in tokens),
        has_question_mark=has_question,
    )
