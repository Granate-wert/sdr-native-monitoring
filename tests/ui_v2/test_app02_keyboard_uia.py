"""Bounded APP-02 product evidence for retained state and keyboard/UIA semantics."""

from dataclasses import replace
import os
import time
import unittest

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.domain.live import LiveSessionState, LiveSpectrumFrame
from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale, text
import tests.test_app02_analyzer_workspace_product as product_fixture


class App02KeyboardUiaTests(unittest.TestCase):
    """Exercise the actual APP-02 composition without touching a receiver."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self._clock_ns = time.time_ns()
        self.fixture.composition.view_model._now_ns = lambda: self._clock_ns

    def tearDown(self) -> None:
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def _publish(self, sequence: int, *, state: LiveSessionState | None = None) -> LiveSpectrumFrame:
        snapshot = self.fixture.live.latest_snapshot()
        configuration = snapshot.applied.applied
        width = 4096
        frequencies = configuration.center_hz + (
            np.arange(width) - width // 2
        ) * (configuration.sample_rate_hz / width)
        values = np.full(width, -78.0, dtype=np.float32)
        values[width // 2 + sequence % 31] = -28.0
        frame = LiveSpectrumFrame(
            sequence=sequence,
            timestamp_ns=self._clock_ns - 10_000_000,
            timestamp_quality=TimestampQuality.ESTIMATED,
            clock_domain="unix_ns",
            source_id="fake-pluto-usb",
            config_generation=snapshot.generation,
            acquisition_epoch=7,
            receiver_id="RX1",
            center_frequency_hz=configuration.center_hz,
            sample_rate_hz=configuration.sample_rate_hz,
            fft_size=configuration.fft_size,
            hop_size=configuration.fft_size,
            frequencies_hz=frequencies,
            values=values,
            unit="dBFS/bin",
        )
        delivered = replace(
            snapshot,
            sequence=sequence,
            state=snapshot.state if state is None else state,
            spectrum=frame,
            active_source_id=frame.source_id,
            receiver_id=frame.receiver_id,
            acquisition_epoch=frame.acquisition_epoch,
            active_config_generation=int(frame.config_generation),
        )
        self.fixture.live._snapshot = delivered
        self.fixture.presenter._emit_snapshot(delivered)
        self.fixture.wait(lambda: self.fixture.composition.view_model.state.spectrum is frame)
        return frame

    def test_100_actual_publications_retain_canvas_viewport_and_markers(self) -> None:
        self.fixture.select_and_apply()
        self.fixture.page.primary.click()
        self.fixture.wait(lambda: self.fixture.live.is_running())
        page = self.fixture.page
        scene = page.visualization.spectrum_scene
        canvas = page.visualization
        self._publish(1)
        scene.view_box.setXRange(2.395e9, 2.405e9, padding=0)
        scene.place_marker("M1", 2.399e9)
        scene.place_marker("M2", 2.401e9)
        # Select the first marker: the original defect silently selected the
        # last (M2) on each refresh, so leaving M2 active would miss it.
        scene.place_marker("M1", 2.399e9)
        expected_view = tuple(scene.view_box.viewRange()[0])
        expected_markers = scene.markers

        for sequence in range(2, 101):
            self._publish(sequence)

        self.assertIs(page.visualization, canvas)
        self.assertEqual(int(scene.latest_frame.spectrum.sequence), 100)
        self.assertEqual(tuple(scene.view_box.viewRange()[0]), expected_view)
        self.assertEqual(tuple(marker.marker_id for marker in scene.markers), ("M1", "M2"))
        self.assertEqual(tuple(marker.frequency_hz for marker in scene.markers),
                         tuple(marker.frequency_hz for marker in expected_markers))
        before_peak = {marker.marker_id: marker for marker in scene.markers}
        moved = scene.move_selected_marker_to_peak()
        after_peak = {marker.marker_id: marker for marker in scene.markers}
        self.assertIsNotNone(moved)
        self.assertEqual(moved.marker_id, "M1")
        self.assertEqual(after_peak["M2"], before_peak["M2"])
        self.assertNotEqual(after_peak["M1"].frequency_hz, before_peak["M1"].frequency_hz)
        self.assertGreater(page.visualization.waterfall_pane.history_rows, 0)

    def test_locale_and_theme_retain_measurement_history_view_and_markers(self) -> None:
        self.fixture.select_and_apply()
        self.fixture.page.primary.click()
        self.fixture.wait(lambda: self.fixture.live.is_running())
        page = self.fixture.page
        scene = page.visualization.spectrum_scene
        frame = self._publish(12)
        scene.view_box.setXRange(2.397e9, 2.403e9, padding=0)
        scene.place_marker("M1", 2.399e9)
        scene.place_marker("M2", 2.401e9)
        canvas = page.visualization
        view = tuple(scene.view_box.viewRange()[0])
        markers = scene.markers
        rows = page.visualization.waterfall_pane.history_rows
        events = tuple(self.fixture.events)

        self.fixture.shell.select_appearance_locale(UiLocale.EN)
        self.fixture.shell.select_appearance_theme(ThemeId.HIGH_CONTRAST)
        self.app.processEvents()

        self.assertIs(self.fixture.shell._workspace_pages["analyzer"], page)
        self.assertIs(page.visualization, canvas)
        self.assertIs(scene.latest_frame.spectrum, frame)
        self.assertEqual(tuple(scene.view_box.viewRange()[0]), view)
        self.assertEqual(scene.markers, markers)
        self.assertEqual(page.visualization.waterfall_pane.history_rows, rows)
        self.assertEqual(tuple(self.fixture.events), events)

    def test_disconnected_snapshot_retains_last_frame_with_stopped_age_status(self) -> None:
        self.fixture.select_and_apply()
        frame = self._publish(21)
        page = self.fixture.page
        scene = page.visualization.spectrum_scene

        self._publish(22, state=LiveSessionState.DISCONNECTED)

        self.assertEqual(int(scene.latest_frame.spectrum.sequence), 22)
        self.assertIs(page._last_bundle, scene.latest_frame)
        self.assertIn(text("analyzer.stopped_last"), page.status.text())
        age_label = self.fixture.composition.analyzer_view_model.state.live.data_age_label
        self.assertTrue(age_label)
        self.assertIn(age_label, page.status.text())
        self.assertNotIn(text("analyzer.unavailable"), page.status.text())
        self.assertIsNot(scene.latest_frame.spectrum, frame)

    def test_ru_en_uia_names_and_tab_reach_main_actions_but_not_disabled_rx(self) -> None:
        self.assertFalse(self.fixture.page.primary.isEnabled())
        self.fixture.select_and_apply()
        for locale in (UiLocale.RU, UiLocale.EN):
            self.fixture.shell.select_appearance_locale(locale)
            self.app.processEvents()
            page = self.fixture.shell._workspace_pages["analyzer"]
            main = (page.source, page.discover, page.mode, page.settings, page.display, page.primary)
            for widget in (*main, page.rx):
                self.assertTrue(widget.accessibleName(), type(widget).__name__)
            self.assertFalse(page.rx.isEnabled())
            self.assertTrue(page.rx.toolTip())

            page.source.setFocus(Qt.FocusReason.TabFocusReason)
            self.app.processEvents()
            visited: set[QWidget] = set()
            for _ in range(40):
                focused = self.app.focusWidget()
                if isinstance(focused, QWidget):
                    visited.add(focused)
                QTest.keyClick(focused or page, Qt.Key.Key_Tab)
                self.app.processEvents()
                if set(main).issubset(visited):
                    break
            self.assertTrue(set(main).issubset(visited),
                            f"missing main controls: {set(main) - visited}")
            self.assertNotIn(page.rx, visited)
            self.assertEqual(self.fixture.events, [])


if __name__ == "__main__":
    unittest.main()
