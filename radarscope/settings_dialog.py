"""Settings dialog: postcode, scope range (miles), poll interval, data source."""

from __future__ import annotations

import copy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QMessageBox,
    QSpinBox, QVBoxLayout, QWidget,
)

from . import __version__, geo
from .config import DATA_SOURCES, MAX_POLL_S, MIN_POLL_S, Config
from .data_source import GeocodeResult, geocode_postcode, normalise_postcode
from .poller import BackgroundTask

ATTRIBUTION_HTML = (
    "Aircraft data © <a href='https://adsb.lol'>adsb.lol</a> contributors, "
    "<a href='https://opendatacommons.org/licenses/odbl/1-0/'>ODbL 1.0</a>; "
    "fallback data from <a href='https://airplanes.live'>airplanes.live</a>.<br>"
    "Postcode geocoding by <a href='https://postcodes.io'>postcodes.io</a> "
    "(Open Government Licence data).<br>"
    "Personal / hobby use only — check each source's terms before any other use."
)


def show_about(parent: QWidget | None) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle("About RadarScope")
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setText(f"<b>RadarScope {__version__}</b><br>Live ADS-B radar scope widget.<br><br>{ATTRIBUTION_HTML}")
    box.exec()


class SettingsDialog(QDialog):
    """Edits a copy of the config; on accept, :attr:`result_config` holds the new values.

    If the postcode changed (or no location is cached yet) it is validated and
    geocoded via postcodes.io on a worker thread before the dialog closes.
    """

    def __init__(self, cfg: Config, parent: QWidget | None = None, first_run: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("RadarScope — Welcome" if first_run else "RadarScope Settings")
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.result_config = copy.deepcopy(cfg)
        self._original = cfg
        self._task: BackgroundTask | None = None

        self.postcode = QLineEdit(cfg.postcode)
        self.postcode.setPlaceholderText("e.g. GL14 2AB")
        self.postcode.setMaxLength(10)

        self.range_mi = QSpinBox()
        self.range_mi.setRange(geo.MIN_SCOPE_RANGE_MI, geo.MAX_SCOPE_RANGE_MI)
        self.range_mi.setSuffix(" mi")
        self.range_mi.setValue(cfg.scope_range_mi)
        self.range_mi.setToolTip("Scope radius in statute miles (max ≈ 287 mi = the feed's 250 nm cap)")

        self.poll = QSpinBox()
        self.poll.setRange(MIN_POLL_S, MAX_POLL_S)
        self.poll.setSuffix(" s")
        self.poll.setValue(cfg.poll_seconds)

        self.source = QComboBox()
        self.source.addItems(DATA_SOURCES)
        self.source.setCurrentText(cfg.data_source)
        self.source.setToolTip("Preferred feed; RadarScope fails over to the other one on repeated errors")

        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #c0392b;")

        intro = QLabel("Enter your UK postcode to centre the scope on your location."
                       if first_run else "")
        intro.setWordWrap(True)
        intro.setVisible(first_run)

        form = QFormLayout()
        form.addRow("Postcode", self.postcode)
        form.addRow("Scope range", self.range_mi)
        form.addRow("Poll every", self.poll)
        form.addRow("Data source", self.source)

        credit = QLabel(ATTRIBUTION_HTML)
        credit.setWordWrap(True)
        credit.setOpenExternalLinks(True)
        credit.setStyleSheet("color: gray; font-size: small;")

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self._on_ok)
        self.buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.status)
        layout.addWidget(credit)
        layout.addWidget(self.buttons)
        self.setMinimumWidth(360)

    def _apply_common(self) -> None:
        c = self.result_config
        c.scope_range_mi = self.range_mi.value()
        c.poll_seconds = self.poll.value()
        c.data_source = self.source.currentText()

    def _set_busy(self, busy: bool) -> None:
        for w in (self.postcode, self.range_mi, self.poll, self.source):
            w.setEnabled(not busy)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not busy)
        if busy:
            self.status.setStyleSheet("color: gray;")
            self.status.setText("Looking up postcode…")

    def _on_ok(self) -> None:
        pc = normalise_postcode(self.postcode.text())
        if not pc:
            self.status.setStyleSheet("color: #c0392b;")
            self.status.setText("Please enter a postcode.")
            return
        self._apply_common()
        if pc == normalise_postcode(self._original.postcode) and self._original.has_home:
            self.accept()
            return
        self._set_busy(True)
        self._task = BackgroundTask(lambda: geocode_postcode(pc), self)
        self._task.succeeded.connect(self._on_geocoded)
        self._task.failed.connect(self._on_geocode_failed)
        self._task.start()

    def _on_geocoded(self, result: GeocodeResult) -> None:
        self._set_busy(False)
        c = self.result_config
        c.postcode, c.home_lat, c.home_lon = result.postcode, result.latitude, result.longitude
        self.accept()

    def _on_geocode_failed(self, message: str) -> None:
        self._set_busy(False)
        self.status.setStyleSheet("color: #c0392b;")
        self.status.setText(message)
        self.postcode.setFocus()
        self.postcode.selectAll()

    def done(self, result: int) -> None:
        if self._task is not None and result != QDialog.DialogCode.Accepted:
            # Cancelled mid-lookup: the daemon thread finishes on its own; ignore its result.
            self._task.cancelled = True
        super().done(result)
