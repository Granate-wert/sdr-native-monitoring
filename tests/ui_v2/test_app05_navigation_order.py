"""Shell navigation restores the exact stopped source without backend work."""
import unittest

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement


class NavigationOrderTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def test_stopped_last_frame_reprojects_after_navigation_without_new_publication(self):
        self.select_and_apply()
        snapshot = measurement(self, 5)
        scene = self.page.visualization.spectrum_scene
        self.presenter._emit_snapshot(snapshot)
        self.wait(lambda: scene.displayed_frame is not None and
                  scene.displayed_frame.spectrum is snapshot.spectrum)
        displayed = scene.displayed_frame
        events = list(self.events)
        for width in (1280, 1920, 2560):
            self.shell.select_workspace("calibration")
            self.assertFalse(scene._presentation_active)
            self.assertIsNone(scene.displayed_frame)
            self.shell.resize(width, 900)
            self.shell.select_workspace("analyzer")
            self.wait(lambda: scene.displayed_frame is displayed and
                      self.composition.spectrum_projector._future is None and
                      scene._projection_key is not None and
                      scene._projection_key[1] == scene._viewport())
            self.assertTrue(self.page.primary.isVisible())
            self.assertEqual(self.events, events)
            self.assertFalse(self.live.is_running())


if __name__ == "__main__":
    unittest.main()
