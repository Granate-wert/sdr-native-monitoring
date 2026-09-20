"""Density mapping must not precede coherent spectrum dispatch on V2 delivery."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement


class PersistenceDispatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.f = product.AnalyzerWorkspaceProductTests("runTest")
        self.f.app = self.app
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.select_and_apply()

    def test_actual_delivery_admits_all_layers_and_dispatches_before_density_mapping(self):
        f = self.f
        scene = f.page.visualization.spectrum_scene
        overlay, port = scene._persistence, f.composition.spectrum_projector
        snapshot = measurement(f)
        snapshot = replace(snapshot, persistence=synthetic_persistence(snapshot.spectrum, 16, 1, 1))
        events = []
        submit, render = port._submit, overlay._render_image

        def submitting(operation):
            events.append("submit")
            self.assertIs(scene.latest_frame.spectrum, snapshot.spectrum)
            self.assertIs(overlay.latest_view.source_frame,
                          f.composition.view_model.state.persistence_frame)
            self.assertIsNotNone(f.composition.view_model.state.waterfall_line)
            return submit(operation)

        def mapping(view):
            events.append("map")
            return render(view)

        with patch.object(port, "_submit", side_effect=submitting), \
             patch.object(overlay, "_render_image", side_effect=mapping):
            f.presenter._emit_snapshot(snapshot)
            f.wait(lambda: overlay.metrics.image_uploads > 0 and scene.displayed_frame is not None)
        self.assertLess(events.index("submit"), events.index("map"))
        self.assertIs(scene.displayed_frame.spectrum, snapshot.spectrum)
        self.assertEqual(f.events, [])  # Stopped latest restoration needs no Start/restart.

    def test_show_defers_mapping_and_restores_stopped_latest_without_publication(self):
        f = self.f
        scene = f.page.visualization.spectrum_scene
        snapshot = measurement(f)
        snapshot = replace(snapshot, persistence=synthetic_persistence(snapshot.spectrum, 16, 1, 1))
        f.presenter._emit_snapshot(snapshot)
        f.wait(lambda: scene._persistence.metrics.image_uploads > 0)
        scene.set_presentation_active(False)
        latest = scene._persistence.latest_view
        before = scene._persistence.metrics.image_uploads
        # No GUI numerical mapping on the show call stack; the existing Qt
        # pending timer restores the exact accepted stopped density afterwards.
        with patch.object(scene._persistence, "_render_image", side_effect=AssertionError("sync show map")):
            scene.set_presentation_active(True)
        f.wait(lambda: scene._persistence.metrics.image_uploads == before + 1)
        self.assertIs(scene._persistence.latest_view, latest)
        self.assertIs(scene._persistence._uploaded_density, latest.density)
        self.assertEqual(f.events, [])


if __name__ == "__main__":
    unittest.main()
