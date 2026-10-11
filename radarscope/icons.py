"""Top-down aircraft silhouettes as vector QPainterPaths, one per category.

Each path is drawn in unit coordinates: nose at (0, -1) pointing up (North),
tail near (0, +1), so the painter can scale it to any size and rotate it to the
aircraft's track. Paths are built once and cached.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainterPath, QPolygonF, QTransform

from .aircraft_types import Category

#: Relative on-screen size of each category (1.0 = a typical airliner).
SCALE = {
    Category.LIGHT: 0.75, Category.TURBOPROP: 0.9, Category.BIZJET: 0.85,
    Category.NARROWBODY: 1.0, Category.WIDEBODY: 1.2, Category.HEAVY: 1.35,
    Category.MIL_TRANSPORT: 1.25, Category.FIGHTER: 0.9, Category.HELICOPTER: 0.85,
    Category.GLIDER: 0.9, Category.BALLOON: 0.7, Category.DRONE: 0.6, Category.UNKNOWN: 0.9,
}


def _mirrored(points: list[tuple[float, float]]) -> QPainterPath:
    """Closed polygon from right-hand points (x >= 0) plus their mirror image."""
    right = [QPointF(x, y) for x, y in points]
    left = [QPointF(-x, y) for x, y in reversed(points)]
    path = QPainterPath()
    path.addPolygon(QPolygonF(right + left))
    path.closeSubpath()
    return path


def _pair(points: list[tuple[float, float]]) -> QPainterPath:
    """A shape on the right (x > 0) and its mirror on the left, e.g. a wing."""
    path = QPainterPath()
    path.addPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
    path.closeSubpath()
    path.addPolygon(QPolygonF([QPointF(-x, y) for x, y in points]))
    path.closeSubpath()
    return path


def _ellipses(centres: list[tuple[float, float]], w: float, h: float) -> QPainterPath:
    path = QPainterPath()
    for x, y in centres:
        for sx in (x, -x):
            path.addEllipse(QRectF(sx - w / 2, y - h / 2, w, h))
    return path


#: Boolean path ops flatten curves at a fixed absolute tolerance, so unit-sized
#: shapes would come out as octagons; do the maths at 100x and scale back.
_UP, _DOWN = QTransform.fromScale(100, 100), QTransform.fromScale(0.01, 0.01)


def _union(*parts: QPainterPath) -> QPainterPath:
    out = QPainterPath()
    for part in parts:
        out = out.united(_UP.map(part))
    return _DOWN.map(out.simplified())


def _ring(centre: QPointF, outer: float, inner: float) -> QPainterPath:
    a, b = QPainterPath(), QPainterPath()
    a.addEllipse(centre, outer, outer)
    b.addEllipse(centre, inner, inner)
    return _DOWN.map(_UP.map(a).subtracted(_UP.map(b)))


def _fuselage(half_width: float, top: float = -1.0, bottom: float = 0.95) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(-half_width, top, 2 * half_width, bottom - top), half_width, half_width * 1.6)
    return path


def _swept_jet(body: float, span: float, engines: list[tuple[float, float]], engine_size: tuple[float, float]) -> QPainterPath:
    wing = _pair([(body * 0.8, -0.2), (span, 0.28), (span, 0.38), (body * 0.8, 0.18)])
    tail = _pair([(body * 0.7, 0.66), (0.38, 0.86), (0.38, 0.94), (body * 0.7, 0.9)])
    return _union(_fuselage(body), wing, tail, _ellipses(engines, *engine_size))


def _build(cat: Category) -> QPainterPath:
    if cat is Category.NARROWBODY:
        return _swept_jet(0.11, 0.95, [(0.4, -0.02)], (0.11, 0.24))
    if cat is Category.WIDEBODY:
        return _swept_jet(0.14, 1.0, [(0.42, 0.0)], (0.15, 0.3))
    if cat is Category.HEAVY:
        return _swept_jet(0.14, 1.0, [(0.34, -0.06), (0.64, 0.1)], (0.12, 0.26))
    if cat is Category.MIL_TRANSPORT:
        wing = _pair([(0.12, -0.25), (1.0, -0.02), (1.0, 0.08), (0.12, -0.02)])
        tail = _pair([(0.06, 0.78), (0.42, 0.86), (0.42, 0.96), (0.06, 0.96)])
        return _union(_fuselage(0.15, -1.0, 0.9), wing, tail,
                      _ellipses([(0.36, -0.28), (0.66, -0.18)], 0.12, 0.26))
    if cat is Category.BIZJET:
        wing = _pair([(0.08, 0.0), (0.72, 0.3), (0.72, 0.38), (0.08, 0.32)])
        tail = _pair([(0.03, 0.82), (0.36, 0.9), (0.36, 0.97), (0.03, 0.97)])
        return _union(_fuselage(0.1, -1.0, 0.92), wing, tail, _ellipses([(0.18, 0.56)], 0.12, 0.28))
    if cat is Category.TURBOPROP:
        wing = _pair([(0.09, -0.15), (1.0, -0.1), (1.0, 0.04), (0.09, 0.1)])
        tail = _pair([(0.06, 0.72), (0.4, 0.78), (0.4, 0.9), (0.06, 0.92)])
        nacelles = _ellipses([(0.36, -0.12)], 0.12, 0.42)
        props = _pair([(0.2, -0.36), (0.52, -0.36), (0.52, -0.31), (0.2, -0.31)])
        return _union(_fuselage(0.1), wing, tail, nacelles, props)
    if cat is Category.LIGHT:
        body = _mirrored([(0.0, -0.98), (0.11, -0.86), (0.1, 0.25), (0.035, 0.95)])
        wing = _pair([(0.06, -0.42), (1.0, -0.4), (1.0, -0.16), (0.06, -0.14)])
        tail = _pair([(0.03, 0.74), (0.36, 0.76), (0.36, 0.92), (0.03, 0.95)])
        prop = _pair([(0.0, -1.0), (0.3, -1.0), (0.3, -0.95), (0.0, -0.95)])
        return _union(body, wing, tail, prop)
    if cat is Category.FIGHTER:
        body = _mirrored([(0.0, -1.0), (0.07, -0.7), (0.11, -0.2), (0.12, 0.95)])
        wing = _pair([(0.1, -0.25), (0.78, 0.45), (0.78, 0.58), (0.1, 0.55)])
        tail = _pair([(0.1, 0.66), (0.42, 0.92), (0.42, 1.0), (0.1, 0.98)])
        return _union(body, wing, tail)
    if cat is Category.GLIDER:
        body = _mirrored([(0.0, -0.9), (0.07, -0.78), (0.05, 0.1), (0.025, 0.95)])
        wing = _pair([(0.04, -0.4), (1.0, -0.34), (1.0, -0.27), (0.04, -0.24)])
        tail = _pair([(0.02, 0.86), (0.26, 0.88), (0.26, 0.95), (0.02, 0.96)])
        return _union(body, wing, tail)
    if cat is Category.HELICOPTER:
        body = QPainterPath()
        body.addEllipse(QRectF(-0.24, -0.62, 0.48, 0.8))
        boom = _mirrored([(0.06, 0.05), (0.035, 0.92), (0.0, 0.95)])
        tail_rotor = _pair([(0.03, 0.8), (0.22, 0.8), (0.22, 0.86), (0.03, 0.86)])
        blades = _mirrored([(0.04, -1.1), (0.04, 0.7), (0.0, 0.72)])
        blades2 = QPainterPath()
        blades2.addRect(QRectF(-0.9, -0.24, 1.8, 0.08))
        return _union(_ring(QPointF(0, -0.2), 0.95, 0.88), body, boom, tail_rotor, blades, blades2)
    if cat is Category.BALLOON:
        envelope = QPainterPath()
        envelope.addEllipse(QPointF(0, -0.15), 0.75, 0.75)
        basket = QPainterPath()
        basket.addRect(QRectF(-0.18, 0.7, 0.36, 0.25))
        return _union(envelope, basket)
    if cat is Category.DRONE:
        arms = QPainterPath()
        arms.addPolygon(QPolygonF([QPointF(-0.75, -0.65), QPointF(-0.65, -0.75), QPointF(0.75, 0.65), QPointF(0.65, 0.75)]))
        arms.addPolygon(QPolygonF([QPointF(0.75, -0.65), QPointF(0.65, -0.75), QPointF(-0.75, 0.65), QPointF(-0.65, 0.75)]))
        rotors = QPainterPath()
        for x in (-0.7, 0.7):
            for y in (-0.7, 0.7):
                rotors.addEllipse(QPointF(x, y), 0.25, 0.25)
        hub = QPainterPath()
        hub.addRect(QRectF(-0.2, -0.3, 0.4, 0.6))
        return _union(arms, rotors, hub)
    # Unknown type: a simple arrowhead, as on the original scope.
    return _mirrored([(0.0, -1.0), (0.62, 0.75), (0.0, 0.4)])


@lru_cache(maxsize=None)
def silhouette(cat: Category) -> QPainterPath:
    """Cached unit-size silhouette for a category."""
    return _build(cat)
