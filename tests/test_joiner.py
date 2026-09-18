"""Tests for the transcript joiner (pause-split sentence re-joining).

The joiner is a pure state machine over events carrying ``at_ms``:

- ``speech_started``     {"type": "speech_started", "at_ms": int}
- ``speech_stopped``     {"type": "speech_stopped", "at_ms": int}
- ``transcription.completed``  {"type": "transcription.completed", "text": str}

It never sleeps and never spawns a thread. Timeout flushing (a dangling
half whose continuation never arrives) is driven by the caller handing it
the current time via ``poll(now_ms)`` -- an injected clock, not a real one --
so tests never need to sleep.

No classifier exists yet (another task builds it). We stand in for it with
a plain list-appending callback and verify the joiner emits exactly ONE
joined utterance carrying the full text for a split hint.
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
    """A reconnect resets at_ms to a small/absolute value; must not "join"."""
    joiner, utterances = _joiner()

    joiner.handle_event({"type": "speech_started", "at_ms": 50_000})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 50_400})
    joiner.handle_event({"type": "transcription.completed", "text": "פתח"})

    # Reconnect: a new session's clock starts again near zero, so the next
    # speech_started's at_ms is *smaller* than the prior speech_stopped's.
    # This must never be treated as a same-utterance continuation.
    joiner.handle_event({"type": "speech_started", "at_ms": 10})
    joiner.handle_event({"type": "speech_stopped", "at_ms": 300})
    joiner.handle_event({"type": "transcription.completed", "text": "את הדלת"})

    joiner.poll(now_ms=300 + GAP_MS + 1)

    # Two separate utterances -- never "פתח את הדלת" as if unbroken.
    assert utterances == ["פתח", "את הדלת"]
    assert "פתח את הדלת" not in utterances


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
    """A runaway buffer (many joined segments) never grows unboundedly."""
    joiner, utterances = _joiner(max_buffer_chars=32)

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    at_ms = 0
    for i in range(50):
        stopped = at_ms + 100
        joiner.handle_event({"type": "speech_stopped", "at_ms": stopped})
        joiner.handle_event({"type": "transcription.completed", "text": "מילה" * 5})
        at_ms = stopped + 10  # well under threshold -> would keep joining
        joiner.handle_event({"type": "speech_started", "at_ms": at_ms})

    joiner.handle_event({"type": "speech_stopped", "at_ms": at_ms + 100})
    joiner.poll(now_ms=at_ms + 100 + GAP_MS + 1)

    # The joiner must never have held an unbounded string in memory; each
    # emitted utterance (there may be more than one, since bounding forces
    # early flushes) stays within a small bound of the configured cap.
    assert utterances
    for utterance in utterances:
        assert len(utterance) <= 32 + len("מילה" * 5) + 1


def test_no_utterance_before_stop_event_seen() -> None:
    """Polling with nothing buffered and no pending stop is a no-op."""
    joiner, utterances = _joiner()

    joiner.poll(now_ms=10_000)
    assert utterances == []

    joiner.handle_event({"type": "speech_started", "at_ms": 0})
    joiner.poll(now_ms=10_000)
    assert utterances == []
