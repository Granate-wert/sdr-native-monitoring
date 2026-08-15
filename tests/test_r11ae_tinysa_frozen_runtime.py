"""R11-AE frozen tinySA dependency-admission tests without Qt or serial."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from scripts.verify_sdr_frozen_tinysa_runtime import verify_frozen_tinysa_runtime
from sdr_monitor import main as product_main


ROOT = Path(__file__).resolve().parents[1]


class _Backend:
    constructions = 0
    discovery_calls = 0

    def __init__(self) -> None:
        type(self).constructions += 1

    def discover_endpoints(self) -> tuple[object, ...]:
        type(self).discovery_calls += 1
        raise AssertionError("frozen tinySA runtime verification must not discover")


class R11AETinySaFrozenRuntimeTests(unittest.TestCase):
    def test_source_verdict_refuses_non_frozen_before_optional_imports(self) -> None:
        with patch.object(product_main.importlib, "import_module") as importer:
            with self.assertRaisesRegex(RuntimeError, "requires a frozen executable"):
                product_main._packaged_tinysa_runtime_verdict()  # noqa: SLF001
        importer.assert_not_called()

    def test_frozen_verdict_imports_dependencies_and_constructs_without_discovery(self) -> None:
        serial_module = types.ModuleType("serial")
        serial_module.__version__ = "3.5"
        pyside_module = types.ModuleType("PySide6")
        pyside_module.__version__ = "6.9.1"
        services_package = types.ModuleType("sdr_monitor.services")
        services_package.__path__ = []  # type: ignore[attr-defined]
        backend_module = types.ModuleType(
            "sdr_monitor.services.tinysa_serial_source_backend"
        )
        backend_module.TinySaSerialSourceBackend = _Backend
        _Backend.constructions = 0
        _Backend.discovery_calls = 0

        def optional_import(name: str) -> types.ModuleType:
            return {"serial": serial_module, "PySide6": pyside_module}[name]

        modules = {
            "sdr_monitor.services": services_package,
            "sdr_monitor.services.tinysa_serial_source_backend": backend_module,
        }
        with (
            patch.object(sys, "frozen", True, create=True),
            patch.dict(sys.modules, modules),
            patch.object(product_main.importlib, "import_module", side_effect=optional_import),
        ):
            observed = product_main._packaged_tinysa_runtime_verdict()  # noqa: SLF001

        self.assertEqual(
            observed,
            {
                "backend_constructed": True,
                "device_discovery_invoked": False,
                "pyside6_available": True,
                "pyside6_version": "6.9.1",
                "pyserial_available": True,
                "pyserial_version": "3.5",
                "serial_port_opened": False,
            },
        )
        self.assertEqual(_Backend.constructions, 1)
        self.assertEqual(_Backend.discovery_calls, 0)

    def test_external_verifier_accepts_only_exact_inert_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw)
            executable = package / "SDRNativeMonitoring.exe"
            executable.touch()
            payload = {
                "backend_constructed": True,
                "device_discovery_invoked": False,
                "pyside6_available": True,
                "pyside6_version": "6.9.1",
                "pyserial_available": True,
                "pyserial_version": "3.5",
                "serial_port_opened": False,
            }
            completed = subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(payload),
                stderr="",
            )
            with patch(
                "scripts.verify_sdr_frozen_tinysa_runtime.subprocess.run",
                return_value=completed,
            ) as run:
                self.assertEqual(verify_frozen_tinysa_runtime(package), payload)
            command = run.call_args.args[0]
            self.assertEqual(command[1], "--verify-packaged-tinysa-runtime")
            self.assertEqual(run.call_args.kwargs["env"]["SDR_AUTO_DISCOVER"], "0")

            payload["device_discovery_invoked"] = True
            completed.stdout = json.dumps(payload)
            with (
                patch(
                    "scripts.verify_sdr_frozen_tinysa_runtime.subprocess.run",
                    return_value=completed,
                ),
                self.assertRaisesRegex(ValueError, "device_discovery_invoked"),
            ):
                verify_frozen_tinysa_runtime(package)

    def test_release_order_and_early_command_branch_are_explicit(self) -> None:
        main_source = (ROOT / "sdr_monitor/main.py").read_text(encoding="utf-8")
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        self.assertLess(
            main_source.index("if arguments.verify_packaged_tinysa_runtime:"),
            main_source.index("logger = _configure_logging()"),
        )
        self.assertIn("verify_sdr_frozen_tinysa_runtime.py", release)
        self.assertGreater(
            release.index("verify_sdr_frozen_tinysa_runtime.py"),
            release.index("verify_sdr_frozen_libiio_runtime.py"),
        )


if __name__ == "__main__":
    unittest.main()
