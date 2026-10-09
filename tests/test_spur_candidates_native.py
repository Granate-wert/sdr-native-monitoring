"""Actual CPU-generated reduced FFT diagnostics, not owner/hardware acceptance.

Test IQ is synthetic and uses the existing native numerical test/feed bridge.
No discovery, RF, GUI, policy enablement or firmware applicability is performed.
"""

import importlib
import importlib.util
import os
from pathlib import Path
import unittest

import numpy as np

from tests.native_test_dependencies import native_test_dll_directory


class NativeSpurCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        selected = os.environ.get("SDR_TEST_NATIVE_MODULE")
        if selected:
            cls.dll_scope = native_test_dll_directory(selected)
            cls.dll_scope.__enter__()
            cls.addClassCleanup(cls.dll_scope.__exit__, None, None, None)
            spec = importlib.util.spec_from_file_location("sdr_monitor._sdr_native", Path(selected).resolve(strict=True))
            if spec is None or spec.loader is None:
                raise RuntimeError("explicit native module loader unavailable")
            cls.native = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.native)
            if not hasattr(cls.native, "spur_candidate_numerical_contract"):
                raise RuntimeError("explicit native module lacks diagnostic contract")
        else:
            try:
                cls.native = importlib.import_module("sdr_monitor._sdr_native")
            except ImportError as error:
                raise unittest.SkipTest("optional native module unavailable") from error
            if not hasattr(cls.native, "spur_candidate_numerical_contract"):
                raise unittest.SkipTest("matching diagnostic native module required")

    def frame(self, lo=100e6, offset=40e3):
        n = self.native
        cpu = n.CpuDspBackend()
        cpu.configure(n.DspConfig(1024, 1024, n.WindowType.RECTANGULAR, n.DetectorType.SAMPLE,
            n.SpectrumUnit.DBFS_BIN, n.PrecisionMode.REFERENCE_F64, 1, 1, 8.6,
            n.CalibrationStatus.UNCALIBRATED, "", 5))
        samples = np.asarray(.2 * np.exp(2j * np.pi * offset * np.arange(1024) / 1024000), dtype=np.complex64)
        cpu.push_samples(samples, 1024000., float(lo))
        return cpu.poll_spectrum()[0]

    def test_contract_explicitly_does_not_admit_product_processing(self):
        contract = self.native.spur_candidate_numerical_contract()
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["scope"], "reduced_fft_numerical_diagnostics_only")
        for key in ("full_owner_context", "processing_mode_admission", "spectrum_modified", "calibrated_probability"):
            self.assertIs(contract[key], False)
        self.assertEqual((contract["max_observations"], contract["max_zones"]), (8, 64))
        self.assertEqual(contract["max_bin_visits"], 4194304)
        self.assertLessEqual(contract["native_report_bytes"], 4096)

    def test_actual_reduced_native_frames_and_immutable_output(self):
        frames = (self.frame(), self.frame(100020000.))
        powers = tuple(frame.values.copy() for frame in frames)
        result = self.native.evaluate_spur_candidates_v1(frames, (("baseband", 35000., 65000.),),
                                                        maximum_observation_span_ns=10_000_000_000)
        self.assertEqual(result["schema_version"], 1)
        self.assertIs(type(result["zones"]), tuple)
        zone = result["zones"][0]
        self.assertEqual(zone["evidence"], "repeated_baseband_candidate")
        self.assertEqual(zone["first_peak_rf_hz"], 100040000.)
        self.assertEqual(zone["first_peak_offset_hz"], 40000.)
        self.assertEqual(zone["complete_fft_observations"], 2)
        for frame, original in zip(frames, powers, strict=True):
            np.testing.assert_array_equal(frame.values, original)
            self.assertFalse(frame.values.flags.writeable)
        self.assertNotIn("probability", zone)
        self.assertNotIn("confirmed_spur", zone)

    def test_rf_zone_never_confirms_internal_spur(self):
        frames = (self.frame(offset=60000.), self.frame(100020000., 40000.))
        report = self.native.evaluate_spur_candidates_v1(frames, (("rf", 100055000., 100065000.),),
                                                        maximum_observation_span_ns=10_000_000_000)
        self.assertEqual(report["zones"][0]["evidence"], "stationary_rf_ambiguous")

    def test_source_and_arrays_do_not_mutate_during_native_call(self):
        frame = self.frame()
        with self.assertRaises(AttributeError):
            frame.source.source_id = "foreign"
        for array in (frame.values, frame.frequencies_hz):
            with self.assertRaises(ValueError):
                array.setflags(write=True)
        metadata = frame.source.metadata_json
        metadata["foreign"] = "not-owner-authority"
        self.assertNotIn("foreign", frame.source.metadata_json)

    def test_duplicate_same_frame_and_foreign_objects_refuse(self):
        frame = self.frame()
        with self.assertRaises(self.native.ConfigurationError):
            self.native.evaluate_spur_candidates_v1((frame, frame), (("baseband", 35000., 65000.),))
        for value in (object(), None, np.zeros(1024)):
            with self.subTest(value=type(value)), self.assertRaises(Exception):
                self.native.evaluate_spur_candidates_v1((value,), (("baseband", 35000., 65000.),))

    def test_bounds_and_exact_types_before_conversion(self):
        class NeverIterate:
            def __iter__(self):
                raise AssertionError("generic iterator must not execute")
        frames = (self.frame(),)
        for invalid in (NeverIterate(), list(frames), frames * 9, (), (None,) * 9):
            with self.subTest(input=type(invalid)), self.assertRaises(self.native.ConfigurationError) as result:
                self.native.evaluate_spur_candidates_v1(invalid, (("baseband", 35000., 65000.),))
            self.assertNotIsInstance(result.exception, AssertionError)
        for zones in ((), (("baseband", 35000., 65000.),) * 65,
                      (("baseband", True, 65000.),), (("baseband", 35000, 65000.),),
                      (("unknown", 35000., 65000.),), (("baseband!", 35000., 65000.),),
                      (("rf", float("nan"), 1.),),
                      (("baseband", 1., 0.),)):
            with self.subTest(zones=zones[:1]), self.assertRaises(self.native.ConfigurationError):
                self.native.evaluate_spur_candidates_v1(frames, zones)
        for kwargs in ({"minimum_peak_dbfs": True}, {"minimum_peak_dbfs": 1.},
                       {"minimum_contrast_db": 0.}, {"maximum_observation_span_ns": True},
                       {"maximum_observation_span_ns": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(self.native.ConfigurationError):
                self.native.evaluate_spur_candidates_v1(frames, (("baseband", 35000., 65000.),), **kwargs)


if __name__ == "__main__":
    unittest.main()
