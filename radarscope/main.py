"""RadarScope entry point: QApplication, frameless window, wiring, ``def main()``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QGuiApplication, QIcon, QKeySequence, QPainter, QPainterPath, QPen, QPixmap,
)
from PySide6.QtWidgets import QApplication, QDialog, QMenu, QMenuBar, QSystemTrayIcon, QVBoxLayout, QWidget

from . import __version__, geo
from .aircraft_types import Category
from .config import MAX_WINDOW_SIZE, MIN_WINDOW_SIZE, Config, config_path, load_config, save_config
from .icons import silhouette
from .model import AircraftStore
from .poller import Poller, PollParams
from .scope_widget import PHOSPHOR, ScopeWidget
from .settings_dialog import SettingsDialog, show_about

log = logging.getLogger("radarscope")

APP_NAME = "RadarScope"
RANGE_PRESETS_MI = (10, 25, 50, 100, 150, 250)
#: Ranges that scroll / pinch / +/- step through (tidy ring labels at every step).
ZOOM_LADDER_MI = (2, 3, 5, 8, 10, 15, 20, 25, 30, 40, 50, 60, 80, 100, 125, 150, 200, 250)
#: Scroll-wheel delta per zoom step (one notch on a mouse wheel).
WHEEL_STEP = 120
#: Accumulated pinch scale per zoom step (trackpad).
PINCH_STEP = 0.12
EDGE_GRAB_PX = 10
SCREEN_MARGIN_PX = 20

IS_LINUX = sys.platform.startswith("linux")
IS_MACOS = sys.platform == "darwin"
IS_WINDOWS = sys.platform.startswith("win")


# ---- platform setup (before QApplication exists) ------------------------------

def configure_platform(environ: dict[str, str] | None = None, platform: str | None = None) -> str | None:
    """Linux/Wayland: prefer XWayland so always-on-top and positioning work.

    Uses Qt's fallback list ``xcb;wayland`` so the app still starts natively on
    Wayland if the xcb plugin can't load (e.g. ``libxcb-cursor0`` missing).
    Never touches macOS/Windows. Returns a note to log, if any.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux") or environ.get("QT_QPA_PLATFORM"):
        return None
    wayland = environ.get("XDG_SESSION_TYPE", "").lower() == "wayland" or bool(environ.get("WAYLAND_DISPLAY"))
    if not wayland:
        return None
    if environ.get("RADARSCOPE_NATIVE_WAYLAND") == "1":
        return ("Running natively on Wayland: always-on-top and window positioning "
                "may be ignored by the compositor.")
    environ["QT_QPA_PLATFORM"] = "xcb;wayland"
    return "Wayland session detected: preferring XWayland (QT_QPA_PLATFORM=xcb;wayland)."


def clamp_to_screens(x: int | None, y: int | None, size: int, screens: list[QRect],
                     primary: QRect) -> QPoint:
    """Keep a saved position reachable on the current monitor layout.

    The window must have at least 60 px visible on some screen's available area;
    otherwise (or when unset) it is placed in the primary screen's top-right corner.
    """
    if x is not None and y is not None:
        rect = QRect(x, y, size, size)
        for s in screens:
            inter = rect.intersected(s)
            if inter.width() >= min(60, size) and inter.height() >= min(60, size):
                # Pull fully inside that screen where possible.
                nx = min(max(x, s.left()), max(s.left(), s.right() + 1 - size))
                ny = min(max(y, s.top()), max(s.top(), s.bottom() + 1 - size))
                return QPoint(nx, ny)
    return QPoint(primary.right() + 1 - size - SCREEN_MARGIN_PX, primary.top() + SCREEN_MARGIN_PX)


def quit_app() -> None:
    """Quit, first closing any open dialog (quit() alone won't end a modal exec())."""
    for w in QApplication.topLevelWidgets():
        if isinstance(w, QDialog) and w.isVisible():
            w.reject()
    QApplication.quit()


def render_icon(size: int = 256) -> QPixmap:
    """The app icon (a small radar scope with a contact), drawn at ``size`` px."""
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(12, 18, 14))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 13, 13)  # macOS-style rounded square
    centre = QPointF(32, 32)
    p.setBrush(QColor(4, 30, 14))
    p.setPen(QPen(PHOSPHOR, 1.6))
    p.drawEllipse(centre, 25, 25)
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QColor(60, 255, 120, 110), 1.0))
    p.drawEllipse(centre, 16.5, 16.5)
    p.drawEllipse(centre, 8, 8)
    p.drawLine(QPointF(7, 32), QPointF(57, 32))
    p.drawLine(QPointF(32, 7), QPointF(32, 57))
    trail = QPainterPath(centre)
    trail.arcTo(QRectF(7, 7, 50, 50), 45, 40)
    trail.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(60, 255, 120, 90))
    p.drawPath(trail)
    p.setPen(QPen(PHOSPHOR, 2.0))
    p.drawLine(centre, QPointF(32 + 17.7, 32 - 17.7))
    p.save()
    p.translate(22, 41)
    p.rotate(45)
    p.scale(6.5, 6.5)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(255, 176, 0))
    p.drawPath(silhouette(Category.NARROWBODY))
    p.restore()
    p.end()
    return pm


def make_icon() -> QIcon:
    return QIcon(render_icon(256))


# ---- the window -----------------------------------------------------------------

class RadarWindow(QWidget):
    """Frameless, draggable, square-resizable, optionally always-on-top scope window."""

    def __init__(self, cfg: Config, cfg_path: Path, start_polling: bool = True) -> None:
        super().__init__()
        self.cfg = cfg
        self.cfg_path = cfg_path
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(make_icon())
        self._fullscreen = False
        self._normal_geometry: QRect | None = None
        self._apply_window_flags()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(MIN_WINDOW_SIZE, MIN_WINDOW_SIZE)

        self.store = AircraftStore()
        self.scope = ScopeWidget(self.store, self)
        self.scope.set_range_miles(cfg.scope_range_mi)
        self.scope.show_operator = cfg.show_airline_names
        if cfg.has_home:
            self.scope.set_home(cfg.home_lat, cfg.home_lon)
        self.scope.installEventFilter(self)
        self.scope.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scope.customContextMenuRequested.connect(self._show_menu)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.scope)

        self._move_origin: tuple[QPointF, QPoint] | None = None
        self._resize_origin: tuple[set[str], QPointF, QRect] | None = None
        self._wheel_accum = 0.0
        self._pinch_accum = 0.0

        # Zooming changes the feed radius; wait until it settles before re-polling.
        self._poll_update_timer = QTimer(self)
        self._poll_update_timer.setSingleShot(True)
        self._poll_update_timer.setInterval(700)
        self._poll_update_timer.timeout.connect(self._push_poll_params)
        self._pushed_radius_nm = geo.api_radius_nm(cfg.scope_range_mi)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.save)

        self._restore_geometry()

        self.poller: Poller | None = None
        if start_polling and cfg.has_home:
            self._start_poller()
        self.scope.start_animation()
        self.tray = self._make_tray()
        self.menu_bar = self._make_menu_bar() if IS_MACOS else None

    # ---- flags & geometry ---------------------------------------------------

    def _apply_window_flags(self) -> None:
        if self._fullscreen:
            # A normal window, so macOS gives it its own full-screen Space.
            self.setWindowFlags(Qt.WindowType.Window)
            return
        flags = Qt.WindowType.FramelessWindowHint
        if IS_WINDOWS:
            # A "widget" feel on Windows: no taskbar button (tray + menu instead).
            flags |= Qt.WindowType.Tool
        else:
            flags |= Qt.WindowType.Window
        if self.cfg.window.always_on_top:
            flags |= Qt.WindowType.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    @property
    def full_screen(self) -> bool:
        return self._fullscreen

    def set_full_screen(self, on: bool) -> None:
        """Switch between the floating widget and full screen (remembered next launch)."""
        if on == self._fullscreen:
            return
        if on:
            self._normal_geometry = self.geometry()
            self._fullscreen = True
            self._apply_window_flags()
            self.showFullScreen()
        else:
            self._fullscreen = False
            self._apply_window_flags()
            self.showNormal()
            if self._normal_geometry is not None:
                self.setGeometry(self._normal_geometry)
        self.cfg.window.fullscreen = on
        self.activateWindow()
        self.save()

    def toggle_full_screen(self) -> None:
        self.set_full_screen(not self._fullscreen)

    def _restore_geometry(self) -> None:
        size = max(MIN_WINDOW_SIZE, min(MAX_WINDOW_SIZE, self.cfg.window.size))
        self.resize(size, size)
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        screens = [s.availableGeometry() for s in QGuiApplication.screens()]
        pos = clamp_to_screens(self.cfg.window.x, self.cfg.window.y, size, screens,
                               screen.availableGeometry())
        self.move(pos)

    def _can_position(self) -> bool:
        # Native Wayland ignores client positioning and reports pos() as 0,0.
        return QGuiApplication.platformName() != "wayland"

    def set_always_on_top(self, on: bool) -> None:
        self.cfg.window.always_on_top = on
        if self._fullscreen:
            self._schedule_save()  # applies when leaving full screen
            return
        pos, visible = self.pos(), self.isVisible()
        self._apply_window_flags()
        self.move(pos)
        if visible:
            self.show()
        self._schedule_save()

    # ---- polling ------------------------------------------------------------

    def _poll_params(self) -> PollParams:
        return PollParams(self.cfg.home_lat, self.cfg.home_lon, geo.api_radius_nm(self.cfg.scope_range_mi),
                          float(self.cfg.poll_seconds), self.cfg.data_source)

    def _start_poller(self) -> None:
        self.poller = Poller(self._poll_params(), parent=self)
        self.poller.aircraft_received.connect(self._on_aircraft)
        self.poller.fetch_failed.connect(self._on_fetch_failed)
        self.poller.start()

    def _on_aircraft(self, aircraft: list, received_at: float, source: str) -> None:
        self.store.update(aircraft, received_at)
        self.scope.mark_success(source)

    def _on_fetch_failed(self, message: str, failures: int, retry_in: float) -> None:
        self.scope.mark_failure(message)
        self.scope.setToolTip(f"Feed error: {message} (retry in {retry_in:.0f}s)")

    def _push_poll_params(self) -> None:
        """Send the (settled) zoom range to the poller; re-poll only if it grew."""
        if self.poller is None or not self.cfg.has_home:
            return
        params = self._poll_params()
        self.poller.update_params(params, refresh=params.radius_nm > self._pushed_radius_nm)
        self._pushed_radius_nm = params.radius_nm

    def _config_changed(self, location_changed: bool) -> None:
        self.scope.set_range_miles(self.cfg.scope_range_mi)
        self.scope.show_operator = self.cfg.show_airline_names
        if location_changed:
            self.store.clear()
            self.scope.selected_hex = self.scope.hover_hex = None
        if self.cfg.has_home:
            self.scope.set_home(self.cfg.home_lat, self.cfg.home_lon)
            if self.poller is None:
                self._start_poller()
            else:
                self.poller.update_params(self._poll_params())
                self._pushed_radius_nm = geo.api_radius_nm(self.cfg.scope_range_mi)
        self.save()

    def set_range(self, miles: int) -> None:
        """Change the scope range (miles); cheap enough to call on every scroll step."""
        miles = max(geo.MIN_SCOPE_RANGE_MI, min(geo.MAX_SCOPE_RANGE_MI, int(miles)))
        if miles == self.cfg.scope_range_mi:
            return
        self.cfg.scope_range_mi = miles
        self.scope.set_range_miles(miles)
        self._poll_update_timer.start()
        self._schedule_save()

    def step_range(self, direction: int) -> None:
        """One step along the zoom ladder: +1 = longer range (zoom out), -1 = zoom in."""
        cur = self.cfg.scope_range_mi
        if direction > 0:
            nxt = next((r for r in ZOOM_LADDER_MI if r > cur), geo.MAX_SCOPE_RANGE_MI)
        else:
            nxt = next((r for r in reversed(ZOOM_LADDER_MI) if r < cur), ZOOM_LADDER_MI[0])
        self.set_range(nxt)

    def zoom_in(self) -> None:
        self.step_range(-1)

    def zoom_out(self) -> None:
        self.step_range(+1)

    def set_show_airline_names(self, on: bool) -> None:
        self.cfg.show_airline_names = on
        self.scope.show_operator = on
        self.scope.update()
        self._schedule_save()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.cfg, self)
        if dlg.exec():
            new = dlg.result_config
            moved = (new.home_lat, new.home_lon) != (self.cfg.home_lat, self.cfg.home_lon)
            new.window = self.cfg.window
            self.cfg = new
            self._config_changed(location_changed=moved)

    def refresh_now(self) -> None:
        if self.poller is not None:
            self.poller.refresh_now()

    # ---- persistence & shutdown ---------------------------------------------

    def _schedule_save(self) -> None:
        self._save_timer.start()

    def save(self) -> None:
        self._save_timer.stop()
        if not self._fullscreen:  # keep the widget's own size/place for next time
            if self._can_position() and self.isVisible():
                self.cfg.window.x, self.cfg.window.y = self.x(), self.y()
            self.cfg.window.size = self.width()
        try:
            save_config(self.cfg, self.cfg_path)
        except OSError as exc:
            log.warning("Could not save config to %s: %s", self.cfg_path, exc)

    def shutdown(self) -> None:
        self.scope.stop_animation()
        if self.poller is not None:
            self.poller.stop()
            self.poller = None
        if self.tray is not None:
            self.tray.hide()
        self.save()

    def moveEvent(self, event) -> None:  # noqa: N802
        super().moveEvent(event)
        self._schedule_save()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_save()

    # ---- menu / tray / keys -------------------------------------------------

    def build_menu(self, parent: QWidget | None = None, menu: QMenu | None = None) -> QMenu:
        if menu is None:
            menu = QMenu(parent or self)
        menu.addAction("Settings…", self.open_settings)
        menu.addAction("Refresh now", self.refresh_now)
        full = QAction("Full screen", menu, checkable=True, checked=self._fullscreen)
        full.toggled.connect(self.set_full_screen)
        menu.addAction(full)
        rng = menu.addMenu("Range")
        group = QActionGroup(rng)
        for miles in RANGE_PRESETS_MI:
            act = QAction(f"{miles} mi", rng, checkable=True, checked=miles == self.cfg.scope_range_mi)
            act.triggered.connect(lambda _=False, m=miles: self.set_range(m))
            group.addAction(act)
            rng.addAction(act)
        names = QAction("Show airline names", menu, checkable=True, checked=self.cfg.show_airline_names)
        names.toggled.connect(self.set_show_airline_names)
        menu.addAction(names)
        top = QAction("Always on top", menu, checkable=True, checked=self.cfg.window.always_on_top)
        top.toggled.connect(self.set_always_on_top)
        menu.addAction(top)
        menu.addSeparator()
        menu.addAction("About RadarScope…", lambda: show_about(self))
        menu.addAction("Quit", quit_app)
        return menu

    def _show_menu(self, pos: QPoint) -> None:
        self.build_menu().exec(self.scope.mapToGlobal(pos))

    def _make_tray(self) -> QSystemTrayIcon | None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None
        tray = QSystemTrayIcon(self.windowIcon(), self)
        tray.setToolTip(APP_NAME)
        self._tray_menu = QMenu()

        def refresh_tray_menu() -> None:  # rebuilt each time so tick marks are current
            self._tray_menu.clear()
            self._tray_menu.addAction("Show / hide", self.toggle_visible)
            self.build_menu(menu=self._tray_menu)

        refresh_tray_menu()
        self._tray_menu.aboutToShow.connect(refresh_tray_menu)
        tray.setContextMenu(self._tray_menu)
        tray.activated.connect(lambda reason: self.toggle_visible()
                               if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.show()
        return tray

    def _make_menu_bar(self) -> QMenuBar:
        """macOS system menu bar (File / View) with the standard shortcuts."""
        bar = QMenuBar()  # parentless = the global macOS menu bar
        app_menu = bar.addMenu("File")
        settings = app_menu.addAction("Settings…", QKeySequence.StandardKey.Preferences, self.open_settings)
        settings.setMenuRole(QAction.MenuRole.PreferencesRole)
        app_menu.addAction("Refresh now", QKeySequence("Ctrl+R"), self.refresh_now)
        about = app_menu.addAction("About RadarScope", lambda: show_about(self))
        about.setMenuRole(QAction.MenuRole.AboutRole)
        quit_action = app_menu.addAction("Quit RadarScope", QKeySequence.StandardKey.Quit, quit_app)
        quit_action.setMenuRole(QAction.MenuRole.QuitRole)

        view = bar.addMenu("View")
        self._act_full = view.addAction("Enter Full Screen", QKeySequence.StandardKey.FullScreen,
                                        self.toggle_full_screen)
        self._act_full.setMenuRole(QAction.MenuRole.NoRole)
        view.addSeparator()
        view.addAction("Zoom In", QKeySequence.StandardKey.ZoomIn, self.zoom_in)
        view.addAction("Zoom Out", QKeySequence.StandardKey.ZoomOut, self.zoom_out)
        view.addSeparator()
        self._act_names = view.addAction("Show Airline Names")
        self._act_names.setCheckable(True)
        self._act_names.toggled.connect(self.set_show_airline_names)
        self._act_top = view.addAction("Always on Top")
        self._act_top.setCheckable(True)
        self._act_top.toggled.connect(self.set_always_on_top)
        view.aboutToShow.connect(self._sync_menu_bar)
        self._sync_menu_bar()
        return bar

    def _sync_menu_bar(self) -> None:
        self._act_full.setText("Exit Full Screen" if self._fullscreen else "Enter Full Screen")
        for action, state in ((self._act_names, self.cfg.show_airline_names),
                              (self._act_top, self.cfg.window.always_on_top)):
            action.blockSignals(True)
            action.setChecked(state)
            action.blockSignals(False)

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        key = event.key()
        if key in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom_in()
        elif key in (Qt.Key.Key_Minus, Qt.Key.Key_Underscore):
            self.zoom_out()
        elif key == Qt.Key.Key_F and not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.toggle_full_screen()
        elif key == Qt.Key.Key_Escape and self._fullscreen:
            self.set_full_screen(False)
        elif key == Qt.Key.Key_R:
            self.refresh_now()
        elif key == Qt.Key.Key_S:
            self.open_settings()
        elif key == Qt.Key.Key_Escape:
            self.scope.selected_hex = None
            self.scope.update()
        elif event.matches(QKeySequence.StandardKey.Quit) or (
                key == Qt.Key.Key_Q and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            quit_app()
        else:
            super().keyPressEvent(event)

    # ---- frameless move / resize --------------------------------------------

    def _edges_at(self, pos: QPointF) -> set[str]:
        edges = set()
        if pos.x() <= EDGE_GRAB_PX:
            edges.add("left")
        elif pos.x() >= self.width() - EDGE_GRAB_PX:
            edges.add("right")
        if pos.y() <= EDGE_GRAB_PX:
            edges.add("top")
        elif pos.y() >= self.height() - EDGE_GRAB_PX:
            edges.add("bottom")
        return edges

    @staticmethod
    def _cursor_for(edges: set[str]) -> Qt.CursorShape:
        if edges in ({"left", "top"}, {"right", "bottom"}):
            return Qt.CursorShape.SizeFDiagCursor
        if edges in ({"right", "top"}, {"left", "bottom"}):
            return Qt.CursorShape.SizeBDiagCursor
        if edges & {"left", "right"}:
            return Qt.CursorShape.SizeHorCursor
        if edges:
            return Qt.CursorShape.SizeVerCursor
        return Qt.CursorShape.ArrowCursor

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if obj is not self.scope:
            return super().eventFilter(obj, event)
        etype = event.type()
        if etype == QEvent.Type.Wheel:
            self._wheel_accum += event.angleDelta().y()
            while abs(self._wheel_accum) >= WHEEL_STEP:
                step = 1 if self._wheel_accum > 0 else -1
                self._wheel_accum -= step * WHEEL_STEP
                self.zoom_in() if step > 0 else self.zoom_out()
            return True
        if (etype == QEvent.Type.NativeGesture
                and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self._pinch_accum += event.value()  # trackpad pinch: + spread = zoom in
            while abs(self._pinch_accum) >= PINCH_STEP:
                step = 1 if self._pinch_accum > 0 else -1
                self._pinch_accum -= step * PINCH_STEP
                self.zoom_in() if step > 0 else self.zoom_out()
            return True
        if etype == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            pos = event.position()
            edges = set() if self._fullscreen else self._edges_at(pos)
            if edges:
                self._resize_origin = (edges, event.globalPosition(), self.geometry())
                return True
            if self.scope.blip_at(pos) is None:
                if self.scope.selected_hex is not None:
                    self.scope.selected_hex = None
                    self.scope.update()
                if self._fullscreen:
                    return True
                handle = self.windowHandle()
                if handle is None or not handle.startSystemMove():
                    self._move_origin = (event.globalPosition(), self.pos())
                return True
        elif etype == QEvent.Type.MouseMove:
            if self._resize_origin is not None:
                self._do_resize(event.globalPosition())
                return True
            if self._move_origin is not None:
                start, origin = self._move_origin
                delta = (event.globalPosition() - start).toPoint()
                self.move(origin + delta)
                return True
            edges = set() if self._fullscreen else self._edges_at(event.position())
            if edges:
                self.scope.setCursor(self._cursor_for(edges))
            elif self.scope.blip_at(event.position()) is not None:
                self.scope.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.scope.setCursor(Qt.CursorShape.ArrowCursor)
        elif etype == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            if self._resize_origin is not None or self._move_origin is not None:
                self._resize_origin = self._move_origin = None
                self._schedule_save()
                return True
        return super().eventFilter(obj, event)

    def _do_resize(self, global_pos: QPointF) -> None:
        edges, start, geo0 = self._resize_origin
        d = global_pos - start
        w, h = geo0.width(), geo0.height()
        if "right" in edges:
            w += d.x()
        if "left" in edges:
            w -= d.x()
        if "bottom" in edges:
            h += d.y()
        if "top" in edges:
            h -= d.y()
        horizontal = bool(edges & {"left", "right"})
        vertical = bool(edges & {"top", "bottom"})
        side = max(w, h) if horizontal and vertical else (w if horizontal else h)
        side = int(max(MIN_WINDOW_SIZE, min(MAX_WINDOW_SIZE, side)))
        x = geo0.x() + geo0.width() - side if "left" in edges else geo0.x()
        y = geo0.y() + geo0.height() - side if "top" in edges else geo0.y()
        self.setGeometry(x, y, side, side)


# ---- entry point ------------------------------------------------------------------

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="radarscope", description="Live ADS-B radar scope desktop widget.")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--config-dir", help="use this config directory instead of the platform default")
    ap.add_argument("--quit-after", type=float, metavar="SECONDS",
                    help="exit cleanly after SECONDS (smoke testing)")
    ap.add_argument("-v", "--verbose", action="store_true", help="log debugging information")
    args, unknown = ap.parse_known_args(argv)
    # Finder may pass e.g. -psn_0_12345 to app bundles; ignore anything we don't know.
    args.ignored = unknown
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    log_kwargs: dict = {}
    if sys.stderr is None or getattr(sys, "frozen", False):
        # pythonw.exe / RadarScope.app have no terminal: log to a file in the config dir.
        log_dir = config_path(args.config_dir).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        log_kwargs["filename"] = str(log_dir / "radarscope.log")
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s", **log_kwargs)
    note = configure_platform()

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setWindowIcon(make_icon())
    if note:
        log.info(note)
    if IS_LINUX and app.platformName() == "wayland":
        log.warning("Running on native Wayland: always-on-top and window positioning may be "
                    "limited. Install libxcb-cursor0 / use the .desktop launcher for XWayland.")

    if args.quit_after is not None:
        QTimer.singleShot(int(args.quit_after * 1000), quit_app)

    path = config_path(args.config_dir)
    cfg = load_config(path)
    log.info("Config: %s", path)
    if not cfg.has_home:
        dlg = SettingsDialog(cfg, first_run=True)
        if not dlg.exec():
            log.info("No postcode entered; exiting.")
            return 0
        cfg = dlg.result_config
        save_config(cfg, path)

    window = RadarWindow(cfg, path)
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    if cfg.window.fullscreen:
        window.set_full_screen(True)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
