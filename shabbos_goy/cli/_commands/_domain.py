"""Shared read-only zmanim/location helpers for the CLI verbs.

Deliberately re-implements the location/rules construction
:mod:`shabbos_goy.mode` already does privately (``_build_location``/
``_build_rules``) rather than reaching into that module's underscore-prefixed
helpers: this is the CLI's own narrow, read-only view of the same config
shape, used only to describe the next strict window to a human -- never to
gate an action. Every actual gating decision still goes exclusively through
:func:`shabbos_goy.mode.resolve_mode` and :func:`shabbos_goy.policy.may_act`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from shabbos_goy import mode as mode_module
from shabbos_goy.config import Config
from shabbos_goy.zmanim import Location, ZmanimRules, next_window


def location_from_config(config: Config) -> Optional[Location]:
    """The configured location, or ``None``. Delegates to :mod:`shabbos_goy.mode`.

    One parser, not two: what the listener refuses (an unknown timezone, a
    malformed location) a describing verb must refuse too, or ``zmanim`` and
    ``preflight`` would print a window the listener is not using.
    """
    return mode_module._build_location(config)  # noqa: SLF001 - the single source of truth


def rules_from_config(config: Config) -> Optional[ZmanimRules]:
    """The configured zmanim rules, or ``None``. Delegates to :mod:`shabbos_goy.mode`."""
    return mode_module._build_rules(config)  # noqa: SLF001 - the single source of truth


@dataclass(frozen=True)
class NextWindow:
    """The next strict window, described in the configured location's local time."""

    starts_at_local: str
    ends_at_local: str
    kinds: tuple[str, ...]


def next_strict_window(now: datetime, config: Config) -> Optional[NextWindow]:
    """The next strict window from ``now``, in local time.

    ``None`` when the config has no usable location/rules, or zmanim itself
    is undefined there (e.g. a polar latitude) -- the same fail-closed shape
    :mod:`shabbos_goy.mode` uses, but this function never gates an action; it
    only describes.
    """
    location = location_from_config(config)
    rules = rules_from_config(config)
    if location is None or rules is None:
        return None
    try:
        window = next_window(now, location, rules)
    except Exception:  # noqa: BLE001 - any zmanim failure means "no window to show"
        return None
    if window is None:
        return None
    zone = location.zone()
    return NextWindow(
        starts_at_local=window.start.astimezone(zone).isoformat(),
        ends_at_local=window.end.astimezone(zone).isoformat(),
        kinds=window.kinds,
    )


def next_strict_window_payload(now: datetime, config: Config) -> Optional[dict]:
    """:func:`next_strict_window`, as a JSON-shaped dict (or ``None``)."""
    win = next_strict_window(now, config)
    if win is None:
        return None
    return {
        "starts_at_local": win.starts_at_local,
        "ends_at_local": win.ends_at_local,
        "kinds": list(win.kinds),
    }
