"""P05 CPU DSP backend: golden parity and engine integration tests."""

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path

import numpy as np

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import (
    CONTRACT_SCHEMA_VERSION,
    ContractValidationError,
    DspConfig,
    PrecisionMode,
    SpectrumFrame,
    SpectrumUnit,
    WindowType,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "sdr_golden_vectors"

# Tolerances fixed by docs/architecture/sdr_golden_reference.md; they must
# not be widened to pass the backend.
PSD_RTOL = 2e-5
PSD_ATOL = 1e-12
PEAK_DB_TOLERANCE = 5e-5
INTEGRATED_RTOL = 2e-5


def _native_dsp_available() -> bool:
    if not native_api.native_availability().available:
        return False
    try:
        module = native_api.require_native()
    except native_api.NativeModuleUnavailableError:
        return False
    return hasattr(module, "CpuDspBackend")


def _golden_files() -> list[Path]:
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="ascii"))
    return [GOLDEN_DIR / entry["file"] for entry in manifest["vectors"]]


class DspFallbackTests(unittest.TestCase):
    def test_missing_module_reports_controlled_unavailability(self) -> None:
        def failing_importer(name: str) -> object:
            raise ImportError(f"no module named {name!r}")

        availability, _module = native_api.probe_native(
            "definitely_missing_sdr_native_dsp",
            importer=failing_importer,
        )
        self.assertFalse(availability.available)


class DspSchemaTests(unittest.TestCase):
    def test_fft_size_power_of_two_contract(self) -> None:
        DspConfig(fft_size=256, hop_size=256)
        DspConfig(fft_size=262_144, hop_size=1)
        for bad in (0, 128, 1023, 1025, 300, 524_288):
            with self.subTest(bad=bad), self.assertRaises(ContractValidationError):
                DspConfig(fft_size=bad, hop_size=1)


@unittest.skipUnless(
    _native_dsp_available(),
    "compiled P05 _sdr_native module is unavailable",
)
class NativeGoldenParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = native_api.require_native()

    def _dsp_config(self, unit: object, precision: object | None = None) -> object:
        native = self.native
        return native.DspConfig(
            1024,
            1024,
            native.WindowType.HANN,
            native.DetectorType.SAMPLE,
            unit,
            precision or native.PrecisionMode.REFERENCE_F64,
            1,
            1,
            8.6,
            native.CalibrationStatus.UNCALIBRATED,
            "",
            CONTRACT_SCHEMA_VERSION,
        )

    def _run_backend(self, samples: np.ndarray, rate: float, center: float, unit: object):
        backend = self.native.CpuDspBackend()
        backend.configure(self._dsp_config(unit))
        backend.push_samples(samples, rate, center)
        return backend.poll_spectrum(0), backend.metrics()

    def test_golden_vectors_match_reference(self) -> None:
        files = _golden_files()
        self.assertEqual(len(files), 12)
        for path in files:
            with self.subTest(vector=path.name):
                with np.load(path) as vector:
                    samples = np.ascontiguousarray(vector["input_iq"])
                    rate = float(vector["sample_rate"])
                    center = float(vector["center_frequency"])
                    expected_psd = vector["expected_psd"]
                    expected_peak = float(vector["expected_peak"])
                    expected_frequency = float(vector["expected_frequency"])
                    expected_axis = vector["expected_frequency_axis"]
                    expected_integrated = float(vector["expected_integrated_power"])

                frames, metrics = self._run_backend(samples, rate, center, self.native.SpectrumUnit.DBFS_HZ)
                self.assertEqual(len(frames), 1)
                self.assertEqual(metrics.fft_frames_computed, 1)
                frame = frames[0]
                np.testing.assert_array_equal(
                    np.asarray(frame.frequencies_hz),
                    expected_axis,
                    err_msg="frequency axis must match exactly",
                )
                actual_psd = np.power(10.0, np.asarray(frame.values, dtype=np.float64) / 10.0)
                np.testing.assert_allclose(
                    actual_psd,
                    expected_psd,
                    rtol=PSD_RTOL,
                    atol=PSD_ATOL,
                )
                bin_width = rate / 1024.0
                integrated = float(actual_psd.sum() * bin_width)
                self.assertAlmostEqual(
                    integrated,
                    expected_integrated,
                    delta=abs(expected_integrated) * INTEGRATED_RTOL,
                )

                frames_bin, _metrics_bin = self._run_backend(
                    samples, rate, center, self.native.SpectrumUnit.DBFS_BIN
                )
                self.assertEqual(len(frames_bin), 1)
                values_bin = np.asarray(frames_bin[0].values)
                peak_bin = int(np.argmax(values_bin))
                self.assertAlmostEqual(float(values_bin[peak_bin]), expected_peak, delta=PEAK_DB_TOLERANCE)
                self.assertEqual(
                    float(np.asarray(frames_bin[0].frequencies_hz)[peak_bin]),
                    expected_frequency,
                    "frequency-bin identity must be exact",
                )

    def test_metrics_fields_and_immutability(self) -> None:
        with np.load(_golden_files()[0]) as vector:
            samples = np.ascontiguousarray(vector["input_iq"])
            rate = float(vector["sample_rate"])
            center = float(vector["center_frequency"])
        _frames, metrics = self._run_backend(samples, rate, center, self.native.SpectrumUnit.DBFS_BIN)
        for name in ("fft_frames_computed", "fft_frames_dropped", "samples_processed", "output_pending"):
            self.assertIsInstance(getattr(metrics, name), int, name)
        with self.assertRaises(AttributeError):
            metrics.fft_frames_computed = 0
        config = self._dsp_config(self.native.SpectrumUnit.DBFS_BIN)
        with self.assertRaises(AttributeError):
            config.fft_size = 512

    def test_native_rejects_non_power_of_two_fft(self) -> None:
        native = self.native
        with self.assertRaises(native.ConfigurationError):
            native.DspConfig(
                1023,
                1,
                native.WindowType.HANN,
                native.DetectorType.SAMPLE,
                native.SpectrumUnit.DBFS_BIN,
                native.PrecisionMode.REFERENCE_F64,
                1,
                1,
                8.6,
                native.CalibrationStatus.UNCALIBRATED,
                "",
                CONTRACT_SCHEMA_VERSION,
            )

    def test_push_samples_rejects_strided_views(self) -> None:
        with np.load(_golden_files()[0]) as vector:
            samples = np.ascontiguousarray(vector["input_iq"])
        backend = self.native.CpuDspBackend()
        backend.configure(self._dsp_config(self.native.SpectrumUnit.DBFS_BIN))
        with self.assertRaises(self.native.ConfigurationError):
            backend.push_samples(samples[::2], 1_024_000.0, 100_000_000.0)

    def test_engine_end_to_end_spectrum(self) -> None:
        native = self.native
        engine = native.SyntheticEngine()
        dsp = native.DspConfig(
            256,
            256,
            native.WindowType.RECTANGULAR,
            native.DetectorType.SAMPLE,
            native.SpectrumUnit.DBFS_BIN,
            native.PrecisionMode.REFERENCE_F64,
            1,
            1,
            8.6,
            native.CalibrationStatus.UNCALIBRATED,
            "",
            CONTRACT_SCHEMA_VERSION,
        )
        engine.configure(
            native.EngineConfig(
                block_size_samples=1024,
                blocks_per_second=200,
                max_blocks=16,
                spectrum_queue_capacity=64,
                dsp=dsp,
            )
        )
        engine.start()
        deadline = time.monotonic() + 60.0
        while engine.state() == native.EngineState.RUNNING and time.monotonic() < deadline:
            time.sleep(0.005)
        engine.join()
        self.assertEqual(engine.state(), native.EngineState.STOPPED)
        metrics = engine.metrics()
        self.assertEqual(metrics.iq_blocks_received, 16)
        dsp_stats = engine.queue_stats(native.QueueId.DSP)
        # P04 shutdown contract: in-flight blocks at auto-stop are counted as
        # dropped, never silently lost; every consumed block yields 4 frames
        # (1024 samples / 256 hop).
        self.assertEqual(
            metrics.iq_blocks_received,
            dsp_stats.popped + metrics.iq_blocks_dropped,
        )
        self.assertEqual(metrics.fft_frames_computed, 4 * dsp_stats.popped)
        self.assertGreater(metrics.fft_frames_computed, 0)
        self.assertGreater(metrics.analytical_fft_rate, 0.0)
        frames = engine.poll_spectrum_frames(0)
        self.assertTrue(frames)
        frame = SpectrumFrame.from_native(frames[-1])
        self.assertEqual(frame.fft_size, 256)
        self.assertEqual(frame.unit, SpectrumUnit.DBFS_BIN)
        self.assertEqual(frame.window, WindowType.RECTANGULAR)
        self.assertEqual(frame.precision_mode, PrecisionMode.REFERENCE_F64)
        self.assertFalse(frame.values.flags.writeable)
        self.assertFalse(frame.frequencies_hz.flags.writeable)
        self.assertTrue(np.isfinite(frame.frequencies_hz).all())


if __name__ == "__main__":
    unittest.main()
