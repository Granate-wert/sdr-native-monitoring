"""Persistence recovery and scratch ownership under shared presentation pressure."""
import unittest
import weakref
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.persistence_contracts import (
    PersistenceRenderMode, adapt_persistence_density,
)
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.waterfall.bounded_ring import BoundedWaterfallRenderer
from tests.ui_v2.test_spectrum_scene import _persistence_frame


class PersistencePressureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.scene = SpectrumScene()
        self.overlay = self.scene._persistence
        self.budget = PresentationAllocationBudget(1_000_000)
        self.overlay.allocation_budget = self.budget
        self.overlay.set_logarithmic(False)
        self.renderer = BoundedWaterfallRenderer()
        self.renderer.allocation_budget = self.budget

    def tearDown(self):
        self.overlay.clear_local_image()
        self.renderer.reset()
        self.scene.close()
        self.scene.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def view(self, value, width=16):
        return adapt_persistence_density(_persistence_frame(np.full((4, width), value, np.float32)))

    def visual_history(self):
        self.overlay.set_render_mode(PersistenceRenderMode.VISUAL)
        self.overlay.set_frame(self.view(.25), now_ns=1)
        self.overlay.set_frame(self.view(.75), now_ns=10**9)
        self.assertIsNotNone(self.overlay._row_scratch)
        return weakref.ref(self.overlay._row_scratch)

    def recovery(self, *, visual=False):
        if visual:
            self.overlay.set_render_mode(PersistenceRenderMode.VISUAL)
        self.overlay.set_frame(self.view(.25), now_ns=1)
        latest = self.view(.75)
        self.budget.observe(latest)
        self.renderer.append(np.ones(16, np.float32), rows=20, timestamp_ns=1)
        tiles = self.renderer.tiles()
        # Other actual image consumers keep these ring bytes alive after reset.
        # Accounted sources can exceed a subsequently tightened presentation cap.
        self.budget.limit_bytes = self.budget.snapshot().observed_bytes - 512
        with patch.object(self.overlay, "_render_image", side_effect=AssertionError("unadmitted map")):
            self.overlay.set_frame(latest, now_ns=10**9)
        self.assertTrue(self.overlay.allocation_limited)
        self.assertIsNone(self.overlay.image_item.image)
        self.assertIsNone(self.overlay._pending_view)
        self.assertFalse(self.overlay._timer.isActive())
        self.renderer.reset()
        self.overlay.set_visible(False)
        self.overlay.set_visible(True)
        self.assertTrue(self.overlay.allocation_limited)
        self.assertIsNone(self.overlay.image_item.image)
        denied = self.budget.snapshot().rejections
        for _ in range(5):
            self.overlay.set_visible(True)
            self.app.processEvents()
        self.assertEqual(self.budget.snapshot().rejections, denied)
        del tiles
        # No new source, no backend restart and no timer polling for capacity.
        self.overlay.set_visible(False)
        self.overlay.set_visible(True)
        self.assertFalse(self.overlay.allocation_limited)
        self.assertIs(self.overlay.latest_view, latest)
        expected = .25 + (.75 - .25) * .65 if visual else .75
        np.testing.assert_allclose(self.overlay.image_item.image, expected)
        self.assertIs(self.overlay._uploaded_density, latest.density)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)
        self.assertLessEqual(self.budget.snapshot().observed_bytes, self.budget.limit_bytes)
        self.assertFalse(self.overlay._timer.isActive())
        uploads = self.overlay.metrics.image_uploads
        self.overlay.set_visible(True)
        self.assertEqual(self.overlay.metrics.image_uploads, uploads)

    def test_direct_explicit_enable_recovers_latest_after_other_owner_releases(self):
        self.recovery()

    def test_visual_explicit_enable_recovers_history_after_other_owner_releases(self):
        self.recovery(visual=True)

    def test_direct_mode_releases_visual_scratch_even_while_hidden(self):
        scratch = self.visual_history()
        self.overlay.set_visible(False)
        before = self.budget.snapshot().observed_bytes
        self.overlay.set_render_mode(PersistenceRenderMode.DIRECT)
        self.assertIsNone(scratch())
        self.assertIsNone(self.overlay._row_scratch)
        self.assertEqual(self.budget.snapshot().observed_bytes, before - 64)

    def test_mapping_reset_releases_visual_scratch_even_while_hidden(self):
        scratch = self.visual_history()
        self.overlay.set_visible(False)
        self.overlay.set_logarithmic(True)
        self.assertIsNone(scratch())
        self.assertIsNone(self.overlay._row_scratch)

    def test_geometry_reset_drops_obsolete_scratch_before_failed_admission(self):
        scratch = self.visual_history()
        self.budget.limit_bytes = 1
        self.overlay.set_frame(self.view(.5, width=8), now_ns=2 * 10**9)
        self.assertTrue(self.overlay.allocation_limited)
        self.assertIsNone(scratch())
        self.assertIsNone(self.overlay._row_scratch)
        self.assertIsNone(self.overlay._visual_buffer)
        self.assertEqual(self.budget.snapshot().reserved_bytes, 0)


if __name__ == "__main__":
    unittest.main()
