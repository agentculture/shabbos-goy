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

from shabbos_goy.config import Config
from shabbos_goy.mode import parse_tzeit_definition
from shabbos_goy.zmanim import Location, SunEventNotFound, ZmanimRules, next_window


def location_from_config(config: Config) -> Optional[Location]:
    """A :class:`Location` from ``config.location``, or ``None`` if unusable."""
    location = config.location
    if not isinstance(location, dict):
        return None
    lat = location.get("lat")
    lon = location.get("lon")
    tz = location.get("timezone")
    if isinstance(lat, bool) or isinstance(lon, bool):
        return None
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return None
    if not isinstance(tz, str) or not tz:
        return None
    try:
        return Location(float(lat), float(lon), tz)
    except ValueError:
        return None


def rules_from_config(config: Config) -> Optional[ZmanimRules]:
    """A :class:`ZmanimRules` from config, or ``None`` if unrecognised."""
    offset = config.candle_lighting_offset_minutes
    if isinstance(offset, bool) or not isinstance(offset, (int, float)):
        return None
    tzeit_rule = parse_tzeit_definition(config.tzeit_definition)
    if tzeit_rule is None:
        return None
    region = config.region
    if region == "israel":
        israel = True
    elif region in ("diaspora", None):
        israel = False
    else:
        return None
    try:
        return ZmanimRules(
            candle_lighting_offset_minutes=int(offset), tzeit=tzeit_rule, israel=israel
        ).validate()
    except ValueError:
        return None


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
    except SunEventNotFound:
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
