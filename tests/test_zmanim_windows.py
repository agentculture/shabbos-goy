"""Strict windows: candle lighting to tzeit, with adjacent holy days merged.

The window is what the mode resolver gates on, so the tests below check its
edges against the published Hebcal times in
``tests/fixtures/zmanim_sun_vectors.json`` and its extent against the
published Yom Tov dates.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shabbos_goy.zmanim import (
    KIND_SHABBAT,
    KIND_YOM_KIPPUR,
    KIND_YOM_TOV,
    Location,
    TzeitRule,
    Window,
    ZmanimRules,
    candle_lighting,
    next_window,
    tzeit,
    windows_for,
)

FIXTURE = Path(__file__).parent / "fixtures" / "zmanim_sun_vectors.json"
VECTORS = json.loads(FIXTURE.read_text(encoding="utf-8"))["vectors"]

TOLERANCE = timedelta(minutes=2)

JERUSALEM = Location(31.778, 35.235, "Asia/Jerusalem")
NEW_YORK = Location(40.7128, -74.006, "America/New_York")

ISRAEL_RULES = ZmanimRules(
    candle_lighting_offset_minutes=40,
    tzeit=TzeitRule.degrees(8.5),
    israel=True,
)
DIASPORA_RULES = ZmanimRules(
    candle_lighting_offset_minutes=18,
    tzeit=TzeitRule.degrees(8.5),
    israel=False,
)


def _published(location_name: str, day: str, key: str) -> datetime:
    for vector in VECTORS:
        if vector["location"] == location_name and vector["date"] == day:
            return datetime.fromisoformat(vector["times"][key]).astimezone(timezone.utc)
    raise AssertionError(f"no vector for {location_name} {day}")


def _published_candle(location_name: str, day: str) -> datetime:
    for vector in VECTORS:
        if vector["location"] == location_name and vector["date"] == day:
            return datetime.fromisoformat(vector["candle_lighting"]["at"]).astimezone(timezone.utc)
    raise AssertionError(f"no candle vector for {location_name} {day}")


CANDLE_CASES = [
    ("jerusalem", JERUSALEM, ISRAEL_RULES, "2026-10-23"),
    ("jerusalem", JERUSALEM, ISRAEL_RULES, "2026-10-30"),
    ("jerusalem", JERUSALEM, ISRAEL_RULES, "2026-06-19"),
    ("jerusalem", JERUSALEM, ISRAEL_RULES, "2026-12-25"),
    ("new_york", NEW_YORK, DIASPORA_RULES, "2026-10-23"),
    ("new_york", NEW_YORK, DIASPORA_RULES, "2026-10-30"),
    ("new_york", NEW_YORK, DIASPORA_RULES, "2026-06-19"),
    ("new_york", NEW_YORK, DIASPORA_RULES, "2026-12-25"),
]


@pytest.mark.parametrize("name,location,rules,day", CANDLE_CASES, ids=lambda v: str(v))
def test_candle_lighting_matches_published(name, location, rules, day):
    actual = candle_lighting(date.fromisoformat(day), location, rules)
    assert abs(actual - _published_candle(name, day)) <= TOLERANCE


@pytest.mark.parametrize("name,location,rules,day", CANDLE_CASES, ids=lambda v: str(v))
def test_tzeit_by_degrees_matches_published(name, location, rules, day):
    actual = tzeit(date.fromisoformat(day), location, rules)
    assert abs(actual - _published(name, day, "tzeit85deg")) <= TOLERANCE


def test_tzeit_by_fixed_minutes_matches_published():
    rules = ZmanimRules(tzeit=TzeitRule.minutes(50))
    actual = tzeit(date(2026, 10, 24), JERUSALEM, rules)
    assert abs(actual - _published("jerusalem", "2026-10-24", "tzeit50min")) <= TOLERANCE


def test_tzeit_rule_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        TzeitRule(kind="vibes", value=3).validate()


def test_shabbat_window_runs_from_friday_candles_to_saturday_tzeit():
    windows = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)
    assert len(windows) == 1
    window = windows[0]
    assert window.kinds == (KIND_SHABBAT,)
    assert abs(window.start - _published_candle("jerusalem", "2026-10-23")) <= TOLERANCE
    assert abs(window.end - _published("jerusalem", "2026-10-24", "tzeit85deg")) <= TOLERANCE


def test_friday_and_saturday_report_the_same_window():
    friday = windows_for(date(2026, 10, 23), JERUSALEM, ISRAEL_RULES)
    saturday = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)
    assert friday == saturday


def test_an_ordinary_weekday_has_no_window():
    assert windows_for(date(2026, 10, 20), JERUSALEM, ISRAEL_RULES) == []


def test_window_contains_is_half_open():
    window = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)[0]
    assert window.contains(window.start)
    assert window.contains(window.start + timedelta(hours=1))
    assert not window.contains(window.end)
    assert not window.contains(window.start - timedelta(seconds=1))


def test_window_contains_requires_an_aware_moment():
    window = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)[0]
    with pytest.raises(ValueError):
        window.contains(datetime(2026, 10, 24, 12, 0))


def test_yom_kippur_is_its_own_window_kind():
    windows = windows_for(date(2026, 9, 21), JERUSALEM, ISRAEL_RULES)
    assert len(windows) == 1
    window = windows[0]
    assert KIND_YOM_KIPPUR in window.kinds
    assert window.days == (date(2026, 9, 21),)
    # Starts the evening before (erev Yom Kippur, 2026-09-20).
    assert window.start.astimezone(ZoneInfo("Asia/Jerusalem")).date() == date(2026, 9, 20)
    assert window.end.astimezone(ZoneInfo("Asia/Jerusalem")).date() == date(2026, 9, 21)


def test_two_day_rosh_hashana_merges_with_the_shabbat_it_starts_on():
    """2026-09-12 Sat + 2026-09-13 Sun: one window, not two or three."""
    windows = windows_for(date(2026, 9, 13), JERUSALEM, ISRAEL_RULES)
    assert len(windows) == 1
    window = windows[0]
    assert window.days == (date(2026, 9, 12), date(2026, 9, 13))
    assert set(window.kinds) == {KIND_SHABBAT, KIND_YOM_TOV}
    assert window.start.astimezone(ZoneInfo("Asia/Jerusalem")).date() == date(2026, 9, 11)
    assert window.end.astimezone(ZoneInfo("Asia/Jerusalem")).date() == date(2026, 9, 13)
    # Every intervening midnight is inside the single merged window.
    assert window.contains(datetime(2026, 9, 13, 0, 0, tzinfo=ZoneInfo("Asia/Jerusalem")))


def test_diaspora_second_day_extends_the_window_israel_ends_earlier():
    """Sukkot: Israel stops Saturday night, the diaspora runs to Sunday night."""
    israel = windows_for(date(2026, 9, 26), JERUSALEM, ISRAEL_RULES)
    diaspora = windows_for(date(2026, 9, 26), NEW_YORK, DIASPORA_RULES)
    assert len(israel) == 1 and len(diaspora) == 1
    assert israel[0].days == (date(2026, 9, 26),)
    assert diaspora[0].days == (date(2026, 9, 26), date(2026, 9, 27))
    assert windows_for(date(2026, 9, 27), JERUSALEM, ISRAEL_RULES) == []
    assert windows_for(date(2026, 9, 27), NEW_YORK, DIASPORA_RULES) == diaspora


def test_simchat_torah_merges_with_shemini_atzeret_in_the_diaspora():
    windows = windows_for(date(2026, 10, 4), NEW_YORK, DIASPORA_RULES)
    assert len(windows) == 1
    assert windows[0].days == (date(2026, 10, 3), date(2026, 10, 4))


def test_erev_yom_kippur_reports_only_the_window_it_opens():
    """Sunday 2026-09-20: Shabbat ended Saturday night, Yom Kippur opens."""
    windows = windows_for(date(2026, 9, 20), JERUSALEM, ISRAEL_RULES)
    assert [window.days for window in windows] == [(date(2026, 9, 21),)]


def test_merging_leaves_at_most_one_window_per_civil_day():
    """Two windows on one day would mean a run that should have merged."""
    day = date(2026, 1, 1)
    end = date(2028, 1, 1)
    while day < end:
        assert len(windows_for(day, NEW_YORK, DIASPORA_RULES)) <= 1
        day += timedelta(days=1)


def test_windows_are_sorted_and_utc():
    for window in windows_for(date(2026, 9, 20), JERUSALEM, ISRAEL_RULES):
        assert window.start.tzinfo is timezone.utc
        assert window.end.tzinfo is timezone.utc
        assert window.start < window.end


def test_next_window_finds_the_coming_shabbat():
    moment = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)  # a Tuesday
    window = next_window(moment, JERUSALEM, ISRAEL_RULES)
    assert window is not None
    assert window.days == (date(2026, 10, 24),)
    assert window.start > moment


def test_next_window_returns_the_window_already_running():
    inside = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)[0]
    moment = inside.start + timedelta(hours=2)
    assert next_window(moment, JERUSALEM, ISRAEL_RULES) == inside


def test_next_window_requires_an_aware_moment():
    with pytest.raises(ValueError):
        next_window(datetime(2026, 10, 20, 12, 0), JERUSALEM, ISRAEL_RULES)


def test_window_is_hashable_and_comparable():
    windows = windows_for(date(2026, 10, 24), JERUSALEM, ISRAEL_RULES)
    assert isinstance(windows[0], Window)
    assert {windows[0]} == {windows_for(date(2026, 10, 23), JERUSALEM, ISRAEL_RULES)[0]}


def test_rules_reject_a_negative_candle_offset():
    with pytest.raises(ValueError):
        ZmanimRules(candle_lighting_offset_minutes=-1).validate()


def test_rules_reject_a_negative_tzeit_value():
    with pytest.raises(ValueError):
        ZmanimRules(tzeit=TzeitRule.minutes(-5)).validate()


def test_default_rules_are_diaspora_eighteen_minutes_and_eight_and_a_half_degrees():
    rules = ZmanimRules().validate()
    assert rules.candle_lighting_offset_minutes == 18
    assert rules.tzeit == TzeitRule.degrees(8.5)
    assert rules.israel is False


def test_next_window_gives_up_rather_than_searching_forever():
    moment = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    assert next_window(moment, JERUSALEM, ISRAEL_RULES, search_days=1) is None


def test_far_north_location_raises_rather_than_guessing():
    from shabbos_goy.zmanim import SunEventNotFound

    longyearbyen = Location(78.2232, 15.6469, "Arctic/Longyearbyen")
    with pytest.raises(SunEventNotFound):
        windows_for(date(2026, 6, 20), longyearbyen, DIASPORA_RULES)
