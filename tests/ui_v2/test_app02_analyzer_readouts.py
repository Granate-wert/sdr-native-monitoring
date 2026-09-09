"""Explicit lifecycle, loss provenance and progressive/terminal UI labels."""

from dataclasses import replace
import unittest

import numpy as np

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.live import LiveSnapshot, LiveSpectrumFrame, LiveSessionState
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepLineState
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.state.analyzer_readouts import analyzer_status
from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode, AnalyzerViewState
from tests.test_app01_product_analyzer import _progress


class AnalyzerReadoutTests(unittest.TestCase):
    def setUp(self):
        locale = current_locale()
        self.addCleanup(set_active_locale, locale)

    def state(self):
        frame = LiveSpectrumFrame(1, 12, 101., 256., 256, 128,
                                  np.array([100., 101., 102.]), np.array([-80., -40., -70.]))
        live = build_live_view_state(LiveSnapshot(generation=1, sequence=1,
                                                  state=LiveSessionState.RUNNING, spectrum=frame))
        return AnalyzerViewState(AnalyzerMode.RTBW, live, live.analyzer_bundle,
                                 False, False, True, False, None)

    def test_unknown_quality_never_becomes_clean_and_known_loss_is_named(self):
        state = self.state()
        for locale in (UiLocale.RU, UiLocale.EN):
            set_active_locale(locale)
            self.assertIn(text("analyzer.quality_unknown"), analyzer_status(state))
            frame = replace(state.bundle.spectrum, native_quality_flags=4,
                            dropped_samples_before=12, dropped_iq_blocks_before=2,
                            dropped_fft_frames_before=3)
            known = replace(state, bundle=replace(state.bundle, spectrum=frame))
            self.assertIn("0x00000004", analyzer_status(known))
            self.assertIn(text("analyzer.frame_loss", samples=12, blocks=2, fft=3), analyzer_status(known))
            self.assertNotIn(text("analyzer.quality_unknown"), analyzer_status(known))

    def test_stopped_and_error_labels_do_not_mutate_retained_measurement(self):
        state = self.state()
        for locale in (UiLocale.RU, UiLocale.EN):
            set_active_locale(locale)
            stopped = replace(state, running=False)
            failed = replace(stopped, error="device disconnected")
            self.assertIn(text("analyzer.stopped_last"), analyzer_status(stopped))
            self.assertIn(text("analyzer.failed_last"), analyzer_status(failed))
            self.assertIs(failed.bundle, state.bundle)

    def test_sweep_progress_and_terminal_counts_do_not_count_missing_segments(self):
        state = replace(self.state(), mode=AnalyzerMode.SWEEP, bundle=bundle_from_sweep(_progress()))
        self.assertIn(text("analyzer.progress", received=1, total=2, revision=1), analyzer_status(state))
        self.assertIn(text("analyzer.partial"), analyzer_status(state))
        complete = SweepLineFrame(1, 2, 900, "sweep", SweepLineState.COMPLETE,
                                  np.array([200., 201.]), np.array([-90., -30.]),
                                  np.array([0, 0], dtype=np.uint16), np.array([0, 1]),
                                  (), ((0, 1), (1, 2)), (), "dBFS/bin")
        finished = replace(state, bundle=bundle_from_sweep(complete))
        self.assertIn(text("analyzer.terminal_progress", received=2, total=2), analyzer_status(finished))
        self.assertIn(text("analyzer.completed"), analyzer_status(finished))
        for generations in (((0, 1), (1, 2)), ((0, 1),)):
            gap = replace(complete, state=SweepLineState.GAP, missing_segment_indices=(1,),
                          segment_config_generations=generations, values_db=np.array([-90., np.nan]),
                          source_segment_indices=np.array([0, -1]), quality_flags=np.array([0, 1], dtype=np.uint16))
            gapped = replace(state, bundle=bundle_from_sweep(gap))
            self.assertIn(text("analyzer.terminal_progress", received=1, total=2), analyzer_status(gapped))
            self.assertIn(text("analyzer.gapped"), analyzer_status(gapped))


if __name__ == "__main__":
    unittest.main()
