"""R12-E clean-process canonical active-native-import tests."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.verify_sdr_native_active_import import (
    CANONICAL_MODULE_NAME,
    verify_active_import,
)


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "scripts/verify_sdr_native_active_import.py"
ACTIVE_MODULES = tuple((ROOT / "sdr_monitor").glob("_sdr_native*.pyd"))
ACTIVE_MANIFEST = ROOT / "sdr_monitor/native_build_manifest.json"


class R12ECanonicalActiveImportTests(unittest.TestCase):
    def test_active_import_rejects_a_digest_mismatch_before_any_module_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            module = directory / "_sdr_native.cp313-win_amd64.pyd"
            manifest = directory / "native_build_manifest.json"
            module.write_bytes(b"not a loadable native module")
            manifest.write_text(
                json.dumps(
                    {
                        "artifact_sha256": hashlib.sha256(b"other bytes").hexdigest(),
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match manifest"):
                verify_active_import(module, manifest)

    @unittest.skipUnless(len(ACTIVE_MODULES) == 1 and ACTIVE_MANIFEST.is_file(), "active native Release artifact is unavailable")
    def test_isolated_process_imports_one_expected_canonical_active_module(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(VERIFIER),
                "--module",
                str(ACTIVE_MODULES[0]),
                "--manifest",
                str(ACTIVE_MANIFEST),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        observed = json.loads(completed.stdout)
        self.assertEqual(observed["module_identity"], CANONICAL_MODULE_NAME)
        self.assertEqual(observed["module_names"], [CANONICAL_MODULE_NAME])
        self.assertEqual(Path(observed["module"]).resolve(), ACTIVE_MODULES[0].resolve())

    def test_legacy_preflight_filename_delegates_without_a_legacy_native_identity(self) -> None:
        legacy_preflight = (ROOT / "scripts/preflight_native_build.py").read_text(encoding="utf-8")

        self.assertIn("from preflight_sdr_native_build import main", legacy_preflight)
        self.assertNotIn("spec_from_file_location", legacy_preflight)
        self.assertNotIn("sys.modules[", legacy_preflight)

    def test_release_build_runs_the_isolated_import_verifier_after_activation(self) -> None:
        build_script = (ROOT / "build_native_sdr.ps1").read_text(encoding="utf-8")

        verifier_index = build_script.index("verify_sdr_native_active_import.py")
        activation_index = build_script.index("Move-Item -LiteralPath $part")
        self.assertGreater(verifier_index, activation_index)
        self.assertIn('"-I"', build_script)


if __name__ == "__main__":
    unittest.main()
