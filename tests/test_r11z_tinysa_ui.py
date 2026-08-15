"""R11-Z offscreen presenter/workspace tests; no serial or device action."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QPushButton

from sdr_monitor.application.tinysa_analyzer import (
    TinySaAnalyzerApplicationService,
    TinySaAnalyzerPhase,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from sdr_monitor.services.tinysa_sweep_policy import TinySaSweepAccuracy
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSettingsApplyStatus,
    TinySaSweepSettingsPlan,
)
from sdr_monitor.services.tinysa_trace_parser import TinySaSpectrumTrace
from sdr_monitor.ui.presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
from sdr_monitor.ui.workspaces.tinysa_analyzer import TinySaAnalyzerWorkspace

ROOT = Path(__file__).resolve().parents[1]
PRESENTER_SOURCE = ROOT / "sdr_monitor/ui/presenters/tinysa_analyzer_presenter.py"
WORKSPACE_SOURCE = ROOT / "sdr_monitor/ui/workspaces/tinysa_analyzer.py"
CANVAS_SOURCE = ROOT / "sdr_monitor/ui/tinysa_trace_canvas.py"


def _app() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _wait(predicate: object, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _app().processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for tinySA presenter result")


def _request() -> TinySaScanRawRequest:
    return TinySaScanRawRequest(
        model=TinySaModel.ULTRA,
        start_frequency_hz=87_500_000,
        stop_frequency_hz=108_000_000,
        points=10_001,
        deadline_seconds=120.0,
    )


def _collection() -> TinySaTraceCollection:
    points = 10_001
    start = 87_500_000.0
    stop = 108_000_000.0
    step = (int(stop) - int(start)) // points
    values = np.full(points, -108.0, dtype=np.float32)
    values[100] = -122.0
    values[5_000] = -75.0
    trace = TinySaSpectrumTrace(
        model=TinySaModel.ULTRA,
        start_frequency_hz=start,
        stop_frequency_hz=stop,
        host_timestamp_ns=1,
        scanraw_zero_offset_db=174.0,
        frequencies_hz=start + np.arange(points, dtype=np.float64) * float(step),
        values_dbm=values,
    )
    return TinySaTraceCollection(
        trace=trace,
        command_bytes=35,
        response_bytes_read=30_041,
        discarded_prefix_bytes=36,
        read_calls=329,
        zero_response_bytes=41,
        zero_read_calls=1,
        scanraw_zero_offset_db=174.0,
        elapsed_seconds=20.0,
        port_closed=True,
    )


class _Collector:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[TinySaScanRawRequest] = []

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        self.calls.append(request)
        if self.fail:
            raise OSError("secret COM31 route")
        return _collection()


class _SettingsExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[TinySaSweepSettingsPlan, str]] = []

    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        self.calls.append((plan, confirmation))
        return TinySaSettingsApplyResult(
            status=TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED,
            commands=plan.commands,
            warnings=plan.warnings,
            command_acknowledgements=len(plan.commands),
            port_factory_invoked=True,
            port_closed=True,
        )


class TinySaAnalyzerPresenterTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.collector = _Collector()
        self.settings = _SettingsExecutor()
        self.service = TinySaAnalyzerApplicationService(self.collector, self.settings)
        self.presenter = TinySaAnalyzerPresenter(self.service)

    def tearDown(self) -> None:
        self.presenter.shutdown()
        self.presenter.deleteLater()

    def test_worker_is_inert_serial_and_rejects_concurrent_ui_operation(self) -> None:
        results: list[object] = []
        self.presenter.trace_ready.connect(results.append)
        self.assertEqual(self.collector.calls, [])
        self.assertEqual(self.settings.calls, [])

        self.presenter.collect_trace(_request(), 640)
        self.presenter.collect_trace(_request(), 640)
        _wait(lambda: bool(results))

        result = results[0]
        self.assertEqual(result.presentation.source_point_count, 10_001)  # type: ignore[attr-defined]
        self.assertLessEqual(result.presentation.display_point_count, 1_280)  # type: ignore[attr-defined]
        self.assertEqual(len(self.collector.calls), 1)
        self.assertEqual(self.presenter.metrics.operations_started, 1)
        self.assertEqual(self.presenter.metrics.operations_rejected_while_busy, 1)

    def test_settings_review_and_confirmation_are_two_distinct_operations(self) -> None:
        reviews: list[object] = []
        self.presenter.settings_review_ready.connect(reviews.append)
        plan = TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST)

        self.presenter.stage_settings(plan)
        _wait(lambda: bool(reviews))
        self.assertEqual(self.settings.calls, [])
        self.assertEqual(self.presenter.current_snapshot.phase, TinySaAnalyzerPhase.SETTINGS_REVIEW)

        self.presenter.confirm_settings(user_confirmed=False)
        _wait(lambda: self.presenter.current_snapshot.phase is TinySaAnalyzerPhase.READY)
        self.assertEqual(self.settings.calls, [])

        self.presenter.stage_settings(plan)
        _wait(lambda: self.presenter.current_snapshot.phase is TinySaAnalyzerPhase.SETTINGS_REVIEW)
        self.presenter.confirm_settings(user_confirmed=True)
        _wait(lambda: self.presenter.current_snapshot.phase is TinySaAnalyzerPhase.READY)
        self.assertEqual(len(self.settings.calls), 1)


class TinySaAnalyzerWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.collector = _Collector()
        self.settings = _SettingsExecutor()
        self.service = TinySaAnalyzerApplicationService(self.collector, self.settings)
        self.presenter = TinySaAnalyzerPresenter(self.service)
        self.workspace = TinySaAnalyzerWorkspace(self.presenter)
        self.workspace.resize(1_024, 900)
        self.workspace.show()
        _app().processEvents()

    def tearDown(self) -> None:
        self.workspace.shutdown()
        self.workspace.close()
        self.workspace.deleteLater()

    def test_workspace_defaults_are_inert_accessible_and_bound_to_10001(self) -> None:
        self.assertEqual(self._calls(), (0, 0))
        self.assertEqual(self.workspace._points.maximum(), 10_001)
        self.assertEqual(self.workspace._points.value(), 10_001)
        self.assertTrue(self.workspace._empty.isVisible())
        for button in self.workspace.findChildren(QPushButton):
            with self.subTest(button=button.text()):
                self.assertTrue(button.text())
                self.assertTrue(button.accessibleDescription())

        self.workspace._acquire.click()
        _wait(lambda: self.workspace._canvas.presentation is not None)

        presentation = self.workspace._canvas.presentation
        assert presentation is not None
        self.assertEqual(presentation.source_point_count, 10_001)
        self.assertLessEqual(presentation.display_point_count, 2 * presentation.pixel_width)
        self.assertEqual(self.service.analytical_trace().values_dbm.size, 10_001)  # type: ignore[union-attr]
        self.assertFalse(self.workspace._empty.isVisible())
        self.assertFalse(self.workspace.grab().isNull())
        self.assertEqual(self._calls(), (1, 0))

    def test_review_button_never_applies_and_cancel_remains_transport_free(self) -> None:
        fast_index = self.workspace._accuracy.findData(TinySaSweepAccuracy.FAST.value)
        self.workspace._accuracy.setCurrentIndex(fast_index)
        self.workspace._review_settings.click()
        _wait(lambda: self.workspace._apply_settings.isEnabled())
        self.assertEqual(self._calls(), (0, 0))
        self.assertIn("not fully readable or restorable", self.workspace._settings_detail.text())

        self.presenter.confirm_settings(user_confirmed=False)
        _wait(lambda: self.presenter.current_snapshot.phase is TinySaAnalyzerPhase.READY)
        self.assertEqual(self._calls(), (0, 0))

    def test_error_state_is_visible_and_redacted(self) -> None:
        self.workspace.shutdown()
        self.workspace.close()
        failing_service = TinySaAnalyzerApplicationService(_Collector(fail=True), self.settings)
        self.presenter = TinySaAnalyzerPresenter(failing_service)
        self.workspace = TinySaAnalyzerWorkspace(self.presenter)
        self.workspace.show()
        self.workspace._acquire.click()
        _wait(lambda: self.workspace._error.isVisible())
        self.assertEqual(self.workspace._error.text(), "tinySA operation failed")
        self.assertNotIn("com31", self.workspace._error.text().casefold())

    def test_ui_sources_do_not_import_dfl_or_open_devices(self) -> None:
        source = "\n".join(
            path.read_text(encoding="utf-8").casefold()
            for path in (PRESENTER_SOURCE, WORKSPACE_SOURCE, CANVAS_SOURCE)
        )
        for forbidden in (
            "esw_dfl",
            "serial(",
            "open(",
            "scanraw ",
            "saveconfig",
            "reset",
            "firmware_write",
            "raw_iq",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def _calls(self) -> tuple[int, int]:
        return len(self.collector.calls), len(self.settings.calls)


if __name__ == "__main__":
    unittest.main()
