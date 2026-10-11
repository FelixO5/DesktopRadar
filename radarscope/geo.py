"""Dependency-free geo maths for RadarScope.

Units rule: everything here computes in **nautical miles** (the aircraft feed's
``radius`` is nm and ground speed is knots = nm/h). Convert to statute miles
only at the display boundary, using :func:`nm_to_miles` / :func:`miles_to_nm`,
which are the only places the conversion factor is used.

No Qt, no network: this module is unit-tested on its own.
"""

from __future__ import annotations

import math

#: 1 nautical mile expressed in statute miles.
MILES_PER_NM = 1.150779
#: Mean Earth radius in nautical miles.
EARTH_RADIUS_NM = 3440.065
#: The aircraft APIs cap the query radius at 250 nm.
API_MAX_RADIUS_NM = 250.0
#: Largest useful scope range in statute miles (250 nm ~= 287.7 mi).
MAX_SCOPE_RANGE_MI = 287
MIN_SCOPE_RANGE_MI = 1


def nm_to_miles(nm: float) -> float:
    """Nautical miles -> statute miles."""
    return nm * MILES_PER_NM


def miles_to_nm(miles: float) -> float:
    """Statute miles -> nautical miles."""
    return miles / MILES_PER_NM


def api_radius_nm(scope_range_mi: float) -> int:
    """Query radius (whole nm, 1..250) to request from the feed for a mile-based scope.

    Rounded *up* so the request always covers the whole scope, then clamped to the
    API's 250 nm cap.
    """
    nm = miles_to_nm(scope_range_mi)
    return int(min(API_MAX_RADIUS_NM, max(1, math.ceil(nm))))


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in nautical miles."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    a = min(1.0, max(0.0, a))
    return 2 * EARTH_RADIUS_NM * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def initial_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing from point 1 to point 2, degrees true 0..360."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def range_and_bearing(home_lat: float, home_lon: float, lat: float, lon: float) -> tuple[float, float]:
    """(range_nm, bearing_deg) from home to a target."""
    return haversine_nm(home_lat, home_lon, lat, lon), initial_bearing(home_lat, home_lon, lat, lon)


def polar_to_screen(
    range_nm: float,
    bearing_deg: float,
    scope_range_nm: float,
    cx: float,
    cy: float,
    r_max_px: float,
    clip: bool = True,
) -> tuple[float, float, bool]:
    """Map (range, bearing) onto the scope. 0 deg = North = up, clockwise.

    Returns ``(x, y, in_range)``. When the target is beyond the scope range and
    ``clip`` is true, the point is pinned to the rim on the correct bearing.
    The ratio is unit-agnostic; both ranges are nm by convention.
    """
    in_range = range_nm <= scope_range_nm
    ratio = range_nm / scope_range_nm if scope_range_nm > 0 else 0.0
    if not in_range and clip:
        ratio = 1.0
    r_px = ratio * r_max_px
    theta = math.radians(bearing_deg)
    return cx + r_px * math.sin(theta), cy - r_px * math.cos(theta), in_range


def destination_point(lat: float, lon: float, bearing_deg: float, distance_nm: float) -> tuple[float, float]:
    """Point reached travelling ``distance_nm`` along ``bearing_deg`` from (lat, lon)."""
    if distance_nm == 0:
        return lat, lon
    delta = distance_nm / EARTH_RADIUS_NM
    theta = math.radians(bearing_deg)
    phi1, lmb1 = math.radians(lat), math.radians(lon)
    sin_phi2 = math.sin(phi1) * math.cos(delta) + math.cos(phi1) * math.sin(delta) * math.cos(theta)
    sin_phi2 = min(1.0, max(-1.0, sin_phi2))
    phi2 = math.asin(sin_phi2)
    y = math.sin(theta) * math.sin(delta) * math.cos(phi1)
    x = math.cos(delta) - math.sin(phi1) * sin_phi2
    lmb2 = lmb1 + math.atan2(y, x)
    lon2 = (math.degrees(lmb2) + 540.0) % 360.0 - 180.0
    return math.degrees(phi2), lon2


def dead_reckon(lat: float, lon: float, gs_knots: float, track_deg: float, dt_seconds: float) -> tuple[float, float]:
    """Project a position forward by ``dt_seconds`` at ``gs_knots`` along ``track_deg``.

    Distance = gs (nm/h) x dt (h), so the result is naturally in nm.
    """
    if dt_seconds <= 0 or gs_knots <= 0:
        return lat, lon
    return destination_point(lat, lon, track_deg, gs_knots * dt_seconds / 3600.0)


def compass_point(bearing_deg: float) -> str:
    """16-wind compass abbreviation for a bearing (e.g. 148 -> 'SSE')."""
    names = ("N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
             "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW")
    return names[int((bearing_deg % 360) / 22.5 + 0.5) % 16]
