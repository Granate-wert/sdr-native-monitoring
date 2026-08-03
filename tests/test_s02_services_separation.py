"""S02 service and AppShell separation tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sdr_monitor.services import build_default_sdr_services
from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class S02AppShellTests(unittest.TestCase):
    def test_default_services_are_constructible_without_legacy_sdr_tree(self) -> None:
        services = build_default_sdr_services()
        self.assertFalse(services.live_sdr.is_running())
        self.assertEqual(services.live_sdr.poll_frames(), [])
        self.assertEqual(services.calibration.list_profiles(), ())

    def test_shell_has_exactly_six_sdr_workspaces(self) -> None:
        _app()
        shell = SDRAppShell()
        try:
            expected = {WorkspaceId.HOME, WorkspaceId.LIVE, WorkspaceId.SWEEP, WorkspaceId.CALIBRATION, WorkspaceId.RECORDING, WorkspaceId.DIAGNOSTICS}
            self.assertEqual(set(shell._nav_buttons), expected)
            self.assertEqual(len(shell._shortcuts), 6)
        finally:
            shell.close()
            shell.deleteLater()

    def test_workspace_factory_receives_standalone_service_root(self) -> None:
        _app()
        shell = SDRAppShell()
        received = []
        def factory():
            received.append(shell.services)
            from PySide6.QtWidgets import QWidget
            return QWidget()
        try:
            shell.register_workspace(WorkspaceId.LIVE, factory)
            shell.set_active_workspace(WorkspaceId.LIVE)
            self.assertEqual(received, [shell.services])
        finally:
            shell.close()
            shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
