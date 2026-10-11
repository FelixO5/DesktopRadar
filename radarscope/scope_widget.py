"""The radar scope: rings, compass, sweep, aircraft blips, labels and detail panel.

Pure rendering + hit-testing. Window move/resize lives in ``main.RadarWindow``.
All animation timing uses ``time.monotonic()``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QConicalGradient, QFont, QFontDatabase, QFontMetricsF, QPainter,
    QPainterPath, QPen, QPolygonF, QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import geo
from .model import Aircraft, AircraftStore

SWEEP_PERIOD_S = 6.0
SWEEP_TRAIL_DEG = 40.0
FRAME_MS = 16  # ~60 fps
#: Seconds for a blip's "painted by the sweep" glow to decay to ~1/e.
SWEEP_GLOW_DECAY_S = 1.2

PHOSPHOR = QColor(60, 255, 120)
COL_BG = QColor(4, 10, 6, 235)
COL_LOW = QColor(255, 176, 0)       # < 10,000 ft: amber
COL_MID = QColor(60, 255, 120)      # 10,000-30,000 ft: green
COL_HIGH = QColor(0, 220, 255)      # > 30,000 ft: cyan
COL_GROUND = QColor(120, 150, 120)
COL_UNKNOWN = QColor(180, 180, 180)
COL_EMERGENCY = QColor(255, 60, 60)
COL_STALE = QColor(255, 176, 0)


def altitude_colour(ac: Aircraft) -> QColor:
    if ac.emergency:
        return QColor(COL_EMERGENCY)
    if ac.on_ground:
        return QColor(COL_GROUND)
    if ac.altitude_ft is None:
        return QColor(COL_UNKNOWN)
    if ac.altitude_ft < 10_000:
        return QColor(COL_LOW)
    if ac.altitude_ft < 30_000:
        return QColor(COL_MID)
    return QColor(COL_HIGH)


def format_range_label(miles: float) -> str:
    """Ring label in statute miles, e.g. ``25mi`` or ``12.5mi``."""
    if abs(miles - round(miles)) < 0.05 or miles >= 100:
        return f"{round(miles)}mi"
    return f"{miles:.1f}mi"


def with_alpha(colour: QColor, alpha: float) -> QColor:
    c = QColor(colour)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


@dataclass
class PaintStats:
    """Counts from the most recent paint (useful for tests and the status line)."""
    on_scope: int = 0
    clipped: int = 0


@dataclass
class _Target:
    hex: str
    pos: QPointF
    range_nm: float
    bearing: float


class ScopeWidget(QWidget):
    #: Emitted when the user selects (clicks) a blip; "" when cleared.
    selection_changed = Signal(str)

    def __init__(self, store: AircraftStore, parent: QWidget | None = None, clock=time.monotonic) -> None:
        super().__init__(parent)
        self.store = store
        self._clock = clock
        self._t0 = clock()
        self.home: tuple[float, float] | None = None
        self.scope_range_mi: float = 50
        self.source_name = ""
        self.stale = False
        self.stale_message = ""
        self.last_success: float | None = None
        self.hover_hex: str | None = None
        self.selected_hex: str | None = None
        self.last_paint_stats = PaintStats()
        self._targets: list[_Target] = []

        self.setMouseTracking(True)
        self.setMinimumSize(160, 160)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)

        self._fixed_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._fixed_font.setStyleHint(QFont.StyleHint.Monospace)

        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._tick)
        self._last_expire = 0.0

    # ---- public API ---------------------------------------------------------

    def start_animation(self) -> None:
        self._timer.start(FRAME_MS)

    def stop_animation(self) -> None:
        self._timer.stop()

    def set_home(self, lat: float, lon: float) -> None:
        self.home = (lat, lon)
        self.update()

    def set_range_miles(self, miles: float) -> None:
        self.scope_range_mi = max(geo.MIN_SCOPE_RANGE_MI, min(geo.MAX_SCOPE_RANGE_MI, miles))
        self.update()

    @property
    def scope_range_nm(self) -> float:
        return geo.miles_to_nm(self.scope_range_mi)

    def mark_success(self, source: str) -> None:
        self.source_name = source
        self.stale = False
        self.stale_message = ""
        self.last_success = self._clock()
        self.update()

    def mark_failure(self, message: str) -> None:
        self.stale = True
        self.stale_message = message
        self.update()

    def sweep_angle(self, now: float | None = None) -> float:
        """Current sweep bearing in degrees (clockwise from North)."""
        now = self._clock() if now is None else now
        return ((now - self._t0) / SWEEP_PERIOD_S * 360.0) % 360.0

    def blip_at(self, pos: QPointF) -> str | None:
        """Hex of the blip nearest ``pos`` within the hit radius, else None."""
        best, best_d = None, self._hit_radius() ** 2
        for t in self._targets:
            d = (t.pos.x() - pos.x()) ** 2 + (t.pos.y() - pos.y()) ** 2
            if d <= best_d:
                best, best_d = t.hex, d
        return best

    # ---- geometry helpers ---------------------------------------------------

    def _geometry(self) -> tuple[float, float, float, float]:
        """(cx, cy, r_max_px, side)."""
        w, h = self.width(), self.height()
        side = min(w, h)
        margin = max(8.0, side * 0.035)
        return w / 2.0, h / 2.0, side / 2.0 - margin, side

    def _blip_size(self) -> float:
        return max(4.5, self._geometry()[3] * 0.018)

    def _hit_radius(self) -> float:
        return max(10.0, self._blip_size() * 2.2)

    def _font(self, scale: float) -> QFont:
        f = QFont(self._fixed_font)
        f.setPixelSize(max(9, int(self._geometry()[3] * scale)))
        return f

    # ---- animation ----------------------------------------------------------

    def _tick(self) -> None:
        now = self._clock()
        if now - self._last_expire > 0.5:
            self._last_expire = now
            removed = self.store.expire(now)
            if self.selected_hex in removed:
                self.selected_hex = None
            if self.hover_hex in removed:
                self.hover_hex = None
        self.update()

    # ---- painting -----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        now = self._clock()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        cx, cy, R, side = self._geometry()
        centre = QPointF(cx, cy)

        self._paint_background(p, cx, cy, R)
        self._paint_rings(p, cx, cy, R)
        self._paint_compass(p, cx, cy, R)
        sweep = self.sweep_angle(now)
        self._paint_sweep(p, centre, R, sweep)
        stats = self._paint_aircraft(p, now, cx, cy, R, sweep)
        self._paint_status(p, now)
        self._paint_detail(p, now)
        p.end()
        self.last_paint_stats = stats

    def _paint_background(self, p: QPainter, cx: float, cy: float, R: float) -> None:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(COL_BG)
        radius = max(10.0, min(self.width(), self.height()) * 0.08)
        p.drawRoundedRect(QRectF(self.rect()), radius, radius)
        grad = QRadialGradient(QPointF(cx, cy), R)
        grad.setColorAt(0.0, QColor(10, 42, 22))
        grad.setColorAt(0.85, QColor(4, 20, 10))
        grad.setColorAt(1.0, QColor(2, 10, 5))
        p.setBrush(QBrush(grad))
        p.drawEllipse(QPointF(cx, cy), R, R)

    def _paint_rings(self, p: QPainter, cx: float, cy: float, R: float) -> None:
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setFont(self._font(0.028))
        fm = QFontMetricsF(p.font())
        for i, frac in enumerate((0.25, 0.5, 0.75, 1.0)):
            r = R * frac
            p.setPen(QPen(with_alpha(PHOSPHOR, 0.55 if frac == 1.0 else 0.32), 1.2 if frac == 1.0 else 1.0))
            p.drawEllipse(QPointF(cx, cy), r, r)
            label = format_range_label(self.scope_range_mi * frac)
            # Label on the ring at ~bearing 22.5 deg, clear of the N tick and crosshair.
            a = math.radians(22.5)
            lx, ly = cx + r * math.sin(a) + 2, cy - r * math.cos(a) + fm.ascent() * 0.5
            if frac == 1.0:
                ly += fm.ascent() * 0.6
            p.setPen(with_alpha(PHOSPHOR, 0.6))
            p.drawText(QPointF(lx, ly), label)
        # faint cross-hairs
        p.setPen(QPen(with_alpha(PHOSPHOR, 0.16), 1.0))
        p.drawLine(QPointF(cx - R, cy), QPointF(cx + R, cy))
        p.drawLine(QPointF(cx, cy - R), QPointF(cx, cy + R))

    def _paint_compass(self, p: QPainter, cx: float, cy: float, R: float) -> None:
        for deg in range(0, 360, 10):
            major = deg % 30 == 0
            length = R * (0.05 if major else 0.025)
            a = math.radians(deg)
            s, c = math.sin(a), math.cos(a)
            p.setPen(QPen(with_alpha(PHOSPHOR, 0.6 if major else 0.35), 1.2 if major else 1.0))
            p.drawLine(QPointF(cx + R * s, cy - R * c), QPointF(cx + (R - length) * s, cy - (R - length) * c))
        font = self._font(0.042)
        font.setBold(True)
        p.setFont(font)
        fm = QFontMetricsF(font)
        inset = R * 0.05 + fm.height() * 0.6
        for deg, letter in ((0, "N"), (90, "E"), (180, "S"), (270, "W")):
            a = math.radians(deg)
            x, y = cx + (R - inset) * math.sin(a), cy - (R - inset) * math.cos(a)
            w = fm.horizontalAdvance(letter)
            p.setPen(with_alpha(PHOSPHOR, 0.85 if letter == "N" else 0.6))
            p.drawText(QPointF(x - w / 2, y + fm.ascent() / 2 - 1), letter)

    def _paint_sweep(self, p: QPainter, centre: QPointF, R: float, sweep: float) -> None:
        # Qt angles: 0 = 3 o'clock, counter-clockwise. Compass bearing b -> 90 - b.
        qt_angle = (90.0 - sweep) % 360.0
        grad = QConicalGradient(centre, qt_angle)
        # The trail lies counter-clockwise of the line (the sweep turns clockwise).
        grad.setColorAt(0.0, with_alpha(PHOSPHOR, 0.42))
        grad.setColorAt(SWEEP_TRAIL_DEG / 360.0 * 0.35, with_alpha(PHOSPHOR, 0.16))
        grad.setColorAt(SWEEP_TRAIL_DEG / 360.0, with_alpha(PHOSPHOR, 0.0))
        grad.setColorAt(0.999, with_alpha(PHOSPHOR, 0.0))
        grad.setColorAt(1.0, with_alpha(PHOSPHOR, 0.42))
        path = QPainterPath()
        path.moveTo(centre)
        rect = QRectF(centre.x() - R, centre.y() - R, 2 * R, 2 * R)
        path.arcTo(rect, qt_angle, SWEEP_TRAIL_DEG)
        path.closeSubpath()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(grad))
        p.drawPath(path)
        a = math.radians(sweep)
        tip = QPointF(centre.x() + R * math.sin(a), centre.y() - R * math.cos(a))
        p.setPen(QPen(with_alpha(PHOSPHOR, 0.9), 1.6))
        p.drawLine(centre, tip)

    def _paint_aircraft(self, p: QPainter, now: float, cx: float, cy: float, R: float,
                        sweep: float) -> PaintStats:
        stats = PaintStats()
        targets: list[_Target] = []
        if self.home is None:
            self._targets = targets
            return stats
        home_lat, home_lon = self.home
        scope_nm = self.scope_range_nm
        s = self._blip_size()
        label_font = self._font(0.03)
        fm = QFontMetricsF(label_font)
        flash = 0.5 + 0.5 * math.sin(now * math.tau * 1.5)

        # Draw emergencies last so they sit on top.
        ordered = sorted(self.store, key=lambda a: (a.emergency is not None, a.hex == self.selected_hex))
        for ac in ordered:
            lat, lon = ac.position_at(now)
            rng, brg = geo.range_and_bearing(home_lat, home_lon, lat, lon)
            x, y, in_range = geo.polar_to_screen(rng, brg, scope_nm, cx, cy, R, clip=True)
            alpha = ac.opacity(now)
            if alpha <= 0.01:
                continue
            colour = altitude_colour(ac)
            pos = QPointF(x, y)
            targets.append(_Target(ac.hex, pos, rng, brg))

            if not in_range:
                # Clipped to the rim: a small outward chevron on the true bearing.
                stats.clipped += 1
                a = math.radians(brg)
                ux, uy = math.sin(a), -math.cos(a)
                px, py = -uy, ux
                tip = QPointF(x + ux * s * 0.9, y + uy * s * 0.9)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(with_alpha(colour, alpha * 0.55))
                p.drawPolygon(QPolygonF([tip, QPointF(x + px * s * 0.6, y + py * s * 0.6),
                                         QPointF(x - px * s * 0.6, y - py * s * 0.6)]))
                continue

            stats.on_scope += 1
            # Sweep "paint": brightest just after the sweep passes, then decays.
            since = ((sweep - brg) % 360.0) / 360.0 * SWEEP_PERIOD_S
            glow = math.exp(-since / SWEEP_GLOW_DECAY_S)
            body_alpha = alpha * (0.7 + 0.3 * glow)
            if glow > 0.05:
                halo = QRadialGradient(pos, s * 2.4)
                halo.setColorAt(0.0, with_alpha(colour, 0.55 * glow * alpha))
                halo.setColorAt(1.0, with_alpha(colour, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(halo))
                p.drawEllipse(pos, s * 2.4, s * 2.4)

            if ac.emergency:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(with_alpha(COL_EMERGENCY, (0.35 + 0.65 * flash) * alpha), 2.0))
                p.drawEllipse(pos, s * 2.3, s * 2.3)

            if ac.hex in (self.selected_hex, self.hover_hex):
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(with_alpha(QColor(255, 255, 255), 0.7 * alpha), 1.0, Qt.PenStyle.DashLine))
                p.drawEllipse(pos, s * 1.9, s * 1.9)

            p.setPen(QPen(with_alpha(colour, body_alpha), 1.0))
            p.setBrush(with_alpha(colour, body_alpha * (0.45 if ac.on_ground else 1.0)))
            p.save()
            p.translate(pos)
            if ac.track_deg is not None:
                p.rotate(ac.track_deg)  # QPainter rotates clockwise; 0 = nose up = North
                p.drawPolygon(QPolygonF([QPointF(0, -s), QPointF(s * 0.62, s * 0.75),
                                         QPointF(0, s * 0.4), QPointF(-s * 0.62, s * 0.75)]))
            else:
                p.drawRect(QRectF(-s * 0.5, -s * 0.5, s, s))
            p.restore()

            # Label: callsign / FLxxx, offset to the side away from the scope edge.
            line1, line2 = ac.label, ac.flight_level
            text_w = max(fm.horizontalAdvance(line1), fm.horizontalAdvance(line2))
            off = s * 1.4
            lx = x + off if x + off + text_w < cx + R + s * 2 else x - off - text_w
            ly = y - fm.height() * 0.15
            p.setFont(label_font)
            p.setPen(with_alpha(colour, alpha * 0.95))
            p.drawText(QPointF(lx, ly), line1)
            p.setPen(with_alpha(colour, alpha * 0.7))
            p.drawText(QPointF(lx, ly + fm.height() * 0.95), line2)

        self._targets = targets
        return stats

    def _paint_status(self, p: QPainter, now: float) -> None:
        font = self._font(0.03)
        p.setFont(font)
        fm = QFontMetricsF(font)
        pad = max(6.0, min(self.width(), self.height()) * 0.03)
        if self.home is None:
            msg = "No location — right-click › Settings"
            p.setPen(with_alpha(COL_STALE, 0.9))
            p.drawText(QRectF(self.rect()), Qt.AlignmentFlag.AlignCenter, msg)
            return
        if self.stale:
            age = f" {now - self.last_success:.0f}s" if self.last_success is not None else ""
            p.setPen(with_alpha(COL_STALE, 0.6 + 0.4 * (0.5 + 0.5 * math.sin(now * math.tau))))
            p.drawText(QPointF(pad, pad + fm.ascent()), f"STALE{age}")
        elif self.store.last_update is None:
            p.setPen(with_alpha(PHOSPHOR, 0.7))
            p.drawText(QPointF(pad, pad + fm.ascent()), "ACQUIRING…")
        footer = f"{self.source_name or '—'} · {len(self.store)}"
        p.setPen(with_alpha(PHOSPHOR, 0.45))
        p.drawText(QPointF(self.width() - pad - fm.horizontalAdvance(footer),
                           self.height() - pad - fm.descent()), footer)

    def detail_lines(self, ac: Aircraft, now: float) -> list[str]:
        """Text for the detail panel (distance in **miles**)."""
        lines = [f"{ac.label}  {ac.hex.upper()}"]
        kind = " ".join(x for x in (ac.type_code, ac.description) if x)
        if kind:
            lines.append(kind)
        if ac.registration:
            lines.append(f"REG  {ac.registration}" + ("  MIL" if ac.military else ""))
        elif ac.military:
            lines.append("MIL")
        trend = {1: " ↑", -1: " ↓", 0: ""}[ac.vertical_trend]
        if ac.on_ground:
            alt = "GND"
        elif ac.altitude_ft is None:
            alt = "—"
        else:
            alt = f"{ac.altitude_ft:,} ft{trend}"
        lines.append(f"ALT  {alt}")
        gs = f"{ac.gs_knots:.0f} kt" if ac.gs_knots is not None else "—"
        trk = f"{ac.track_deg:03.0f}°" if ac.track_deg is not None else "—"
        lines.append(f"GS   {gs}  TRK {trk}")
        sq = ac.squawk or "—"
        if ac.emergency:
            sq += f" {ac.emergency}"
        lines.append(f"SQK  {sq}")
        if self.home is not None:
            lat, lon = ac.position_at(now)
            rng, brg = geo.range_and_bearing(*self.home, lat, lon)
            lines.append(f"DIST {geo.nm_to_miles(rng):.1f} mi")
            lines.append(f"BRG  {brg:03.0f}° {geo.compass_point(brg)}")
        return lines

    def _paint_detail(self, p: QPainter, now: float) -> None:
        hex_id = self.hover_hex or self.selected_hex
        ac = self.store.get(hex_id) if hex_id else None
        target = next((t for t in self._targets if t.hex == hex_id), None)
        if ac is None or target is None:
            return
        lines = self.detail_lines(ac, now)
        font = self._font(0.032)
        p.setFont(font)
        fm = QFontMetricsF(font)
        pad = 6.0
        w = max(fm.horizontalAdvance(line) for line in lines) + 2 * pad
        h = fm.height() * len(lines) + 2 * pad
        max_w = self.width() - 8
        if w > max_w:
            w = max_w
        x, y = target.pos.x() + 14, target.pos.y() + 14
        if x + w > self.width() - 4:
            x = target.pos.x() - 14 - w
        if y + h > self.height() - 4:
            y = target.pos.y() - 14 - h
        x = max(4.0, min(x, self.width() - 4 - w))
        y = max(4.0, min(y, self.height() - 4 - h))
        box = QRectF(x, y, w, h)
        colour = altitude_colour(ac)
        p.setPen(QPen(with_alpha(colour, 0.85), 1.0))
        p.setBrush(QColor(0, 16, 6, 225))
        p.drawRoundedRect(box, 4, 4)
        for i, line in enumerate(lines):
            p.setPen(with_alpha(colour if i == 0 else PHOSPHOR, 1.0 if i == 0 else 0.85))
            text = fm.elidedText(line, Qt.TextElideMode.ElideRight, w - 2 * pad)
            p.drawText(QPointF(x + pad, y + pad + fm.ascent() + i * fm.height()), text)

    # ---- interaction --------------------------------------------------------

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        hit = self.blip_at(event.position())
        if hit != self.hover_hex:
            self.hover_hex = hit
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        if self.hover_hex is not None:
            self.hover_hex = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            hit = self.blip_at(event.position())
            new = None if hit == self.selected_hex else hit
            if new != self.selected_hex:
                self.selected_hex = new
                self.selection_changed.emit(new or "")
                self.update()
            event.accept()
            return
        super().mousePressEvent(event)
