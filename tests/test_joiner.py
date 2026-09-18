"""Tests for the transcript joiner (pause-split sentence re-joining).

The joiner is a pure state machine over the real lobes/OpenAI-Realtime
event names (``lobes-cli``'s ``docs/contracts/realtime-tool-calling.md``):

- ``input_audio_buffer.speech_started``  {"at_ms": int, "item_id": str}
- ``input_audio_buffer.speech_stopped``  {"at_ms": int, "item_id": str}
- ``conversation.item.input_audio_transcription.completed``
  {"item_id": str, "text": str} -- no ``at_ms``.

Most tests below use the short aliases (``speech_started`` /
``speech_stopped`` / ``transcription.completed``, with no ``item_id``) for
brevity where item_id-based matching isn't what's under test; one test
below uses the full real event names and item_ids explicitly to prove
out-of-order transcript arrival still joins correctly.

The joiner never sleeps and never spawns a thread. Timeout flushing (a
dangling half whose continuation never arrives) is driven by the caller
handing it the current time via ``poll(now_ms)`` -- an injected clock, not
a real one -- so tests never need to sleep.

No classifier exists yet (another task builds it). We stand in for it with
a plain list-appending callback and verify the joiner emits exactly ONE
joined utterance carrying the full text for a split hint, and that it
NEVER emits any part of a discarded (overflow or reconnect/reset)
utterance -- a fragment reaching that callback is exactly the failure this
module exists to prevent.
"""

from __future__ import annotations

from shabbos_goy.joiner import TranscriptJoiner

GAP_MS = 500


def _joiner(threshold_ms: int = GAP_MS, max_buffer_chars: int = 4096):
    utterances: list[str] = []
    joiner = TranscriptJoiner(
        gap_threshold_ms=threshold_ms,
        on_utterance=utterances.append,
        max_buffer_chars=max_buffer_chars,
    )
    return joiner, utterances


def test_split_hint_classifies_as_one_utterance() -> None:
    """A hint the ASR splits across a short pause joins into one utterance."""
    joiner, utterances = _joiner()

    # First half: "חם פה" (it's hot here), ends at 400ms.
    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": "חם פה"})

    # Second half starts 100ms later (< 500ms threshold) -> continuation.
    joiner.handle_event({"type": "speech_started", "at_ms": 500})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 900})
    joiner.handle_event({"type": "transcription.completed", "text": "מאוד"})

    # Nothing else arrives; caller polls well past the gap threshold.
    joiner.poll(now_ms=900 + GAP_MS + 1)

    assert utterances == ["חם פה מאוד"]


def test_no_continuation_is_a_single_short_utterance() -> None:
    """Two unrelated remarks separated by a long pause are NOT joined."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": "חם פה"})

    # Long gap: 2000ms, well over the 500ms threshold -> not a continuation.
    joiner.handle_event({"type": "speech_started", "at_ms": 2400})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 2800})
    joiner.handle_event({"type": "transcription.completed", "text": "קר פה"})

    joiner.poll(now_ms=2800 + GAP_MS + 1)

    assert utterances == ["חם פה", "קר פה"]


def test_dangling_half_never_reaches_classifier_when_second_half_never_arrives() -> None:
    """If the continuation never shows up, the timeout flush still fires once."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": "תדליק"})

    # No second speech_started ever arrives. Caller polls periodically;
    # before the threshold elapses nothing should flush yet.
    joiner.poll(now_ms=400 + GAP_MS - 1)
    assert utterances == []

    # Once the threshold has elapsed with no continuation, flush exactly once.
    joiner.poll(now_ms=400 + GAP_MS + 1)
    assert utterances == ["תדליק"]

    # Further polling must not re-emit the same (already flushed) utterance.
    joiner.poll(now_ms=400 + GAP_MS + 10_000)
    assert utterances == ["תדליק"]


def test_dangling_half_not_joined_after_reconnect_at_ms_restarts() -> None:
    """A reconnect resets at_ms; the cut-off half must be DISCARDED, not
    emitted -- the turn it belonged to never really finished (lobes: "one
    session per connection; no resume"), so it may be an incomplete
    fragment, and a fragment must never reach the classifier."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 50_000})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 50_400})
    joiner.handle_event({"type": "transcription.completed", "text": "פתח"})

    # Reconnect: a new session's clock starts again near zero, so the next
    # speech_started's at_ms is *smaller* than the prior speech_stopped's.
    # This must never be treated as a same-utterance continuation, AND the
    # pre-reconnect half must never be emitted at all.
    joiner.handle_event({"type": "speech_started", "at_ms": 10})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 300})
    joiner.handle_event({"type": "transcription.completed", "text": "את הדלת"})

    joiner.poll(now_ms=300 + GAP_MS + 1)

    # "פתח" is gone -- never emitted, alone or joined -- and the new
    # (post-reconnect) segment is emitted on its own once it closes.
    assert utterances == ["את הדלת"]
    assert "פתח" not in utterances
    assert "פתח את הדלת" not in utterances
    assert joiner.dropped_reconnect == 1


def test_reset_discards_pending_without_emitting() -> None:
    """An explicit reset() (lobes client on disconnect) discards silently."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": "תדליק"})

    joiner.reset()

    # Even after the threshold would otherwise have elapsed, nothing is
    # emitted: reset() already discarded it.
    joiner.poll(now_ms=400 + GAP_MS + 1)
    assert utterances == []
    assert joiner.dropped_reconnect == 1

    # The joiner is usable again afterwards for a fresh utterance.
    joiner.handle_event({"type": "speech_started", "at_ms": 10_000})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 10_400})
    joiner.handle_event({"type": "transcription.completed", "text": "סגור"})
    joiner.poll(now_ms=10_400 + GAP_MS + 1)
    assert utterances == ["סגור"]


def test_empty_text_transcripts_are_ignored() -> None:
    """An empty-text transcription.completed event contributes nothing."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": ""})

    joiner.poll(now_ms=400 + GAP_MS + 1)

    # Nothing buffered, nothing flushed -- an empty utterance never reaches
    # the (stand-in) classifier callback.
    assert utterances == []


def test_empty_text_mid_join_does_not_break_the_join() -> None:
    """A stray empty transcript between two real halves must not lose text."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 400})
    joiner.handle_event({"type": "transcription.completed", "text": "חם"})
    joiner.handle_event({"type": "transcription.completed", "text": ""})

    joiner.handle_event({"type": "speech_started", "at_ms": 450})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 900})
    joiner.handle_event({"type": "transcription.completed", "text": "פה"})

    joiner.poll(now_ms=900 + GAP_MS + 1)

    assert utterances == ["חם פה"]


def test_buffer_size_is_bounded() -> None:
    """An over-long joined utterance is discarded whole -- NEVER emitted as
    a truncated fragment, since a fragment handed to the classifier as if
    complete is exactly what this module must prevent."""
    joiner, utterances = _joiner(max_buffer_chars=32)

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    at_ms = 0
    for _ in range(50):
        stopped = at_ms + 100
        joiner.handle_event({"type": "speech_stopped", "at_ms": stopped})
        joiner.handle_event({"type": "transcription.completed", "text": "מילה" * 5})
        at_ms = stopped + 10  # well under threshold -> would keep joining
        joiner.handle_event({"type": "speech_started", "at_ms": at_ms})

    joiner.handle_event({"type": "speech_stopped", "at_ms": at_ms + 100})
    joiner.poll(now_ms=at_ms + 100 + GAP_MS + 1)

    # Zero emissions for the over-long utterance -- no fragment of it ever
    # reached the (stand-in) classifier -- and the discard is counted.
    assert utterances == []
    assert joiner.dropped_overflow == 1

    # A normal short utterance arriving afterwards, separated by a gap
    # over the threshold, is still emitted normally: overflow of one
    # utterance must not wedge the joiner for the next one.
    joiner.handle_event({"type": "speech_started", "at_ms": at_ms + 100 + GAP_MS + 500})
    joiner.handle_event({"type": "speech_stopped", "at_ms": at_ms + 100 + GAP_MS + 900})
    joiner.handle_event({"type": "transcription.completed", "text": "חם פה"})
    joiner.poll(now_ms=at_ms + 100 + GAP_MS + 900 + GAP_MS + 1)

    assert utterances == ["חם פה"]


def test_late_transcript_after_next_speech_started_still_joins_in_order() -> None:
    """The real lobes contract: transcription.completed carries item_id and
    NO at_ms, arrives ~200ms after its own speech_stopped, and CAN arrive
    after the next speech_started. The joiner must match text to its
    segment by item_id and join in segment (start) order, not arrival
    order."""
    joiner, utterances = _joiner()

    joiner.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": 0, "item_id": "item_1"}
    )
    joiner.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": 400, "item_id": "item_1"}
    )
    # Short gap: the next segment starts before item_1's transcript arrives.
    joiner.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": 450, "item_id": "item_2"}
    )
    # item_1's transcript arrives late, AFTER item_2's speech_started.
    joiner.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_1",
            "text": "חם",
        }
    )
    joiner.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": 900, "item_id": "item_2"}
    )
    joiner.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": "item_2",
            "text": "פה",
        }
    )

    joiner.poll(now_ms=900 + GAP_MS + 1)

    # Joined in segment (start) order -- "חם" then "פה" -- despite item_1's
    # text arriving after item_2's speech_started.
    assert utterances == ["חם פה"]


def test_no_utterance_before_stop_event_seen() -> None:
    """Polling with nothing buffered and no pending stop is a no-op."""
    joiner, utterances = _joiner()

    joiner.poll(now_ms=10_000)
    assert utterances == []

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.poll(now_ms=10_000)
    assert utterances == []
