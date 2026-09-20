"""Post-close diagnostics cannot root owners or hide fixture retention as UI leaks."""
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import weakref

from scripts.benchmark_app05_context_memory import direct_referrers


class ContextMemoryTests(unittest.TestCase):
    def test_referrer_evidence_is_scalar_and_does_not_retain_owner(self):
        class Owner:
            pass
        owner = Owner()
        reference = weakref.ref(owner)
        holder = SimpleNamespace(owner=owner)
        evidence = direct_referrers(reference)
        self.assertTrue(evidence["alive"])
        json.dumps(evidence)
        del holder, owner
        self.assertIsNone(reference())
        self.assertFalse(direct_referrers(reference)["alive"])

    def test_exception_hook_records_errors_without_capturing_fixture(self):
        from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests
        fixture = AnalyzerWorkspaceProductTests("runTest")
        fixture.setUpClass()
        fixture.setUp()
        try:
            hook = sys.excepthook.side_effect
            error = ValueError("test hook only")
            hook(type(error), error, None)
            self.assertIn("test hook only", fixture._qt_errors[0])
            fixture._qt_errors.clear()  # Expected synthetic error, before normal teardown assertion.
            self.assertFalse(any(cell.cell_contents is fixture for cell in (hook.__closure__ or ())),
                             "mock hook must not keep the entire fixture alive after unpatch")
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_repeated_context_cli_normal_gc_workers_and_reservations(self):
        root = Path(__file__).resolve().parents[2]
        with TemporaryDirectory(prefix="app05-contexts-") as temporary:
            output = Path(temporary) / "result.json"
            process = subprocess.run([sys.executable, "-I", "-X", "faulthandler",
                str(root / "scripts/benchmark_app05_context_memory.py"), "--checkout", str(root),
                "--output", str(output), "--contexts", "3", "--seconds", "1",
                "--bins", "4096", "--power-bins", "32", "--inspect-referrers"],
                cwd=root, capture_output=True, text=True, timeout=40)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(len(report["runs"]), 3)
        for index, (run, point) in enumerate(zip(report["runs"], report["checkpoints"]), 1):
            self.assertEqual(run["remaining_workers"], [])
            self.assertEqual(run["product_imports_outside_checkout"], [])
            self.assertFalse(run["memory"]["collect_after_context"])
            self.assertGreater(run["persistence"]["overlay_metrics"]["image_uploads"], 0)
            self.assertEqual(len(point["closed_contexts"]), index)
            self.assertTrue(all(row["allocation_budget"]["reserved_bytes"] == 0
                                for row in point["closed_contexts"]))
        # Ordinary GC has no fixed deadline: no assertion that every weak owner
        # vanishes within 500ms and no forced collection to make that assertion pass.
