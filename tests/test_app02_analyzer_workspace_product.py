"""Actual composition + real Qt controls over deterministic in-memory SDRs."""

from dataclasses import replace
import os
import time
import traceback
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain import BackendKind
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.services.live_session import fake_pluto_device
from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app01_product_analyzer import _AtomicFakeLive, _FakeAnalyzerDisplay


class AnalyzerWorkspaceProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._qt_errors = []
        exception_hook = patch("sys.excepthook", side_effect=lambda *args: self._qt_errors.append(
            "".join(traceback.format_exception(*args))))
        exception_hook.start()
        self.addCleanup(exception_hook.stop)
        self._temporary_settings = TemporaryDirectory(prefix="app02-qt-")
        self.addCleanup(self._temporary_settings.cleanup)
        self._settings = QSettings(str(Path(self._temporary_settings.name) / "ui.ini"), QSettings.Format.IniFormat)
        self.events = []
        self.live = _AtomicFakeLive(self.events)
        device = fake_pluto_device()
        self.live._devices = (replace(device, capabilities=replace(device.capabilities,
                                                                  sample_rates_hz=(61.44e6,))),)
        display = _FakeAnalyzerDisplay(self.events)
        services = SimpleNamespace(live_sdr=self.live, analyzer_display=display, sweep=Mock(),
                                   calibration=Mock(), diagnostics=Mock(), replay=Mock())
        captured = []

        def compose(*args, **kwargs):
            result = compose_v2_live_product(*args, **kwargs)
            captured.append((result, args[0]))
            return result

        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=compose), \
             patch("sdr_monitor.ui.v2.shell.app_shell.QSettings", return_value=self._settings), \
             patch("sdr_monitor.ui.v2.waterfall.spectrum_view.QSettings", return_value=self._settings):
            self.shell = build_v2_shell(services)
        self.composition, self.presenter = captured[0]
        self.page = self.shell._workspace_pages["analyzer"]
        self.shell.resize(1366, 768)
        self.shell.show()
        self.app.processEvents()

    def tearDown(self):
        sweep = self.composition.analyzer_presenter
        self.wait(lambda: not sweep.is_starting)
        if not sweep.can_close():
            sweep.stop()
            self.wait(lambda: not sweep.is_stopping)
        if self.live.is_running():
            self.presenter.stop()
            self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        self.shell.close()
        self.composition.shutdown()
        self.shell.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertEqual(self._qt_errors, [], "Qt callback exceptions are test failures")

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Qt/application completion timeout")
            time.sleep(0.001)
        self.app.processEvents()

    def select_and_apply(self):
        self.page.discover.click()
        self.wait(lambda: self.page.source.count() == 2 and not self.composition.view_model.state.busy)
        self.page.source.setCurrentIndex(1)
        self.wait(lambda: self.composition.view_model.state.snapshot is not None
                  and self.composition.view_model.state.snapshot.device is not None
                  and not self.composition.view_model.state.busy)
        self.page.settings.click()
        drawer = self.page.drawer
        drawer._sample_rate.setValue(61.44)
        drawer._backend.setCurrentIndex(drawer._backend.findData(BackendKind.CPU.value))
        drawer._apply.click()
        self.wait(lambda: self.composition.view_model.state.has_applied_configuration
                  and not self.composition.view_model.state.busy and not drawer.pending)
        self.page._hide_settings()

    def test_same_canvas_rtbw_then_partial_sweep_from_explicit_buttons(self):
        self.assertEqual(self.events, [])
        self.select_and_apply()
        canvas = self.page.visualization
        self.page.primary.click()
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        configuration = self.live.latest_snapshot().applied.applied
        frame = LiveSpectrumFrame(
            sequence=1, timestamp_ns=12, source_id="fake-pluto-usb",
            config_generation=self.live.latest_snapshot().generation,
            center_frequency_hz=configuration.center_hz, sample_rate_hz=61.44e6,
            fft_size=configuration.fft_size, hop_size=configuration.fft_size,
            frequencies_hz=configuration.center_hz + (np.arange(4096) - 2048) * (61.44e6 / 4096),
            values=np.full(4096, -70.0, dtype=np.float32), unit="dBFS/bin",
        )
        delivered = replace(self.live.latest_snapshot(), spectrum=frame)
        self.live._snapshot = delivered
        self.presenter.offer_snapshot_for_render(delivered)
        self.wait(lambda: self.page._last_bundle is not None)
        self.assertEqual(self.page._last_bundle.mode, "rtbw")
        retained = self.page.visualization.spectrum_scene.latest_frame
        scene = self.page.visualization.spectrum_scene
        scene.place_marker("M1", configuration.center_hz)
        scene.place_marker("M2", configuration.center_hz + 1e6)
        self.page.primary.click()
        self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        from sdr_monitor.ui.v2.i18n import text
        self.assertIs(self.page.visualization.spectrum_scene.latest_frame.spectrum, retained.spectrum)
        self.assertIn(text("analyzer.stopped_last"), self.page.status.text())
        self.assertIn(text("analyzer.quality_unknown"), self.page.status.text())
        self.page.mode.setCurrentIndex(self.page.mode.findData(AnalyzerMode.SWEEP))
        self.assertIsNone(scene.latest_frame)
        self.assertEqual(scene.markers, ())
        self.assertTrue(scene._empty_overlay.isVisible())
        self.assertTrue(all(not item.isVisible() for item in scene._marker_lines.values()))
        self.assertTrue(all(not item.isVisible() for item in scene._marker_labels.values()))
        self.assertIsNone(scene.place_marker("M1", configuration.center_hz))
        self.assertIsNone(scene.move_selected_marker_to_peak())
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])
        self.page.primary.click()
        self.wait(lambda: self.page._last_bundle is not None and self.page._last_bundle.mode == "sweep")
        self.assertIs(self.page.visualization, canvas)
        self.assertEqual(self.page._last_bundle.publication_kind.value, "sweep_progress")
        self.assertTrue(np.isnan(self.page._last_bundle.values[-1]))
        self.assertFalse(self.page.mode.isEnabled())
        self.assertFalse(self.page.source.isEnabled())
        self.assertIn("1/2", self.page.status.text())
        self.page.primary.click()
        self.wait(lambda: self.composition.analyzer_presenter.can_close())
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop", "sweep-start", "sweep-stop"])

    def test_locale_retains_canvas_draft_and_drawer_escape_returns_focus(self):
        self.select_and_apply()
        self.page.settings.click()
        self.page.drawer._center.setValue(915)
        canvas = self.page.visualization
        self.shell.select_appearance_locale(UiLocale.EN)
        self.app.processEvents()  # Reparented retained page must be visible before user input.
        self.assertIs(self.shell._workspace_pages["analyzer"], self.page)
        self.assertIs(self.page.visualization, canvas)
        self.assertEqual(self.page.drawer._center.value(), 915)
        self.assertTrue(self.page.drawer.dirty)
        QTest.keyClick(self.page.drawer, Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertFalse(self.page.drawer.isVisible())
        self.assertTrue(self.page.settings.hasFocus())
        self.assertEqual(self.events, [])

    def test_display_controls_do_not_reserve_plot_area_and_keep_stop_exposed(self):
        self.select_and_apply()
        self.shell.resize(1366, 768)
        self.app.processEvents()
        canvas = self.page.visualization
        rects = (canvas.spectrum_scene.view_box.sceneBoundingRect(), canvas.waterfall_pane.view_box.sceneBoundingRect())
        fraction = sum(rect.width() * rect.height() for rect in rects) / (self.shell.width() * self.shell.height())
        self.assertGreaterEqual(fraction, 0.65)
        self.page.display.click()
        self.app.processEvents()
        self.assertTrue(self.page.display_controls.isVisible())
        self.assertLess(self.page.primary.geometry().bottom(), self.page.display_controls.geometry().top())
        QTest.keyClick(self.page.display_controls, Qt.Key.Key_Escape)
        self.assertFalse(self.page.display_controls.isVisible())
        self.assertTrue(self.page.display.hasFocus())
        self.assertEqual(self.events, [])

    def test_analyzer_inspector_is_explicit_overlay_at_every_width_and_preserves_canvas(self):
        self.select_and_apply()
        canvas = self.page.visualization
        for width, height in ((960, 540), (1280, 720), (1920, 1080), (2560, 1440)):
            self.shell.resize(width, height)
            self.app.processEvents()
            self.assertFalse(self.shell._inspector.isVisible())
            geometry = canvas.geometry()
            self.shell._inspector_toggle.click()
            self.app.processEvents()
            overlay = self.shell._narrow_inspector_drawer
            self.assertTrue(overlay.isVisible())
            self.assertEqual(canvas.geometry(), geometry)
            stop_bottom = self.page.primary.mapTo(self.shell._root, self.page.primary.rect().bottomRight()).y()
            self.assertGreater(overlay.y(), stop_bottom)
            self.assertTrue(self.shell._root.rect().contains(overlay.geometry()))
            QTest.keyClick(overlay, Qt.Key.Key_Escape)
            self.app.processEvents()
            self.assertFalse(overlay.isVisible())
            self.assertTrue(self.shell._inspector_toggle.hasFocus())
        self.assertIs(self.page.visualization, canvas)
        self.assertEqual(self.events, [])

    def test_pending_apply_has_disabled_explicit_primary_until_confirmation(self):
        from sdr_monitor.ui.v2.i18n import text

        self.select_and_apply()
        self.page.settings.click()
        self.page.drawer._gain.setValue(20)
        with patch.object(self.composition.view_model, "apply_configuration", return_value=True):
            self.page.drawer._apply.click()
        self.assertTrue(self.composition.analyzer_view_model.state.configuration_pending)
        self.assertEqual(self.page.primary.text(), text("analyzer.applying"))
        self.assertFalse(self.page.primary.isEnabled())
        self.assertFalse(self.page.mode.isEnabled())
        self.assertFalse(self.page.source.isEnabled())
        snapshot = self.live.latest_snapshot()
        applied = replace(snapshot.applied, applied=replace(snapshot.applied.applied, gain_db=20))
        delivered = replace(snapshot, generation=snapshot.generation + 1, applied=applied)
        self.presenter._emit_snapshot(delivered)
        self.app.processEvents()
        self.assertFalse(self.composition.analyzer_view_model.state.configuration_pending)
        self.assertEqual(self.page.primary.text(), text("analyzer.start"))
        self.assertTrue(self.page.primary.isEnabled())
        self.assertEqual(self.events, [])

    def test_failed_select_keeps_published_source_in_combo(self):
        self.select_and_apply()
        applied_device = self.live.latest_snapshot().device
        other = replace(applied_device, device_id="fake-other", label="Other SDR")
        self.page._devices((applied_device, other))
        with patch.object(self.live, "select_device", side_effect=RuntimeError("selection rejected")):
            self.page.source.setCurrentIndex(self.page.source.findData("fake-other"))
            self.wait(lambda: not self.composition.view_model.state.busy)
        self.assertEqual(self.page.source.currentData(), applied_device.device_id)
        self.assertIn("selection rejected", self.page.error.text())
        self.assertEqual(self.events, [])

    def test_apply_exception_releases_pending_without_losing_draft_or_snapshot(self):
        self.select_and_apply()
        before = self.composition.view_model.state.snapshot
        self.page.settings.click()
        self.page.drawer._gain.setValue(20)
        with patch.object(self.live, "apply_configuration", side_effect=RuntimeError("apply rejected")):
            self.page.drawer._apply.click()
            self.wait(lambda: not self.composition.view_model.state.busy)
        self.assertIs(self.composition.view_model.state.snapshot, before)
        self.assertFalse(self.page.drawer.pending)
        self.assertFalse(self.composition.analyzer_view_model.state.configuration_pending)
        self.assertTrue(self.page.drawer.dirty)
        self.assertEqual(self.page.drawer._gain.value(), 20)
        self.assertIn("apply rejected", self.page.error.text())
        self.assertEqual(self.events, [])

    def test_graph_space_is_explicit_start_stop_not_discover_or_field_edit(self):
        scene = self.page.visualization.spectrum_scene
        scene.setFocus()
        self.app.processEvents()
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.assertEqual(self.events, [])
        self.assertEqual(self.page.source.count(), 1)
        self.select_and_apply()
        self.page.settings.click()
        self.page.drawer._uri.setFocus()
        QTest.keyClick(self.page.drawer._uri, Qt.Key.Key_Space)
        self.assertEqual(self.events, [])
        QTest.keyClick(self.page.drawer, Qt.Key.Key_Escape)
        scene.setFocus()
        self.app.processEvents()
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.wait(lambda: self.live.is_running() and not self.composition.view_model.state.busy)
        QTest.keyClick(scene, Qt.Key.Key_Space)
        self.wait(lambda: not self.live.is_running() and not self.composition.view_model.state.busy)
        self.assertEqual(self.events, ["rtbw-start", "rtbw-stop"])


if __name__ == "__main__":
    unittest.main()
