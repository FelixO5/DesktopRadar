"""geo.py against known values — no Qt, no network."""

import math

import pytest

from radarscope import geo

LONDON = (51.5074, -0.1278)
PARIS = (48.8566, 2.3522)


def test_unit_conversion():
    assert geo.nm_to_miles(1) == pytest.approx(1.1508, abs=1e-4)
    assert geo.miles_to_nm(geo.nm_to_miles(42.0)) == pytest.approx(42.0)
    assert geo.miles_to_nm(1.150779) == pytest.approx(1.0)


def test_london_paris_distance_and_bearing():
    d_nm = geo.haversine_nm(*LONDON, *PARIS)
    assert d_nm == pytest.approx(185, abs=2)
    assert geo.nm_to_miles(d_nm) == pytest.approx(213, abs=2)
    assert geo.initial_bearing(*LONDON, *PARIS) == pytest.approx(148, abs=1)


def test_bearing_cardinal_and_normalised():
    assert geo.initial_bearing(51, 0, 52, 0) == pytest.approx(0, abs=1e-9)
    assert geo.initial_bearing(51, 0, 50, 0) == pytest.approx(180)
    west = geo.initial_bearing(51, 0, 51, -1)
    assert 269 < west < 271
    assert 0 <= geo.initial_bearing(51, 0, 51.0001, -0.0001) < 360


def test_zero_distance():
    assert geo.haversine_nm(*LONDON, *LONDON) == 0


def test_polar_to_screen_north_is_straight_up():
    x, y, inside = geo.polar_to_screen(25, 0, 50, 100, 100, 80)
    assert inside
    assert x == pytest.approx(100)
    assert y == pytest.approx(60)  # half of r_max above centre


def test_polar_to_screen_east_and_south():
    x, y, _ = geo.polar_to_screen(50, 90, 50, 100, 100, 80)
    assert (x, y) == (pytest.approx(180), pytest.approx(100))
    x, y, _ = geo.polar_to_screen(50, 180, 50, 100, 100, 80)
    assert (x, y) == (pytest.approx(100), pytest.approx(180))


def test_polar_to_screen_clips_beyond_range():
    x, y, inside = geo.polar_to_screen(120, 90, 50, 100, 100, 80, clip=True)
    assert not inside
    assert x == pytest.approx(180)  # pinned to the rim
    x, y, inside = geo.polar_to_screen(120, 90, 50, 100, 100, 80, clip=False)
    assert x == pytest.approx(100 + 80 * 120 / 50)


def test_dead_reckon_600kt_60s_is_10nm():
    lat, lon = geo.dead_reckon(51.0, -1.0, 600, 90, 60)
    assert geo.haversine_nm(51.0, -1.0, lat, lon) == pytest.approx(10.0, abs=1e-6)
    assert geo.initial_bearing(51.0, -1.0, lat, lon) == pytest.approx(90, abs=0.2)


def test_dead_reckon_due_north_increases_latitude_only():
    lat, lon = geo.dead_reckon(51.0, -1.0, 360, 0, 600)  # 60 nm = 1 deg of latitude
    assert lat == pytest.approx(52.0, abs=1e-3)
    assert lon == pytest.approx(-1.0, abs=1e-9)


def test_dead_reckon_noop_cases():
    assert geo.dead_reckon(51, -1, 0, 90, 60) == (51, -1)
    assert geo.dead_reckon(51, -1, 400, 90, 0) == (51, -1)


def test_destination_round_trip():
    lat, lon = geo.destination_point(*LONDON, 148.0, 185.0)
    assert geo.haversine_nm(*LONDON, lat, lon) == pytest.approx(185.0, rel=1e-9)


def test_api_radius_is_nm_and_capped():
    assert geo.api_radius_nm(50) == math.ceil(50 / geo.MILES_PER_NM)  # 44 nm, not 50
    assert geo.api_radius_nm(287) == 250
    assert geo.api_radius_nm(1000) == 250
    assert geo.api_radius_nm(0.1) == 1


def test_compass_point():
    assert geo.compass_point(0) == "N"
    assert geo.compass_point(148) == "SSE"
    assert geo.compass_point(359) == "N"
    assert geo.compass_point(270) == "W"
