import pytest

from radarscope import geo
from radarscope.model import EXPIRE_AFTER_S, FADE_OUT_S, Aircraft, AircraftStore


def obj(**kw):
    base = {"hex": "4CA123", "flight": "RYR12AB ", "lat": 51.9, "lon": -2.4, "alt_baro": 35000,
            "gs": 450, "track": 90, "squawk": "1234", "seen_pos": 0.0}
    base.update(kw)
    return base


def test_parse_fields():
    ac = Aircraft.from_feed(obj(r="EI-ABC", t="B738", desc="BOEING 737-800", baro_rate=-1200), 100.0)
    assert ac.hex == "4ca123"
    assert ac.callsign == "RYR12AB"
    assert ac.flight_level == "FL350"
    assert ac.vertical_trend == -1
    assert ac.registration == "EI-ABC" and ac.type_code == "B738"
    assert ac.emergency is None


def test_flight_level_formatting():
    assert Aircraft.from_feed(obj(alt_baro=4500), 0).flight_level == "FL045"
    assert Aircraft.from_feed(obj(alt_baro="ground"), 0).flight_level == "GND"
    g = Aircraft.from_feed(obj(alt_baro="ground"), 0)
    assert g.on_ground and g.altitude_ft == 0
    assert Aircraft.from_feed(obj(alt_baro=None), 0).flight_level == "FL---"


def test_no_position_is_dropped():
    assert Aircraft.from_feed(obj(lat=None), 0) is None
    assert Aircraft.from_feed({"hex": "abc"}, 0) is None


def test_emergency_and_military():
    ac = Aircraft.from_feed(obj(squawk="7700", dbFlags=1), 0)
    assert ac.emergency == "EMERGENCY"
    assert ac.military


def test_label_fallbacks():
    assert Aircraft.from_feed(obj(flight="   ", r="G-ABCD"), 0).label == "G-ABCD"
    assert Aircraft.from_feed(obj(flight=None), 0).label == "4CA123"


def test_fix_time_accounts_for_seen_pos_and_dead_reckons():
    ac = Aircraft.from_feed(obj(gs=600, track=0, seen_pos=6.0), received_at=1000.0)
    assert ac.fix_time == pytest.approx(994.0)
    lat, lon = ac.position_at(1054.0)  # 60 s after the fix -> 10 nm north
    assert geo.haversine_nm(ac.lat, ac.lon, lat, lon) == pytest.approx(10.0, abs=1e-6)
    assert lat > ac.lat


def test_ground_aircraft_do_not_move():
    ac = Aircraft.from_feed(obj(alt_baro="ground", gs=20), 0)
    assert ac.position_at(30) == (ac.lat, ac.lon)


def test_store_update_counts_and_snaps():
    store = AircraftStore()
    r = store.update([obj(), obj(hex="abc", lat=None), "junk"], 10.0)
    assert r.added == ["4ca123"] and r.dropped == 2
    r = store.update([obj(lat=52.0)], 18.0)
    assert r.updated == 1 and not r.added
    assert store.get("4ca123").lat == 52.0  # snapped to the true fix


def test_expiry_relative_to_successful_polls_with_fade():
    store = AircraftStore()
    store.update([obj()], 0.0)
    # Network outage: no successful polls -> keep showing last good data.
    assert store.expire(500.0) == [] and len(store) == 1
    # Polls resume without this aircraft.
    store.update([], EXPIRE_AFTER_S + 1)
    assert store.expire(EXPIRE_AFTER_S + 1) == []
    ac = store.get("4ca123")
    assert ac.expiring_since is not None
    assert 0 < ac.opacity(EXPIRE_AFTER_S + 1 + FADE_OUT_S / 2) < 1
    assert store.expire(EXPIRE_AFTER_S + 1 + FADE_OUT_S) == ["4ca123"]
    assert len(store) == 0


def test_reappearing_aircraft_cancels_fade():
    store = AircraftStore()
    store.update([obj()], 0.0)
    store.update([], 70.0)
    store.expire(70.0)
    store.update([obj()], 72.0)
    assert store.get("4ca123").expiring_since is None


def test_opacity_dims_with_age():
    ac = Aircraft.from_feed(obj(), 0.0)
    assert ac.opacity(1.0) == 1.0
    assert ac.opacity(40.0) < 1.0
    assert ac.opacity(1000.0) == pytest.approx(0.35)
