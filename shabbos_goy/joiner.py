"""Transcript joiner: re-join sentences the ASR splits across a pause.

The reSpeaker/lobes speech stack ends a "turn" after ~500ms of silence
(see ``docs/`` and the project ``CLAUDE.md``, "Speech stack: lobes
integration"). One spoken sentence can therefore arrive as two separate
``transcription.completed`` events, each bracketed by its own
``speech_started`` / ``speech_stopped`` pair. Classifying either half in
isolation risks the core invariant: a half-sentence must never be treated
as actionable (see ``CLAUDE.md``, "A half-sentence never acts.").

This module is a **pure state machine**. It holds no thread, spawns no
timer, and never sleeps. It reacts only to:

- events handed to :meth:`TranscriptJoiner.handle_event`, each optionally
  carrying an ``at_ms`` (a monotonic milliseconds timestamp from the
  speech session), and
- an **injected clock**: the caller periodically calls
  :meth:`TranscriptJoiner.poll` with the current time (in the same
  ``at_ms`` units), and the joiner decides then whether a pending buffer
  has waited long enough with no continuation to flush it. Tests drive
  this directly with arbitrary ``now_ms`` values, so no real sleeping is
  ever required.

Event shapes consumed by :meth:`handle_event`:

- ``{"type": "speech_started", "at_ms": int}``
- ``{"type": "speech_stopped", "at_ms": int}``
- ``{"type": "transcription.completed", "text": str}``

Any other/unknown event type is ignored (forward-compatible with the
lobes contract growing new event types this joiner does not need).

Join rule (acceptance criterion 1): when a ``speech_started`` event's
``at_ms`` arrives less than ``gap_threshold_ms`` after the *previous*
``speech_stopped.at_ms``, the two segments' transcript text are treated
as one continued utterance and their texts are joined with a single
space. Only one joined utterance is ever emitted for such a pair -- the
callback fires once, carrying the full joined text.

Safety rules (acceptance criterion 2):

- A pending half whose continuation never arrives is flushed on its own
  once the caller's injected clock shows the threshold has elapsed --
  never held indefinitely, and never silently merged with unrelated
  speech that happens to arrive much later.
- A reconnect resets the speech session's clock, so a later
  ``speech_started.at_ms`` can be *smaller* than the previous
  ``speech_stopped.at_ms``. That can never look like a short gap: any
  non-positive or otherwise implausible gap forces a flush of whatever is
  buffered instead of joining across the reconnect boundary.
- Empty-text ``transcription.completed`` events are dropped noise (see
  ``CLAUDE.md``: "Empty text transcripts are dropped noise") and never
  contribute to, or by themselves start, a buffered utterance.
- The buffer is bounded: it can never grow without limit no matter how
  many segments keep joining, so a runaway/never-ending session cannot
  turn this into unbounded memory growth.
"""

from __future__ import annotations

from typing import Callable, Optional

__all__ = ["TranscriptJoiner"]

_DEFAULT_GAP_THRESHOLD_MS = 500
_DEFAULT_MAX_BUFFER_CHARS = 4096


class TranscriptJoiner:
    """Joins pause-split transcript halves into single utterances.

    Parameters
    ----------
    gap_threshold_ms:
        The maximum silence gap (in the same units as event ``at_ms``,
        milliseconds) between one segment's ``speech_stopped`` and the
        next segment's ``speech_started`` that still counts as one
        continued utterance. Must match (or be looser than) the speech
        session's own silence-based turn-ending window, since a gap at or
        above it means the session itself already ended the turn.
    on_utterance:
        Called with the joined (or single, un-joined) utterance text
        exactly once per flush. This stands in for the not-yet-built
        classifier: nothing downstream is invoked automatically, and this
        module never imports or depends on it.
    max_buffer_chars:
        Upper bound on the buffered utterance text. Reaching it forces an
        immediate flush of what has been buffered so far before the new
        text is appended (which itself starts a fresh buffer), so a
        pathological run of continuations cannot grow memory unboundedly.
    """

    def __init__(
        self,
        gap_threshold_ms: int = _DEFAULT_GAP_THRESHOLD_MS,
        on_utterance: Optional[Callable[[str], None]] = None,
        max_buffer_chars: int = _DEFAULT_MAX_BUFFER_CHARS,
    ) -> None:
        if gap_threshold_ms <= 0:
            raise ValueError("gap_threshold_ms must be positive")
        if max_buffer_chars <= 0:
            raise ValueError("max_buffer_chars must be positive")
        self._gap_threshold_ms = gap_threshold_ms
        self._on_utterance = on_utterance
        self._max_buffer_chars = max_buffer_chars

        self._buffer_text = ""
        self._last_stopped_at_ms: Optional[int] = None
        # The at_ms deadline at/after which a poll() must flush a pending
        # buffer if no qualifying continuation has arrived by then.
        self._flush_due_at_ms: Optional[int] = None

    def handle_event(self, event: dict) -> None:
        """Feed one event from the speech session into the state machine."""
        event_type = event.get("type")
        if event_type == "speech_started":
            self._on_speech_started(event.get("at_ms"))
        elif event_type == "speech_stopped":
            self._on_speech_stopped(event.get("at_ms"))
        elif event_type == "transcription.completed":
            self._on_transcript(event.get("text") or "")
        # Unknown event types are ignored by design (forward-compatible).

    def poll(self, now_ms: int) -> None:
        """Give the joiner the current time (an injected clock).

        If a buffered utterance has been waiting for a continuation for
        at least ``gap_threshold_ms`` with none arriving, flush it now.
        Safe to call at any cadence, including when nothing is buffered.
        """
        if self._flush_due_at_ms is not None and now_ms >= self._flush_due_at_ms:
            self._flush()

    def _on_speech_started(self, at_ms: Optional[int]) -> None:
        if self._last_stopped_at_ms is not None and at_ms is not None:
            gap = at_ms - self._last_stopped_at_ms
            if gap < 0 or gap >= self._gap_threshold_ms:
                # A reconnect (at_ms went backwards) or a genuine long
                # pause: this speech is not a continuation of what is
                # buffered. Flush the old buffer as its own utterance
                # before starting to track the new segment.
                self._flush()
        # A new segment has begun; drop any stale flush deadline from a
        # speech_stopped whose continuation is now this speech_started.
        self._flush_due_at_ms = None

    def _on_speech_stopped(self, at_ms: Optional[int]) -> None:
        self._last_stopped_at_ms = at_ms
        if at_ms is not None:
            self._flush_due_at_ms = at_ms + self._gap_threshold_ms
        else:
            # No timestamp to reason about a gap from: flush immediately
            # rather than buffer indefinitely with no way to time out.
            self._flush_due_at_ms = None
            self._flush()

    def _on_transcript(self, text: str) -> None:
        if not text:
            return  # empty-text transcripts are dropped noise
        if len(self._buffer_text) + 1 + len(text) > self._max_buffer_chars:
            # Bound the buffer: emit what we have so far as its own
            # utterance rather than let it grow without limit.
            self._flush()
        if self._buffer_text:
            self._buffer_text = f"{self._buffer_text} {text}"
        else:
            self._buffer_text = text

    def _flush(self) -> None:
        text, self._buffer_text = self._buffer_text, ""
        self._last_stopped_at_ms = None
        self._flush_due_at_ms = None
        if text and self._on_utterance is not None:
            self._on_utterance(text)
