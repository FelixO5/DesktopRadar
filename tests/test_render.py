"""Headless rendering (QT_QPA_PLATFORM=offscreen): force real paintEvents via grab()."""

import time

import pytest
from PySide6.QtCore import QElapsedTimer, QPointF, QRect
from PySide6.QtWidgets import QApplication

from radarscope import geo
from radarscope.config import Config
from radarscope.data_source import FeedError, FeedResult
from radarscope.main import RadarWindow, clamp_to_screens, configure_platform
from radarscope.model import AircraftStore
from radarscope.poller import Poller, PollParams, backoff_delay
from radarscope.scope_widget import ScopeWidget

HOME = (51.82, -2.49)
NOW = 5000.0

AIRCRAFT = [
    # normal, ~12 nm due north, high (cyan)
    {"hex": "aaa001", "flight": "BAW123 ", "lat": HOME[0] + 0.2, "lon": HOME[1], "alt_baro": 35000,
     "gs": 450, "track": 45, "squawk": "4521", "t": "A320", "r": "G-EUUA", "seen_pos": 0.0},
    # emergency squawk, east
    {"hex": "aaa002", "flight": "EMG77", "lat": HOME[0], "lon": HOME[1] + 0.3, "alt_baro": 8000,
     "gs": 210, "track": 270, "squawk": "7700", "seen_pos": 1.2},
    # on the ground, near home, no track
    {"hex": "aaa003", "flight": "GND1", "lat": HOME[0] - 0.05, "lon": HOME[1] - 0.05, "alt_baro": "ground",
     "gs": 0, "seen_pos": 3.0},
    # beyond the 50-mile scope (~120 nm north) -> clipped to the rim
    {"hex": "aaa004", "flight": "FAR9", "lat": HOME[0] + 2.0, "lon": HOME[1], "alt_baro": 39000,
     "gs": 480, "track": 180, "seen_pos": 0.5},
    # no position -> dropped
    {"hex": "aaa005", "flight": "NOPOS", "alt_baro": 12000},
]


def make_scope(size=360):
    store = AircraftStore()
    result = store.update(AIRCRAFT, NOW)
    scope = ScopeWidget(store, clock=lambda: NOW)
    scope.resize(size, size)
    scope.set_home(*HOME)
    scope.set_range_miles(50)
    scope.mark_success("adsb.lol")
    return scope, store, result


def test_render_counts(qapp):
    scope, store, result = make_scope()
    assert result.dropped == 1 and len(store) == 4
    pixmap = scope.grab()  # forces a real paintEvent
    assert not pixmap.isNull()
    stats = scope.last_paint_stats
    assert (stats.on_scope, stats.clipped) == (3, 1)


def test_blip_drawn_at_correct_position(qapp):
    scope, _, _ = make_scope()
    image = scope.grab().toImage()
    cx, cy, r_max, _ = scope._geometry()
    rng, brg = geo.range_and_bearing(*HOME, HOME[0] + 0.2, HOME[1])
    x, y, inside = geo.polar_to_screen(rng, brg, scope.scope_range_nm, cx, cy, r_max)
    assert inside and abs(x - cx) < 0.5 and y < cy  # due north -> straight up
    colour = image.pixelColor(round(x), round(y))
    assert colour.blue() > 150 and colour.red() < 140, colour.name()  # cyan high-altitude blip
    assert scope.blip_at(QPointF(x + 2, y + 2)) == "aaa001"
    assert scope.blip_at(QPointF(cx - r_max * 0.8, cy)) is None


def test_render_at_many_sizes_and_sweep_angles(qapp):
    scope, _, _ = make_scope()
    for size in (160, 240, 360, 900):
        scope.resize(size, size)
        for t in (0.0, 1.3, 2.9, 4.4, 5.99):
            scope._clock = lambda t=t: NOW + t
            assert not scope.grab().isNull()


def test_detail_panel_and_lines(qapp):
    scope, store, _ = make_scope()
    scope.grab()
    scope.hover_hex = "aaa002"
    assert not scope.grab().isNull()  # paints the overlay
    lines = scope.detail_lines(store.get("aaa002"), NOW)
    text = "\n".join(lines)
    assert "7700 EMERGENCY" in text
    assert " mi" in text and "BRG" in text
    dist = next(line for line in lines if line.startswith("DIST"))
    miles = float(dist.split()[1])
    assert miles == pytest.approx(12.8, abs=0.6)  # 0.3 deg lon at 51.8N ~ 11.1 nm ~ 12.8 mi


def test_status_states_render(qapp):
    scope, _, _ = make_scope()
    scope.mark_failure("adsb.lol: timed out")
    assert scope.stale
    scope.grab()
    empty = ScopeWidget(AircraftStore())
    empty.resize(300, 300)
    assert not empty.grab().isNull()  # "no location" message path


def test_radar_window_builds_and_paints(qapp, tmp_path):
    cfg = Config(postcode="GL14 2AB", home_lat=HOME[0], home_lon=HOME[1])
    win = RadarWindow(cfg, tmp_path / "config.toml", start_polling=False)
    win.store.update(AIRCRAFT, time.monotonic())
    win.show()
    assert not win.grab().isNull()
    win.step_range(+1)
    assert cfg.scope_range_mi == 100 and win.scope.scope_range_mi == 100
    win.step_range(-1)
    win.step_range(-1)
    assert cfg.scope_range_mi == 25
    win.build_menu()
    win.shutdown()
    assert (tmp_path / "config.toml").exists()


def test_clamp_to_screens():
    primary = QRect(0, 0, 1920, 1040)
    screens = [primary, QRect(1920, 0, 1280, 1024)]
    assert clamp_to_screens(100, 100, 360, screens, primary).toTuple() == (100, 100)
    # On the second monitor: kept.
    assert clamp_to_screens(2000, 50, 360, screens, primary).toTuple() == (2000, 50)
    # Saved on a monitor that no longer exists -> default top-right of primary.
    assert clamp_to_screens(5000, 3000, 360, screens, primary).toTuple() == (1920 - 360 - 20, 20)
    # Hanging partially off the edge -> pulled fully on-screen.
    assert clamp_to_screens(1800, -100, 360, [primary], primary).toTuple() == (1560, 0)
    # Unset -> default placement.
    assert clamp_to_screens(None, None, 360, screens, primary).toTuple() == (1540, 20)


def test_configure_platform():
    env = {"XDG_SESSION_TYPE": "wayland"}
    assert configure_platform(env, "linux") and env["QT_QPA_PLATFORM"] == "xcb;wayland"
    env = {"XDG_SESSION_TYPE": "wayland", "QT_QPA_PLATFORM": "wayland"}
    assert configure_platform(env, "linux") is None and env["QT_QPA_PLATFORM"] == "wayland"
    for plat in ("darwin", "win32"):
        env = {"XDG_SESSION_TYPE": "wayland"}
        assert configure_platform(env, plat) is None and "QT_QPA_PLATFORM" not in env
    env = {"XDG_SESSION_TYPE": "x11"}
    assert configure_platform(env, "linux") is None and "QT_QPA_PLATFORM" not in env


def test_backoff():
    assert backoff_delay(8, 0) == 8
    assert [backoff_delay(8, n) for n in (1, 2, 3, 4)] == [8, 16, 32, 64]
    assert backoff_delay(8, 10) == 120


class FakeFeed:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.active = "adsb.lol"
        self.calls = 0

    def fetch(self, lat, lon, radius_nm):
        self.calls += 1
        item = self.outcomes.pop(0) if self.outcomes else FeedResult([], "adsb.lol")
        if isinstance(item, Exception):
            raise item
        return item

    def set_preferred(self, source):
        self.active = source

    def close(self):
        pass


def wait_for(predicate, timeout_ms=5000):
    timer = QElapsedTimer()
    timer.start()
    while not predicate() and timer.elapsed() < timeout_ms:
        QApplication.processEvents()
        time.sleep(0.01)
    return predicate()


def test_poller_thread_delivers_results_and_failures(qapp):
    feed = FakeFeed([FeedError("adsb.lol: HTTP 503"), FeedResult([{"hex": "x"}], "adsb.lol")])
    poller = Poller(PollParams(51.8, -2.5, 44, 5.0, "adsb.lol"), feed=feed)
    got, failed = [], []
    poller.aircraft_received.connect(lambda ac, t, src: got.append((ac, src)))
    poller.fetch_failed.connect(lambda msg, n, delay: failed.append((msg, n, delay)))
    poller.start()
    try:
        assert wait_for(lambda: failed)
        assert failed[0] == ("adsb.lol: HTTP 503", 1, 5.0)
        poller.refresh_now()  # skip the backoff wait
        assert wait_for(lambda: got)
        assert got[0] == ([{"hex": "x"}], "adsb.lol")
    finally:
        poller.stop()
    assert not poller.isRunning()


def test_ring_labels_in_miles():
    from radarscope.scope_widget import format_range_label
    assert [format_range_label(50 * f) for f in (0.25, 0.5, 0.75, 1.0)] == ["12.5mi", "25mi", "37.5mi", "50mi"]
    assert format_range_label(287 * 0.75) == "215mi"


def test_end_to_end_failover_into_window(qapp, tmp_path):
    """Real Poller + AircraftFeed against a mock server: primary down -> stale -> fallback data."""
    import httpx

    from radarscope import data_source as ds

    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        if req.url.host == "api.adsb.lol":
            return httpx.Response(503)
        return httpx.Response(200, json={"ac": AIRCRAFT, "msg": "No error", "now": 0, "total": 5})

    feed = ds.AircraftFeed("adsb.lol", httpx.Client(transport=httpx.MockTransport(handler)),
                           sleep=lambda s: None)
    cfg = Config(postcode="GL14 2AB", home_lat=HOME[0], home_lon=HOME[1])
    win = RadarWindow(cfg, tmp_path / "config.toml", start_polling=False)
    stale_seen = []
    win.poller = Poller(PollParams(HOME[0], HOME[1], 44, 0.05, "adsb.lol"), feed=feed, parent=win)
    win.poller.aircraft_received.connect(win._on_aircraft)
    win.poller.fetch_failed.connect(win._on_fetch_failed)
    win.poller.fetch_failed.connect(lambda *a: stale_seen.append(win.scope.stale))
    win.poller.start()
    try:
        assert wait_for(lambda: len(win.store) == 4)
    finally:
        win.shutdown()
    assert hosts[:3] == ["api.adsb.lol"] * 3 and "api.airplanes.live" in hosts
    assert stale_seen and all(stale_seen)
    assert not win.scope.stale and win.scope.source_name == "airplanes.live"
    win.grab()
    assert win.scope.last_paint_stats.on_scope == 3


def test_military_colour_and_emergency_priority():
    from radarscope.model import Aircraft
    from radarscope.scope_widget import COL_EMERGENCY, COL_HIGH, COL_MILITARY, altitude_colour
    base = {"hex": "43c001", "lat": 51.9, "lon": -2.4, "alt_baro": 35000}
    assert altitude_colour(Aircraft.from_feed(base, 0)) == COL_HIGH
    assert altitude_colour(Aircraft.from_feed({**base, "dbFlags": 1}, 0)) == COL_MILITARY
    assert altitude_colour(Aircraft.from_feed({**base, "dbFlags": 1, "alt_baro": "ground"}, 0)) == COL_MILITARY
    assert altitude_colour(Aircraft.from_feed({**base, "dbFlags": 1, "squawk": "7700"}, 0)) == COL_EMERGENCY
