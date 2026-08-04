"""S11 separate-process DPI, accessibility and bounded performance tests."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.app_shell import SDRAppShell, WorkspaceId
from sdr_monitor.ui.performance import FrameRateMeter, MemoryPlateau
from sdr_monitor.ui.validation import DPI_MATRIX, audit_accessibility, run_dpi_probe, shortcut_collisions


def app() -> QApplication:
    return QApplication.instance() or QApplication([])


class S11AccessibilityPerformanceTests(unittest.TestCase):
    def test_each_dpi_runs_in_a_separate_process(self) -> None:
        for dpi in DPI_MATRIX:
            completed = run_dpi_probe(dpi)
            self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
            self.assertIn("dpi-ok", completed.stdout)

    def test_accessible_names_and_keyboard_workspace_workflow(self) -> None:
        app()
        shell = SDRAppShell()
        shell.show()
        shell.activateWindow()
        shell.setFocus()
        app().processEvents()
        try:
            report = audit_accessibility(shell)
            self.assertTrue(report.passed, report.issues)
            QTest.keyClick(shell, Qt.Key.Key_W, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(shell.active_workspace, WorkspaceId.SWEEP)
            QTest.keyClick(shell, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(shell.active_workspace, WorkspaceId.CALIBRATION)
            self.assertGreaterEqual(report.named_widgets, report.focusable_widgets)
        finally:
            shell.close()

    def test_60hz_handler_p95_and_bounded_memory_measurement(self) -> None:
        meter = FrameRateMeter(capacity=512)
        for _ in range(256):
            meter.record(2.0)
        summary = meter.summary()
        self.assertLessEqual(summary.p95_ms, 16.67)
        self.assertTrue(summary.meets_60hz)
        memory = MemoryPlateau(capacity=8)
        for value in range(100, 108):
            memory.record(value * 1024)
        self.assertTrue(memory.summary()["bounded"])

    def test_duplicate_shortcuts_are_detected(self) -> None:
        self.assertEqual(shortcut_collisions({"home": "Ctrl+1", "live": "Ctrl+L"}), ())
        self.assertEqual(shortcut_collisions({"a": "Ctrl+R", "b": "ctrl+r"}), ("ctrl+r",))


if __name__ == "__main__":
    unittest.main()
