"""Optional Sweep ABI pair must refuse old/mismatched native modules before SDK."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from scripts.preflight_sdr_native_build import ContractSurfaceError, validate_hackrf_factory
from sdr_monitor.services.hackrf_sweep_contract import hackrf_sweep_contract_version
from sdr_monitor.services.source_capability_providers import _qualified_hackrf_sdk_directory
from tests.test_app06_frozen_shared_runtime import native_fixture, package_fixture


class HackrfSweepContractTests(unittest.TestCase):
    @staticmethod
    def native() -> SimpleNamespace:
        return SimpleNamespace(
            HACKRF_FACTORY_CONTRACT_VERSION=2,
            create_hackrf_runtime_dsp_control=Mock(),
            create_hackrf_sweep_runtime_control=Mock(),
            HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION=1,
            HACKRF_SWEEP_FACTORY_CONTRACT_VERSION=1,
        )

    @staticmethod
    def manifest() -> dict[str, object]:
        return {
            "hackrf_official_compiled": True,
            "hackrf_factory_contract_version": 2,
            "hackrf_sweep_bridge_contract_version": 1,
            "hackrf_sweep_factory_contract_version": 1,
        }

    def test_exact_pair_and_legacy_absence(self):
        native, manifest = self.native(), self.manifest()
        self.assertEqual(hackrf_sweep_contract_version(native, manifest), 1)
        validate_hackrf_factory(native, manifest)
        del native.HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION
        del native.HACKRF_SWEEP_FACTORY_CONTRACT_VERSION
        del native.create_hackrf_sweep_runtime_control
        manifest.pop("hackrf_sweep_bridge_contract_version")
        manifest.pop("hackrf_sweep_factory_contract_version")
        self.assertIsNone(hackrf_sweep_contract_version(native, manifest))
        validate_hackrf_factory(native, manifest)

    def test_every_half_pair_and_invalid_version_refuses_before_factory(self):
        for key in ("HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION",
                    "HACKRF_SWEEP_FACTORY_CONTRACT_VERSION",
                    "create_hackrf_sweep_runtime_control"):
            with self.subTest(missing_native=key):
                native, manifest = self.native(), self.manifest()
                delattr(native, key)
                with self.assertRaises(ValueError):
                    hackrf_sweep_contract_version(native, manifest)
                with self.assertRaises(ContractSurfaceError):
                    validate_hackrf_factory(native, manifest)
                native.create_hackrf_runtime_dsp_control.assert_not_called()
        for key in ("hackrf_sweep_bridge_contract_version",
                    "hackrf_sweep_factory_contract_version"):
            for value in (None, True, 0, 2, "1"):
                with self.subTest(manifest_key=key, value=value):
                    native, manifest = self.native(), self.manifest()
                    manifest[key] = value
                    with self.assertRaises(ValueError):
                        hackrf_sweep_contract_version(native, manifest)
                    with self.assertRaises(ContractSurfaceError):
                        validate_hackrf_factory(native, manifest)
                    native.create_hackrf_sweep_runtime_control.assert_not_called()

    def test_catalog_rejects_tampered_pair_without_discovery_or_rx(self):
        with tempfile.TemporaryDirectory() as tmp:
            module, _, manifest = package_fixture(Path(tmp).resolve())
            native = native_fixture(module)
            native.HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION = 1
            native.HACKRF_SWEEP_FACTORY_CONTRACT_VERSION = 1
            native.create_hackrf_sweep_runtime_control = Mock()
            manifest.update(self.manifest())
            path = module.parent / "native_build_manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertEqual(_qualified_hackrf_sdk_directory(native), module.parent)
            manifest.pop("hackrf_sweep_factory_contract_version")
            path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertIsNone(_qualified_hackrf_sdk_directory(native))
            native.create_hackrf_sweep_runtime_control.assert_not_called()
            native.scan_pluto_contexts.assert_not_called()

    def test_optional_metrics_exact_pair_legacy_absence_and_mismatch_refusal(self):
        native, manifest = self.native(), self.manifest()
        validate_hackrf_factory(native, manifest)
        native.HACKRF_SWEEP_METRICS_CONTRACT_VERSION = 1
        manifest["hackrf_sweep_metrics_contract_version"] = 1
        validate_hackrf_factory(native, manifest)
        for observed, declared in ((None, 1), (1, None), (True, 1), (1, True),
                                   (0, 1), (1, 2), ("1", 1), (1, "1")):
            with self.subTest(observed=observed, declared=declared):
                native.HACKRF_SWEEP_METRICS_CONTRACT_VERSION = observed
                manifest["hackrf_sweep_metrics_contract_version"] = declared
                with self.assertRaises(ContractSurfaceError):
                    validate_hackrf_factory(native, manifest)
                native.create_hackrf_sweep_runtime_control.assert_not_called()
        native.HACKRF_SWEEP_METRICS_CONTRACT_VERSION = 1
        manifest.pop("hackrf_sweep_metrics_contract_version")
        with self.assertRaises(ContractSurfaceError):
            validate_hackrf_factory(native, manifest)
        manifest["hackrf_sweep_metrics_contract_version"] = 1
        del native.HACKRF_SWEEP_METRICS_CONTRACT_VERSION
        with self.assertRaises(ContractSurfaceError):
            validate_hackrf_factory(native, manifest)


if __name__ == "__main__":
    unittest.main()
