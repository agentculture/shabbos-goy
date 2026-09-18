"""Ears-only client for the lobes ``/v1/realtime`` Hebrew session.

Three layers, deliberately separate:

* :mod:`shabbos_goy.lobes.ws` — a stdlib RFC 6455 wire, cited from
  ``lobes-cli`` ``scripts/realtime-smoke.py`` (see that module's CITATION).
* :mod:`shabbos_goy.lobes.config` — host and key, from the environment only.
* :mod:`shabbos_goy.lobes.client` — the session: reconnect with capped
  backoff, answer PING with PONG, drop empty transcripts, never send
  anything but audio, exit non-zero when the session wedges.

This package emits normalised transcript events to a callback. It does not
classify, it does not decide, and it never persists anything.
"""

from .client import (
    EXIT_ENVIRONMENT,
    EXIT_OK,
    EXIT_STALLED,
    BackoffPolicy,
    ClientStats,
    EarsOnlyViolation,
    LobesAuthError,
    LobesClient,
    LobesConnectError,
    SessionOutcome,
    Watchdog,
)
from .config import LobesConfig, LobesConfigError, config_from_env
from .events import ERROR_CODES, EVENT_TYPES, LobesEvent, normalize_event

__all__ = [
    "BackoffPolicy",
    "ClientStats",
    "EXIT_ENVIRONMENT",
    "EXIT_OK",
    "EXIT_STALLED",
    "ERROR_CODES",
    "EVENT_TYPES",
    "EarsOnlyViolation",
    "LobesAuthError",
    "LobesClient",
    "LobesConfig",
    "LobesConfigError",
    "LobesConnectError",
    "LobesEvent",
    "SessionOutcome",
    "Watchdog",
    "config_from_env",
    "normalize_event",
]
