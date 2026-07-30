from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.spectrogram import SpectrogramRow
from esw_dfl.time_gated_power import (
    ActivityDetectionConfig,
    ActivityDetectionService,
    ActivityThresholdMode,
    ChannelPowerRequest,
    ChannelPowerService,
    ChannelPowerSeries,
    ChannelPowerMode,
    PowerSemantics,
    SmoothingMode,
    TimeGatedChannelPowerService,
    dbm_to_mw,
)


class ChannelPowerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = ChannelPowerService()
        self.frequencies = np.array([0.0, 10.0, 20.0])
        self.values = np.full(3, -30.0)

    def test_power_per_bin_and_partial_bins(self) -> None:
        mw, dbm, approximate, _ = self.service.frame_power(
            self.frequencies, self.values, -5.0, 25.0, PowerSemantics.POWER_PER_BIN
        )
        self.assertAlmostEqual(mw, 0.003)
        self.assertAlmostEqual(dbm, -30.0 + 10 * np.log10(3))
        self.assertFalse(approximate)
        partial_mw, *_ = self.service.frame_power(
            self.frequencies, self.values, 0.0, 10.0, PowerSemantics.POWER_PER_BIN
        )
        self.assertAlmostEqual(partial_mw, 0.001)

    def test_psd_per_hz_multiplies_by_overlap_width(self) -> None:
        mw, dbm, *_ = self.service.frame_power(
            self.frequencies, self.values, -5.0, 25.0, PowerSemantics.PSD_PER_HZ
        )
        self.assertAlmostEqual(mw, 0.03)
        self.assertAlmostEqual(dbm, float(10 * np.log10(0.03)))

    def test_rbw_filtered_power_and_unknown_quality(self) -> None:
        mw, *_ = self.service.frame_power(
            self.frequencies, self.values, -5.0, 25.0,
            PowerSemantics.RBW_FILTERED_POWER, rbw_hz=10.0,
        )
        self.assertAlmostEqual(mw, 0.003)
        _, _, approximate, warnings = self.service.frame_power(
            self.frequencies, self.values, -5.0, 25.0, PowerSemantics.UNKNOWN
        )
        self.assertTrue(approximate)
        self.assertTrue(warnings)

    def test_nan_inf_and_outside_band_are_not_valid_power(self) -> None:
        mw, dbm, *_ = self.service.frame_power(
            self.frequencies, np.array([np.nan, -30.0, np.inf]), 5.0, 15.0,
            PowerSemantics.POWER_PER_BIN,
        )
        self.assertTrue(np.isfinite(mw))
        self.assertTrue(np.isfinite(dbm))
        outside, *_ = self.service.frame_power(
            self.frequencies, self.values, 100.0, 200.0, PowerSemantics.POWER_PER_BIN
        )
        self.assertTrue(np.isnan(outside))


class ActivityDetectionTests(unittest.TestCase):
    @staticmethod
    def series(values: list[float]) -> ChannelPowerSeries:
        array = np.asarray(values, dtype=np.float32)
        return ChannelPowerSeries(
            np.arange(array.size), np.arange(array.size, dtype=float), dbm_to_mw(array),
            array, np.isfinite(array),
        )

    def test_hysteresis_avoids_chatter(self) -> None:
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-69.0,
            threshold_on_offset_db=10.0,
            threshold_off_offset_db=6.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
            min_inactive_frames=1,
            max_gap_frames=0,
            merge_gap_frames=0,
        )
        result = ActivityDetectionService().detect(
            self.series([-80, -70, -65, -68, -72, -74, -80]), config
        )
        np.testing.assert_array_equal(
            result.effective_activity_mask,
            np.array([False, False, True, True, True, False, False]),
        )

    def test_minimum_activity_and_short_gap_fill(self) -> None:
        service = ActivityDetectionService()
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-50.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=2,
            min_inactive_frames=1,
            max_gap_frames=1,
            merge_gap_frames=0,
        )
        result = service.detect(
            self.series([-80, -20, -80, -20, -20, -80, -20, -20, -80]), config
        )
        self.assertFalse(result.effective_activity_mask[1])
        self.assertTrue(result.effective_activity_mask[5])

    def test_auto_idle_estimate_and_thresholds(self) -> None:
        values = [-82.0] * 20 + [-40.0] * 10
        result = ActivityDetectionService().detect(
            self.series(values), ActivityDetectionConfig(min_active_frames=1)
        )
        self.assertIsNotNone(result.idle_estimate)
        self.assertAlmostEqual(result.idle_estimate.median_idle_dbm, -82.0)
        self.assertAlmostEqual(result.threshold_on_dbm, -72.0)
        self.assertEqual(int(np.count_nonzero(result.effective_activity_mask)), 10)

    def test_duration_constraints_and_percentile_mode(self) -> None:
        series = self.series([-80, -20, -80, -80, -20, -20, -80])
        series.timestamps_s = np.array([0.0, 0.01, 0.02, 0.10, 0.11, 0.25, 0.26])
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.PERCENTILE,
            idle_percentile=20.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
            min_inactive_frames=1,
            min_active_duration_s=0.05,
            max_gap_frames=0,
            merge_gap_frames=0,
        )
        result = ActivityDetectionService().detect(series, config)
        self.assertFalse(result.effective_activity_mask[1])
        self.assertTrue(result.effective_activity_mask[4])
        self.assertTrue(result.effective_activity_mask[5])


class TimeGatedSummaryTests(unittest.TestCase):
    def request(self, config: ActivityDetectionConfig) -> ChannelPowerRequest:
        return ChannelPowerRequest(
            "session", "trace", -5.0, 5.0,
            mode=ChannelPowerMode.ENTIRE_RECORDING_ACTIVE_ONLY,
            activity_config=config,
            power_semantics=PowerSemantics.POWER_PER_BIN,
        )

    def test_linear_time_average_duty_cycle_events_and_noise_correction(self) -> None:
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=10.0,
            threshold_on_offset_db=10.0,
            threshold_off_offset_db=6.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
            min_inactive_frames=1,
            max_gap_frames=0,
            merge_gap_frames=0,
        )
        rows = [
            SpectrogramRow(index, float(index), np.array([level], dtype=np.float32))
            for index, level in enumerate([30.0, 0.0, 0.0, 0.0])
        ]
        service = TimeGatedChannelPowerService()
        result = service.analyze(self.request(config), np.array([0.0]), rows)
        expected_long_mw = 0.25 * 1000.0 + 0.75 * 1.0
        self.assertAlmostEqual(result.active_mean_power_dbm, 30.0)
        self.assertAlmostEqual(result.long_term_mean_power_mw, expected_long_mw)
        self.assertAlmostEqual(result.duty_cycle_percent, 25.0)
        self.assertEqual(len(result.events), 1)
        self.assertAlmostEqual(result.noise_corrected_active_power_mw, 999.0)

    def test_threshold_change_reuses_cached_channel_power_series(self) -> None:
        base = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-50.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
        )
        service = TimeGatedChannelPowerService()
        rows = [SpectrogramRow(0, 0.0, np.array([-20.0], dtype=np.float32))]
        request = self.request(base)
        first = service.analyze(request, np.array([0.0]), rows)
        changed = replace(
            request,
            activity_config=replace(base, absolute_threshold_dbm=-10.0),
        )
        second = service.analyze(changed, np.array([0.0]), None)
        self.assertIs(first.series, second.series)
        self.assertNotEqual(first.frame_count_active, second.frame_count_active)

    def test_source_revision_invalidates_cached_power_series(self) -> None:
        service = TimeGatedChannelPowerService()
        config = ActivityDetectionConfig(
            threshold_mode=ActivityThresholdMode.ABSOLUTE,
            absolute_threshold_dbm=-50.0,
            smoothing_mode=SmoothingMode.NONE,
            min_active_frames=1,
        )
        first_request = replace(self.request(config), source_revision="size:mtime-1")
        second_request = replace(first_request, source_revision="size:mtime-2")
        first = service.analyze(
            first_request, np.array([0.0]),
            [SpectrogramRow(0, 0.0, np.array([-20.0], dtype=np.float32))],
        )
        second = service.analyze(
            second_request, np.array([0.0]),
            [SpectrogramRow(0, 0.0, np.array([-10.0], dtype=np.float32))],
        )
        self.assertIsNot(first.series, second.series)
        self.assertNotEqual(first.series.power_dbm[0], second.series.power_dbm[0])


if __name__ == "__main__":
    unittest.main()
