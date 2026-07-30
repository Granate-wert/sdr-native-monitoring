"""Regression tests for window layout scaling.

Covers the defects seen after the Heatmap dock was added:
- a saved ``windowState`` from another dock layout (no version marker) must be
  ignored, otherwise Qt misplaces unknown docks (overlapping panels);
- restored/initial window geometry must stay inside the available screen area;
- the tall Heatmap dock content must be scrollable on short screens.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QSettings
from PySide6.QtWidgets import QApplication, QScrollArea

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import gui as gui_module
from esw_dfl.gui import WINDOW_STATE_VERSION, MainWindow
from heatmap_test_isolation import shutdown_window


def _make_window(settings: QSettings) -> MainWindow:
    with patch.object(gui_module, "QSettings", lambda *args, **kwargs: settings):
        return MainWindow()


class WindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings_path = str(Path(self._tmp.name) / "layout.ini")
        self._windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self._windows:
            shutdown_window(window, self.app)
        self._tmp.cleanup()

    def _settings(self) -> QSettings:
        return QSettings(self.settings_path, QSettings.Format.IniFormat)

    def _window(self, settings: QSettings) -> MainWindow:
        window = _make_window(settings)
        self._windows.append(window)
        return window

    def test_stale_window_state_without_version_falls_back_to_default_layout(self) -> None:
        settings = self._settings()
        first = self._window(settings)
        # Simulate a layout saved by a version with a different dock placement.
        first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first.heatmap_dock)
        settings.setValue("geometry", first.saveGeometry())
        settings.setValue("windowState", first.saveState())
        # No "windowStateVersion" key — like settings saved before this fix.
        settings.sync()

        second = self._window(settings)
        self.assertFalse(second.heatmap_dock.isFloating())
        self.assertIn(second.heatmap_dock, second.tabifiedDockWidgets(second.display_dock))
        self.assertEqual(
            second.dockWidgetArea(second.heatmap_dock), Qt.DockWidgetArea.RightDockWidgetArea
        )

    def test_current_window_state_version_is_restored(self) -> None:
        settings = self._settings()
        first = self._window(settings)
        first.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, first.heatmap_dock)
        settings.setValue("geometry", first.saveGeometry())
        settings.setValue("windowState", first.saveState())
        settings.setValue("windowStateVersion", WINDOW_STATE_VERSION)
        settings.sync()

        second = self._window(settings)
        self.assertEqual(
            second.dockWidgetArea(second.heatmap_dock), Qt.DockWidgetArea.LeftDockWidgetArea
        )

    def test_window_clamped_into_available_geometry(self) -> None:
        window = self._window(self._settings())
        window.setGeometry(-5000, -5000, 1500, 920)
        window._clamp_window_to_screen()
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry()
        frame = window.frameGeometry()
        self.assertGreaterEqual(frame.x(), available.x())
        self.assertGreaterEqual(frame.y(), available.y())
        # The window fits the work area unless its own minimum size is larger.
        self.assertLessEqual(frame.width(), max(available.width(), window.minimumSize().width()))
        self.assertLessEqual(frame.height(), max(available.height(), window.minimumSize().height()))

    def test_initial_size_fits_available_geometry(self) -> None:
        window = self._window(self._settings())
        available = QApplication.primaryScreen().availableGeometry()
        frame = window.frameGeometry()
        self.assertLessEqual(frame.width(), max(available.width(), window.minimumSize().width()))
        self.assertLessEqual(frame.height(), max(available.height(), window.minimumSize().height()))

    def test_heatmap_dock_content_is_scrollable(self) -> None:
        window = self._window(self._settings())
        scroll = window.heatmap_dock.widget()
        self.assertIsInstance(scroll, QScrollArea)
        self.assertIsNotNone(scroll.widget())

    def test_close_saves_window_state_version(self) -> None:
        settings = self._settings()
        window = self._window(settings)
        window.close()
        self.assertEqual(int(settings.value("windowStateVersion", 0)), WINDOW_STATE_VERSION)


if __name__ == "__main__":
    unittest.main()
