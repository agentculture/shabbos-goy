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

import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Generic, Optional, TypeVar

T = TypeVar("T")

#: A clock is any zero-argument callable returning seconds as a float. It
#: need not be wall-clock time (tests use a fake, advanceable clock); it
#: only needs to be monotonically non-decreasing for the semantics below
#: to hold.
Clock = Callable[[], float]


def system_clock() -> float:
    """The default clock: monotonic wall time. Never used in tests."""
    return time.monotonic()


class BoundedRing(Generic[T]):
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

    @property
    def capacity(self) -> int:
        return self._capacity

    def append(self, item: T) -> None:
        self._items.append(item)

    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self):
        return iter(self._items)

    def is_full(self) -> bool:
        return len(self._items) >= self._capacity

    def latest(self) -> Optional[T]:
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

    @classmethod
    def from_dict(cls, data: dict) -> "LimitsConfig":
        return cls(
            min_interval_seconds=float(data["min_interval_seconds"]),
            daily_cap=int(data["daily_cap"]),
            strict_delay_seconds=float(data.get("strict_delay_seconds", 15.0)),
            retry_window_seconds=float(data.get("retry_window_seconds", 300.0)),
            retry_max_attempts=int(data.get("retry_max_attempts", 5)),
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
        self._daily_events: dict[str, BoundedRing[float]] = {}
        self._refusal_log: BoundedRing[Refusal] = BoundedRing(log_capacity)

    def _bucket(self, key: str) -> BoundedRing[float]:
        ring = self._daily_events.get(key)
        if ring is None:
            ring = BoundedRing(self._config.daily_cap)
            self._daily_events[key] = ring
        return ring

    def check(self, key: str = "default") -> tuple[bool, Optional[str]]:
        """Return (allowed, reason). Does not mutate any counters — call
        ``record()`` afterwards if (and only if) the action actually ran."""
        now = self._clock()

        last = self._last_change_at.get(key)
        if last is not None:
            elapsed = now - last
            if elapsed < self._config.min_interval_seconds:
                reason = (
                    f"minimum interval not elapsed for {key!r}: "
                    f"{elapsed:.1f}s < {self._config.min_interval_seconds:.1f}s"
                )
                self._log_refusal(key, now, reason)
                return False, reason

        ring = self._bucket(key)
        window_start = now - 86400.0
        count_in_window = sum(1 for t in ring if t >= window_start)
        if count_in_window >= self._config.daily_cap:
            reason = (
                f"daily cap reached for {key!r}: " f"{count_in_window}/{self._config.daily_cap}"
            )
            self._log_refusal(key, now, reason)
            return False, reason

        return True, None

    def record(self, key: str = "default") -> None:
        """Record that the action actually happened. Only call this after
        a ``check()`` that returned ``True``."""
        now = self._clock()
        self._last_change_at[key] = now
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
