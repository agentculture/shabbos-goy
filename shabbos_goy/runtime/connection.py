"""The lobes connection's state, as the dashboard is allowed to see it.

:class:`~shabbos_goy.lobes.LobesClient` already reports everything that
happens to a session as normalised events, and already counts what it has
seen. What it does not have is a single word for "where are we right now",
which is the one thing an operator glancing at the page needs.

This is that word, plus the attempt count behind it. It is deliberately
**text-free**: a :class:`~shabbos_goy.lobes.events.LobesEvent` carries the
transcript in ``text`` and a human-readable (host-quoting, key-hinting)
string in ``message``, and neither is ever copied into a snapshot that a page
renders and a log line might echo. Only kinds, codes and counters cross this
boundary.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from ..lobes import events as ev
from ..lobes.client import ERROR_AUTH_FAILED

__all__ = [
    "STATE_AUTH_FAILED",
    "STATE_CONNECTED",
    "STATE_CONNECTING",
    "STATE_IDLE",
    "STATE_RECONNECTING",
    "STATE_STALLED",
    "ConnectionMonitor",
]

STATE_IDLE = "idle"
STATE_CONNECTING = "connecting"
STATE_CONNECTED = "connected"
STATE_RECONNECTING = "reconnecting"
STATE_AUTH_FAILED = "auth_failed"
STATE_STALLED = "stalled"

#: Kinds that mean "this session is over". Each one bumps the attempt count,
#: because whatever happens next is a reconnection.
_LOST_KINDS = frozenset({ev.KIND_CONNECTION_LOST, ev.KIND_SESSION_CLOSED})

#: Kinds that prove the far end is alive and talking to us.
_ALIVE_KINDS = frozenset(
    {
        ev.KIND_SESSION_CREATED,
        ev.KIND_TRANSCRIPT,
        ev.KIND_SPEECH_STARTED,
        ev.KIND_SPEECH_STOPPED,
    }
)


class ConnectionMonitor:
    """Turns the client's event stream into one connection state.

    Safe to call from the reader thread while the dashboard thread reads
    :meth:`snapshot`: every mutation is under one lock, and the snapshot is a
    copy.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state = STATE_IDLE
        self._reason = ""
        self._attempts = 0
        self._transcripts = 0
        self._errors = 0
        self._last_event_at: Optional[float] = None

    # -- transitions -------------------------------------------------------

    def note_connecting(self) -> None:
        with self._lock:
            self._state = STATE_CONNECTING
            self._reason = ""

    def handle_event(self, event: Any) -> None:
        """Fold one :class:`LobesEvent` into the state. Never raises."""
        kind = getattr(event, "kind", None)
        code = getattr(event, "code", None)
        with self._lock:
            self._last_event_at = self._clock()
            if kind == ev.KIND_TRANSCRIPT:
                self._transcripts += 1
            if kind in _ALIVE_KINDS:
                self._state = STATE_CONNECTED
                self._reason = ""
                self._attempts = 0
                return
            if kind == ev.KIND_STALLED:
                self._state = STATE_STALLED
                self._reason = str(code or "stalled")
                return
            if kind == ev.KIND_ERROR:
                self._errors += 1
                if code == ERROR_AUTH_FAILED:
                    self._state = STATE_AUTH_FAILED
                    self._reason = ERROR_AUTH_FAILED
                    return
                # A named client-side error ("cannot reach the gateway") is a
                # failed attempt; a server-side one is not -- the session is
                # still up.
                if self._state != STATE_CONNECTED:
                    self._state = STATE_RECONNECTING
                    self._attempts += 1
                    self._reason = str(code or "error")
                return
            if kind in _LOST_KINDS:
                self._state = STATE_RECONNECTING
                self._attempts += 1
                self._reason = str(kind)

    # -- reading -----------------------------------------------------------

    def snapshot(self) -> dict:
        """The dashboard's ``connection`` block. Counters and codes only."""
        with self._lock:
            age = None
            if self._last_event_at is not None:
                age = max(0.0, self._clock() - self._last_event_at)
            return {
                "state": self._state,
                "attempts": self._attempts,
                "transcripts": self._transcripts,
                "errors": self._errors,
                "reason": self._reason,
                "last_event_age_seconds": age,
            }

    def __repr__(self) -> str:  # never a message, never text
        return f"ConnectionMonitor(state={self._state!r}, attempts={self._attempts})"

    __str__ = __repr__
