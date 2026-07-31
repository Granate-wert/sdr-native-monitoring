"""P16UI-00 protection tests for the legacy MainWindow before migration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
from typing import ClassVar
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from esw_dfl import gui as gui_module
from esw_dfl.gui import MainWindow
from esw_dfl.ui_inventory import (
    ActionInventory,
    PERSISTED_ON_CLOSE_SETTINGS_KEYS,
    REQUIRED_DOCK_OBJECT_NAMES,
    REQUIRED_LEGACY_SETTINGS_KEYS,
    capture_main_window_inventory,
    duplicate_shortcuts,
    measure_ui_timing,
    settings_keys_from_source,
    write_inventory_atomic,
)
from heatmap_test_isolation import shutdown_window


class P16UiBaselineTests(unittest.TestCase):
    app: ClassVar[QApplication]

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="p16-ui-test-")
        self._settings = QSettings(
            str(Path(self._temporary.name) / "settings.ini"),
            QSettings.Format.IniFormat,
        )
        self._windows: list[MainWindow] = []

    def tearDown(self) -> None:
        for window in self._windows:
            shutdown_window(window, self.app)
        self._temporary.cleanup()

    def _window(self) -> MainWindow:
        with patch.object(gui_module, "QSettings", lambda *args, **kwargs: self._settings):
            window = MainWindow()
        self._windows.append(window)
        return window

    def test_runtime_inventory_preserves_docks_actions_menus_and_dialogs(self) -> None:
        inventory = capture_main_window_inventory(self._window(), gui_module)
        dock_names = {dock.object_name for dock in inventory.docks}
        self.assertTrue(set(REQUIRED_DOCK_OBJECT_NAMES).issubset(dock_names))
        self.assertEqual(inventory.duplicate_shortcuts, {})
        self.assertIn("ViewSettingsDialog", inventory.dialog_classes)
        self.assertIn("FrameNavigationSettingsDialog", inventory.dialog_classes)
        self.assertEqual(len(inventory.toolbars), 1)
        self.assertEqual(inventory.toolbars[0].object_name, "mainToolbar")
        self.assertIn("Файл", {menu.title for menu in inventory.menus})
        self.assertIn("Экспорт", {menu.title for menu in inventory.menus})
        actions = {action.text: action.shortcut for action in inventory.actions}
        self.assertEqual(actions["Открыть DFL…"], "Ctrl+O")
        self.assertEqual(actions["Открыть Live SDR…"], "Ctrl+L")
        self.assertEqual(actions["Воспроизведение"], "Space")

    def test_legacy_settings_contract_is_static_and_persisted_in_isolation(self) -> None:
        source_keys = settings_keys_from_source(Path(gui_module.__file__))
        self.assertTrue(REQUIRED_LEGACY_SETTINGS_KEYS.issubset(source_keys))
        window = self._window()
        window.close()
        self._settings.sync()
        self.assertTrue(PERSISTED_ON_CLOSE_SETTINGS_KEYS.issubset(set(self._settings.allKeys())))

    def test_repeated_offscreen_creation_and_status_updates_stay_bounded(self) -> None:
        def create_window() -> MainWindow:
            with patch.object(gui_module, "QSettings", lambda *args, **kwargs: self._settings):
                return MainWindow()

        baseline = measure_ui_timing(
            create_window,
            lambda window: shutdown_window(window, self.app),
            creation_runs=3,
            status_update_runs=60,
        )
        metrics = baseline.to_dict()
        self.assertEqual(metrics["creation_runs"], 3)
        self.assertEqual(metrics["status_update_runs"], 180)
        self.assertLess(metrics["creation_p95_ms"], 2_000.0)
        self.assertLess(metrics["status_update_p95_ms"], 8.0)

    def test_duplicate_shortcut_detector_is_explicit(self) -> None:
        duplicates = duplicate_shortcuts(
            (
                ActionInventory("One", "Ctrl+K", "", True),
                ActionInventory("Two", "Ctrl+K", "", True),
                ActionInventory("Three", "", "", True),
            )
        )
        self.assertEqual(duplicates, {"Ctrl+K": ("One", "Two")})

    def test_manifest_writer_is_atomic_and_anonymised(self) -> None:
        target = Path(self._temporary.name) / "inventory.json"
        write_inventory_atomic(target, {"schema": "test", "docks": ["filesTracesDock"]})
        self.assertFalse(Path(f"{target}.part").exists())
        self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["schema"], "test")


if __name__ == "__main__":
    unittest.main()
