"""Visible Windows DPI preflight for R11-AE without product or device work.

This deliberately creates a small disposable Qt window instead of the normal
AppShell. It cannot discover a source, open a serial port or reserve the
write-once final-evidence destination. Its only purpose is to reject a wrong
Windows scale or insufficient logical geometry before a human consumes one of
the four final cells.
"""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QGuiApplication, QPaintEvent
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ..r11ae_tinysa_visible_ui_evidence import (
    R11AEDisplayPreflight,
    R11AEVisibleUiProfile,
)


class _DisplayPreflightWindow(QWidget):
    """A local painted surface; it intentionally has no product wiring."""

    def __init__(self, profile: R11AEVisibleUiProfile) -> None:
        super().__init__()
        self._paint_events = 0
        self.setWindowTitle("SDR Native Monitoring — R11-AE display/DPI preflight")
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Checking only Windows scale and logical geometry. "
                "No source discovery, serial port or tinySA command is performed."
            )
        )
        self.resize(profile.width, profile.height)

    @property
    def paint_events(self) -> int:
        return self._paint_events

    def paintEvent(self, event: QPaintEvent) -> None:
        self._paint_events += 1
        super().paintEvent(event)


def collect_r11ae_windows_display_preflight(
    profile: R11AEVisibleUiProfile,
    *,
    dwell_milliseconds: int = 350,
) -> R11AEDisplayPreflight:
    """Observe one painted Windows display surface without device-side effects."""

    if not 50 <= dwell_milliseconds <= 5_000:
        raise ValueError("R11-AE display preflight dwell must be between 50 and 5000 ms")
    if QGuiApplication.platformName().casefold() != "windows":
        raise RuntimeError("R11-AE display preflight accepts only the visible Windows Qt platform")
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        raise TypeError("R11-AE display preflight requires QApplication")
    window = _DisplayPreflightWindow(profile)
    try:
        window.show()
        window.raise_()
        window.activateWindow()
        settled = QEventLoop()
        QTimer.singleShot(dwell_milliseconds, settled.quit)
        settled.exec()
        screen = window.screen()
        if screen is None:
            raise RuntimeError("R11-AE display preflight window has no screen")
        return R11AEDisplayPreflight(
            profile=profile,
            observed_at_utc=datetime.now(UTC).isoformat(),
            platform_name=QGuiApplication.platformName(),
            window_visible=window.isVisible(),
            window_paint_events=window.paint_events,
            device_pixel_ratio=window.devicePixelRatioF(),
            logical_dpi_x=screen.logicalDotsPerInchX(),
            logical_dpi_y=screen.logicalDotsPerInchY(),
            logical_width=window.width(),
            logical_height=window.height(),
        )
    finally:
        window.close()
        window.deleteLater()
        app.processEvents()


__all__ = ["collect_r11ae_windows_display_preflight"]
