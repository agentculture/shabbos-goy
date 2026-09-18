"""Hebrew calendar arithmetic, stdlib only.

Pure integer arithmetic on the fixed (post-Hillel) Hebrew calendar: the
molad-based year start with its four dechiyot, month lengths, conversion to
and from proleptic Gregorian dates, and which days are Yom Tov.

Months are numbered the conventional way for calculation, from Nisan:

===== =============== ===== ===============
1     Nisan           8     Cheshvan
2     Iyyar           9     Kislev
3     Sivan           10    Tevet
4     Tamuz           11    Shvat
5     Av              12    Adar (Adar I in a leap year)
6     Elul            13    Adar II (leap years only)
7     Tishrei
===== =============== ===== ===============

The year number advances at 1 Tishrei, so month 7 begins the year and months
1-6 fall in its second half.

Deliberately separate from ``sun.py``: this file knows nothing about
latitude, longitude or clocks, and every value in it is exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: Offset between this module's elapsed-day count and the proleptic
#: Gregorian ordinal (``date.toordinal()``, where 0001-01-01 is day 1).
_EPOCH_OFFSET = 1373429

NISAN = 1
SIVAN = 3
TISHREI = 7

MONTH_NAMES = {
    1: "Nisan",
    2: "Iyyar",
    3: "Sivan",
    4: "Tamuz",
    5: "Av",
    6: "Elul",
    7: "Tishrei",
    8: "Cheshvan",
    9: "Kislev",
    10: "Tevet",
    11: "Shvat",
    12: "Adar I",
    13: "Adar II",
}

#: Yom Tov: (month, day, name, diaspora_only). These are the days on which
#: melacha is forbidden. Chol HaMoed is not included; neither are rabbinic
#: festivals (Chanukah, Purim) on which it is permitted.
_YOM_TOV: tuple[tuple[int, int, str, bool], ...] = (
    (TISHREI, 1, "rosh_hashana_i", False),
    (TISHREI, 2, "rosh_hashana_ii", False),
    (TISHREI, 10, "yom_kippur", False),
    (TISHREI, 15, "sukkot_i", False),
    (TISHREI, 16, "sukkot_ii", True),
    (TISHREI, 22, "shemini_atzeret", False),
    (TISHREI, 23, "simchat_torah", True),
    (NISAN, 15, "pesach_i", False),
    (NISAN, 16, "pesach_ii", True),
    (NISAN, 21, "pesach_vii", False),
    (NISAN, 22, "pesach_viii", True),
    (SIVAN, 6, "shavuot_i", False),
    (SIVAN, 7, "shavuot_ii", True),
)

YOM_KIPPUR = "yom_kippur"


@dataclass(frozen=True, order=True)
class HebrewDate:
    """A Hebrew year/month/day, months numbered from Nisan (see module docs)."""

    year: int
    month: int
    day: int


def is_leap_year(year: int) -> bool:
    """Is ``year`` one of the seven leap years in the 19-year cycle?"""
    return ((7 * year + 1) % 19) < 7


def months_in_year(year: int) -> int:
    return 13 if is_leap_year(year) else 12


def _elapsed_days(year: int) -> int:
    """Days from the epoch to 1 Tishrei of ``year``, dechiyot applied."""
    prior = year - 1
    months_elapsed = 235 * (prior // 19) + 12 * (prior % 19) + (7 * (prior % 19) + 1) // 19
    parts_elapsed = 204 + 793 * (months_elapsed % 1080)
    hours_elapsed = 5 + 12 * months_elapsed + 793 * (months_elapsed // 1080) + parts_elapsed // 1080
    day = 1 + 29 * months_elapsed + hours_elapsed // 24
    parts = (hours_elapsed % 24) * 1080 + parts_elapsed % 1080

    # Dechiyot: molad zaken, GaTaRaD and BeTU'TaKPaT.
    if (
        parts >= 19440
        or (day % 7 == 2 and parts >= 9924 and not is_leap_year(year))
        or (day % 7 == 1 and parts >= 16789 and is_leap_year(year - 1))
    ):
        day += 1
    # Lo ADU Rosh: never Sunday, Wednesday or Friday.
    if day % 7 in (0, 3, 5):
        day += 1
    return day


def days_in_year(year: int) -> int:
    """353, 354, 355 (plain) or 383, 384, 385 (leap)."""
    return _elapsed_days(year + 1) - _elapsed_days(year)


def is_long_cheshvan(year: int) -> bool:
    return days_in_year(year) % 10 == 5


def is_short_kislev(year: int) -> bool:
    return days_in_year(year) % 10 == 3


def days_in_month(year: int, month: int) -> int:
    """Length of ``month`` in ``year`` (29 or 30 days)."""
    _check_month(year, month)
    if month in (2, 4, 6, 10, 13):
        return 29
    if month == 12 and not is_leap_year(year):
        return 29
    if month == 8 and not is_long_cheshvan(year):
        return 29
    if month == 9 and is_short_kislev(year):
        return 29
    return 30


def month_name(year: int, month: int) -> str:
    """Hebcal-style English name; month 12 is plain ``Adar`` in a plain year."""
    _check_month(year, month)
    if month == 12 and not is_leap_year(year):
        return "Adar"
    return MONTH_NAMES[month]


def _check_month(year: int, month: int) -> None:
    if not 1 <= month <= months_in_year(year):
        raise ValueError(f"month {month} does not exist in Hebrew year {year}")


def to_gregorian(hebrew_date: HebrewDate) -> date:
    """Convert a Hebrew date to a proleptic Gregorian ``date``."""
    year, month, day = hebrew_date.year, hebrew_date.month, hebrew_date.day
    if year < 1:
        raise ValueError(f"Hebrew year out of range: {year}")
    _check_month(year, month)
    if not 1 <= day <= days_in_month(year, month):
        raise ValueError(f"day {day} does not exist in {month_name(year, month)} {year}")
    return date.fromordinal(_to_ordinal(year, month, day))


def _to_ordinal(year: int, month: int, day: int) -> int:
    total = day
    if month < TISHREI:
        # Second half of the year: Tishrei..last month, then Nisan..month-1.
        for other in range(TISHREI, months_in_year(year) + 1):
            total += days_in_month(year, other)
        for other in range(NISAN, month):
            total += days_in_month(year, other)
    else:
        for other in range(TISHREI, month):
            total += days_in_month(year, other)
    return total + _elapsed_days(year) - _EPOCH_OFFSET


def from_gregorian(day: date) -> HebrewDate:
    """Convert a proleptic Gregorian ``date`` to a Hebrew date."""
    ordinal = day.toordinal()
    year = (ordinal + _EPOCH_OFFSET) // 366
    while _to_ordinal(year + 1, TISHREI, 1) <= ordinal:
        year += 1
    month = TISHREI if ordinal < _to_ordinal(year, NISAN, 1) else NISAN
    while ordinal > _to_ordinal(year, month, days_in_month(year, month)):
        month += 1
    return HebrewDate(year, month, ordinal - _to_ordinal(year, month, 1) + 1)


def rosh_hashana(hebrew_year: int) -> date:
    """Gregorian date of 1 Tishrei."""
    return to_gregorian(HebrewDate(hebrew_year, TISHREI, 1))


def yom_kippur(hebrew_year: int) -> date:
    """Gregorian date of 10 Tishrei."""
    return to_gregorian(HebrewDate(hebrew_year, TISHREI, 10))


def yom_tov_dates(hebrew_year: int, *, israel: bool) -> dict[date, str]:
    """Every Yom Tov in ``hebrew_year``, as ``{gregorian date: name}``.

    ``israel=True`` drops the second days of the festivals (but never the
    second day of Rosh Hashana, which is kept everywhere).
    """
    result: dict[date, str] = {}
    for month, day, name, diaspora_only in _YOM_TOV:
        if diaspora_only and israel:
            continue
        result[to_gregorian(HebrewDate(hebrew_year, month, day))] = name
    return result


def yom_tov_name(day: date, *, israel: bool) -> str | None:
    """Name of the Yom Tov falling on ``day``, or ``None``."""
    hebrew_date = from_gregorian(day)
    # A Gregorian date in Nisan-Elul belongs to the Hebrew year that began
    # the previous Tishrei, so one lookup on its own Hebrew year suffices.
    for month, hday, name, diaspora_only in _YOM_TOV:
        if diaspora_only and israel:
            continue
        if hebrew_date.month == month and hebrew_date.day == hday:
            return name
    return None


def is_yom_tov(day: date, *, israel: bool) -> bool:
    return yom_tov_name(day, israel=israel) is not None
