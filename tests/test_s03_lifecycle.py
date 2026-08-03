"""S03 lifecycle, settings and bounded-notification tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget
from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId
from sdr_monitor.ui.notifications import NotificationItem, NotificationSeverity, NotificationStore
from sdr_monitor.ui.settings import CURRENT_SCHEMA_VERSION, ensure_schema_version, schema_version


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class ProbeWorkspace(QWidget):
    shutdown_calls = 0
    def shutdown(self) -> None:
        type(self).shutdown_calls += 1


class S03LifecycleTests(unittest.TestCase):
    def test_notification_store_reports_eviction_truthfully(self) -> None:
        store = NotificationStore(capacity=2)
        self.assertTrue(store.push(NotificationItem("one", "one", NotificationSeverity.INFO)))
        self.assertTrue(store.push(NotificationItem("two", "two", NotificationSeverity.WARNING)))
        self.assertFalse(store.push(NotificationItem("three", "three", NotificationSeverity.ERROR)))
        self.assertEqual(store.dropped_count, 1)
        self.assertEqual([item.notification_id for item in store.items], ["two", "three"])

    def test_settings_schema_resets_unknown_versions(self) -> None:
        settings = QSettings("sdr_s03_test", "sdr_s03_test")
        settings.clear()
        settings.setValue("schema_version", CURRENT_SCHEMA_VERSION + 1)
        settings.setValue("obsolete", "value")
        ensure_schema_version(settings)
        self.assertEqual(schema_version(settings), CURRENT_SCHEMA_VERSION)
        self.assertNotIn("obsolete", settings.allKeys())

    def test_replacing_workspace_disposes_the_old_widget_once(self) -> None:
        app = _app()
        ProbeWorkspace.shutdown_calls = 0
        shell = SDRAppShell()
        try:
            shell.register_workspace(WorkspaceId.LIVE, ProbeWorkspace)
            shell.set_active_workspace(WorkspaceId.LIVE)
            shell.register_workspace(WorkspaceId.LIVE, ProbeWorkspace)
            app.processEvents()
            self.assertEqual(ProbeWorkspace.shutdown_calls, 1)
        finally:
            shell.close()
            shell.deleteLater()

    def test_workspace_signal_is_emitted_once_per_change(self) -> None:
        _app()
        shell = SDRAppShell()
        calls = []
        try:
            shell.workspace_changed.connect(calls.append)
            shell.set_active_workspace(WorkspaceId.LIVE)
            shell.set_active_workspace(WorkspaceId.LIVE)
            self.assertEqual(calls, [WorkspaceId.LIVE])
        finally:
            shell.close()
            shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
