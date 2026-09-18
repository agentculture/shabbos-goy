"""The ears-only lobes realtime session: reconnecting, watchdogged, silent.

This module is the session logic; ``ws.py`` is the wire. It emits normalised
:class:`~shabbos_goy.lobes.events.LobesEvent` objects to a callback and knows
nothing about classification, Shabbat, zmanim or actuators.

Four properties are load-bearing and each is a test, not a comment:

1. **Ears-only.** The only event this client can put on the wire is
   ``input_audio_buffer.append``. ``response.create``, ``session.update``
   and any tool declaration are refused in code
   (:class:`EarsOnlyViolation`), not merely left uncalled — the brief's
   "no confirmation, no conversation" invariant must not depend on a prompt.
2. **Nothing survives a restart.** No file, no queue, no spool. A turn cut
   off by a lost connection yields no transcript at all, so nothing
   downstream can act on half a sentence, and nothing is left for a restart
   to replay (issue #1 invariant 3).
3. **A stall is fatal, loudly.** Compose's ``restart: unless-stopped``
   reacts to a process exit, never to an "unhealthy" healthcheck, so a
   wedged listener (socket open, no frames) must exit non-zero itself.
4. **The mic is never muted.** Audio keeps streaming while the agent's own
   TTS plays; own-voice suppression is the pipeline's job downstream, done
   by discarding overlapping transcripts, not by going deaf.
"""

from __future__ import annotations

import base64
import json
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from . import events as ev
from . import ws
from .config import ENV_API_KEY, LobesConfig

# Exit codes, following the repo CLI contract (0 ok, 1 user, 2 environment,
# 3+ reserved).
EXIT_OK = 0
EXIT_ENVIRONMENT = 2
EXIT_STALLED = 3

# Named client-side error codes, kept distinct from lobes' own ErrorCode
# vocabulary so a log line never conflates "the server said X" with "we
# could not reach the server".
ERROR_AUTH_FAILED = "auth_failed"
ERROR_CONNECT_FAILED = "connect_failed"
ERROR_HANDSHAKE_REFUSED = "handshake_refused"
ERROR_STALLED = "watchdog_stalled"

REASON_SERVER_CLOSED = "server_closed"
REASON_CONNECTION_LOST = "connection_lost"
REASON_STOPPED = "stopped"
REASON_STALLED = "stalled"

#: The whole of this client's outbound vocabulary.
ALLOWED_CLIENT_EVENT_TYPES: frozenset[str] = frozenset({"input_audio_buffer.append"})

AUTH_STATUSES = (401, 403)


class EarsOnlyViolation(RuntimeError):
    """Refused: this session may only ever send audio (see module docstring)."""


class LobesAuthError(RuntimeError):
    """The gateway refused the handshake with 401/403 — an environment error."""


class LobesConnectError(RuntimeError):
    """The gateway could not be reached, or refused the upgrade."""


@dataclass(frozen=True)
class BackoffPolicy:
    """Exponential backoff with a hard cap.

    lobes containers restart ``unless-stopped`` but need minutes of model
    load after a host reboot, so the cap must be patient rather than the
    retry count bounded: this client never gives up on its own.
    """

    base: float = 1.0
    factor: float = 2.0
    cap: float = 60.0

    def delay(self, failures: int) -> float:
        if failures <= 0:
            return 0.0
        return min(self.cap, self.base * (self.factor ** (failures - 1)))


class Watchdog:
    """Fires when nothing has arrived for *window* seconds.

    Any frame counts as a heartbeat, PING included: a lobes session that is
    pinging but has stopped transcribing is still alive at the socket level,
    while one that has gone quiet in both is wedged.
    """

    def __init__(self, window: float, clock: Callable[[], float] = time.monotonic) -> None:
        self._window = window
        self._clock = clock
        self._last = clock()

    def beat(self) -> None:
        self._last = self._clock()

    def expired(self) -> bool:
        return (self._clock() - self._last) > self._window


@dataclass
class ClientStats:
    """Counters only — never transcript text (CLAUDE.md, Privacy)."""

    connections: int = 0
    sessions_with_events: int = 0
    audio_chunks_sent: int = 0
    pings_answered: int = 0
    transcripts: int = 0
    dropped_empty_transcripts: int = 0
    ignored_events: int = 0
    unknown_events: int = 0
    unknown_error_codes: int = 0
    turns_lost: int = 0
    auth_failures: int = 0
    connect_failures: int = 0
    stalls: int = 0
    callback_errors: int = 0
    errors_by_code: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionOutcome:
    """How one connection ended. Held in memory, never written anywhere."""

    reason: str
    events: int = 0
    turn_interrupted: bool = False


EventCallback = Callable[[ev.LobesEvent], None]
AudioSource = Callable[[], bytes | None]


class LobesClient:
    """One ears-only realtime session, reconnected for as long as it runs."""

    def __init__(
        self,
        config: LobesConfig,
        on_event: EventCallback,
        *,
        audio_source: AudioSource | None = None,
        backoff: BackoffPolicy | None = None,
        watchdog_seconds: float = 45.0,
        read_timeout: float = 1.0,
        connect_timeout: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        exit_action: Callable[[int], None] = sys.exit,
        max_connections: int | None = None,
        idle_audio_pause: float = 0.01,
    ) -> None:
        self.config = config
        self.stats = ClientStats()
        self._on_event = on_event
        self._audio_source = audio_source
        self._backoff = backoff or BackoffPolicy()
        self._watchdog_seconds = watchdog_seconds
        self._read_timeout = read_timeout
        self._connect_timeout = connect_timeout
        self._clock = clock
        self._sleep = sleep
        self._exit_action = exit_action
        self._max_connections = max_connections
        self._idle_audio_pause = idle_audio_pause

        self._stopped = threading.Event()
        self._session_over = threading.Event()
        self._conn: ws.WebSocketConnection | None = None
        self._playback_active = False
        self._open_turns: set[str] = set()

    # -- public surface ---------------------------------------------------
    @property
    def playback_active(self) -> bool:
        return self._playback_active

    def set_playback_active(self, active: bool) -> None:
        """Mark the agent's own TTS as playing.

        This changes NOTHING about capture on purpose: the mic keeps
        streaming so a household member can still be heard over the agent,
        and own-voice transcripts are discarded downstream instead.
        """
        self._playback_active = bool(active)

    def stop(self) -> None:
        """Ask the loop to finish after the current read."""
        self._stopped.set()

    def send_event(self, event: Mapping[str, object]) -> bool:
        """Send one client event, if and only if it is ears-only.

        Returns False when there is no live connection (a disconnect is not
        an error for audio: the frames are simply dropped, never queued).
        """
        wire_type = event.get("type")
        if wire_type not in ALLOWED_CLIENT_EVENT_TYPES:
            raise EarsOnlyViolation(
                f"refusing to send {wire_type!r}: this session is ears-only, so "
                f"{sorted(ALLOWED_CLIENT_EVENT_TYPES)} is the whole outbound vocabulary "
                "(no response.create, no session.update, no tool declarations)"
            )
        conn = self._conn
        if conn is None:
            return False
        try:
            conn.send_text(json.dumps(event))
        except OSError:
            return False
        return True

    def send_audio(self, pcm: bytes) -> bool:
        """Stream one PCM16 chunk as ``input_audio_buffer.append``."""
        sent = self.send_event(
            {
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )
        if sent:
            self.stats.audio_chunks_sent += 1
        return sent

    def run(self) -> int:
        """Connect, listen, reconnect with capped backoff. Returns an exit code.

        A refused key, a dead gateway and a mid-turn kill are all the same
        shape here: name the failure, wait, try again. The only thing that
        ends this loop by itself is :meth:`stop`, the ``max_connections``
        bound (tests and drills), or the watchdog exiting the process.
        """
        failures = 0
        productive = 0
        while not self._stopped.is_set():
            if (
                self._max_connections is not None
                and self.stats.connections >= self._max_connections
            ):
                break
            if self.stats.connections > 0:
                self._sleep(self._backoff.delay(max(1, failures)))
                if self._stopped.is_set():
                    break
            try:
                outcome = self.run_once()
            except (LobesAuthError, LobesConnectError):
                # Already reported to the callback as a named error event.
                failures += 1
                continue
            if outcome.events:
                productive += 1
                failures = 0
            else:
                failures += 1
        if productive == 0 and self.stats.connections > 0:
            return EXIT_ENVIRONMENT
        return EXIT_OK

    def run_once(self) -> SessionOutcome:
        """One connection, from handshake to close. Never persists anything."""
        self.stats.connections += 1
        conn = self._connect()
        self._conn = conn
        self._session_over.clear()
        self._open_turns = set()
        feeder = self._start_feeder()
        watchdog = Watchdog(self._watchdog_seconds, self._clock)
        watchdog.beat()
        reason = REASON_STOPPED
        seen = 0
        try:
            while not self._stopped.is_set():
                try:
                    fin, opcode, payload = conn.read_frame(timeout=self._read_timeout)
                except socket.timeout:
                    if watchdog.expired():
                        reason = self._stall()
                        break
                    continue
                except (ws.FrameReadError, OSError):
                    reason = REASON_CONNECTION_LOST
                    break
                watchdog.beat()
                if not fin:
                    continue
                if opcode == ws.OPCODE_TEXT:
                    seen += 1
                    self._handle_text(payload)
                elif opcode == ws.OPCODE_PING:
                    conn.send_pong(payload)
                    self.stats.pings_answered += 1
                elif opcode == ws.OPCODE_CLOSE:
                    reason = REASON_SERVER_CLOSED
                    break
                # BINARY/PONG/CONTINUATION: this route sends none; ignored.
        finally:
            self._session_over.set()
            self._conn = None
            conn.close()
            if feeder is not None:
                feeder.join(timeout=2.0)
        if seen:
            self.stats.sessions_with_events += 1
        return self._finish_session(reason, seen)

    # -- internals --------------------------------------------------------
    def _connect(self) -> ws.WebSocketConnection:
        try:
            conn, status, _headers = ws.WebSocketConnection.connect(
                self.config.host,
                self.config.port,
                self.config.realtime_path,
                extra_headers=self.config.handshake_headers(),
                tls=self.config.tls,
                connect_timeout=self._connect_timeout,
            )
        except (OSError, ws.HandshakeError) as exc:
            self.stats.connect_failures += 1
            self._emit(
                ev.LobesEvent(
                    kind=ev.KIND_ERROR,
                    code=ERROR_CONNECT_FAILED,
                    message=f"cannot reach {self.config.endpoint}: {type(exc).__name__}: {exc}",
                )
            )
            raise LobesConnectError(str(exc)) from exc
        if status == 101:
            return conn
        conn.close()
        if status in AUTH_STATUSES:
            self.stats.auth_failures += 1
            self._emit(
                ev.LobesEvent(
                    kind=ev.KIND_ERROR,
                    code=ERROR_AUTH_FAILED,
                    message=(
                        f"{self.config.endpoint} refused the handshake with HTTP {status}: "
                        f"check {ENV_API_KEY} in the environment (it is never read from a "
                        "tracked file)"
                    ),
                )
            )
            raise LobesAuthError(f"HTTP {status}")
        self.stats.connect_failures += 1
        self._emit(
            ev.LobesEvent(
                kind=ev.KIND_ERROR,
                code=ERROR_HANDSHAKE_REFUSED,
                message=f"{self.config.endpoint} answered HTTP {status}, expected 101",
            )
        )
        raise LobesConnectError(f"HTTP {status}")

    def _start_feeder(self) -> threading.Thread | None:
        if self._audio_source is None:
            return None
        thread = threading.Thread(target=self._feed, name="shabbos-goy-audio", daemon=True)
        thread.start()
        return thread

    def _feed(self) -> None:
        """Stream capture for the whole session, silence included.

        Runs while the agent's own playback is active too — see
        :meth:`set_playback_active`.
        """
        assert self._audio_source is not None
        while not self._stopped.is_set() and not self._session_over.is_set():
            chunk = self._audio_source()
            if chunk is None:
                return
            if not chunk:
                self._sleep(self._idle_audio_pause)
                continue
            if not self.send_audio(chunk):
                return

    def _handle_text(self, payload: bytes) -> None:
        try:
            wire = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Undecodable frame: counted, never fatal, never echoed (it
            # could contain transcript text).
            self.stats.unknown_events += 1
            return
        if not isinstance(wire, dict):
            self.stats.unknown_events += 1
            return
        event = ev.normalize_event(wire)

        if event.kind == ev.KIND_IGNORED:
            # A conversation-surface event we never asked for. Counted so an
            # operator can see it happened; never forwarded, so it can never
            # become an action.
            self.stats.ignored_events += 1
            return
        if event.kind == ev.KIND_UNKNOWN:
            self.stats.unknown_events += 1
            return
        if event.kind == ev.KIND_SPEECH_STARTED and event.item_id:
            self._open_turns.add(event.item_id)
        if event.kind == ev.KIND_TRANSCRIPT:
            if event.item_id:
                self._open_turns.discard(event.item_id)
            if not event.text.strip():
                # The sidecar emits empty text for a low-confidence
                # hallucination. Dropped noise, not an utterance.
                self.stats.dropped_empty_transcripts += 1
                return
            self.stats.transcripts += 1
        if event.kind == ev.KIND_ERROR:
            code = event.code or ""
            self.stats.errors_by_code[code] = self.stats.errors_by_code.get(code, 0) + 1
            if not ev.is_known_error_code(event.code):
                self.stats.unknown_error_codes += 1
        self._emit(event)

    def _stall(self) -> str:
        self.stats.stalls += 1
        self._emit(
            ev.LobesEvent(
                kind=ev.KIND_STALLED,
                code=ERROR_STALLED,
                message=(
                    f"no frame from {self.config.endpoint} for {self._watchdog_seconds}s "
                    "(socket still up): exiting so the supervisor restarts us"
                ),
            )
        )
        self._exit_action(EXIT_STALLED)
        return REASON_STALLED

    def _finish_session(self, reason: str, seen: int) -> SessionOutcome:
        interrupted = bool(self._open_turns)
        if interrupted:
            self.stats.turns_lost += len(self._open_turns)
        self._open_turns = set()
        if reason == REASON_CONNECTION_LOST or interrupted:
            self._emit(
                ev.LobesEvent(
                    kind=ev.KIND_CONNECTION_LOST,
                    turn_interrupted=interrupted,
                    message=(
                        "session ended mid-turn: the interrupted turn is dropped, never "
                        "queued and never replayed"
                        if interrupted
                        else "session ended"
                    ),
                )
            )
        return SessionOutcome(reason=reason, events=seen, turn_interrupted=interrupted)

    def _emit(self, event: ev.LobesEvent) -> None:
        """Hand one event downstream.

        A consumer that raises must not deafen the agent, but the failure is
        counted rather than swallowed silently — and the exception text is
        NOT re-raised or printed, because it can carry transcript text
        (CLAUDE.md, Privacy).
        """
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - a consumer bug must not kill the ears
            self.stats.callback_errors += 1
