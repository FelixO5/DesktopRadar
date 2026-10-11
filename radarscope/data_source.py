"""HTTP clients: postcodes.io geocoding and adsb.lol / airplanes.live aircraft feeds.

Pure ``httpx``; no Qt. Blocking calls — run them on a worker thread.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from . import __version__, geo

log = logging.getLogger(__name__)

USER_AGENT = f"RadarScope/{__version__} (personal hobby widget)"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

POSTCODES_IO = "https://api.postcodes.io"
FEED_BASE_URLS = {
    "adsb.lol": "https://api.adsb.lol",
    "airplanes.live": "https://api.airplanes.live",
}
#: Minimum seconds between requests to each source (airplanes.live soft limit ~1 req/s).
MIN_REQUEST_GAP_S = {"adsb.lol": 1.0, "airplanes.live": 1.0}

#: Consecutive failures on the active source before failing over.
FAILOVER_AFTER = 3
#: While on the fallback, retry the preferred source this often (s).
RETRY_PREFERRED_AFTER_S = 300.0


class GeocodeError(Exception):
    """Postcode could not be validated/geocoded. ``str(exc)`` is user-friendly."""


class FeedError(Exception):
    """Aircraft feed request failed."""


def make_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}, follow_redirects=True)


def normalise_postcode(text: str) -> str:
    """Upper-case and tidy spacing: ' gl142ab ' -> 'GL14 2AB'."""
    compact = "".join(text.split()).upper()
    if len(compact) > 3:
        return f"{compact[:-3]} {compact[-3:]}"
    return compact


@dataclass
class GeocodeResult:
    postcode: str
    latitude: float
    longitude: float


def geocode_postcode(postcode: str, client: httpx.Client | None = None) -> GeocodeResult:
    """Validate then geocode a UK postcode via postcodes.io."""
    pc = normalise_postcode(postcode)
    if not pc:
        raise GeocodeError("Please enter a postcode.")
    own = client is None
    client = client or make_client()
    try:
        path = quote(pc, safe="")
        try:
            resp = client.get(f"{POSTCODES_IO}/postcodes/{path}/validate")
            resp.raise_for_status()
            if resp.json().get("result") is not True:
                raise GeocodeError(f"“{pc}” isn't a recognised UK postcode.")
            resp = client.get(f"{POSTCODES_IO}/postcodes/{path}")
            if resp.status_code == 404:
                raise GeocodeError(f"“{pc}” has no location data (terminated postcode?).")
            resp.raise_for_status()
            result = resp.json().get("result") or {}
        except httpx.HTTPError as exc:
            raise GeocodeError(f"Couldn't reach postcodes.io ({exc.__class__.__name__}). "
                               "Check your connection and try again.") from exc
        except ValueError as exc:
            raise GeocodeError("postcodes.io returned an unexpected response.") from exc
        lat, lon = result.get("latitude"), result.get("longitude")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            raise GeocodeError(f"“{pc}” has no coordinates (e.g. a non-geographic postcode).")
        return GeocodeResult(result.get("postcode") or pc, float(lat), float(lon))
    finally:
        if own:
            client.close()


def feed_url(source: str, lat: float, lon: float, radius_nm: float) -> str:
    """``/v2/point/{lat}/{lon}/{radius}`` — radius in **nm**, clamped to the 250 nm cap."""
    radius = int(min(geo.API_MAX_RADIUS_NM, max(1, round(radius_nm))))
    return f"{FEED_BASE_URLS[source]}/v2/point/{lat:.4f}/{lon:.4f}/{radius}"


@dataclass
class FeedResult:
    aircraft: list[dict[str, Any]]
    source: str
    #: True if this result came from a different source than the previous one.
    switched: bool = False


class AircraftFeed:
    """Fetches the aircraft list, failing over between sources on repeated errors.

    Thread-safe enough for one worker thread calling :meth:`fetch` while the GUI
    thread calls :meth:`set_preferred`.
    """

    def __init__(self, preferred: str = "adsb.lol", client: httpx.Client | None = None,
                 clock=time.monotonic, sleep=time.sleep) -> None:
        if preferred not in FEED_BASE_URLS:
            raise ValueError(f"unknown data source {preferred!r}")
        self._lock = threading.Lock()
        self._client = client or make_client()
        self._clock = clock
        self._sleep = sleep
        self.preferred = preferred
        self.active = preferred
        self.consecutive_failures = 0
        self._failed_over_at: float | None = None
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def set_preferred(self, source: str) -> None:
        if source not in FEED_BASE_URLS:
            raise ValueError(f"unknown data source {source!r}")
        with self._lock:
            self.preferred = self.active = source
            self.consecutive_failures = 0
            self._failed_over_at = None

    def _other(self, source: str) -> str:
        return next(s for s in FEED_BASE_URLS if s != source)

    def _choose_source(self) -> str:
        with self._lock:
            if (self.active != self.preferred and self._failed_over_at is not None
                    and self._clock() - self._failed_over_at >= RETRY_PREFERRED_AFTER_S):
                return self.preferred  # periodic attempt to go back to the preferred source
            return self.active

    def _request(self, source: str, lat: float, lon: float, radius_nm: float) -> list[dict[str, Any]]:
        gap = MIN_REQUEST_GAP_S.get(source, 0.0)
        last = self._last_request.get(source)
        if last is not None:
            wait = gap - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_request[source] = self._clock()
        url = feed_url(source, lat, lon, radius_nm)
        try:
            resp = self._client.get(url)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            raise FeedError(f"{source}: HTTP {exc.response.status_code}") from exc
        except httpx.TimeoutException as exc:
            raise FeedError(f"{source}: timed out") from exc
        except httpx.HTTPError as exc:
            raise FeedError(f"{source}: {exc.__class__.__name__}") from exc
        except ValueError as exc:
            raise FeedError(f"{source}: invalid JSON") from exc
        if not isinstance(data, dict):
            raise FeedError(f"{source}: unexpected response")
        ac = data.get("ac")
        if ac is None:
            ac = []  # some servers omit "ac" when nothing is in range
        if not isinstance(ac, list):
            raise FeedError(f"{source}: unexpected response")
        return ac

    def fetch(self, lat: float, lon: float, radius_nm: float) -> FeedResult:
        """One request. Raises :class:`FeedError` on failure (after failover bookkeeping)."""
        source = self._choose_source()
        try:
            aircraft = self._request(source, lat, lon, radius_nm)
        except FeedError:
            with self._lock:
                if source != self.active:
                    # A retry of the preferred source failed; stay on the fallback a while longer.
                    self._failed_over_at = self._clock()
                else:
                    self.consecutive_failures += 1
                    if self.consecutive_failures >= FAILOVER_AFTER:
                        old, self.active = self.active, self._other(self.active)
                        self.consecutive_failures = 0
                        self._failed_over_at = self._clock() if self.active != self.preferred else None
                        log.warning("%s failing repeatedly; switching to %s", old, self.active)
            raise
        with self._lock:
            switched = source != self.active
            self.active = source
            self.consecutive_failures = 0
            if source == self.preferred:
                self._failed_over_at = None
        if switched:
            log.info("Back on preferred source %s", source)
        return FeedResult(aircraft, source, switched)
