"""Memory/soak observation must deliver normal Qt deferred lifecycle events."""
import unittest

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from tests import test_app02_analyzer_workspace_product as fixture
from tests.ui_v2.run_app05_memory_inventory import qt_idle_turn, qt_wrapper_counts


class ProfileEventLoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_idle_turn_delivers_delete_later_without_gc_or_direct_delete(self):
        owner = QObject()
        deleted = []
        owner.destroyed.connect(lambda: deleted.append(True))
        owner.deleteLater()
        qt_idle_turn()
        self.assertEqual(deleted, [True])


class ProfileInspectorLifecycleTests(unittest.TestCase):
    setUpClass = classmethod(fixture.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = fixture.AnalyzerWorkspaceProductTests.setUp
    tearDown = fixture.AnalyzerWorkspaceProductTests.tearDown
    wait = fixture.AnalyzerWorkspaceProductTests.wait

    def test_navigation_does_not_accumulate_inspectors_or_model_listeners(self):
        qt_idle_turn()
        model = self.composition.analyzer_view_model
        initial_listeners = len(model._listeners)
        key = "sdr_monitor.ui.v2.workspaces.analyzer_inspector.AnalyzerInspector"
        initial_inspectors = qt_wrapper_counts().get(key, 0)
        self.assertEqual(initial_inspectors, 1)
        for _ in range(40):
            self.shell.select_workspace("calibration")
            qt_idle_turn()
            self.assertEqual(qt_wrapper_counts().get(key, 0), 0)
            self.assertLess(len(model._listeners), initial_listeners)
            self.shell.select_workspace("analyzer")
            qt_idle_turn()
            self.assertEqual(qt_wrapper_counts().get(key, 0), initial_inspectors)
            self.assertEqual(len(model._listeners), initial_listeners)


if __name__ == "__main__":
    unittest.main()
