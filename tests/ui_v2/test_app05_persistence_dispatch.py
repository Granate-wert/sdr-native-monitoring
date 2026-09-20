"""Density mapping must not precede coherent spectrum dispatch on V2 delivery."""
from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode, adapt_persistence_density
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement
from tests.ui_v2.test_spectrum_scene import _persistence_frame


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


class DeferredDensityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = SpectrumScene()
        self.overlay = self.scene._persistence
        self.overlay.defer_frame_uploads = True
        self.overlay.set_logarithmic(False)

    def tearDown(self):
        self.overlay.clear_local_image()
        self.scene.close()
        self.scene.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def view(self, value):
        return adapt_persistence_density(_persistence_frame(np.full((4, 16), value, np.float32)))

    def test_latest_only_slot_and_cadence_are_preserved(self):
        first, latest = self.view(.25), self.view(.75)
        self.overlay.set_frame(first, now_ns=1)
        self.overlay.set_frame(latest, now_ns=2)
        self.assertIsNone(self.overlay.image_item.image)
        self.assertIs(self.overlay._pending_view, latest)
        self.overlay.flush_pending(now_ns=3)
        self.assertEqual(self.overlay.metrics.image_uploads, 1)
        self.assertIs(self.overlay._uploaded_density, latest.density)
        self.overlay.set_frame(first, now_ns=4)
        self.overlay.flush_pending(now_ns=5)
        self.assertEqual(self.overlay.metrics.image_uploads, 1)
        self.overlay.flush_pending(now_ns=10**9)
        self.assertEqual(self.overlay.metrics.image_uploads, 2)
        np.testing.assert_array_equal(self.overlay.image_item.image, first.density)

    def test_hide_clear_and_force_never_resurrect_discarded_density(self):
        self.overlay.set_frame(self.view(.25))
        self.overlay.set_presentation_active(False)
        self.app.processEvents()
        self.assertEqual(self.overlay.metrics.image_uploads, 0)
        self.overlay.set_presentation_active(True)
        self.assertTrue(self.overlay._pending_force)
        self.overlay.clear_local_image()
        self.app.processEvents()
        self.assertIsNone(self.overlay.image_item.image)
        self.assertIsNone(self.overlay._pending_view)
        self.assertFalse(self.overlay._pending_force)
        self.assertFalse(self.overlay._timer.isActive())

    def test_visual_show_reuses_history_and_superseding_latest(self):
        self.overlay.set_render_mode(PersistenceRenderMode.VISUAL)
        self.overlay.set_frame(self.view(.25), now_ns=1)
        self.overlay.flush_pending(now_ns=2)
        history = self.overlay._visual_buffer
        self.overlay.set_presentation_active(False)
        self.overlay.set_frame(self.view(.5), now_ns=3)
        self.overlay.set_presentation_active(True)
        newest = self.view(.75)
        self.overlay.set_frame(newest, now_ns=4)
        # Show force survives a newer accepted source and bypasses only the
        # first restore's cadence; it does not reset the Visual accumulator.
        self.overlay.flush_pending(now_ns=5)
        self.assertIs(self.overlay._visual_buffer, history)
        self.assertIs(self.overlay._uploaded_density, newest.density)
        np.testing.assert_allclose(history, .25 + (.75 - .25) * .65)
        self.assertFalse(self.overlay._pending_force)


if __name__ == "__main__":
    unittest.main()
