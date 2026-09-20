"""Actual-composition retained ownership inventory; no RSS/RF assertion."""
import dataclasses
import threading
import time
import unittest
from unittest.mock import patch

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app05_prepared_live import measurement
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode


class PresentationInventoryTests(unittest.TestCase):
    setUpClass = classmethod(product.AnalyzerWorkspaceProductTests.setUpClass.__func__)
    setUp = product.AnalyzerWorkspaceProductTests.setUp
    tearDown = product.AnalyzerWorkspaceProductTests.tearDown
    wait = product.AnalyzerWorkspaceProductTests.wait
    select_and_apply = product.AnalyzerWorkspaceProductTests.select_and_apply

    def snapshot(self):
        return self.composition.memory_snapshot(self.page)

    def test_idle_inventory_is_scalar_and_does_not_create_deferred_owners(self):
        before = list(self.events)
        report = self.snapshot()
        self.assertEqual(self.events, before)
        self.assertIsNone(self.composition.diagnostics_view_model._presenter)
        self.assertIsNone(self.composition.replay_view_model._presenter)
        self.assertIn("Qt raster/cache", report.missing)
        self.assertIn("workspace not supplied", self.composition.memory_snapshot().missing)
        self.assertGreaterEqual(report.unique_array_bytes, 0)
        # Serialization is scalar metadata, never array payloads or source refs.
        import json
        self.assertLess(len(json.dumps(dataclasses.asdict(report))), 4000)

    def test_actual_rtbw_shared_sources_deduplicate_across_presenter_model_scene(self):
        self.select_and_apply()
        snap = measurement(self)
        self.presenter._emit_snapshot(snap)
        self.wait(lambda: self.page._last_bundle is not None)
        self.wait(lambda: self.page.visualization.spectrum_scene.displayed_frame is self.page._last_bundle)
        report = self.snapshot()
        owners = {owner.name: owner.bytes for owner in report.owners}
        source_bytes = snap.spectrum.frequencies_hz.nbytes + snap.spectrum.values.nbytes
        self.assertGreaterEqual(owners["live.view-model"], source_bytes)
        self.assertGreaterEqual(owners["analyzer.view-model"], source_bytes)
        self.assertGreaterEqual(owners["spectrum.sources"], source_bytes)
        self.assertGreaterEqual(owners["waterfall"], self.page.visualization.waterfall_pane._renderer.buffer._data.nbytes)
        self.assertGreater(report.shared_alias_bytes, 2 * source_bytes)
        self.assertEqual(sum(owners.values()) - report.shared_alias_bytes, report.unique_array_bytes)
        self.assertEqual(report.in_flight, ())

    def test_active_and_done_before_gui_ack_are_visible_without_waiting(self):
        self.select_and_apply()
        entered, release = threading.Event(), threading.Event()
        original = self.presenter._snapshot_preparer
        snap = measurement(self)

        def blocked(value):
            entered.set()
            if not release.wait(3):
                raise RuntimeError("test barrier expired")
            return original(value)

        try:
            with patch.object(self.presenter, "_snapshot_preparer", side_effect=blocked):
                self.presenter._emit_snapshot(snap)
                self.wait(entered.is_set)
                start = time.monotonic()
                active = self.snapshot()
                self.assertLess(time.monotonic() - start, .5)
                self.assertIn("live.prepare", active.in_flight)
                self.assertIs(self.presenter._active_preparation_snapshot, snap)
                active_size = next(x.bytes for x in active.owners if x.name == "live.presenter")
                self.assertGreaterEqual(active_size, snap.spectrum.frequencies_hz.nbytes + snap.spectrum.values.nbytes)
                release.set()
                self.presenter._preparation_future.result(timeout=3)  # barrier, no event delivery
                done = self.snapshot()
                self.assertNotIn("live.prepare", done.in_flight)
                self.assertGreaterEqual(next(x.bytes for x in done.owners if x.name == "live.presenter"), active_size)
                self.wait(lambda: self.presenter._preparation_future is None)
                self.assertIsNone(self.presenter._active_preparation_snapshot)
        finally:
            release.set()

    def test_hidden_and_clear_report_retained_history_instead_of_claiming_zero(self):
        self.select_and_apply()
        self.presenter._emit_snapshot(measurement(self))
        self.wait(lambda: self.page._last_bundle is not None)
        pane = self.page.visualization.waterfall_pane
        self.wait(lambda: self.composition.spectrum_projector._future is None)
        before = self.snapshot()
        self.shell.select_workspace("calibration")
        hidden = self.snapshot()
        self.assertGreater(hidden.unique_array_bytes, 0)
        self.assertEqual(dict((x.name, x.bytes) for x in before.owners)["waterfall"],
                         dict((x.name, x.bytes) for x in hidden.owners)["waterfall"])
        pane.clear_history()
        after = self.snapshot()
        # Clear history empties content but deliberately retains the reusable ring.
        self.assertEqual(pane.history_rows, 0)
        self.assertGreater(next(x.bytes for x in after.owners if x.name == "waterfall"), 0)
        self.composition.analyzer_view_model.select_mode(AnalyzerMode.SWEEP)
        self.app.processEvents()
        changed = self.snapshot()
        self.assertEqual(next(x.bytes for x in changed.owners if x.name == "spectrum.sources"), 0)

    def test_inventory_refuses_non_gui_thread(self):
        errors = []
        def read():
            try:
                self.snapshot()
            except RuntimeError as error:
                errors.append(str(error))
        thread = threading.Thread(target=read)
        thread.start()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)

    def test_hide_releases_painted_source_but_marker_intent_returns_with_latest(self):
        self.select_and_apply()
        self.presenter._emit_snapshot(measurement(self))
        scene = self.page.visualization.spectrum_scene
        self.wait(lambda: scene.displayed_frame is not None)
        marker = scene.place_marker("M1", self.page._last_bundle.frequencies_hz[10])
        self.assertIsNotNone(marker)
        self.shell.select_workspace("calibration")
        self.assertIsNone(scene._displayed_view)
        self.assertEqual(scene._envelopes, {})
        self.assertFalse(scene._marker_labels["M1"].isVisible())
        self.assertIsNone(scene._persistence.image_item.image)
        latest = measurement(self, sequence=2)
        self.presenter._emit_snapshot(latest)
        self.wait(lambda: self.composition.view_model.state.spectrum is latest.spectrum)
        self.shell.select_workspace("analyzer")
        self.wait(lambda: scene.displayed_frame is self.page._last_bundle)
        self.assertEqual(scene.markers[0].frequency_hz, marker.frequency_hz)
        self.assertEqual(scene.markers[0].value, -68)


if __name__ == "__main__":
    unittest.main()
