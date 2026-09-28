"""A shared Analyzer publication never merges independent V2 pane histories."""

from dataclasses import replace
import unittest

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.prepared_sweep import SweepSnapshotPreparer
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.waterfall import WaterfallPalette
from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


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


if __name__ == "__main__":
    unittest.main()
