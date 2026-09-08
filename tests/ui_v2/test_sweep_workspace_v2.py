"""Offscreen UI2-08A tests over a fake frozen Sweep presenter only."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import (
    SweepConfiguration,
    SweepMode,
    SweepPlan,
    SweepProgress,
    SweepQuality,
    SweepResult,
    SweepSegment,
    SweepState,
)
from sdr_monitor.ui.v2.i18n import text
from sdr_monitor.ui.v2.view_models.sweep_view_model import SweepViewModel
from sdr_monitor.ui.v2.workspaces import SweepWorkspaceV2, sweep_workspace_definition


class FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[Callable[..., object]] = []

    def connect(self, callback: Callable[..., object]) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback: Callable[..., object]) -> None:
        self.callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class FakeSweepPresenter:
    def __init__(self) -> None:
        self.busy_changed = FakeSignal()
        self.plan_ready = FakeSignal()
        self.progress_changed = FakeSignal()
        self.result_ready = FakeSignal()
        self.export_ready = FakeSignal()
        self.task_failed = FakeSignal()
        self.plan_calls: list[SweepConfiguration] = []
        self.execute_calls: list[SweepConfiguration] = []
        self.cancel_calls = 0
        self.export_calls: list[tuple[SweepResult, Path]] = []

    def plan(self, configuration: SweepConfiguration) -> None:
        self.plan_calls.append(configuration)

    def execute(self, configuration: SweepConfiguration) -> None:
        self.execute_calls.append(configuration)

    def cancel(self) -> None:
        self.cancel_calls += 1

    def export_result(self, result: SweepResult, output_path: Path) -> None:
        self.export_calls.append((result, output_path))


def _plan(configuration: SweepConfiguration) -> SweepPlan:
    split = (configuration.start_hz + configuration.stop_hz) / 2
    return SweepPlan(
        configuration=configuration,
        segments=(
            SweepSegment(0, configuration.start_hz, split, configuration.start_hz + 10_000.0, split - 10_000.0),
            SweepSegment(1, split, configuration.stop_hz, split + 10_000.0, configuration.stop_hz - 10_000.0),
        ),
        estimated_seconds=2.5,
        resolution_hz=1_000.0,
    )


class SweepWorkspaceV2Tests(unittest.TestCase):
    app: QApplication

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.presenter = FakeSweepPresenter()
        self.model = SweepViewModel(self.presenter)
        self.workspace = SweepWorkspaceV2(self.model)
        self.workspace.resize(1366, 768)
        self.workspace.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.workspace.close()
        self.workspace.deleteLater()
        self.model.dispose()
        self.app.processEvents()

    def test_navigation_construction_is_inert_and_plan_is_explicit(self) -> None:
        self.assertEqual(self.presenter.plan_calls, [])
        self.assertFalse(self.workspace._run_button.isEnabled())
        self.assertEqual(self.workspace._mode.property("ui2Role"), "utility-select")
        self.assertEqual(self.workspace._discard_blocks.property("ui2Role"), "range-control")
        self.workspace._plan_button.click()
        self.assertEqual(len(self.presenter.plan_calls), 1)
        requested = self.presenter.plan_calls[0]
        self.assertEqual(requested.mode, SweepMode.BALANCED)
        self.assertEqual(requested.start_hz, 400_000_000.0)
        self.assertEqual(requested.stop_hz, 6_000_000_000.0)

    def test_plan_then_explicit_run_uses_only_matching_frozen_configuration(self) -> None:
        self.workspace._plan_button.click()
        configuration = self.presenter.plan_calls[-1]
        self.presenter.plan_ready.emit(_plan(configuration))
        self.assertTrue(self.workspace._run_button.isEnabled())
        self.workspace._run_button.click()
        self.assertEqual(self.presenter.execute_calls, [configuration])
        self.workspace._stop_mhz.setValue(5_999.0)
        self.assertFalse(self.workspace._run_button.isEnabled())
        self.workspace._run_button.click()
        self.assertEqual(len(self.presenter.plan_calls), 1)
        self.assertEqual(len(self.presenter.execute_calls), 1)

    def test_progress_result_and_export_show_only_published_quality_not_fake_spectrum(self) -> None:
        self.workspace._plan_button.click()
        configuration = self.presenter.plan_calls[-1]
        plan = _plan(configuration)
        self.presenter.plan_ready.emit(plan)
        self.presenter.busy_changed.emit(True)
        self.presenter.progress_changed.emit(SweepProgress(SweepState.RUNNING, 1, 2, stage="acquire"))
        self.assertTrue(self.workspace._cancel_button.isEnabled())
        self.workspace._cancel_button.click()
        self.assertEqual(self.presenter.cancel_calls, 1)
        self.presenter.result_ready.emit(
            SweepResult(
                state=SweepState.COMPLETED,
                plan=plan,
                duration_seconds=2.4,
                quality=SweepQuality(0, 0.8, 100.0, "published quality only"),
            )
        )
        self.presenter.busy_changed.emit(False)
        self.assertIn(text("sweep.state.completed"), self.workspace._result_summary.text())
        self.assertNotIn("spectrum", self.workspace._result_summary.text().casefold())
        self.assertTrue(self.workspace._export_button.isEnabled())
        self.workspace._export_path.set_value("summary.json")
        self.workspace._export_button.click()
        self.assertEqual(self.presenter.export_calls[0][1], Path("summary.json"))

    def test_presenter_error_is_visible_without_an_invented_recovery_action(self) -> None:
        self.presenter.task_failed.emit("sweep adapter failed")
        self.assertTrue(self.workspace._error_banner.isVisible())
        self.assertIn("sweep adapter failed", self.workspace._error_banner.accessibleDescription())
        self.assertFalse(self.workspace._error_banner._action.isVisible())

    def test_definition_is_external_and_factory_is_lazy(self) -> None:
        definition = sweep_workspace_definition(self.model)
        self.assertEqual(definition.workspace_id, "sweep")
        self.assertEqual(definition.label, "Обзор")
        page = definition.workspace_factory()
        self.assertIsInstance(page, SweepWorkspaceV2)
        page.close()
        page.deleteLater()
