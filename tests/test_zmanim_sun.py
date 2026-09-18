"""Sun maths checked against published Hebcal times.

A wrong sunset moves candle lighting and tzeit, which moves the strict
window, which is how the agent could act on Shabbat. So the expected values
here are not ours: they come from ``tests/fixtures/zmanim_sun_vectors.json``,
fetched from Hebcal's public API (see the fixture's ``source`` field).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shabbos_goy.zmanim import Location, SunEventNotFound, sun

FIXTURE = Path(__file__).parent / "fixtures" / "zmanim_sun_vectors.json"

#: The acceptance criterion's tolerance. Hebcal publishes whole minutes, so
#: part of this budget is rounding.
TOLERANCE = timedelta(minutes=2)


def _load() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


VECTORS = _load()["vectors"]


def _ids(vector: dict) -> str:
    return f"{vector['location']}-{vector['date']}"


def _location(vector: dict) -> Location:
    return Location(
        latitude=vector["latitude"],
        longitude=vector["longitude"],
        timezone=vector["timezone"],
    )


def _published(vector: dict, key: str) -> datetime:
    return datetime.fromisoformat(vector["times"][key]).astimezone(timezone.utc)


def _close(actual: datetime, expected: datetime) -> bool:
    return abs(actual - expected) <= TOLERANCE


def test_fixture_cites_its_source():
    """The vectors file must say where its numbers came from."""
    data = _load()
    assert "hebcal" in data["source"].lower()
    assert "NOT produced by shabbos_goy" in data["source"]


def test_fixture_covers_three_locations_and_both_sides_of_the_dst_change():
    locations = {vector["location"] for vector in VECTORS}
    assert len(locations) >= 3
    dates = {vector["date"] for vector in VECTORS}
    assert any(day < "2026-10-25" for day in dates)
    assert any(day > "2026-10-25" for day in dates)


@pytest.mark.parametrize("vector", VECTORS, ids=_ids)
def test_sunset_matches_published(vector):
    day = date.fromisoformat(vector["date"])
    actual = sun.sunset(day, _location(vector))
    assert _close(actual, _published(vector, "sunset"))


@pytest.mark.parametrize("vector", VECTORS, ids=_ids)
def test_sunrise_matches_published(vector):
    day = date.fromisoformat(vector["date"])
    actual = sun.sunrise(day, _location(vector))
    assert _close(actual, _published(vector, "sunrise"))


@pytest.mark.parametrize("vector", VECTORS, ids=_ids)
def test_solar_noon_matches_published(vector):
    day = date.fromisoformat(vector["date"])
    actual = sun.solar_noon(day, _location(vector))
    assert _close(actual, _published(vector, "chatzot"))


@pytest.mark.parametrize("vector", VECTORS, ids=_ids)
@pytest.mark.parametrize("key,degrees", [("tzeit7083deg", 7.083), ("tzeit85deg", 8.5)])
def test_evening_depression_angles_match_published(vector, key, degrees):
    day = date.fromisoformat(vector["date"])
    actual = sun.depression_time(day, _location(vector), degrees)
    assert _close(actual, _published(vector, key))


@pytest.mark.parametrize("vector", VECTORS, ids=_ids)
def test_results_are_utc_aware_and_land_on_the_local_date(vector):
    """Criterion 1: compute in UTC, convert with an explicit IANA zone."""
    day = date.fromisoformat(vector["date"])
    location = _location(vector)
    moment = sun.sunset(day, location)
    assert moment.tzinfo is timezone.utc
    assert moment.astimezone(ZoneInfo(vector["timezone"])).date() == day


def test_local_date_uses_the_configured_zone_not_the_process_zone(monkeypatch):
    """TZ in the environment must not change a single returned instant."""
    location = Location(31.778, 35.235, "Asia/Jerusalem")
    day = date(2026, 10, 24)
    baseline = sun.sunset(day, location)
    for name in ("UTC", "America/New_York", "Pacific/Kiritimati"):
        monkeypatch.setenv("TZ", name)
        assert sun.sunset(day, location) == baseline


def test_dst_change_shifts_the_local_clock_but_not_the_maths():
    """2026-10-25 ends Israeli DST: same solar day, an hour earlier locally."""
    location = Location(31.778, 35.235, "Asia/Jerusalem")
    zone = ZoneInfo("Asia/Jerusalem")
    before = sun.sunset(date(2026, 10, 24), location).astimezone(zone)
    after = sun.sunset(date(2026, 10, 25), location).astimezone(zone)
    assert before.utcoffset() == timedelta(hours=3)
    assert after.utcoffset() == timedelta(hours=2)
    assert before.hour == 17
    assert after.hour == 16


def test_polar_day_has_no_sunset():
    """Svalbard in June: fail loudly rather than invent a time."""
    longyearbyen = Location(78.2232, 15.6469, "Arctic/Longyearbyen")
    with pytest.raises(SunEventNotFound):
        sun.sunset(date(2026, 6, 21), longyearbyen)


def test_polar_night_has_no_sunrise():
    longyearbyen = Location(78.2232, 15.6469, "Arctic/Longyearbyen")
    with pytest.raises(SunEventNotFound):
        sun.sunrise(date(2026, 12, 21), longyearbyen)


def test_location_rejects_an_unknown_timezone():
    with pytest.raises(ValueError):
        Location(31.778, 35.235, "Mars/Olympus_Mons").zone()


def test_location_rejects_an_empty_timezone():
    with pytest.raises(ValueError):
        Location(31.778, 35.235, "")


def test_depression_angle_must_not_be_negative():
    with pytest.raises(ValueError):
        sun.depression_time(date(2026, 10, 24), Location(31.778, 35.235, "Asia/Jerusalem"), -1.0)


def test_location_rejects_out_of_range_coordinates():
    with pytest.raises(ValueError):
        Location(91.0, 0.0, "UTC")
    with pytest.raises(ValueError):
        Location(0.0, 181.0, "UTC")


def test_extreme_east_timezone_still_lands_on_the_local_date():
    """UTC+14 pushes local noon onto the previous UTC day."""
    kiritimati = Location(1.87, -157.4, "Pacific/Kiritimati")
    day = date(2026, 3, 15)
    moment = sun.sunset(day, kiritimati)
    assert moment.astimezone(ZoneInfo("Pacific/Kiritimati")).date() == day
