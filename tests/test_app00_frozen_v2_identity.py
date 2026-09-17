"""Frozen verification must reject historical Legacy smoke evidence."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class FrozenV2IdentityTests(unittest.TestCase):
    def test_both_verifiers_require_v2_identity(self):
        for filename, function, native in (
            ("verify_sdr_frozen_shell.py", "verify_frozen_offscreen_shell", False),
            ("verify_sdr_frozen_default_shell.py", "verify_frozen_default_offscreen_shell", True),
        ):
            spec = importlib.util.spec_from_file_location(filename[:-3], ROOT / "scripts" / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            verdict = dict(
                qt_platform="offscreen", workspace="analyzer",
                window_title="SDR Native Monitoring — UI V2", startup_visible=True,
                closed=True, automatic_discovery_pending=False, live_running=False,
                live_service="NativeLiveSessionService" if native else "UnavailableLiveService",
                ui_mode="v2", shell_class="AppShellV2",
            )
            if native:
                verdict.update(native_device_constructed=False, native_engine_constructed=False, pluto_compiled=True)
            for mutation in ({}, {"ui_mode": "standalone"}, {"shell_class": "SDRAppShell"},
                             {"workspace": "home"}):
                with self.subTest(filename=filename, mutation=mutation), \
                     patch.object(Path, "is_file", return_value=True), \
                     patch.object(module.subprocess, "run", return_value=SimpleNamespace(
                         returncode=0, stdout=json.dumps(verdict | mutation), stderr="")):
                    if mutation:
                        with self.assertRaisesRegex(ValueError, "rejected"):
                            getattr(module, function)(ROOT)
                    else:
                        self.assertEqual(getattr(module, function)(ROOT)["shell_class"], "AppShellV2")


if __name__ == "__main__":
    unittest.main()
