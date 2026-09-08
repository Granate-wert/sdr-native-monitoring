from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.ui_v2 import check_ui2_scope
from tests.ui_v2.ui2_scope import classify_paths, normalize_path


class Ui2ScopePolicyTests(unittest.TestCase):
    def test_normalizes_windows_path(self) -> None:
        self.assertEqual(
            normalize_path(r".\sdr_monitor\ui\v2\scenes\spectrum_scene.py"),
            "sdr_monitor/ui/v2/scenes/spectrum_scene.py",
        )

    def test_allows_presentation_and_composition_seams(self) -> None:
        violations = classify_paths(
            (
                "sdr_monitor/ui/v2/scenes/spectrum_scene.py",
                "tests/ui_v2/test_spectrum_scene.py",
                "docs/ui_v2/BASELINE.json",
                "sdr_monitor/main.py",
                "README.md",
            )
        )
        self.assertEqual(violations, ())

    def test_rejects_backend_and_legacy_changes(self) -> None:
        violations = classify_paths(
            (
                "native/sdr_core/src/dsp/cpu_dsp_backend.cpp",
                "sdr_monitor/services/native_live.py",
                "sdr_monitor/ui/workspaces/live.py",
            )
        )
        self.assertEqual(
            [(item.path, item.reason) for item in violations],
            [
                ("native/sdr_core/src/dsp/cpu_dsp_backend.cpp", "protected backend path"),
                ("sdr_monitor/services/native_live.py", "protected backend path"),
                ("sdr_monitor/ui/workspaces/live.py", "outside UI V2 scope"),
            ],
        )

    def test_deduplicates_before_scope_evaluation(self) -> None:
        violations = classify_paths(
            (
                "sdr_monitor/domain/live.py",
                r"sdr_monitor\domain\live.py",
            )
        )
        self.assertEqual(len(violations), 1)

    def test_cli_collects_uncommitted_and_untracked_paths(self) -> None:
        with patch.object(
            check_ui2_scope,
            "_git_paths",
            side_effect=(
                ("sdr_monitor/ui/v2/committed.py",),
                ("sdr_monitor/ui/v2/unstaged.py",),
                ("tests/ui_v2/staged.py",),
                ("tests/ui_v2/untracked.py",),
            ),
        ):
            self.assertEqual(
                check_ui2_scope.changed_files("base"),
                (
                    "sdr_monitor/ui/v2/committed.py",
                    "sdr_monitor/ui/v2/unstaged.py",
                    "tests/ui_v2/staged.py",
                    "tests/ui_v2/untracked.py",
                ),
            )
