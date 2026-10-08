"""Contributing Sweep FFT observations: synthetic/native CPU only, no RX proof."""
from dataclasses import FrozenInstanceError, replace
import importlib
from types import SimpleNamespace
import unittest

import numpy as np

from sdr_monitor.domain.processing_policy import HostDcMode
from sdr_monitor.domain.sweep_acquisition import SweepSegmentAcquisition
from sdr_monitor.services.native_continuous_sweep import _to_domain_acquisition, _to_domain_line
from tests.test_processing_recipe_bridge import recipe


def acquisition(mode=HostDcMode.OFF):
    metadata = SimpleNamespace(center_frequency_hz=100e6, sample_rate_hz=8e6,
        analog_bandwidth_hz=8e6, fft_size=256, hop_size=256, window="rectangular",
        detector="sample", precision_mode="reference_f64", fft_bin_width_hz=31250.,
        enbw_hz=31250., nominal_rbw_hz=31250., averaging_frames=1,
        calibration_status="uncalibrated", calibration_profile_id="",
        estimated_uncertainty_db=float("nan"), window_normalization_version="power-norm-v1",
        dsp_processing_recipe=recipe(mode))
    return SimpleNamespace(segment_index=0, config_generation=7, frame_sequence=12,
        first_sample_index=256, timestamp_ns=123, sample_rate_hz=8e6,
        fft_size=256, quality_flags=512 if mode is HostDcMode.BLOCK_MEAN else 0,
        processing_metadata=metadata)


def mapped(record):
    return _to_domain_acquisition(SimpleNamespace(segment_acquisition=(record,), unit="dBFS/bin"))[0]


class SweepProcessingMapperTests(unittest.TestCase):
    def test_actual_recipe_and_window_not_panorama_center(self):
        for mode in HostDcMode:
            item = mapped(acquisition(mode))
            self.assertEqual(item.processing_metadata.center_frequency_hz, 100e6)
            self.assertIs(item.processing_metadata.numerical_provenance.processing_recipe.dc_mode, mode)
            self.assertEqual(item.frame_sequence, 12)
            self.assertEqual(item.first_sample_index, 256)
            with self.assertRaises(FrozenInstanceError):
                item.processing_metadata.hop_size = 1

    def test_legacy_missing_and_throwing_metadata_stays_unknown(self):
        item = acquisition(HostDcMode.BLOCK_MEAN)
        del item.processing_metadata
        self.assertIsNone(mapped(item).processing_metadata)
        class Legacy:
            def __getattr__(self, name):
                if name == "processing_metadata":
                    raise RuntimeError("old binding unavailable")
                return getattr(item, name)
        self.assertIsNone(mapped(Legacy()).processing_metadata)

    def test_missing_dc_quality_and_bad_recipe_refused(self):
        item = acquisition(HostDcMode.BLOCK_MEAN)
        item.quality_flags = 0
        with self.assertRaisesRegex(ValueError, "DC_REMOVED"):
            mapped(item)
        item = acquisition()
        item.processing_metadata.dsp_processing_recipe.policy_digest = "fabricated"
        with self.assertRaisesRegex(ValueError, "policy_digest"):
            mapped(item)

    def test_metadata_cannot_transplant_other_fft_or_sample_rate(self):
        for name, value in (("sample_rate_hz", 20e6), ("fft_size", 512), ("hop_size", True)):
            item = acquisition()
            setattr(item.processing_metadata, name, value)
            with self.subTest(name=name), self.assertRaises(ValueError):
                mapped(item)

    def test_typed_geometry_and_quality_required(self):
        item = mapped(acquisition(HostDcMode.BLOCK_MEAN))
        with self.assertRaises(TypeError):
            replace(item, processing_metadata={})
        with self.assertRaisesRegex(ValueError, "DC_REMOVED"):
            replace(item, quality_flags=0)
        with self.assertRaises(ValueError):
            replace(item.processing_metadata, center_frequency_hz=float("nan"))

    def test_old_eight_position_record_remains_unknown(self):
        self.assertIsNone(SweepSegmentAcquisition(0, 7, 12, 256, 123, 8e6, 256, 512).processing_metadata)


class NativeSweepProcessingMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native unavailable") from error
        if not hasattr(cls.native.SweepSegmentAcquisition, "processing_metadata"):
            raise unittest.SkipTest("legacy native has no Sweep FFT metadata")

    def test_exact_numerical_contract_not_product_owner_authority(self):
        self.assertEqual(self.native.sweep_processing_metadata_contract(), {
            "schema_version": 1, "scope": "native_contributing_sweep_metadata_only",
            "full_owner_context": False, "mixed_processing_line_refused": True,
            "string_max_bytes": 256, "segment_record_reserved_bytes": 1024})

    def test_original_cpu_to_assembler_to_domain_off_and_block_mean(self):
        n = self.native
        for mode in HostDcMode:
            from sdr_monitor.domain.processing_policy import SdrProcessingPolicyV1
            backend = n.make_cpu_dsp_backend_for_policy_v1(SdrProcessingPolicyV1(dc_mode=mode).canonical_bytes())
            backend.configure(n.DspConfig(1024, 1024, n.WindowType.RECTANGULAR, n.DetectorType.SAMPLE,
                n.SpectrumUnit.DBFS_BIN, n.PrecisionMode.REFERENCE_F64, 1, 1, 8.6,
                n.CalibrationStatus.UNCALIBRATED, "", 5))
            sample_index = np.arange(1024)
            samples = (0.2 + 0.1j + 0.25 * np.exp(2j * np.pi * 128 * sample_index / 1024)).astype(np.complex64)
            backend.push_samples(samples, 8e6, 100e6, 256)
            frame = backend.poll_spectrum()[0]
            raw = n._make_test_single_segment_line(frame, 256, 512)
            line = _to_domain_line(raw)
            metadata = line.segment_acquisition[0].processing_metadata
            self.assertEqual(metadata.center_frequency_hz, frame.center_frequency_hz)
            self.assertEqual(metadata.hop_size, frame.hop_size)
            self.assertIs(metadata.numerical_provenance.processing_recipe.dc_mode, mode)
            self.assertEqual(metadata.numerical_provenance.window_normalization_version,
                             frame.window_normalization_version)
            self.assertEqual(raw.segment_acquisition[0].processing_metadata.fft_size, frame.fft_size)
            with self.assertRaises(AttributeError):
                raw.segment_acquisition[0].processing_metadata.fft_size = 512
            with self.assertRaises(AttributeError):
                raw.segment_acquisition[0].processing_metadata = None
