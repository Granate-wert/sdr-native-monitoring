"""Reject the observed Poppler ICU collision before launching frozen Qt."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("app00_preflight", ROOT / "scripts/preflight_sdr_release.py")
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class PackageRuntimeGuardTests(unittest.TestCase):
    def test_os_icu_is_not_required_as_a_private_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "SDRNativeMonitoring.exe").write_bytes(b"fixture")
            self.assertEqual(len(PREFLIGHT.build_manifest(root, "CPU", "test")["files"]), 1)

    def test_private_icu_is_rejected_in_any_package_directory(self):
        for name in ("icuuc.dll", "ICUUC.DLL", "icudt78.dll"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "SDRNativeMonitoring.exe").write_bytes(b"fixture")
                internal = root / "_internal" / "nested"
                internal.mkdir(parents=True)
                (internal / name).write_bytes(b"fixture")
                with self.assertRaisesRegex(ValueError, "unapproved private ICU runtime"):
                    PREFLIGHT.build_manifest(root, "CPU", "test")


if __name__ == "__main__":
    unittest.main()
