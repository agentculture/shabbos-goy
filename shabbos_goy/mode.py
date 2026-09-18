"""Mode resolver: zmanim-computed mode, in-memory override, clock trust.

This is the single place that turns "what time/day is it, and does the
operator have an override set" into the ``weekday``/``strict`` mode that
:func:`shabbos_goy.policy.may_act` gates on. Both the CLI and the (planned)
dashboard call :func:`resolve_mode` and :func:`set_override` -- neither UI
keeps its own copy of this logic or this state.

Three things compose here, in this priority order:

1. **Clock trust** (:func:`clock_is_trusted`). Mode depends on wall-clock
   time and the zmanim it implies, so an untrusted clock must never resolve
   to ``weekday`` -- CLAUDE.md's "Clock trust" rule: fail toward the
   stricter behaviour, never toward acting. When the clock is untrusted this
   overrides everything below, including an explicit ``weekday`` override.
2. **The in-memory override** (:func:`set_override`/:func:`get_override`).
   Set from the CLI or the dashboard, it forces ``strict`` on or ``weekday``
   off regardless of what zmanim says -- including *inside* a live window.
   It lives only in a module-level variable: nothing here writes it to disk,
   so a freshly started process always begins with no override and computes
   the zmanim mode fresh (the invariant a crash/reboot must not weaken --
   CLAUDE.md invariant #3, "no queued action survives a restart", extended
   here to "no queued override" either).
3. **The zmanim-computed mode** (:func:`compute_zmanim_mode`). ``strict``
   whenever ``now`` falls inside a candle-lighting-to-tzeit window for the
   configured location and rules, ``weekday`` otherwise. A config that is
   not ``.ok``, has no usable location, or names a ``tzeit_definition`` this
   module does not recognise, all fail the same way as a location at a polar
   latitude that makes zmanim undefined (:class:`SunEventNotFound`): strict,
   with no window kind to report. This is a fail-closed design on purpose --
   see CLAUDE.md's clock-trust rule and the halachic-scope section.

A window's ``kinds`` (``shabbat``/``yom_kippur``/``yom_tov``) are reported
alongside the resolved mode purely for the UIs to display -- the *mode* a
caller acts on is always exactly ``"weekday"`` or ``"strict"``
(:data:`shabbos_goy.policy.MODES`), never a window kind.
"""

from __future__ import annotations

import subprocess  # nosec B404 - argv-locked, no shell; see clock_is_trusted
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from shabbos_goy.config import Config
from shabbos_goy.policy import MODES
from shabbos_goy.zmanim import (
    Location,
    SunEventNotFound,
    TzeitRule,
    Window,
    ZmanimRules,
    next_window,
)

#: Below this year, the clock is untrusted even if timedatectl claims sync --
#: a dead RTC or a container started before NTP has caught up commonly wakes
#: up at the UNIX epoch or some other implausible date. Bump this only when
#: it would otherwise reject a real, correct date (i.e. keep it comfortably
#: in the past).
MIN_TRUSTED_YEAR = 2024

#: Named tzeit (nightfall) definitions a ``tzeit_definition`` config string
#: may name, mapped to the :class:`TzeitRule` this module's zmanim maths
#: understands. Degrees-below-horizon values follow common poskim-cited
#: figures (see e.g. the Shulchan Aruch HaRav / Geonim view for "3 medium
#: stars", the Magen Avraham for the fixed-minute opinions); a community
#: that holds a different position sets its own minutes/degrees value
#: instead of relying on this table -- see :func:`parse_tzeit_definition`.
NAMED_TZEIT_DEFINITIONS: dict[str, TzeitRule] = {
    "3_small_stars": TzeitRule.degrees(3.65),
    "3_medium_stars": TzeitRule.degrees(8.5),
    "3_large_stars": TzeitRule.degrees(11.5),
    "42_minutes": TzeitRule.minutes(42),
    "50_minutes": TzeitRule.minutes(50),
    "72_minutes": TzeitRule.minutes(72),
}


def parse_tzeit_definition(value: object) -> TzeitRule | None:
    """A config ``tzeit_definition`` string, as a :class:`TzeitRule`.

    Recognises the named table above, plus two explicit forms so a
    community is never limited to the table: ``"minutes:NN"`` and
    ``"degrees:N.N"`` (case-insensitive, e.g. ``"degrees:8.5"``). Anything
    else -- an unknown name, a malformed explicit form, a non-string value,
    or a negative number -- returns ``None``: an unrecognised definition
    means the caller cannot build zmanim rules and must fail to strict.
    """
    if not isinstance(value, str) or not value:
        return None
    named = NAMED_TZEIT_DEFINITIONS.get(value)
    if named is not None:
        return named
    lowered = value.strip().lower()
    for prefix, factory in (("minutes:", TzeitRule.minutes), ("degrees:", TzeitRule.degrees)):
        if lowered.startswith(prefix):
            raw = lowered[len(prefix) :]
            try:
                number = float(raw)
            except ValueError:
                return None
            try:
                return factory(number).validate()
            except ValueError:
                return None
    return None


def _build_location(config: Config) -> Location | None:
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


def _build_rules(config: Config) -> ZmanimRules | None:
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


def _current_window(now: datetime, location: Location, rules: ZmanimRules) -> Window | None:
    """The window (if any) that contains ``now``. Raises :class:`SunEventNotFound`
    unchanged, for the caller to treat as a zmanim failure (strict)."""
    window = next_window(now, location, rules)
    if window is not None and window.contains(now):
        return window
    return None


def compute_zmanim_mode(now: datetime, config: Config) -> tuple[str, tuple[str, ...]]:
    """The mode zmanim alone implies at ``now``, ignoring override/clock trust.

    Returns ``("strict", window.kinds)`` inside any zmanim window (Shabbat,
    Yom Kippur, Yom Tov) and ``("weekday", ())`` outside one. A config that
    is not ``.ok``, has no usable ``location``, or names an unrecognised
    ``tzeit_definition``/``region``/``candle_lighting_offset_minutes`` -- and
    a location where zmanim itself is undefined (:class:`SunEventNotFound`,
    e.g. polar latitudes) -- all resolve to ``("strict", ())``: the same
    fail-closed rule as an untrusted clock, never toward acting.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    if not config.ok:
        return "strict", ()

    location = _build_location(config)
    if location is None:
        return "strict", ()

    rules = _build_rules(config)
    if rules is None:
        return "strict", ()

    try:
        window = _current_window(now, location, rules)
    except SunEventNotFound:
        return "strict", ()

    if window is not None:
        return "strict", window.kinds
    return "weekday", ()


def _window_dict(window: Window) -> dict:
    return {
        "start": window.start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "end": window.end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "kinds": list(window.kinds),
    }


def window_summary(now: datetime, config: Config) -> dict:
    """The window running at ``now`` (if any) and the next one, as plain data.

    Public because a UI needs exactly this and must not reach for
    :func:`_build_location`/:func:`_build_rules` to get it: a second copy of
    the config-to-zmanim rules is a second place to get fail-closed wrong.
    Every failure --- a broken config, an unusable location or ruleset, a
    latitude where the sun event does not happen --- is reported as
    ``available: False`` with a short reason code, never as an exception and
    never as a fabricated window.
    """
    unavailable = {"available": False, "current": None, "next": None, "reason": "no_zmanim"}
    if not config.ok:
        return {**unavailable, "reason": "config"}
    location = _build_location(config)
    rules = _build_rules(config)
    if location is None or rules is None:
        return {**unavailable, "reason": "config"}
    try:
        window = next_window(now, location, rules)
        if window is None:
            return {"available": True, "current": None, "next": None}
        if window.contains(now):
            following = next_window(window.end, location, rules)
            return {
                "available": True,
                "current": _window_dict(window),
                "next": _window_dict(following) if following is not None else None,
            }
        return {"available": True, "current": None, "next": _window_dict(window)}
    except (SunEventNotFound, ValueError):
        return {**unavailable, "reason": "sun_event_not_found"}


def check_ntp_synchronized(*, runner=subprocess.run) -> bool | None:
    """``timedatectl``'s ``NTPSynchronized`` property, or ``None`` if
    ``timedatectl`` is unavailable, errors, or gives an unparseable answer
    (the caller then falls back to the year-floor sanity check)."""
    try:
        result = runner(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],  # nosec B603
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip().lower()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def clock_is_trusted(
    now: datetime, *, runner=subprocess.run, min_year: int = MIN_TRUSTED_YEAR
) -> bool:
    """Whether ``now`` may be trusted for mode resolution.

    Two checks, per CLAUDE.md's clock-trust rule ("check NTP sync via
    timedatectl when available, else a sanity floor on the year"):

    1. A sanity floor on the year -- always applied, regardless of NTP. A
       clock claiming a date before :data:`MIN_TRUSTED_YEAR` is untrusted no
       matter what ``timedatectl`` says (a dead RTC can report "synced" to a
       wrong upstream, or the check can be unavailable entirely).
    2. ``timedatectl``'s ``NTPSynchronized`` property, when it is available
       and gives a definite yes/no answer. When it is not (not installed,
       errored, unparseable output -- e.g. non-systemd hosts), the year
       floor above is the only check, and passing it is trusted.

    ``runner`` is injected (defaults to :func:`subprocess.run`) so tests
    never invoke a real ``timedatectl``.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if now.year < min_year:
        return False
    synced = check_ntp_synchronized(runner=runner)
    if synced is not None:
        return synced
    return True


@dataclass(frozen=True)
class ResolvedMode:
    """The fully-resolved mode, and why."""

    #: Always exactly one of ``shabbos_goy.policy.MODES`` -- what a caller
    #: passes to ``may_act``.
    mode: str
    #: The zmanim window kind(s) active at ``now`` (``()`` outside one),
    #: for UI display only. Independent of ``mode``/``overridden``: an
    #: override changes ``mode`` but never fabricates or hides which window
    #: is really running.
    kinds: tuple[str, ...]
    clock_trusted: bool
    overridden: bool


#: The in-memory override: ``None`` (no override), or one of
#: ``shabbos_goy.policy.MODES``. Deliberately module-level, process-local
#: state -- never written to a file or environment variable -- so a crash or
#: restart always comes back with no override (criterion 2).
_override: str | None = None
_override_lock = threading.Lock()


def set_override(mode: str | None) -> None:
    """Force the resolved mode to ``mode`` (one of ``policy.MODES``), or
    clear the override with ``None``.

    Shared by the CLI and the dashboard: both call this same function, so
    there is exactly one place the override lives, not one copy per UI.
    Memory-only -- see the module docstring -- so it never survives a
    restart, and setting it here has no effect on any other process.
    """
    global _override
    if mode is not None and mode not in MODES:
        raise ValueError(f"override mode must be one of {MODES} or None, not {mode!r}")
    with _override_lock:
        _override = mode


def get_override() -> str | None:
    """The current in-memory override, or ``None`` if none is set."""
    with _override_lock:
        return _override


def clear_override() -> None:
    """Equivalent to ``set_override(None)``."""
    set_override(None)


def resolve_mode(now: datetime, config: Config, *, runner=subprocess.run) -> ResolvedMode:
    """The mode to act on right now: zmanim, then the override, then clock trust.

    Clock trust always wins last: when the clock cannot be trusted, the
    result is ``"strict"`` regardless of the zmanim mode *or* an explicit
    override -- an operator cannot override their way into acting on an
    untrusted clock (CLAUDE.md: "Never fail toward acting").
    """
    zmanim_mode, kinds = compute_zmanim_mode(now, config)
    trusted = clock_is_trusted(now, runner=runner)
    override = get_override()

    if not trusted:
        return ResolvedMode(
            mode="strict", kinds=kinds, clock_trusted=False, overridden=override is not None
        )

    if override is not None:
        return ResolvedMode(mode=override, kinds=kinds, clock_trusted=True, overridden=True)

    return ResolvedMode(mode=zmanim_mode, kinds=kinds, clock_trusted=True, overridden=False)
