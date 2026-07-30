from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.power_measurements import (
    AcquisitionMode,
    AdjacentPairDefinition,
    AclrService,
    MaskLimitUnit,
    MeasurementQuality,
    MeasurementRegion,
    MultiChannelDefinition,
    PowerSemantics,
    ReferenceMode,
    RegionRole,
    SemMaskSegment,
    SpectrumFrame,
    SpectrumPowerIntegrator,
    TemporalMode,
    TraceMode,
    carrier_to_noise,
    harmonic_powers,
    measure_regions,
    multi_channel_aclr,
    occupied_bandwidth,
    spectrum_emission_mask,
    spurious_search,
    waterfall_power_statistics,
    waterfall_obw_statistics,
    x_db_bandwidth,
)


def frame(
    values: np.ndarray,
    *,
    start: float = 0.0,
    step: float = 10.0,
    semantics: PowerSemantics = PowerSemantics.POWER_PER_BIN,
    unit: str = "dBm",
    trace_mode: TraceMode = TraceMode.CLEAR_WRITE,
    acquisition: AcquisitionMode = AcquisitionMode.REAL_TIME,
    rbw_hz: float | None = None,
) -> SpectrumFrame:
    values = np.asarray(values, dtype=np.float64)
    return SpectrumFrame(
        start + step * np.arange(values.size), values, unit=unit, source_id="test",
        detector="Sample", trace_mode=trace_mode, acquisition_mode=acquisition,
        power_semantics=semantics, rbw_hz=rbw_hz,
    )


class SpectrumPowerIntegratorTests(unittest.TestCase):
    def test_power_per_bin_uses_partial_edge_bins(self) -> None:
        result = SpectrumPowerIntegrator().integrate(frame(np.full(3, -30.0)), 0.0, 10.0)
        self.assertAlmostEqual(result.power_dbm or 0.0, -30.0, places=8)
        self.assertEqual(result.selected_bin_count, 2)
        self.assertEqual(result.quality, MeasurementQuality.EXACT)

    def test_psd_scales_by_hertz(self) -> None:
        result = SpectrumPowerIntegrator().integrate(
            frame(np.array([-30.0, -30.0]), semantics=PowerSemantics.PSD_PER_HZ), 0.0, 10.0
        )
        self.assertAlmostEqual(result.power_dbm or 0.0, -20.0, places=8)

    def test_dbm_per_hz_unit_overrides_unknown_semantics(self) -> None:
        result = SpectrumPowerIntegrator().integrate(
            frame(np.array([-30.0, -30.0]), semantics=PowerSemantics.UNKNOWN, unit="dBm/Hz"), 0, 10
        )
        self.assertAlmostEqual(result.power_dbm or 0.0, -20.0, places=8)

    def test_rbw_filtered_requires_bandwidth(self) -> None:
        result = SpectrumPowerIntegrator().integrate(
            frame(np.full(3, -30.0), semantics=PowerSemantics.RBW_FILTERED_POWER), 0, 20
        )
        self.assertEqual(result.quality, MeasurementQuality.INVALID)
        self.assertIn("enbw_required", {warning.code for warning in result.warnings})

    def test_unknown_semantics_is_approximate(self) -> None:
        result = SpectrumPowerIntegrator().integrate(
            frame(np.full(3, -30.0), semantics=PowerSemantics.UNKNOWN), 0, 20
        )
        self.assertEqual(result.quality, MeasurementQuality.APPROXIMATE)

    def test_nan_is_omitted_and_reported(self) -> None:
        result = SpectrumPowerIntegrator().integrate(frame(np.array([-30.0, np.nan, -30.0])), 0, 20)
        self.assertEqual(result.valid_bin_count, 2)
        self.assertEqual(result.quality, MeasurementQuality.LIMITED)
        self.assertIn("non_finite_values", {warning.code for warning in result.warnings})

    def test_degenerate_frequency_axis_is_invalid(self) -> None:
        item = frame(np.array([-30.0, -20.0, -10.0]))
        item.frequencies_hz[:] = 0.0
        result = SpectrumPowerIntegrator().integrate(item, 0, 10)
        self.assertEqual(result.quality, MeasurementQuality.INVALID)

    def test_max_hold_and_swept_are_limited(self) -> None:
        item = frame(
            np.full(3, -30.0), trace_mode=TraceMode.MAX_HOLD,
            acquisition=AcquisitionMode.SWEPT,
        )
        result = SpectrumPowerIntegrator().integrate(item, 0, 20)
        self.assertEqual(result.quality, MeasurementQuality.LIMITED)
        self.assertEqual({warning.code for warning in result.warnings}, {"max_hold_source", "swept_acquisition"})


class ExtendedPowerMeasurementTests(unittest.TestCase):
    def test_aclr_averages_main_and_adjacent_in_linear_domain(self) -> None:
        first = frame(np.array([-60, -40, -10, -10, -10, -40, -60]), start=70)
        second = frame(np.array([-60, -30, -20, -20, -20, -30, -60]), start=70)
        result = AclrService().measure(
            [first, second], 100, 20, 30, adjacent_bandwidth_hz=20,
            temporal_mode=TemporalMode.MEAN,
        )
        self.assertEqual(len(result.adjacent), 2)
        self.assertGreater(result.adjacent[0].aclr_db or 0.0, 0.0)

    def test_aclr_uses_shared_activity_mask(self) -> None:
        frames = [frame(np.full(7, level), start=70) for level in (-80.0, -20.0, -10.0)]
        result = AclrService().measure(
            frames, 100, 20, 30, temporal_mode=TemporalMode.ACTIVE_MEAN,
            activity_mask=np.array([False, True, False]),
        )
        self.assertAlmostEqual(result.main.power_dbm or 0.0, -16.989700043, places=6)

    def test_aclr_supports_named_independent_adjacent_pairs(self) -> None:
        item = frame(np.full(21, -30.0), start=0)
        result = AclrService().measure(
            [item], 100, 20, 20,
            pair_definitions=(
                AdjacentPairDefinition("Near", 30, 10),
                AdjacentPairDefinition("Far", 70, 20),
            ),
        )
        self.assertEqual([band.name for band in result.adjacent], ["Lower Near", "Upper Near", "Lower Far", "Upper Far"])

    def test_multi_channel_reference_sum(self) -> None:
        item = frame(np.full(9, -30.0), start=0)
        definitions = (
            MultiChannelDefinition("Ref 1", 10, 10, True),
            MultiChannelDefinition("Ref 2", 30, 10, True),
            MultiChannelDefinition("Adjacent", 60, 10),
        )
        result = multi_channel_aclr(item, definitions, ReferenceMode.SUM_REFERENCES)
        self.assertAlmostEqual(result.reference_power_dbm or 0.0, -26.989700, places=5)
        self.assertAlmostEqual(result.channels[-1].aclr_db or 0.0, 3.0103, places=4)

    def test_occupied_bandwidth_and_waterfall_statistics(self) -> None:
        frames = [frame(np.array([-80, -20, -20, -80])), frame(np.array([-80, -10, -10, -80]))]
        obw = occupied_bandwidth(frames[0], 0.99)
        stats = waterfall_power_statistics(frames, 5, 25)
        self.assertIsNotNone(obw.bandwidth_hz)
        self.assertEqual(stats.valid_frame_count, 2)
        self.assertGreater(stats.maximum_power_dbm or -100, stats.minimum_power_dbm or 0)
        obw_stats = waterfall_obw_statistics(frames, activity_mask=np.array([True, False]))
        self.assertIsNotNone(obw_stats.active_mean_hz)

    def test_carrier_to_noise_uses_noise_bandwidth_scaling(self) -> None:
        item = frame(np.array([-100, -100, -20, -20, -100, -100]), start=0)
        result = carrier_to_noise(item, (15, 35), (45, 55))
        self.assertIsNotNone(result.cn_db)
        self.assertGreater(result.cn_db or 0.0, 60.0)

    def test_x_db_bandwidth_interpolates_both_crossings(self) -> None:
        result = x_db_bandwidth(frame(np.array([-20, -10, 0, -10, -20])), 3.0)
        self.assertAlmostEqual(result.left_crossing_hz or 0.0, 17.0)
        self.assertAlmostEqual(result.right_crossing_hz or 0.0, 23.0)

    def test_harmonics_report_out_of_range_orders(self) -> None:
        item = frame(np.array([-100, -10, -30, -40, -100]), start=0)
        result = harmonic_powers(item, 10, count=6, measurement_bandwidth_hz=10)
        self.assertEqual(len(result), 6)
        self.assertFalse(result[-1].in_range)
        self.assertAlmostEqual(result[1].relative_dbc or 0.0, -20.0, places=6)

    def test_custom_regions_respect_enabled_and_exclude_roles(self) -> None:
        item = frame(np.full(5, -30.0))
        regions = (
            MeasurementRegion("signal", 0, 10, RegionRole.SIGNAL),
            MeasurementRegion("excluded", 20, 30, RegionRole.EXCLUDE),
            MeasurementRegion("disabled", 30, 40, enabled=False),
        )
        self.assertEqual([result.region.name for result in measure_regions(item, regions)], ["signal"])

    def test_custom_region_reports_density_peak_and_reference_delta(self) -> None:
        item = frame(np.array([-30.0, -20.0, -40.0]))
        regions = (
            MeasurementRegion("main", 0, 10, RegionRole.MAIN),
            MeasurementRegion("adj", 10, 20, RegionRole.ADJACENT, reference_region="main"),
        )
        result = measure_regions(item, regions)
        self.assertIsNotNone(result[0].mean_density_dbm_hz)
        self.assertEqual(result[0].peak_frequency_hz, 10.0)
        self.assertIsNotNone(result[1].relative_db)

    def test_sem_supports_dbm_dbmhz_and_dbc(self) -> None:
        item = frame(np.array([-40, -20, -40]), rbw_hz=10)
        result = spectrum_emission_mask(item, (
            SemMaskSegment(0, 20, -30, -30, MaskLimitUnit.DBM),
            SemMaskSegment(0, 20, -40, -40, MaskLimitUnit.DBM_PER_HZ),
            SemMaskSegment(0, 20, -20, -20, MaskLimitUnit.DBC),
        ), reference_dbm=-10)
        self.assertFalse(result.passed)
        self.assertGreater(result.maximum_excess_db, 0)

    def test_spurious_search_applies_prominence_distance_and_exclusions(self) -> None:
        item = frame(np.array([-80, -20, -80, -25, -80, -10, -80]))
        peaks = spurious_search(
            item, 0, 60, minimum_level_dbm=-30, minimum_prominence_db=20,
            minimum_distance_hz=20, exclusions=((45, 55),),
        )
        self.assertEqual([peak.frequency_hz for peak in peaks], [10.0, 30.0])

    def test_spurious_result_can_include_integrated_relative_power_and_limit(self) -> None:
        item = frame(np.array([-80, -20, -80]))
        peaks = spurious_search(
            item, 0, 20, measurement_bandwidth_hz=10, main_power_dbm=-10,
            main_center_hz=0, limit_line_dbm=-30,
        )
        self.assertEqual(peaks[0].status, "FAIL")
        self.assertIsNotNone(peaks[0].integrated_power_dbm)
        self.assertEqual(peaks[0].distance_from_main_hz, 10.0)


if __name__ == "__main__":
    unittest.main()
