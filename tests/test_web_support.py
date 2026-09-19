"""Shared, fixtures-only helpers for the dashboard tests.

Nothing here touches a microphone, a lobes server, a Sensibo account or the
host's real volume: the decider is a stub, the actuators are fakes, every
clock is hand-advanced, and every server binds 127.0.0.1 on an ephemeral
port (``:0``).

This module is named ``test_web_support`` only because the task owns
``tests/test_web_*.py``; it holds no tests of its own.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from shabbos_goy import mode as mode_module
from shabbos_goy.config import load_config
from shabbos_goy.decider import Decision
from shabbos_goy.pipeline import Pipeline

POD = "PODFAKE1"
VOLUME_KEY = "self"

#: Markers used by the key-leak test. Not keys: just improbable strings that
#: a naive implementation (dumping ``config.raw`` or ``os.environ``) would
#: echo back.
MARKER_SENSIBO_KEY = "SENSIBOKEYMARKERQQ01"
MARKER_LOBES_KEY = "LOBESKEYMARKERQQ02"
MARKER_TEXT = "MARKER7QX"

HOT = "חם פה"
NOISY = "רועש פה מדי"

#: A Friday afternoon in Jerusalem, well outside any window (weekday), and a
#: Friday night inside one (strict). Both timezone-aware, as the zmanim code
#: requires.
NOW_WEEKDAY = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)  # a Wednesday
NOW_STRICT = datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc)  # Friday night


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


class FakeClock:
    """A hand-advanced monotonic clock (seconds)."""

    def __init__(self, now: float = 10_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeAC:
    """Stands in for ``shabbos_goy.actuators.sensibo`` -- never runs sensibo."""

    def __init__(self, state: str = "off") -> None:
        self.state = state
        self.power_calls: list[tuple[str, bool, bool]] = []
        self.status_calls = 0
        self.raises: Exception | None = None
        self.status_raises: Exception | None = None

    def power(self, pod_id: str, on: bool, *, apply: bool = False) -> dict:
        self.power_calls.append((pod_id, on, apply))
        if self.raises is not None:
            raise self.raises
        if apply:
            self.state = "on" if on else "off"
        return {"acted": bool(apply), "requested_apply": apply, "changes": {}}

    def status(self, pod_id: str) -> dict:
        self.status_calls += 1
        if self.status_raises is not None:
            raise self.status_raises
        return {"power": self.state, "temperature": 28.0, "humidity": 41.0}


class FakeVolume:
    def __init__(self) -> None:
        self.steps: list[int] = []
        self.raises: Exception | None = None

    def __call__(self, steps: int) -> float:
        self.steps.append(steps)
        if self.raises is not None:
            raise self.raises
        return 0.5


class StubDecider:
    """Labels every utterance the same way, with a named source."""

    source = "stub:p1"

    def __init__(self, answer: Decision | None = None) -> None:
        self.answer = answer if answer is not None else remark("cool")
        self.calls: list[str] = []

    def decide(self, utterance, context, *, mode, ac_state=None):
        self.calls.append(utterance)
        return self.answer


def remark(intent: str = "cool", confidence: float = 0.9) -> Decision:
    return Decision(
        klass="remark", intent=intent, confidence=confidence, source="stub:p1", reason="ok"
    )


@dataclass
class _Completed:
    returncode: int
    stdout: str
    stderr: str = ""


def timedatectl_says_synced(argv, **kwargs):
    """A ``subprocess.run`` stand-in: the clock is NTP-synced. Never runs anything."""
    return _Completed(returncode=0, stdout="yes\n")


def timedatectl_says_unsynced(argv, **kwargs):
    return _Completed(returncode=0, stdout="no\n")


# ---------------------------------------------------------------------------
# config + pipeline
# ---------------------------------------------------------------------------

_CONFIG: dict[str, Any] = {
    "location": {"lat": 31.78, "lon": 35.22, "timezone": "Asia/Jerusalem"},
    "candle_lighting_offset_minutes": 18,
    "tzeit_definition": "3_medium_stars",
    "region": "israel",
    "whitelist": {
        "sensibo": {"pods": [POD], "actions": {"power": ["on", "off"]}},
        "volume": {"pods": [VOLUME_KEY], "actions": {"step": ["up", "down"]}},
    },
    "rate_limits": {"min_interval_seconds": 600, "daily_cap": 12},
    "strict_mode_delay_seconds": 15,
    "min_confidence": 0.6,
    "join_gap_ms": 500,
    "ring_sizes": {"transcript_buffer": 20, "action_log": 200},
    "dashboard_bind_address": "127.0.0.1:0",
    # Deliberately planted secrets: an operator who put their keys in the
    # config file must still never see them echoed by the dashboard.
    "sensibo_api_key": MARKER_SENSIBO_KEY,
    "lobes": {"api_key": MARKER_LOBES_KEY, "url": "ws://<lobes-host>:8001/v1/realtime"},
}


def write_config(tmp_path: Path, **overrides: Any) -> Path:
    data = json.loads(json.dumps(_CONFIG))
    data.update(overrides)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def make_config(tmp_path: Path, **overrides: Any):
    config = load_config(path=write_config(tmp_path, **overrides))
    assert config.ok, config.error
    return config


@dataclass
class Stack:
    """Everything one dashboard test needs, all of it fake."""

    config: Any
    pipeline: Pipeline
    ac: FakeAC
    volume: FakeVolume
    clock: FakeClock
    mode_provider: Callable[[], Any]
    now: datetime


def make_stack(tmp_path: Path, *, config_overrides: dict[str, Any] | None = None, **overrides):
    """A real :class:`Pipeline` with fake adapters, ready for the dashboard."""
    config = overrides.pop("config", None)
    if config is None:
        config = make_config(tmp_path, **(config_overrides or {}))
    clock = overrides.pop("clock", None) or FakeClock()
    ac = overrides.pop("ac", None) or FakeAC()
    volume = overrides.pop("volume", None) or FakeVolume()
    now = overrides.pop("now", NOW_WEEKDAY)
    runner = overrides.pop("runner", timedatectl_says_synced)
    mode_provider = overrides.pop("mode_provider", None)
    if mode_provider is None:

        def mode_provider():
            return mode_module.resolve_mode(now, config, runner=runner)

    kwargs: dict[str, Any] = dict(
        decider=overrides.pop("decider", None) or StubDecider(),
        config=config,
        mode_provider=mode_provider,
        pod_id=POD,
        ac_power=ac.power,
        ac_status=ac.status,
        volume_step=volume,
        clock=clock,
        log=lambda record: None,
    )
    kwargs.update(overrides)
    pipeline = Pipeline(**kwargs)
    return Stack(
        config=config,
        pipeline=pipeline,
        ac=ac,
        volume=volume,
        clock=clock,
        mode_provider=mode_provider,
        now=now,
    )


_SEQ = {"n": 0}


def feed(pipeline, text: str) -> None:
    """Drive one complete utterance through the pipeline, then flush it."""
    _SEQ["n"] += 1
    item_id = f"web-item-{_SEQ['n']}"
    start = _SEQ["n"] * 10_000
    stop = start + 800
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_started", "at_ms": start, "item_id": item_id}
    )
    pipeline.handle_event(
        {"type": "input_audio_buffer.speech_stopped", "at_ms": stop, "item_id": item_id}
    )
    pipeline.handle_event(
        {
            "type": "conversation.item.input_audio_transcription.completed",
            "item_id": item_id,
            "text": text,
        }
    )
    pipeline.poll(stop + 600)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Response:
    status: int
    body: str
    headers: dict[str, str]

    def json(self) -> Any:
        return json.loads(self.body)


def request(
    url: str,
    *,
    method: str = "GET",
    body: Any = None,
    headers: dict[str, str] | None = None,
) -> Response:
    """One HTTP request to the test server. Loopback only, five-second cap."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)  # nosec B310 - http://127.0.0.1
    if data is not None:
        req.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # nosec B310 - loopback only
            return Response(resp.status, resp.read().decode("utf-8"), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        return Response(exc.code, exc.read().decode("utf-8"), dict(exc.headers))


@contextmanager
def serving(server) -> Iterator[Any]:
    """Start ``server``, yield its bind result, always stop it."""
    result = server.start()
    try:
        yield result
    finally:
        server.stop()


@contextmanager
def dashboard(tmp_path: Path, **overrides) -> Iterator[Any]:
    """A started :class:`DashboardServer` over a fake stack, plus that stack."""
    from shabbos_goy.web import DashboardServer

    server_kwargs = dict(overrides.pop("server_kwargs", {}))
    stack = make_stack(tmp_path, **overrides)
    # The dashboard's own "now" must be the test's, not the wall clock.
    server_kwargs.setdefault("now_provider", lambda: stack.now)
    server = DashboardServer(stack.pipeline, stack.mode_provider, stack.config, **server_kwargs)
    with serving(server) as result:
        assert result.ok, result.reason
        yield Dashboard(server=server, stack=stack, url=server.url)


@dataclass
class Dashboard:
    server: Any
    stack: Stack
    url: str

    def get(self, path: str, **kwargs) -> Response:
        return request(f"{self.url}{path}", **kwargs)

    def post(self, path: str, body: Any = None, **kwargs) -> Response:
        return request(f"{self.url}{path}", method="POST", body=body or {}, **kwargs)


def reset_override() -> None:
    mode_module.clear_override()
