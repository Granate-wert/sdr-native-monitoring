"""A shared Analyzer publication never merges independent V2 pane histories."""

from dataclasses import replace
import gc
import unittest
import weakref

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.prepared_sweep import SweepSnapshotPreparer
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.waterfall import WaterfallPalette
from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests.ui_v2.test_app05_prepared_live import measurement


class AnalyzerPaneStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        fixture = product.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.addCleanup(fixture.tearDown)
        self.fixture = fixture
        self.prepare = SweepSnapshotPreparer(PresentationAllocationBudget())

    def _state(self, frame, *, line=None):
        snapshot = ContinuousSweepDisplaySnapshot(line, ContinuousSweepDisplayMetrics(), frame)
        prepared = self.prepare(snapshot, snapshot.analyzer_bundle)
        return replace(self.fixture.page.model.state, mode=AnalyzerMode.SWEEP, running=True,
                       bundle=prepared.analyzer_bundle, sweep_snapshot=snapshot, prepared_sweep=prepared)

    def test_same_capture_has_two_independent_history_and_measurement_owners(self) -> None:
        fixture = self.fixture
        primary = fixture.page.visualization
        secondary = AnalyzerPaneViewV2(
            AnalyzerMode.RTBW, settings=fixture._settings,
            settings_prefix="ui_v2/analyzer/pane2/v1",
        )
        self.addCleanup(secondary.deleteLater)
        self.addCleanup(secondary.close)
        secondary.resize(650, 460)
        secondary.show()
        self.app.processEvents()
        self.assertEqual(fixture.events, [])  # Another pane did not open/retune an SDR.

        first = self._state(progress(1))
        fixture.page._render(first)
        secondary.apply_analyzer_state(first)
        self.assertIs(primary.last_bundle, first.bundle)
        self.assertIs(secondary.last_bundle, first.bundle)
        self.assertEqual(primary.waterfall_pane.history_rows, 1)
        self.assertEqual(secondary.waterfall_pane.history_rows, 1)

        frequency = float(first.bundle.frequencies_hz[1])
        self.assertIsNotNone(secondary.spectrum_scene.place_marker("M1", frequency))
        self.assertEqual(primary.spectrum_scene.markers, ())
        secondary.spectrum_scene.view_box.setXRange(
            frequency, float(first.bundle.frequencies_hz[2]), padding=0,
        )
        self.assertNotEqual(primary.spectrum_scene.view_box.viewRange()[0],
                            secondary.spectrum_scene.view_box.viewRange()[0])

        second = self._state(progress(2), line=terminal(1))
        secondary.apply_analyzer_state(second)
        self.assertIs(primary.last_bundle, first.bundle)
        self.assertIs(secondary.last_bundle, second.bundle)
        self.assertEqual(primary.waterfall_pane.history_rows, 1)
        self.assertEqual(secondary.waterfall_pane.history_rows, 2)

        new_epoch = self._state(replace(progress(3), epoch=8))
        secondary.apply_analyzer_state(new_epoch)
        self.assertEqual(secondary.waterfall_pane.history_rows, 1)
        self.assertEqual(primary.waterfall_pane.history_rows, 1)
        self.assertIs(primary.last_bundle, first.bundle)
        fixture.page._render(new_epoch)
        self.assertIs(primary.last_bundle, new_epoch.bundle)
        self.assertEqual(primary.waterfall_pane.history_rows, 1)

        primary.waterfall_pane.flush_settings()
        primary_palette = fixture._settings.value("ui_v2/live/waterfall/v1/palette")
        secondary.waterfall_pane.set_palette(WaterfallPalette.GRAYSCALE)
        secondary.flush_settings()
        self.assertEqual(str(fixture._settings.value("ui_v2/analyzer/pane2/v1/version")), "1")
        self.assertEqual(fixture._settings.value("ui_v2/analyzer/pane2/v1/palette"),
                         WaterfallPalette.GRAYSCALE.value)
        self.assertEqual(fixture._settings.value("ui_v2/live/waterfall/v1/palette"), primary_palette)
        secondary.release_presentation_after_shutdown()
        self.assertIsNone(secondary.last_bundle)
        self.assertIs(primary.last_bundle, new_epoch.bundle)
        self.assertEqual(fixture.events, [])

    def test_product_shared_views_fan_out_one_live_capture_with_separate_projection(self) -> None:
        fixture = self.fixture
        fixture.select_and_apply()
        fixture.page.primary.click()
        fixture.wait(lambda: fixture.live.is_running() and not fixture.composition.view_model.state.busy)
        fixture.page.shared_views.setCurrentIndex(1)
        self.assertEqual(len(fixture.page._panes), 2)
        first, second = fixture.page._panes
        self.assertIsNot(first.spectrum_scene._projector, second.spectrum_scene._projector)
        self.assertIs(fixture.composition.spectrum_projector, first.spectrum_scene._projector)
        self.assertEqual(fixture.events, ["rtbw-start"])

        captured = measurement(fixture)
        fixture.live._snapshot = captured
        fixture.presenter.offer_snapshot_for_render(captured)
        fixture.wait(lambda: first.last_bundle is not None and second.last_bundle is first.last_bundle)
        fixture.wait(lambda: first.last_bundle is not None
                     and second.last_bundle is first.last_bundle
                     and first.spectrum_scene.displayed_frame is first.last_bundle
                     and second.spectrum_scene.displayed_frame is second.last_bundle)
        self.assertIs(first.last_bundle.spectrum, second.last_bundle.spectrum)
        frequency = float(second.last_bundle.frequencies_hz[10])
        self.assertIsNotNone(second.spectrum_scene.place_marker("M1", frequency))
        self.assertEqual(first.spectrum_scene.markers, ())
        fixture.page.selected_view.setCurrentIndex(1)
        first_range = tuple(first.spectrum_scene.view_box.viewRange()[0])
        fixture.page._change_viewport_span(1_000_000.0)
        second_range = second.spectrum_scene.view_box.viewRange()[0]
        self.assertAlmostEqual(second_range[1] - second_range[0], 1_000_000.0, delta=1)
        self.assertEqual(tuple(first.spectrum_scene.view_box.viewRange()[0]), first_range)
        fixture.page.display.click()
        overlay = fixture.page._display_overlays[1]
        grid = second.spectrum_scene.measurement_grid
        assert grid is not None
        overlay.range_start.setValue(float(grid[500]) / 1e6)
        overlay.range_stop.setValue(float(grid[1500]) / 1e6)
        overlay.apply_range.click()
        selected_range = second.spectrum_scene.view_box.viewRange()[0]
        self.assertAlmostEqual(selected_range[0], float(grid[500]), delta=1)
        self.assertAlmostEqual(selected_range[1], float(grid[1500]), delta=1)
        self.assertEqual(tuple(first.spectrum_scene.view_box.viewRange()[0]), first_range)
        self.assertFalse(overlay.range_error.isVisible())
        overlay.range_start.setValue(1.0)  # Beyond the current capture, not an RF command.
        overlay.apply_range.click()
        self.assertTrue(overlay.range_error.isVisible())
        self.assertEqual(tuple(second.spectrum_scene.view_box.viewRange()[0]), tuple(selected_range))
        fixture.page._hide_display()
        report = fixture.composition.memory_snapshot(fixture.page)
        owners = {owner.name: owner.bytes for owner in report.owners}
        self.assertGreater(owners["pane2.spectrum.sources"], 0)
        self.assertIn("viewport.pane2.requests", owners)
        self.assertGreater(report.shared_alias_bytes, 0)

        fixture.page.shared_views.setCurrentIndex(0)
        self.assertIsNone(second.last_bundle)
        self.assertIsNone(second.spectrum_scene.latest_frame)
        self.assertIsNone(second.waterfall_pane._renderer.buffer)
        self.assertIsNotNone(first.last_bundle)
        self.assertEqual(fixture.events, ["rtbw-start"])
        fixture.page.primary.click()
        fixture.wait(lambda: not fixture.live.is_running() and not fixture.composition.view_model.state.busy)
        self.assertEqual(fixture.events, ["rtbw-start", "rtbw-stop"])

    def test_product_shared_views_fan_out_progressive_sweep_without_second_owner(self) -> None:
        fixture = self.fixture
        fixture.page.shared_views.setCurrentIndex(1)
        primary, secondary = fixture.page._panes
        partial = self._state(progress(1))
        fixture.page._render(partial)
        self.assertIs(primary.last_bundle, partial.bundle)
        self.assertIs(secondary.last_bundle, partial.bundle)
        self.assertEqual(primary.waterfall_pane.history_rows, 1)
        self.assertEqual(secondary.waterfall_pane.history_rows, 1)
        completed = self._state(progress(2), line=terminal(1))
        fixture.page._render(completed)
        self.assertIs(primary.last_bundle, completed.bundle)
        self.assertIs(secondary.last_bundle, completed.bundle)
        self.assertEqual(primary.waterfall_pane.history_rows, 2)
        self.assertEqual(secondary.waterfall_pane.history_rows, 2)
        fixture.page.selected_view.setCurrentIndex(1)
        fixture.page.display.click()
        overlay = fixture.page._display_overlays[1]
        overlay.range_start.setValue(100.5)
        overlay.range_stop.setValue(102.5)
        overlay.apply_range.click()
        selected_range = secondary.spectrum_scene.view_box.viewRange()[0]
        self.assertAlmostEqual(selected_range[0], 100_500_000, delta=1)
        self.assertAlmostEqual(selected_range[1], 102_500_000, delta=1)
        self.assertNotEqual(primary.spectrum_scene.view_box.viewRange()[0], selected_range)
        self.assertFalse(overlay.range_error.isVisible())
        same_epoch = self._state(progress(2, revision=2), line=terminal(1))
        fixture.page._render(same_epoch)
        self.assertFalse(overlay.range_error.isVisible())
        self.assertAlmostEqual(secondary.spectrum_scene.view_box.viewRange()[0][0],
                               100_500_000, delta=1)
        self.assertAlmostEqual(secondary.spectrum_scene.view_box.viewRange()[0][1],
                               102_500_000, delta=1)
        new_epoch = self._state(replace(progress(3), epoch=8))
        fixture.page._render(new_epoch)
        self.assertTrue(overlay.range_error.isVisible())
        reset_range = tuple(secondary.spectrum_scene.view_box.viewRange()[0])
        overlay.apply_range.click()
        self.assertEqual(tuple(secondary.spectrum_scene.view_box.viewRange()[0]), reset_range)
        fixture.page._hide_display()
        self.assertEqual(fixture.events, [])

    def test_projection_backpressure_is_joint_and_shared_views_are_bounded(self) -> None:
        fixture = self.fixture
        fixture.page.shared_views.setCurrentIndex(3)
        self.assertEqual(len(fixture.page._panes), 4)
        self.assertEqual(len(fixture.composition._pane_projectors), 4)
        self.assertEqual(fixture.events, [])
        primary, secondary = fixture.composition._pane_projectors[:2]
        primary.work_active_changed.emit(True)
        secondary.work_active_changed.emit(True)
        self.assertTrue(fixture.presenter._projection_in_flight)
        primary.work_active_changed.emit(False)
        self.assertTrue(fixture.presenter._projection_in_flight)
        secondary.work_active_changed.emit(False)
        self.assertFalse(fixture.presenter._projection_in_flight)
        with self.assertRaisesRegex(RuntimeError, "at most four"):
            fixture.composition.create_shared_pane_projector()

    def test_selected_shared_view_routes_display_controls_without_touching_rx(self) -> None:
        fixture = self.fixture
        page = fixture.page
        fixture.shell.resize(1920, 1080)
        page.shared_views.setCurrentIndex(3)
        self.app.processEvents()
        self.assertEqual(len(page._panes), 4)
        for pane in page._panes:
            self.assertTrue(pane.isVisible())
            self.assertGreater(pane.spectrum_scene.view_box.sceneBoundingRect().width(), 100)
        page.selected_view.setCurrentIndex(2)
        self.assertIs(page._selected_pane(), page._panes[2])
        self.assertTrue(page._panes[2]._pane_badge.property("ui2Selected"))
        self.assertFalse(page._panes[0]._pane_badge.property("ui2Selected"))
        page.display.click()
        self.assertTrue(page._display_overlays[2].isVisible())
        self.assertFalse(page.display_controls.isVisible())
        page.selected_view.setCurrentIndex(1)
        self.assertFalse(any(overlay.isVisible() for overlay in page._display_overlays))
        page.display.click()
        self.assertTrue(page._display_overlays[1].isVisible())
        page._hide_display()
        page.shared_views.setCurrentIndex(0)
        self.assertEqual(page.selected_view.currentData(), 1)
        self.assertFalse(any(pane.isVisible() for pane in page._panes[1:]))
        self.assertEqual(fixture.events, [])


class AnalyzerSharedViewReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_four_populated_views_release_all_observed_arrays_after_terminal_close(self) -> None:
        def run_product():
            fixture = product.AnalyzerWorkspaceProductTests("runTest")
            fixture.app = self.app
            fixture.setUp()
            budget = fixture.composition.allocation_budget
            owner_ref = weakref.ref(fixture.composition)
            try:
                fixture.select_and_apply()
                fixture.page.primary.click()
                fixture.wait(lambda: fixture.live.is_running() and not fixture.composition.view_model.state.busy)
                fixture.page.shared_views.setCurrentIndex(3)
                captured = measurement(fixture)
                fixture.live._snapshot = captured
                fixture.presenter.offer_snapshot_for_render(captured)
                fixture.wait(lambda: all(pane.last_bundle is not None
                            and pane.spectrum_scene.displayed_frame is pane.last_bundle
                            for pane in fixture.page._panes))
                self.assertEqual(len(fixture.composition._pane_projectors), 4)
                self.assertGreater(budget.snapshot().observed_bytes, 0)
            finally:
                fixture.tearDown()
                fixture.doCleanups()
            return budget, owner_ref

        budget, owner_ref = run_product()
        gc.collect()
        self.assertEqual(budget.snapshot().reserved_bytes, 0)
        self.assertEqual(budget.snapshot().observed_bytes, 0)
        self.assertIsNone(owner_ref())


if __name__ == "__main__":
    unittest.main()
