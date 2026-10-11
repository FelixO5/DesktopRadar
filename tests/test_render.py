"""Headless rendering (QT_QPA_PLATFORM=offscreen): force real paintEvents via grab()."""

import time

import pytest
from PySide6.QtCore import QElapsedTimer, QPointF, QRect, Qt
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
    # just beyond the 50-mile scope (~60 nm north) -> clipped to the rim
    {"hex": "aaa004", "flight": "FAR9", "lat": HOME[0] + 1.0, "lon": HOME[1], "alt_baro": 39000,
     "gs": 480, "track": 180, "seen_pos": 0.5},
    # far beyond (~120 nm, > 2x range) -> hidden
    {"hex": "aaa006", "flight": "VFAR1", "lat": HOME[0] + 2.0, "lon": HOME[1], "alt_baro": 37000,
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
    assert result.dropped == 1 and len(store) == 5
    pixmap = scope.grab()  # forces a real paintEvent
    assert not pixmap.isNull()
    stats = scope.last_paint_stats
    assert (stats.on_scope, stats.clipped, stats.hidden) == (3, 1, 1)


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
    assert cfg.scope_range_mi == 60 and win.scope.scope_range_mi == 60
    win.zoom_in()
    win.zoom_in()
    assert cfg.scope_range_mi == 40
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
        assert wait_for(lambda: len(win.store) == 5)
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


def _wheel(widget, dy):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(widget.width() / 2, widget.height() / 2)
    ev = QWheelEvent(pos, widget.mapToGlobal(pos), QPoint(0, 0), QPoint(0, dy),
                     Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                     Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, ev)


class RecordingPoller:
    def __init__(self):
        self.calls = []

    def update_params(self, params, refresh=True):
        self.calls.append((params.radius_nm, refresh))

    def stop(self):
        pass


def test_scroll_zoom_steps_ladder_and_debounces_polling(qapp, tmp_path):
    cfg = Config(postcode="GL14 2AB", home_lat=HOME[0], home_lon=HOME[1], scope_range_mi=50)
    win = RadarWindow(cfg, tmp_path / "config.toml", start_polling=False)
    win.poller = RecordingPoller()
    win.show()
    _wheel(win.scope, 120)          # one notch away from you: zoom in
    assert cfg.scope_range_mi == 40
    _wheel(win.scope, 60)           # half a notch (trackpad): accumulates, no change yet
    assert cfg.scope_range_mi == 40
    _wheel(win.scope, 60)
    assert cfg.scope_range_mi == 30
    _wheel(win.scope, -360)         # three notches back: zoom out
    assert cfg.scope_range_mi == 60
    assert win.poller.calls == []   # nothing sent while still scrolling
    win._push_poll_params()         # what the debounce timer fires
    assert win.poller.calls == [(geo.api_radius_nm(60), True)]  # wider -> re-poll now
    win.zoom_in()
    win._push_poll_params()
    assert win.poller.calls[-1] == (geo.api_radius_nm(50), False)  # narrower -> no extra request
    for _ in range(40):
        win.zoom_in()
    assert cfg.scope_range_mi == 2
    win.poller = None
    win.shutdown()


def test_full_screen_toggle_keeps_widget_geometry(qapp, tmp_path):
    cfg = Config(postcode="GL14 2AB", home_lat=HOME[0], home_lon=HOME[1])
    cfg.window.x, cfg.window.y, cfg.window.size = 100, 100, 400
    win = RadarWindow(cfg, tmp_path / "config.toml", start_polling=False)
    win.show()
    win.set_full_screen(True)
    assert win.full_screen and cfg.window.fullscreen
    assert not win.grab().isNull()          # paints edge-to-edge without crashing
    win.set_full_screen(False)
    assert not win.full_screen and not cfg.window.fullscreen
    assert cfg.window.size == 400            # full-screen size never saved as the widget size
    assert win.windowFlags() & Qt.WindowType.FramelessWindowHint
    win.shutdown()


def test_airline_names_and_silhouettes_render(qapp):
    scope, store, _ = make_scope(500)
    store.update([
        {"hex": "400a01", "flight": "BAW12K", "t": "A320", "lat": HOME[0] + 0.1, "lon": HOME[1],
         "alt_baro": 12000, "track": 90},
        {"hex": "43c6f1", "flight": "RRR4401", "t": "A332", "dbFlags": 1, "lat": HOME[0] - 0.1,
         "lon": HOME[1], "alt_baro": 24000, "track": 200},
        {"hex": "400b02", "flight": "GHELI", "category": "A7", "lat": HOME[0], "lon": HOME[1] + 0.15,
         "alt_baro": 1500, "track": 10},
    ], NOW)
    scope.grab()
    lines = scope.detail_lines(store.get("400a01"), NOW)
    assert "OPR  British Airways" in lines
    assert "OPR  Royal Air Force" in scope.detail_lines(store.get("43c6f1"), NOW)
    assert "Helicopter" in scope.detail_lines(store.get("400b02"), NOW)[1]
    scope.show_operator = False
    assert not scope.grab().isNull()


def test_every_silhouette_builds():
    from radarscope.aircraft_types import Category
    from radarscope.icons import SCALE, silhouette
    for cat in Category:
        path = silhouette(cat)
        box = path.boundingRect()
        assert not path.isEmpty() and cat in SCALE
        assert box.top() >= -1.25 and box.bottom() <= 1.1 and box.width() <= 2.2, (cat, box)
