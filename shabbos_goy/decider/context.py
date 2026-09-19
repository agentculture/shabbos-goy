"""The rolling context window (deviation d3).

A trimmed window of recent utterances travels with each model call so the
model can tell a continuation from a fresh sentence. It is **memory only**:
built on :class:`shabbos_goy.limits.BoundedRing`, bounded by item count, by
age and by rendered size, never written to disk, never logged, empty in a
newly constructed instance -- which is what "gone on restart" means for a
process that persists nothing (CLAUDE.md invariant #3).

It holds transcript text, so ``__repr__`` deliberately shows counts only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from ..limits import BoundedRing, Clock, system_clock
from .decision import Decision

DEFAULT_MAX_ITEMS = 8
DEFAULT_MAX_AGE_SECONDS = 900.0
DEFAULT_MAX_RENDER_CHARS = 800


@dataclass(frozen=True)
class ContextEntry:
    """One remembered utterance and the verdict it got."""

    at: float
    text: str
    klass: str
    intent: str


class ContextWindow:
    """A bounded, in-memory rolling window of recent utterances."""

    def __init__(
        self,
        max_items: int = DEFAULT_MAX_ITEMS,
        max_age_seconds: float = DEFAULT_MAX_AGE_SECONDS,
        max_render_chars: int = DEFAULT_MAX_RENDER_CHARS,
        clock: Clock = system_clock,
    ) -> None:
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be > 0")
        if max_render_chars < 1:
            raise ValueError("max_render_chars must be >= 1")
        self._max_items = max_items
        self._max_age = float(max_age_seconds)
        self._max_render_chars = max_render_chars
        self._clock = clock
        self._ring: BoundedRing[ContextEntry] = BoundedRing(max_items)

    # -- state -----------------------------------------------------------

    def add(self, text: str, decision: Decision) -> None:
        """Remember one utterance and its verdict (oldest evicted on overflow)."""
        self._ring.append(
            ContextEntry(
                at=self._clock(),
                text=text,
                klass=decision.klass,
                intent=decision.intent,
            )
        )
        self._expire()

    def clear(self) -> None:
        """Drop everything -- used on a reconnect, and on any doubt."""
        self._ring = BoundedRing(self._max_items)

    def entries(self) -> Iterator[ContextEntry]:
        self._expire()
        return iter(list(self._ring))

    def __len__(self) -> int:
        self._expire()
        return len(self._ring)

    def _expire(self) -> None:
        """Drop entries older than ``max_age_seconds``, oldest first."""
        cutoff = self._clock() - self._max_age
        surviving = [entry for entry in self._ring if entry.at > cutoff]
        if len(surviving) != len(self._ring):
            ring: BoundedRing[ContextEntry] = BoundedRing(self._max_items)
            for entry in surviving:
                ring.append(entry)
            self._ring = ring

    # -- rendering -------------------------------------------------------

    def render(self) -> str:
        """The compact context block sent to the model, newest last.

        Capped at ``max_render_chars``; when it does not fit, the oldest
        lines are dropped until it does.
        """
        self._expire()
        lines = [f"[{entry.klass}/{entry.intent}] {entry.text}" for entry in self._ring]
        while lines:
            rendered = "\n".join(lines)
            if len(rendered) <= self._max_render_chars:
                return rendered
            lines.pop(0)
        return ""

    def __repr__(self) -> str:
        return (
            f"ContextWindow(items={len(self._ring)}/{self._max_items}, "
            f"max_age_seconds={self._max_age:.0f}, max_render_chars={self._max_render_chars})"
        )

    __str__ = __repr__
