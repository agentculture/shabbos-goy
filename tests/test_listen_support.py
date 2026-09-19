"""Shared, fixtures-only helpers for the ``listen`` runtime tests.

Nothing here touches a microphone, a real lobes server, a Sensibo account,
the host's real volume or any address but ``127.0.0.1``. The decider is a
recorded replay, the actuators are fakes, the audio-stream clock is
hand-advanced, and every server binds an ephemeral loopback port.

Named ``test_listen_support`` only because this task owns ``tests/test_listen*``
(and the brief names that glob); it holds no tests of its own.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from shabbos_goy import mode as mode_module
from shabbos_goy.decider import ReplayDecider
from shabbos_goy.runtime import Listener, ListenerOptions

from .test_web_support import (  # noqa: F401 - re-exported for the listen tests
    NOW_STRICT,
    POD,
    VOLUME_KEY,
    FakeAC,
    FakeVolume,
    make_config,
    request,
    timedatectl_says_synced,
)

MARKER_TEXT = "MARKER7QX"
HOT = "חם פה"
HOT_MARKED = f"{HOT} {MARKER_TEXT}"
IMPERATIVE = "תדליק את המזגן"

REPLAY = Path(__file__).parent / "fixtures" / "pipeline" / "replay.json"

#: A Wednesday morning in Jerusalem: weekday, well outside any window.
NOW_WEEKDAY = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


class AudioClock:
    """A hand-advanced monotonic clock in seconds (the listener's clock)."""

    def __init__(self, now: float = 1_000.0) -> None:
        self._now = now
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += seconds


class FakeVolumeReader:
    def __init__(self, level: float = 0.0, muted: bool = True) -> None:
        self.level = level
        self.muted = muted
        self.calls = 0

    def __call__(self) -> dict:
        self.calls += 1
        return {"level": self.level, "muted": self.muted}


def utterance_events(text: str, *, item_id: str, start_ms: int) -> list[dict]:
    """One complete speech turn, as lobes puts it on the wire."""
    return [
        {"type": "input_audio_buffer.speech_started", "at_ms": start_ms, "item_id": item_id},
        {"type": "input_audio_buffer.speech_stopped", "at_ms": start_ms + 800, "item_id": item_id},
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "text": text,
        },
    ]


def write_events_file(path: Path, texts: list[str]) -> Path:
    lines: list[str] = []
    for index, text in enumerate(texts, start=1):
        for event in utterance_events(text, item_id=f"i{index}", start_ms=index * 10_000):
            lines.append(json.dumps(event, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class ScriptedSource:
    """A listener source that submits a fixed event list, then drains.

    Stands exactly where the real lobes reader thread stands, so the rest of
    the listener (queue, worker, ticker, servers) is the production code.
    """

    def __init__(self, events: list[dict], *, drain: bool = True) -> None:
        self.events = events
        self.drain = drain
        self.started = threading.Event()
        self.finished = threading.Event()

    def __call__(self, listener: Listener) -> None:
        self.started.set()
        for event in self.events:
            if listener.stopping:
                break
            listener.submit(event)
        if self.drain:
            listener.submit_drain()
        self.finished.set()
        listener.wait_for_stop()


def make_listener(
    tmp_path: Path,
    *,
    events: list[dict] | None = None,
    source: Callable[[Listener], None] | None = None,
    options: ListenerOptions | None = None,
    config_overrides: dict[str, Any] | None = None,
    ac: FakeAC | None = None,
    volume: FakeVolume | None = None,
    volume_get: Callable[[], Any] | None = None,
    clock: Callable[[], float] | None = None,
    now: datetime | None = None,
    **overrides: Any,
) -> Listener:
    """A real :class:`Listener` over fakes, not started."""
    config = overrides.pop("config", None)
    if config is None:
        config = make_config(tmp_path, **(config_overrides or {}))
    ac = ac if ac is not None else FakeAC()
    volume = volume if volume is not None else FakeVolume()
    clock = clock if clock is not None else AudioClock()
    now = now if now is not None else NOW_WEEKDAY
    if source is None:
        source = ScriptedSource(list(events or []))
    if options is None:
        options = ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01)

    def mode_provider():
        return mode_module.resolve_mode(now, config, runner=timedatectl_says_synced)

    kwargs: dict[str, Any] = dict(
        config=config,
        decider=ReplayDecider.from_file(REPLAY),
        options=options,
        source=source,
        mode_provider=mode_provider,
        pod_id=POD,
        ac_power=ac.power,
        ac_status=ac.status,
        volume_step=volume,
        volume_get=volume_get,
        clock=clock,
        now_provider=lambda: now,
        heartbeat_path=tmp_path / "heartbeat.json",
    )
    kwargs.update(overrides)
    listener = Listener(**kwargs)
    listener.fakes = {"ac": ac, "volume": volume, "clock": clock, "config": config}  # type: ignore
    return listener


@contextmanager
def running(listener: Listener, *, timeout: float = 10.0) -> Iterator[Listener]:
    """Start ``listener``, always stop it, and never leave a thread behind."""
    listener.start()
    try:
        yield listener
    finally:
        listener.stop(timeout=timeout)
        alive = [t.name for t in listener.threads() if t.is_alive()]
        assert alive == [], f"threads still alive after stop(): {alive}"


def wait_until(predicate: Callable[[], bool], timeout: float = 10.0) -> bool:
    """Poll ``predicate`` without sleeping the whole timeout away."""
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if predicate():
            return True
        _time.sleep(0.005)
    return predicate()


def verdicts(listener: Listener) -> list[tuple[str, str]]:
    return [(r.verdict, r.action) for r in listener.pipeline.log_records]


def note_events(listener: Listener) -> list[str]:
    return [note.event for note in listener.notes]
