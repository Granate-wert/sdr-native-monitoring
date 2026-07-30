from __future__ import annotations

import math
import unittest

import numpy as np

from esw_dfl.sdr.contracts import DetectorType, WindowType
from esw_dfl.sdr.reference_dsp import (
    apply_detector,
    coherent_gain,
    equivalent_noise_bandwidth,
    integrate_psd,
    integrated_band_power,
    normalize_adc,
    parseval_windowed_power,
    reference_spectrum,
    window_coefficients,
)
from esw_dfl.sdr.synthetic import SyntheticConfig, complex_tone


class ReferenceDspTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SyntheticConfig(sample_count=4096, sample_rate_hz=4_096_000.0)

    def test_exact_bin_tone_is_zero_dbfs_after_coherent_gain_correction(self) -> None:
        frequency = 321.0 * self.config.bin_width_hz
        signal = complex_tone(self.config, frequency)
        for window in WindowType:
            with self.subTest(window=window.value):
                spectrum = reference_spectrum(signal.samples, self.config.sample_rate_hz, window=window)
                peak = int(np.argmax(spectrum.dbfs_per_bin))
                self.assertAlmostEqual(spectrum.frequencies_hz[peak], frequency, places=9)
                self.assertAlmostEqual(spectrum.dbfs_per_bin[peak], 0.0, delta=2e-10)

    def test_coherent_gain_for_every_window(self) -> None:
        size = 65_536
        expected = {
            WindowType.RECTANGULAR: 1.0,
            WindowType.HANN: 0.5 * (size - 1) / size,
            WindowType.BLACKMAN_HARRIS_4TERM: 0.35875 + (-0.48829 + 0.14128 - 0.01168) / size,
            WindowType.FLAT_TOP: 0.21557895 + (-0.41663158 + 0.277263158 - 0.083578947 + 0.006947368) / size,
            WindowType.NUTTALL: 0.355768 + (-0.487396 + 0.144232 - 0.012604) / size,
        }
        for window in WindowType:
            with self.subTest(window=window.value):
                coefficients = window_coefficients(window, size)
                gain = coherent_gain(coefficients)
                self.assertTrue(math.isfinite(gain))
                self.assertGreater(gain, 0.0)
                if window in expected:
                    self.assertAlmostEqual(gain, expected[window], delta=2e-12)

    def test_known_equivalent_noise_bandwidths(self) -> None:
        size = 65_536
        expected_bins = {
            WindowType.RECTANGULAR: 1.0,
            WindowType.HANN: 1.500022888532845,
            WindowType.BLACKMAN_HARRIS_4TERM: 2.004383512452347,
            WindowType.FLAT_TOP: 3.7703042025049287,
            WindowType.NUTTALL: 2.0212634203318767,
            WindowType.KAISER: 1.7214003273207084,
        }
        for window, expected in expected_bins.items():
            with self.subTest(window=window.value):
                value, value_hz = equivalent_noise_bandwidth(
                    window_coefficients(window, size),
                    float(size),
                )
                self.assertAlmostEqual(value, expected, delta=2e-9)
                self.assertAlmostEqual(value_hz, expected, delta=2e-9)

    def test_parseval_and_full_psd_integration_agree(self) -> None:
        rng = np.random.default_rng(0x503033)
        samples = rng.normal(size=2048) + 1j * rng.normal(size=2048)
        coefficients = window_coefficients(WindowType.BLACKMAN_HARRIS_4TERM, samples.size)
        spectrum = reference_spectrum(
            samples,
            2_048_000.0,
            window=WindowType.BLACKMAN_HARRIS_4TERM,
        )
        expected = parseval_windowed_power(samples, coefficients)
        actual = integrate_psd(spectrum.psd_dbfs_per_hz_linear, spectrum.bin_width_hz)
        self.assertAlmostEqual(actual, expected, delta=2e-12 * expected)

    def test_band_integration_uses_linear_domain(self) -> None:
        frequency = -512.0 * self.config.bin_width_hz
        signal = complex_tone(self.config, frequency, amplitude=0.5)
        spectrum = reference_spectrum(
            signal.samples,
            self.config.sample_rate_hz,
            window=WindowType.RECTANGULAR,
        )
        integrated = integrated_band_power(
            spectrum.frequencies_hz,
            spectrum.psd_dbfs_per_hz_linear,
            frequency - self.config.bin_width_hz / 2.0,
            frequency + self.config.bin_width_hz / 2.0,
            density=True,
            bin_width_hz=spectrum.bin_width_hz,
        )
        self.assertAlmostEqual(integrated, 0.25, delta=2e-12)

    def test_adc_normalization_and_range_validation(self) -> None:
        normalized = normalize_adc(
            np.asarray([-32768, 0, 32767], dtype=np.int16),
            np.asarray([0, -16384, 16384], dtype=np.int16),
            32768,
        )
        np.testing.assert_allclose(
            normalized,
            np.asarray([-1.0 + 0.0j, -0.5j, 32767 / 32768 + 0.5j]),
            rtol=0.0,
            atol=0.0,
        )
        with self.assertRaises(ValueError):
            normalize_adc([32768], [0], 32768)

    def test_detectors_operate_on_linear_power(self) -> None:
        frames = (
            np.asarray([1.0, 4.0, 2.0]),
            np.asarray([9.0, 1.0, 3.0]),
        )
        np.testing.assert_array_equal(
            apply_detector(frames, DetectorType.PEAK),
            np.asarray([9.0, 4.0, 3.0]),
        )
        np.testing.assert_array_equal(
            apply_detector(frames, DetectorType.AVERAGE_POWER),
            np.asarray([5.0, 2.5, 2.5]),
        )
        np.testing.assert_array_equal(
            apply_detector(frames, DetectorType.SAMPLE),
            frames[-1],
        )


if __name__ == "__main__":
    unittest.main()
