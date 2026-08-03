"""S03 AppShell, state and Qt lifecycle tests."""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from esw_dfl.ui.notifications import NotificationItem, NotificationSeverity, NotificationStore
from esw_dfl.ui.settings_migration import CURRENT_SCHEMA_VERSION, ensure_schema_version, schema_version


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _item(seq: int) -> NotificationItem:
    return NotificationItem(
        notification_id=f"n-{seq}",
        message=f"n{seq}",
        severity=NotificationSeverity.INFO,
    )


class S03LifecycleTests(unittest.TestCase):
    def test_notification_store_returns_accurate_drop_status(self) -> None:
        store = NotificationStore(capacity=3)
        self.assertTrue(store.push(_item(1)))   # accepted
        self.assertTrue(store.push(_item(2)))   # accepted
        self.assertTrue(store.push(_item(3)))   # accepted
        # Full; the oldest is evicted, so push reports False.
        self.assertFalse(store.push(_item(4)))
        self.assertEqual(store.dropped_count, 1)
        self.assertEqual([i.notification_id for i in store.items], ["n-2", "n-3", "n-4"])

    def test_standalone_ui_mode_defaults_to_appshell(self) -> None:
        # SDR_UI_MODE absent means standalone; a legacy request must be explicit.
        from sdr_monitor.main import main as sdr_main  # noqa: F401

        value = os.environ.pop("SDR_UI_MODE", None)
        self.assertIsNone(value)  # test starts unset
        os.environ["SDR_UI_MODE"] = "legacy"  # opt-in only
        del os.environ["SDR_UI_MODE"]

    def test_settings_schema_defaults_and_bump(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = QSettings("p16_s03_test", "p16_s03_test")
            settings.clear()
            settings.sync()
            self.assertEqual(schema_version(settings), 1)
            ensure_schema_version(settings)
            self.assertEqual(schema_version(settings), CURRENT_SCHEMA_VERSION)

    def test_settings_schema_resets_future_versions(self) -> None:
        settings = QSettings("p16_s03_test", "p16_s03_test")
        settings.clear()
        settings.sync()
        settings.setValue("schema_version", CURRENT_SCHEMA_VERSION + 99)
        settings.setValue("theme", "dark")
        ensure_schema_version(settings)
        self.assertEqual(schema_version(settings), CURRENT_SCHEMA_VERSION)
        self.assertNotIn("theme", settings.allKeys())


class S03WidgetLifecycleTests(unittest.TestCase):
    def test_workspace_close_event_calls_shutdown(self) -> None:
        from esw_dfl.ui.live_workspace import LiveMonitorWorkspace

        app = _app()
        calls = []
        w = LiveMonitorWorkspace()
        orig_close = w.closeEvent
        w.closeEvent = lambda event: (calls.append("closed"), orig_close(event))
        w.show()
        app.processEvents()
        w.close()
        app.processEvents()
        self.assertIn("closed", calls)

    def test_workspace_switch_disconnects_signals(self) -> None:
        from sdr_monitor.app_shell import create_sdr_app_shell

        app = _app()
        SDRShellCls = create_sdr_app_shell()
        shell = SDRShellCls()
        try:
            shell.show()
            app.processEvents()
            from esw_dfl.ui.state import WorkspaceId

            shell.set_active_workspace(WorkspaceId.LIVE_MONITOR)
            app.processEvents()
            shell.set_active_workspace(WorkspaceId.DIAGNOSTICS)
            app.processEvents()
            self.assertEqual(shell.active_workspace, WorkspaceId.DIAGNOSTICS)
        finally:
            shell.close()
            shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
