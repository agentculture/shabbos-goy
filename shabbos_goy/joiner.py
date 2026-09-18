"""Transcript joiner: re-join sentences the ASR splits across a pause.

The reSpeaker/lobes speech stack ends a "turn" after a short silence (see
``docs/`` and the project ``CLAUDE.md``, "Speech stack: lobes
integration"). One spoken sentence can therefore arrive as two separate
transcription-completed events, each bracketed by its own speech-started /
speech-stopped pair. Classifying either half in isolation risks the core
invariant: a half-sentence must never be treated as actionable (see
``CLAUDE.md``, "A half-sentence never acts.").

This module is a **pure state machine**. It holds no thread, spawns no
timer, and never sleeps. It reacts only to:

- events handed to :meth:`TranscriptJoiner.handle_event`, and
- an **injected clock**: the caller periodically calls
  :meth:`TranscriptJoiner.poll` with the current time (in the same
  ``at_ms`` units as the boundary events), and the joiner decides then
  whether a pending buffer has waited long enough with no continuation to
  flush it. Tests drive this directly with arbitrary ``now_ms`` values, so
  no real sleeping is ever required.

Event names follow the real lobes/OpenAI-Realtime contract
(``lobes-cli``'s ``docs/contracts/realtime-tool-calling.md``, section 4):

- ``input_audio_buffer.speech_started``   ``{at_ms, item_id}``
- ``input_audio_buffer.speech_stopped``   ``{at_ms, item_id, reason}``
- ``conversation.item.input_audio_transcription.completed``
  ``{item_id, text}`` -- **no** ``at_ms``. It arrives roughly 200ms after
  its own ``speech_stopped``, and can arrive **after** the *next*
  ``speech_started`` if the two segments turn out to be one continued
  utterance. Matching a transcript to its segment is therefore done by
  ``item_id``, never by arrival order.

The short names ``speech_started`` / ``speech_stopped`` /
``transcription.completed`` are accepted as aliases of the three event
types above, for callers (and tests) that don't carry the full lobes
envelope (and, for those, transcripts are matched FIFO to the oldest
still-untexted segment instead of by ``item_id``).

Join rule (acceptance criterion 1): when a ``speech_started`` event's
``at_ms`` arrives less than ``gap_threshold_ms`` after the *previous*
``speech_stopped.at_ms``, the two segments are treated as one continued
utterance. Their texts are joined **in segment order** (the order the
segments started in, not the order their transcripts happened to arrive)
with a single space, and the callback fires once with the full text.

Safety rule -- nothing but a complete, gap-closed utterance is ever
emitted (acceptance criterion 2). Every path that ends a chain of
segments is one of exactly three:

1. the gap timeout elapsing via :meth:`poll` with no continuation having
   started;
2. a new segment starting after a gap at or above ``gap_threshold_ms``
   (the session itself already ended that turn); or
3. a reconnect -- a ``speech_started.at_ms`` *smaller* than the previous
   ``speech_stopped.at_ms`` (the speech session's clock restarted, so a
   short/negative "gap" would otherwise look like a continuation) or an
   explicit :meth:`reset` call.

Path 3 is different from the other two: a reconnect means the turn that
was in progress got cut off mid-sentence (lobes: "one session per
connection; no resume"), so what is buffered may well be an incomplete
half. It is therefore **discarded, never emitted** -- counted in
:attr:`dropped_reconnect` so the caller can log that it happened, with no
transcript text ever leaving this object.

A buffered utterance that would exceed ``max_buffer_chars`` is likewise
**discarded whole**, never emitted as a truncated fragment -- a partial
utterance handed to the classifier as if complete is exactly the failure
this module exists to prevent. Every further continuation segment of that
same (still-open) utterance is discarded too, until the chain is closed by
one of the three paths above; only then does state reset for the next
utterance. Each such discard is counted in :attr:`dropped_overflow`.

Finally, empty-text transcript events are dropped noise (see
``CLAUDE.md``: "Empty text transcripts are dropped noise") and never
contribute to, or by themselves start, a buffered utterance; and a late
transcript for an ``item_id`` whose segment already closed (its chain was
flushed, discarded, or reset) is orphaned and dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional

__all__ = ["TranscriptJoiner"]

_DEFAULT_GAP_THRESHOLD_MS = 500
_DEFAULT_MAX_BUFFER_CHARS = 4096

_SPEECH_STARTED_TYPES = frozenset({"input_audio_buffer.speech_started", "speech_started"})
_SPEECH_STOPPED_TYPES = frozenset({"input_audio_buffer.speech_stopped", "speech_stopped"})
_TRANSCRIPT_TYPES = frozenset(
    {
        "conversation.item.input_audio_transcription.completed",
        "transcription.completed",
    }
)


@dataclass
class _Segment:
    item_id: object
    text: Optional[str] = None


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
        exactly once per completed, non-discarded chain. This stands in
        for the not-yet-built classifier: nothing downstream is invoked
        automatically, and this module never imports or depends on it.
        It is **never** called with a fragment of a discarded (overflow
        or reconnect) utterance.
    max_buffer_chars:
        Upper bound on the buffered utterance text. Reaching it discards
        the whole in-progress utterance (see module docstring) rather
        than emitting a truncated fragment, and counts it in
        :attr:`dropped_overflow`.
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

        self._segments: List[_Segment] = []
        self._overflowed = False
        self._last_stopped_at_ms: Optional[int] = None
        # The at_ms deadline at/after which a poll() must close a pending
        # chain if no qualifying continuation has arrived by then.
        self._flush_due_at_ms: Optional[int] = None
        self._auto_item_seq = 0

        #: Count of utterances discarded whole because they would have
        #: exceeded ``max_buffer_chars``. No text from these ever reaches
        #: ``on_utterance``.
        self.dropped_overflow = 0
        #: Count of utterances discarded because a reconnect (at_ms going
        #: backwards) or an explicit :meth:`reset` cut them off mid-turn.
        #: No text from these ever reaches ``on_utterance``.
        self.dropped_reconnect = 0

    def handle_event(self, event: dict) -> None:
        """Feed one event from the speech session into the state machine."""
        event_type = event.get("type")
        if event_type in _SPEECH_STARTED_TYPES:
            self._on_speech_started(event.get("at_ms"), event.get("item_id"))
        elif event_type in _SPEECH_STOPPED_TYPES:
            self._on_speech_stopped(event.get("at_ms"))
        elif event_type in _TRANSCRIPT_TYPES:
            self._on_transcript(event.get("item_id"), event.get("text") or "")
        # Unknown event types are ignored by design (forward-compatible).

    def poll(self, now_ms: int) -> None:
        """Give the joiner the current time (an injected clock).

        If a buffered chain has been waiting for a continuation for at
        least ``gap_threshold_ms`` with none arriving, close it now (and
        emit it, unless it was already discarded for overflow). Safe to
        call at any cadence, including when nothing is buffered.
        """
        if self._flush_due_at_ms is not None and now_ms >= self._flush_due_at_ms:
            self._close_chain(emit=True)

    def reset(self) -> None:
        """Discard whatever is buffered without emitting it.

        For the lobes client to call on disconnect: "one session per
        connection; no resume" means a disconnect always cuts a turn off
        mid-sentence, exactly like a reconnect's at_ms regression, so this
        does the same discard (and counts it the same way).
        """
        self._discard_reconnect()

    def _on_speech_started(self, at_ms: Optional[int], item_id: object) -> None:
        if self._last_stopped_at_ms is not None and at_ms is not None:
            gap = at_ms - self._last_stopped_at_ms
            if gap < 0:
                # Reconnect: the session's clock restarted. Never treat
                # this as a continuation; the cut-off buffer is lost.
                self._discard_reconnect()
            elif gap >= self._gap_threshold_ms:
                # A genuine long pause: the session itself already ended
                # that turn. Close (and emit) the prior chain.
                self._close_chain(emit=True)
        self._flush_due_at_ms = None
        self._auto_item_seq += 1
        effective_item_id = item_id if item_id is not None else f"_auto_{self._auto_item_seq}"
        if not self._overflowed:
            self._segments.append(_Segment(item_id=effective_item_id))
        # else: this utterance is already being discarded for overflow;
        # don't bother tracking further segments until the chain closes.

    def _on_speech_stopped(self, at_ms: Optional[int]) -> None:
        self._last_stopped_at_ms = at_ms
        if at_ms is not None:
            self._flush_due_at_ms = at_ms + self._gap_threshold_ms
        else:
            # No timestamp to reason about a gap from: close now rather
            # than buffer indefinitely with no way to time out.
            self._flush_due_at_ms = None
            self._close_chain(emit=True)

    def _on_transcript(self, item_id: object, text: str) -> None:
        if not text:
            return  # empty-text transcripts are dropped noise
        segment = self._find_segment(item_id)
        if segment is None:
            # Either this utterance is being discarded for overflow (we
            # stopped tracking segments), or this is a late/orphan
            # transcript for a chain that already closed. Either way,
            # drop it silently.
            return
        segment.text = text if segment.text is None else f"{segment.text} {text}"
        joined = self._joined_text()
        if len(joined) > self._max_buffer_chars:
            # Discard the WHOLE utterance now -- never emit a fragment.
            # Stop tracking segments so memory is bounded immediately and
            # every further continuation of this same chain is dropped
            # too, until it closes.
            self._overflowed = True
            self.dropped_overflow += 1
            self._segments = []

    def _find_segment(self, item_id: object) -> Optional[_Segment]:
        if item_id is not None:
            for segment in self._segments:
                if segment.item_id == item_id:
                    return segment
            return None
        # No item_id on the transcript event: fall back to the oldest
        # segment still missing its text (FIFO), which matches strict
        # in-order arrival -- the only case this fallback is used for.
        for segment in self._segments:
            if segment.text is None:
                return segment
        return None

    def _joined_text(self) -> str:
        return " ".join(segment.text for segment in self._segments if segment.text)

    def _close_chain(self, emit: bool) -> None:
        text = self._joined_text()
        was_overflowed = self._overflowed
        self._segments = []
        self._overflowed = False
        self._last_stopped_at_ms = None
        self._flush_due_at_ms = None
        if was_overflowed:
            return  # already discarded; never emit any part of it
        if emit and text and self._on_utterance is not None:
            self._on_utterance(text)

    def _discard_reconnect(self) -> None:
        # Whatever was buffered is a mid-turn fragment; never emit it.
        self._close_chain(emit=False)
        self.dropped_reconnect += 1
