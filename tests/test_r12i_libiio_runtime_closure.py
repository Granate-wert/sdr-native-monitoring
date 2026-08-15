"""R12-I app-local libiio closure and verifier tests without native loading."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sdr_monitor import main as main_module
from scripts.verify_sdr_frozen_libiio_runtime import (
    LIBIIO_RUNTIME_COMPONENTS as VERIFIER_COMPONENTS,
)
from scripts.verify_sdr_frozen_libiio_runtime import (
    verify_frozen_libiio_runtime,
)
from sdr_monitor.libiio_runtime import (
    LIBIIO_RUNTIME_COMPONENTS,
    PackagedLibiioRuntimeError,
    configure_frozen_libiio_runtime,
    frozen_libiio_runtime_components,
)


class R12ILibiioRuntimeClosureTests(unittest.TestCase):
    def test_frozen_binding_requires_complete_closure_and_replaces_external_path(self) -> None:
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
                    self.assertEqual(
                        tuple(path.name for path in components), LIBIIO_RUNTIME_COMPONENTS
                    )
                    self.assertEqual(os.environ["LIBIIO_DLL_PATH"], str(components[0]))

    def test_source_tree_binding_is_noop(self) -> None:
        native = types.SimpleNamespace(__file__=__file__)
        with (
            patch.object(sys, "frozen", False, create=True),
            patch.dict(os.environ, {"LIBIIO_DLL_PATH": "C:\\external\\libiio.dll"}),
        ):
            self.assertEqual(configure_frozen_libiio_runtime(native), ())
            self.assertEqual(os.environ["LIBIIO_DLL_PATH"], "C:\\external\\libiio.dll")

    def test_verifier_requires_exact_dll_closure_and_load_only_result(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdr-r12i-verifier-") as temporary_root:
            root = Path(temporary_root)
            executable = root / "SDRNativeMonitoring.exe"
            executable.touch()
            runtime_dir = root / "_internal" / "sdr_monitor"
            runtime_dir.mkdir(parents=True)
            for component in VERIFIER_COMPONENTS:
                (runtime_dir / component).touch()
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "libiio_available": True,
                        "library_package_local": True,
                        "pluto_compiled": True,
                        "runtime_component_count": len(VERIFIER_COMPONENTS),
                        "libiio_major": 0,
                        "libiio_minor": 25,
                    }
                ),
                stderr="",
            )
            with patch(
                "scripts.verify_sdr_frozen_libiio_runtime.subprocess.run",
                return_value=completed,
            ) as run:
                observed = verify_frozen_libiio_runtime(root)
            self.assertEqual(observed["runtime_component_count"], len(VERIFIER_COMPONENTS))
            self.assertEqual(run.call_args.args[0][1], "--verify-packaged-libiio-runtime")
            self.assertIn("external-runtime-must-not-be-loaded.dll", run.call_args.kwargs["env"]["LIBIIO_DLL_PATH"])

            (runtime_dir / VERIFIER_COMPONENTS[-1]).unlink()
            with self.assertRaisesRegex(ValueError, "components are missing"):
                verify_frozen_libiio_runtime(root)

    def test_packaged_command_rejects_nonlocal_loader_and_never_scans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="sdr-r12i-command-") as temporary_root:
            package_dir = Path(temporary_root) / "_internal" / "sdr_monitor"
            package_dir.mkdir(parents=True)
            module_path = package_dir / "_sdr_native.pyd"
            module_path.touch()
            runtime_path = package_dir / LIBIIO_RUNTIME_COMPONENTS[0]
            for component in LIBIIO_RUNTIME_COMPONENTS:
                (package_dir / component).touch()
            runtime = types.SimpleNamespace(
                available=True,
                library_path=str(runtime_path),
                major=0,
                minor=25,
                error="",
            )
            scan_contexts = Mock()
            native = types.SimpleNamespace(
                __file__=str(module_path),
                build_info=lambda: {"pluto_compiled": True},
                pluto_runtime_info=lambda: runtime,
                scan_pluto_contexts=scan_contexts,
            )
            with (
                patch.object(sys, "frozen", True, create=True),
                patch.dict(os.environ, {"LIBIIO_DLL_PATH": "C:\\external\\libiio.dll"}),
                patch("sdr_monitor.main.importlib.import_module", return_value=native),
            ):
                observed = main_module._packaged_libiio_runtime_verdict()
            self.assertTrue(observed["library_package_local"])
            self.assertEqual(observed["runtime_component_count"], len(LIBIIO_RUNTIME_COMPONENTS))
            scan_contexts.assert_not_called()

            runtime.library_path = str(package_dir.parent / "libiio.dll")
            with (
                patch.object(sys, "frozen", True, create=True),
                patch("sdr_monitor.main.importlib.import_module", return_value=native),
                self.assertRaisesRegex(RuntimeError, "escaped the package-local runtime"),
            ):
                main_module._packaged_libiio_runtime_verdict()

    def test_release_wires_local_components_and_verifier_before_tinysa_check(self) -> None:
        release = (Path(__file__).resolve().parents[1] / "build_sdr_release.ps1").read_text(encoding="utf-8")
        main_source = (Path(__file__).resolve().parents[1] / "sdr_monitor" / "main.py").read_text(encoding="utf-8")
        self.assertIn("$libiioRuntimeNames", release)
        self.assertIn("SDR_LIBIIO_RUNTIME_DIR", release)
        self.assertIn("verify_sdr_frozen_libiio_runtime.py", release)
        self.assertIn('throw "standalone frozen package preflight failed"', release)
        self.assertLess(release.index("verify_sdr_frozen_libiio_runtime.py"), release.index("verify_sdr_frozen_tinysa_runtime.py"))
        self.assertIn("--verify-packaged-libiio-runtime", main_source)
        self.assertLess(
            main_source.index("if arguments.verify_packaged_libiio_runtime:"),
            main_source.index("logger = _configure_logging()"),
        )


if __name__ == "__main__":
    unittest.main()
