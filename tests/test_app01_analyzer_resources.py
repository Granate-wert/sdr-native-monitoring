"""Shared bounded numeric-buffer accounting, not a process-memory benchmark."""
import unittest

from sdr_monitor.domain.analyzer_resources import (
    AnalyzerGeometryPreflight,
    estimate_analyzer_reduced,
)


class AnalyzerResourceTests(unittest.TestCase):
    def test_all_configuration_paths_reject_excessive_resources_before_port_calls(self):
        from unittest.mock import Mock, patch
        from dataclasses import replace
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.domain.live import LiveConfiguration, DEFAULT_LIVE_RESOURCE_BUDGET

        # The immutable configuration already validates its construction-time
        # budget. Exercise stricter application admission without forging it.
        oversized = LiveConfiguration()
        budget = replace(DEFAULT_LIVE_RESOURCE_BUDGET, max_total_bytes=1)
        for command in ("apply_configuration", "start_with_configuration", "reconfigure"):
            with self.subTest(command=command):
                port = Mock()
                port.is_running.return_value = True
                application = LiveSessionApplicationService(port)
                with patch("sdr_monitor.application.live_session.DEFAULT_LIVE_RESOURCE_BUDGET", budget), self.assertRaises(ValueError):
                    getattr(application, command)(oversized)
                self.assertEqual(port.mock_calls, [])

    def test_reconfigure_does_not_apply_or_restart_when_stop_is_unconfirmed(self):
        from unittest.mock import Mock
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.domain.live import LiveConfiguration, LiveSnapshot, LiveSessionState

        port = Mock()
        port.is_running.return_value = True
        port.stop.return_value = LiveSnapshot(
            generation=1, sequence=1, state=LiveSessionState.RUNNING,
        )
        application = LiveSessionApplicationService(port)
        result = application.reconfigure(LiveConfiguration())
        self.assertIs(result, port.stop.return_value)
        port.stop.assert_called_once_with()
        port.apply_configuration.assert_not_called()
        port.start.assert_not_called()

    def test_live_application_preflight_has_no_port_calls(self):
        from unittest.mock import Mock
        from sdr_monitor.application.live_session import LiveSessionApplicationService
        from sdr_monitor.domain.live import LiveConfiguration
        port = Mock()
        application = LiveSessionApplicationService(port)
        result = application.preflight_configuration(LiveConfiguration(
            center_hz=100e6, sample_rate_hz=3e6, fft_size=1024,
        ))
        self.assertEqual(result.mode, "rtbw")
        self.assertEqual(result.output_spacing_hz, 3e6 / 1024)
        self.assertEqual(result.reduced.output_bins, 1024)
        self.assertIsNone(result.rbw_hz)
        self.assertIsNone(result.enbw_hz)
        self.assertEqual(port.mock_calls, [])

    def test_geometry_preflight_distinguishes_grid_spacing_from_unknown_rbw_enbw(self):
        reduced = estimate_analyzer_reduced("sweep", 4096, 4, physical_fft_size=8192, segment_count=1)
        result = AnalyzerGeometryPreflight(
            "sweep", 61.44e6, 8192, 36e6 / 4096, 1, 34e6, reduced,
            usable_window_hz=36e6,
            analysis_bins_per_usable_window=4096,
            physical_bin_spacing_hz=61.44e6 / 8192,
        )
        self.assertEqual(result.output_spacing_hz, 36e6 / 4096)
        self.assertEqual(result.physical_bin_spacing_hz, 61.44e6 / 8192)
        self.assertIsNone(result.rbw_hz)
        self.assertIsNone(result.enbw_hz)

    def test_geometry_preflight_rejects_malformed_frozen_scalars(self):
        reduced = estimate_analyzer_reduced("rtbw", 1024, 4)
        for kwargs in (
            {"sample_rate_hz": True},
            {"output_spacing_hz": float("nan")},
            {"segment_count": False},
            {"physical_bin_spacing_hz": -1.0},
            {"rbw_hz": float("inf")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                AnalyzerGeometryPreflight(
                    mode="rtbw",
                    sample_rate_hz=kwargs.get("sample_rate_hz", 3e6),
                    physical_fft_size=1024,
                    output_spacing_hz=kwargs.get("output_spacing_hz", 3e6 / 1024),
                    segment_count=kwargs.get("segment_count", 1),
                    segment_stride_hz=0.0,
                    reduced=reduced,
                    physical_bin_spacing_hz=kwargs.get("physical_bin_spacing_hz", 0.0),
                    rbw_hz=kwargs.get("rbw_hz"),
                )

    def test_rtbw_preserves_existing_accounting(self):
        estimate = estimate_analyzer_reduced("rtbw", 4096, 8)
        self.assertEqual(estimate.total_bytes, 4096 * 16 * 8)
        self.assertEqual(estimate.assembly_bytes, 0)
        self.assertEqual(estimate.retained_segment_bytes, 0)

    def test_sweep_includes_working_and_retained_input_arrays(self):
        estimate = estimate_analyzer_reduced("sweep", 10000, 7,
                                            physical_fft_size=4096, segment_count=8)
        self.assertEqual(estimate.output_bytes, 10000 * 20 * 7)
        self.assertEqual(estimate.assembly_bytes, 10000 * 28)
        self.assertEqual(estimate.retained_segment_bytes, 8 * 4096 * 16 + 4096 * 8)
        self.assertEqual(estimate.total_bytes, sum((estimate.output_bytes,
                         estimate.assembly_bytes, estimate.retained_segment_bytes)))

    def test_invalid_geometry_fails_before_allocation(self):
        for mode, bins, retained in (("unknown", 1024, 4), ("rtbw", True, 4),
                                     ("rtbw", 1, 4), ("sweep", 2000001, 4),
                                     ("rtbw", 1024, 0)):
            with self.subTest(mode=mode, bins=bins), self.assertRaises(ValueError):
                estimate_analyzer_reduced(mode, bins, retained)
        with self.assertRaises(ValueError):
            estimate_analyzer_reduced("sweep", 1024, 4, physical_fft_size=4096, segment_count=65)
