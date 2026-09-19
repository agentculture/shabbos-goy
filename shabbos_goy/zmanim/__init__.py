"""Zmanim: when the strict window opens and closes, computed with the stdlib.

Three concerns, three files, so each can be checked on its own:

* ``sun`` - NOAA solar position maths (sunrise, sunset, depression angles),
  all in UTC.
* ``hebrew`` - Hebrew calendar arithmetic and which days are Yom Tov.
* ``windows`` - candle lighting, tzeit and the merged strict windows the
  mode resolver gates on.

Nothing here reads the clock, the environment or a config file: callers pass
a :class:`Location` (with an explicit IANA timezone) and :class:`ZmanimRules`
(candle-lighting offset, tzeit definition, israel/diaspora), both of which
come from configuration. The maths runs in UTC and converts only where a
civil date is needed.

A wrong answer here fails toward acting on a holy day, so the expected values
in the tests come from a published source (Hebcal), never from this code.
"""

from __future__ import annotations

from . import hebrew, sun
from .hebrew import HebrewDate
from .location import Location
from .sun import SunEventNotFound
from .windows import (
    KIND_SHABBAT,
    KIND_YOM_KIPPUR,
    KIND_YOM_TOV,
    TZEIT_DEGREES,
    TZEIT_MINUTES,
    TzeitRule,
    Window,
    ZmanimRules,
    candle_lighting,
    day_kinds,
    next_window,
    tzeit,
    windows_for,
)

__all__ = [
    "HebrewDate",
    "KIND_SHABBAT",
    "KIND_YOM_KIPPUR",
    "KIND_YOM_TOV",
    "Location",
    "SunEventNotFound",
    "TZEIT_DEGREES",
    "TZEIT_MINUTES",
    "TzeitRule",
    "Window",
    "ZmanimRules",
    "candle_lighting",
    "day_kinds",
    "hebrew",
    "next_window",
    "sun",
    "tzeit",
    "windows_for",
]
