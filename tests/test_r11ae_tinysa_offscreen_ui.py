"""R11-AE offscreen UI telemetry tests; never accepted as visible evidence."""

from __future__ import annotations

import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.application.tinysa_analyzer import TinySaAnalyzerApplicationService
from sdr_monitor.r11ae_tinysa_visible_ui_evidence import R11AEVisibleUiProfile
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaScanRawRequest, TinySaTraceCollection
from sdr_monitor.services.tinysa_source_composition import (
    TinySaIdentityAssurance,
    TinySaVerifiedSource,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import TinySaSweepSettingsPlan
from sdr_monitor.services.tinysa_trace_parser import TinySaSpectrumTrace
from sdr_monitor.ui.presenters.tinysa_analyzer_presenter import TinySaAnalyzerPresenter
from sdr_monitor.ui.r11ae_tinysa_display_preflight import (
    collect_r11ae_windows_display_preflight,
)
from sdr_monitor.ui.r11ae_tinysa_visible_witness import R11AEVisibleUiWitness
from sdr_monitor.ui.tinysa_trace_canvas import TinySaTraceCanvasMetrics
from sdr_monitor.ui.workspaces.tinysa_analyzer import TinySaAnalyzerWorkspace


def _app() -> QApplication:
    existing = QApplication.instance()
    return existing if isinstance(existing, QApplication) else QApplication([])


def _wait(predicate: object, timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _app().processEvents()
        if callable(predicate) and predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out waiting for R11-AE offscreen trace")


class _Collector:
    def __init__(self) -> None:
        self.calls = 0
        self.last_request: TinySaScanRawRequest | None = None

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        self.calls += 1
        self.last_request = request
        points = request.points
        step = (request.stop_frequency_hz - request.start_frequency_hz) // points
        values = np.linspace(-120.0, -55.0, points, dtype=np.float32)
        trace = TinySaSpectrumTrace(
            model=TinySaModel.ULTRA,
            start_frequency_hz=float(request.start_frequency_hz),
            stop_frequency_hz=float(request.stop_frequency_hz),
            host_timestamp_ns=1,
            scanraw_zero_offset_db=174.0,
            frequencies_hz=request.start_frequency_hz + np.arange(points) * step,
            values_dbm=values,
        )
        return TinySaTraceCollection(
            trace=trace,
            command_bytes=len(request.command),
            response_bytes_read=request.expected_frame_bytes,
            discarded_prefix_bytes=0,
            read_calls=10,
            zero_response_bytes=16,
            zero_read_calls=1,
            scanraw_zero_offset_db=174.0,
            elapsed_seconds=20.0,
            port_closed=True,
        )


class _NoSettings:
    def apply(self, plan: TinySaSweepSettingsPlan, *, confirmation: str) -> object:
        del plan, confirmation
        raise AssertionError("R11-AE offscreen path must not apply settings")


class R11AETinySaOffscreenUiTests(unittest.TestCase):
    def setUp(self) -> None:
        _app()
        self.collector = _Collector()
        self.service = TinySaAnalyzerApplicationService(self.collector, _NoSettings())
        self.presenter = TinySaAnalyzerPresenter(self.service)
        self.workspace = TinySaAnalyzerWorkspace(self.presenter)

    def tearDown(self) -> None:
        self.workspace.shutdown()
        self.workspace.close()
        self.workspace.deleteLater()

    def test_maximum_trace_paints_bounded_data_at_supported_logical_geometries(self) -> None:
        for width, height in ((1_024, 640), (1_280, 900), (1_440, 900)):
            with self.subTest(geometry=(width, height)):
                self.workspace.resize(width, height)
                self.workspace.show()
                _app().processEvents()
                self.workspace._acquire.click()
                _wait(lambda: self.workspace._canvas.presentation is not None)
                self.workspace._canvas.grab()
                _app().processEvents()
                presentation = self.workspace._canvas.presentation
                metrics = self.workspace._canvas.metrics
                self.assertIsNotNone(presentation)
                self.assertEqual(presentation.source_point_count, 10_001)  # type: ignore[union-attr]
                self.assertLessEqual(
                    presentation.display_point_count,  # type: ignore[union-attr]
                    2 * presentation.pixel_width,  # type: ignore[union-attr]
                )
                self.assertGreaterEqual(metrics.trace_paint_events, 1)
                self.assertGreater(metrics.painted_points_total, 0)
                self.assertEqual(self.service.analytical_trace().values_dbm.size, 10_001)  # type: ignore[union-attr]
                self.workspace._canvas.clear()

    def test_worker_to_gui_dispatch_is_counted_and_summary_disclaims_lps(self) -> None:
        self.workspace.resize(1_280, 900)
        self.workspace.show()
        self.workspace._acquire.click()
        _wait(lambda: self.presenter.metrics.completion_dispatches == 1)
        _wait(lambda: self.workspace._canvas.presentation is not None)

        metrics = self.presenter.metrics
        self.assertEqual(metrics.operations_started, 1)
        self.assertEqual(metrics.completion_dispatches, 1)
        self.assertGreaterEqual(metrics.last_worker_to_gui_latency_ns, 0)
        self.assertGreaterEqual(
            metrics.maximum_worker_to_gui_latency_ns,
            metrics.last_worker_to_gui_latency_ns,
        )
        summary = self.workspace._trace_summary.text().casefold()
        self.assertIn("not lps", summary)
        self.assertIn("not lps", self.workspace._trace_summary.accessibleDescription().casefold())
        self.assertEqual(self.collector.calls, 1)

    def test_workspace_forwards_scalar_trace_paint_completion(self) -> None:
        observed: list[TinySaTraceCanvasMetrics] = []
        self.workspace.trace_painted.connect(observed.append)
        self.workspace.resize(1_280, 900)
        self.workspace.show()
        self.workspace._acquire.click()
        _wait(lambda: bool(observed))

        metrics = observed[-1]
        self.assertEqual(metrics, self.workspace.canvas_metrics)
        self.assertGreaterEqual(metrics.trace_paint_events, 1)
        self.assertGreater(metrics.painted_points_total, 0)

    def test_visible_evidence_lock_uses_one_immutable_trace_and_disables_settings(self) -> None:
        source = TinySaVerifiedSource(
            source_id="tinysa-0123456789abcdef",
            identity_key="sha256:" + "a" * 64,
            label="tinySA Ultra",
            model=TinySaModel.ULTRA,
            firmware_fingerprint="sha256:" + "b" * 64,
            identity_assurance=TinySaIdentityAssurance.USB_SERIAL,
        )
        request = TinySaScanRawRequest(
            TinySaModel.ULTRA,
            87_500_000,
            108_000_000,
            10_001,
            deadline_seconds=120.0,
        )
        self.workspace.set_source_identity(source)
        self.workspace.lock_unchanged_trace_for_visible_evidence(request, 1_024)

        self.assertTrue(self.workspace.visible_trace_locked)
        self.assertFalse(self.workspace._start_mhz.isEnabled())
        self.assertFalse(self.workspace._stop_mhz.isEnabled())
        self.assertFalse(self.workspace._points.isEnabled())
        self.assertFalse(self.workspace._review_settings.isEnabled())
        self.assertFalse(self.workspace._apply_settings.isEnabled())
        # Programmatic value changes cannot change the immutable request retained by the lock.
        self.workspace._start_mhz.setValue(300.0)
        self.workspace._acquire.click()
        _wait(lambda: self.workspace.last_trace_result is not None)

        self.assertEqual(self.collector.calls, 1)
        self.assertEqual(self.collector.last_request, request)
        self.assertEqual(self.presenter.metrics.trace_operations_started, 1)
        self.assertEqual(self.presenter.metrics.settings_review_operations_started, 0)
        self.assertEqual(self.presenter.metrics.settings_confirmation_operations_started, 0)

    def test_visible_witness_rejects_offscreen_platform_before_observation(self) -> None:
        witness = R11AEVisibleUiWitness(
            R11AEVisibleUiProfile(100),
            draft_preflight_sha256="a" * 64,
            ready_preflight_sha256="b" * 64,
            display_preflight_sha256="c" * 64,
            source_id="tinysa-0123456789abcdef",
            identity_assurance="usb_serial",
        )
        with self.assertRaisesRegex(RuntimeError, "visible Windows Qt platform"):
            witness.attach(self.workspace)

    def test_display_preflight_rejects_offscreen_before_a_window_is_created(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "visible Windows Qt platform"):
            collect_r11ae_windows_display_preflight(R11AEVisibleUiProfile(100))


if __name__ == "__main__":
    unittest.main()
