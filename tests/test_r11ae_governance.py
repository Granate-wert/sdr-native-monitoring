"""R11-AE status/evidence governance tests; no Qt, PnP or serial access."""

from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from sdr_monitor.r11ae_tinysa_visible_ui_evidence import (
    R11AE_VISIBLE_EXECUTION_ENABLED,
    R11AEVisibleUiProfile,
    validate_r11ae_preflight,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs/reports/sdr/evidence/tinysa"


class R11AEGovernanceTests(unittest.TestCase):
    def test_authoritative_sources_name_active_execution_gate_without_visible_claim(self) -> None:
        expected = {
            "AGENTS.md": ("R11-AE is now", "NOT_EXECUTED"),
            "docs/current_project_state.md": ("active R11-AE checkpoint", "CELLS NOT_EXECUTED"),
            "docs/agent_handoff.md": (
                "R11-AE tinySA visible UI preparation handoff",
                "R11AE_VISIBLE_EXECUTION_ENABLE_PROCESS=1",
            ),
            "docs/refactoring/refactor_program_2026-08.md": (
                "R11-AE software/offscreen and frozen admission",
                "enabled drafts and four source-bound ready-v2",
            ),
            "TZ_SDR_native_monitoring/START_HERE.md": (
                "R11-AE has completed its Python-3.13",
                "R11AE_VISIBLE_EXECUTION_ENABLE_PROCESS=1",
            ),
        }
        for relative, tokens in expected.items():
            with self.subTest(relative=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                for token in tokens:
                    self.assertIn(token, source)
                self.assertNotIn("R11-AE may next prepare", source)

    def test_package_plan_keeps_r11ae_current_and_physical_action_confirmation_gated(self) -> None:
        plan = (ROOT / "TZ_SDR_native_monitoring/PACKAGE_PLAN.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'current_package: "R11-AE tinySA visible normal-product UI/DPI evidence preparation"',
            plan,
        )
        self.assertIn("per-process opt-in", plan)
        self.assertIn("R11-V/Y/AD stay consumed", plan)
        self.assertNotIn('current_package: "R11-AD', plan)

    def test_four_draft_preflights_are_exact_write_once_records(self) -> None:
        expected_hashes = {
            100: "3e0b6f79acf5cb5e3f22ac3388f7ff1687a0215c345312b590159aba89af8a04",
            150: "24900e6c9924f96eb876cf0192f8677b7b03e5321b9611b5397a4bf803aec94b",
            200: "0c8f10d7ab1a5886c7f5a7eb1fb3d56a7fbc554bd61889729bfcfb6106ecb419",
            300: "4893dbc323d6aba2b2d9d56bf02c7d626ba3b2924b665b3479de5364d5237901",
        }
        for scale, expected_digest in expected_hashes.items():
            with self.subTest(scale=scale):
                path = EVIDENCE / (
                    f"12-r11ae-draft-visible-ui-{scale}pct-preflight-v1-20260815.json"
                )
                raw = path.read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_digest)
                payload = json.loads(raw)
                validate_r11ae_preflight(payload, R11AEVisibleUiProfile(scale))
                self.assertFalse(payload["visible_execution_enabled"])

    def test_execution_and_release_guards_are_structurally_present(self) -> None:
        self.assertFalse(R11AE_VISIBLE_EXECUTION_ENABLED)
        runner = (ROOT / "scripts/r11ae_tinysa_visible_ui.py").read_text(
            encoding="utf-8"
        )
        self.assertLess(
            runner.index("if not r11ae_visible_execution_is_enabled():"),
            runner.index("profile = R11AEVisibleUiProfile"),
        )
        release = (ROOT / "build_sdr_release.ps1").read_text(encoding="utf-8")
        self.assertGreater(
            release.index("verify_sdr_frozen_tinysa_runtime.py"),
            release.index("verify_sdr_frozen_libiio_runtime.py"),
        )
        decision = (ROOT / "docs/architecture_decisions.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("## ADR-124", decision)
        self.assertIn("VISIBLE ACCEPTANCE OPEN", decision)


if __name__ == "__main__":
    unittest.main()
