"""Actual product checks for one RF draft and an independent display span."""

from dataclasses import replace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
import tests.test_app02_analyzer_workspace_product as product_fixture


class EmptySourceFrequencyBarTests(unittest.TestCase):
    def test_both_apply_controls_wait_for_selected_source_without_implicit_commands(self):
        app = QApplication.instance() or QApplication([])
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = app
        fixture.setUp()
        try:
            bar, drawer = fixture.page.frequency_bar, fixture.page.drawer
            with patch.object(fixture.composition.view_model, "apply_configuration") as apply:
                self.assertFalse(bar.apply.isEnabled())
                self.assertFalse(drawer.can_apply)
                bar.gain.setValue(23)
                for locale in (UiLocale.EN, UiLocale.RU):
                    fixture.shell.select_appearance_locale(locale)
                    app.processEvents()
                    self.assertFalse(bar.apply.isEnabled())
                    self.assertFalse(drawer.can_apply)
                    self.assertEqual(drawer._status.text(), text("analyzer.settings.select_source"))
                    bar.apply.click()
                    drawer.apply_draft()
                apply.assert_not_called()
                self.assertEqual(fixture.events, [])
                # Discard only the local draft before an explicit fake discovery/selection.
                bar.cancel.click()
                fixture.page.discover.click()
                fixture.wait(lambda: fixture.page.source.count() == 2
                             and not fixture.composition.view_model.state.busy)
                fixture.page.source.setCurrentIndex(1)
                fixture.wait(lambda: fixture.composition.view_model.state.snapshot is not None
                             and fixture.composition.view_model.state.snapshot.device is not None
                             and not fixture.composition.view_model.state.busy)
                self.assertTrue(drawer.can_apply)
                self.assertTrue(bar.apply.isEnabled())
                self.assertFalse(fixture.page.primary.isEnabled())
                apply.assert_not_called()
                self.assertEqual(fixture.events, [])
        finally:
            try:
                fixture.tearDown()
            finally:
                fixture.doCleanups()


class FrequencyBarProductTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self.fixture.select_and_apply()

    def tearDown(self):
        try:
            self.fixture.tearDown()
        finally:
            self.fixture.doCleanups()

    def test_shared_editors_apply_once_then_cancel_without_configuration_call(self):
        f = self.fixture
        bar, drawer = f.page.frequency_bar, f.page.drawer
        self.assertIs(bar.center, drawer._center)
        self.assertIs(bar.fft, drawer._fft)
        self.assertIs(bar.gain, drawer._gain)
        initial = f.live.latest_snapshot().applied.applied
        with patch.object(f.composition.view_model, "apply_configuration",
                          wraps=f.composition.view_model.apply_configuration) as apply:
            bar.center.setValue(915)
            bar.fft.setValue(8192)
            bar.gain.setValue(21)
            self.assertEqual(f.live.latest_snapshot().applied.applied, initial)
            apply.assert_not_called()
            self.assertTrue(bar.apply.isEnabled())
            bar.apply.click()
            f.wait(lambda: not drawer.pending and not f.composition.view_model.state.busy)
            apply.assert_called_once()
            applied = f.live.latest_snapshot().applied.applied
            self.assertEqual((applied.center_hz, applied.fft_size, applied.gain_db),
                             (915e6, 8192, 21))
            self.assertFalse(drawer.dirty)
            self.assertFalse(bar.apply.isEnabled())
            bar.center.setValue(433)
            bar.fft.setValue(2048)
            bar.gain.setValue(10)
            bar.cancel.click()
            self.assertEqual((bar.center.value(), bar.fft.value(), bar.gain.value()),
                             (915, 8192, 21))
            self.assertFalse(drawer.dirty)
            apply.assert_called_once()
            self.assertFalse(bar.apply.isEnabled())
            self.assertFalse(bar.cancel.isEnabled())
        self.assertEqual(f.events, [])

    def test_empty_mode_switch_does_not_show_stale_sweep_span_as_rtbw_view(self):
        f = self.fixture
        bar = f.page.frequency_bar
        self.assertEqual(bar.span.text(), "—")
        self.assertFalse(bar.span.isEnabled())
        f.page.mode.setCurrentIndex(1)
        f.wait(lambda: f.page.model.state.mode is AnalyzerMode.SWEEP)
        bar.span.setValue(900)
        f.page.mode.setCurrentIndex(0)
        f.wait(lambda: f.page.model.state.mode is AnalyzerMode.RTBW)
        self.assertEqual(bar.span.text(), "—")
        self.assertFalse(bar.span.isEnabled())
        self.assertEqual(f.events, [])

    def test_first_frame_restores_view_even_if_x_range_does_not_change(self):
        f = self.fixture
        f.page.primary.click()
        f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)
        snapshot = f.live.latest_snapshot()
        config = snapshot.applied.applied
        frame = LiveSpectrumFrame(
            sequence=1, timestamp_ns=1, source_id="fake-pluto-usb",
            config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
            sample_rate_hz=config.sample_rate_hz, fft_size=config.fft_size,
            hop_size=config.fft_size,
            frequencies_hz=config.center_hz + (np.arange(config.fft_size) - config.fft_size / 2)
            * config.sample_rate_hz / config.fft_size,
            values=np.full(config.fft_size, -80, dtype=np.float32), unit="dBFS/bin",
        )
        view = f.page.visualization.spectrum_scene.view_box
        view.setXRange(float(frame.frequencies_hz[0]), float(frame.frequencies_hz[-1]), padding=0)
        bar = f.page.frequency_bar
        self.assertEqual(bar.span.text(), "—")
        delivered = replace(snapshot, spectrum=frame)
        f.live._snapshot = delivered
        f.presenter.offer_snapshot_for_render(delivered)
        f.wait(lambda: f.page._last_bundle is not None)
        self.assertTrue(bar.span.isEnabled())
        self.assertNotEqual(bar.span.text(), "—")
        lower, upper = view.viewRange()[0]
        self.assertAlmostEqual(bar.span.value(), (upper - lower) / 1e6, places=6)

    def test_one_hertz_live_view_is_numeric_not_empty_placeholder(self):
        f = self.fixture
        bar = f.page.frequency_bar
        bar.apply_view_state(f.page.model.state, has_frame=True)
        bar.set_viewport_span(1.0)
        self.assertEqual(bar.span.value(), 0.000001)
        self.assertNotEqual(bar.span.text(), "—")

    def test_live_span_and_pan_change_only_viewport_and_survive_locale(self):
        f = self.fixture
        f.page.primary.click()
        f.wait(lambda: f.live.is_running() and not f.composition.view_model.state.busy)
        snapshot = f.live.latest_snapshot()
        config = snapshot.applied.applied
        frame = LiveSpectrumFrame(
            sequence=1, timestamp_ns=1, source_id="fake-pluto-usb",
            config_generation=snapshot.generation, center_frequency_hz=config.center_hz,
            sample_rate_hz=config.sample_rate_hz, fft_size=config.fft_size,
            hop_size=config.fft_size,
            frequencies_hz=config.center_hz + (np.arange(config.fft_size) - config.fft_size / 2)
            * config.sample_rate_hz / config.fft_size,
            values=np.full(config.fft_size, -80, dtype=np.float32), unit="dBFS/bin",
        )
        delivered = replace(snapshot, spectrum=frame)
        f.live._snapshot = delivered
        f.presenter._emit_snapshot(delivered)
        f.wait(lambda: f.composition.view_model.state.snapshot is delivered)
        bar = f.page.frequency_bar
        view = f.page.visualization.spectrum_scene.view_box
        with patch.object(f.composition.view_model, "apply_configuration") as apply:
            self.assertTrue(bar.span.isEnabled())
            self.assertFalse(bar.center.isEnabled())
            bar.span.setValue(10)
            bounds = view.viewRange()[0]
            self.assertAlmostEqual(bounds[1] - bounds[0], 10e6, delta=1)
            view.setXRange(config.center_hz - 2e6, config.center_hz + 3e6, padding=0)
            self.assertAlmostEqual(bar.span.value(), 5)
            f.shell.select_appearance_locale(UiLocale.EN)
            f.app.processEvents()
            self.assertAlmostEqual(bar.span.value(), 5)
            self.assertFalse(f.page.drawer.dirty)
            self.assertEqual(f.live.latest_snapshot().generation, snapshot.generation)
            self.assertEqual(f.live.latest_snapshot().applied.applied, config)
            apply.assert_not_called()
        self.assertEqual(f.events, ["rtbw-start"])
