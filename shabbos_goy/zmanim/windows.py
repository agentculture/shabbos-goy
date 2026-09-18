"""Strict windows: candle lighting to tzeit, adjacent holy days merged.

A *window* is a half-open UTC interval during which the agent must run in
strict mode. It opens at candle lighting (sunset minus a configured offset)
on the evening before the first holy day and closes at tzeit hakochavim on
the last. Consecutive holy days do not get separate windows: a two-day Rosh
Hashana, or Shabbat followed by Yom Tov, is one continuous window, because
nothing permitted happens at the boundary.

The candle-lighting offset, the tzeit definition and israel/diaspora all come
from configuration (:class:`ZmanimRules`), never from constants here: a
community narrows them to match its posek.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from . import hebrew
from .location import Location
from .sun import depression_time, sunset

KIND_SHABBAT = "shabbat"
KIND_YOM_KIPPUR = "yom_kippur"
KIND_YOM_TOV = "yom_tov"

#: Canonical order for a window's ``kinds`` tuple.
_KIND_ORDER = (KIND_SHABBAT, KIND_YOM_KIPPUR, KIND_YOM_TOV)

TZEIT_MINUTES = "minutes"
TZEIT_DEGREES = "degrees"

#: How far ``next_window`` will look ahead before giving up.
_DEFAULT_SEARCH_DAYS = 400


@dataclass(frozen=True)
class TzeitRule:
    """How nightfall is defined: fixed minutes after sunset, or an angle."""

    kind: str
    value: float

    @classmethod
    def minutes(cls, value: float) -> "TzeitRule":
        return cls(TZEIT_MINUTES, value)

    @classmethod
    def degrees(cls, value: float) -> "TzeitRule":
        return cls(TZEIT_DEGREES, value)

    def validate(self) -> "TzeitRule":
        if self.kind not in (TZEIT_MINUTES, TZEIT_DEGREES):
            raise ValueError(
                f"tzeit kind must be {TZEIT_MINUTES!r} or {TZEIT_DEGREES!r}, not {self.kind!r}"
            )
        if self.value < 0:
            raise ValueError(f"tzeit value must not be negative: {self.value}")
        return self


@dataclass(frozen=True)
class ZmanimRules:
    """The configurable half of the calculation."""

    candle_lighting_offset_minutes: int = 18
    tzeit: TzeitRule = field(default_factory=lambda: TzeitRule.degrees(8.5))
    israel: bool = False

    def validate(self) -> "ZmanimRules":
        if self.candle_lighting_offset_minutes < 0:
            raise ValueError(
                "candle-lighting offset must not be negative: "
                f"{self.candle_lighting_offset_minutes}"
            )
        self.tzeit.validate()
        return self


@dataclass(frozen=True)
class Window:
    """A half-open ``[start, end)`` strict-mode interval, in UTC."""

    start: datetime
    end: datetime
    kinds: tuple[str, ...]
    days: tuple[date, ...]

    def contains(self, moment: datetime) -> bool:
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("moment must be timezone-aware")
        return self.start <= moment < self.end


def candle_lighting(day: date, location: Location, rules: ZmanimRules) -> datetime:
    """Candle lighting on the evening of ``day``: sunset minus the offset."""
    rules.validate()
    return sunset(day, location) - timedelta(minutes=rules.candle_lighting_offset_minutes)


def tzeit(day: date, location: Location, rules: ZmanimRules) -> datetime:
    """Nightfall on the evening of ``day``, per the configured rule."""
    rules.validate()
    if rules.tzeit.kind == TZEIT_MINUTES:
        return sunset(day, location) + timedelta(minutes=rules.tzeit.value)
    return depression_time(day, location, rules.tzeit.value)


def day_kinds(day: date, rules: ZmanimRules) -> tuple[str, ...]:
    """Which strict-mode kinds apply to the civil day ``day`` (may be empty)."""
    kinds: list[str] = []
    if day.weekday() == 5:  # Saturday
        kinds.append(KIND_SHABBAT)
    name = hebrew.yom_tov_name(day, israel=rules.israel)
    if name == hebrew.YOM_KIPPUR:
        kinds.append(KIND_YOM_KIPPUR)
    elif name is not None:
        kinds.append(KIND_YOM_TOV)
    return tuple(kinds)


def _is_holy(day: date, rules: ZmanimRules) -> bool:
    return bool(day_kinds(day, rules))


def _run_containing(day: date, rules: ZmanimRules) -> tuple[date, ...]:
    """The maximal run of consecutive holy days containing ``day``."""
    first = day
    while _is_holy(first - timedelta(days=1), rules):
        first -= timedelta(days=1)
    last = day
    while _is_holy(last + timedelta(days=1), rules):
        last += timedelta(days=1)
    span = (last - first).days + 1
    return tuple(first + timedelta(days=offset) for offset in range(span))


def _window_for_run(run: tuple[date, ...], location: Location, rules: ZmanimRules) -> Window:
    start = candle_lighting(run[0] - timedelta(days=1), location, rules)
    end = tzeit(run[-1], location, rules)
    kinds = {kind for day in run for kind in day_kinds(day, rules)}
    return Window(
        start=start,
        end=end,
        kinds=tuple(kind for kind in _KIND_ORDER if kind in kinds),
        days=run,
    )


def windows_for(day: date, location: Location, rules: ZmanimRules) -> list[Window]:
    """Every strict window overlapping the civil day ``day``, in local terms.

    A Friday reports the Shabbat window that opens that evening; the Saturday
    reports the same window. Adjacent holy days are merged, so a day never
    reports two windows.
    """
    rules.validate()
    zone = location.zone()
    day_start = datetime(day.year, day.month, day.day, tzinfo=zone).astimezone(timezone.utc)
    next_day = day + timedelta(days=1)
    day_end = datetime(next_day.year, next_day.month, next_day.day, tzinfo=zone).astimezone(
        timezone.utc
    )

    runs: list[tuple[date, ...]] = []
    for candidate in (day, next_day):
        if not _is_holy(candidate, rules):
            continue
        run = _run_containing(candidate, rules)
        if run not in runs:
            runs.append(run)

    windows = [_window_for_run(run, location, rules) for run in runs]
    overlapping = [
        window for window in windows if window.start < day_end and window.end > day_start
    ]
    return sorted(overlapping, key=lambda window: window.start)


def next_window(
    moment: datetime,
    location: Location,
    rules: ZmanimRules,
    *,
    search_days: int = _DEFAULT_SEARCH_DAYS,
) -> Window | None:
    """The window running at, or next starting after, ``moment``.

    Returns ``None`` if none is found within ``search_days``.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("moment must be timezone-aware")
    local_day = moment.astimezone(location.zone()).date()
    for offset in range(-2, search_days):
        for window in windows_for(local_day + timedelta(days=offset), location, rules):
            if window.end > moment:
                return window
    return None
