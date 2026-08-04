"""S06 standalone sweep planner, presenter and workspace tests."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import SweepConfiguration, SweepMode, SweepState
from sdr_monitor.services.sweep_session import InMemorySweepService
from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId
from sdr_monitor.ui.presenters import SweepPresenter
from sdr_monitor.ui.workspaces import SweepWorkspace


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _wait_for(predicate, timeout_s: float = 2.0) -> bool:
    app = _app()
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


class S06SweepServiceTests(unittest.TestCase):
    def test_plan_is_mode_aware_and_preserves_usable_span(self) -> None:
        service = InMemorySweepService()
        fast = service.plan(SweepConfiguration(start_hz=400e6, stop_hz=600e6, mode=SweepMode.FAST))
        precise = service.plan(SweepConfiguration(start_hz=400e6, stop_hz=600e6, mode=SweepMode.PRECISE))
        self.assertLess(len(fast.segments), len(precise.segments))
        self.assertLess(fast.resolution_hz, 1e6)
        self.assertTrue(all(segment.usable_start_hz <= segment.usable_stop_hz for segment in fast.segments))

    def test_invalid_range_is_rejected_before_execution(self) -> None:
        with self.assertRaises(ValueError):
            SweepConfiguration(start_hz=6e9, stop_hz=400e6)

    def test_cancellation_keeps_missing_segments_explicit(self) -> None:
        service = InMemorySweepService()
        configuration = SweepConfiguration(start_hz=400e6, stop_hz=800e6, mode=SweepMode.PRECISE)

        def cancel_after_first(progress) -> None:
            if progress.completed_segments >= 1:
                service.cancel()

        result = service.execute(configuration, cancel_after_first)
        self.assertEqual(result.state, SweepState.CANCELLED)
        self.assertGreater(result.quality.missing_segments, 0)
        self.assertIsNone(result.quality.seam_p95_db)

    def test_export_is_atomic_and_contains_no_fabricated_quality(self) -> None:
        service = InMemorySweepService()
        result = service.execute(SweepConfiguration(start_hz=400e6, stop_hz=450e6), lambda _progress: None)
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "summary.json"
            self.assertEqual(service.export_result(result, target), target)
            self.assertTrue(target.exists())
            self.assertFalse(target.with_suffix(".json.part").exists())
            self.assertIn('"seam_p95_db": null', target.read_text(encoding="utf-8"))


class S06SweepWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.service = InMemorySweepService()
        self.presenter = SweepPresenter(self.service)
        self.workspace = SweepWorkspace(self.presenter)

    def tearDown(self) -> None:
        self.workspace.shutdown()
        self.workspace.close()
        self.workspace.deleteLater()
        self.presenter.shutdown()

    def test_mode_and_plan_preview_are_available_without_legacy_product(self) -> None:
        self.assertEqual(self.workspace.mode_combo.count(), 3)
        self.assertTrue(_wait_for(lambda: self.workspace._plan is not None))
        self.workspace._apply_mode(0)
        self.assertEqual(self.workspace.dwell.value(), 0.02)
        self.assertIn("Сегментов", self.workspace._plan_summary.text())

    def test_invalid_range_is_actionable_in_workspace(self) -> None:
        self.workspace.start.set_frequency_hz(6e9)
        self.workspace.stop.set_frequency_hz(400e6)
        self.workspace.request_plan()
        self.assertTrue(not self.workspace._error.isHidden())
        self.assertIn("stop frequency", self.workspace._error.accessibleDescription())

    def test_changed_configuration_is_replanned_before_run(self) -> None:
        self.assertTrue(_wait_for(lambda: self.workspace._plan is not None))
        original = self.workspace._plan
        self.workspace.stop.set_frequency_hz(5e9)
        self.workspace.run()
        self.assertTrue(_wait_for(lambda: self.workspace._plan is not None and self.workspace._plan != original))
        self.assertEqual(self.workspace._plan.configuration.stop_hz, 5e9)
        self.assertIsNone(self.workspace._result)
    def test_run_result_exposes_unknown_quality_instead_of_inventing_values(self) -> None:
        self.assertTrue(_wait_for(lambda: self.workspace._plan is not None))
        self.workspace.run()
        self.assertTrue(_wait_for(lambda: self.workspace._result is not None))
        self.assertEqual(self.workspace._result.state, SweepState.COMPLETED)
        self.assertIn("не измерено", self.workspace._seam_chip.accessibleName())

    def test_shell_uses_standalone_sweep_workspace_and_stops_presenters(self) -> None:
        shell = SDRAppShell()
        try:
            shell.set_active_workspace(WorkspaceId.SWEEP)
            self.assertIsInstance(shell._workspace_pages[WorkspaceId.SWEEP], SweepWorkspace)
        finally:
            shell.close()
            self.assertTrue(shell._live_presenter._closed)
            self.assertTrue(shell._sweep_presenter._closed)
            shell.deleteLater()


if __name__ == "__main__":
    unittest.main()
