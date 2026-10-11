"""Network clients against httpx.MockTransport (no real network)."""

import httpx
import pytest

from radarscope import data_source as ds


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": ds.USER_AGENT})


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def test_normalise_postcode():
    assert ds.normalise_postcode(" gl142ab ") == "GL14 2AB"
    assert ds.normalise_postcode("SW1A  1AA") == "SW1A 1AA"


def test_geocode_success():
    seen = []

    def handler(req):
        seen.append(req.url.raw_path.decode())
        if req.url.path.endswith("/validate"):
            return httpx.Response(200, json={"status": 200, "result": True})
        return httpx.Response(200, json={"status": 200, "result": {
            "postcode": "GL14 2AB", "latitude": 51.82, "longitude": -2.49}})

    r = ds.geocode_postcode("gl14 2ab", client_for(handler))
    assert (r.postcode, r.latitude, r.longitude) == ("GL14 2AB", 51.82, -2.49)
    assert seen == ["/postcodes/GL14%202AB/validate", "/postcodes/GL14%202AB"]


def test_geocode_invalid_postcode_is_friendly():
    handler = lambda req: httpx.Response(200, json={"status": 200, "result": False})
    with pytest.raises(ds.GeocodeError, match="isn't a recognised UK postcode"):
        ds.geocode_postcode("ZZ99 9ZZ", client_for(handler))


def test_geocode_network_error():
    def handler(req):
        raise httpx.ConnectError("boom")
    with pytest.raises(ds.GeocodeError, match="Couldn't reach postcodes.io"):
        ds.geocode_postcode("GL14 2AB", client_for(handler))


def test_feed_url_radius_is_nm_and_capped():
    assert ds.feed_url("adsb.lol", 51.82, -2.49, 44) == "https://api.adsb.lol/v2/point/51.8200/-2.4900/44"
    assert ds.feed_url("airplanes.live", 0, 0, 999).endswith("/250")


def test_fetch_parses_and_sets_user_agent():
    def handler(req):
        assert req.headers["User-Agent"].startswith("RadarScope/")
        return httpx.Response(200, json={"ac": [{"hex": "abc"}], "msg": "No error", "now": 1, "total": 1})

    feed = ds.AircraftFeed("adsb.lol", client_for(handler))
    res = feed.fetch(51.8, -2.5, 44)
    assert res.aircraft == [{"hex": "abc"}] and res.source == "adsb.lol"


def test_failover_after_repeated_errors_and_return_to_preferred():
    clock = FakeClock()
    state = {"primary_up": False}
    hosts = []

    def handler(req):
        hosts.append(req.url.host)
        if req.url.host == "api.adsb.lol" and not state["primary_up"]:
            return httpx.Response(503)
        return httpx.Response(200, json={"ac": []})

    feed = ds.AircraftFeed("adsb.lol", client_for(handler), clock=clock, sleep=clock.sleep)
    for _ in range(ds.FAILOVER_AFTER):
        with pytest.raises(ds.FeedError, match="HTTP 503"):
            feed.fetch(51, -2, 44)
        clock.t += 8
    assert feed.active == "airplanes.live"
    assert feed.fetch(51, -2, 44).source == "airplanes.live"

    # Preferred still down at the retry point -> stays on fallback.
    clock.t += ds.RETRY_PREFERRED_AFTER_S
    with pytest.raises(ds.FeedError):
        feed.fetch(51, -2, 44)
    assert feed.active == "airplanes.live"

    # Preferred recovers -> next retry switches back.
    state["primary_up"] = True
    clock.t += ds.RETRY_PREFERRED_AFTER_S
    res = feed.fetch(51, -2, 44)
    assert res.source == "adsb.lol" and res.switched and feed.active == "adsb.lol"


def test_rate_limit_gap_between_requests():
    clock = FakeClock()
    slept = []

    def sleep(s):
        slept.append(s)
        clock.sleep(s)

    feed = ds.AircraftFeed("airplanes.live", client_for(lambda r: httpx.Response(200, json={"ac": []})),
                           clock=clock, sleep=sleep)
    feed.fetch(0, 0, 10)
    clock.t += 0.25
    feed.fetch(0, 0, 10)
    assert slept == [pytest.approx(0.75)]


def test_bad_payloads_raise_feed_error():
    feed = ds.AircraftFeed("adsb.lol", client_for(lambda r: httpx.Response(200, text="not json")))
    with pytest.raises(ds.FeedError, match="invalid JSON"):
        feed.fetch(0, 0, 10)
    feed = ds.AircraftFeed("adsb.lol", client_for(lambda r: httpx.Response(200, json={"ac": "x"})))
    with pytest.raises(ds.FeedError):
        feed.fetch(0, 0, 10)


def test_timeout_raises_feed_error():
    def handler(req):
        raise httpx.ReadTimeout("slow")
    feed = ds.AircraftFeed("adsb.lol", client_for(handler))
    with pytest.raises(ds.FeedError, match="timed out"):
        feed.fetch(0, 0, 10)
