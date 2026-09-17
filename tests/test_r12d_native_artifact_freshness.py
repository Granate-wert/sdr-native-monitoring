"""R12-D tests for native-artifact and Python-contract freshness guards."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.preflight_sdr_native_build import (
    ContractExpectations,
    ContractSurfaceError,
    _file_sha256,
    load_contract_expectations,
    validate_active_artifact,
    validate_contract_surface,
    validate_manifest,
)


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_SOURCE = ROOT / "esw_dfl/sdr/contracts.py"


def _native_module(expectations: ContractExpectations) -> SimpleNamespace:
    """Build a no-runtime stand-in matching the public native contract shape."""

    native_quality = type("QualityFlag", (), dict(expectations.enums["QualityFlag"]))
    schema = {
        "schema": expectations.schema_name,
        "schema_version": expectations.schema_version,
        "enums": {name: dict(members) for name, members in expectations.enums.items()},
    }
    return SimpleNamespace(
        CONTRACT_SCHEMA_NAME=expectations.schema_name,
        CONTRACT_SCHEMA_VERSION=expectations.schema_version,
        QualityFlag=native_quality,
        contract_schema=lambda: schema,
    )


class R12DNativeArtifactFreshnessTests(unittest.TestCase):
    def test_static_contract_source_contains_the_reserved_v5_discontinuity_bit(self) -> None:
        expectations = load_contract_expectations(CONTRACT_SOURCE)

        self.assertEqual(expectations.schema_name, "sdr-native-contracts")
        self.assertEqual(expectations.schema_version, 5)
        self.assertEqual(expectations.enums["QualityFlag"]["BACKEND_DISCONTINUITY"], 1 << 15)

    def test_matching_native_contract_surface_is_accepted_without_importing_legacy_packages(self) -> None:
        expectations = load_contract_expectations(CONTRACT_SOURCE)

        validate_contract_surface(_native_module(expectations), expectations)  # type: ignore[arg-type]

    def test_missing_native_quality_flag_is_rejected(self) -> None:
        expectations = load_contract_expectations(CONTRACT_SOURCE)
        module = _native_module(expectations)
        native_schema = module.contract_schema()
        del native_schema["enums"]["QualityFlag"]["BACKEND_DISCONTINUITY"]

        with self.assertRaisesRegex(ContractSurfaceError, "QualityFlag wire mapping"):
            validate_contract_surface(module, expectations)  # type: ignore[arg-type]

    def test_active_artifact_and_manifest_must_match_the_staged_release_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            staged = directory / "_sdr_native.cp313-win_amd64.pyd"
            active = directory / "active" / staged.name
            active.parent.mkdir()
            staged.write_bytes(b"staged native artifact")
            active.write_bytes(staged.read_bytes())
            manifest = {
                "cuda_compiled": True,
                "python_abi": "cp313-win_amd64",
                "native_version": "0.6.0",
                "artifact_sha256": _file_sha256(staged),
            }

            validate_manifest(staged, manifest, expected_cuda=True)
            validate_active_artifact(staged, manifest, active, dict(manifest))

            active.write_bytes(b"stale active artifact")
            with self.assertRaisesRegex(ContractSurfaceError, "active native module hash"):
                validate_active_artifact(staged, manifest, active, dict(manifest))

    def test_release_build_script_runs_a_second_preflight_after_atomic_activation(self) -> None:
        script = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")

        self.assertIn("artifact_sha256", script)
        self.assertIn('"--active-module", $active', script)
        self.assertIn('"--active-manifest", $activeManifest', script)
        self.assertGreater(script.index('"--active-module", $active'), script.index("Move-Item -LiteralPath $part"))


if __name__ == "__main__":
    unittest.main()
