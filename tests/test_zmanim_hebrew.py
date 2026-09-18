"""Hebrew calendar checked against published Hebcal holiday dates.

``tests/fixtures/zmanim_yomtov_vectors.json`` holds every day Hebcal marks
``yomtov: true`` for Hebrew years 5786-5800, separately for Israel and the
diaspora. Those dates are the contract: getting one wrong means the agent
treats a Yom Tov as an ordinary weekday.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from shabbos_goy.zmanim import hebrew
from shabbos_goy.zmanim.hebrew import HebrewDate

FIXTURE = Path(__file__).parent / "fixtures" / "zmanim_yomtov_vectors.json"

DATA = json.loads(FIXTURE.read_text(encoding="utf-8"))
YEARS = sorted(int(year) for year in DATA["years"])


def _published(hebrew_year: int, region: str) -> list[dict]:
    return DATA["years"][str(hebrew_year)][region]


def test_fixture_cites_its_source():
    assert "hebcal" in DATA["source"].lower()
    assert "NOT produced by shabbos_goy" in DATA["source"]


def test_fixture_covers_5786_to_5800():
    assert YEARS == list(range(5786, 5801))


@pytest.mark.parametrize("hebrew_year", YEARS)
@pytest.mark.parametrize("region,israel", [("israel", True), ("diaspora", False)])
def test_yom_tov_dates_match_published(hebrew_year, region, israel):
    expected = [date.fromisoformat(item["date"]) for item in _published(hebrew_year, region)]
    actual = sorted(hebrew.yom_tov_dates(hebrew_year, israel=israel))
    assert actual == expected


@pytest.mark.parametrize("hebrew_year", YEARS)
def test_diaspora_adds_exactly_the_second_days(hebrew_year):
    israel = set(hebrew.yom_tov_dates(hebrew_year, israel=True))
    diaspora = set(hebrew.yom_tov_dates(hebrew_year, israel=False))
    assert israel < diaspora
    # Sukkot II, Simchat Torah, Pesach II, Pesach VIII, Shavuot II.
    assert len(diaspora - israel) == 5
    # Every added day immediately follows a day that is Yom Tov in Israel.
    for day in diaspora - israel:
        assert day - timedelta(days=1) in israel


@pytest.mark.parametrize("hebrew_year", YEARS)
def test_yom_kippur_matches_published(hebrew_year):
    expected = [
        date.fromisoformat(item["date"])
        for item in _published(hebrew_year, "israel")
        if item["title"] == "Yom Kippur"
    ]
    assert len(expected) == 1
    assert hebrew.yom_kippur(hebrew_year) == expected[0]
    assert hebrew.to_gregorian(HebrewDate(hebrew_year, 7, 10)) == expected[0]


@pytest.mark.parametrize("hebrew_year", YEARS)
def test_rosh_hashana_matches_the_first_published_yom_tov(hebrew_year):
    first = date.fromisoformat(_published(hebrew_year, "israel")[0]["date"])
    assert hebrew.rosh_hashana(hebrew_year) == first
    assert hebrew.to_gregorian(HebrewDate(hebrew_year, 7, 1)) == first


@pytest.mark.parametrize("hebrew_year", YEARS)
def test_every_published_hdate_converts_both_ways(hebrew_year):
    """Hebcal ships the Hebrew date too; parse it and match the conversion."""
    months = {name: number for number, name in hebrew.MONTH_NAMES.items()}
    for region in ("israel", "diaspora"):
        for item in _published(hebrew_year, region):
            day_text, month_text, year_text = item["hdate"].split()
            expected = HebrewDate(int(year_text), months[month_text], int(day_text))
            gregorian = date.fromisoformat(item["date"])
            assert hebrew.to_gregorian(expected) == gregorian
            assert hebrew.from_gregorian(gregorian) == expected


def test_conversion_round_trips_over_forty_years():
    day = date(2026, 1, 1)
    end = date(2066, 1, 1)
    while day < end:
        assert hebrew.to_gregorian(hebrew.from_gregorian(day)) == day
        day += timedelta(days=1)


def test_leap_years_follow_the_nineteen_year_cycle():
    leap_positions = {0, 3, 6, 8, 11, 14, 17}
    for year in range(5780, 5830):
        assert hebrew.is_leap_year(year) is ((year % 19) in leap_positions)


def test_leap_year_has_thirteen_months_with_two_adars():
    assert hebrew.is_leap_year(5788) is False
    assert hebrew.months_in_year(5788) == 12
    assert hebrew.is_leap_year(5787) is True
    assert hebrew.months_in_year(5787) == 13
    assert hebrew.days_in_month(5787, 12) == 30  # Adar I
    assert hebrew.days_in_month(5787, 13) == 29  # Adar II
    assert hebrew.month_name(5787, 12) == "Adar I"
    assert hebrew.days_in_month(5788, 12) == 29  # plain Adar
    assert hebrew.month_name(5788, 12) == "Adar"


def test_year_lengths_are_always_one_of_the_six_legal_values():
    for year in range(5780, 5900):
        assert hebrew.days_in_year(year) in (353, 354, 355, 383, 384, 385)


def test_rosh_hashana_never_falls_on_sunday_wednesday_or_friday():
    """Lo ADU Rosh - the dechiyot the elapsed-days rule encodes."""
    for year in range(5780, 5900):
        weekday = hebrew.rosh_hashana(year).weekday()
        assert weekday not in (6, 2, 4)  # Sunday, Wednesday, Friday


def test_is_yom_tov_and_name_agree_with_the_date_list():
    for day in hebrew.yom_tov_dates(5787, israel=False):
        assert hebrew.is_yom_tov(day, israel=False)
        assert hebrew.yom_tov_name(day, israel=False)
    ordinary = date(2026, 10, 20)
    assert not hebrew.is_yom_tov(ordinary, israel=False)
    assert hebrew.yom_tov_name(ordinary, israel=False) is None


def test_yom_kippur_is_named_as_such():
    assert hebrew.yom_tov_name(date(2026, 9, 21), israel=True) == "yom_kippur"


def test_sukkot_second_day_is_diaspora_only():
    second_day = date(2026, 9, 27)
    assert hebrew.is_yom_tov(second_day, israel=False)
    assert not hebrew.is_yom_tov(second_day, israel=True)


def test_invalid_hebrew_date_is_rejected():
    with pytest.raises(ValueError):
        hebrew.to_gregorian(HebrewDate(5788, 13, 1))  # no Adar II in 5788
    with pytest.raises(ValueError):
        hebrew.to_gregorian(HebrewDate(5787, 7, 31))  # Tishrei has 30 days
    with pytest.raises(ValueError):
        hebrew.to_gregorian(HebrewDate(5787, 0, 1))
    with pytest.raises(ValueError):
        hebrew.to_gregorian(HebrewDate(0, 7, 1))
