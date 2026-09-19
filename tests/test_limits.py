"""Tests for shabbos_goy.limits: rate limits, strict-mode delay, bounded retry,
and the BoundedRing type every buffer is built from.

Every test uses an injected fake clock. Nothing here sleeps, touches the
network, or writes to disk.
"""

from __future__ import annotations

import os

from shabbos_goy.limits import (
    BoundedRing,
    DelayTimer,
    LimitsConfig,
    RateLimiter,
    RetryWindow,
)


class FakeClock:
    """A controllable, injectable clock. No wall-clock time, no sleeping."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


# ---------------------------------------------------------------------------
# BoundedRing
# ---------------------------------------------------------------------------


def test_bounded_ring_caps_size():
    ring: BoundedRing[int] = BoundedRing(3)
    for i in range(10):
        ring.append(i)
        assert len(ring) <= 3
    assert len(ring) == 3


def test_bounded_ring_evicts_oldest_first():
    ring: BoundedRing[int] = BoundedRing(3)
    for i in range(5):
        ring.append(i)
    # oldest (0, 1) evicted; only the last 3 remain, in order
    assert list(ring) == [2, 3, 4]


def test_bounded_ring_latest():
    ring: BoundedRing[str] = BoundedRing(2)
    assert ring.latest() is None
    ring.append("a")
    ring.append("b")
    assert ring.latest() == "b"


def test_bounded_ring_rejects_non_positive_capacity():
    try:
        BoundedRing(0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for capacity < 1")


# ---------------------------------------------------------------------------
# RateLimiter: minimum interval + daily cap
# ---------------------------------------------------------------------------


def _config(**overrides):
    base = dict(min_interval_seconds=60.0, daily_cap=3)
    base.update(overrides)
    return LimitsConfig.from_dict(base)


def test_min_interval_refuses_immediate_repeat_then_allows_later():
    clock = FakeClock()
    limiter = RateLimiter(_config(), clock=clock)

    ok, reason = limiter.check()
    assert ok
    assert reason is None
    limiter.record()

    # immediately again: refused
    ok, reason = limiter.check()
    assert not ok
    assert "interval" in reason

    # not enough time elapsed
    clock.advance(30.0)
    ok, reason = limiter.check()
    assert not ok

    # interval satisfied
    clock.advance(31.0)
    ok, reason = limiter.check()
    assert ok
    assert reason is None


def test_daily_cap_refuses_once_reached():
    clock = FakeClock()
    limiter = RateLimiter(_config(min_interval_seconds=1.0, daily_cap=2), clock=clock)

    ok, _ = limiter.check()
    assert ok
    limiter.record()
    clock.advance(2.0)

    ok, _ = limiter.check()
    assert ok
    limiter.record()
    clock.advance(2.0)

    ok, reason = limiter.check()
    assert not ok
    assert "cap" in reason


def test_daily_cap_resets_after_a_day_elapses():
    clock = FakeClock()
    limiter = RateLimiter(_config(min_interval_seconds=1.0, daily_cap=1), clock=clock)

    ok, _ = limiter.check()
    assert ok
    limiter.record()

    clock.advance(2.0)
    ok, _ = limiter.check()
    assert not ok  # cap of 1 already used

    clock.advance(86400.0 + 1.0)
    ok, reason = limiter.check()
    assert ok
    assert reason is None


def test_refused_action_is_logged_and_never_replayed():
    clock = FakeClock()
    limiter = RateLimiter(_config(min_interval_seconds=100.0, daily_cap=10), clock=clock)

    limiter.check()
    limiter.record()

    clock.advance(1.0)
    ok, reason = limiter.check()
    assert not ok

    log = limiter.refusal_log
    assert len(log) == 1
    assert log[0].reason == reason

    # a refusal is never replayed: calling check() again does not act, and
    # there is no queue anywhere that later fires this refused attempt.
    clock.advance(1.0)
    ok2, _ = limiter.check()
    assert not ok2
    assert len(limiter.refusal_log) == 2  # each refusal is its own log entry
    # nothing was recorded (no power change happened) for either refusal
    assert len(limiter._daily_events["default"]) == 1


def test_refusal_log_is_bounded():
    clock = FakeClock()
    limiter = RateLimiter(
        _config(min_interval_seconds=1_000_000.0, daily_cap=10),
        clock=clock,
        log_capacity=5,
    )
    limiter.check()
    limiter.record()
    for _ in range(20):
        clock.advance(1.0)
        limiter.check()
    assert len(limiter.refusal_log) == 5


def test_rate_limiter_keys_are_independent():
    clock = FakeClock()
    limiter = RateLimiter(_config(min_interval_seconds=60.0, daily_cap=1), clock=clock)

    ok, _ = limiter.check(key="ac-livingroom")
    assert ok
    limiter.record(key="ac-livingroom")

    # a different device/key is unaffected
    ok, _ = limiter.check(key="light-hallway")
    assert ok


# ---------------------------------------------------------------------------
# DelayTimer (strict mode)
# ---------------------------------------------------------------------------


def test_delay_timer_default_is_15_seconds():
    clock = FakeClock()
    timer = DelayTimer(clock=clock)
    timer.schedule("turn-ac-on")

    assert timer.due() == []
    clock.advance(14.9)
    assert timer.due() == []
    clock.advance(0.2)
    due = timer.due()
    assert len(due) == 1
    assert due[0].action == "turn-ac-on"


def test_delay_timer_pending_is_bounded():
    clock = FakeClock()
    timer = DelayTimer(clock=clock, capacity=4)
    for i in range(10):
        timer.schedule(f"action-{i}")
    assert len(timer) <= 4


def test_delay_timer_fresh_instance_after_restart_is_empty_and_writes_no_file(tmp_path):
    clock = FakeClock()
    timer = DelayTimer(clock=clock)
    timer.schedule("turn-ac-on")
    assert len(timer) == 1

    before = set(os.listdir(tmp_path))

    # simulate a restart: a brand-new process constructs a brand-new timer.
    # nothing from the old timer's pending state can carry over because
    # nothing was ever written to disk.
    restarted = DelayTimer(clock=clock)
    assert len(restarted) == 0
    assert restarted.due() == []

    after = set(os.listdir(tmp_path))
    assert before == after  # the module wrote nothing to disk


# ---------------------------------------------------------------------------
# RetryWindow (bounded retry for failed actuations)
# ---------------------------------------------------------------------------


def test_retry_window_allows_up_to_max_attempts():
    clock = FakeClock()
    window = RetryWindow(window_seconds=300.0, max_attempts=3, clock=clock)

    assert window.can_retry()
    window.record_failure()
    assert window.can_retry()
    window.record_failure()
    assert window.can_retry()
    window.record_failure()
    assert not window.can_retry()


def test_retry_window_is_bounded_in_memory():
    clock = FakeClock()
    window = RetryWindow(window_seconds=300.0, max_attempts=3, clock=clock)
    for _ in range(50):
        window.record_failure()
        clock.advance(1.0)
    assert len(window) <= 3


def test_retry_window_allows_again_once_old_failures_age_out():
    clock = FakeClock()
    window = RetryWindow(window_seconds=60.0, max_attempts=2, clock=clock)
    window.record_failure()
    clock.advance(10.0)
    window.record_failure()
    assert not window.can_retry()

    clock.advance(61.0)  # both failures now older than the 60s window
    assert window.can_retry()


def test_retry_window_fresh_instance_after_restart_is_empty(tmp_path):
    clock = FakeClock()
    window = RetryWindow(clock=clock)
    window.record_failure()
    assert len(window) == 1

    before = set(os.listdir(tmp_path))
    restarted = RetryWindow(clock=clock)
    assert len(restarted) == 0
    after = set(os.listdir(tmp_path))
    assert before == after


# ---------------------------------------------------------------------------
# Soak test: a synthetic 25h+ event stream through ring, joiner-sized
# buffers, and the limiter, all on an injected clock.
# ---------------------------------------------------------------------------


def test_soak_25h_stream_keeps_buffers_bounded_and_admits_a_fresh_hint():
    clock = FakeClock()

    # A generic bounded event ring, standing in for whatever raw event log
    # the agent keeps in memory.
    event_ring: BoundedRing[float] = BoundedRing(500)

    # A "joiner-sized" buffer: small, like the transcript-pause-joining
    # buffer described in CLAUDE.md's speech stack section. The real
    # joiner lives in a sibling task's module and is not imported here;
    # this is our own stand-in built from the same BoundedRing type.
    joiner_buffer: BoundedRing[str] = BoundedRing(32)

    limiter = RateLimiter(
        LimitsConfig.from_dict(dict(min_interval_seconds=60.0, daily_cap=20)),
        clock=clock,
        log_capacity=200,
    )

    total_seconds = 25 * 3600 + 120  # a little past 25 hours
    step_seconds = 15  # a synthetic "hint" arrives every 15s
    steps = total_seconds // step_seconds

    for i in range(steps):
        clock.advance(step_seconds)
        event_ring.append(clock())
        joiner_buffer.append(f"transcript-chunk-{i}")

        ok, _ = limiter.check()
        if ok:
            limiter.record()

        # bounds must hold at every single step, not just at the end
        assert len(event_ring) <= 500
        assert event_ring.capacity == 500
        assert len(joiner_buffer) <= 32
        assert len(limiter.refusal_log) <= 200
        assert len(limiter._daily_events.get("default", [])) <= 20

    # Whatever the last admitted timestamp was (the dense flood keeps the
    # limiter riding right at its cap, admitting a new one the instant an
    # old one ages out — a "conveyor belt" at capacity, which is correct
    # behaviour for sustained flooding), advance a full day plus one
    # minimum-interval past it. That deterministically clears both the
    # daily window and the minimum-interval gate, regardless of exactly
    # when the flood last admitted something.
    clock.advance(86400.0 + 61.0)

    ok, reason = limiter.check()
    assert ok, f"a fresh hint should still be admitted after 25h+: {reason}"


# -- asymmetric power limits and the operator bypass (operator decision) -----
#
# The interval exists to stop the compressor short-cycling, which is about
# switching ON soon after OFF. Switching OFF soon after ON is harmless, and on
# Shabbat it is the only way a cold person can stop the AC. Operator controls
# (CLI, dashboard) bypass the interval but still count toward it.


def _power_limiter(clock: "FakeClock") -> RateLimiter:
    config = LimitsConfig.from_dict(
        {
            "min_interval_seconds": 600,
            "daily_cap": 12,
            "off_min_interval_seconds": 60,
            "on_after_off_min_interval_seconds": 240,
        }
    )
    return RateLimiter(config, clock)


def test_power_interval_defaults_are_the_agreed_numbers() -> None:
    config = LimitsConfig.from_dict({"min_interval_seconds": 600, "daily_cap": 12})
    assert config.off_min_interval_seconds == 60.0
    assert config.on_after_off_min_interval_seconds == 240.0


def test_off_is_allowed_soon_after_on_but_not_inside_the_debounce() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    limiter.record("pod", direction="on")
    clock.advance(30)
    assert limiter.check("pod", direction="off")[0] is False
    clock.advance(35)  # 65 s after the power-on: past the 60 s debounce
    assert limiter.check("pod", direction="off")[0] is True


def test_on_after_off_waits_for_the_compressor_interval() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    limiter.record("pod", direction="off")
    clock.advance(120)
    allowed, reason = limiter.check("pod", direction="on")
    assert allowed is False
    assert "compressor" in (reason or "")
    clock.advance(125)  # 245 s after the power-off
    assert limiter.check("pod", direction="on")[0] is True


def test_the_compressor_interval_is_measured_from_the_last_off_not_the_last_change() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    limiter.record("pod", direction="off")
    clock.advance(250)
    limiter.record("pod", direction="on")
    clock.advance(70)
    limiter.record("pod", direction="off")
    clock.advance(100)  # 100 s after the latest off, 400 s after the first one
    assert limiter.check("pod", direction="on")[0] is False


def test_an_undirected_action_keeps_the_symmetric_interval() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    limiter.record("self")
    clock.advance(300)
    assert limiter.check("self")[0] is False
    clock.advance(301)
    assert limiter.check("self")[0] is True


def test_the_daily_cap_still_bounds_directed_actions() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    for index in range(12):
        direction = "on" if index % 2 == 0 else "off"
        assert limiter.check("pod", direction=direction)[0] is True
        limiter.record("pod", direction=direction)
        clock.advance(300)
    assert limiter.check("pod", direction="off")[0] is False


def test_an_operator_bypasses_the_interval_but_still_counts_toward_it() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    limiter.record("pod", direction="off")
    clock.advance(5)
    # Voice is refused; the operator is not.
    assert limiter.check("pod", direction="on")[0] is False
    assert limiter.check("pod", direction="on", operator=True)[0] is True
    limiter.record("pod", direction="on")
    clock.advance(5)
    # The operator's action counted: voice is still inside the off-debounce.
    assert limiter.check("pod", direction="off")[0] is False


def test_an_operator_does_not_bypass_the_daily_cap() -> None:
    clock = FakeClock()
    limiter = _power_limiter(clock)
    for _ in range(12):
        limiter.record("pod", direction="on")
        clock.advance(10)
    assert limiter.check("pod", direction="off", operator=True)[0] is False


def test_an_unknown_direction_is_refused() -> None:
    limiter = _power_limiter(FakeClock())
    assert limiter.check("pod", direction="sideways")[0] is False


def test_a_bounded_ring_can_be_iterated_while_another_thread_appends() -> None:
    """Found in CI: `RuntimeError: deque mutated during iteration`.

    Every ring in the listener (log records, recent utterances, the limiter's
    refusal log, decide latencies) is appended by one thread and read by the
    dashboard's HTTP threads. Iteration must work on a snapshot.
    """
    import threading

    ring: BoundedRing[int] = BoundedRing(4000)
    for value in range(4000):
        ring.append(value)
    stop = threading.Event()

    def hammer() -> None:
        value = 0
        while not stop.is_set():
            ring.append(value)
            value += 1

    writer = threading.Thread(target=hammer, daemon=True)
    writer.start()
    try:
        for _ in range(400):
            assert len(list(ring)) <= 4000
            assert len([item for item in ring]) <= 4000
    finally:
        stop.set()
        writer.join(timeout=5)
    assert not writer.is_alive()
