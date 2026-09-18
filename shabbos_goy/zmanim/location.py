"""Where the agent is, for sun maths.

A location is latitude, longitude and an **explicit IANA timezone**. The
timezone is never taken from the process environment: ``TZ`` in a container
is one more thing that can silently be wrong, and a wrong local date moves
the strict window. Everything downstream computes in UTC and converts with
this zone.
"""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@dataclass(frozen=True)
class Location:
    """A fixed observer position with an explicit IANA timezone."""

    latitude: float
    longitude: float
    timezone: str

    def __post_init__(self) -> None:
        if not -90.0 <= self.latitude <= 90.0:
            raise ValueError(f"latitude out of range: {self.latitude}")
        if not -180.0 <= self.longitude <= 180.0:
            raise ValueError(f"longitude out of range: {self.longitude}")
        if not self.timezone:
            raise ValueError("timezone is required (an IANA name such as Asia/Jerusalem)")
        # Resolve the name *now*, not at the first sun calculation. An
        # unknown IANA name used to surface as a ValueError from deep inside
        # next_window, where the mode resolver was not expecting one; here it
        # is simply "this config has no usable location", which every caller
        # already treats as strict.
        self.zone()

    def zone(self) -> ZoneInfo:
        """Resolve the IANA name, raising ``ValueError`` if it is unknown."""
        try:
            return ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {self.timezone!r}") from exc
