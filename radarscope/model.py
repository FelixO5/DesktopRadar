"""Aircraft model: parsing feed objects, dead-reckoning, ageing and expiry.

No Qt here. All timing uses ``time.monotonic()`` values supplied by the caller,
so the model is immune to wall-clock / NTP / DST changes and easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cached_property
from typing import Any, Iterable

from . import geo
from .aircraft_types import Category, classify
from .airlines import operator_name

EMERGENCY_SQUAWKS = {"7500": "HIJACK", "7600": "RADIO FAIL", "7700": "EMERGENCY"}
#: dbFlags bit for military aircraft (ADSB-Exchange v2 convention).
DBFLAG_MILITARY = 1

#: Drop an aircraft once it has been absent from successful polls this long (s).
EXPIRE_AFTER_S = 60.0
#: Length of the fade-out once expiry starts (s).
FADE_OUT_S = 4.0
#: Never extrapolate a position further than this from its last fix (s).
MAX_DEAD_RECKON_S = 90.0


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@dataclass
class Aircraft:
    hex: str
    lat: float
    lon: float
    #: Monotonic time at which ``lat``/``lon`` were true (poll time - seen_pos).
    fix_time: float
    #: Monotonic time this aircraft last appeared in a poll response.
    last_seen: float
    callsign: str = ""
    registration: str = ""
    type_code: str = ""
    description: str = ""
    altitude_ft: int | None = None
    on_ground: bool = False
    gs_knots: float | None = None
    track_deg: float | None = None
    baro_rate: float | None = None
    squawk: str = ""
    db_flags: int = 0
    #: ADS-B emitter category, e.g. "A3" (large aircraft), "A7" (rotorcraft).
    emitter_category: str = ""
    #: Set when the aircraft has started fading out.
    expiring_since: float | None = None

    @classmethod
    def from_feed(cls, obj: dict[str, Any], received_at: float) -> "Aircraft | None":
        """Build an Aircraft from one ``ac[]`` object, or ``None`` if it has no position."""
        hex_id = _text(obj.get("hex")).lower()
        lat, lon = _num(obj.get("lat")), _num(obj.get("lon"))
        if not hex_id or lat is None or lon is None:
            return None
        seen_pos = _num(obj.get("seen_pos")) or 0.0
        alt = obj.get("alt_baro")
        on_ground = isinstance(alt, str) and alt.strip().lower() == "ground"
        alt_num = _num(alt)
        altitude = 0 if on_ground else (int(round(alt_num)) if alt_num is not None else None)
        flags = obj.get("dbFlags")
        return cls(
            hex=hex_id,
            lat=lat,
            lon=lon,
            fix_time=received_at - max(0.0, seen_pos),
            last_seen=received_at,
            callsign=_text(obj.get("flight")),
            registration=_text(obj.get("r")),
            type_code=_text(obj.get("t")),
            description=_text(obj.get("desc")),
            altitude_ft=altitude,
            on_ground=on_ground,
            gs_knots=_num(obj.get("gs")),
            track_deg=_num(obj.get("track")),
            baro_rate=_num(obj.get("baro_rate")) if obj.get("baro_rate") is not None else _num(obj.get("geom_rate")),
            squawk=_text(obj.get("squawk")),
            db_flags=flags if isinstance(flags, int) and not isinstance(flags, bool) else 0,
            emitter_category=_text(obj.get("category")).upper(),
        )

    # ---- derived properties -------------------------------------------------

    @property
    def label(self) -> str:
        """Best short identifier: callsign, else registration, else hex."""
        return self.callsign or self.registration or self.hex.upper()

    @property
    def flight_level(self) -> str:
        """``FLxxx`` (alt/100, zero-padded), ``GND`` on the ground, ``FL---`` if unknown."""
        if self.on_ground:
            return "GND"
        if self.altitude_ft is None:
            return "FL---"
        return f"FL{max(0, round(self.altitude_ft / 100)):03d}"

    @property
    def emergency(self) -> str | None:
        """Emergency description if squawking 7500/7600/7700, else None."""
        return EMERGENCY_SQUAWKS.get(self.squawk)

    @property
    def military(self) -> bool:
        return bool(self.db_flags & DBFLAG_MILITARY)

    @cached_property
    def category(self) -> Category:
        """Silhouette category (airliner, fighter, helicopter, ...)."""
        return classify(self.type_code, self.emitter_category)

    @cached_property
    def operator(self) -> str | None:
        """Airline / operator name from the callsign (or military address block)."""
        return operator_name(self.callsign, self.hex, self.military)

    @property
    def vertical_trend(self) -> int:
        """+1 climbing, -1 descending, 0 level/unknown (±300 ft/min dead-band)."""
        if self.baro_rate is None:
            return 0
        if self.baro_rate > 300:
            return 1
        if self.baro_rate < -300:
            return -1
        return 0

    # ---- time-dependent -----------------------------------------------------

    def position_at(self, now: float) -> tuple[float, float]:
        """Dead-reckoned (lat, lon) at monotonic time ``now``."""
        if self.on_ground or not self.gs_knots or self.track_deg is None:
            return self.lat, self.lon
        dt = min(max(0.0, now - self.fix_time), MAX_DEAD_RECKON_S)
        return geo.dead_reckon(self.lat, self.lon, self.gs_knots, self.track_deg, dt)

    def position_age(self, now: float) -> float:
        return max(0.0, now - self.fix_time)

    def opacity(self, now: float) -> float:
        """0..1 opacity: dims with position age, then fades out while expiring."""
        age = self.position_age(now)
        # Full brightness for fresh fixes, dimming to 35 % by ~60 s old.
        alpha = 1.0 if age <= 10 else max(0.35, 1.0 - (age - 10) / 50 * 0.65)
        if self.expiring_since is not None:
            alpha *= max(0.0, 1.0 - (now - self.expiring_since) / FADE_OUT_S)
        return alpha


@dataclass
class UpdateResult:
    added: list[str] = field(default_factory=list)
    updated: int = 0
    #: Feed objects ignored because they had no position.
    dropped: int = 0


class AircraftStore:
    """Aircraft keyed by ICAO hex; merges polls and expires stale contacts."""

    def __init__(self) -> None:
        self.aircraft: dict[str, Aircraft] = {}
        #: Monotonic time of the last successful poll merged in (None until first).
        self.last_update: float | None = None

    def __len__(self) -> int:
        return len(self.aircraft)

    def __iter__(self):
        return iter(self.aircraft.values())

    def get(self, hex_id: str) -> Aircraft | None:
        return self.aircraft.get(hex_id)

    def update(self, feed_objects: Iterable[dict[str, Any]], received_at: float) -> UpdateResult:
        """Merge one poll's ``ac[]`` list (snap existing entries to their true fix)."""
        result = UpdateResult()
        for obj in feed_objects:
            if not isinstance(obj, dict):
                result.dropped += 1
                continue
            ac = Aircraft.from_feed(obj, received_at)
            if ac is None:
                result.dropped += 1
                continue
            if ac.hex in self.aircraft:
                result.updated += 1
            else:
                result.added.append(ac.hex)
            self.aircraft[ac.hex] = ac
        self.last_update = received_at
        return result

    def expire(self, now: float) -> list[str]:
        """Start fading contacts missing from polls > EXPIRE_AFTER_S; drop faded ones.

        Absence is measured against the last *successful* poll, so a network
        outage keeps showing the last good data (dimming with age) rather than
        wiping the scope.
        """
        removed: list[str] = []
        if self.last_update is None:
            return removed
        for hex_id, ac in list(self.aircraft.items()):
            if ac.expiring_since is None:
                if self.last_update - ac.last_seen > EXPIRE_AFTER_S:
                    ac.expiring_since = now
            elif now - ac.expiring_since >= FADE_OUT_S:
                del self.aircraft[hex_id]
                removed.append(hex_id)
        return removed

    def clear(self) -> None:
        self.aircraft.clear()
        self.last_update = None
