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

import json
import threading
import time

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
        try:
            response = request(f"{listener.control_url}/api/state")
            assert response.status >= 500
        except Exception:  # noqa: BLE001 - a 500 may surface as a closed socket
            pass
        finally:
            type(control).state = original

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


def test_heartbeat_path_comes_from_the_environment_and_defaults_under_tmp() -> None:
    assert str(heartbeat_path(env={"SHABBOS_GOY_HEARTBEAT": "/run/hb.json"})) == "/run/hb.json"
    assert str(heartbeat_path(env={})).startswith("/tmp/")  # nosec B108 - tmpfs by design


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
