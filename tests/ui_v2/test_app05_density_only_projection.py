"""Density-only updates must not recompute already displayed required layers."""
import unittest
from dataclasses import replace
import threading
from unittest.mock import patch

from sdr_monitor.ui.v2.spectrum import projection
from sdr_monitor.ui.v2.spectrum.contracts import PreparedSpectrumFrame, TraceKind
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

    def test_changed_secondary_trace_requires_reduction_with_same_current_source(self):
        current = self.scene.displayed_frame
        secondary = self.spectrum(-43)
        self.scene.set_frame(current, prepared=PreparedSpectrumFrame(current))
        self.drain()
        with patch.object(projection, "peak_preserving_envelope", wraps=projection.peak_preserving_envelope) as reduce:
            self.scene.set_trace(TraceKind.MAXIMUM, secondary)
            self.density()
            self.drain()
            self.assertEqual(reduce.call_count, 2)
        self.assertIs(self.scene.displayed_frame, current)

    def test_late_old_required_ack_does_not_mark_new_source_clean(self):
        self.spectrum(-60)
        self.density()
        self.worker.finish()  # old required/density result, GUI ack held
        fresh = self.spectrum(-30)
        self.scene.commit_projection()
        self.drain()
        self.assertIs(self.scene.displayed_frame, fresh)
        self.assertFalse(self.scene._required_projection_dirty)

    def test_show_after_hidden_density_projects_required_latest(self):
        self.scene.set_presentation_active(False)
        fresh = self.spectrum(-31)
        self.density()
        self.drain()
        with patch.object(projection, "peak_preserving_envelope", wraps=projection.peak_preserving_envelope) as reduce:
            self.scene.set_presentation_active(True)
            self.drain()
            self.assertGreater(reduce.call_count, 0)
        self.assertIs(self.scene.displayed_frame, fresh)


class DensityOnlyCompositionTests(unittest.TestCase):
    setUp = wiring.PersistenceCompositionTests.setUp

    def test_due_density_waits_for_coherent_live_preparation_not_old_executor_job(self):
        from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
        from tests.ui_v2.test_app05_prepared_live import measurement
        f = self.f
        scene, port = f.page.visualization.spectrum_scene, f.composition.spectrum_projector
        source = measurement(f)
        source = replace(source, persistence=synthetic_persistence(source.spectrum, 32, 1, 1))
        f.presenter._emit_snapshot(source)
        f.wait(lambda: scene.persistence_metrics.image_uploads == 1 and port._future is None)
        fresh = replace(source, spectrum=replace(source.spectrum, sequence=2, timestamp_ns=20))
        entered, release = threading.Event(), threading.Event()
        original = f.presenter._prepare

        def prepare(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("coherent Live preparation barrier")
            return original(*args)

        with patch.object(f.presenter, "_prepare", side_effect=prepare):
            try:
                f.presenter._offer_preparation(fresh, f.presenter._control_revision)
                f.wait(entered.is_set)
                scene.set_persistence_logarithmic(False)
                scene.commit_projection()
                self.assertIsNone(port._future, "density queued behind preparation uses an old scene")
                self.assertTrue(port.has_pending)
                release.set()
                f.wait(lambda: scene.displayed_frame is not None
                       and scene.displayed_frame.spectrum is fresh.spectrum and port._future is None)
                self.assertEqual(scene.persistence_metrics.image_uploads, 2)
            finally:
                release.set()

    def test_failed_preparation_releases_optional_gate_and_preserves_stopped_latest(self):
        from scripts.benchmark_app05_rtbw_observation import synthetic_persistence
        from tests.ui_v2.test_app05_prepared_live import measurement
        f = self.f
        scene, port = f.page.visualization.spectrum_scene, f.composition.spectrum_projector
        source = measurement(f)
        source = replace(source, persistence=synthetic_persistence(source.spectrum, 32, 1, 1))
        f.presenter._emit_snapshot(source)
        f.wait(lambda: scene.persistence_metrics.image_uploads == 1 and port._future is None)
        previous = scene.displayed_frame
        entered, release = threading.Event(), threading.Event()
        errors = []
        f.presenter.task_failed.connect(errors.append)

        def fail(*args):
            entered.set()
            if not release.wait(3):
                raise TimeoutError("failed preparation barrier")
            raise ValueError("expected preparation error")

        with patch.object(f.presenter, "_snapshot_preparer", fail):
            try:
                f.presenter._offer_preparation(source, f.presenter._control_revision)
                f.wait(entered.is_set)
                scene.set_persistence_logarithmic(False)
                scene.commit_projection()
                self.assertTrue(port._live_preparation_in_flight)
                self.assertIsNone(port._future)
                release.set()
                f.wait(lambda: not port._live_preparation_in_flight and port._future is None
                       and scene.persistence_metrics.image_uploads == 2)
            finally:
                release.set()
        self.assertIs(scene.displayed_frame, previous)
        self.assertTrue(any("expected preparation error" in value for value in errors))
