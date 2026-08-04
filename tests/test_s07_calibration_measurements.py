"""Standalone S07 calibration and measurement acceptance tests."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from sdr_monitor.domain import (
    CalibrationPoint,
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationSignature,
    CalibrationStatus,
    MeasurementQuality,
    MeasurementValue,
    apply_calibration,
)
from sdr_monitor.services.calibration_service import CalibrationService
from sdr_monitor.services.calibration_store import CalibrationProfileStore


class S07CalibrationTests(unittest.TestCase):
    def make_profile(self, *, backend: str = "cpu") -> CalibrationProfile:
        signature = CalibrationSignature(backend=backend)
        return CalibrationProfile(
            "lab-profile",
            1,
            signature,
            (
                CalibrationPoint(100.0, 1.0, 0.2),
                CalibrationPoint(200.0, 3.0, 0.4),
            ),
        )

    def test_exact_interpolated_and_extrapolated_are_explicit(self) -> None:
        profile = self.make_profile()
        self.assertEqual(profile.evaluate(100).status, CalibrationStatus.CALIBRATED)
        self.assertEqual(profile.evaluate(150).status, CalibrationStatus.INTERPOLATED)
        self.assertEqual(profile.evaluate(50).status, CalibrationStatus.INVALID)
        self.assertEqual(profile.evaluate(50, allow_extrapolation=True).status, CalibrationStatus.EXTRAPOLATED)

    def test_applicable_array_is_dbm_but_invalid_array_stays_dbfs(self) -> None:
        profile = self.make_profile()
        values = apply_calibration([10.0, 20.0], [100.0, 150.0], profile, CalibrationSignature())
        self.assertEqual(values.unit, "dBm/bin")
        self.assertEqual(values.status, CalibrationStatus.INTERPOLATED)
        invalid = apply_calibration([10.0], [100.0], profile, CalibrationSignature(sample_rate_hz=2e6))
        self.assertEqual(invalid.unit, "dBFS/bin")
        self.assertEqual(invalid.status, CalibrationStatus.INVALID)
        uncalibrated = apply_calibration([10.0], [100.0], None, CalibrationSignature())
        self.assertEqual(uncalibrated.unit, "dBFS/bin")

    def test_incompatible_activation_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            service = CalibrationService(CalibrationProfileStore(Path(temporary)))
            profile = self.make_profile()
            service.finalize_profile(profile)
            service.set_current_settings(CalibrationSignature(sample_rate_hz=2e6))
            with self.assertRaises(CalibrationProfileError):
                service.select_active_profile(profile)
            result = service.select_active_profile(profile, expert_override=True)
            self.assertFalse(result.applicable)
            self.assertEqual(service.active_profile(), profile)

    def test_profile_versions_are_immutable_and_csv_preview_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = CalibrationProfileStore(Path(temporary))
            service = CalibrationService(store)
            profile = self.make_profile()
            service.finalize_profile(profile)
            self.assertEqual(service.list_profiles(), (profile,))
            with self.assertRaises(CalibrationProfileError):
                service.finalize_profile(CalibrationProfile("lab-profile", 1, profile.signature, (CalibrationPoint(100, 4, .2), CalibrationPoint(200, 5, .2))))
            preview = service.preview_csv("frequency_hz,correction_db,uncertainty_db\n100,1,.2\n200,3,.4\n", "csv-profile", 2)
            self.assertTrue(preview.valid)
            self.assertFalse((Path(temporary) / "csv-profile").exists())
            finalized = service.finalize_preview(preview)
            self.assertEqual(finalized.profile_version, 2)

    def test_measurement_has_unit_quality_and_does_not_fabricate_dbm(self) -> None:
        with self.assertRaises(ValueError):
            MeasurementValue("peak", "Peak", 1.0, "dBm", MeasurementQuality.UNSUPPORTED, None, 1, 1, "live", CalibrationStatus.UNCALIBRATED)
        measurement = MeasurementValue("peak", "Peak", 1.0, "dBFS/bin", MeasurementQuality.UNSUPPORTED, None, 1, 1, "live", CalibrationStatus.UNCALIBRATED)
        self.assertEqual(measurement.unit, "dBFS/bin")
        self.assertEqual(measurement.quality, MeasurementQuality.UNSUPPORTED)

    def test_cpu_cuda_signatures_share_profile_math(self) -> None:
        profile = self.make_profile(backend="cpu")
        cpu = apply_calibration(np.array([10.0, 12.0]), np.array([100.0, 150.0]), profile, CalibrationSignature(backend="cpu"))
        cuda = apply_calibration(np.array([10.0, 12.0]), np.array([100.0, 150.0]), profile, CalibrationSignature(backend="cuda"))
        self.assertEqual(cuda.status, CalibrationStatus.INVALID)
        self.assertFalse(np.allclose(cpu.values, cuda.values))
        same_backend_profile = self.make_profile(backend="cuda")
        cuda = apply_calibration(np.array([10.0, 12.0]), np.array([100.0, 150.0]), same_backend_profile, CalibrationSignature(backend="cuda"))
        self.assertTrue(np.allclose(cpu.values, cuda.values))


if __name__ == "__main__":
    unittest.main()
