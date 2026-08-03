"""S02 service-separation tests for the standalone SDR AppShell."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from esw_dfl.ui.state import WorkspaceId
from sdr_monitor.app_shell import create_sdr_app_shell


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


class S02AppShellTests(unittest.TestCase):
    def test_sdr_shell_excludes_offline_dfl(self) -> None:
        _app()
        shell = create_sdr_app_shell()()
        try:
            shell.show()
            workspaces = [d.workspace_id for d in shell._workspaces.descriptors()]
            self.assertIn(WorkspaceId.LIVE_MONITOR, workspaces)
            self.assertIn(WorkspaceId.WIDEBAND_SWEEP, workspaces)
            self.assertIn(WorkspaceId.CALIBRATION, workspaces)
            self.assertIn(WorkspaceId.RECORDING_REPLAY, workspaces)
            self.assertIn(WorkspaceId.DIAGNOSTICS, workspaces)
            self.assertNotIn(WorkspaceId.OFFLINE_DFL, workspaces)
        finally:
            shell.close()

    def test_sdr_shell_no_legacy_fallback_in_nav(self) -> None:
        _app()
        shell = create_sdr_app_shell()()
        try:
            shell.show()
            # QTabWidget inside the shell contains only SDR tabs.
            tab_texts = [
                shell._workspace_host.tabText(i)
                for i in range(shell._workspace_host.count())
            ]
            self.assertNotIn("offline_dfl", " ".join(tab_texts))
        finally:
            shell.close()

    def test_sdr_shell_keyboard_shortcuts_present(self) -> None:
        _app()
        shell = create_sdr_app_shell()()
        try:
            sequences = sorted({sc.key().toString() for sc in shell._workspace_shortcuts})
            # There are 6 shortcuts total for SDR workspaces; the numbers
            # Ctrl+1..7 are wired from the base registry.  OFFLINE_DFL's
            # Ctrl+\, connects to a no-op inside SDRAppShell.
            self.assertEqual(len(sequences), 6)
            self.assertIn("Ctrl+1", sequences)
            self.assertIn("Ctrl+7", sequences)
        finally:
            shell.close()

    def test_sdr_shell_close_clean(self) -> None:
        _app()
        shell = create_sdr_app_shell()()
        shell.show()
        shell.close()
        shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
