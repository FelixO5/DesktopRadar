"""Background polling on a worker thread, plus a one-shot task runner for geocoding.

Network I/O never runs on the GUI thread. Workers are daemon threads that emit
Qt signals; because the emitting thread differs from the receiver's, Qt queues
the calls onto the GUI thread. Daemon threads (rather than QThread) mean a
request stuck in a timeout can never block or crash application exit.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

from .data_source import AircraftFeed, FeedError

log = logging.getLogger(__name__)

#: Cap for exponential backoff after failures (s).
MAX_BACKOFF_S = 120.0


@dataclass
class PollParams:
    lat: float
    lon: float
    radius_nm: float
    interval_s: float
    source: str


def backoff_delay(interval_s: float, failures: int) -> float:
    """Delay before the next poll: the interval when healthy, doubling per failure, capped."""
    if failures <= 0:
        return interval_s
    return min(MAX_BACKOFF_S, interval_s * 2 ** (failures - 1))


class Poller(QObject):
    """Polls the aircraft feed every ``interval_s`` on a background thread."""

    #: (aircraft list, monotonic receive time, source name)
    aircraft_received = Signal(object, float, str)
    #: (error message, consecutive failures, seconds until retry)
    fetch_failed = Signal(str, int, float)

    def __init__(self, params: PollParams, feed: AircraftFeed | None = None, parent=None) -> None:
        super().__init__(parent)
        self._params = params
        self._params_lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._feed = feed or AircraftFeed(params.source)
        self._failures = 0
        self._thread: threading.Thread | None = None

    # ---- called from the GUI thread ----------------------------------------

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="radarscope-poller", daemon=True)
            self._thread.start()

    def isRunning(self) -> bool:  # noqa: N802 - mirrors the QThread API
        return self._thread is not None and self._thread.is_alive()

    def update_params(self, params: PollParams, refresh: bool = True) -> None:
        """Apply new location/range/interval/source; poll promptly if ``refresh``."""
        with self._params_lock:
            source_changed = params.source != self._params.source
            self._params = params
            self._failures = 0
        if source_changed:
            self._feed.set_preferred(params.source)
        if refresh:
            self.refresh_now()

    def refresh_now(self) -> None:
        self._wake.set()

    def stop(self, timeout_s: float = 2.0) -> None:
        """Ask the worker to finish; a request stuck in a timeout is abandoned (daemon)."""
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
            if self._thread.is_alive():
                log.info("Poller still waiting on the network; abandoning it")

    @property
    def active_source(self) -> str:
        return self._feed.active

    # ---- worker thread ------------------------------------------------------

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                self._wake.clear()
                with self._params_lock:
                    p = self._params
                started = time.monotonic()
                try:
                    result = self._feed.fetch(p.lat, p.lon, p.radius_nm)
                except FeedError as exc:
                    delay = self._failed(p, str(exc))
                except Exception as exc:  # never let the worker die silently
                    log.exception("Unexpected poll error")
                    delay = self._failed(p, f"error: {exc}")
                else:
                    with self._params_lock:
                        self._failures = 0
                    delay = p.interval_s
                    if not self._stop.is_set():
                        self.aircraft_received.emit(result.aircraft, time.monotonic(), result.source)
                # Never poll faster than the interval: measure from the request start.
                remaining = max(0.0, delay - (time.monotonic() - started))
                self._wake.wait(remaining)
        finally:
            self._feed.close()

    def _failed(self, p: PollParams, message: str) -> float:
        with self._params_lock:
            self._failures += 1
            failures = self._failures
        delay = backoff_delay(p.interval_s, failures)
        log.warning("Poll failed (%s); retrying in %.0fs", message, delay)
        if not self._stop.is_set():
            self.fetch_failed.emit(message, failures, delay)
        return delay


class BackgroundTask(QObject):
    """Run one blocking callable on a daemon thread and report the outcome."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any], parent=None) -> None:
        super().__init__(parent)
        self._fn = fn
        self._thread = threading.Thread(target=self._run, name="radarscope-task", daemon=True)
        self.cancelled = False

    def start(self) -> None:
        self._thread.start()

    def isRunning(self) -> bool:  # noqa: N802
        return self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # reported to the GUI as text
            if not self.cancelled:
                self.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            if not self.cancelled:
                self.succeeded.emit(result)
