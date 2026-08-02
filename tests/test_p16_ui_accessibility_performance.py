"""P16UI-09 accessibility, settings migration and UI performance tests."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QWidget

from esw_dfl.ui.app_shell import AppShell
from esw_dfl.ui.notifications import (
    NotificationItem,
    NotificationSeverity,
    NotificationStore,
)
from esw_dfl.ui.settings_migration import (
    CURRENT_SCHEMA_VERSION,
    ensure_schema_version,
    reset_settings,
    schema_version,
)
from esw_dfl.ui.state import WorkspaceId


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _notification(seq: int) -> NotificationItem:
    return NotificationItem(
        notification_id=f"n-{seq}",
        message=f"message {seq}",
        severity=NotificationSeverity.INFO,
    )


class NotificationBoundsTests(unittest.TestCase):
    def test_bounded_capacity(self) -> None:
        store = NotificationStore(capacity=4)
        for index in range(6):
            store.push(_notification(index))
        self.assertEqual(len(store.items), 4)
        self.assertEqual(store.dropped_count, 2)
        self.assertEqual(store.items[0].notification_id, "n-2")

    def test_dismiss_and_clear(self) -> None:
        store = NotificationStore(capacity=8)
        store.push(_notification(1))
        store.push(_notification(2))
        self.assertTrue(store.dismiss("n-1"))
        self.assertFalse(store.dismiss("n-1"))
        store.clear()
        self.assertEqual(len(store.items), 0)

    def test_capacity_validation(self) -> None:
        with self.assertRaises(ValueError):
            NotificationStore(capacity=0)


class WorkspaceShortcutTests(unittest.TestCase):
    def test_shortcuts_registered_for_all_workspaces(self) -> None:
        _app()
        shell = AppShell()
        try:
            sequences = {shortcut.key().toString() for shortcut in shell._workspace_shortcuts}
            expected = {"Ctrl+1", "Ctrl+2", "Ctrl+3", "Ctrl+4", "Ctrl+5", "Ctrl+6", "Ctrl+7"}
            self.assertEqual(sequences, expected)
        finally:
            shell.close()

    def test_shortcut_activation_switches_workspace(self) -> None:
        app = _app()
        shell = AppShell()
        try:
            shell.show()
            app.processEvents()
            target = next(
                shortcut
                for shortcut in shell._workspace_shortcuts
                if shortcut.key() == QKeySequence("Ctrl+3")
            )
            target.activated.emit()
            app.processEvents()
            self.assertIs(shell.active_workspace, WorkspaceId.WIDEBAND_SWEEP)
        finally:
            shell.close()


class SettingsSchemaTests(unittest.TestCase):
    def _settings(self) -> QSettings:
        settings = QSettings("p16_ui_09_test", "p16_ui_09_test")
        settings.clear()
        settings.sync()
        return settings

    def test_missing_schema_means_legacy_v1(self) -> None:
        settings = self._settings()
        self.assertEqual(schema_version(settings), 1)

    def test_ensure_schema_bumps_legacy(self) -> None:
        settings = self._settings()
        ensure_schema_version(settings)
        self.assertEqual(schema_version(settings), CURRENT_SCHEMA_VERSION)

    def test_ensure_schema_resets_newer_versions(self) -> None:
        settings = self._settings()
        settings.setValue("schema_version", CURRENT_SCHEMA_VERSION + 99)
        settings.setValue("theme", "dark")
        ensure_schema_version(settings)
        self.assertEqual(schema_version(settings), CURRENT_SCHEMA_VERSION)
        self.assertNotIn("theme", settings.allKeys())

    def test_reset_settings_keeps_only_schema(self) -> None:
        settings = self._settings()
        settings.setValue("theme", "dark")
        settings.setValue("frame_navigation/fps", 30)
        reset_settings(settings)
        self.assertEqual(settings.allKeys(), ["schema_version"])


class RepeatLifecycleTests(unittest.TestCase):
    def test_repeated_shell_lifecycle(self) -> None:
        """Repeated open/close must call workspace shutdown and stay bounded."""

        app = _app()
        shutdown_calls: list[str] = []

        class _ClosingWorkspace(QWidget):
            def __init__(self, workspace_id: WorkspaceId) -> None:
                super().__init__()
                self._workspace_id = workspace_id

            def request_shutdown(self) -> None:
                shutdown_calls.append(self._workspace_id.value)
                self.close()

        for _ in range(6):
            shell = AppShell(
                recording_workspace_factory=lambda: _ClosingWorkspace(WorkspaceId.RECORDING_REPLAY),
                diagnostics_workspace_factory=lambda: _ClosingWorkspace(WorkspaceId.DIAGNOSTICS),
            )
            shell.show()
            app.processEvents()
            shell.set_active_workspace(WorkspaceId.RECORDING_REPLAY)
            shell.set_active_workspace(WorkspaceId.DIAGNOSTICS)
            shell.close()
            shell.deleteLater()
            app.processEvents()
        self.assertEqual(shutdown_calls.count(WorkspaceId.RECORDING_REPLAY.value), 6)
        self.assertEqual(shutdown_calls.count(WorkspaceId.DIAGNOSTICS.value), 6)


class UiPerformanceTests(unittest.TestCase):
    def test_workspace_switch_budget(self) -> None:
        app = _app()
        shell = AppShell()
        try:
            shell.show()
            app.processEvents()
            ids = list(WorkspaceId)
            durations: list[float] = []
            for _ in range(50):
                for wid in ids:
                    start = time.perf_counter()
                    shell.set_active_workspace(wid)
                    app.processEvents()
                    durations.append(time.perf_counter() - start)
            durations.sort()
            p95 = durations[int(len(durations) * 0.95)]
            self.assertLess(p95, 0.016667, f"p95 switch {p95:.6f}s exceeds 60Hz budget")
        finally:
            shell.close()


class HighContrastThemeTests(unittest.TestCase):
    def test_high_contrast_stylesheet_available(self) -> None:
        from esw_dfl.ui.design_tokens import ThemeId
        from esw_dfl.ui.themes import ThemeProvider

        stylesheet = ThemeProvider.stylesheet(ThemeId.HIGH_CONTRAST)
        self.assertIn("font-weight", stylesheet)
        self.assertIn("outline", stylesheet)


class WorkspaceDpiMatrixTests(unittest.TestCase):
    """AppShell must construct and render at 1280x720 under several scales."""

    def test_app_shell_fits_1280x720_at_100_150_200(self) -> None:
        app = _app()
        for scale in (1.0, 1.5, 2.0):
            previous = os.environ.get("QT_SCALE_FACTOR")
            os.environ["QT_SCALE_FACTOR"] = str(scale)
            try:
                shell = AppShell()
                try:
                    shell.resize(1280, 720)
                    shell.show()
                    app.processEvents()
                    # Primary controls must remain reachable: the workspace host
                    # is the central widget and the health bar label is laid out
                    # inside the status bar.
                    minimum = shell._health_bar_label.minimumSizeHint()
                    self.assertGreaterEqual(shell.height(), max(1, minimum.height()))
                    host = shell._workspace_host
                    self.assertGreater(host.width(), 0)
                    self.assertGreater(host.height(), 0)
                finally:
                    shell.close()
                    shell.deleteLater()
            finally:
                if previous is None:
                    os.environ.pop("QT_SCALE_FACTOR", None)
                else:
                    os.environ["QT_SCALE_FACTOR"] = previous


if __name__ == "__main__":
    unittest.main()
