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
- an **injected clock**, in two places:

  - a **receive clock** (constructor's ``clock``: a zero-arg callable
    returning milliseconds, default ``time.monotonic`` based) that the
    joiner samples itself, once, whenever a boundary event is handled --
    its *local receive time*. This is what a gap is computed from
    whenever the wire's own ``at_ms`` is missing on either side of the
    gap (see below): ``lobes/realtime/_session.py`` declares boundary
    events' ``at_ms`` as ``int | None = None``, so a missing ``at_ms`` is
    a legitimate wire shape, not an error, and must never be treated as
    "the gap is large" -- that would split one sentence's two ASR halves
    into two utterances, and a standalone second half can mean the
    *opposite* of what the speaker intended (e.g. "הלוואי ש" + "היה קר"
    -> the standalone remark "היה קר").
  - the caller periodically calling :meth:`TranscriptJoiner.poll`, either
    with an explicit ``now_ms`` (for ``at_ms``-driven timeouts) or with no
    argument at all, in which case the joiner reads its own injected
    receive clock. Either way, the joiner decides then whether a pending
    chain has waited long enough with no continuation to close it. Tests
    drive both forms directly (an explicit ``now_ms``, or a hand-advanced
    fake receive clock), so no real sleeping is ever required.

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

Join rule (acceptance criterion 1): a ``speech_started`` continues the
previous chain (its text joins the prior segments' rather than starting a
new utterance) when the gap since the previous ``speech_stopped`` is below
``gap_threshold_ms``. That gap is computed:

- from ``at_ms`` -- ``speech_started.at_ms - speech_stopped.at_ms`` --
  **only when both events carried an** ``at_ms``;
- otherwise, from the joiner's own local receive timestamps for those same
  two events (sampled from the injected receive clock when each event was
  handled). This covers ``at_ms`` missing on *either* side: both events
  missing it, or only one of them (e.g. the stop had ``at_ms`` but the
  next start did not -- the wire can mix the two within one session).

Segments joined this way have their texts concatenated **in segment
order** (the order the segments started in, not the order their
transcripts happened to arrive) with a single space, and the callback
fires once with the full text.

Safety rule -- nothing but a complete, gap-closed utterance is ever
emitted (acceptance criterion 2). Every path that ends a chain of
segments is one of exactly three:

1. the gap timeout elapsing via :meth:`poll` with no continuation having
   started -- on whichever timeline (``at_ms`` or local receive time) the
   pending ``speech_stopped`` used to set its deadline;
2. a new segment starting after a gap (computed as above) at or above
   ``gap_threshold_ms`` (the session itself already ended that turn); or
3. a reconnect -- a ``speech_started.at_ms`` *smaller* than the previous
   ``speech_stopped.at_ms`` -- **only evaluated when both carried an**
   ``at_ms``, since a missing ``at_ms`` is normal wire behaviour, not a
   reconnect signal -- or an explicit :meth:`reset` call.

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

Speech-start instant (strict-window-close-boundary, t1) --- a downstream
mode decision must be able to ask "what was in force when this utterance's
speech STARTED", not just "what is in force now" (a decision can complete
after a strict window has closed). The receive clock this module already
samples on every ``speech_started`` (``local_now`` above) is monotonic-only
by contract ("It need not be wall-clock time" --- see ``clock`` below), so
it cannot by itself be compared against a zmanim window. This module
therefore also samples one genuine wall-clock reading (``wall_clock``,
default :func:`time.time`) at the same instant, for the *first* segment of
each chain only --- continuations of an already-open chain never move the
utterance's start. The pair is exposed as :class:`SpeechStart` via
:attr:`TranscriptJoiner.last_utterance_start`, set immediately before
``on_utterance`` fires so a caller's callback can read it synchronously.
Nothing here consumes it: this module only threads it through.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, List, Optional

__all__ = ["SpeechStart", "TranscriptJoiner"]

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


def _default_clock() -> float:
    return time.monotonic() * 1000.0


@dataclass(frozen=True)
class SpeechStart:
    """The instant an utterance's speech STARTED --- not when it was joined,
    not when its transcript arrived, and not re-derived from either.

    ``monotonic_ms`` is the joiner's own local receive clock (whatever was
    injected as ``clock``; monotonic by contract, not necessarily wall-clock)
    sampled at the same moment as ``wall_time``, which is always a genuine
    wall-clock reading (seconds since the epoch) suitable for comparison
    against a zmanim window. Both are sampled once, on the FIRST
    ``speech_started`` of the chain that produced the utterance.
    """

    monotonic_ms: float
    wall_time: float


@dataclass
class _Segment:
    item_id: object
    text: Optional[str] = None


class TranscriptJoiner:
    """Joins pause-split transcript halves into single utterances.

    Parameters
    ----------
    gap_threshold_ms:
        The maximum silence gap (in milliseconds, on whichever timeline --
        ``at_ms`` or local receive time -- the gap ends up computed on)
        between one segment's ``speech_stopped`` and the next segment's
        ``speech_started`` that still counts as one continued utterance.
        Must match (or be looser than) the speech session's own
        silence-based turn-ending window, since a gap at or above it means
        the session itself already ended the turn.
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
    clock:
        Zero-arg callable returning the current time in milliseconds,
        used ONLY as a local *receive clock* -- sampled once whenever a
        boundary event is handled -- to compute gaps and timeouts when the
        wire's own ``at_ms`` is missing on either side. Defaults to a
        ``time.monotonic``-based clock. Tests inject a fake, hand-advanced
        one instead of sleeping. It "need not be wall-clock time", which is
        exactly why ``wall_clock`` below exists separately.
    wall_clock:
        Zero-arg callable returning the current wall-clock time in seconds
        (default :func:`time.time`), sampled once on the first
        ``speech_started`` of each chain to build :class:`SpeechStart`. Never
        used for any gap, timeout or reconnect decision -- those still run
        entirely on ``clock``/``at_ms`` as before. Tests inject a fake here
        too, so nothing in this module ever needs to sleep.
    """

    def __init__(
        self,
        gap_threshold_ms: int = _DEFAULT_GAP_THRESHOLD_MS,
        on_utterance: Optional[Callable[[str], None]] = None,
        max_buffer_chars: int = _DEFAULT_MAX_BUFFER_CHARS,
        clock: Callable[[], float] = _default_clock,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if gap_threshold_ms <= 0:
            raise ValueError("gap_threshold_ms must be positive")
        if max_buffer_chars <= 0:
            raise ValueError("max_buffer_chars must be positive")
        self._gap_threshold_ms = gap_threshold_ms
        self._on_utterance = on_utterance
        self._max_buffer_chars = max_buffer_chars
        self._clock = clock
        self._wall_clock = wall_clock

        self._segments: List[_Segment] = []
        self._overflowed = False
        # The (monotonic, wall-clock) instant of the chain currently open,
        # sampled once on its first speech_started. None while no chain is
        # open. Never touched by a continuation of an already-open chain.
        self._chain_start: Optional[SpeechStart] = None
        #: The :class:`SpeechStart` of the utterance most recently handed to
        #: ``on_utterance`` -- set immediately before that call, so a
        #: synchronous callback can read it. ``None`` before the first
        #: emitted utterance; never set for a discarded (overflow or
        #: reconnect) chain, since those never emit at all.
        self.last_utterance_start: Optional[SpeechStart] = None
        self._last_stopped_at_ms: Optional[int] = None
        # Local receive-time stamp of the last speech_stopped, sampled
        # from the injected clock -- tracked unconditionally (even when
        # at_ms is also present) so a *later* event missing at_ms can
        # still fall back to it.
        self._last_stopped_local_ms: Optional[float] = None
        # Exactly one of these two is set while a chain is pending,
        # matching whichever timeline the pending speech_stopped's gap
        # timeout was computed on.
        self._flush_due_at_ms: Optional[int] = None
        self._flush_due_local_ms: Optional[float] = None
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

    def poll(self, now_ms: Optional[int] = None) -> None:
        """Give the joiner the current time (an injected clock).

        If a buffered chain has been waiting for a continuation for at
        least ``gap_threshold_ms`` with none arriving, close it now (and
        emit it, unless it was already discarded for overflow). Safe to
        call at any cadence, including when nothing is buffered.

        ``now_ms``, when given, is compared against an ``at_ms``-timeline
        deadline (the pending ``speech_stopped`` carried an ``at_ms``).
        When the pending deadline is on the local receive-time timeline
        instead (the ``speech_stopped`` had no ``at_ms``), ``now_ms`` is
        used if given, otherwise the joiner samples its own injected
        receive clock.
        """
        if self._flush_due_at_ms is not None:
            if now_ms is not None and now_ms >= self._flush_due_at_ms:
                self._close_chain(emit=True)
            return
        if self._flush_due_local_ms is not None:
            effective_now = now_ms if now_ms is not None else self._clock()
            if effective_now >= self._flush_due_local_ms:
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
        local_now = self._clock()
        if self._last_stopped_at_ms is not None and at_ms is not None:
            # Both sides carry at_ms: use it, including reconnect
            # detection (a regression is only meaningful on this
            # timeline).
            gap = at_ms - self._last_stopped_at_ms
            if gap < 0:
                # Reconnect: the session's clock restarted. Never treat
                # this as a continuation; the cut-off buffer is lost.
                self._discard_reconnect()
            elif gap >= self._gap_threshold_ms:
                # A genuine long pause: the session itself already ended
                # that turn. Close (and emit) the prior chain.
                self._close_chain(emit=True)
        elif self._last_stopped_local_ms is not None:
            # at_ms missing on one or both sides: fall back to local
            # receive-time gap. A missing at_ms is a legitimate wire shape
            # (lobes/realtime/_session.py: `at_ms: int | None = None`), so
            # this must never be treated as "the gap is large" by
            # default -- only an actually-long receive-time gap closes
            # the chain. No reconnect check here: a monotonic receive
            # clock cannot regress the way a restarted session's at_ms
            # can, and the reconnect rule applies only when both sides
            # carried at_ms.
            local_gap = local_now - self._last_stopped_local_ms
            if local_gap >= self._gap_threshold_ms:
                self._close_chain(emit=True)
        self._flush_due_at_ms = None
        self._flush_due_local_ms = None
        self._auto_item_seq += 1
        effective_item_id = item_id if item_id is not None else f"_auto_{self._auto_item_seq}"
        if not self._overflowed:
            if not self._segments:
                # The first segment of a brand-new chain: this is the
                # utterance's speech-start instant. A later continuation of
                # this same chain (segments already non-empty) must never
                # move it.
                self._chain_start = SpeechStart(
                    monotonic_ms=local_now, wall_time=self._wall_clock()
                )
            self._segments.append(_Segment(item_id=effective_item_id))
        # else: this utterance is already being discarded for overflow;
        # don't bother tracking further segments until the chain closes.

    def _on_speech_stopped(self, at_ms: Optional[int]) -> None:
        local_now = self._clock()
        self._last_stopped_at_ms = at_ms
        self._last_stopped_local_ms = local_now
        if at_ms is not None:
            self._flush_due_at_ms = at_ms + self._gap_threshold_ms
            self._flush_due_local_ms = None
        else:
            # No at_ms to reason about a gap/timeout from: use the local
            # receive clock instead of closing immediately -- a missing
            # at_ms must never look like "the continuation never arrives
            # in time" any sooner than a real timeout does.
            self._flush_due_at_ms = None
            self._flush_due_local_ms = local_now + self._gap_threshold_ms

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
        chain_start = self._chain_start
        self._segments = []
        self._overflowed = False
        self._chain_start = None
        self._last_stopped_at_ms = None
        self._last_stopped_local_ms = None
        self._flush_due_at_ms = None
        self._flush_due_local_ms = None
        if was_overflowed:
            return  # already discarded; never emit any part of it
        if emit and text and self._on_utterance is not None:
            # Set immediately before the call so a synchronous callback
            # (the only kind this module supports; see the class docstring)
            # can read the instant for the utterance it is about to handle.
            self.last_utterance_start = chain_start
            self._on_utterance(text)

    def _discard_reconnect(self) -> None:
        # Whatever was buffered is a mid-turn fragment; never emit it.
        self._close_chain(emit=False)
        self.dropped_reconnect += 1
