"""Actual common owner/root DSP controls and negative publications; fake RX."""

import os
import time
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.ui.v2.design import ThemeId, stylesheet_for_theme, tokens_for_theme
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2.workspaces.analyzer_hackrf_configuration import HackrfConfigurationBar
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph, stage


class HackrfDspOwnerTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        self.g.native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")

    def tearDown(self):
        self.g.application.shutdown()

    def wait(self, predicate):
        deadline = time.monotonic() + 2
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("bounded DSP publication timeout")
            time.sleep(.001)

    def test_exact_group_window_detector_reaches_same_owner_and_bundle(self):
        selection = self.g.application.current_source_selection()
        profile = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20,
            window="nuttall", detector="peak", hop_size=1024, averaging_frames=8,
            source_id=selection.selected_id)
        staged = self.g.application.stage_hackrf_configuration(HackrfConfigurationPatch(profile, selection.revision, 0))
        self.assertTrue(staged.hackrf_detector_groups_available)
        self.assertEqual((self.g.observation.probes, self.g.factory.controls), (0, []))
        self.g.application.start()
        self.wait(lambda: self.g.application.current_snapshot().spectrum is not None)
        snapshot = self.g.application.current_snapshot()
        self.assertEqual(self.g.factory.controls[0].request.averaging_frames, 8)
        self.assertEqual((snapshot.spectrum.numerical_provenance.window,
                          snapshot.spectrum.numerical_provenance.detector,
                          snapshot.spectrum.numerical_provenance.averaging_frames), ("nuttall", "peak", 8))
        self.assertEqual(snapshot.spectrum.hop_size, 1024)
        self.assertEqual(snapshot.unit, "dBFS/bin")
        self.assertIsNone(self.g.application.stop().error)
        self.assertIsNone(self.g.live._external_analyzer_owner)

    def test_optional_runtime_disappears_or_is_malformed_refuses_before_identity_probe(self):
        original = stage(self.g)
        selection = self.g.application.current_source_selection()
        profile = replace(original.hackrf_request, averaging_frames=8)
        for version in (None, True, 0, 2, "1"):
            with self.subTest(version=version):
                self.g.native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = version
                with self.assertRaises(LiveAdmissionRejected):
                    self.g.application.stage_hackrf_configuration(HackrfConfigurationPatch(profile,
                        selection.revision, original.generation))
                self.assertIs(self.g.application.current_snapshot(), original)
                self.assertEqual((self.g.observation.probes, self.g.factory.controls), (0, []))

    def test_start_rechecks_optional_runtime_after_staging(self):
        snapshot = stage(self.g)
        selection = self.g.application.current_source_selection()
        self.g.application.stage_hackrf_configuration(HackrfConfigurationPatch(
            replace(snapshot.hackrf_request, averaging_frames=4), selection.revision, snapshot.generation))
        self.g.native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = None
        refused = self.g.application.start()
        self.assertIsNotNone(refused.error)
        self.assertFalse(refused.stop_required)
        self.assertIsNone(self.g.live._external_analyzer_owner)
        self.assertEqual((self.g.observation.probes, self.g.factory.controls), (0, []))

    def test_missing_or_mismatching_producer_dsp_metadata_fails_closed(self):
        mutations = (lambda frame: setattr(frame, "window", "nuttall"),
                     lambda frame: setattr(frame, "detector", "peak"),
                     lambda frame: setattr(frame, "averaging_frames", 2),
                     lambda frame: setattr(frame, "averaging_frames", 0),
                     lambda frame: setattr(frame, "precision_mode", "fast_f32"),
                     lambda frame: delattr(frame, "window"),
                     lambda frame: delattr(frame, "calibration_status"))
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                stage(self.g)
                self.g.factory.change = mutation
                self.g.application.start()
                self.wait(lambda: self.g.application.current_snapshot().error is not None)
                snapshot = self.g.application.current_snapshot()
                self.assertIsNone(snapshot.spectrum)
                self.assertTrue(snapshot.stop_required)
                self.assertIsNone(self.g.application.stop().error)
                self.assertIsNone(self.g.live._external_analyzer_owner)


class HackrfDspUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("bounded common V2 DSP completion timeout")
            time.sleep(.001)

    def test_draft_preserves_hidden_queue_fields_and_arbitrary_hop_without_io(self):
        base = HackrfLiveRequest(100e6, 20e6, 15_000_000, 16, 20, source_id="source-hackrf",
            slot_count=12, ready_capacity=7, presentation_capacity=9, dsp_output_capacity=3,
            window="kaiser", detector="average_power", averaging_frames=4, hop_size=1234)
        state = SimpleNamespace(controls_locked=False, hackrf_controls_available=True,
            source_selection=SimpleNamespace(revision=6, selected=SimpleNamespace(device_id="source-hackrf")),
            live=SimpleNamespace(snapshot=SimpleNamespace(generation=4, hackrf_request=base,
                                                         hackrf_detector_groups_available=True)))
        model = SimpleNamespace(state=state, stage_hackrf_configuration=Mock())
        bar = HackrfConfigurationBar(model)
        try:
            bar.apply_view_state(state)
            self.assertEqual(bar.hop.value(), 1234)
            self.assertEqual(bar.averaging.value(), 4)
            bar.lna.setValue(24)
            bar._stage()
            draft = model.stage_hackrf_configuration.call_args.args[0].request
            self.assertEqual((draft.slot_count, draft.ready_capacity, draft.presentation_capacity,
                              draft.dsp_output_capacity, draft.hop_size, draft.averaging_frames), (12, 7, 9, 3, 1234, 4))
            self.assertEqual((draft.window, draft.detector, draft.lna_gain_db), ("kaiser", "average_power", 24))
            self.assertFalse(draft.rf_amplifier_enabled)
            self.assertFalse(draft.bias_tee_enabled)
            # Locale and expansion are view-only, never stage or reset a draft.
            count = model.stage_hackrf_configuration.call_count
            locale = current_locale()
            try:
                for value in (UiLocale.EN, UiLocale.RU):
                    set_active_locale(value)
                    bar.set_locale()
                    bar.dsp_toggle.setChecked(not bar.dsp_toggle.isChecked())
                    self.assertEqual((bar.hop.value(), bar.averaging.value(), bar.fft_window.currentData()), (1234, 4, "kaiser"))
                    self.assertTrue(bar.dirty)
            finally:
                set_active_locale(locale)
            self.assertEqual(model.stage_hackrf_configuration.call_count, count)
        finally:
            bar.deleteLater()
            self.app.processEvents()

    def test_old_runtime_disables_groups_and_fft_change_clamps_only_visible_hop(self):
        state = SimpleNamespace(controls_locked=False, hackrf_controls_available=True,
            source_selection=SimpleNamespace(revision=1),
            live=SimpleNamespace(snapshot=SimpleNamespace(generation=0, hackrf_request=None)))
        model = SimpleNamespace(state=state)
        bar = HackrfConfigurationBar(model)
        try:
            bar.apply_view_state(state)
            self.assertFalse(bar.averaging.isEnabled())
            self.assertEqual(bar.averaging.toolTip(), text("hackrf.group.unavailable"))
            bar.fft.setCurrentIndex(bar.fft.findData(512))
            self.assertEqual(bar.hop.maximum(), 512)
            self.assertEqual(bar.hop.value(), 512)
            self.assertTrue(bar.dirty)
        finally:
            bar.deleteLater()
            self.app.processEvents()

    def test_shared_theme_roles_keep_labels_and_fields_legible_in_all_three_themes(self):
        bar = HackrfConfigurationBar(SimpleNamespace(state=SimpleNamespace(controls_locked=False)))
        try:
            bar._reset()
            bar.setProperty("ui2Root", True)
            for theme in ThemeId:
                colors = tokens_for_theme(theme).colors
                bar.setStyleSheet(stylesheet_for_theme(theme))
                for label, _key in bar._labels:
                    label.ensurePolished()
                    self.assertEqual(label.property("ui2Role"), "secondary")
                    self.assertEqual(label.palette().color(QPalette.ColorRole.WindowText).name(), colors.secondary_text.lower())
                for field in bar._fields:
                    self.assertIn(field.property("ui2Role"), ("utility-select", "range-control"))
                    field.ensurePolished()
                    self.assertEqual(field.palette().color(QPalette.ColorRole.Text).name(), colors.primary_text.lower())
                self.assertEqual(bar.dsp_toggle.property("ui2Role"), "utility-action")
        finally:
            bar.deleteLater()
            self.app.processEvents()

    def test_supported_large_fft_and_nonpreset_fs_are_preserved_without_growing_list(self):
        base = HackrfLiveRequest(100e6, 12e6, 10_000_000, 16, 20, source_id="source-hackrf",
                                fft_size=262144, hop_size=131072)
        state = SimpleNamespace(controls_locked=False, hackrf_controls_available=True,
            source_selection=SimpleNamespace(revision=1), live=SimpleNamespace(
                snapshot=SimpleNamespace(generation=1, hackrf_request=base)))
        bar = HackrfConfigurationBar(SimpleNamespace(state=state))
        try:
            bar.apply_view_state(state)
            for _ in range(3):
                bar._reset()
                self.assertEqual((bar.fft.currentData(), bar.rate.currentData(), bar.hop.value()),
                                 (262144, 12e6, 131072))
                self.assertEqual(bar.rate.count(), 5)
                self.assertFalse(bar.dirty)
        finally:
            bar.deleteLater()
            self.app.processEvents()

    def test_actual_root_dsp_stage_same_canvas_locked_fields_and_explicit_stop(self):
        g = graph()
        g.native.HACKRF_DSP_PROFILE_CONTRACT_VERSION = 1
        captures = []
        def capture(*args, **kwargs):
            result = compose_v2_live_product(*args, **kwargs)
            captures.append(result)
            return result
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_hackrf=g.hackrf,
            sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=capture):
            shell = build_v2_shell(services)
        composition = captures[0]
        page = shell._workspace_pages["analyzer"]
        try:
            page.discover.click()
            self.wait(lambda: page.source.count() == 3 and not composition.view_model.state.busy)
            page.source.setCurrentIndex(page.source.findData("source-hackrf"))
            self.wait(lambda: composition.analyzer_view_model.state.hackrf_controls_available and not composition.view_model.state.busy)
            bar = page.hackrf_bar
            self.assertTrue(bar.averaging.isEnabled())
            bar.dsp_toggle.setChecked(True)
            bar.fft_window.setCurrentIndex(bar.fft_window.findData("blackman_harris_4term"))
            bar.detector.setCurrentIndex(bar.detector.findData("peak"))
            bar.hop.setValue(1024)
            bar.averaging.setValue(8)
            self.assertFalse(page.primary.isEnabled())
            bar.stage.click()
            self.wait(lambda: composition.analyzer_view_model.state.rtbw_profile_ready and not composition.view_model.state.busy)
            self.assertEqual(g.observation.probes, 0)
            self.assertIn(text("hackrf.window.blackman_harris_4term"), page.applied.text())
            page.primary.click()
            self.wait(lambda: composition.analyzer_view_model.state.bundle is not None and not composition.view_model.state.busy)
            self.assertIsNotNone(page.visualization.spectrum_scene.latest_frame)
            self.assertEqual(composition.analyzer_view_model.state.bundle.spectrum.numerical_provenance.averaging_frames, 8)
            self.assertFalse(any(field.isEnabled() for field in bar._fields))
            page.primary.click()
            self.wait(lambda: not composition.analyzer_view_model.state.running and not composition.view_model.state.busy)
            self.assertTrue(bar.averaging.isEnabled())
            self.assertEqual((bar.fft_window.currentData(), bar.detector.currentData(), bar.hop.value(), bar.averaging.value()),
                             ("blackman_harris_4term", "peak", 1024, 8))
        finally:
            shell.close()
            self.wait(lambda: shell._is_closed)
            composition.shutdown()
            shell.deleteLater()
            self.app.processEvents()
        self.assertIsNone(g.live._external_analyzer_owner)
        self.assertIsNone(g.hackrf._poller)


if __name__ == "__main__":
    unittest.main()
