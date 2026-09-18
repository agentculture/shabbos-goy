"""The rolling context window: bounded by count, by age and by rendered size.

The window holds transcript text, so every test here also asserts the
privacy contract: nothing is written to disk, a new window is empty (that
is what "gone on restart" means for a process that persists nothing), and
``clear()`` empties it for a reconnect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shabbos_goy.decider import ContextWindow, Decision


class FakeClock:
    """A monotonic clock a test drives by hand: no real sleeping anywhere."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _decision(klass: str = "remark", intent: str = "cool") -> Decision:
    return Decision(klass=klass, intent=intent, confidence=0.8, source="test", reason="fixture")


def test_new_window_is_empty_and_renders_empty() -> None:
    window = ContextWindow(max_items=4, max_age_seconds=600, clock=FakeClock())
    assert len(window) == 0
    assert window.render() == ""


def test_trims_by_item_count_keeping_the_newest() -> None:
    clock = FakeClock()
    window = ContextWindow(max_items=3, max_age_seconds=3600, clock=clock)
    for i in range(10):
        clock.advance(1.0)
        window.add(f"utterance-{i}", _decision())
    assert len(window) == 3
    rendered = window.render()
    assert "utterance-9" in rendered
    assert "utterance-7" in rendered
    assert "utterance-6" not in rendered


def test_trims_by_age() -> None:
    clock = FakeClock()
    window = ContextWindow(max_items=50, max_age_seconds=100, clock=clock)
    window.add("old", _decision())
    clock.advance(101.0)
    window.add("fresh", _decision())
    rendered = window.render()
    assert "fresh" in rendered
    assert "old" not in rendered
    assert len(window) == 1


def test_age_trim_happens_on_render_even_without_new_items() -> None:
    clock = FakeClock()
    window = ContextWindow(max_items=50, max_age_seconds=10, clock=clock)
    window.add("stale", _decision())
    clock.advance(11.0)
    assert window.render() == ""
    assert len(window) == 0


def test_render_is_capped_and_drops_oldest_first() -> None:
    clock = FakeClock()
    window = ContextWindow(max_items=100, max_age_seconds=10_000, max_render_chars=120, clock=clock)
    for i in range(40):
        clock.advance(1.0)
        window.add(f"line-{i}-" + "x" * 20, _decision())
    rendered = window.render()
    assert len(rendered) <= 120
    assert "line-39-" in rendered
    assert "line-0-" not in rendered


def test_render_carries_class_and_intent_of_each_entry() -> None:
    window = ContextWindow(max_items=4, max_age_seconds=600, clock=FakeClock())
    window.add("חם פה", _decision(klass="remark", intent="cool"))
    rendered = window.render()
    assert "remark" in rendered
    assert "cool" in rendered
    assert "חם פה" in rendered


def test_clear_empties_the_window() -> None:
    window = ContextWindow(max_items=4, max_age_seconds=600, clock=FakeClock())
    window.add("something", _decision())
    window.clear()
    assert len(window) == 0
    assert window.render() == ""


def test_repr_never_contains_transcript_text() -> None:
    window = ContextWindow(max_items=4, max_age_seconds=600, clock=FakeClock())
    window.add("MARKER-SECRET-UTTERANCE", _decision())
    assert "MARKER" not in repr(window)
    assert "MARKER" not in str(window)


def test_window_writes_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    clock = FakeClock()
    window = ContextWindow(max_items=8, max_age_seconds=600, clock=clock)
    for i in range(50):
        clock.advance(1.0)
        window.add(f"MARKER-{i}", _decision())
    window.render()
    assert list(tmp_path.iterdir()) == []


def test_invalid_bounds_are_rejected() -> None:
    with pytest.raises(ValueError):
        ContextWindow(max_items=0, max_age_seconds=10)
    with pytest.raises(ValueError):
        ContextWindow(max_items=4, max_age_seconds=0)
    with pytest.raises(ValueError):
        ContextWindow(max_items=4, max_age_seconds=10, max_render_chars=0)


def test_soak_25_hours_stays_bounded() -> None:
    """A synthetic 25h+ stream: count, rendered size and memory stay bounded.

    Simulated time only — the injected clock advances 30 s per utterance, so
    3200 utterances cover more than 26 hours in milliseconds of real time.
    """
    clock = FakeClock()
    window = ContextWindow(max_items=10, max_age_seconds=900, max_render_chars=600, clock=clock)
    utterances = 3200
    for i in range(utterances):
        clock.advance(30.0)
        window.add(f"utterance-{i} " + "פה חם מאוד", _decision())
        assert len(window) <= 10
        assert len(window.render()) <= 600
    assert clock.now / 3600.0 > 25.0
    rendered = window.render()
    assert f"utterance-{utterances - 1}" in rendered
    # The internal buffer is bounded too, not just the rendered view.
    assert len(list(window.entries())) <= 10
