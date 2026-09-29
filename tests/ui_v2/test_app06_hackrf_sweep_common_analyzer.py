"""Actual common V2 Analyzer with an inert HackRF Sweep port, no SDR effects."""

from __future__ import annotations

import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState, SweepQualitySchema
from sdr_monitor.domain.sweep_progress import SweepProgressFrame
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph


def _readonly(values, dtype):
    array = np.asarray(values, dtype=dtype)
    array.setflags(write=False)
    return array


class FakeHackrfSweep:
    def __init__(self) -> None:
        self.request = None
        self.selection = None
        self.events: list[str] = []
        self._publications: list[ContinuousSweepDisplaySnapshot] = []

    def start(self, request, selection) -> None:
        self.request, self.selection = request, selection
        self.events.append("start")
        first = request.start_hz + 20_000_000.0 / request.fft_size
        last = request.stop_hz - 20_000_000.0 / request.fft_size
        frequencies = _readonly(np.linspace(first, last, 4), np.float64)
        progress = SweepProgressFrame(
            source_id=request.source.device_id, sequence=1, epoch=request.epoch, revision=1,
            unit="dBFS/bin", frequencies_hz=frequencies,
            values_db=_readonly([-80, -75, np.nan, np.nan], np.float32),
            quality_flags=_readonly([0, 0, 4096, 4096], np.uint32),
            source_segment_indices=_readonly([0, 0, -1, -1], np.int32),
            acquired_segment_generations=((0, request.epoch),), pending_segment_indices=(1,),
        )
        line = SweepLineFrame(
            source_id=request.source.device_id, sequence=1, epoch=request.epoch,
            completed_at_ns=time.monotonic_ns(), state=SweepLineState.COMPLETE,
            frequencies_hz=frequencies, values_db=np.array([-80, -75, -70, -65], np.float32),
            quality_flags=np.zeros(4, np.uint16), source_segment_indices=np.array([0, 0, 1, 1], np.int32),
            missing_segment_indices=(), segment_config_generations=((0, request.epoch), (1, request.epoch)),
            gap_reasons=(), unit="dBFS/bin", quality_schema=SweepQualitySchema.NATIVE_V5,
            analysis_window_hz=5_000_000.0,
            analysis_bins_per_usable_window=request.fft_size // 4,
            physical_fft_bin_width_hz=20_000_000.0 / request.fft_size,
            physical_fft_size=request.fft_size,
        )
        metrics = ContinuousSweepDisplayMetrics()
        self._publications = [ContinuousSweepDisplaySnapshot(None, metrics, progress),
                              ContinuousSweepDisplaySnapshot(line, metrics)]

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        self.events.append("poll")
        return (self._publications.pop(0) if self._publications
                else ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics()))

    def stop(self) -> None:
        self.events.append("stop")


class HackrfSweepCommonAnalyzerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate) -> None:
        deadline = time.monotonic() + 5
        while not predicate():
            self.app.processEvents()
            if time.monotonic() >= deadline:
                self.fail("bounded common HackRF Sweep UI timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_explicit_sweep_start_progress_line_same_canvas_stop_and_locale(self):
        g = graph()
        fake = FakeHackrfSweep()
        compositions = []

        def capture(*args, **kwargs):
            composition = compose_v2_live_product(*args, **kwargs)
            compositions.append(composition)
            return composition

        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog,
            analyzer_hackrf=g.hackrf, analyzer_hackrf_sweep=fake,
            sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        previous_locale = current_locale()
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        composition = compositions[0]
        page = shell._workspace_pages["analyzer"]
        accepted = []
        unsubscribe = composition.analyzer_view_model.subscribe(
            lambda state: accepted.append(state.bundle.publication_kind.value)
            if state.bundle is not None else None)
        try:
            page.discover.click()
            self.wait(lambda: page.source.findData("source-hackrf") >= 0 and not composition.view_model.state.busy)
            page.source.setCurrentIndex(page.source.findData("source-hackrf"))
            self.wait(lambda: composition.analyzer_view_model.state.hackrf_controls_available
                      and not composition.view_model.state.busy)
            page.mode.setCurrentIndex(page.mode.findData("sweep"))
            self.assertEqual(composition.analyzer_view_model.state.mode.value, "sweep")
            # A benign Live state notification for the same selected device
            # must not silently switch the user's Sweep intent back to RTBW.
            composition.analyzer_view_model._on_live(composition.view_model.state)
            self.assertEqual(composition.analyzer_view_model.state.mode.value, "sweep")
            self.assertFalse(page.hackrf_sweep_bar.isHidden())
            self.assertTrue(page.hackrf_bar.isHidden())
            self.assertTrue(page.frequency_bar.isHidden())
            for locale in (UiLocale.EN, UiLocale.RU):
                set_active_locale(locale)
                page.set_locale()
                self.assertEqual(page.hackrf_sweep_bar.start_mhz.value(), 100)
                self.assertEqual(page.hackrf_sweep_bar.stop_mhz.value(), 220)
            page.hackrf_sweep_bar.stop_mhz.setValue(221)
            self.assertFalse(page.primary.isEnabled())
            self.assertIn(text("hackrf.sweep.invalid"), page.primary.toolTip())
            page.hackrf_sweep_bar.stop_mhz.setValue(220)
            self.assertTrue(page.primary.isEnabled())
            self.assertEqual(fake.events, [])
            page.primary.click()
            self.wait(lambda: "sweep_progress" in accepted and "sweep_complete" in accepted)
            self.assertEqual(fake.events[0], "start")
            self.assertEqual(fake.request.epoch, 1)
            self.assertIs(fake.request.source, fake.selection.selected)
            self.assertEqual(composition.analyzer_view_model.state.bundle.unit, "dBFS/bin")
            self.assertIsNotNone(page.visualization.spectrum_scene.latest_frame)
            self.assertFalse(page._sweep_waterfall_error)
            page.primary.click()
            self.wait(lambda: not composition.analyzer_view_model.state.running and not composition.view_model.state.busy)
            self.assertIn("stop", fake.events)
            self.assertLess(fake.events.index("start"), fake.events.index("stop"))
        finally:
            unsubscribe()
            set_active_locale(previous_locale)
            shell.close()
            self.wait(lambda: shell._is_closed)
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
