"""Vector correction equivalence to retained scalar evaluator, not SDR timing."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.calibration import (
    CalibrationPoint, CalibrationProfile, CalibrationProfileError, CalibrationStatus, apply_calibration,
)
from tests import test_calibration_receiver_scope as fixtures


class VectorCalibrationTests(unittest.TestCase):
    def compare(self, profile, frequencies, extrapolate=False):
        raw = np.arange(len(frequencies), dtype=float) / 3
        original = raw.copy()
        samples = [profile.evaluate(float(f), allow_extrapolation=extrapolate) for f in frequencies]
        output = apply_calibration(raw, frequencies, profile, profile.signature, allow_extrapolation=extrapolate)
        if any(s.status is CalibrationStatus.INVALID for s in samples):
            self.assertIs(output.status, CalibrationStatus.INVALID)
            self.assertEqual(output.unit, profile.signature.fft_unit_convention)
            np.testing.assert_array_equal(output.values, raw)
            self.assertTrue(np.all(np.isnan(output.uncertainty_db)))
        else:
            expected = (CalibrationStatus.EXTRAPOLATED if any(s.status is CalibrationStatus.EXTRAPOLATED for s in samples)
                        else CalibrationStatus.INTERPOLATED if any(s.status is CalibrationStatus.INTERPOLATED for s in samples)
                        else CalibrationStatus.CALIBRATED)
            self.assertIs(output.status, expected)
            np.testing.assert_allclose(output.values, raw + [s.correction_db for s in samples], rtol=1e-13, atol=1e-13)
            np.testing.assert_allclose(output.uncertainty_db, [s.uncertainty_db for s in samples], rtol=1e-13, atol=1e-13)
        np.testing.assert_array_equal(raw, original)
        self.assertEqual(output.profile_id, profile.profile_id)

    def test_random_unsorted_grids_exact_nodes_and_both_extrapolation_edges(self):
        rng = np.random.default_rng(42)
        for count in (2, 5, 1001):
            grid = np.linspace(1e6, 2e6, count)
            points = tuple(CalibrationPoint(float(f), float(c), float(u)) for f, c, u in zip(
                grid, rng.normal(size=count), rng.uniform(.1, 2., count)))
            profile = replace(fixtures.profile(), points=points, valid_start_hz=None, valid_stop_hz=None)
            frequencies = np.concatenate((grid, rng.uniform(.9e6, 2.1e6, 128)))
            rng.shuffle(frequencies)
            for extrapolate in (False, True):
                with self.subTest(count=count, extrapolate=extrapolate):
                    self.compare(profile, frequencies, extrapolate)

    def test_all_exact_points_are_calibrated(self):
        self.compare(fixtures.profile(), np.array([200., 100., 200.]))

    def test_per_hz_units_and_interpolated_values(self):
        profile = replace(fixtures.profile(), signature=replace(fixtures.signature(), fft_unit_convention='dBFS/Hz'))
        self.compare(profile, np.array([100., 125., 199., 200.]))
        self.assertEqual(apply_calibration([0.], [125.], profile, profile.signature).unit, 'dBm/Hz')

    def test_array_path_does_not_rebuild_scalar_curve_per_bin(self):
        profile = fixtures.profile()
        with patch.object(CalibrationProfile, 'evaluate', side_effect=AssertionError('per-bin curve allocation')):
            result = apply_calibration(np.zeros(16384), np.linspace(100., 200., 16384), profile, profile.signature)
        self.assertIs(result.status, CalibrationStatus.INTERPOLATED)

    def test_nonfinite_frequency_refuses_when_profile_is_applicable(self):
        profile = fixtures.profile()
        for f in (float('nan'), float('inf'), -float('inf')):
            with self.subTest(f=f), self.assertRaises(CalibrationProfileError):
                apply_calibration([0.], [f], profile, profile.signature)


if __name__ == '__main__':
    unittest.main()
