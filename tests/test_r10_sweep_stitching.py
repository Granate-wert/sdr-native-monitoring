"""R10 standalone multi-band sweep stitching contracts."""

from __future__ import annotations

import unittest

import numpy as np

from sdr_monitor.domain import (
    SweepBinQuality,
    SweepConfiguration,
    SweepPlan,
    SweepRateEvidence,
    SweepSegment,
    SweepSegmentSpectrum,
)
from sdr_monitor.services.sweep_session import InMemorySweepService
from sdr_monitor.services.sweep_stitching import SweepStitchError, SweepStitchOptions, stitch_sweep_segments


def _plan() -> SweepPlan:
    configuration = SweepConfiguration(start_hz=0.0, stop_hz=100.0, dc_margin_hz=0.0)
    return SweepPlan(
        configuration,
        (
            SweepSegment(0, 0.0, 60.0, 0.0, 60.0),
            SweepSegment(1, 40.0, 100.0, 40.0, 100.0),
        ),
        0.0,
        10.0,
    )


def _spectrum(index: int, start: float, stop: float, value_db: float, *, source: str = "source-a", generation: int = 7) -> SweepSegmentSpectrum:
    frequency = np.arange(start, stop + 1.0, 10.0, dtype=np.float64)
    return SweepSegmentSpectrum(index, source, generation, frequency, np.full(frequency.size, value_db))


class R10SweepStitchingTests(unittest.TestCase):
    def test_stitch_uses_linear_power_correction_and_retains_seam_evidence(self) -> None:
        grid = stitch_sweep_segments(
            _plan(),
            (_spectrum(0, 0.0, 60.0, -30.0), _spectrum(1, 40.0, 100.0, -27.0)),
            SweepStitchOptions(target_spacing_hz=10.0, edge_taper_bins=0),
        )

        self.assertEqual(grid.source_id, "source-a")
        self.assertEqual(grid.config_generation, 7)
        self.assertTrue(np.allclose(grid.values_db, -30.0, atol=1e-5, equal_nan=False))
        self.assertEqual(len(grid.seams), 1)
        self.assertAlmostEqual(grid.seams[0].correction_db, 3.0, places=5)
        self.assertAlmostEqual(grid.seams[0].before_p95_db, 3.0, places=5)
        self.assertAlmostEqual(grid.seams[0].after_p95_db, 0.0, places=5)
        overlap_bins = (grid.frequencies_hz >= 40.0) & (grid.frequencies_hz <= 60.0)
        self.assertTrue(np.all(grid.quality_flags[overlap_bins] & int(SweepBinQuality.STITCH_OVERLAP)))
        self.assertFalse(grid.values_db.flags.writeable)
        self.assertFalse(grid.quality_flags.flags.writeable)

    def test_seam_aligns_different_local_regrid_slices_by_frequency(self) -> None:
        """An FFT axis between target bins must not cause seam shape mismatch."""

        left = _spectrum(0, 0.0, 60.0, -30.0)
        right_frequency = np.asarray([40.5, 50.5, 60.5, 70.5, 80.5, 90.5, 100.0])
        right = SweepSegmentSpectrum(
            1,
            "source-a",
            7,
            right_frequency,
            np.full(right_frequency.size, -27.0),
        )

        grid = stitch_sweep_segments(
            _plan(),
            (left, right),
            SweepStitchOptions(target_spacing_hz=10.0, edge_taper_bins=0, min_overlap_points=2),
        )

        self.assertEqual(len(grid.seams), 1)
        self.assertAlmostEqual(grid.seams[0].correction_db, 3.0, places=5)
        self.assertAlmostEqual(grid.seams[0].after_p95_db, 0.0, places=5)

    def test_missing_segment_remains_nan_and_is_never_filled_by_ui_logic(self) -> None:
        grid = stitch_sweep_segments(
            _plan(),
            (_spectrum(0, 0.0, 60.0, -30.0),),
            SweepStitchOptions(target_spacing_hz=10.0, edge_taper_bins=0),
        )

        missing = grid.frequencies_hz > 60.0
        self.assertTrue(np.all(np.isnan(grid.values_db[missing])))
        self.assertTrue(np.all(grid.quality_flags[missing] & int(SweepBinQuality.MISSING_SEGMENT)))
        self.assertEqual(grid.missing_bin_count, int(np.count_nonzero(missing)))
        self.assertEqual(grid.missing_segment_indices, (1,))

    def test_mixed_source_or_generation_is_rejected_before_combining_data(self) -> None:
        with self.assertRaisesRegex(SweepStitchError, "mixed source"):
            stitch_sweep_segments(
                _plan(),
                (_spectrum(0, 0.0, 60.0, -30.0), _spectrum(1, 40.0, 100.0, -30.0, source="source-b")),
            )
        with self.assertRaisesRegex(SweepStitchError, "mixed segment generations"):
            stitch_sweep_segments(
                _plan(),
                (_spectrum(0, 0.0, 60.0, -30.0), _spectrum(1, 40.0, 100.0, -30.0, generation=8)),
            )

    def test_explicit_generation_map_allows_only_expected_retune_transitions(self) -> None:
        grid = stitch_sweep_segments(
            _plan(),
            (_spectrum(0, 0.0, 60.0, -30.0, generation=7), _spectrum(1, 40.0, 100.0, -30.0, generation=8)),
            SweepStitchOptions(
                target_spacing_hz=10.0,
                edge_taper_bins=0,
                expected_generation_by_segment=((0, 7), (1, 8)),
            ),
        )
        self.assertIsNone(grid.config_generation)
        self.assertEqual(grid.segment_config_generations, ((0, 7), (1, 8)))
        with self.assertRaisesRegex(SweepStitchError, "differs from the explicit"):
            stitch_sweep_segments(
                _plan(),
                (_spectrum(0, 0.0, 60.0, -30.0, generation=7), _spectrum(1, 40.0, 100.0, -30.0, generation=8)),
                SweepStitchOptions(expected_generation_by_segment=((0, 7), (1, 9))),
            )

    def test_target_grid_capacity_is_an_explicit_bound(self) -> None:
        with self.assertRaisesRegex(SweepStitchError, "bounded bin limit"):
            stitch_sweep_segments(
                _plan(),
                (_spectrum(0, 0.0, 60.0, -30.0),),
                SweepStitchOptions(target_spacing_hz=1.0, max_target_bins=10),
            )

    def test_in_memory_service_exposes_synthetic_rates_without_fabricating_fft_lps(self) -> None:
        service = InMemorySweepService()
        result = service.execute(SweepConfiguration(start_hz=400e6, stop_hz=450e6), lambda _progress: None)

        self.assertIsNotNone(result.stitched_grid)
        self.assertIsNotNone(result.rate_metrics)
        assert result.stitched_grid is not None
        assert result.rate_metrics is not None
        self.assertGreater(result.stitched_grid.frequencies_hz.size, 2)
        self.assertIs(result.rate_metrics.evidence, SweepRateEvidence.SYNTHETIC)
        self.assertIsNotNone(result.rate_metrics.megahertz_per_second)
        self.assertIsNotNone(result.rate_metrics.sweeps_per_second)
        self.assertIsNone(result.rate_metrics.fft_lps)


if __name__ == "__main__":
    unittest.main()
