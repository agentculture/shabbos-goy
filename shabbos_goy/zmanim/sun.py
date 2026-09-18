"""Solar position maths (NOAA), stdlib only.

Implements the NOAA Solar Calculator equations: equation of time, solar
declination and the hour angle at a given solar zenith. Everything here is
pure arithmetic on a date and a :class:`~shabbos_goy.zmanim.location.Location`
and returns timezone-aware **UTC** instants; nothing reads the clock or the
environment.

Accuracy is about a minute, which is why the test vectors allow two.

Deliberately separate from the Hebrew calendar (``hebrew.py``): one file is
astronomy, the other is arithmetic on a fixed calendar, and mixing them makes
both harder to check.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from .location import Location

#: Geometric zenith plus the standard refraction and solar-radius allowance.
#: The sun's upper limb touches the horizon at this zenith angle.
SUNRISE_SUNSET_ZENITH = 90.833

#: Julian day number of the J2000.0 epoch.
_J2000 = 2451545.0

_EPOCH = datetime(2000, 1, 1, 12, 0, tzinfo=timezone.utc)


class SunEventNotFound(ValueError):
    """The sun never reaches the requested altitude on that date.

    Raised for polar day and polar night rather than returning a guessed
    time: a made-up sunset would silently move the strict window.
    """


def _julian_day(moment: datetime) -> float:
    """Julian day of a UTC instant."""
    return _J2000 + (moment - _EPOCH).total_seconds() / 86400.0


def _julian_century(julian_day: float) -> float:
    return (julian_day - _J2000) / 36525.0


def _geometric_mean_longitude(t: float) -> float:
    return (280.46646 + t * (36000.76983 + t * 0.0003032)) % 360.0


def _geometric_mean_anomaly(t: float) -> float:
    return 357.52911 + t * (35999.05029 - 0.0001537 * t)


def _eccentricity(t: float) -> float:
    return 0.016708634 - t * (0.000042037 + 0.0000001267 * t)


def _equation_of_centre(t: float) -> float:
    m = math.radians(_geometric_mean_anomaly(t))
    return (
        math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m) * 0.000289
    )


def _apparent_longitude(t: float) -> float:
    true_longitude = _geometric_mean_longitude(t) + _equation_of_centre(t)
    return true_longitude - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))


def _obliquity_correction(t: float) -> float:
    seconds = 21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))
    mean_obliquity = 23.0 + (26.0 + seconds / 60.0) / 60.0
    return mean_obliquity + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))


def _declination(t: float) -> float:
    """Solar declination in degrees."""
    obliquity = math.radians(_obliquity_correction(t))
    apparent = math.radians(_apparent_longitude(t))
    return math.degrees(math.asin(math.sin(obliquity) * math.sin(apparent)))


def _equation_of_time(t: float) -> float:
    """Equation of time in minutes."""
    epsilon = math.radians(_obliquity_correction(t))
    mean_longitude = math.radians(_geometric_mean_longitude(t))
    anomaly = math.radians(_geometric_mean_anomaly(t))
    eccentricity = _eccentricity(t)
    y = math.tan(epsilon / 2.0) ** 2
    value = (
        y * math.sin(2 * mean_longitude)
        - 2 * eccentricity * math.sin(anomaly)
        + 4 * eccentricity * y * math.sin(anomaly) * math.cos(2 * mean_longitude)
        - 0.5 * y * y * math.sin(4 * mean_longitude)
        - 1.25 * eccentricity * eccentricity * math.sin(2 * anomaly)
    )
    return 4.0 * math.degrees(value)


def _hour_angle(latitude: float, declination_deg: float, zenith_deg: float) -> float:
    """Hour angle in degrees between solar noon and the given zenith."""
    lat = math.radians(latitude)
    dec = math.radians(declination_deg)
    cos_ha = math.cos(math.radians(zenith_deg)) / (math.cos(lat) * math.cos(dec)) - math.tan(
        lat
    ) * math.tan(dec)
    if cos_ha > 1.0 or cos_ha < -1.0:
        raise SunEventNotFound(
            f"the sun does not reach zenith {zenith_deg}deg at latitude {latitude}"
        )
    return math.degrees(math.acos(cos_ha))


def _reference_instant(day: date, location: Location) -> datetime:
    """Local noon of ``day``, in UTC.

    Anchoring on *local* noon is what makes the returned event fall on the
    caller's civil date even where the timezone is far from the longitude
    (UTC+14, say), without ever consulting the process timezone.
    """
    local_noon = datetime(day.year, day.month, day.day, 12, 0, tzinfo=location.zone())
    return local_noon.astimezone(timezone.utc)


def _solar_noon_utc(reference: datetime, location: Location) -> datetime:
    """Solar noon nearest ``reference``, refined once."""
    midnight = reference.replace(hour=0, minute=0, second=0, microsecond=0)
    moment = reference
    for _ in range(2):
        t = _julian_century(_julian_day(moment))
        minutes = 720.0 - 4.0 * location.longitude - _equation_of_time(t)
        moment = midnight + timedelta(minutes=minutes)
    return moment


def solar_noon(day: date, location: Location) -> datetime:
    """Solar noon (chatzot) on ``day``, as a UTC instant."""
    return _solar_noon_utc(_reference_instant(day, location), location)


def _solar_event(day: date, location: Location, zenith_deg: float, *, evening: bool) -> datetime:
    noon = _solar_noon_utc(_reference_instant(day, location), location)
    moment = noon
    for _ in range(2):
        t = _julian_century(_julian_day(moment))
        hour_angle = _hour_angle(location.latitude, _declination(t), zenith_deg)
        offset = timedelta(minutes=4.0 * hour_angle)
        moment = noon + offset if evening else noon - offset
    return moment


def sunset(day: date, location: Location) -> datetime:
    """Sunset (shkia) on ``day``, as a UTC instant."""
    return _solar_event(day, location, SUNRISE_SUNSET_ZENITH, evening=True)


def sunrise(day: date, location: Location) -> datetime:
    """Sunrise (netz) on ``day``, as a UTC instant."""
    return _solar_event(day, location, SUNRISE_SUNSET_ZENITH, evening=False)


def depression_time(
    day: date, location: Location, degrees: float, *, evening: bool = True
) -> datetime:
    """When the sun's centre is ``degrees`` below the geometric horizon.

    This is the depression-angle form of tzeit (8.5deg and 7.083deg are the
    common choices). No refraction allowance: the angle is geometric, which
    is how the published tables define it.
    """
    if degrees < 0:
        raise ValueError(f"depression angle must not be negative: {degrees}")
    return _solar_event(day, location, 90.0 + degrees, evening=evening)
