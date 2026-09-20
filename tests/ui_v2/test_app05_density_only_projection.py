"""Density-only updates must not recompute already displayed required layers."""
import unittest
from unittest.mock import patch

from sdr_monitor.ui.v2.spectrum import projection
from tests.ui_v2 import test_app05_persistence_wiring as wiring


class DensityOnlyProjectionTests(unittest.TestCase):
    setUpClass = classmethod(wiring.PersistenceWiringTests.setUpClass.__func__)
    tearDown = wiring.PersistenceWiringTests.tearDown
    pump = wiring.PersistenceWiringTests.pump
    drain = wiring.PersistenceWiringTests.drain
    spectrum = wiring.PersistenceWiringTests.spectrum
    density = wiring.PersistenceWiringTests.density

    def setUp(self):
        wiring.PersistenceWiringTests.setUp(self)
        self.scene.setFixedSize(1800, 700)
        self.pump()
        self.drain()

    def test_density_only_updates_preserve_required_pixels_without_reducer(self):
        before = self.scene.displayed_frame
        with patch.object(projection, "peak_preserving_envelope", wraps=projection.peak_preserving_envelope) as reduce, \
             patch.object(self.scene, "_paint_trace", wraps=self.scene._paint_trace) as paint:
            for value in (.2, .7):
                self.scene._persistence._last_upload_ns = 0
                self.density(value)
                self.drain()
            self.assertEqual(reduce.call_count, 0)
            self.assertEqual(paint.call_count, 0)
        self.assertIs(self.scene.displayed_frame, before)
        self.assertEqual(self.scene.persistence_metrics.image_uploads, 2)

    def test_new_source_while_density_is_owned_requires_exact_new_projection(self):
        with patch.object(projection, "peak_preserving_envelope", wraps=projection.peak_preserving_envelope) as reduce:
            self.density()
            active = self.port._future
            fresh = self.spectrum(-42)
            self.scene.commit_projection()
            self.assertIs(self.port._future, active)
            self.assertIsNotNone(self.port._pending)
            self.drain()
            self.assertEqual(reduce.call_count, 1)
        self.assertIs(self.scene.displayed_frame, fresh)
        self.assertEqual(self.scene.persistence_metrics.image_uploads, 1)

    def test_changed_viewport_requires_reduction_even_for_density_update(self):
        with patch.object(projection, "peak_preserving_envelope", wraps=projection.peak_preserving_envelope) as reduce:
            self.scene.plot_item.setXRange(100.2e6, 100.8e6, padding=0)
            self.density()
            self.drain()
            self.assertGreater(reduce.call_count, 0)
        self.assertEqual(self.scene._displayed_projection_geometry[1], self.scene._viewport())
