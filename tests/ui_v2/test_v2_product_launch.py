"""V2 product launch composes existing public presenters without device work."""

from __future__ import annotations

from types import SimpleNamespace
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.main import _build_v2_shell
from sdr_monitor.services.live_session import InMemoryLiveSessionService
from sdr_monitor.services.sweep_session import InMemorySweepService
from sdr_monitor.ui.v2.workspaces import LiveWorkspaceV2, SweepWorkspaceV2


class V2ProductLaunchTests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_explicit_v2_launch_uses_one_service_graph_and_lazy_live_workspace(self) -> None:
        services = SimpleNamespace(
            live_sdr=InMemoryLiveSessionService(),
            sweep=InMemorySweepService(),
            calibration=object(),
            diagnostics=object(),
        )
        with patch("sdr_monitor.services.build_default_sdr_services", return_value=services) as build_services:
            shell = _build_v2_shell()
        try:
            self.assertEqual(build_services.call_count, 1)
            self.assertEqual(shell.active_workspace_id, "home")
            self.assertNotIn("live", shell.created_workspace_ids)
            shell.select_workspace("live")
            self.assertIsInstance(shell._workspace_pages["live"], LiveWorkspaceV2)
            self.assertNotIn("sweep", shell.created_workspace_ids)
            shell.select_workspace("sweep")
            self.assertIsInstance(shell._workspace_pages["sweep"], SweepWorkspaceV2)
        finally:
            shell.close()
            shell.deleteLater()
            self.app.processEvents()
