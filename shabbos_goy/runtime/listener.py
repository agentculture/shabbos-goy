"""The ambient listener: the threads, and the one queue between them.

This module is the integration point. It owns no policy of its own --- every
gate lives in :mod:`shabbos_goy.pipeline`, :mod:`shabbos_goy.policy`,
:mod:`shabbos_goy.config` and :mod:`shabbos_goy.mode` --- and it adds no
second route to an actuator. What it adds is *lifetime*: which threads run,
what they are allowed to touch, and how the whole thing comes down when
Docker sends SIGTERM.

Threads and the locking
-----------------------

Six threads, and exactly one piece of shared mutable state between them:

============== ====================================================
``source``     the ears. Either the real lobes reader (which itself
               starts the **mic feeder** thread inside
               :class:`~shabbos_goy.lobes.LobesClient`) or a scripted
               replay. Its only contact with the rest of the process
               is :meth:`Listener.submit`.
``worker``     the only thread that ever touches the
               :class:`~shabbos_goy.pipeline.Pipeline`'s state
               machine: it drains the queue, calls ``handle_event``
               and ``poll``, and therefore runs the whole
               decide -> gate -> actuate chain single-threaded.
``ticker``     time. It asks the worker to poll (so the strict-mode
               delay and the joiner's gap timeout advance even in
               silence), samples the client's counters into the
               heartbeat, writes the heartbeat file, and retries a
               dashboard bind that has not come up yet. It never
               touches the pipeline.
``dashboard``  / ``control``: ``http.server``'s own serving threads,
               one per bound server (plus a thread per request).
============== ====================================================

**The queue is the boundary.** ``source`` -> ``worker`` is a bounded
:class:`queue.Queue`; when it is full the *oldest* event is dropped and
counted, because a listener that blocks its ears to preserve a backlog is
worse than one that forgets a stale second of audio. Nothing is ever spooled
to disk, so a restart replays nothing (issue #1 invariant 3).

**The ticker does not share state with the worker**, it signals it: a single
:class:`threading.Event` the worker clears. A poll request that arrives while
the worker is busy is coalesced, never queued up.

**The HTTP threads are the one genuine overlap.** A control endpoint calls
the same adapters and the same rate limiter the worker does, so those calls
are serialised under :attr:`Listener.actuator_lock`; the pipeline's read
surfaces (``log_records``, ``recent()``, ``decide_latencies()``) are snapshot
copies of bounded rings and are safe to read without it.

Startup is stateless
--------------------

Nothing is resumed. The mode is computed from the clock and zmanim on every
boot, the configured volume is re-applied (silent by default), and the lobes
connection is retried with backoff. A missing lobes environment or Sensibo
key is **one named line and a retry**, never a crash loop: a container that
exits on a missing variable takes the whole household's Shabbat with it.

Privacy
-------
Everything this module prints is a :class:`RuntimeNote`: a named event and a
short code from a fixed vocabulary. The type has no field that could hold
transcript text, a host, a key or a pod id --- the same guarantee
:class:`shabbos_goy.pipeline.LogRecord` makes, for the same reason (Docker
keeps container stdout).
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess  # nosec B404 - only for the capture pipe's own types
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Optional

from ..audio import pipewire
from ..config import Config
from ..limits import BoundedRing
from ..lobes import LobesClient, LobesConfigError, config_from_env
from ..lobes import events as ev
from ..mode import resolve_mode
from ..pipeline import DEFAULT_JOIN_GAP_MS, Pipeline
from ..web import server as web_server
from .connection import ConnectionMonitor
from .heartbeat import Heartbeat
from .heartbeat import heartbeat_path as resolve_heartbeat_path

__all__ = [
    "Listener",
    "ListenerOptions",
    "RuntimeNote",
    "events_file_source",
    "lobes_source",
    "read_events_file",
    "wav_audio_source",
]


class _Received(NamedTuple):
    """One lobes event plus the WALL-CLOCK instant it was received.

    The worker is single-threaded and also runs the decide -> gate -> actuate
    chain, so a speech-start event can sit in the queue behind a model
    decision (measured live at ~600 ms). Sampling the wall clock when the
    worker finally dispatches the event would let speech received INSIDE a
    strict window be stamped after the window closed -- reintroducing exactly
    the boundary failure this listener exists to prevent. The instant is
    therefore captured on the receiving thread, before the event is queued.
    """

    event: Any
    wall: float


#: Sentinels on the event queue. Neither is an event and neither can act.
_TICK = object()
_DRAIN = object()

DEFAULT_CONTROL_PORT = 8787
DEFAULT_QUEUE_SIZE = 256
DEFAULT_POLL_INTERVAL = 0.25
DEFAULT_BIND_RETRY_SECONDS = 30.0
DEFAULT_SHUTDOWN_TIMEOUT = 5.0
DEFAULT_LOBES_RETRY_SECONDS = 5.0
CAPTURE_CHUNK_BYTES = 3200  # 100 ms of 16 kHz mono PCM16
NOTE_CAPACITY = 100


@dataclass(frozen=True)
class RuntimeNote:
    """One diagnostic line. A named event and a short code --- nothing else.

    There is deliberately no field that could hold transcript text, a host, a
    key or a pod id: the type itself is the guarantee.
    """

    event: str
    detail: str = ""

    def render(self) -> str:
        return f"event={self.event}" + (f" detail={self.detail}" if self.detail else "")


@dataclass(frozen=True)
class ListenerOptions:
    """Everything about the loop's *lifetime* (never about its policy)."""

    apply: bool = False
    dashboard: bool = True
    control: bool = True
    #: Where the loopback control endpoint binds. The CLI's client resolves
    #: the same address (see ``cli/_commands/_control.py``), so these two must
    #: agree or ``shabbos-goy ac status`` finds nothing.
    control_address: str = f"127.0.0.1:{DEFAULT_CONTROL_PORT}"
    poll_interval: float = DEFAULT_POLL_INTERVAL
    bind_retry_seconds: float = DEFAULT_BIND_RETRY_SECONDS
    queue_size: int = DEFAULT_QUEUE_SIZE
    shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT


def _stderr_note(note: RuntimeNote) -> None:
    """The default sink: diagnostics to stderr (the CLI's output contract)."""
    print(note.render(), file=sys.stderr)


def control_address_for(config: Config, override: Optional[str] = None) -> str:
    """The loopback control address, matching what the CLI client resolves.

    The CLI falls back to ``config.dashboard_bind_address`` and then to
    ``127.0.0.1:8787``, so the listener takes the *port* from the same place
    and always binds it on loopback.
    """
    if override:
        return override
    configured = config.dashboard_bind_address
    if isinstance(configured, str) and ":" in configured:
        _host, _, port = configured.rpartition(":")
        if port.isdigit():
            return f"127.0.0.1:{port}"
    return f"127.0.0.1:{DEFAULT_CONTROL_PORT}"


# ---------------------------------------------------------------------------
# sources: where events come from
# ---------------------------------------------------------------------------


def read_events_file(path: str | Path) -> list[dict]:
    """A JSONL (or JSON-array) file of lobes wire events.

    The fixtures-only path: the same events the server would have sent, with
    no server, no socket and no audio. Anything that is not a JSON object is
    skipped rather than raising --- a scripted file is data from outside this
    process, like everything else here.
    """
    text = Path(path).read_text(encoding="utf-8")
    stripped = text.lstrip()
    events: list[dict] = []
    if stripped.startswith("["):
        payload = json.loads(text)
        candidates: Iterable[Any] = payload if isinstance(payload, list) else []
    else:
        candidates = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                candidates = [*candidates, json.loads(line)]
            except ValueError:
                continue
    for item in candidates:
        if isinstance(item, dict):
            events.append(item)
    return events


def events_file_source(path: str | Path) -> Callable[["Listener"], None]:
    """A source that replays a scripted events file, then drains and stops."""
    events = read_events_file(path)

    def source(listener: "Listener") -> None:
        for event in events:
            if listener.stopping:
                return
            listener.submit(event)
        # End of the script is the end of the audio: flush whatever the
        # joiner is still holding, exactly as waiting out the gap would.
        listener.submit_drain()
        listener.wait_for_drain()

    return source


def wav_audio_source(path: str | Path, *, chunk_bytes: int = CAPTURE_CHUNK_BYTES):
    """A ``LobesClient`` audio source that streams a WAV's PCM16 frames.

    Returns ``None`` once the file is exhausted, which is how the client's
    feeder learns the session is over (it then closes the turn with a tail of
    silence and ends the session by name --- see
    :meth:`shabbos_goy.lobes.LobesClient._end_of_source`).
    """
    import wave

    handle = wave.open(str(path), "rb")  # noqa: SIM115 - closed when exhausted
    frame_size = max(1, handle.getsampwidth() * handle.getnchannels())
    frames = max(1, chunk_bytes // frame_size)
    done = threading.Event()

    def close() -> None:
        """Idempotent: the reader closes on EOF, teardown closes regardless."""
        if not done.is_set():
            done.set()
        try:
            handle.close()
        except (OSError, ValueError, AttributeError):  # pragma: no cover
            pass

    def read() -> Optional[bytes]:
        if done.is_set():
            return None
        data = handle.readframes(frames)
        if not data:
            close()
            return None
        return data

    read.close = close  # type: ignore[attr-defined]
    return read


def _wait_for_capture(process: Any) -> None:
    """Reap the capture child, escalating to kill if it ignores terminate."""
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:  # pragma: no cover - a wedged child
        try:
            process.kill()
            process.wait(timeout=2.0)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    except (OSError, ValueError):  # pragma: no cover - already reaped
        pass


def _reap_capture(process: Any) -> None:
    """Terminate the capture child, wait for it, close its pipe. Idempotent."""
    try:
        if process.poll() is None:
            process.terminate()
    except (OSError, ValueError):  # pragma: no cover - already gone
        pass
    _wait_for_capture(process)
    stream = getattr(process, "stdout", None)
    if stream is not None:
        try:
            stream.close()
        except (OSError, ValueError):  # pragma: no cover
            pass


def pipewire_audio_source(
    node: str, *, chunk_bytes: int = CAPTURE_CHUNK_BYTES, popen=subprocess.Popen
):
    """Start ``pw-record`` on ``node`` and stream its stdout in chunks.

    This is the mic feeder's data supply: the capture process is started
    here, the client's own feeder thread pulls from it, and nothing mutes it
    --- own-voice suppression happens downstream, by discarding transcripts.

    The returned callable carries a ``close()`` that **terminates and reaps
    the child**. A capture process must never outlive the session that
    started it: without this, every reconnect would leave another ``pw-record``
    holding the microphone, and the box would slowly run out of them.
    ``start_capture`` sends the child's stderr to ``DEVNULL`` for the same
    reason --- an undrained pipe is a child that blocks forever on a full
    buffer.
    """
    process = pipewire.start_capture(node, popen=popen)

    def read() -> Optional[bytes]:
        stream = process.stdout
        if stream is None:
            return None
        try:
            data = stream.read(chunk_bytes)
        except (OSError, ValueError):
            # The pipe was closed under us by close(): EOF, not an error.
            return None
        return data if data else None

    def close() -> None:
        """Terminate the capture child and wait for it. Safe to call twice."""
        _reap_capture(process)

    read.process = process  # type: ignore[attr-defined]
    read.close = close  # type: ignore[attr-defined]
    return read


def _close_audio_source(listener: "Listener", audio: Any) -> None:
    """Close an audio source that has one. Never raises, never names a device."""
    closer = getattr(audio, "close", None)
    if closer is None:
        return
    try:
        closer()
    except Exception as exc:  # noqa: BLE001 - name it; teardown continues
        listener.note("capture_close_failed", type(exc).__name__)


def _open_audio_source(listener: "Listener", factory: Optional[Callable[[], Any]]) -> Any:
    """Build the capture source, if there is one. A failure is named, not fatal."""
    if factory is None:
        return None
    try:
        return factory()
    except Exception as exc:  # noqa: BLE001
        # Name it, keep listening: capture can come back on the next attempt.
        listener.note("capture_failed", type(exc).__name__)
        return None


def _run_lobes_session(
    listener: "Listener",
    config: Any,
    audio: Any,
    client_factory: Optional[Callable[..., Any]],
) -> bool:
    """One connection, start to finish. True means the audio is over for good."""
    listener.connection.note_connecting()
    factory = client_factory or LobesClient
    client = factory(
        config,
        listener.on_lobes_event,
        audio_source=audio,
        # The client's watchdog fires on THIS thread, where sys.exit would
        # only end the thread and run() would return 0. Hand it a stall
        # path that stops the whole listener with the stall exit code, so
        # Compose's restart policy (which reacts to exit, never to
        # "unhealthy") brings the container back (spec c31).
        exit_action=listener.stalled,
    )
    listener.attach_client(client)
    try:
        client.run()
    except Exception as exc:  # noqa: BLE001 - a session must not kill us
        listener.note("lobes_session_failed", type(exc).__name__)
    finally:
        listener.attach_client(None)
        _close_audio_source(listener, audio)
    return bool(getattr(client, "source_ended", False))


def lobes_source(
    *,
    env: Optional[Mapping[str, str]] = None,
    retry_seconds: float = DEFAULT_LOBES_RETRY_SECONDS,
    audio_source_factory: Optional[Callable[[], Any]] = None,
    client_factory: Optional[Callable[..., Any]] = None,
) -> Callable[["Listener"], None]:
    """The real ears: connect, stream, reconnect, for as long as we run.

    A missing or malformed lobes environment is **not** fatal. It is one
    named line and another attempt later, so a box that boots before its
    secrets are mounted comes back on its own rather than crash-looping
    through Shabbat.

    Two endings are not "try again":

    * a **finite** audio source (a ``--script`` WAV) that reached EOF --- the
      client closed the last turn and named the session ``source_ended``.
      Reconnecting would replay the file from the top and the run would never
      finish. A microphone never ends, so this only ever happens on the
      fixtures path.
    * the listener stopping (SIGTERM).

    The audio source is closed in a ``finally`` on **every** session end, so
    no capture child survives a reconnect.
    """

    def source(listener: "Listener") -> None:
        while not listener.stopping:
            environment = os.environ if env is None else env
            try:
                config = config_from_env(environment)
            except LobesConfigError:
                # Never the variable's value: it is the key.
                listener.note("lobes_env_missing")
                listener.wait_for_stop(retry_seconds)
                continue

            audio = _open_audio_source(listener, audio_source_factory)
            if _run_lobes_session(listener, config, audio, client_factory):
                listener.note("source_ended")
                return
            if not listener.stopping:
                listener.note("lobes_reconnecting")
                listener.wait_for_stop(retry_seconds)

    return source


# ---------------------------------------------------------------------------
# the listener
# ---------------------------------------------------------------------------


class Listener:
    """The ambient loop: ears -> queue -> pipeline, plus its servers."""

    # Every argument below is a keyword-only dependency-injection seam with a
    # default, relied on by the test suite; grouping them would hide the seams.
    def __init__(
        self,  # NOSONAR - S107 is anchored on the parameter list; reason above
        *,
        config: Config,
        decider: Any,
        source: Callable[["Listener"], None],
        options: Optional[ListenerOptions] = None,
        mode_provider: Optional[Callable[[], Any]] = None,
        mode_at: Optional[Callable[[float], Any]] = None,
        pod_id: str = "",
        ac_power: Optional[Callable[..., Mapping[str, Any]]] = None,
        ac_status: Optional[Callable[[str], Mapping[str, Any]]] = None,
        volume_step: Optional[Callable[[int], Any]] = None,
        volume_get: Optional[Callable[[], Any]] = None,
        speak: Optional[Callable[[str], None]] = None,
        startup_audio: Optional[Callable[[], Any]] = None,
        heartbeat: Optional[Heartbeat] = None,
        heartbeat_path: Optional[str | Path] = None,
        clock: Callable[[], float] = time.monotonic,
        now_provider: Optional[Callable[[], datetime]] = None,
        note_sink: Optional[Callable[[RuntimeNote], None]] = None,
        log: Optional[Callable[[Any], None]] = None,
        dashboard_server_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.config = config
        self.options = options if options is not None else ListenerOptions()
        self._clock = clock
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._note_sink = note_sink if note_sink is not None else _stderr_note
        self._source = source
        self._startup_audio = startup_audio
        self._dashboard_server_factory = dashboard_server_factory

        self.notes: BoundedRing[RuntimeNote] = BoundedRing(NOTE_CAPACITY)
        self.connection = ConnectionMonitor(clock=clock)
        self.heartbeat = (
            heartbeat
            if heartbeat is not None
            else Heartbeat(resolve_heartbeat_path(override=heartbeat_path))
        )

        self._mode_provider = mode_provider or (
            lambda: resolve_mode(self._now_provider(), self.config)
        )
        # The same resolution, at an arbitrary wall-clock instant, so the
        # pipeline can judge an utterance by the window in force when SPEECH
        # STARTED rather than when the decision completed. Without this the
        # window-close fix is inert in the real deployment: the pipeline's
        # mode_at is optional and falls back to decision-time only.
        #
        # INJECTABLE, and it must be: an injected mode_provider that is not
        # paired with an injected mode_at gives the pipeline two readings of
        # ONE utterance drawn from different mode, clock and trust policies,
        # and stricter_mode then refuses an utterance the injected policy
        # permits. The production default is used only when neither is
        # supplied, so real calendar and host-clock state cannot leak into an
        # injected setup.
        if mode_at is not None:
            self._mode_at = mode_at
        elif mode_provider is not None:
            # A caller who replaced the current-mode policy but said nothing
            # about history gets its OWN policy at both instants rather than
            # a silent mix with the host's zmanim.
            self._mode_at = lambda at: self._mode_provider()
        else:
            self._mode_at = lambda at: resolve_mode(
                datetime.fromtimestamp(at, tz=timezone.utc), self.config
            )

        pipeline_kwargs: dict[str, Any] = {
            "decider": decider,
            "config": config,
            "mode_provider": self._mode_provider,
            "mode_at": self._mode_at,
            "joiner_wall_clock": self._speech_wall_clock,
            "pod_id": pod_id,
            "ac_power": self._guarded(ac_power),
            "ac_status": ac_status,
            "volume_step": self._guarded(volume_step),
            "speak": speak,
            "clock": clock,
            "apply": self.options.apply,
        }
        if log is not None:
            pipeline_kwargs["log"] = log
        self.pipeline = Pipeline(**pipeline_kwargs)
        self._volume_get = volume_get

        join_gap = config.join_gap_ms
        self._join_gap_ms = (
            join_gap
            if isinstance(join_gap, int) and not isinstance(join_gap, bool) and join_gap > 0
            else DEFAULT_JOIN_GAP_MS
        )

        self._queue: queue.Queue = queue.Queue(maxsize=max(1, self.options.queue_size))
        # Set by the worker for the duration of one event's dispatch; read by
        # the joiner through joiner_wall_clock. Worker-only, single-threaded.
        self._receipt_wall: Optional[float] = None
        self._tick = threading.Event()
        self._stopping = threading.Event()
        self._exit_code = 0
        self._drained = threading.Event()
        self._paused = threading.Event()
        self._threads: list[threading.Thread] = []
        self._client: Any = None
        self._client_stats = {"pings": 0, "audio": 0}
        self._audio_anchor: Optional[tuple[int, float]] = None
        self._started = False

        #: Serialises every adapter call, whoever makes it: the worker thread
        #: on the voice path, and an HTTP thread on the control path.
        self.actuator_lock = threading.RLock()

        self.queue_dropped = 0
        self._sink_failures = 0
        self.dashboard: Any = None
        self.control: Any = None
        self._pending_dashboard: Any = None
        self._last_bind_attempt = 0.0

    # -- small helpers -----------------------------------------------------

    def _guarded(self, adapter):
        """Wrap an adapter so only one thread is ever inside it."""
        if adapter is None:
            return None

        def call(*args, **kwargs):
            with self.actuator_lock:
                return adapter(*args, **kwargs)

        return call

    def note(self, event: str, detail: str = "") -> None:
        """Record and print one named diagnostic line."""
        record = RuntimeNote(event=event, detail=detail)
        self.notes.append(record)
        try:
            self._note_sink(record)
        except Exception:  # noqa: BLE001 - a broken sink must not deafen us
            self._sink_failures += 1

    def resolved_mode(self) -> Any:
        return self._mode_provider()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set()

    def wait_for_stop(self, timeout: Optional[float] = None) -> bool:
        return self._stopping.wait(timeout)

    def wait_for_drain(self, timeout: float = 5.0) -> bool:
        """Block until the worker has caught up (or the listener is stopping)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._stopping.is_set() or (self._queue.empty() and self._drained.is_set()):
                return True
            time.sleep(0.005)
        return self._queue.empty()

    def queue_depth(self) -> int:
        return self._queue.qsize()

    def threads(self) -> list[threading.Thread]:
        return list(self._threads)

    def pause_worker(self) -> None:
        """Test seam: hold the worker so the queue's bound can be observed."""
        self._paused.set()

    def resume_worker(self) -> None:
        self._paused.clear()

    # -- ingress -----------------------------------------------------------

    def submit(self, event: Any) -> None:
        """Hand one lobes event to the worker. Never blocks, never spools.

        Called from the source thread (and, in tests, from several at once).
        A full queue drops its *oldest* entry: the newest second of speech is
        always the one worth keeping, and a dropped fragment can only ever
        cost a missed hint --- never a false action, since the joiner treats
        an incomplete chain as incomplete.
        """
        wire = event if isinstance(event, Mapping) else None
        if wire is not None:
            at_ms = wire.get("at_ms")
            if isinstance(at_ms, int) and not isinstance(at_ms, bool):
                self._audio_anchor = (at_ms, self._clock())
            if wire.get("type") == ev.TYPE_TRANSCRIPT:
                self.heartbeat.mark("transcript")
            # A scripted replay goes through exactly the same state as a live
            # session, so the dashboard reads the same way either way.
            self.connection.handle_event(ev.normalize_event(wire))
        self._offer(_Received(event, time.time()))

    def submit_drain(self) -> None:
        """Ask the worker to flush the joiner as an end-of-audio would."""
        self._drained.clear()
        self._offer(_DRAIN)

    def _offer(self, item: Any) -> None:
        while True:
            try:
                self._queue.put_nowait(item)
                return
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self.queue_dropped += 1
                except queue.Empty:  # pragma: no cover - another thread won
                    continue

    def on_lobes_event(self, event: Any) -> None:
        """The :class:`LobesClient` callback. Runs on the reader thread."""
        self.connection.handle_event(event)
        kind = getattr(event, "kind", None)
        if kind == ev.KIND_TRANSCRIPT:
            self.heartbeat.mark("transcript")
        self._offer(_Received(event, time.time()))
        at_ms = getattr(event, "at_ms", None)
        if isinstance(at_ms, int) and not isinstance(at_ms, bool):
            self._audio_anchor = (at_ms, self._clock())

    def attach_client(self, client: Any) -> None:
        self._client = client
        self._client_stats = {"pings": 0, "audio": 0}

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Compute the mode, apply the configured volume, bind, and run."""
        if self._started:
            return
        self._started = True

        resolved = self.resolved_mode()
        self.note("mode", str(getattr(resolved, "mode", "strict")))
        if not getattr(resolved, "clock_trusted", True):
            # Never fail toward acting: the pipeline already forces strict,
            # but an operator should see why.
            self.note("clock_untrusted")

        self._apply_startup_audio()
        self._start_servers()

        for name, target in (
            ("shabbos-goy-worker", self._worker_loop),
            ("shabbos-goy-ticker", self._ticker_loop),
            ("shabbos-goy-source", self._source_loop),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

        # Write the heartbeat once immediately, so a healthcheck that runs a
        # moment after boot finds a file (reporting "no activity yet", which
        # is the truth) rather than "missing".
        self.heartbeat.write()

    def run(self) -> int:
        """Start, run until the ears stop or we are asked to, then shut down."""
        self.start()
        try:
            for thread in self._threads:
                if thread.name == "shabbos-goy-source":
                    while thread.is_alive() and not self._stopping.is_set():
                        thread.join(timeout=0.1)
        except KeyboardInterrupt:  # pragma: no cover - interactive only
            self.request_stop("SIGINT")
        finally:
            self.stop()
        return self._exit_code

    def stalled(self, code: int = 3) -> None:
        """The ears froze (socket open, no frames): stop, and exit non-zero."""
        self.note("lobes_stalled", str(code))
        self._exit_code = int(code) or 3
        self.request_stop("stalled")

    def request_stop(self, reason: str = "") -> None:
        """Ask for a clean shutdown (a signal handler's whole job)."""
        if not self._stopping.is_set():
            self.note("shutdown", reason)
        self._stopping.set()
        client = self._client
        if client is not None:
            try:
                client.stop()
            except Exception as exc:  # noqa: BLE001 - shutting down regardless
                self.note("client_stop_failed", type(exc).__name__)

    def install_signal_handlers(self) -> None:
        """SIGTERM/SIGINT -> a clean stop. ``docker stop`` sends SIGTERM."""
        for signum in (signal.SIGTERM, signal.SIGINT):
            # `name=` binds per iteration: a late-bound closure over `signum`
            # would make every signal report the last one installed.
            def handler(*_args, name: str = signal.Signals(signum).name) -> None:
                self.request_stop(name)

            try:
                signal.signal(signum, handler)
            except (ValueError, OSError):  # pragma: no cover - not the main thread
                pass

    def stop(self, timeout: float = DEFAULT_SHUTDOWN_TIMEOUT) -> None:
        """Stop everything, joining every thread with a bound. Idempotent."""
        if not self._started:
            return
        # Give the worker a moment to finish what is already queued, so a
        # scripted run's last utterance is not lost to the shutdown. Nothing
        # is persisted either way (invariant #3).
        self._stopping.set()
        self.resume_worker()
        self.wait_for_drain(timeout=min(2.0, timeout))
        self.request_stop("stop")

        deadline = time.monotonic() + timeout
        for thread in self._threads:
            remaining = max(0.05, deadline - time.monotonic())
            thread.join(timeout=remaining)
            if thread.is_alive():
                self.note("thread_did_not_stop", thread.name)

        for server in (self.dashboard, self.control):
            if server is not None:
                try:
                    server.stop()
                except Exception as exc:  # noqa: BLE001 - shutting down regardless
                    self.note("server_stop_failed", type(exc).__name__)
        self.dashboard = self.control = None
        self._started = False

    # -- servers -----------------------------------------------------------

    @property
    def control_url(self) -> Optional[str]:
        return self.control.url if self.control is not None else None

    @property
    def dashboard_url(self) -> Optional[str]:
        return self.dashboard.url if self.dashboard is not None else None

    def _controls(self) -> Any:
        controls = web_server.controls_from_pipeline(self.pipeline)
        return web_server.Controls(
            pod_id=controls.pod_id,
            pod_alias=controls.pod_alias,
            volume_key=controls.volume_key,
            volume_alias=controls.volume_alias,
            ac_power=controls.ac_power,
            ac_status=controls.ac_status,
            volume_step=controls.volume_step,
            volume_get=self._guarded(self._volume_get),
            apply=controls.apply,
        )

    def _server_kwargs(self) -> dict[str, Any]:
        return {
            "controls": self._controls(),
            "connection_provider": self.connection.snapshot,
            "latency_provider": self.pipeline.decide_latencies,
            "health_provider": self.heartbeat.health,
            "now_provider": self._now_provider,
            "clock": self._clock,
        }

    def _start_servers(self) -> None:
        if self.options.control:
            kwargs = self._server_kwargs()
            kwargs["bind_address"] = self.options.control_address
            self.control = web_server.control_server(
                self.pipeline, self._mode_provider, self.config, **kwargs
            )
            result = self.control.start()
            if not result.ok:
                self.note("control_bind_failed", result.reason)
            else:
                self.note("control_bound")

        if not self.options.dashboard:
            return

        kwargs = self._server_kwargs()
        if self._dashboard_server_factory is not None:
            kwargs["server_factory"] = self._dashboard_server_factory
        candidate = web_server.DashboardServer(
            self.pipeline, self._mode_provider, self.config, **kwargs
        )
        if self._shares_the_control_server(candidate):
            # One address, one server: the dashboard and the CLI endpoint are
            # the same handler anyway, and binding it twice would just fail.
            self.dashboard = self.control
            self.note("dashboard_shared")
            return
        self._pending_dashboard = candidate
        self._try_dashboard_bind()

    def _shares_the_control_server(self, candidate: Any) -> bool:
        if self.control is None or not self.control.bind_result.ok:
            return False
        try:
            host, port = candidate.resolve_bind()
        except Exception:  # noqa: BLE001 - a refusal is not "shared"
            return False
        # Port 0 means "any free port", so two such servers never collide.
        return port != 0 and (host, port) == (
            self.control.bind_result.address,
            self.control.bind_result.port,
        )

    def _try_dashboard_bind(self) -> None:
        candidate = self._pending_dashboard
        if candidate is None:
            return
        self._last_bind_attempt = time.monotonic()
        result = candidate.start()
        if result.ok:
            self.dashboard = candidate
            self._pending_dashboard = None
            self.note("dashboard_bound")
            return
        # A tailnet address that is not up yet is the common case, and it is
        # not a reason to stop listening: retry on the ticker.
        self.note("dashboard_bind_failed", result.reason)

    def _apply_startup_audio(self) -> None:
        action = self._startup_audio
        if action is None:
            action = self._default_startup_audio()
        if action is None:
            self.note("startup_volume_skipped", "no_audio_config")
            return
        try:
            action()
        except Exception as exc:  # noqa: BLE001 - never a reason not to listen
            self.note("startup_volume_failed", type(exc).__name__)
            return
        self.note("startup_volume")

    def _default_startup_audio(self) -> Optional[Callable[[], Any]]:
        """Re-apply the configured level/mute (silent by default) at boot."""
        node = self.config.volume_node
        if node is None:
            return None
        volume = self.config.volume if isinstance(self.config.volume, dict) else {}
        bounds = pipewire.VolumeBounds(
            min_volume=float(volume.get("min", pipewire.DEFAULT_MIN_VOLUME)),
            max_volume=float(volume.get("max", pipewire.DEFAULT_MAX_VOLUME)),
            step=float(volume.get("step", pipewire.DEFAULT_VOLUME_STEP)),
        )
        audio_config = pipewire.AudioConfig(
            mic_node=self.config.mic_node or node,
            speaker_node=self.config.speaker_node or node,
            volume_node=node,
            bounds=bounds,
            default_volume=float(volume.get("level", pipewire.DEFAULT_MIN_VOLUME)),
            default_muted=bool(volume.get("muted", True)),
            allow_split_devices=True,
        )
        return lambda: pipewire.apply_startup_state(audio_config)

    # -- the threads -------------------------------------------------------

    def _source_loop(self) -> None:
        try:
            self._source(self)
        except Exception as exc:  # noqa: BLE001 - name it; never a traceback
            self.note("source_failed", type(exc).__name__)

    def _worker_loop(self) -> None:
        """The only thread that touches the pipeline's state machine."""
        while True:
            try:
                item = self._queue.get(timeout=self.options.poll_interval)
            except queue.Empty:
                item = _TICK
            if self._paused.is_set() and item is not _TICK:
                if self._idle_while_paused(item):
                    return
                continue
            self._dispatch(item)
            if self._stopping.is_set() and self._queue.empty():
                return

    def _idle_while_paused(self, item: Any) -> bool:
        """Put *item* back and idle. True when the worker should stop."""
        # A test seam only: put it back and idle.
        self._offer(item)
        if self._stopping.is_set():
            return True
        time.sleep(0.005)
        return False

    def _dispatch(self, item: Any) -> None:
        """Route one queue item. The order of these three cases is the contract."""
        if item is _DRAIN:
            self._poll(drain=True)
            self._drained.set()
        elif item is _TICK or self._tick.is_set():
            self._tick.clear()
            if item is not _TICK:
                self._handle(item)
            self._poll()
        else:
            self._handle(item)

    def _handle(self, event: Any) -> None:
        if isinstance(event, _Received):
            self._receipt_wall = event.wall
            event = event.event
        try:
            self.pipeline.handle_event(event)
        except Exception as exc:  # noqa: BLE001 - a bug must not deafen us
            self.note("pipeline_error", type(exc).__name__)
        finally:
            self._receipt_wall = None

    def _speech_wall_clock(self) -> float:
        """The instant the event being handled was RECEIVED, not now.

        Falls back to the current time only outside event dispatch (the
        joiner's own timeout flush), where there is no receipt to honour.
        """
        received = self._receipt_wall
        return float(received) if received is not None else time.time()

    def _poll(self, *, drain: bool = False) -> None:
        """Advance the joiner's and the delay timer's clocks.

        ``at_ms`` is the *audio stream's* clock, and it only moves when
        events arrive --- so in silence the joiner would never time out a
        half-finished chain. The projection below runs it forward in real
        time from the last event seen, which is what the stream itself is
        doing. A drain jumps past the gap outright: the audio has ended.
        """
        anchor = self._audio_anchor
        now_ms: Optional[int] = None
        if anchor is not None:
            at_ms, at_clock = anchor
            now_ms = at_ms + int(max(0.0, self._clock() - at_clock) * 1000.0)
            if drain:
                now_ms = max(now_ms, at_ms + self._join_gap_ms + 1)
        try:
            self.pipeline.poll(now_ms)
        except Exception as exc:  # noqa: BLE001
            self.note("pipeline_error", type(exc).__name__)

    def _ticker_loop(self) -> None:
        while not self._stopping.wait(self.options.poll_interval):
            self._tick.set()
            self._sample_client_stats()
            self.heartbeat.write()
            self._maybe_rebind_dashboard()

    def _sample_client_stats(self) -> None:
        client = self._client
        stats = getattr(client, "stats", None)
        if stats is None:
            return
        pings = int(getattr(stats, "pings_answered", 0))
        chunks = int(getattr(stats, "audio_chunks_sent", 0))
        if pings > self._client_stats["pings"]:
            self.heartbeat.mark("pong")
        if chunks > self._client_stats["audio"]:
            self.heartbeat.mark("audio")
        self._client_stats = {"pings": pings, "audio": chunks}

    def _maybe_rebind_dashboard(self) -> None:
        if self._pending_dashboard is None:
            return
        if time.monotonic() - self._last_bind_attempt < self.options.bind_retry_seconds:
            return
        self._try_dashboard_bind()

    def __repr__(self) -> str:  # counts only, never text
        return (
            f"Listener(apply={self.options.apply}, queued={self.queue_depth()}, "
            f"dropped={self.queue_dropped}, threads={len(self._threads)})"
        )

    __str__ = __repr__
