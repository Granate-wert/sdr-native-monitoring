"""Native numerical recipe boundary; synthetic CPU only, no SDK/RX or UI authority."""

import hashlib
import importlib
import json
import unittest

import numpy as np

from sdr_monitor.domain.processing_policy import (
    HostDcMode, HostSpurMode, SdrProcessingPolicyV1, SpurProfileReference,
)


class NativeProcessingRecipeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native module unavailable") from error
        if not hasattr(cls.native, "dsp_processing_contract"):
            raise unittest.SkipTest("legacy native has no numerical recipe provenance")

    def config(self):
        n = self.native
        return n.DspConfig(256, 256, n.WindowType.RECTANGULAR, n.DetectorType.SAMPLE,
                           n.SpectrumUnit.DBFS_BIN, n.PrecisionMode.REFERENCE_F64, 1, 1, 8.6,
                           n.CalibrationStatus.UNCALIBRATED, "", 5)

    def frame(self, backend, first=0, center=100e6, rate=61.44e6):
        backend.push_samples(np.full(256, 0.25 + 0.125j, dtype=np.complex64), rate, center, first)
        frames = backend.poll_spectrum()
        self.assertEqual(len(frames), 1)
        return frames[0]

    def test_truthful_cpu_only_recipe_handshake_not_owner_authority(self):
        n = self.native
        contract = n.dsp_processing_contract()
        self.assertEqual(contract["schema_version"], 1)
        self.assertEqual(contract["policy_schema_version"], 1)
        self.assertEqual(contract["scope"], "dsp_recipe_only")
        self.assertFalse(contract["full_owner_context"])
        self.assertEqual(contract["producer_backends"], ("cpu-pocketfft",))
        self.assertEqual(contract["dc_modes"], ("off", "block_mean_v1"))
        self.assertEqual(contract["spur_modes"], ("off",))
        self.assertFalse(contract["comparison"])
        self.assertTrue(contract["canonical_input_required"])
        self.assertEqual(contract["max_policy_bytes"], 16384)
        self.assertEqual(contract["inline_recipe_bytes"], 1)
        self.assertGreater(contract["frame_slot_reserved_bytes"], contract["inline_recipe_bytes"])
        self.assertEqual(n.CONTRACT_SCHEMA_VERSION, 5)

    def test_actual_producer_recipe_matches_domain_canonical_bytes_and_hash(self):
        for dc in HostDcMode:
            with self.subTest(dc=dc):
                policy = SdrProcessingPolicyV1(dc_mode=dc)
                backend = self.native.make_cpu_dsp_backend_for_policy_v1(policy.canonical_bytes())
                backend.configure(self.config())
                frame = self.frame(backend)
                recipe = frame.dsp_processing_recipe
                self.assertEqual(recipe.scope, "dsp_recipe_only")
                self.assertEqual(recipe.schema_version, 1)
                self.assertEqual(recipe.dc_mode, dc.value)
                self.assertEqual(recipe.spur_mode, "off")
                self.assertEqual(recipe.canonical_policy_json.encode(), policy.canonical_bytes())
                self.assertEqual(recipe.policy_digest, policy.digest)
                self.assertEqual(recipe.policy_digest, "sha256:" + hashlib.sha256(
                    recipe.canonical_policy_json.encode()).hexdigest())
                self.assertEqual(recipe.whole_frame_modified, dc is HostDcMode.BLOCK_MEAN)
                self.assertIsNone(recipe.hardware_dc_tracking)
                self.assertFalse(recipe.comparison_applied)
                self.assertEqual(bool(frame.quality_flags & (1 << 9)), dc is HostDcMode.BLOCK_MEAN)
                self.assertEqual(frame.center_frequency_hz, 100e6)
                self.assertEqual(frame.sample_rate_hz, 61.44e6)
                self.assertEqual(frame.first_sample_index, 0)
                if dc is HostDcMode.BLOCK_MEAN:
                    self.assertTrue(np.all(np.isneginf(frame.values)))
                else:
                    self.assertAlmostEqual(float(frame.values[128]), 10 * np.log10(0.25**2 + 0.125**2), places=5)

    def test_no_constructor_or_mutable_recipe_fields(self):
        backend = self.native.CpuDspBackend()
        backend.configure(self.config())
        recipe = self.frame(backend).dsp_processing_recipe
        with self.assertRaises(TypeError):
            self.native.DspProcessingRecipeV1()
        for field, value in (("dc_mode", "block_mean_v1"), ("policy_digest", "fake"),
                             ("whole_frame_modified", True)):
            with self.subTest(field=field), self.assertRaises(AttributeError):
                setattr(recipe, field, value)

    def test_historical_test_constructor_stays_unknown(self):
        frame = self.native._make_test_spectrum_frame(16)
        self.assertIsNone(frame.dsp_processing_recipe)

    def test_noncanonical_malformed_and_wrong_type_refused(self):
        factory = self.native.make_cpu_dsp_backend_for_policy_v1
        off = SdrProcessingPolicyV1().canonical_bytes()
        for payload in (None, True, 1, off.decode(), bytearray(off), memoryview(off), b"", b"{}",
                        off + b" ", json.dumps(json.loads(off), indent=2).encode(), b"x" * 16385,
                        b"\xff", b"\x00", off.replace(b'"schema_version":1', b'"schema_version":true'),
                        off.replace(b'"dc_mode":"off"', b'"dc_mode":"off","dc_mode":"off"')):
            with self.subTest(payload_type=type(payload).__name__), self.assertRaises(self.native.ConfigurationError):
                factory(payload)

    def test_unsupported_comparison_and_profiles_refused_not_silent_off(self):
        profile = SpurProfileReference("test-only", 1, "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        for dc in HostDcMode:
            requests = [SdrProcessingPolicyV1(dc_mode=dc, compare_raw=True)]
            requests.extend(SdrProcessingPolicyV1(dc, spur, profile) for spur in HostSpurMode
                            if spur is not HostSpurMode.OFF)
            for policy in requests:
                with self.subTest(dc=dc, spur=policy.spur_mode), self.assertRaises(self.native.ConfigurationError):
                    self.native.make_cpu_dsp_backend_for_policy_v1(policy.canonical_bytes())

    def test_default_off_and_recipe_survive_reset_with_actual_new_grid(self):
        for factory in (self.native.CpuDspBackend,
                        lambda: self.native.make_cpu_dsp_backend_for_policy_v1(SdrProcessingPolicyV1().canonical_bytes())):
            backend = factory()
            backend.configure(self.config())
            old = self.frame(backend)
            backend.reset()
            fresh = self.frame(backend, first=9000, center=101e6, rate=16e6)
            self.assertEqual(old.dsp_processing_recipe.policy_digest, fresh.dsp_processing_recipe.policy_digest)
            self.assertEqual(fresh.dsp_processing_recipe.dc_mode, "off")
            self.assertEqual(fresh.first_sample_index, 9000)
            self.assertEqual(fresh.center_frequency_hz, 101e6)
            self.assertEqual(fresh.sample_rate_hz, 16e6)
