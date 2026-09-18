"""The ambient runtime loop: threads, gates, dashboard, heartbeat, shutdown.

Acceptance criteria (t14):

1. ``listen`` starts capture, the lobes client, the pipeline and (if
   configured) the dashboard thread; ``--script`` runs the same loop with no
   hardware; dry-run unless ``--apply``;
2. with the dashboard address unavailable the listener still classifies and
   acts, the bind is retried, and an exception in a dashboard handler does
   not stop the loop;
3. a heartbeat on tmpfs (and a local ``/healthz``) reflects recent
   pong/audio/transcript activity for the container healthcheck.

Every test here is fixtures-only: no microphone, no lobes server, no Sensibo
account, no real volume change, and no address but ``127.0.0.1``. Every
thread this module starts is joined with a timeout, so a bug cannot hang the
suite.
"""

from __future__ import annotations

import http.client
import json
import os
import stat
import threading
import time
import urllib.error

import pytest

from shabbos_goy import mode as mode_module
from shabbos_goy.lobes import events as lobes_events
from shabbos_goy.runtime import (
    ConnectionMonitor,
    Heartbeat,
    Listener,
    ListenerOptions,
    RuntimeNote,
    healthcheck,
    heartbeat_path,
)

from .test_listen_support import (
    HOT,
    HOT_MARKED,
    IMPERATIVE,
    MARKER_TEXT,
    NOW_STRICT,
    POD,
    AudioClock,
    FakeAC,
    FakeVolume,
    FakeVolumeReader,
    ScriptedSource,
    make_listener,
    note_events,
    request,
    running,
    utterance_events,
    verdicts,
    wait_until,
)


@pytest.fixture(autouse=True)
def _no_override():
    mode_module.clear_override()
    yield
    mode_module.clear_override()


def _turn(text: str, index: int = 1) -> list[dict]:
    return utterance_events(text, item_id=f"t{index}", start_ms=index * 10_000)


def _flush(listener: Listener, clock: AudioClock) -> bool:
    """Let the audio timeline run past the join gap, as real time would."""
    clock.advance(2.0)
    return wait_until(lambda: listener.pipeline.log_records != [])


# ---------------------------------------------------------------------------
# criterion 1: the loop starts, runs and stops
# ---------------------------------------------------------------------------


def test_start_runs_the_source_pipeline_ticker_and_control_threads(tmp_path) -> None:
    source = ScriptedSource([])
    listener = make_listener(tmp_path, source=source)
    with running(listener):
        assert source.started.wait(timeout=5)
        names = {t.name for t in listener.threads()}
        assert {"shabbos-goy-source", "shabbos-goy-worker", "shabbos-goy-ticker"} <= names
        assert listener.control_url is not None


def test_a_scripted_utterance_reaches_the_pipeline_and_acts_dry_run(tmp_path) -> None:
    clock = AudioClock()
    listener = make_listener(tmp_path, events=_turn(HOT), clock=clock)
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])
    ac = listener.fakes["ac"]
    assert ac.power_calls == [(POD, True, False)]


def test_apply_is_what_turns_a_dry_run_into_an_action(tmp_path) -> None:
    clock = AudioClock()
    listener = make_listener(
        tmp_path,
        events=_turn(HOT),
        clock=clock,
        options=ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01, apply=True),
    )
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("acted", "ac_power_on")])
    assert listener.fakes["ac"].power_calls == [(POD, True, True)]


def test_an_imperative_is_dropped_by_the_running_loop(tmp_path) -> None:
    """The core invariant, on the real loop: in strict mode a command acts on
    nothing at all, not even after the strict-mode delay."""
    clock = AudioClock()
    listener = make_listener(tmp_path, events=_turn(IMPERATIVE), clock=clock, now=NOW_STRICT)
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("gate_refused", "none")])
    assert listener.fakes["ac"].power_calls == []


def test_startup_applies_the_configured_volume_and_computes_the_mode(tmp_path) -> None:
    applied: list[str] = []
    listener = make_listener(tmp_path, startup_audio=lambda: applied.append("silent"))
    with running(listener):
        assert wait_until(lambda: applied == ["silent"])
        assert listener.resolved_mode().mode == "weekday"
    assert "startup_volume" in note_events(listener)


def test_a_startup_audio_failure_is_named_and_does_not_stop_the_loop(tmp_path) -> None:
    clock = AudioClock()

    def boom() -> None:
        raise OSError("wpctl is not installed")

    listener = make_listener(tmp_path, events=_turn(HOT), clock=clock, startup_audio=boom)
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])
    assert "startup_volume_failed" in note_events(listener)


def test_a_missing_lobes_environment_is_one_named_line_not_a_crash_loop(tmp_path) -> None:
    from shabbos_goy.runtime.listener import lobes_source

    listener = make_listener(
        tmp_path,
        source=lobes_source(env={}, retry_seconds=0.01),
        options=ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01),
    )
    with running(listener):
        assert wait_until(lambda: "lobes_env_missing" in note_events(listener))
        # Still listening: the control endpoint is up and the loop is alive.
        assert listener.control_url is not None
    # One named line per retry at most, and never the variable's value.
    assert all(isinstance(note, RuntimeNote) for note in listener.notes)


# ---------------------------------------------------------------------------
# criterion 2: the dashboard never stops the listener
# ---------------------------------------------------------------------------


class _RefusingFactory:
    """A server factory that fails the first ``fail`` binds, then works."""

    def __init__(self, fail: int = 1) -> None:
        self.fail = fail
        self.attempts = 0

    def __call__(self, address, handler):
        self.attempts += 1
        if self.attempts <= self.fail:
            raise OSError("address not available yet")
        from http.server import ThreadingHTTPServer

        return ThreadingHTTPServer(address, handler)


def test_a_dashboard_bind_failure_does_not_stop_the_listener(tmp_path) -> None:
    clock = AudioClock()
    factory = _RefusingFactory(fail=99)
    listener = make_listener(
        tmp_path,
        events=_turn(HOT),
        clock=clock,
        options=ListenerOptions(
            control_address="127.0.0.1:0",
            poll_interval=0.01,
            dashboard=True,
            bind_retry_seconds=0.01,
        ),
        dashboard_server_factory=factory,
    )
    with running(listener):
        assert _flush(listener, clock)
        # The listener still classifies and acts with no dashboard at all.
        assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])
        assert listener.dashboard_url is None
        # ... and the bind is retried rather than given up on.
        assert wait_until(lambda: factory.attempts >= 3)
    assert "dashboard_bind_failed" in note_events(listener)


def test_a_dashboard_bind_is_retried_until_the_address_comes_up(tmp_path) -> None:
    factory = _RefusingFactory(fail=2)
    listener = make_listener(
        tmp_path,
        options=ListenerOptions(
            control_address="127.0.0.1:0",
            poll_interval=0.01,
            dashboard=True,
            bind_retry_seconds=0.01,
        ),
        dashboard_server_factory=factory,
    )
    with running(listener):
        assert wait_until(lambda: listener.dashboard_url is not None)
    assert factory.attempts >= 3


def test_an_exception_in_a_dashboard_handler_does_not_stop_the_loop(tmp_path) -> None:
    clock = AudioClock()
    listener = make_listener(
        tmp_path,
        events=_turn(HOT),
        clock=clock,
        options=ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01, dashboard=False),
    )
    with running(listener):
        control = listener.control
        assert control is not None

        def boom(self=None):
            raise RuntimeError("a handler bug")

        original = type(control).state
        type(control).state = boom
        status = 0
        try:
            status = request(f"{listener.control_url}/api/state").status
        except (urllib.error.URLError, ConnectionError, http.client.RemoteDisconnected):
            # http.server can drop the connection instead of finishing the 500.
            # Only THAT is tolerated -- the assertion below still has to run,
            # so a bare `except` must never swallow it.
            status = 500
        finally:
            type(control).state = original
        assert status >= 500

        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])


def test_the_dashboard_shares_the_control_server_when_both_want_one_address(tmp_path) -> None:
    from tests.decider_fake_server import closed_port

    port = closed_port()
    listener = make_listener(
        tmp_path,
        config_overrides={"dashboard_bind_address": f"127.0.0.1:{port}"},
        options=ListenerOptions(
            control_address=f"127.0.0.1:{port}", poll_interval=0.01, dashboard=True
        ),
    )
    with running(listener):
        assert wait_until(lambda: listener.dashboard_url is not None)
        # Loopback config + loopback control endpoint: one server, not two.
        assert listener.dashboard is listener.control
    assert "dashboard_shared" in note_events(listener)


# ---------------------------------------------------------------------------
# criterion 3: the heartbeat and /healthz
# ---------------------------------------------------------------------------


def test_heartbeat_records_pong_audio_and_transcript_activity(tmp_path) -> None:
    beat = Heartbeat(tmp_path / "hb.json", clock=lambda: 1_000.0, window_seconds=60.0)
    beat.mark("audio")
    beat.mark("pong")
    beat.mark("transcript")
    assert beat.write() is True

    data = json.loads((tmp_path / "hb.json").read_text(encoding="utf-8"))
    assert data["last"] == {"pong": 1000.0, "audio": 1000.0, "transcript": 1000.0}
    assert data["window_seconds"] == 60.0


def test_healthcheck_is_ok_while_activity_is_recent_and_fails_when_it_is_stale(tmp_path) -> None:
    path = tmp_path / "hb.json"
    beat = Heartbeat(path, clock=lambda: 1_000.0, window_seconds=60.0)
    beat.mark("transcript")
    beat.write()

    ok, reason = healthcheck(path, now=1_030.0)
    assert (ok, reason) == (True, "ok")

    stale_ok, stale_reason = healthcheck(path, now=1_200.0)
    assert stale_ok is False
    assert stale_reason == "stale"


def test_healthcheck_on_a_missing_file_is_a_named_failure(tmp_path) -> None:
    ok, reason = healthcheck(tmp_path / "nope.json", now=1.0)
    assert ok is False
    assert reason == "missing"


def test_heartbeat_path_comes_from_the_environment_and_defaults_per_user() -> None:
    assert str(heartbeat_path(env={"SHABBOS_GOY_HEARTBEAT": "/run/hb.json"})) == "/run/hb.json"

    # XDG_RUNTIME_DIR is already a per-user tmpfs: use it when there is one.
    xdg = heartbeat_path(env={"XDG_RUNTIME_DIR": "/run/user/4242"})
    assert str(xdg) == "/run/user/4242/shabbos-goy/heartbeat.json"

    # Without one, a per-uid directory -- never a fixed name another user on
    # the host could predict and pre-create as a symlink.
    fallback = heartbeat_path(env={})
    assert fallback.parent.name == f"shabbos-goy-{os.getuid()}"
    assert fallback.name == "heartbeat.json"


def test_the_heartbeat_directory_and_file_are_owner_only(tmp_path) -> None:
    beat = Heartbeat(tmp_path / "run" / "hb.json", clock=lambda: 1_000.0)
    beat.mark("pong")
    assert beat.write() is True
    assert stat.S_IMODE((tmp_path / "run").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "run" / "hb.json").stat().st_mode) == 0o600


def test_a_symlinked_heartbeat_directory_is_refused_not_followed(tmp_path) -> None:
    """The symlink-attack case: somebody else's link must not redirect us."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "link"
    link.symlink_to(elsewhere, target_is_directory=True)

    beat = Heartbeat(link / "hb.json", clock=lambda: 1_000.0)
    beat.mark("pong")
    assert beat.write() is False, "the write followed a symlinked directory"
    assert list(elsewhere.iterdir()) == []


def test_a_heartbeat_directory_owned_by_another_user_is_refused(tmp_path, monkeypatch) -> None:
    """Owned by somebody else means somebody else can swap the file."""
    from shabbos_goy.runtime import heartbeat as heartbeat_module

    # `heartbeat.os` IS the os module, so capture the real uid before patching.
    not_us = os.getuid() + 1
    monkeypatch.setattr(heartbeat_module.os, "getuid", lambda: not_us)
    beat = Heartbeat(tmp_path / "hb.json", clock=lambda: 1_000.0)
    beat.mark("pong")
    assert beat.write() is False
    assert not (tmp_path / "hb.json").exists()


def test_the_heartbeat_is_never_written_through_a_planted_symlink(tmp_path) -> None:
    """O_NOFOLLOW: a link planted at the temp name is refused, not followed."""
    target = tmp_path / "victim"
    target.write_text("untouched", encoding="utf-8")
    (tmp_path / "hb.json.tmp").symlink_to(target)

    beat = Heartbeat(tmp_path / "hb.json", clock=lambda: 1_000.0)
    beat.mark("pong")
    assert beat.write() is False
    assert target.read_text(encoding="utf-8") == "untouched"


def test_a_refused_heartbeat_never_takes_the_listener_down(tmp_path) -> None:
    (tmp_path / "real").mkdir()
    link = tmp_path / "link"
    link.symlink_to(tmp_path / "real", target_is_directory=True)
    clock = AudioClock()
    listener = make_listener(
        tmp_path, events=_turn(HOT), clock=clock, heartbeat_path=link / "hb.json"
    )
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])


def test_the_running_listener_writes_a_heartbeat_and_serves_healthz(tmp_path) -> None:
    clock = AudioClock()
    listener = make_listener(tmp_path, events=_turn(HOT), clock=clock)
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: (tmp_path / "heartbeat.json").exists())
        assert wait_until(lambda: listener.heartbeat.snapshot()["transcript"] is not None)
        response = request(f"{listener.control_url}/healthz")
        assert response.status == 200
        assert response.json()["ok"] is True


def test_healthz_reports_503_when_nothing_has_arrived_for_a_while(tmp_path) -> None:
    wall = AudioClock(now=5_000.0)
    beat = Heartbeat(tmp_path / "hb.json", clock=wall, window_seconds=1.0)
    listener = make_listener(tmp_path, heartbeat=beat)
    with running(listener):
        wall.advance(600.0)
        response = request(f"{listener.control_url}/healthz")
        assert response.status == 503
        assert response.json()["ok"] is False


# ---------------------------------------------------------------------------
# the connection-state provider (text-free)
# ---------------------------------------------------------------------------


def test_connection_monitor_walks_connecting_connected_and_reconnecting() -> None:
    monitor = ConnectionMonitor(clock=lambda: 10.0)
    assert monitor.snapshot()["state"] == "idle"

    monitor.note_connecting()
    assert monitor.snapshot()["state"] == "connecting"

    monitor.handle_event(lobes_events.LobesEvent(kind=lobes_events.KIND_SESSION_CREATED))
    assert monitor.snapshot()["state"] == "connected"

    monitor.handle_event(lobes_events.LobesEvent(kind=lobes_events.KIND_CONNECTION_LOST))
    snapshot = monitor.snapshot()
    assert snapshot["state"] == "reconnecting"
    assert snapshot["attempts"] == 1


def test_connection_monitor_names_auth_failure_and_a_stall() -> None:
    monitor = ConnectionMonitor(clock=lambda: 0.0)
    monitor.handle_event(
        lobes_events.LobesEvent(kind=lobes_events.KIND_ERROR, code="auth_failed", message="key")
    )
    assert monitor.snapshot()["state"] == "auth_failed"

    monitor.handle_event(lobes_events.LobesEvent(kind=lobes_events.KIND_STALLED, code="x"))
    assert monitor.snapshot()["state"] == "stalled"


def test_connection_monitor_never_carries_transcript_text_or_a_message() -> None:
    monitor = ConnectionMonitor(clock=lambda: 0.0)
    monitor.handle_event(
        lobes_events.LobesEvent(
            kind=lobes_events.KIND_TRANSCRIPT, text=MARKER_TEXT, message=MARKER_TEXT
        )
    )
    rendered = json.dumps(monitor.snapshot(), ensure_ascii=False)
    assert MARKER_TEXT not in rendered
    assert monitor.snapshot()["transcripts"] == 1


def test_the_listener_feeds_its_connection_state_to_the_dashboard(tmp_path) -> None:
    listener = make_listener(tmp_path, events=_turn(HOT))
    with running(listener):
        assert wait_until(lambda: listener.connection.snapshot()["transcripts"] >= 1)
        state = request(f"{listener.control_url}/api/state").json()
        assert state["connection"]["state"] in ("idle", "connecting", "connected")


# ---------------------------------------------------------------------------
# threads: bounded queue, no deadlock, clean shutdown
# ---------------------------------------------------------------------------


def test_the_event_queue_is_bounded_and_drops_rather_than_growing(tmp_path) -> None:
    many = [
        event
        for index in range(1, 60)
        for event in utterance_events(HOT, item_id=f"q{index}", start_ms=index * 10_000)
    ]
    listener = make_listener(
        tmp_path,
        events=many,
        options=ListenerOptions(control_address="127.0.0.1:0", poll_interval=0.01, queue_size=4),
    )
    listener.pause_worker()  # keep the worker from draining while we flood
    with running(listener):
        assert wait_until(lambda: listener.queue_dropped > 0)
        assert listener.queue_depth() <= 4
        listener.resume_worker()
        assert wait_until(lambda: listener.queue_depth() == 0)


def test_a_multi_threaded_soak_stays_bounded_and_shuts_down_cleanly(tmp_path) -> None:
    clock = AudioClock()
    listener = make_listener(tmp_path, source=ScriptedSource([], drain=False), clock=clock)
    stop = threading.Event()
    feeders: list[threading.Thread] = []

    def feed(worker: int) -> None:
        index = 0
        while not stop.is_set() and index < 40:
            index += 1
            for event in utterance_events(
                HOT, item_id=f"s{worker}-{index}", start_ms=(worker * 1000 + index) * 100
            ):
                listener.submit(event)
            time.sleep(0.001)

    with running(listener):
        for worker in range(4):
            thread = threading.Thread(
                target=feed, args=(worker,), name=f"feeder-{worker}", daemon=True
            )
            thread.start()
            feeders.append(thread)
        for thread in feeders:
            thread.join(timeout=10)
            assert not thread.is_alive()
        stop.set()
        clock.advance(5.0)
        started = time.monotonic()
        assert wait_until(lambda: listener.queue_depth() == 0)
        assert time.monotonic() - started < 10
        # Bounded rings: memory does not grow with the flood.
        assert len(listener.pipeline.recent()) <= 20
        assert len(listener.pipeline.log_records) <= 200


def test_stop_is_idempotent_and_joins_every_thread(tmp_path) -> None:
    listener = make_listener(tmp_path)
    listener.start()
    listener.stop(timeout=10)
    listener.stop(timeout=10)
    assert [t.name for t in listener.threads() if t.is_alive()] == []


def test_a_signal_asks_the_listener_to_stop(tmp_path) -> None:
    listener = make_listener(tmp_path)
    with running(listener):
        listener.request_stop("SIGTERM")
        assert wait_until(lambda: listener.stopping)
    assert "shutdown" in note_events(listener)


def test_run_returns_when_the_source_finishes(tmp_path) -> None:
    """The real events-file source: when the script ends, so does the run."""
    from shabbos_goy.runtime import events_file_source

    from .test_listen_support import write_events_file

    clock = AudioClock()
    listener = make_listener(
        tmp_path,
        source=events_file_source(write_events_file(tmp_path / "events.jsonl", [HOT])),
        clock=clock,
    )

    result: dict[str, int] = {}

    def go() -> None:
        result["code"] = listener.run()

    thread = threading.Thread(target=go, name="run", daemon=True)
    thread.start()
    try:
        assert wait_until(lambda: listener.pipeline.log_records != [])
    finally:
        thread.join(timeout=10)
        assert not thread.is_alive()
    assert result["code"] == 0
    assert verdicts(listener) == [("dry_run", "ac_power_on")]


# ---------------------------------------------------------------------------
# privacy
# ---------------------------------------------------------------------------


def test_nothing_the_listener_prints_carries_transcript_text_or_a_pod_id(tmp_path, capsys) -> None:
    clock = AudioClock()
    listener = make_listener(
        tmp_path, events=_turn(HOT_MARKED), clock=clock, note_sink=None, log=None
    )
    with running(listener):
        assert _flush(listener, clock)
        assert wait_until(lambda: verdicts(listener) != [])

    captured = capsys.readouterr()
    printed = captured.out + captured.err
    assert MARKER_TEXT not in printed
    assert HOT not in printed
    assert POD not in printed
    # The heartbeat file is written too, and is just as public.
    assert MARKER_TEXT not in (tmp_path / "heartbeat.json").read_text(encoding="utf-8")


def test_a_runtime_note_can_only_carry_a_named_event_and_a_short_detail() -> None:
    note = RuntimeNote(event="dashboard_bind_failed", detail="bind_refused")
    assert note.render() == "event=dashboard_bind_failed detail=bind_refused"
    assert set(vars(note)) == {"event", "detail"}


# ---------------------------------------------------------------------------
# the volume reader the control endpoint needs
# ---------------------------------------------------------------------------


def test_the_listener_exposes_a_volume_reader_to_the_control_endpoint(tmp_path) -> None:
    reader = FakeVolumeReader(level=0.3, muted=False)
    listener = make_listener(tmp_path, volume_get=reader)
    with running(listener):
        payload = request(f"{listener.control_url}/volume").json()
    assert payload["available"] is True
    assert payload["level"] == 0.3
    assert payload["muted"] is False


def test_volume_is_honestly_unavailable_with_no_reader(tmp_path) -> None:
    listener = make_listener(tmp_path, volume_get=None)
    with running(listener):
        payload = request(f"{listener.control_url}/volume").json()
    assert payload["available"] is False
    assert payload["reason"] == "no_adapter"


def test_fake_adapters_are_never_the_real_ones(tmp_path) -> None:
    """Guard rail for this test module itself: nothing real is ever wired."""
    listener = make_listener(tmp_path)
    assert isinstance(listener.fakes["ac"], FakeAC)
    assert isinstance(listener.fakes["volume"], FakeVolume)


# ---------------------------------------------------------------------------
# the real client path, against the fake lobes server
# ---------------------------------------------------------------------------


def test_the_listener_drives_the_real_lobes_client_against_a_fake_server(
    tmp_path, monkeypatch
) -> None:
    """Criterion 1's "starts the lobes client", with the production client.

    The only fake below the listener is the *server* (an in-process WebSocket
    on 127.0.0.1) and the audio, which is a short list of PCM chunks rather
    than a microphone. Everything between -- the client, the queue, the
    pipeline, the gates -- is the shipped code.
    """
    from shabbos_goy.lobes import LobesClient
    from shabbos_goy.runtime import lobes_source

    from .lobes_fake_server import FakeLobesServer

    def script(server, conn):
        conn.send_event({"type": "session.created"})
        conn.send_event(
            {"type": "input_audio_buffer.speech_started", "at_ms": 1000, "item_id": "live"}
        )
        conn.send_event(
            {"type": "input_audio_buffer.speech_stopped", "at_ms": 1800, "item_id": "live"}
        )
        conn.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "live",
                "text": HOT,
            }
        )

    chunks = [b"\x00\x01" * 160] * 5

    def audio_factory():
        remaining = list(chunks)

        def read():
            return remaining.pop(0) if remaining else b""

        return read

    def client_factory(config, on_event, **kwargs):
        # Bounded so a bug cannot reconnect forever inside a test.
        return LobesClient(config, on_event, max_connections=1, read_timeout=0.05, **kwargs)

    with FakeLobesServer(script) as server:
        monkeypatch.setenv("SHABBOS_GOY_LOBES_URL", f"ws://{server.host}:{server.port}/v1/realtime")
        listener = make_listener(
            tmp_path,
            # The real monotonic clock, so the audio timeline the joiner's gap
            # lives on advances by itself, exactly as it does in production.
            clock=time.monotonic,
            source=lobes_source(
                retry_seconds=0.01,
                audio_source_factory=audio_factory,
                client_factory=client_factory,
            ),
        )
        with running(listener):
            assert wait_until(lambda: listener.connection.snapshot()["transcripts"] >= 1)
            assert wait_until(lambda: verdicts(listener) == [("dry_run", "ac_power_on")])
            assert wait_until(lambda: listener.heartbeat.snapshot()["transcript"] is not None)

    # Ears-only: the client sent audio frames and nothing else, ever.
    conn = server.connections[0]
    assert conn.sent_events()
    assert {event["type"] for event in conn.sent_events()} == {"input_audio_buffer.append"}


def test_a_wav_script_is_streamed_as_pcm_chunks_then_ends(tmp_path) -> None:
    import wave

    from shabbos_goy.runtime import wav_audio_source

    path = tmp_path / "clip.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x01" * 800)

    read = wav_audio_source(path, chunk_bytes=640)
    first = read()
    assert first is not None and len(first) == 640
    seen = len(first)
    while True:
        chunk = read()
        if chunk is None:
            break
        seen += len(chunk)
    assert seen == 1600
    # Exhausted stays exhausted: the client's feeder learns the session ended.
    assert read() is None


def test_a_frozen_lobes_session_makes_run_return_the_stall_exit_code(tmp_path, monkeypatch) -> None:
    """Spec c31: Compose restarts on EXIT, not on "unhealthy".

    The lobes client's watchdog calls its ``exit_action`` on the reader thread,
    where ``sys.exit`` would only kill that thread and ``run()`` would return 0.
    The listener must hand the client an exit action that stops the whole
    listener and makes ``run()`` return the stall code, with one text-free
    log line naming the stall.
    """
    from shabbos_goy.lobes.client import EXIT_STALLED
    from shabbos_goy.runtime import lobes_source

    seen: dict = {}

    class FrozenClient:
        def __init__(self, config, on_event, **kwargs):
            seen["exit_action"] = kwargs.get("exit_action")

        def run(self):
            # What LobesClient does when its watchdog window expires.
            seen["exit_action"](EXIT_STALLED)

        def stop(self):
            pass

        def set_playback_active(self, active):
            pass

    monkeypatch.setenv("SHABBOS_GOY_LOBES_URL", "ws://lobes-host.invalid:8001/v1/realtime")
    listener = make_listener(
        tmp_path,
        source=lobes_source(retry_seconds=0.01, client_factory=FrozenClient),
    )
    result: dict = {}
    thread = threading.Thread(target=lambda: result.update(code=listener.run()), daemon=True)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive(), "run() did not return after a stall"
    assert callable(seen.get("exit_action")), "the listener gave the client no exit action"
    assert result["code"] == EXIT_STALLED
    assert any(note.event == "lobes_stalled" for note in listener.notes)


# ---------------------------------------------------------------------------
# The capture child must not outlive its session (review thread #4).
# ---------------------------------------------------------------------------


class _FakePwRecord:
    """A stand-in for ``pw-record``: no binary, no microphone, no PipeWire."""

    instances: list["_FakePwRecord"] = []

    def __init__(self, argv, stdout=None, stderr=None, env=None) -> None:
        import io

        self.argv = list(argv)
        self.stderr_argument = stderr
        self.stdout = io.BytesIO(b"\x00\x01" * 8192)
        self.terminated = False
        self.killed = False
        self.waited = False
        self._returncode: int | None = None
        type(self).instances.append(self)

    # -- the subprocess.Popen surface the runtime uses --------------------
    def poll(self):
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15

    def kill(self) -> None:  # pragma: no cover - the fake always terminates
        self.killed = True
        self._returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


@pytest.fixture
def fake_capture():
    _FakePwRecord.instances = []
    yield _FakePwRecord
    _FakePwRecord.instances = []


def test_the_capture_child_gets_devnull_stderr_and_is_reaped_by_close(fake_capture) -> None:
    """An undrained stderr pipe blocks the child; an unreaped child holds the mic."""
    import subprocess

    from shabbos_goy.runtime import pipewire_audio_source

    source = pipewire_audio_source("alsa_input.fake", popen=fake_capture, chunk_bytes=64)
    process = fake_capture.instances[-1]
    assert process.stderr_argument is subprocess.DEVNULL, "stderr must not be an undrained pipe"
    assert source() is not None

    source.close()
    assert process.terminated is True
    assert process.waited is True
    assert process.poll() is not None
    # Reading a closed source is EOF, not an exception on the feeder thread.
    assert source() is None
    source.close()  # idempotent


def test_no_capture_process_survives_repeated_lobes_reconnects(
    tmp_path, monkeypatch, fake_capture
) -> None:
    """Every session end closes its capture, so reconnects never accumulate."""
    from shabbos_goy.runtime import lobes_source, pipewire_audio_source

    class _ImmediatelyEndingClient:
        def __init__(self, config, on_event, **kwargs) -> None:
            self.stats = None
            self.source_ended = False

        def run(self):
            return 0

        def stop(self) -> None:
            pass

    monkeypatch.setenv("SHABBOS_GOY_LOBES_URL", "ws://lobes-host.invalid:8001/v1/realtime")
    listener = make_listener(
        tmp_path,
        source=lobes_source(
            retry_seconds=0.001,
            audio_source_factory=lambda: pipewire_audio_source(
                "alsa_input.fake", popen=fake_capture, chunk_bytes=64
            ),
            client_factory=_ImmediatelyEndingClient,
        ),
    )
    with running(listener):
        assert wait_until(lambda: len(fake_capture.instances) >= 4)
        alive = [p for p in fake_capture.instances[:-1] if p.poll() is None]
        assert alive == [], f"{len(alive)} capture children outlived their session"

    survivors = [p for p in fake_capture.instances if p.poll() is None]
    assert survivors == [], f"{len(survivors)} capture children survived the shutdown"
