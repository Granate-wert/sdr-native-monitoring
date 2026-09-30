"""Exact wide geometry, paired old-native refusal, no hidden FFT reduction."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.preflight_sdr_native_build import ContractSurfaceError, validate_hackrf_factory

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.sweep_geometry_contract import (
    require_extended_sweep_geometry, sweep_geometry_contract,
)
from tests.test_app06_hackrf_sweep_display import setup_owner


class ExtendedSweepGeometryTests(unittest.TestCase):
    def test_standalone_preflight_pairs_geometry_even_when_hackrf_sdk_is_off(self):
        native = SimpleNamespace(SWEEP_GEOMETRY_CONTRACT_VERSION=1, SWEEP_MAX_SEGMENTS=2048,
                                 SWEEP_MAX_REDUCED_BYTES=134217728)
        manifest = {"sweep_geometry_contract_version": 1, "sweep_max_segments": 2048,
                    "sweep_max_reduced_bytes": 134217728, "hackrf_official_compiled": False}
        validate_hackrf_factory(native, manifest)
        validate_hackrf_factory(SimpleNamespace(), {"hackrf_official_compiled": False})
        for updates in ({"sweep_max_segments": 64}, {"sweep_max_reduced_bytes": 67108864},
                        {"sweep_geometry_contract_version": True}, {"sweep_geometry_contract_version": 2}):
            with self.subTest(updates=updates), self.assertRaises(ContractSurfaceError):
                validate_hackrf_factory(native, {**manifest, **updates})
        with self.assertRaises(ContractSurfaceError):
            validate_hackrf_factory(native, {"hackrf_official_compiled": False})

    def test_optional_contract_is_strict_and_legacy_absence_is_explicit(self):
        native = SimpleNamespace(SWEEP_GEOMETRY_CONTRACT_VERSION=1, SWEEP_MAX_SEGMENTS=2048,
                                 SWEEP_MAX_REDUCED_BYTES=134217728)
        manifest = {"sweep_geometry_contract_version": 1, "sweep_max_segments": 2048,
                    "sweep_max_reduced_bytes": 134217728}
        self.assertEqual(sweep_geometry_contract(native, manifest), 1)
        self.assertIsNone(sweep_geometry_contract(SimpleNamespace(), {}))
        for key, value in (("sweep_geometry_contract_version", True),
                           ("sweep_geometry_contract_version", 2),
                           ("sweep_max_segments", 64), ("sweep_max_segments", 2048.0),
                           ("sweep_max_reduced_bytes", True), ("sweep_max_reduced_bytes", 67108864)):
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                sweep_geometry_contract(native, {**manifest, key: value})
        with self.assertRaises(ValueError):
            sweep_geometry_contract(native, {})

    def test_extended_profile_requires_same_artifact_manifest_and_hash(self):
        with TemporaryDirectory() as directory:
            artifact = Path(directory) / "fixture.pyd"
            artifact.write_bytes(b"not-loaded-test-fixture")
            native = SimpleNamespace(__file__=str(artifact), SWEEP_GEOMETRY_CONTRACT_VERSION=1,
                                     SWEEP_MAX_SEGMENTS=2048, SWEEP_MAX_REDUCED_BYTES=134217728)
            manifest = {"sweep_geometry_contract_version": 1, "sweep_max_segments": 2048,
                        "sweep_max_reduced_bytes": 134217728,
                        "artifact_sha256": hashlib.sha256(artifact.read_bytes()).hexdigest()}
            sibling = artifact.with_name("native_build_manifest.json")
            sibling.write_text(json.dumps(manifest), encoding="utf-8")
            require_extended_sweep_geometry(native)
            sibling.write_text(json.dumps({**manifest, "artifact_sha256": "0" * 64}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "paired manifest"):
                require_extended_sweep_geometry(native)
            sibling.write_bytes(b" " * 16_385)
            with self.assertRaisesRegex(ValueError, "bound"):
                require_extended_sweep_geometry(native)

    def test_full_ad_geometry_is_pure_but_old_module_cannot_activate_it(self):
        profile = LiveConfiguration(center_hz=3e9, sample_rate_hz=61.44e6,
            analog_bandwidth_hz=40e6, fft_size=2048, backend=BackendKind.CPU)
        request = ContinuousSweepPlanRequest(70e6, 6e9, output_queue_capacity=2,
            analysis_bins_per_usable_window=1024)
        result = NativeContinuousSweepPlanFactory.preflight_profile(profile, request)
        self.assertEqual((result.segment_count, result.physical_fft_size), (175, 2048))
        self.assertLess(result.reduced.total_bytes, 64 * 1024 * 1024)
        with self.assertRaises(ValueError):
            NativeContinuousSweepPlanFactory.preflight_native_profile(SimpleNamespace(), profile, request)
        with self.assertRaisesRegex(ValueError, "memory budget"):
            NativeContinuousSweepPlanFactory.preflight_profile(
                replace(profile, fft_size=16384), replace(request, analysis_bins_per_usable_window=8192))

    def test_extended_hackrf_refuses_before_identity_factory_or_claim(self):
        for start, stop in ((1_000_000, 6_000_000_000), (100_000_000, 122_000_000)):
            service, native, _, exclusion, base, selection = setup_owner()
            request = replace(base, start_hz=start, stop_hz=stop, fft_size=1024)
            with self.subTest(stop=stop), patch.object(service._identity, "observe") as identity:
                with self.assertRaisesRegex(LiveAdmissionRejected, "extended Sweep"):
                    service.start(request, selection)
                identity.assert_not_called()
                native.create_hackrf_sweep_runtime_control.assert_not_called()
                self.assertEqual(exclusion.events, [])

    def test_hackrf_exact_stop_padding_and_gain_no_hidden_reduction(self):
        _, _, _, _, base, _ = setup_owner()
        full = replace(base, start_hz=1_000_000, stop_hz=6_000_000_000, fft_size=1024)
        self.assertEqual((full.stop_hz, full.hardware_stop_hz, full.geometry.segment_count),
                         (6_000_000_000, 6_001_000_000, 1200))
        self.assertEqual(full.geometry.physical_fft_size, 1024)
        self.assertLess(full.geometry.reduced.total_bytes, 64 * 1024 * 1024)
        with self.assertRaisesRegex(ValueError, "memory budget"):
            replace(full, fft_size=4096)
        with self.assertRaisesRegex(ValueError, "RF envelope"):
            replace(base, start_hz=5_970_000_000, stop_hz=6_000_000_000)
        partial = replace(base, stop_hz=122_000_000)
        self.assertEqual((partial.stop_hz, partial.hardware_stop_hz), (122_000_000, 140_000_000))


if __name__ == "__main__":
    unittest.main()
