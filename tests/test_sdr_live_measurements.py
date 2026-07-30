from __future__ import annotations

import unittest

import numpy as np

from esw_dfl.models import MeasurementQuality
from esw_dfl.sdr.contracts import (
    CalibrationStatus,
    DetectorType,
    PrecisionMode,
    QualityFlag,
    SourceType,
    SpectrumFrame,
    SpectrumUnit,
    WindowType,
)
from esw_dfl.domain import SourceDescriptor
from esw_dfl.sdr.measurements import LiveMeasurementAdapter


class LiveMeasurementAdapterTests(unittest.TestCase):
    frequencies = np.arange(9, dtype=np.float64)
    values = np.asarray([-60, -60, -60, -10, -10, -60, -60, -60, -60], dtype=np.float32)

    @classmethod
    def make_frame(
        cls,
        *,
        unit: SpectrumUnit = SpectrumUnit.DBM_BIN,
        calibration: CalibrationStatus = CalibrationStatus.APPLIED,
        flags: QualityFlag = QualityFlag.NONE,
        frame_sequence: int = 41,
        config_generation: int = 7,
        values: np.ndarray | None = None,
        dropped_fft_frames_before: int = 0,
        uncertainty: float = 0.25,
    ) -> SpectrumFrame:
        profile = "synthetic-profile" if calibration in (
            CalibrationStatus.APPLIED,
            CalibrationStatus.INTERPOLATED,
            CalibrationStatus.EXTRAPOLATED,
        ) else None
        return SpectrumFrame(
            source=SourceDescriptor(SourceType.SYNTHETIC, "fixture", "P11 fixture"),
            frame_sequence=frame_sequence,
            first_sample_index=frame_sequence * 8,
            timestamp_ns=1_700_000_000_000_000_000 + frame_sequence,
            config_generation=config_generation,
            center_frequency_hz=100.0,
            sample_rate_hz=9.0,
            analog_bandwidth_hz=9.0,
            fft_bin_width_hz=1.0,
            enbw_hz=1.0,
            nominal_rbw_hz=1.0,
            fft_size=9,
            hop_size=4,
            window=WindowType.HANN,
            detector=DetectorType.AVERAGE_POWER,
            precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
            unit=unit,
            frequencies_hz=cls.frequencies,
            values=cls.values if values is None else values,
            calibration_status=calibration,
            calibration_profile_id=profile,
            estimated_uncertainty_db=uncertainty,
            dropped_fft_frames_before=dropped_fft_frames_before,
            quality_flags=flags,
        )

    def test_channel_power_matches_linear_bin_sum(self) -> None:
        result = LiveMeasurementAdapter(self.make_frame()).channel_power(2.5, 4.5)
        self.assertIsNotNone(result.value)
        self.assertAlmostEqual(result.value.integrated.power_dbm, 10.0 * np.log10(0.2), places=6)
        self.assertEqual(result.frame_sequence, 41)
        self.assertEqual(result.config_generation, 7)
        self.assertEqual(result.source_id, "fixture")
        self.assertEqual(result.uncertainty_db, 0.25)

    def test_psd_integration_uses_physical_bandwidth(self) -> None:
        frame = self.make_frame(unit=SpectrumUnit.DBM_HZ)
        result = LiveMeasurementAdapter(frame).channel_power(2.5, 4.5)
        self.assertAlmostEqual(result.value.integrated.power_dbm, 10.0 * np.log10(0.2), places=6)

    def test_dbfs_integrated_power_is_rejected(self) -> None:
        frame = self.make_frame(
            unit=SpectrumUnit.DBFS_BIN,
            calibration=CalibrationStatus.UNCALIBRATED,
            flags=QualityFlag.UNCALIBRATED,
        )
        result = LiveMeasurementAdapter(frame).channel_power(2.5, 4.5)
        self.assertIsNone(result.value)
        self.assertEqual(result.quality, MeasurementQuality.UNSUPPORTED)
        self.assertIn("unsupported_unit", {item.code for item in result.warnings})

    def test_uncalibrated_relative_peak_is_explicitly_approximate(self) -> None:
        frame = self.make_frame(
            unit=SpectrumUnit.DBFS_BIN,
            calibration=CalibrationStatus.UNCALIBRATED,
            flags=QualityFlag.UNCALIBRATED,
        )
        result = LiveMeasurementAdapter(frame).peak(limit=1)
        self.assertEqual(result.quality, MeasurementQuality.APPROXIMATE)
        self.assertEqual(result.value[0].frequency_hz, 3.0)
        self.assertIn("uncalibrated", {item.code for item in result.warnings})

    def test_agc_drops_and_interval_drops_are_visible(self) -> None:
        frame = self.make_frame(
            flags=QualityFlag.GAIN_MODE_AGC | QualityFlag.IQ_DROPPED,
            dropped_fft_frames_before=2,
        )
        result = LiveMeasurementAdapter(frame, interval_dropped_frames=3).peak(limit=1)
        self.assertEqual(result.quality, MeasurementQuality.LIMITED)
        codes = {item.code for item in result.warnings}
        self.assertTrue({"agc_active", "dropped_frames", "dropped_frames_interval"} <= codes)

    def test_partial_region_is_limited_and_warns(self) -> None:
        result = LiveMeasurementAdapter(self.make_frame()).channel_power(7.5, 10.5)
        self.assertEqual(result.quality, MeasurementQuality.LIMITED)
        self.assertIn("partial_frequency_coverage", {item.code for item in result.warnings})

    def test_aclr_obw_noise_snr_and_peak_share_one_frame_identity(self) -> None:
        adapter = LiveMeasurementAdapter(self.make_frame())
        aclr = adapter.acpr(4.0, 2.0, 3.0, adjacent_bandwidth_hz=2.0)
        obw = adapter.occupied_bandwidth(0.99)
        noise = adapter.noise_floor(0.0, 2.0)
        snr = adapter.snr((2.5, 4.5), (0.0, 2.0))
        peak = adapter.peak(limit=1)
        for result in (aclr, obw, noise, snr, peak):
            self.assertEqual(result.frame_sequence, 41)
            self.assertEqual(result.config_generation, 7)
            self.assertEqual(result.source_id, "fixture")
            self.assertEqual(result.timestamp_ns, 1_700_000_000_000_000_041)
        self.assertIsNotNone(snr.value)
        self.assertGreater(snr.value.snr_db, 0.0)
        self.assertEqual(peak.value[0].index, 3)

    def test_edge_bin_quality_flag_is_preserved(self) -> None:
        frame = self.make_frame(flags=QualityFlag.EDGE_BIN)
        result = LiveMeasurementAdapter(frame).channel_power(2.5, 4.5)
        self.assertEqual(result.quality, MeasurementQuality.LIMITED)
        self.assertIn("edge_bin", {item.code for item in result.warnings})


if __name__ == "__main__":
    unittest.main()
