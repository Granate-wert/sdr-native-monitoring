"""Isolated composition native binding is explicit and fails before Qt work."""
import hashlib
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from scripts.app05_scene_gpu_support import native_probe_arguments


class NativeProbeBindingTests(unittest.TestCase):
    def test_parent_arguments_keep_exact_file_and_hash(self):
        with TemporaryDirectory(prefix="app05-native-binding-") as directory:
            path = Path(directory) / "_sdr_native.fixture"
            path.write_bytes(b"metadata fixture only, not an extension")
            with patch("importlib.import_module", return_value=SimpleNamespace(__file__=str(path))) as load:
                args = native_probe_arguments()
            load.assert_called_once_with("sdr_monitor._sdr_native")
            self.assertEqual(args, ["--native-module", str(path.resolve()), "--native-sha256",
                                   hashlib.sha256(path.read_bytes()).hexdigest()])

    def test_missing_parent_extension_has_no_fallback(self):
        with patch("importlib.import_module", side_effect=ModuleNotFoundError("unavailable")):
            with self.assertRaises(ModuleNotFoundError):
                native_probe_arguments()

    def test_non_native_parent_path_is_refused(self):
        with TemporaryDirectory(prefix="app05-native-binding-") as directory:
            path = Path(directory) / "wrong.fixture"
            path.write_bytes(b"not native")
            with patch("importlib.import_module", return_value=SimpleNamespace(__file__=str(path))):
                with self.assertRaises(ValueError):
                    native_probe_arguments()

    def test_isolated_missing_pair_and_wrong_hash_refuse_without_output(self):
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-native-binding-") as directory:
            path = Path(directory) / "_sdr_native.fixture"
            path.write_bytes(b"not a loadable extension; hash refusal must precede import")
            cases = (["--native-module", str(path)], ["--native-sha256", "0" * 64],
                     ["--native-module", str(path), "--native-sha256", "0" * 64])
            for number, args in enumerate(cases):
                with self.subTest(case=number):
                    output = Path(directory) / f"refused-{number}.json"
                    run = subprocess.run([sys.executable, "-I", str(root / "scripts/probe_app05_scene_gpu.py"),
                        "--checkout", str(root), "--output", str(output), *args],
                        cwd=root, timeout=40, capture_output=True, text=True, encoding="utf-8")
                    self.assertEqual(run.returncode, 2, run.stdout + run.stderr)
                    self.assertFalse(output.exists())
                    self.assertFalse(output.with_suffix("").exists())
                    self.assertNotIn("Traceback", run.stderr)
                    self.assertNotIn("QApplication", run.stderr)
                    self.assertEqual(run.stdout, "")


if __name__ == "__main__":
    unittest.main()
