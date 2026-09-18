#!/usr/bin/env python3
"""Regenerate the zmanim test vectors from Hebcal's public API.

The vectors this writes are the *independent* half of the zmanim tests: a
wrong sunset or a wrong Yom Kippur date fails toward acting on a holy day,
so the expected values must never come from this repo's own implementation.
Hebcal is a published, widely used source (its ``@hebcal/core`` engine backs
hebcal.com's printed calendars), and it is the source cited in the fixture
files.

Run it only when the vectors need refreshing; it needs network access:

    python3 tests/fixtures/fetch_zmanim_vectors.py

It rewrites, next to itself:

* ``zmanim_sun_vectors.json``    - sunrise/solar noon/sunset/tzeit/candle
  lighting for several locations, on dates either side of the 2026-10-25
  Israel DST change and at both solstices.
* ``zmanim_yomtov_vectors.json`` - every Yom Tov (Hebcal's ``yomtov: true``)
  for Hebrew years 5786-5800, separately for Israel and the diaspora.

The tests never call this module; they read the committed JSON.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent

HEBCAL = "https://www.hebcal.com"
ZMANIM_API = f"{HEBCAL}/zmanim"
SHABBAT_API = f"{HEBCAL}/shabbat"
HEBCAL_API = f"{HEBCAL}/hebcal"

SOURCE_NOTE = (
    "Hebcal REST API (hebcal.com), fetched by tests/fixtures/"
    "fetch_zmanim_vectors.py. Hebcal is an independent published source; "
    "these values were NOT produced by shabbos_goy."
)

#: name, latitude, longitude, IANA timezone, candle-lighting offset (minutes)
LOCATIONS = [
    ("jerusalem", 31.778, 35.235, "Asia/Jerusalem", 40),
    ("new_york", 40.7128, -74.006, "America/New_York", 18),
    ("sydney", -33.8688, 151.2093, "Australia/Sydney", 18),
    ("london", 51.5074, -0.1278, "Europe/London", 18),
]

#: Fridays and Saturdays either side of 2026-10-25 (Israel DST ends), plus
#: both solstices, so the sun maths is checked far from the equinox too.
DATES = [
    "2026-10-23",
    "2026-10-24",
    "2026-10-30",
    "2026-10-31",
    "2026-06-19",
    "2026-06-20",
    "2026-12-25",
    "2026-12-26",
]

#: Only the Fridays get a candle-lighting vector.
CANDLE_DATES = ["2026-10-23", "2026-10-30", "2026-06-19", "2026-12-25"]

HEBREW_YEARS = list(range(5786, 5801))

WANTED_TIMES = ("sunrise", "chatzot", "sunset", "tzeit7083deg", "tzeit85deg", "tzeit50min")


def _get(base: str, params: dict[str, object]) -> dict:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(  # noqa: S310 - fixed https Hebcal endpoint
        f"{base}?{query}", headers={"User-Agent": "shabbos-goy-vectors/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
        return json.loads(response.read().decode("utf-8"))


def fetch_sun_vectors() -> dict:
    entries = []
    for name, lat, lon, tzid, offset in LOCATIONS:
        for day in DATES:
            times = _get(
                ZMANIM_API,
                {"cfg": "json", "latitude": lat, "longitude": lon, "tzid": tzid, "date": day},
            )["times"]
            entry = {
                "location": name,
                "latitude": lat,
                "longitude": lon,
                "timezone": tzid,
                "date": day,
                "times": {key: times[key] for key in WANTED_TIMES if key in times},
            }
            if day in CANDLE_DATES:
                shabbat = _get(
                    SHABBAT_API,
                    {
                        "cfg": "json",
                        "latitude": lat,
                        "longitude": lon,
                        "tzid": tzid,
                        "geo": "pos",
                        "gy": day[:4],
                        "gm": day[5:7],
                        "gd": day[8:10],
                        "b": offset,
                        "M": "on",
                    },
                )
                for item in shabbat.get("items", []):
                    if item.get("category") == "candles" and item["date"].startswith(day):
                        entry["candle_lighting"] = {
                            "offset_minutes": offset,
                            "at": item["date"],
                        }
                        break
            entries.append(entry)
    return {
        "source": SOURCE_NOTE,
        "note": (
            "times are local ISO-8601 with offset, as Hebcal returns them; "
            "tzeit7083deg/tzeit85deg are solar depression angles 7.083 and "
            "8.5 degrees, tzeit50min is sunset + 50 minutes"
        ),
        "vectors": entries,
    }


def fetch_yomtov_vectors() -> dict:
    years: dict[str, dict] = {}
    for hebrew_year in HEBREW_YEARS:
        per_region = {}
        for region, israel_flag in (("israel", "on"), ("diaspora", "off")):
            data = _get(
                HEBCAL_API,
                {
                    "v": 1,
                    "cfg": "json",
                    "maj": "on",
                    "year": hebrew_year,
                    "yt": "H",
                    "i": israel_flag,
                    "lg": "s",
                },
            )
            days = []
            for item in data.get("items", []):
                if not item.get("yomtov"):
                    continue
                hdate = item.get("hdate", "")
                if not hdate.endswith(str(hebrew_year)):
                    continue
                days.append({"date": item["date"], "title": item["title"], "hdate": hdate})
            days.sort(key=lambda day: day["date"])
            per_region[region] = days
        years[str(hebrew_year)] = per_region
    return {
        "source": SOURCE_NOTE,
        "note": (
            "every day Hebcal marks yomtov=true within the given Hebrew year, "
            "for Israel (i=on) and the diaspora (i=off)"
        ),
        "years": years,
    }


def main() -> int:
    (HERE / "zmanim_sun_vectors.json").write_text(
        json.dumps(fetch_sun_vectors(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (HERE / "zmanim_yomtov_vectors.json").write_text(
        json.dumps(fetch_yomtov_vectors(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("wrote zmanim_sun_vectors.json and zmanim_yomtov_vectors.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
