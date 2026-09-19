"""Session-logic tests for the ears-only lobes client.

Every test here runs against ``tests/lobes_fake_server.py`` — an in-process
WebSocket server on ``127.0.0.1`` — so the suite needs no microphone, no
lobes deployment, no network and no API key. Time is injected wherever the
client would otherwise wait.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path

import pytest

from shabbos_goy.lobes import client as lc
from shabbos_goy.lobes import config as cfg
from shabbos_goy.lobes import events as ev
from tests.lobes_fake_server import OPCODE_CLOSE, OPCODE_PONG, OPCODE_TEXT, FakeLobesServer

FIXTURES = Path(__file__).parent / "fixtures" / "lobes" / "event-fixtures.json"


def load_fixtures() -> dict:
    with FIXTURES.open(encoding="utf-8") as handle:
        return json.load(handle)


def make_config(server: FakeLobesServer) -> cfg.LobesConfig:
    return cfg.config_from_env(
        {
            "SHABBOS_GOY_LOBES_URL": f"ws://{server.host}:{server.port}",
            "SHABBOS_GOY_LOBES_API_KEY": "test-key",
        }
    )


class Recorder:
    """Collects the events the client hands to its callback."""

    def __init__(self) -> None:
        self.events: list[ev.LobesEvent] = []
        self._lock = threading.Lock()

    def __call__(self, event: ev.LobesEvent) -> None:
        with self._lock:
            self.events.append(event)

    def kinds(self) -> list[str]:
        with self._lock:
            return [e.kind for e in self.events]

    def of_kind(self, kind: str) -> list[ev.LobesEvent]:
        with self._lock:
            return [e for e in self.events if e.kind == kind]

    def transcripts(self) -> list[str]:
        return [e.text for e in self.of_kind(ev.KIND_TRANSCRIPT)]


class FakeClock:
    """A monotonic clock that jumps *step* seconds on every read."""

    def __init__(self, step: float = 0.0, start: float = 0.0) -> None:
        self.now = start
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SleepRecorder:
    def __init__(self) -> None:
        self.calls: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


# ---------------------------------------------------------------------------
# Criterion 1 + 2 — the cited fixture replay
# ---------------------------------------------------------------------------


def test_fixture_file_covers_every_cited_event_type_and_error_code() -> None:
    data = load_fixtures()
    seen_types = {e["type"] for e in data["events"]}
    seen_codes = {e.get("code") for e in data["events"] if e["type"] == "error"}
    assert set(data["event_types"]) <= seen_types
    assert set(data["error_codes"]) <= seen_codes
    assert data["_citation"]["source_files"][0].endswith("event-fixtures.ts")


def test_every_cited_fixture_event_is_replayed_without_crashing_the_loop() -> None:
    data = load_fixtures()

    def script(server: FakeLobesServer, conn) -> None:
        for event in data["events"]:
            conn.send_event(event)
        conn.send_close()

    recorder = Recorder()
    with FakeLobesServer(script) as server:
        client = lc.LobesClient(make_config(server), recorder, read_timeout=0.05)
        outcome = client.run_once()

    # The loop reached the server's own close frame: nothing in between —
    # an unknown event type, an unknown error code, a tool call — stopped it.
    assert outcome.reason == lc.REASON_SERVER_CLOSED

    # Ears-only events reach the callback, in wire order.
    assert recorder.transcripts() == [
        "מה מזג האוויר במאדים?",
        "מה השעה עכשיו?",
        "וגם תגלגל קובייה",
        "okay actually never mind, tell me about Io instead",
        "never mind, forget it",
    ]
    assert len(recorder.of_kind(ev.KIND_SPEECH_STARTED)) == 5
    stopped = recorder.of_kind(ev.KIND_SPEECH_STOPPED)
    assert [e.reason for e in stopped] == ["silence", "silence", "silence", "max_turn", "silence"]
    assert [e.at_ms for e in stopped] == [2048, 6600, 8800, 39000, 42700]

    # The low-confidence (empty text) transcript is dropped, not forwarded.
    assert client.stats.dropped_empty_transcripts == 1
    assert "" not in recorder.transcripts()

    # Every named error code is handled by name; the invented one is counted
    # separately and is not fatal.
    errors = recorder.of_kind(ev.KIND_ERROR)
    assert [e.code for e in errors] == data["error_codes"] + ["quantum_flux_detected"]
    assert client.stats.errors_by_code["tts_failed"] == 1
    assert client.stats.unknown_error_codes == 1

    # response.* / tool / session.updated events are ignored, never actioned.
    assert recorder.of_kind(ev.KIND_IGNORED) == []
    assert client.stats.ignored_events == sum(
        1 for e in data["events"] if e["type"] in ev.IGNORED_EVENT_TYPES
    )
    # The invented event type is counted and ignored.
    assert client.stats.unknown_events == 1


# ---------------------------------------------------------------------------
# Criterion 2 — keepalive and the ears-only send contract
# ---------------------------------------------------------------------------


def test_the_client_answers_ping_with_pong() -> None:
    def script(server: FakeLobesServer, conn) -> None:
        conn.send_event({"type": "session.created", "config": {"language": "he"}})
        conn.send_ping(b"uvicorn-keepalive")
        conn.wait_for(lambda frames: any(f.opcode == OPCODE_PONG for f in frames), timeout=5.0)
        conn.send_close()

    with FakeLobesServer(script) as server:
        client = lc.LobesClient(make_config(server), Recorder(), read_timeout=0.05)
        client.run_once()
        conn = server.wait_for_connection()

    pongs = [f for f in conn.snapshot() if f.opcode == OPCODE_PONG]
    assert pongs, "uvicorn drops a peer that never pongs"
    assert pongs[0].payload == b"uvicorn-keepalive"
    assert client.stats.pings_answered == 1


def test_no_frame_the_client_sends_is_ever_a_response_create_or_a_tool_declaration() -> None:
    data = load_fixtures()
    chunks = [b"\x00\x01" * 256 for _ in range(4)]
    feed = list(chunks)

    def audio_source() -> bytes | None:
        return feed.pop(0) if feed else None

    def script(server: FakeLobesServer, conn) -> None:
        conn.wait_for(
            lambda frames: sum(1 for f in frames if f.opcode == OPCODE_TEXT) >= len(chunks),
            timeout=5.0,
        )
        for event in data["events"]:
            conn.send_event(event)
        conn.send_ping()
        conn.wait_for(lambda frames: any(f.opcode == OPCODE_PONG for f in frames), timeout=5.0)
        conn.send_close()

    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server), Recorder(), audio_source=audio_source, read_timeout=0.05
        )
        client.run_once()
        conn = server.wait_for_connection()

    frames = conn.snapshot()
    assert frames, "the client sent nothing at all"
    for frame in frames:
        assert frame.opcode in (OPCODE_TEXT, OPCODE_PONG, OPCODE_CLOSE)
        assert b"response.create" not in frame.payload
        assert b"session.update" not in frame.payload
        assert b'"tools"' not in frame.payload
        if frame.opcode == OPCODE_TEXT:
            assert frame.json()["type"] == "input_audio_buffer.append"


def test_sending_any_non_ears_only_event_is_refused_in_code() -> None:
    conf = cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": "ws://127.0.0.1:1"})
    client = lc.LobesClient(conf, Recorder())
    for forbidden in (
        {"type": "response.create"},
        {"type": "session.update", "session": {"tools": []}},
        {"type": "conversation.item.create"},
    ):
        with pytest.raises(lc.EarsOnlyViolation):
            client.send_event(forbidden)


def test_audio_frames_keep_flowing_while_playback_is_active() -> None:
    wanted = 8
    sent = []

    def audio_source() -> bytes | None:
        if len(sent) >= wanted:
            return None
        sent.append(b"\x10\x20" * 256)
        return sent[-1]

    def script(server: FakeLobesServer, conn) -> None:
        conn.wait_for(
            lambda frames: sum(1 for f in frames if f.opcode == OPCODE_TEXT) >= wanted,
            timeout=5.0,
        )
        conn.send_close()

    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server), Recorder(), audio_source=audio_source, read_timeout=0.05
        )
        client.set_playback_active(True)
        client.run_once()
        conn = server.wait_for_connection()

    appends = [f for f in conn.snapshot() if f.opcode == OPCODE_TEXT]
    assert len(appends) >= wanted, "the mic must never be muted during the agent's own playback"
    assert client.playback_active is True
    assert client.stats.audio_chunks_sent >= wanted


# ---------------------------------------------------------------------------
# Criterion 3 — reconnect, lost turns, no persistence, named auth failure
# ---------------------------------------------------------------------------


def test_backoff_grows_and_is_capped() -> None:
    policy = lc.BackoffPolicy(base=1.0, factor=2.0, cap=8.0)
    assert [policy.delay(n) for n in range(1, 7)] == [1.0, 2.0, 4.0, 8.0, 8.0, 8.0]
    assert policy.delay(0) == 0.0


def test_a_server_killed_mid_turn_reconnects_and_the_interrupted_turn_yields_nothing() -> None:
    def script(server: FakeLobesServer, conn) -> None:
        index = server.connections.index(conn)
        conn.send_event({"type": "session.created", "config": {"language": "he"}})
        if index == 0:
            conn.send_event(
                {
                    "type": "input_audio_buffer.speech_started",
                    "item_id": "item_lost",
                    "at_ms": 100,
                }
            )
            conn.abort()  # the server dies mid-turn
            return
        conn.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "item_after",
                "text": "חם פה",
            }
        )
        conn.send_close()

    recorder = Recorder()
    sleeper = SleepRecorder()
    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            recorder,
            backoff=lc.BackoffPolicy(base=1.0, factor=2.0, cap=8.0),
            sleep=sleeper,
            read_timeout=0.05,
            max_connections=2,
        )
        exit_code = client.run()

    assert exit_code == lc.EXIT_OK
    # It reconnected, and it waited a capped backoff before doing so.
    assert client.stats.connections == 2
    assert sleeper.calls == [1.0]

    lost = recorder.of_kind(ev.KIND_CONNECTION_LOST)
    assert len(lost) == 1
    assert lost[0].turn_interrupted is True

    # The interrupted turn produced no transcript at all — nothing downstream
    # could act on it — and it is not replayed after the reconnect.
    assert recorder.transcripts() == ["חם פה"]
    assert [e.item_id for e in recorder.of_kind(ev.KIND_TRANSCRIPT)] == ["item_after"]
    assert client.stats.turns_lost == 1


def test_nothing_is_written_that_a_restart_could_replay(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)

    def script(server: FakeLobesServer, conn) -> None:
        index = server.connections.index(conn)
        conn.send_event({"type": "session.created", "config": {"language": "he"}})
        conn.send_event({"type": "input_audio_buffer.speech_started", "item_id": "x", "at_ms": 1})
        if index == 0:
            conn.abort()
            return
        conn.send_close()

    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            Recorder(),
            sleep=SleepRecorder(),
            read_timeout=0.05,
            max_connections=2,
        )
        client.run()

    assert list(tmp_path.iterdir()) == [], "a restart must have nothing to replay"

    # Belt and braces: the package holds no write primitive at all.
    package = Path(lc.__file__).parent
    for module in sorted(package.glob("*.py")):
        text = module.read_text(encoding="utf-8")
        for primitive in ("open(", "write_text", "write_bytes", "pickle", "shelve", "json.dump("):
            assert primitive not in text, f"{module.name} can persist state via {primitive}"


def test_a_401_is_a_named_environment_error_with_backoff_not_a_crash_loop() -> None:
    recorder = Recorder()
    sleeper = SleepRecorder()
    with FakeLobesServer(handshake_status=401, handshake_body=b"unauthorized") as server:
        client = lc.LobesClient(
            make_config(server),
            recorder,
            backoff=lc.BackoffPolicy(base=1.0, factor=2.0, cap=4.0),
            sleep=sleeper,
            read_timeout=0.05,
            max_connections=4,
        )
        exit_code = client.run()

    assert exit_code == lc.EXIT_ENVIRONMENT
    errors = recorder.of_kind(ev.KIND_ERROR)
    assert len(errors) == 4
    assert {e.code for e in errors} == {lc.ERROR_AUTH_FAILED}
    assert all("SHABBOS_GOY_LOBES_API_KEY" in e.message for e in errors)
    assert all("401" in e.message for e in errors)
    # Backed off between attempts, capped — never a tight crash loop.
    assert sleeper.calls == [1.0, 2.0, 4.0]
    assert client.stats.auth_failures == 4


def test_an_unreachable_server_backs_off_and_names_the_transport_error() -> None:
    # A port nothing listens on: bound, never listened on, closed again.
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    probe.close()
    conf = cfg.config_from_env({"SHABBOS_GOY_LOBES_URL": f"ws://{host}:{port}"})
    recorder = Recorder()
    sleeper = SleepRecorder()
    client = lc.LobesClient(
        conf,
        recorder,
        backoff=lc.BackoffPolicy(base=0.5, factor=2.0, cap=2.0),
        sleep=sleeper,
        read_timeout=0.05,
        connect_timeout=0.5,
        max_connections=3,
    )
    assert client.run() == lc.EXIT_ENVIRONMENT
    assert [e.code for e in recorder.of_kind(ev.KIND_ERROR)] == [lc.ERROR_CONNECT_FAILED] * 3
    assert sleeper.calls == [0.5, 1.0]


# ---------------------------------------------------------------------------
# Criterion 4 — the watchdog
# ---------------------------------------------------------------------------


def test_watchdog_expires_only_after_its_window_of_silence() -> None:
    clock = FakeClock(step=0.0)
    watchdog = lc.Watchdog(window=30.0, clock=clock)
    watchdog.beat()
    clock.advance(29.0)
    assert watchdog.expired() is False
    clock.advance(2.0)
    assert watchdog.expired() is True
    watchdog.beat()
    assert watchdog.expired() is False


def test_a_frozen_server_makes_the_process_exit_non_zero_within_the_window() -> None:
    """Socket open, not one frame after session.created — the wedge Compose cannot see."""

    def script(server: FakeLobesServer, conn) -> None:
        conn.send_event({"type": "session.created", "config": {"language": "he"}})
        # ... and then nothing, ever. The socket stays open.

    exits: list[int] = []
    socket_alive_at_exit: list[bool] = []

    class Exited(Exception):
        pass

    recorder = Recorder()
    with FakeLobesServer(script) as server:

        def exit_action(code: int) -> None:
            exits.append(code)
            # The point of the drill: the connection was still perfectly
            # healthy at the socket level when we decided to die.
            socket_alive_at_exit.append(not server.connections[0].closed.is_set())
            raise Exited()

        client = lc.LobesClient(
            make_config(server),
            recorder,
            read_timeout=0.02,
            watchdog_seconds=30.0,
            clock=FakeClock(step=10.0),
            exit_action=exit_action,
        )
        with pytest.raises(Exited):
            client.run_once()

    assert exits == [lc.EXIT_STALLED]
    assert lc.EXIT_STALLED != 0, "Compose restarts on a non-zero exit, not on 'unhealthy'"
    assert socket_alive_at_exit == [True], "the drill is a frozen server, not a closed socket"
    assert recorder.of_kind(ev.KIND_STALLED), "the stall is reported before the exit"


def test_arriving_frames_keep_the_watchdog_quiet() -> None:
    data = load_fixtures()

    def script(server: FakeLobesServer, conn) -> None:
        for event in data["events"]:
            conn.send_event(event)
        conn.send_close()

    exits: list[int] = []
    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            Recorder(),
            read_timeout=0.02,
            watchdog_seconds=30.0,
            clock=FakeClock(step=1.0),
            exit_action=exits.append,
        )
        outcome = client.run_once()

    assert exits == []
    assert outcome.reason == lc.REASON_SERVER_CLOSED


# ---------------------------------------------------------------------------
# A FINITE source ends the session (review thread #3): `listen --script a.wav`
# must finish, not hang on the watchdog and then replay the file.
# ---------------------------------------------------------------------------


def _one_shot_source(chunks: list[bytes]):
    feed = list(chunks)

    def audio_source() -> bytes | None:
        return feed.pop(0) if feed else None

    return audio_source


def test_a_finite_source_closes_the_turn_with_silence_and_ends_the_session() -> None:
    """EOF on a WAV: a tail of silence (so the server's VAD closes the turn),
    a short wait for the last transcript, then a named end -- never a stall."""
    speech = b"\x01\x02" * 256

    def script(server: FakeLobesServer, conn) -> None:
        # Wait until the tail of silence has been sent, then answer with the
        # last transcript, exactly as the real VAD-driven server would.
        conn.wait_for(
            lambda frames: sum(1 for f in frames if f.opcode == OPCODE_TEXT) >= 3, timeout=5.0
        )
        conn.send_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "item_id": "last",
                "text": "חם פה",
            }
        )
        # Deliberately no close: the client must end this session itself.

    recorder = Recorder()
    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            recorder,
            audio_source=_one_shot_source([speech]),
            read_timeout=0.05,
            watchdog_seconds=5.0,
            tail_silence_seconds=0.2,
            source_end_grace=0.3,
        )
        outcome = client.run_once()
        conn = server.wait_for_connection()

    assert outcome.reason == lc.REASON_SOURCE_ENDED
    assert client.source_ended is True
    assert client.stats.stalls == 0
    assert recorder.transcripts() == ["חם פה"]

    appends = [f.json() for f in conn.snapshot() if f.opcode == OPCODE_TEXT]
    assert all(event["type"] == "input_audio_buffer.append" for event in appends)
    import base64 as _b64

    tail = [_b64.b64decode(event["audio"]) for event in appends[1:]]
    assert tail, "no tail of silence was sent, so the server's VAD never closed the turn"
    assert all(set(chunk) == {0} for chunk in tail)


def test_the_client_does_not_reconnect_after_a_finite_source_ended() -> None:
    """Reconnecting would replay the file from the top, so the run never ends."""

    def script(server: FakeLobesServer, conn) -> None:
        conn.wait_for(
            lambda frames: sum(1 for f in frames if f.opcode == OPCODE_TEXT) >= 2, timeout=5.0
        )

    sleeper = SleepRecorder()
    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            Recorder(),
            audio_source=_one_shot_source([b"\x03\x04" * 256]),
            read_timeout=0.05,
            watchdog_seconds=5.0,
            sleep=sleeper,
            tail_silence_seconds=0.1,
            source_end_grace=0.1,
            max_connections=3,
        )
        code = client.run()

    assert client.stats.connections == 1, "the exhausted script was replayed by a reconnect"
    assert sleeper.calls == []
    assert code in (lc.EXIT_OK, lc.EXIT_ENVIRONMENT)


def test_stop_ends_a_scripted_run_at_once_even_inside_the_end_of_source_grace() -> None:
    """SIGTERM promptness: the post-EOF wait is interruptible, not a sleep."""

    def script(server: FakeLobesServer, conn) -> None:
        conn.wait_for(lambda frames: False, timeout=5.0)

    with FakeLobesServer(script) as server:
        client = lc.LobesClient(
            make_config(server),
            Recorder(),
            audio_source=_one_shot_source([b"\x05\x06" * 256]),
            read_timeout=0.05,
            watchdog_seconds=30.0,
            tail_silence_seconds=0.0,
            source_end_grace=30.0,
        )
        stopper = threading.Timer(0.2, client.stop)
        stopper.daemon = True
        stopper.start()
        started = time.monotonic()
        outcome = client.run_once()
        elapsed = time.monotonic() - started
        stopper.cancel()

    assert elapsed < 5.0, f"stop() took {elapsed:.1f}s: the grace wait is not interruptible"
    assert outcome.reason in (lc.REASON_STOPPED, lc.REASON_SOURCE_ENDED)
