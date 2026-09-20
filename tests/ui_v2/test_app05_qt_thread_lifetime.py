"""Presenter destruction must not invalidate the application's borrowed thread."""
import gc
from pathlib import Path
import subprocess
import sys
import unittest

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
import shiboken6

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.run_app05_memory_inventory import qt_idle_turn
from tests.ui_v2.test_app05_prepared_live import measurement


class GuiThreadLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_refresh_and_destroy_preserve_borrowed_gui_thread(self):
        enabled = gc.isenabled()
        # Do not use app.thread() here: that changes PySide's binding parent and
        # can conceal the transient-presenter ownership regression being tested.
        thread = QThread.currentThread()
        for _ in range(5):
            fixture = product.AnalyzerWorkspaceProductTests("runTest")
            fixture.app = self.app
            fixture.setUp()
            try:
                fixture.select_and_apply()
                snapshot = measurement(fixture)
                fixture.presenter._emit_snapshot(snapshot)
                fixture.wait(lambda page=fixture.page: page._last_bundle is not None)
            finally:
                fixture.tearDown()
                fixture.doCleanups()
            self.assertTrue(shiboken6.isValid(thread), "presenter invalidated the GUI thread wrapper")
            del fixture
            gc.collect()  # deliberate regression stress, never product behavior
            self.assertTrue(shiboken6.isValid(thread))
            qt_idle_turn()
            self.assertIs(QThread.currentThread(), thread)
        self.assertEqual(gc.isenabled(), enabled)

    def test_original_inventory_event_loop_sequence_in_fresh_process(self):
        root = Path(__file__).resolve().parents[2]
        code = (
            "import pathlib,sys,unittest,gc; sys.path.insert(0,str(pathlib.Path.cwd())); "
            "assert gc.isenabled(); "
            "r=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.loadTestsFromNames("
            "['tests.ui_v2.test_app05_memory_inventory','tests.ui_v2.test_app05_profile_event_loop'])); "
            "assert gc.isenabled(); raise SystemExit(not r.wasSuccessful())"
        )
        result = subprocess.run([sys.executable, "-I", "-X", "faulthandler", "-c", code],
                                cwd=root, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # Inventory now also checks active required-stage roots and the optional
        # reserve. Keep exact completeness, including that additional case.
        self.assertIn("Ran 10 tests", result.stderr)


if __name__ == "__main__":
    unittest.main()
