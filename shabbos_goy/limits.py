"""In-memory rate limits, strict-mode delay, and bounded retry/ring buffers.

Everything in this module is memory-only. Nothing here writes to disk, opens
a socket, or sleeps: every time-based type takes an injected clock (a
zero-argument callable returning a monotonically increasing float), so tests
can drive hours of simulated time without waiting.

This module underpins invariant #3 in CLAUDE.md ("Imperatives are dropped,
not queued... persist no pending actions or transcript buffers across
restarts"): a refused action is logged and discarded, never queued for
later replay, and every stateful type here starts empty the moment it is
constructed — which is exactly what "after a restart" means for a process
that persists nothing.

Config values (minimum interval, daily cap, strict-mode delay, retry window)
are supplied by the caller via ``LimitsConfig.from_dict`` — this module has
no opinion on where that dict came from (YAML file, defaults, etc.) and
imports nothing beyond the standard library, keeping the package's
``dependencies = []`` contract intact.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Optional

#: A clock is any zero-argument callable returning seconds as a float. It
#: need not be wall-clock time (tests use a fake, advanceable clock); it
#: only needs to be monotonically non-decreasing for the semantics below
#: to hold.
Clock = Callable[[], float]


def system_clock() -> float:
    """The default clock: monotonic wall time. Never used in tests."""
    return time.monotonic()


class BoundedRing[T]:
    """A fixed-capacity FIFO buffer that evicts its oldest entry on overflow.

    Every buffer this agent holds in memory — raw event logs, transcript
    joining buffers, refusal logs, pending-timer queues, retry-attempt
    logs — is built on this type, so no buffer can grow without bound
    regardless of how long the process has been listening.
    """

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._items: Deque[T] = deque(maxlen=capacity)
        # One thread appends (the pipeline worker) while others read (the
        # dashboard's HTTP threads). Iterating a live deque that is being
        # appended to raises "deque mutated during iteration", so every read
        # works on a snapshot taken under this lock.
        self._lock = threading.Lock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(self, item: T) -> None:
        with self._lock:
            self._items.append(item)

    def snapshot(self) -> list[T]:
        """The current contents, oldest first, safe to iterate from any thread."""
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)

    def __iter__(self):
        return iter(self.snapshot())

    def is_full(self) -> bool:
        return len(self) >= self._capacity

    def latest(self) -> Optional[T]:
        with self._lock:
            return self._items[-1] if self._items else None


@dataclass(frozen=True)
class LimitsConfig:
    """Rate-limit and timing configuration. Values come from config, not
    from code — this dataclass just gives them names and types."""

    min_interval_seconds: float
    daily_cap: int
    strict_delay_seconds: float = 15.0
    retry_window_seconds: float = 300.0
    retry_max_attempts: int = 5
    #: Power OFF is safe for the unit at any moment, and on Shabbat it is the only
    #: way a cold person can stop the AC: it needs only a short debounce.
    off_min_interval_seconds: float = 60.0
    #: Power ON soon after OFF short-cycles the compressor (the refrigerant
    #: pressures have not equalised). This is the interval that protects it.
    on_after_off_min_interval_seconds: float = 240.0

    @classmethod
    def from_dict(cls, data: dict) -> "LimitsConfig":
        return cls(
            min_interval_seconds=float(data["min_interval_seconds"]),
            daily_cap=int(data["daily_cap"]),
            strict_delay_seconds=float(data.get("strict_delay_seconds", 15.0)),
            retry_window_seconds=float(data.get("retry_window_seconds", 300.0)),
            retry_max_attempts=int(data.get("retry_max_attempts", 5)),
            off_min_interval_seconds=float(data.get("off_min_interval_seconds", 60.0)),
            on_after_off_min_interval_seconds=float(
                data.get("on_after_off_min_interval_seconds", 240.0)
            ),
        )


@dataclass(frozen=True)
class Refusal:
    """A record of one refused action. Refusals are logged, never replayed:
    nothing in this module re-attempts a refused action later."""

    at: float
    key: str
    reason: str


class RateLimiter:
    """Enforces a minimum interval between power changes and a daily cap,
    per key (e.g. a Sensibo pod id), in memory only.

    A refusal is terminal for that attempt: ``check()`` returning ``False``
    means the caller must drop the action. This class never queues a
    refused action for a later retry — that would turn a dropped command
    into a delayed one, which is exactly what invariant #3 forbids.
    """

    def __init__(
        self,
        config: LimitsConfig,
        clock: Clock = system_clock,
        log_capacity: int = 200,
    ) -> None:
        self._config = config
        self._clock = clock
        self._last_change_at: dict[str, float] = {}
        self._last_off_at: dict[str, float] = {}
        self._daily_events: dict[str, BoundedRing[float]] = {}
        self._refusal_log: BoundedRing[Refusal] = BoundedRing(log_capacity)

    def _bucket(self, key: str) -> BoundedRing[float]:
        ring = self._daily_events.get(key)
        if ring is None:
            ring = BoundedRing(self._config.daily_cap)
            self._daily_events[key] = ring
        return ring

    def check(
        self,
        key: str = "default",
        *,
        direction: Optional[str] = None,
        operator: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """May ``key`` change now? Call ``record()`` afterwards iff the action ran.

        ``direction`` is ``"on"`` / ``"off"`` for a power change and ``None`` for
        anything else (a volume step), which keeps the symmetric interval.
        Power is asymmetric on purpose: OFF needs only a short debounce since
        the last change; ON must wait the compressor interval since the last
        OFF. ``operator=True`` (CLI, dashboard) bypasses the intervals but not
        the daily cap, and the operator's action still counts for everyone.
        """
        now = self._clock()
        if direction not in (None, "on", "off"):
            reason = f"unknown direction for {key!r}"
            self._log_refusal(key, now, reason)
            return False, reason

        if not operator:
            reason = self._interval_refusal(key, direction, now)
            if reason is not None:
                self._log_refusal(key, now, reason)
                return False, reason

        ring = self._bucket(key)
        window_start = now - 86400.0
        count_in_window = sum(1 for t in ring if t >= window_start)
        if count_in_window >= self._config.daily_cap:
            reason = f"daily cap reached for {key!r}: {count_in_window}/{self._config.daily_cap}"
            self._log_refusal(key, now, reason)
            return False, reason

        return True, None

    def _interval_refusal(self, key: str, direction: Optional[str], now: float) -> Optional[str]:
        last = self._last_change_at.get(key)
        if direction is None:
            needed = self._config.min_interval_seconds
            if last is not None and now - last < needed:
                return (
                    f"minimum interval not elapsed for {key!r}: {now - last:.1f}s < {needed:.1f}s"
                )
            return None
        debounce = self._config.off_min_interval_seconds
        if last is not None and now - last < debounce:
            return f"power debounce not elapsed for {key!r}: {now - last:.1f}s < {debounce:.1f}s"
        if direction == "on":
            last_off = self._last_off_at.get(key)
            needed = self._config.on_after_off_min_interval_seconds
            if last_off is not None and now - last_off < needed:
                return (
                    f"compressor interval not elapsed for {key!r}: "
                    f"{now - last_off:.1f}s since power-off < {needed:.1f}s"
                )
        return None

    def record(self, key: str = "default", *, direction: Optional[str] = None) -> None:
        """Note that the action ran. Only ever call this after it actually did."""
        now = self._clock()
        self._last_change_at[key] = now
        if direction == "off":
            self._last_off_at[key] = now
        self._bucket(key).append(now)

    def _log_refusal(self, key: str, now: float, reason: str) -> None:
        self._refusal_log.append(Refusal(at=now, key=key, reason=reason))

    @property
    def refusal_log(self) -> list[Refusal]:
        return list(self._refusal_log)


@dataclass(frozen=True)
class PendingAction:
    """A strict-mode delayed action awaiting its due time."""

    action: object
    due_at: float


class DelayTimer:
    """Strict-mode delayed-action timer (default 15s).

    Pending actions live only in this instance's memory. There is no file,
    no environment variable, no other process-visible state: constructing a
    new ``DelayTimer`` (what a restarted process does) always starts with
    zero pending actions, which is what "provably empty after a restart"
    means for code that persists nothing.
    """

    def __init__(
        self,
        delay_seconds: Optional[float] = None,
        clock: Clock = system_clock,
        capacity: int = 64,
    ) -> None:
        self._delay = 15.0 if delay_seconds is None else delay_seconds
        self._clock = clock
        self._pending: BoundedRing[PendingAction] = BoundedRing(capacity)

    @property
    def delay_seconds(self) -> float:
        return self._delay

    def schedule(self, action: object) -> PendingAction:
        pending = PendingAction(action=action, due_at=self._clock() + self._delay)
        self._pending.append(pending)
        return pending

    def due(self) -> list[PendingAction]:
        """Pending actions whose delay has elapsed, per the injected clock."""
        now = self._clock()
        return [p for p in self._pending if p.due_at <= now]

    def __len__(self) -> int:
        return len(self._pending)


@dataclass(frozen=True)
class RetryAttempt:
    at: float


class RetryWindow:
    """A bounded retry window for failed actuations (e.g. a Sensibo call
    that errored). In memory only; failures older than the window no
    longer count against the attempt cap."""

    def __init__(
        self,
        window_seconds: Optional[float] = None,
        max_attempts: Optional[int] = None,
        clock: Clock = system_clock,
    ) -> None:
        self._window = 300.0 if window_seconds is None else window_seconds
        self._max_attempts = 5 if max_attempts is None else max_attempts
        self._clock = clock
        self._attempts: BoundedRing[RetryAttempt] = BoundedRing(self._max_attempts)

    def record_failure(self) -> None:
        self._attempts.append(RetryAttempt(at=self._clock()))

    def can_retry(self) -> bool:
        now = self._clock()
        window_start = now - self._window
        recent = sum(1 for a in self._attempts if a.at >= window_start)
        return recent < self._max_attempts

    def __len__(self) -> int:
        return len(self._attempts)
