"""Numerical semantics survive native -> Live -> Analyzer without defaults."""

from dataclasses import FrozenInstanceError
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain.spectrum_provenance import SpectrumProvenance
from sdr_monitor.services.native_spectrum_provenance import native_spectrum_provenance, validate_absolute_unit
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class SpectrumProvenanceTests(unittest.TestCase):
    def test_compiled_cpu_tone_metadata_and_values_survive_live_bridge(self):
        from esw_dfl.sdr import native_api
        from esw_dfl.sdr.contracts import CONTRACT_SCHEMA_VERSION

        native = native_api.require_native()
        backend = native.CpuDspBackend()
        backend.configure(native.DspConfig(
            1024, 1024, native.WindowType.HANN, native.DetectorType.AVERAGE_POWER,
            native.SpectrumUnit.DBFS_BIN, native.PrecisionMode.REFERENCE_F64,
            1, 4, 8.6, native.CalibrationStatus.UNCALIBRATED, "", CONTRACT_SCHEMA_VERSION,
        ))
        # Synthetic test vector only: physical receiver I/Q never enters Qt/Python.
        samples = np.asarray(0.5 * np.exp(2j * np.pi * 73 * np.arange(4096) / 1024), dtype=np.complex64)
        backend.push_samples(samples, 3_072_000., 100_000_000.)
        frames = backend.poll_spectrum(0)
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.averaging_frames, 4)
        service = NativeLiveSessionService(_FakeNative())
        self.assertTrue(service._publish_frame(frame))
        spectrum = service.latest_snapshot().spectrum
        metadata = spectrum.numerical_provenance
        self.assertEqual((metadata.window, metadata.detector, metadata.precision_mode),
                         ("hann", "average_power", "reference_f64"))
        self.assertEqual(metadata.averaging_frames, 4)
        self.assertEqual(metadata.fft_bin_width_hz, 3000.)
        window = np.hanning(1024)
        expected_enbw = 3_072_000. * np.sum(window ** 2) / np.sum(window) ** 2
        self.assertAlmostEqual(metadata.enbw_hz, expected_enbw, places=7)
        self.assertEqual(metadata.nominal_rbw_hz, metadata.enbw_hz)
        self.assertIsNone(metadata.estimated_uncertainty_db)
        self.assertEqual(spectrum.unit, "dBFS/bin")
        peak = int(np.argmax(spectrum.values))
        self.assertEqual(spectrum.frequencies_hz[peak], 100_000_000. + 73 * 3000.)
        self.assertAlmostEqual(float(spectrum.values[peak]), 20 * np.log10(0.5), delta=5e-5)
        np.testing.assert_array_equal(spectrum.values, frame.values)
        self.assertFalse(spectrum.values.flags.writeable)

        # A profile/status claim cannot turn uncorrected native digital power
        # into calibrated dBm, even if the producer supplies an apparent ID.
        forged = SimpleNamespace(**{
            name: getattr(frame, name) for name in (
                "timestamp_ns", "source", "config_generation", "window", "detector", "precision_mode",
                "fft_bin_width_hz", "enbw_hz", "nominal_rbw_hz", "averaging_frames",
                "estimated_uncertainty_db", "dropped_samples_before", "dropped_iq_blocks_before",
                "dropped_fft_frames_before",
            )
        })
        forged.frame_sequence = 2
        forged.unit = SimpleNamespace(name="DBM_BIN")
        forged.calibration_status = SimpleNamespace(name="APPLIED")
        forged.calibration_profile_id = "fixture"
        forged.quality_flags = 1  # native UNCALIBRATED
        self.assertFalse(service._publish_frame(forged))
        self.assertIn("absolute dBm is unavailable", service.latest_snapshot().error)

    def test_missing_metadata_is_unknown_and_reported_fields_are_immutable(self):
        self.assertEqual(native_spectrum_provenance(SimpleNamespace()), SpectrumProvenance())
        metadata = native_spectrum_provenance(SimpleNamespace(
            window=SimpleNamespace(name="HANN"), detector=SimpleNamespace(name="AVERAGE_POWER"),
            precision_mode=SimpleNamespace(name="REFERENCE_F64"), fft_bin_width_hz=3000.,
            enbw_hz=4500., nominal_rbw_hz=4500., averaging_frames=4,
            calibration_status=SimpleNamespace(name="UNCALIBRATED"), calibration_profile_id="",
            estimated_uncertainty_db=float("nan"),
        ))
        self.assertEqual((metadata.window, metadata.detector, metadata.averaging_frames),
                         ("hann", "average_power", 4))
        self.assertIsNone(metadata.estimated_uncertainty_db)
        with self.assertRaises(FrozenInstanceError):
            metadata.window = "kaiser"

    def test_invalid_quantities_and_absolute_unit_without_provenance_fail_closed(self):
        for field in ("fft_bin_width_hz", "enbw_hz", "nominal_rbw_hz"):
            for invalid in (-1, 0, float("inf"), float("nan"), True):
                with self.subTest(field=field, value=invalid), self.assertRaises(ValueError):
                    native_spectrum_provenance(SimpleNamespace(**{field: invalid}))
        for invalid in (True, False, -1, 1.5):
            with self.assertRaises(ValueError):
                native_spectrum_provenance(SimpleNamespace(averaging_frames=invalid))
        for metadata in (SpectrumProvenance(), SpectrumProvenance(calibration_status="applied"),
                         SpectrumProvenance(calibration_status="invalid", calibration_profile_id="fixture")):
            with self.assertRaises(ValueError):
                validate_absolute_unit("dBm/bin", metadata)
        with self.assertRaises(ValueError):
            validate_absolute_unit("dBm/bin", SpectrumProvenance(calibration_status="applied",
                                                                calibration_profile_id="fixture"))
        for field in ("window", "detector", "precision_mode", "calibration_status"):
            for invalid in ("mystery", "HANN ", 0):
                with self.subTest(field=field, invalid=invalid), self.assertRaises(ValueError):
                    native_spectrum_provenance(SimpleNamespace(**{field: invalid}))
        for invalid in (False, 0, 1):
            with self.assertRaises(ValueError):
                native_spectrum_provenance(SimpleNamespace(calibration_profile_id=invalid))
        with self.assertRaises(ValueError):
            native_spectrum_provenance(SimpleNamespace(estimated_uncertainty_db=float("inf")))

    def test_bridge_preserves_metadata_and_does_not_change_measured_values(self):
        service = NativeLiveSessionService(_FakeNative())
        values = np.array([-80., -70.], dtype=np.float32)
        frame = SimpleNamespace(frame_sequence=1, timestamp_ns=1, center_frequency_hz=100.,
            sample_rate_hz=2., fft_size=2, hop_size=1, frequencies_hz=np.array([99., 100.]),
            values=values, unit=SimpleNamespace(name="DBFS_BIN"), dropped_samples_before=0,
            dropped_iq_blocks_before=0, dropped_fft_frames_before=0, config_generation=0,
            source=SimpleNamespace(source_id="native-live"), window=SimpleNamespace(name="HANN"),
            detector=SimpleNamespace(name="SAMPLE"), fft_bin_width_hz=1., enbw_hz=1.5,
            nominal_rbw_hz=1.5, averaging_frames=1, calibration_status=SimpleNamespace(name="UNCALIBRATED"))
        self.assertTrue(service._publish_frame(frame))
        spectrum = service.latest_snapshot().spectrum
        self.assertEqual(spectrum.numerical_provenance.window, "hann")
        self.assertEqual(spectrum.numerical_provenance.averaging_frames, 1)
        np.testing.assert_array_equal(spectrum.values, [-80., -70.])
        self.assertTrue(np.shares_memory(spectrum.values, values))
        self.assertFalse(spectrum.values.flags.writeable)
