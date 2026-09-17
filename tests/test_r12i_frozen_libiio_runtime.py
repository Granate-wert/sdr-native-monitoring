"""R12-I source contracts for frozen app-local libiio admission."""

from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from sdr_monitor.libiio_runtime import (
    LIBIIO_RUNTIME_COMPONENTS,
    PackagedLibiioRuntimeError,
    configure_frozen_libiio_runtime,
    frozen_libiio_runtime_components,
)


ROOT = Path(__file__).resolve().parents[1]


class R12IFrozenLibiioRuntimeTests(unittest.TestCase):
    def test_frozen_binding_requires_complete_package_closure_and_overrides_external_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdr-r12i-") as temporary_root:
            package_dir = Path(temporary_root) / "_internal" / "sdr_monitor"
            package_dir.mkdir(parents=True)
            module_path = package_dir / "_sdr_native.pyd"
            module_path.touch()
            native = types.SimpleNamespace(__file__=str(module_path))
            with patch.object(sys, "frozen", True, create=True):
                with self.assertRaises(PackagedLibiioRuntimeError):
                    frozen_libiio_runtime_components(native)
                for component in LIBIIO_RUNTIME_COMPONENTS:
                    (package_dir / component).touch()
                with patch.dict(os.environ, {"LIBIIO_DLL_PATH": "C:\\external\\libiio.dll"}):
                    components = configure_frozen_libiio_runtime(native)
                    self.assertEqual(tuple(path.name for path in components), LIBIIO_RUNTIME_COMPONENTS)
                    self.assertEqual(os.environ["LIBIIO_DLL_PATH"], str(components[0]))

    def test_source_tree_binding_is_noop(self) -> None:
        native = types.SimpleNamespace(__file__=__file__)
        with patch.object(sys, "frozen", False, create=True):
            with patch.dict(os.environ, {"LIBIIO_DLL_PATH": "C:\\external\\libiio.dll"}):
                self.assertEqual(configure_frozen_libiio_runtime(native), ())
                self.assertEqual(os.environ["LIBIIO_DLL_PATH"], "C:\\external\\libiio.dll")

    def test_private_command_and_release_order_are_explicit(self) -> None:
        main = (ROOT / "sdr_monitor/main.py").read_text(encoding="utf-8")
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        verifier = (ROOT / "scripts/verify_sdr_frozen_libiio_runtime.py").read_text(encoding="utf-8")

        self.assertLess(
            main.index("if arguments.verify_packaged_libiio_runtime:"),
            main.index("logger = _configure_logging()"),
        )
        command_start = main.index("def _packaged_libiio_runtime_verdict")
        command_section = main[command_start : main.index("def _offscreen_shell_verdict", command_start)]
        self.assertIn("configure_frozen_libiio_runtime", command_section)
        self.assertIn("pluto_runtime_info", command_section)
        self.assertNotIn("scan_pluto_contexts", command_section)
        self.assertNotIn("PlutoDevice", command_section)
        self.assertIn("$libiioRuntimeNames", release)
        self.assertIn("SDR_LIBIIO_RUNTIME_DIR", release)
        self.assertGreater(
            release.index("verify_sdr_frozen_libiio_runtime.py"),
            release.index("verify_sdr_frozen_default_shell.py"),
        )
        self.assertIn("external-runtime-must-not-be-loaded.dll", verifier)
        self.assertIn("runtime_component_count", verifier)


if __name__ == "__main__":
    unittest.main()
