"""P15 validation and evidence regression tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from esw_dfl.sdr import native_api
from esw_dfl.sdr.synthetic import SyntheticScenario
from esw_dfl.sdr.validation import (
    run_offline_validation,
    validate_calibration_residuals,
    validate_cpu_precision,
    validate_recording,
    validate_sweep_quality,
    validate_synthetic_fixed_band,
    validate_synthetic_generators,
)


class P15ValidationTests(unittest.TestCase):
    def test_all_synthetic_generators_are_deterministic(self) -> None:
        result = validate_synthetic_generators()
        self.assertEqual(result["scenario_count"], len(SyntheticScenario))
        self.assertEqual(len(result["scenarios"]), len(SyntheticScenario))
        for row in result["scenarios"]:
            self.assertEqual(len(row["sha256"]), 64)
            self.assertGreater(row["sample_count"], 0)

    def test_calibration_residuals_and_unit_guard(self) -> None:
        result = validate_calibration_residuals()
        self.assertEqual(result["residual_db"], 0.0)
        self.assertEqual(result["calibrated_unit"], "dBm/bin")
        self.assertEqual(result["incompatible_unit"], "dBFS/bin")

    def test_sweep_stitching_reports_seams_and_coverage(self) -> None:
        result = validate_sweep_quality()
        self.assertGreater(result["segment_count"], 1)
        self.assertGreater(result["finite_bins"], 0)
        self.assertGreater(result["seam_count"], 0)
        self.assertEqual(result["coverage_gaps"], [])

    def test_recording_replays_without_drops(self) -> None:
        result = validate_recording(8)
        self.assertEqual(result["replayed_iq_blocks"], 8)
        self.assertGreater(result["replayed_spectrum_frames"], 0)
        self.assertEqual(result["dropped_items"], 0)
        self.assertEqual(result["gap_count"], 0)

    def test_native_cpu_precision_and_fixed_band(self) -> None:
        if not native_api.native_availability().available:
            self.skipTest("compiled native extension is unavailable")
        precision = validate_cpu_precision()
        self.assertEqual(precision["axis_max_error_hz"], 0.0)
        self.assertLess(precision["linear_psd_max_relative_error"], 2.0e-5)
        fixed_band = validate_synthetic_fixed_band()
        self.assertGreater(fixed_band["fft_frames_computed"], 0)
        self.assertEqual(fixed_band["fft_frames_dropped"], 0)
        self.assertTrue(fixed_band["healthy"])

    def test_offline_report_and_atomic_evidence(self) -> None:
        report = run_offline_validation(benchmark_repeats=1, recording_blocks=8)
        self.assertFalse(report.failed)
        self.assertTrue(report.not_verified)
        with tempfile.TemporaryDirectory(prefix="p15-evidence-") as temporary:
            root = Path(temporary)
            paths = report.write_evidence(root)
            for key in ("json", "csv", "log"):
                self.assertIn(key, paths)
                self.assertTrue(paths[key].is_file())
            payload = json.loads(paths["json"].read_text(encoding="utf-8"))
            self.assertEqual(payload["schema"], "sdr-native-p15-validation")
            self.assertFalse(list(root.glob("*.part")))


if __name__ == "__main__":
    unittest.main()
