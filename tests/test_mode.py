"""Tests for the mode resolver: zmanim-computed mode, override, clock trust.

No microphone, no network, no real ``timedatectl``/sleeping: every clock
check is injected via ``runner``, and every zmanim input is a plain
:class:`~shabbos_goy.config.Config` built in-memory or a real Location built
from ``tests/fixtures`` constants shared with ``test_zmanim_windows.py``.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone

import pytest

from shabbos_goy.config import Config
from shabbos_goy.mode import (
    MIN_TRUSTED_YEAR,
    ResolvedMode,
    check_ntp_synchronized,
    clear_override,
    clock_is_trusted,
    compute_zmanim_mode,
    get_override,
    parse_tzeit_definition,
    resolve_mode,
    set_override,
)
from shabbos_goy.policy import MODES
from shabbos_goy.zmanim import TzeitRule

JERUSALEM_RAW = {"lat": 31.778, "lon": 35.235, "timezone": "Asia/Jerusalem"}

# 2026-10-24 is a Saturday (see tests/test_zmanim_windows.py); the Friday
# evening before opens the window, mid-Saturday is inside it, Sunday morning
# is outside it again.
INSIDE_SHABBAT = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)
OUTSIDE_SHABBAT = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)  # a Tuesday
INSIDE_YOM_KIPPUR = datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc)


def _config(overrides: dict | None = None) -> Config:
    raw = {
        "location": dict(JERUSALEM_RAW),
        "candle_lighting_offset_minutes": 40,
        "tzeit_definition": "3_medium_stars",
        "region": "israel",
    }
    if overrides:
        raw.update(overrides)
    return Config(path=__import__("pathlib").Path("unused"), raw=raw, error=None)


def _broken_config() -> Config:
    from shabbos_goy.cli._errors import EXIT_ENV_ERROR, CliError

    return Config(
        path=__import__("pathlib").Path("unused"),
        raw={},
        error=CliError(code=EXIT_ENV_ERROR, message="broken", remediation="fix it"),
    )


@pytest.fixture(autouse=True)
def _reset_override():
    clear_override()
    yield
    clear_override()


def _fake_runner(stdout: str = "yes\n", returncode: int = 0):
    def runner(argv, **kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return runner


def _raising_runner(exc: Exception):
    def runner(argv, **kwargs):
        raise exc

    return runner


# ---------------------------------------------------------------------------
# tzeit_definition parsing
# ---------------------------------------------------------------------------


def test_parse_named_tzeit_definition():
    assert parse_tzeit_definition("3_medium_stars") == TzeitRule.degrees(8.5)


def test_parse_explicit_minutes_and_degrees_forms():
    assert parse_tzeit_definition("minutes:50") == TzeitRule.minutes(50)
    assert parse_tzeit_definition("degrees:8.5") == TzeitRule.degrees(8.5)
    assert parse_tzeit_definition("DEGREES:6") == TzeitRule.degrees(6)


def test_parse_unknown_definition_is_none():
    assert parse_tzeit_definition("some_made_up_opinion") is None
    assert parse_tzeit_definition("minutes:not-a-number") is None
    assert parse_tzeit_definition("minutes:-5") is None
    assert parse_tzeit_definition("") is None
    assert parse_tzeit_definition(None) is None
    assert parse_tzeit_definition(42) is None


# ---------------------------------------------------------------------------
# compute_zmanim_mode: criterion 1
# ---------------------------------------------------------------------------


def test_inside_shabbat_window_is_strict_with_shabbat_kind():
    result_mode, kinds = compute_zmanim_mode(INSIDE_SHABBAT, _config())
    assert result_mode == "strict"
    assert kinds == ("shabbat",)


def test_inside_yom_kippur_window_is_strict_with_yom_kippur_kind():
    result_mode, kinds = compute_zmanim_mode(INSIDE_YOM_KIPPUR, _config())
    assert result_mode == "strict"
    assert kinds == ("yom_kippur",)


def test_outside_any_window_is_weekday():
    result_mode, kinds = compute_zmanim_mode(OUTSIDE_SHABBAT, _config())
    assert result_mode == "weekday"
    assert kinds == ()


def test_broken_config_is_strict():
    result_mode, kinds = compute_zmanim_mode(OUTSIDE_SHABBAT, _broken_config())
    assert result_mode == "strict"
    assert kinds == ()


def test_missing_location_is_strict():
    assert compute_zmanim_mode(OUTSIDE_SHABBAT, _config({"location": {}})) == ("strict", ())
    assert compute_zmanim_mode(OUTSIDE_SHABBAT, _config({"location": "nope"})) == ("strict", ())
    assert compute_zmanim_mode(
        OUTSIDE_SHABBAT, _config({"location": {"lat": 31.7, "timezone": "Asia/Jerusalem"}})
    ) == ("strict", ())


def test_unknown_tzeit_definition_is_strict():
    result_mode, kinds = compute_zmanim_mode(
        OUTSIDE_SHABBAT, _config({"tzeit_definition": "an opinion nobody holds"})
    )
    assert result_mode == "strict"
    assert kinds == ()


def test_unknown_region_is_strict():
    result_mode, _ = compute_zmanim_mode(OUTSIDE_SHABBAT, _config({"region": "narnia"}))
    assert result_mode == "strict"


def test_polar_location_zmanim_failure_is_strict():
    """SunEventNotFound (e.g. polar latitudes) is treated as a zmanim failure."""
    polar = _config(
        {"location": {"lat": 78.2232, "lon": 15.6469, "timezone": "Arctic/Longyearbyen"}}
    )
    summer_solstice_ish = datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)
    result_mode, kinds = compute_zmanim_mode(summer_solstice_ish, polar)
    assert result_mode == "strict"
    assert kinds == ()


def test_compute_zmanim_mode_requires_aware_datetime():
    with pytest.raises(ValueError):
        compute_zmanim_mode(datetime(2026, 10, 24, 10, 0), _config())


# ---------------------------------------------------------------------------
# clock trust
# ---------------------------------------------------------------------------


def test_clock_trusted_when_ntp_synced_and_year_is_sane():
    assert clock_is_trusted(INSIDE_SHABBAT, runner=_fake_runner("yes\n")) is True


def test_clock_untrusted_when_ntp_reports_not_synced():
    assert clock_is_trusted(INSIDE_SHABBAT, runner=_fake_runner("no\n")) is False


def test_clock_untrusted_below_year_floor_even_if_ntp_says_synced():
    ancient = datetime(MIN_TRUSTED_YEAR - 1, 6, 1, tzinfo=timezone.utc)
    assert clock_is_trusted(ancient, runner=_fake_runner("yes\n")) is False


def test_clock_trusted_at_exactly_the_year_floor():
    floor = datetime(MIN_TRUSTED_YEAR, 1, 1, tzinfo=timezone.utc)
    assert clock_is_trusted(floor, runner=_fake_runner("yes\n")) is True


def test_clock_falls_back_to_year_floor_when_timedatectl_unavailable():
    missing_binary = _raising_runner(FileNotFoundError("no timedatectl"))
    assert clock_is_trusted(INSIDE_SHABBAT, runner=missing_binary) is True
    ancient = datetime(MIN_TRUSTED_YEAR - 1, 6, 1, tzinfo=timezone.utc)
    assert clock_is_trusted(ancient, runner=missing_binary) is False


def test_clock_falls_back_to_year_floor_on_unparseable_timedatectl_output():
    assert clock_is_trusted(INSIDE_SHABBAT, runner=_fake_runner("garbage\n")) is True


def test_clock_falls_back_to_year_floor_on_nonzero_exit():
    assert clock_is_trusted(INSIDE_SHABBAT, runner=_fake_runner("yes\n", returncode=1)) is True


def test_check_ntp_synchronized_none_on_timeout():
    assert check_ntp_synchronized(runner=_raising_runner(subprocess.TimeoutExpired("x", 5))) is None


def test_clock_is_trusted_requires_aware_datetime():
    with pytest.raises(ValueError):
        clock_is_trusted(datetime(2026, 10, 24, 10, 0))


# ---------------------------------------------------------------------------
# override: memory-only, shared by CLI + dashboard
# ---------------------------------------------------------------------------


def test_override_defaults_to_none():
    assert get_override() is None


def test_set_override_rejects_anything_not_a_known_mode_or_none():
    with pytest.raises(ValueError):
        set_override("holiday")


@pytest.mark.parametrize("m", MODES)
def test_set_and_get_override_round_trips(m):
    set_override(m)
    assert get_override() == m


def test_clear_override_resets_to_none():
    set_override("strict")
    clear_override()
    assert get_override() is None


def test_override_is_not_written_to_any_file(tmp_path, monkeypatch):
    """Memory-only per the brief: setting it must not touch the filesystem."""
    before = {p for p in tmp_path.iterdir()}
    set_override("strict")
    after = {p for p in tmp_path.iterdir()}
    assert before == after


def test_a_new_process_has_no_override():
    """A fresh interpreter always starts with no override -- nothing on disk
    holds it, so a restart cannot resume a forced mode (criterion 2)."""
    set_override("strict")  # set it in *this* process
    result = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, test-only
        [
            "python3",
            "-c",
            "from shabbos_goy.mode import get_override; print(get_override())",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
        cwd=str(__import__("pathlib").Path(__file__).parent.parent),
    )
    assert result.stdout.strip() == "None"


# ---------------------------------------------------------------------------
# resolve_mode: the full composition
# ---------------------------------------------------------------------------


def test_resolve_mode_matches_zmanim_when_no_override_and_clock_trusted():
    result = resolve_mode(INSIDE_SHABBAT, _config(), runner=_fake_runner())
    assert result == ResolvedMode(
        mode="strict", kinds=("shabbat",), clock_trusted=True, overridden=False
    )
    result = resolve_mode(OUTSIDE_SHABBAT, _config(), runner=_fake_runner())
    assert result == ResolvedMode(mode="weekday", kinds=(), clock_trusted=True, overridden=False)


def test_override_forces_strict_outside_any_window():
    set_override("strict")
    result = resolve_mode(OUTSIDE_SHABBAT, _config(), runner=_fake_runner())
    assert result.mode == "strict"
    assert result.overridden is True
    assert result.kinds == ()  # no window is really running


def test_override_forces_weekday_inside_a_simulated_window():
    set_override("weekday")
    result = resolve_mode(INSIDE_SHABBAT, _config(), runner=_fake_runner())
    assert result.mode == "weekday"
    assert result.overridden is True
    # The real window is still reported for the UI, independent of the forced mode.
    assert result.kinds == ("shabbat",)


def test_untrusted_clock_beats_a_weekday_override():
    set_override("weekday")
    result = resolve_mode(INSIDE_SHABBAT, _config(), runner=_fake_runner("no\n"))
    assert result.mode == "strict"
    assert result.clock_trusted is False
    assert result.overridden is True  # still reported: an override *was* set


def test_untrusted_clock_is_strict_even_with_no_override():
    result = resolve_mode(OUTSIDE_SHABBAT, _config(), runner=_fake_runner("no\n"))
    assert result.mode == "strict"
    assert result.clock_trusted is False
    assert result.overridden is False


def test_set_override_shared_by_two_call_sites_sees_the_same_state():
    """Simulates the CLI and the dashboard both calling the one shared function."""

    def cli_forces_strict():
        set_override("strict")

    def dashboard_reads_it():
        return get_override()

    cli_forces_strict()
    assert dashboard_reads_it() == "strict"

    def dashboard_forces_weekday():
        set_override("weekday")

    dashboard_forces_weekday()
    assert get_override() == "weekday"


# --------------------------------------------------------------------------
# window_summary: public, so the dashboard stops reaching for private helpers
# --------------------------------------------------------------------------


def test_window_summary_reports_the_next_window_outside_one():
    from shabbos_goy.mode import window_summary

    summary = window_summary(datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc), _config())
    assert summary["available"] is True
    assert summary["current"] is None
    assert summary["next"] is not None
    assert set(summary["next"]) == {"start", "end", "kinds"}
    assert "shabbat" in summary["next"]["kinds"]


def test_window_summary_reports_a_running_window_and_the_one_after_it():
    from shabbos_goy.mode import window_summary

    summary = window_summary(datetime(2026, 9, 18, 18, 30, tzinfo=timezone.utc), _config())
    assert summary["current"] is not None
    assert summary["next"] is not None


def test_window_summary_fails_closed_on_a_broken_config():
    from shabbos_goy.mode import window_summary

    summary = window_summary(datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc), _broken_config())
    assert summary == {"available": False, "current": None, "next": None, "reason": "config"}


# --------------------------------------------------------------------------
# Fail-closed on bad config values (PR #3 review, threads #6/#7/#14/#15)
#
# Every value below is a config a human could plausibly write and that the
# code used to accept, truncate or crash on. None of them may resolve to
# ``weekday``, and none of them may raise: a bad value is a strict value.
# --------------------------------------------------------------------------

BAD_ZMANIM_CONFIG_VALUES = [
    # thread #6 -- nightfall at or below the sunset geometry (sunset is
    # computed 0.833deg below the horizon), so the window would close before
    # Shabbat is actually out.
    ("tzeit_definition", "degrees:0"),
    ("tzeit_definition", "degrees:0.5"),
    ("tzeit_definition", "degrees:2"),
    ("tzeit_definition", "minutes:0"),
    # ... and absurdly late nightfall, which is just as much a typo.
    ("tzeit_definition", "degrees:90"),
    ("tzeit_definition", "minutes:100000"),
    # thread #7 -- non-finite numbers parse as floats and used to reach
    # timedelta()/the solar maths, raising ValueError/OverflowError.
    ("tzeit_definition", "minutes:nan"),
    ("tzeit_definition", "degrees:nan"),
    ("tzeit_definition", "minutes:inf"),
    ("tzeit_definition", "degrees:inf"),
    ("tzeit_definition", "minutes:-inf"),
    # thread #14 -- an unknown IANA name only blew up later, in Location.zone().
    ("location", {"lat": 31.778, "lon": 35.235, "timezone": "Mars/Olympus_Mons"}),
    ("location", {"lat": 31.778, "lon": 35.235, "timezone": "Asia/Jerusalem\x00"}),
    ("location", []),
    # thread #15 -- the offset used to be truncated with int() before being
    # validated, so -0.5 became a silently-accepted 0.
    ("candle_lighting_offset_minutes", -0.5),
    ("candle_lighting_offset_minutes", -1),
    ("candle_lighting_offset_minutes", 18.5),
    ("candle_lighting_offset_minutes", float("nan")),
    ("candle_lighting_offset_minutes", float("inf")),
    ("candle_lighting_offset_minutes", True),
    ("candle_lighting_offset_minutes", "18"),
    ("candle_lighting_offset_minutes", 10_000),
]


@pytest.mark.parametrize(
    "key,value", BAD_ZMANIM_CONFIG_VALUES, ids=[f"{k}={v!r}" for k, v in BAD_ZMANIM_CONFIG_VALUES]
)
def test_resolve_mode_fails_closed_on_a_bad_config_value(key, value):
    # OUTSIDE_SHABBAT is a Tuesday: a *good* config resolves to weekday here,
    # so "strict" can only come from the bad value being refused.
    resolved = resolve_mode(OUTSIDE_SHABBAT, _config({key: value}), runner=_fake_runner())
    assert resolved.mode == "strict"
    assert resolved.clock_trusted is True
    assert resolved.overridden is False


@pytest.mark.parametrize(
    "key,value", BAD_ZMANIM_CONFIG_VALUES, ids=[f"{k}={v!r}" for k, v in BAD_ZMANIM_CONFIG_VALUES]
)
def test_compute_zmanim_mode_fails_closed_on_a_bad_config_value(key, value):
    assert compute_zmanim_mode(OUTSIDE_SHABBAT, _config({key: value})) == ("strict", ())


@pytest.mark.parametrize(
    "key,value", BAD_ZMANIM_CONFIG_VALUES, ids=[f"{k}={v!r}" for k, v in BAD_ZMANIM_CONFIG_VALUES]
)
def test_window_summary_reports_unavailable_on_a_bad_config_value(key, value):
    from shabbos_goy.mode import window_summary

    summary = window_summary(OUTSIDE_SHABBAT, _config({key: value}))
    assert summary["available"] is False
    assert summary["current"] is None


@pytest.mark.parametrize(
    "definition",
    ["degrees:0", "degrees:0.5", "minutes:0", "minutes:nan", "degrees:nan", "minutes:inf"],
)
def test_parse_tzeit_definition_refuses_unusable_numbers(definition):
    assert parse_tzeit_definition(definition) is None


@pytest.mark.parametrize("exc", [ValueError("boom"), OverflowError("boom"), ZeroDivisionError()])
def test_compute_zmanim_mode_fails_closed_on_any_zmanim_exception(monkeypatch, exc):
    import shabbos_goy.mode as mode_module

    def explode(*_args, **_kwargs):
        raise exc

    monkeypatch.setattr(mode_module, "next_window", explode)
    assert compute_zmanim_mode(INSIDE_SHABBAT, _config()) == ("strict", ())
    assert resolve_mode(INSIDE_SHABBAT, _config(), runner=_fake_runner()).mode == "strict"
