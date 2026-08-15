"""R11-Z Qt-free tinySA application and peak-preserving presentation tests."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import cast

import numpy as np

from sdr_monitor.application.tinysa_analyzer import (
    TinySaAnalyzerApplicationService,
    TinySaAnalyzerOperationError,
    TinySaAnalyzerPhase,
    TinySaAnalyzerReason,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaModel
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
)
from sdr_monitor.services.tinysa_sweep_policy import TinySaSweepAccuracy
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    R11W_TINYSA_SETTINGS_CONFIRMATION,
    TinySaSettingsApplyResult,
    TinySaSettingsApplyStatus,
    TinySaSweepSettingsPlan,
)
from sdr_monitor.services.tinysa_trace_parser import TinySaSpectrumTrace
from sdr_monitor.services.tinysa_trace_presentation import reduce_tinysa_trace_for_width

ROOT = Path(__file__).resolve().parents[1]
APPLICATION_SOURCE = ROOT / "sdr_monitor/application/tinysa_analyzer.py"
PRESENTATION_SOURCE = ROOT / "sdr_monitor/services/tinysa_trace_presentation.py"


def _trace(points: int = 10_001) -> TinySaSpectrumTrace:
    start = 87_500_000.0
    stop = 108_000_000.0
    step = (int(stop) - int(start)) // points
    values = np.full(points, -105.0, dtype=np.float32)
    values[17] = -123.0
    values[points // 2] = -72.0
    values[-9] = -118.0
    return TinySaSpectrumTrace(
        model=TinySaModel.ULTRA,
        start_frequency_hz=start,
        stop_frequency_hz=stop,
        host_timestamp_ns=1,
        scanraw_zero_offset_db=174.0,
        frequencies_hz=start + np.arange(points, dtype=np.float64) * float(step),
        values_dbm=values,
    )


def _collection() -> TinySaTraceCollection:
    trace = _trace()
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
            raise OSError("secret COM route")
        return _collection()


class _SettingsExecutor:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[TinySaSweepSettingsPlan, str]] = []

    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        self.calls.append((plan, confirmation))
        if self.fail:
            raise OSError("secret settings route")
        return TinySaSettingsApplyResult(
            status=TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED,
            commands=plan.commands,
            warnings=plan.warnings,
            command_acknowledgements=len(plan.commands),
            port_factory_invoked=True,
            port_closed=True,
        )


def _request() -> TinySaScanRawRequest:
    return TinySaScanRawRequest(
        model=TinySaModel.ULTRA,
        start_frequency_hz=87_500_000,
        stop_frequency_hz=108_000_000,
        points=10_001,
        deadline_seconds=120.0,
    )


class TinySaTracePresentationTests(unittest.TestCase):
    def test_reduction_preserves_bucket_extrema_and_full_trace_ownership(self) -> None:
        trace = _trace()
        reduced = reduce_tinysa_trace_for_width(trace, 640)

        self.assertEqual(reduced.source_point_count, 10_001)
        self.assertLessEqual(reduced.display_point_count, 1_280)
        self.assertIn(17, reduced.source_indices)
        self.assertIn(5_000, reduced.source_indices)
        self.assertIn(9_992, reduced.source_indices)
        self.assertEqual(float(np.min(reduced.values_dbm)), -123.0)
        self.assertEqual(float(np.max(reduced.values_dbm)), -72.0)
        self.assertFalse(reduced.values_dbm.flags.writeable)
        self.assertEqual(trace.values_dbm.size, 10_001)

    def test_reduction_is_exact_when_width_exceeds_source_count(self) -> None:
        trace = _trace(101)
        reduced = reduce_tinysa_trace_for_width(trace, 512)

        self.assertEqual(reduced.display_point_count, 101)
        np.testing.assert_array_equal(reduced.source_indices, np.arange(101))
        np.testing.assert_array_equal(reduced.values_dbm, trace.values_dbm)

    def test_invalid_width_fails_before_allocating_unbounded_output(self) -> None:
        for width in (0, 4_097):
            with self.subTest(width=width), self.assertRaises(ValueError):
                reduce_tinysa_trace_for_width(_trace(101), cast(int, width))
        with self.assertRaises(TypeError):
            reduce_tinysa_trace_for_width(_trace(101), cast(int, True))


class TinySaAnalyzerApplicationTests(unittest.TestCase):
    def test_construction_is_inert_and_collection_retains_full_trace(self) -> None:
        collector = _Collector()
        settings = _SettingsExecutor()
        service = TinySaAnalyzerApplicationService(collector, settings)

        self.assertEqual(collector.calls, [])
        self.assertEqual(settings.calls, [])
        self.assertEqual(service.current().phase, TinySaAnalyzerPhase.READY)

        result = service.collect_trace(_request(), 800)

        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(settings.calls, [])
        self.assertEqual(result.snapshot.phase, TinySaAnalyzerPhase.TRACE_READY)
        self.assertEqual(result.presentation.source_point_count, 10_001)
        self.assertLessEqual(result.presentation.display_point_count, 1_600)
        retained = service.analytical_trace()
        self.assertIsNotNone(retained)
        self.assertEqual(cast(TinySaSpectrumTrace, retained).values_dbm.size, 10_001)
        self.assertAlmostEqual(result.observed_points_per_second, 500.05)

    def test_settings_require_review_then_separate_confirmation(self) -> None:
        collector = _Collector()
        settings = _SettingsExecutor()
        service = TinySaAnalyzerApplicationService(collector, settings)

        self.assertIsNone(service.stage_settings(TinySaSweepSettingsPlan()))
        self.assertEqual(settings.calls, [])
        self.assertIs(service.current().reason, TinySaAnalyzerReason.NO_SETTINGS_CHANGES)

        plan = TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.FAST)
        review = service.stage_settings(plan)
        self.assertIsNotNone(review)
        self.assertEqual(cast(object, review).commands, ("sweep fast",))  # type: ignore[attr-defined]
        self.assertEqual(service.current().phase, TinySaAnalyzerPhase.SETTINGS_REVIEW)
        self.assertEqual(settings.calls, [])

        cancelled = service.confirm_settings(user_confirmed=False)
        self.assertIs(cancelled.reason, TinySaAnalyzerReason.SETTINGS_CANCELLED)
        self.assertEqual(settings.calls, [])

        service.stage_settings(plan)
        applied = service.confirm_settings(user_confirmed=True)
        self.assertIs(applied.reason, TinySaAnalyzerReason.SETTINGS_ACKNOWLEDGED_UNVERIFIED)
        self.assertEqual(settings.calls, [(plan, R11W_TINYSA_SETTINGS_CONFIRMATION)])
        self.assertFalse(applied.can_confirm_settings)

    def test_failures_are_redacted_and_do_not_retry(self) -> None:
        collector = _Collector(fail=True)
        service = TinySaAnalyzerApplicationService(collector, _SettingsExecutor())
        with self.assertRaisesRegex(TinySaAnalyzerOperationError, "collection failed") as captured:
            service.collect_trace(_request(), 640)
        self.assertNotIn("secret", str(captured.exception).casefold())
        self.assertEqual(len(collector.calls), 1)
        self.assertEqual(service.current().phase, TinySaAnalyzerPhase.FAULTED)
        self.assertTrue(service.current().can_collect)

        settings = _SettingsExecutor(fail=True)
        setting_service = TinySaAnalyzerApplicationService(_Collector(), settings)
        setting_service.stage_settings(
            TinySaSweepSettingsPlan(accuracy=TinySaSweepAccuracy.NORMAL)
        )
        with self.assertRaisesRegex(TinySaAnalyzerOperationError, "settings application failed"):
            setting_service.confirm_settings(user_confirmed=True)
        self.assertEqual(len(settings.calls), 1)
        self.assertEqual(setting_service.current().phase, TinySaAnalyzerPhase.FAULTED)

    def test_sources_keep_standalone_and_qt_device_boundaries(self) -> None:
        application = APPLICATION_SOURCE.read_text(encoding="utf-8").casefold()
        presentation = PRESENTATION_SOURCE.read_text(encoding="utf-8").casefold()
        for forbidden in (
            "esw_dfl",
            "pyside",
            "qwidget",
            "serial(",
            "com31",
            "saveconfig",
            "reset",
            "firmware_write",
            "raw_iq",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, application + presentation)


if __name__ == "__main__":
    unittest.main()
