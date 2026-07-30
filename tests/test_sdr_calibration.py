from __future__ import annotations

import json
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from esw_dfl.sdr.calibration_store import (
    CalibrationApplicationStatus,
    CalibrationApplier,
    CalibrationPoint,
    CalibrationProfile,
    CalibrationProfileError,
    CalibrationProfileStore,
    CalibrationSignature,
    apply_calibration,
    check_applicability,
    profile_from_csv,
)
from esw_dfl.sdr.contracts import SpectrumUnit


def signature(*, serial: str = "S1", backend: str = "cpu", gain: float = 10.0) -> CalibrationSignature:
    return CalibrationSignature(
        device_serial=serial,
        backend=backend,
        rf_port_path="rx",
        sample_rate_hz=1_000_000.0,
        analog_bandwidth_hz=800_000.0,
        gain_mode="manual",
        manual_gain_db=gain,
        window_normalization_version="p09-v1",
        fft_unit_convention="dBFS/bin",
        frontend_chain="pluto-rx",
        reference_plane="rf_input",
    )


def profile(*, version: int = 1, backend: str = "cpu") -> CalibrationProfile:
    return CalibrationProfile(
        profile_id="test-profile",
        profile_version=version,
        signature=signature(backend=backend),
        reference_plane="rf_input",
        points=(
            CalibrationPoint(100.0, -50.0, -51.0, 1.0, 1.0),
            CalibrationPoint(200.0, -40.0, -43.0, 3.0, 2.0),
            CalibrationPoint(300.0, -30.0, -35.0, 5.0, 3.0),
        ),
        created_at="2026-07-30T00:00:00+00:00",
    )


class CalibrationCoreTests(unittest.TestCase):
    def test_exact_interpolation_and_extrapolation_status(self) -> None:
        item = profile()
        self.assertEqual(item.evaluate(200.0).status, CalibrationApplicationStatus.CALIBRATED)
        interpolated = item.evaluate(150.0)
        self.assertEqual(interpolated.status, CalibrationApplicationStatus.INTERPOLATED)
        self.assertAlmostEqual(interpolated.correction_db, 2.0)
        self.assertAlmostEqual(interpolated.uncertainty_db, 1.5)
        self.assertEqual(item.evaluate(50.0).status, CalibrationApplicationStatus.INVALID_FOR_SETTINGS)
        self.assertEqual(item.evaluate(50.0, allow_extrapolation=True).status, CalibrationApplicationStatus.EXTRAPOLATED)

    def test_applicability_rejects_serial_and_gain_mismatch(self) -> None:
        item = profile()
        self.assertFalse(check_applicability(item, signature(serial="other")).applicable)
        self.assertIn("serial", check_applicability(item, signature(serial="other")).reason)
        self.assertFalse(check_applicability(item, signature(gain=11.0)).applicable)
        self.assertIn("gain", check_applicability(item, signature(gain=11.0)).reason)

    def test_correction_and_uncertainty_propagation(self) -> None:
        result = apply_calibration(
            np.array([100.0, 200.0, 300.0]),
            np.array([-10.0, -20.0, -30.0]),
            profile=profile(),
            settings=signature(),
            raw_uncertainty_db=np.array([4.0, 4.0, 4.0]),
        )
        np.testing.assert_allclose(result.values_db, [-9.0, -17.0, -25.0])
        np.testing.assert_allclose(result.uncertainty_db, [np.sqrt(17.0), np.sqrt(20.0), 5.0])
        self.assertEqual(result.unit, SpectrumUnit.DBM_BIN)
        self.assertEqual(result.status, CalibrationApplicationStatus.CALIBRATED)

    def test_uncalibrated_data_never_gets_dBm_label(self) -> None:
        frequencies = np.array([100.0, 200.0])
        values = np.array([-10.0, -20.0])
        missing = apply_calibration(frequencies, values, profile=None, settings=None)
        self.assertEqual(missing.unit, SpectrumUnit.DBFS_BIN)
        self.assertEqual(missing.status, CalibrationApplicationStatus.UNCALIBRATED)
        incompatible = apply_calibration(frequencies, values, profile=profile(), settings=signature(serial="bad"))
        self.assertEqual(incompatible.unit, SpectrumUnit.DBFS_BIN)
        self.assertEqual(incompatible.status, CalibrationApplicationStatus.INVALID_FOR_SETTINGS)
        np.testing.assert_array_equal(incompatible.values_db, values)

    def test_cpu_and_cuda_profiles_use_same_correction_math(self) -> None:
        frequencies = np.array([125.0, 225.0, 300.0])
        cpu = profile(backend="cpu")
        cuda = profile(backend="cuda")
        cpu_result = apply_calibration(frequencies, np.zeros(3), profile=cpu, settings=signature(backend="cpu"))
        cuda_result = apply_calibration(frequencies, np.zeros(3), profile=cuda, settings=signature(backend="cuda"))
        np.testing.assert_allclose(cpu_result.correction_db, cuda_result.correction_db)
        np.testing.assert_allclose(cpu_result.values_db, cuda_result.values_db)


class CalibrationStoreTests(unittest.TestCase):
    def test_cache_reuse_and_version_invalidation(self) -> None:
        applier = CalibrationApplier(max_entries=2)
        grid = np.array([100.0, 150.0, 200.0])
        first = applier.prepare(profile(), signature(), grid)
        second = applier.prepare(profile(), signature(), grid)
        self.assertIs(first, second)
        self.assertEqual(applier.cache_size, 1)
        changed = applier.prepare(replace(profile(), profile_version=2), signature(), grid)
        self.assertIsNot(first, changed)
        applier.invalidate_profile("test-profile", 1)
        self.assertEqual(applier.cache_size, 1)

    def test_atomic_save_load_latest_and_immutable_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = CalibrationProfileStore(directory)
            item = profile()
            path = store.save(item)
            self.assertTrue(path.is_file())
            self.assertFalse(list(Path(directory).glob("*.part")))
            self.assertEqual(store.load("test-profile").fingerprint, item.fingerprint)
            store.save(item)
            changed = replace(item, points=(
                CalibrationPoint(100.0, -50.0, -50.0, 0.0, 1.0),
                *item.points[1:],
            ))
            with self.assertRaises(CalibrationProfileError):
                store.save(changed)
            store.save(replace(item, profile_version=2))
            self.assertEqual(store.load("test-profile").profile_version, 2)
            self.assertEqual(len(store.list_profiles()), 2)

    def test_corrupted_profile_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.v1.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(CalibrationProfileError):
                CalibrationProfileStore(directory).load("broken", 1)

    def test_csv_import_uses_required_columns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            csv_path = Path(directory) / "points.csv"
            csv_path.write_text(
                "frequency_hz,reference_dbm,measured_dbfs,correction_db,uncertainty_db\n"
                "100,-50,-51,1,1\n200,-40,-43,3,2\n",
                encoding="utf-8",
            )
            item = profile_from_csv(csv_path, profile_id="csv-profile", profile_version=1, signature=signature())
            self.assertEqual(len(item.points), 2)
            self.assertEqual(item.points[1].correction_db, 3.0)

    def test_schema_document_matches_wire_root(self) -> None:
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "docs" / "schemas" / "sdr_calibration_profile.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema"]["const"], "sdr-calibration-profile")
        self.assertEqual(schema["properties"]["schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
