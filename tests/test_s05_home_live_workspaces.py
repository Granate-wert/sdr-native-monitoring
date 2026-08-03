"""S05 Home/Live workspace presentation tests."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sdr_monitor.domain import LiveConfiguration
from sdr_monitor.services.live_session import InMemoryLiveSessionService, fake_pluto_device
from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId
from sdr_monitor.ui.components import AppliedValueRow
from sdr_monitor.ui.presenters import LivePresenter
from sdr_monitor.ui.workspaces import HomeWorkspace, LiveMonitorWorkspace


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_for(predicate, timeout_s: float = 1.0) -> bool:
    app = _app()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


class S05HomeLiveWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.service = InMemoryLiveSessionService((fake_pluto_device(),))
        self.presenter = LivePresenter(self.service)

    def tearDown(self) -> None:
        self.presenter.shutdown()

    def test_home_routes_to_live_in_one_action(self) -> None:
        app = _app()
        shell = SDRAppShell()
        try:
            home = shell._workspace_pages[WorkspaceId.HOME]
            self.assertIsInstance(home, HomeWorkspace)
            home.live_requested.emit()
            app.processEvents()
            self.assertEqual(shell.active_workspace, WorkspaceId.LIVE)
            self.assertIsInstance(shell._workspace_pages[WorkspaceId.LIVE], LiveMonitorWorkspace)
            self.assertIs(shell._inspector.widget(), shell._workspace_pages[WorkspaceId.LIVE].inspector)
        finally:
            shell.close()
            shell.deleteLater()

    def test_live_layout_contains_requested_and_applied_truth(self) -> None:
        _app()
        workspace = LiveMonitorWorkspace(self.presenter)
        self.assertEqual(workspace.center.frequency_hz(), 2.4e9)
        self.assertEqual(workspace.backend.count(), 4)
        applied = workspace.findChild(AppliedValueRow)
        self.assertIsNotNone(applied)
        self.assertIn("ожидает устройство", applied.accessibleDescription())
        workspace.shutdown()

    def test_live_controls_follow_selected_device_capabilities(self) -> None:
        workspace = LiveMonitorWorkspace(self.presenter)
        snapshots = []
        self.presenter.snapshot_changed.connect(snapshots.append)
        self.presenter.select_device("fake-pluto-usb")
        self.assertTrue(_wait_for(lambda: bool(snapshots)))
        self.assertEqual(workspace.sample_rate.count(), 3)
        self.assertEqual(workspace.backend.count(), 2)
        workspace.shutdown()
    def test_presenter_runs_selection_apply_and_start_off_widget_path(self) -> None:
        snapshots = []
        self.presenter.snapshot_changed.connect(snapshots.append)
        self.presenter.select_device("fake-pluto-usb")
        self.assertTrue(_wait_for(lambda: len(snapshots) == 1))
        self.presenter.apply_configuration(LiveConfiguration())
        self.assertTrue(_wait_for(lambda: len(snapshots) == 2))
        self.presenter.start()
        self.assertTrue(_wait_for(lambda: len(snapshots) == 3))
        self.assertEqual(snapshots[-1].state.value, "running")

    def test_fake_publications_are_latest_wins_and_bounded_to_60_hz(self) -> None:
        renders = []
        self.presenter.render_ready.connect(renders.append)
        self.service.select_device("fake-pluto-usb")
        applied = self.service.apply_configuration(LiveConfiguration())
        self.service.start()
        for _ in range(120):
            self.presenter.offer_snapshot_for_render(self.service.publish_fake_snapshot(applied.generation))
        self.assertTrue(_wait_for(lambda: bool(renders)))
        self.assertEqual(len(renders), 1)
        self.assertEqual(renders[0].sequence, 120)
    def test_inspector_auto_collapses_at_1280_pixels(self) -> None:
        app = _app()
        shell = SDRAppShell()
        try:
            shell.resize(1280, 720)
            shell.show()
            app.processEvents()
            self.assertFalse(shell._inspector.isVisible())
        finally:
            shell.close()
            shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
