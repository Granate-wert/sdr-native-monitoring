"""Offscreen Qt coverage for capability-selected AD936x pane profiles."""

from __future__ import annotations

import os
from dataclasses import replace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import CapabilityRange
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan
from sdr_monitor.ui.v2_pane_user_stage import discard_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2

from tests.ui_v2.test_app07_ad936x_rate_profiles import thirty_graph
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class Ad936xProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def editor(self, graph):
        editor = IndependentPaneSetupV2(install=lambda _handle: None, uninstall=lambda: None)
        selected = graph.live.discover(startup=True)[0]
        editor.update_sources(AnalyzerSourceSelection(revision=1, choices=(selected,)))
        row = editor._rows[0]
        row.source.setCurrentIndex(row.source.findData(selected.device_id))
        return editor, row, selected

    def test_fresh_30_72_profile_sweep_window_and_toggle_restore_en_ru(self) -> None:
        native, graph = thirty_graph()
        editor, row, _choice = self.editor(graph)
        locale = current_locale()
        prepared = None
        try:
            self.assertEqual(row.rate.currentData(), 30.72e6)
            self.assertIs(RtbwBandPolicy(row.band.currentData()), RtbwBandPolicy.FULL_RECEIVE)
            row.rate.setCurrentIndex(row.rate.findData(20e6))
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            self.assertEqual(row.rate.currentData(), 30.72e6)
            self.assertEqual(editor._read_drafts()[0].sweep_window_hz, None)
            row.sweep_window.setValue(18.0)
            self.assertEqual(editor._read_drafts()[0].sweep_window_hz, 18e6)
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.RTBW.value))
            self.assertEqual(row.rate.currentData(), 20e6)
            self.assertIs(RtbwBandPolicy(editor._read_drafts()[0].rtbw_band), RtbwBandPolicy.FULL_RECEIVE)
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            self.assertEqual(row.rate.currentData(), 30.72e6)
            row.sweep_window.setValue(0.0)

            pool = PaneProductGraphPool(lambda _resource: graph)
            prepared = prepare_user_pane_session(editor._read_drafts()[:1], pool_factory=lambda: pool)
            profile = prepared.plan.layout.schedule.resources[0].jobs[0].profile
            self.assertEqual(profile.request_template.usable_window_hz, 30e6)
            self.assertEqual(profile.configuration.sample_rate_hz, 30.72e6)
            self.assertEqual(profile.configuration.analog_bandwidth_hz, 30e6)
            self.assertEqual(profile.configuration.fft_size, 8192)
            self.assertEqual(native.engines, [])
            editor._prepared = prepared
            for language, window_label in ((UiLocale.EN, "Sweep window W"),
                                           (UiLocale.RU, "Окно Sweep W")):
                set_active_locale(language)
                editor.set_locale()
                self.assertIn(window_label, tuple(label.text() for label in editor.band_headers))
                self.assertEqual(row.rate.currentData(), 30.72e6)
                self.assertEqual(row.sweep_window.value(), 0.0)
                preview_line = next(line for line in editor.preview.text().splitlines()
                                    if "AD936x Sweep plan" in line or "план сканирования AD936x" in line)
                self.assertIn("RF filter 30 MHz" if language is UiLocale.EN else "RF-фильтр 30 МГц",
                              preview_line)
                self.assertIn("W 30", preview_line)
                self.assertIn("N 4096", preview_line)
                self.assertIn("8192", preview_line)
            editor._prepared = None
            self.assertEqual(native.engines, [])
        finally:
            if prepared is not None:
                discard_user_pane_session(prepared)
            set_active_locale(locale)
            editor.release_after_shutdown()
            editor.close()
            graph.live.shutdown()

    def test_existing_61_44_defaults_stay_full_receive_and_sweep_36(self) -> None:
        native, graph = _ad_graph(serial="ui-ad936x-6144")
        editor, row, _choice = self.editor(graph)
        try:
            self.assertEqual(row.rate.currentData(), 61.44e6)
            self.assertIs(RtbwBandPolicy(row.band.currentData()), RtbwBandPolicy.FULL_RECEIVE)
            self.assertIsNone(editor._read_drafts()[0].sweep_window_hz)
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            self.assertEqual(row.rate.currentData(), 61.44e6)
        finally:
            editor.release_after_shutdown()
            editor.close()
            graph.live.shutdown()

    def test_passive_same_source_refresh_preserves_unsupported_request_for_stage_refusal(self) -> None:
        native, graph = thirty_graph()
        editor, row, choice = self.editor(graph)
        try:
            self.assertEqual(row.rate.currentData(), 30.72e6)
            snapshot = choice.binding.snapshot
            narrowed = replace(snapshot, sample_rate_ranges_hz=(CapabilityRange(2e6, 20e6, "Hz"),))
            refreshed = replace(choice, binding=replace(choice.binding, snapshot=narrowed))
            editor.update_sources(AnalyzerSourceSelection(revision=2, choices=(refreshed,)))
            self.assertEqual(row.rate.currentData(), 30.72e6)
            self.assertFalse(row.rate.model().item(row.rate.currentIndex()).isEnabled())
            draft = editor._read_drafts()[0]
            self.assertEqual(draft.sample_rate_hz, 30.72e6)
            with self.assertRaisesRegex(PaneUserPlanError, "observed capabilities"):
                compile_user_pane_plan((draft,), {refreshed.device_id: refreshed},
                                       {refreshed.device_id: 2})
            self.assertEqual(native.engines, [])
        finally:
            editor.release_after_shutdown()
            editor.close()
            graph.live.shutdown()

    def test_no_admitted_rate_has_no_synthetic_default_and_stage_is_inert(self) -> None:
        native, graph = thirty_graph()
        choice = graph.live.discover(startup=True)[0]
        snapshot = choice.binding.snapshot
        excluded = replace(snapshot,
            sample_rate_ranges_hz=(CapabilityRange(2e6, 19e6, "Hz"),),
            analog_bandwidth_ranges_hz=(CapabilityRange(.2e6, 19e6, "Hz"),))
        excluded_choice = replace(choice, binding=replace(choice.binding, snapshot=excluded))
        editor = IndependentPaneSetupV2(install=lambda _handle: None, uninstall=lambda: None)
        editor.update_sources(AnalyzerSourceSelection(revision=2, choices=(excluded_choice,)))
        row = editor._rows[0]
        row.source.setCurrentIndex(row.source.findData(excluded_choice.device_id))
        try:
            self.assertIsNone(row.rate.currentData())
            self.assertTrue(row.rate.currentText())
            editor.prepare.click()
            self.app.processEvents()
            self.assertEqual(editor._error_key, "analyzer.pane.setup.ad_sweep_window_refusal")
            self.assertIsNone(editor._future)
            self.assertEqual(native.engines, [])
        finally:
            editor.release_after_shutdown()
            editor.close()
            graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
