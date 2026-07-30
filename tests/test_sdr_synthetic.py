from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from esw_dfl.sdr.contracts import QualityFlag, WindowType
from esw_dfl.sdr.native_api import probe_native
from esw_dfl.sdr.reference_dsp import reference_spectrum
from esw_dfl.sdr.synthetic import (
    DEFAULT_SEED,
    SyntheticConfig,
    SyntheticScenario,
    generate_scenario,
    signal_sha256,
)
from tests.sdr_golden_vectors.generate_vectors import vector_arrays, write_golden_vectors


class SyntheticSignalTests(unittest.TestCase):
    def test_all_scenarios_are_finite_read_only_and_repeatable(self) -> None:
        config = SyntheticConfig()
        for scenario in SyntheticScenario:
            with self.subTest(scenario=scenario.value):
                first = generate_scenario(scenario, config)
                second = generate_scenario(scenario, config)
                self.assertEqual(first.samples.dtype, np.dtype("<c16"))
                self.assertEqual(first.samples.shape, (config.sample_count,))
                self.assertFalse(first.samples.flags.writeable)
                self.assertTrue(np.all(np.isfinite(first.samples)))
                self.assertEqual(signal_sha256(first), signal_sha256(second))
                np.testing.assert_array_equal(first.samples, second.samples)

    def test_seed_changes_noise_but_not_non_random_tone(self) -> None:
        first_config = SyntheticConfig(seed=DEFAULT_SEED)
        second_config = SyntheticConfig(seed=DEFAULT_SEED + 1)
        first_noise = generate_scenario(SyntheticScenario.BROADBAND_NOISE, first_config)
        second_noise = generate_scenario(SyntheticScenario.BROADBAND_NOISE, second_config)
        self.assertNotEqual(signal_sha256(first_noise), signal_sha256(second_noise))
        first_tone = generate_scenario(SyntheticScenario.EXACT_BIN_TONE, first_config)
        second_tone = generate_scenario(SyntheticScenario.EXACT_BIN_TONE, second_config)
        np.testing.assert_array_equal(first_tone.samples, second_tone.samples)

    def test_clipping_scenario_sets_overload_flag(self) -> None:
        signal = generate_scenario(SyntheticScenario.CLIPPING)
        self.assertTrue(signal.quality_flags & QualityFlag.ADC_OVERLOAD)
        self.assertGreater(int(signal.metadata["clipped_sample_count"]), 0)
        self.assertLess(np.max(np.abs(signal.samples.real)), 1.0)
        self.assertLess(np.max(np.abs(signal.samples.imag)), 1.0)

    def test_iq_imbalance_produces_image_component(self) -> None:
        signal = generate_scenario(SyntheticScenario.IQ_IMBALANCE)
        spectrum = reference_spectrum(
            signal.samples,
            signal.config.sample_rate_hz,
            window=WindowType.RECTANGULAR,
        )
        frequency = float(signal.metadata["frequency_offset_hz"])
        positive = int(np.argmin(np.abs(spectrum.frequencies_hz - frequency)))
        negative = int(np.argmin(np.abs(spectrum.frequencies_hz + frequency)))
        self.assertGreater(spectrum.dbfs_per_bin[positive] - spectrum.dbfs_per_bin[negative], 10.0)
        self.assertLess(spectrum.dbfs_per_bin[positive] - spectrum.dbfs_per_bin[negative], 40.0)

    def test_golden_vector_contract_contains_required_fields(self) -> None:
        arrays = vector_arrays(SyntheticScenario.EXACT_BIN_TONE)
        required = {
            "input_iq",
            "sample_rate",
            "center_frequency",
            "config",
            "expected_frequency",
            "expected_peak",
            "expected_integrated_power",
            "expected_psd",
        }
        self.assertTrue(required.issubset(arrays))
        config = json.loads(str(arrays["config"]))
        self.assertEqual(config["schema"], "sdr-golden-vector")
        self.assertEqual(config["scenario"], "exact_bin_tone")

    def test_checked_in_golden_vectors_regenerate_byte_for_byte(self) -> None:
        checked_in = Path(__file__).parent / "sdr_golden_vectors"
        expected_manifest = json.loads((checked_in / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as temporary:
            generated = Path(temporary)
            actual_manifest = write_golden_vectors(generated)
            self.assertEqual(actual_manifest, expected_manifest)
            for record in expected_manifest["vectors"]:
                name = record["file"]
                expected = (checked_in / name).read_bytes()
                actual = (generated / name).read_bytes()
                self.assertEqual(actual, expected, name)
                self.assertEqual(hashlib.sha256(actual).hexdigest().upper(), record["sha256"])
                with np.load(generated / name, allow_pickle=False) as archive:
                    self.assertEqual(set(archive.files), set(vector_arrays(SyntheticScenario(record["scenario"]))))

    def test_native_synthetic_skeleton_matches_schema_when_available(self) -> None:
        availability, native = probe_native()
        if not availability.available or native is None:
            self.skipTest(availability.reason or "native module unavailable")
        self.assertEqual(native.SYNTHETIC_SCHEMA_NAME, "sdr-synthetic-source")
        self.assertEqual(native.SYNTHETIC_SCHEMA_VERSION, 1)
        config = native.SyntheticSourceConfig(
            native.SyntheticScenario.EXACT_BIN_TONE,
            DEFAULT_SEED,
            1024,
            1_024_000.0,
            100_000_000.0,
            1,
        )
        source = native.SyntheticSourceSkeleton(config)
        self.assertEqual(source.descriptor().source_type, native.SourceType.SYNTHETIC)
        self.assertNotEqual(source.block_seed(0), source.block_seed(1))


if __name__ == "__main__":
    unittest.main()
