"""Actual application preview with no receiver access or UI-side planner."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as fixture


class SweepPreviewTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_active_locale, current_locale())
        self.harness = fixture.AnalyzerWorkspaceProductTests("runTest")
        self.harness.setUpClass()
        self.harness.setUp()
        self.page = self.harness.page

    def tearDown(self):
        try:
            self.harness.tearDown()
        finally:
            self.harness.doCleanups()

    def sweep(self):
        self.page.mode.setCurrentIndex(self.page.mode.findData(AnalyzerMode.SWEEP))

    def test_navigation_and_edits_debounce_no_device_command_or_epoch_reservation(self):
        with patch.object(self.harness.presenter, "preview_sweep",
                          wraps=self.harness.presenter.preview_sweep) as preview:
            QTest.qWait(220)
            self.assertEqual(preview.call_count, 0)  # RTBW has no Sweep preview.
            self.harness.select_and_apply()
            before = self.harness.live.latest_snapshot()
            self.sweep()
            for stop in (1001, 1002, 1003, 1004):
                self.page.stop_frequency.setValue(stop)
            self.assertIsNone(self.page.sweep_preview.result)
            self.harness.wait(lambda: self.page.sweep_preview.result is not None)
            self.assertEqual(preview.call_count, 1)
            configuration, request = preview.call_args.args
            self.assertEqual(request.stop_hz, 1004e6)
            self.assertEqual(configuration, before.applied.applied)
            self.assertEqual(self.harness.live.latest_snapshot(), before)
            self.assertEqual(self.harness.events, [])
            result = self.page.sweep_preview.result
            self.assertEqual(result.segment_count, 27)
            self.assertEqual(result.physical_fft_size, configuration.fft_size)
            self.assertGreater(result.statistics_payload_bytes, 0)
            for _ in range(5):
                self.page._render(self.page.model.state)
            QTest.qWait(220)
            self.assertEqual(preview.call_count, 1)

    def test_local_draft_not_applied_and_invalid_geometry_never_starts(self):
        self.harness.select_and_apply()
        self.sweep()
        self.assertTrue(self.page.sweep_preview.resolve())
        original = self.harness.live.latest_snapshot().applied.applied
        self.page.drawer._sample_rate.setValue(20)
        self.assertIsNone(self.page.sweep_preview.result)
        self.assertFalse(self.page.sweep_preview.resolve())
        self.assertIn("window", self.page.sweep_preview.summary.text())
        self.assertEqual(self.harness.live.latest_snapshot().applied.applied, original)
        self.page.drawer.cancel_draft()
        self.assertTrue(self.page.sweep_preview.resolve())
        self.page.start_frequency.setValue(1500)
        self.page.primary.click()
        self.assertEqual(self.harness.events, [])
        self.assertIsNone(self.page.sweep_preview.result)
        self.page.start_frequency.setValue(100)
        self.assertTrue(self.page.sweep_preview.resolve())

    def test_explicit_start_flushes_latest_profile_without_waiting_for_debounce(self):
        self.harness.select_and_apply()
        self.sweep()
        self.assertTrue(self.page.sweep_preview.resolve())
        control = self.page.drawer.sweep_profile.choice
        control.setCurrentIndex(control.findData(SweepSpeedProfile.BALANCED.value))
        self.assertIsNone(self.page.sweep_preview.result)
        self.assertTrue(self.page.sweep_preview._timer.isActive())
        self.page.primary.click()
        self.harness.wait(lambda: self.harness.composition.analyzer_presenter._timer.isActive())
        self.assertEqual(self.page.sweep_preview.result.fft_averaging_frames, 4)
        self.assertFalse(self.page.sweep_preview._timer.isActive())
        self.assertEqual(self.harness.events, ["sweep-start"])
        self.page.primary.click()
        self.harness.wait(lambda: self.harness.composition.analyzer_presenter.can_close())

    def test_locale_updates_labels_without_recalculation_and_details_define_scope(self):
        self.harness.select_and_apply()
        self.sweep()
        self.assertTrue(self.page.sweep_preview.resolve())
        result = self.page.sweep_preview.result
        with patch.object(self.harness.presenter, "preview_sweep", side_effect=AssertionError("recomputed")):
            for locale in UiLocale:
                self.harness.shell.select_appearance_locale(locale)
                self.assertIs(self.page.sweep_preview.result, result)
                label = self.page.sweep_preview.summary
                self.assertIn(text("analyzer.preview.scope"), label.toolTip())
                self.assertEqual(label.toolTip(), label.accessibleDescription())
                self.assertIn(str(result.statistics_payload_bytes), label.toolTip())
                self.assertIn(str(result.physical_fft_size), label.toolTip())
                self.assertIn("RBW", label.toolTip())
                self.assertNotIn("dBm", label.text())

    def test_mode_change_cancels_pending_work_and_disposal_stops_timer(self):
        self.sweep()
        with patch.object(self.harness.presenter, "preview_sweep", side_effect=AssertionError("late preview")):
            self.page.mode.setCurrentIndex(self.page.mode.findData(AnalyzerMode.RTBW))
            QTest.qWait(220)
            self.assertFalse(self.page.sweep_preview._timer.isActive())
            self.assertTrue(self.page.sweep_preview.isHidden())
            self.sweep()
            self.page.close()
            QTest.qWait(220)
            self.assertFalse(self.page.sweep_preview._timer.isActive())

    def test_wrong_contract_refused_and_scalar_preflight_matches_start_policy(self):
        self.harness.select_and_apply()
        self.sweep()
        with patch.object(self.harness.presenter, "preview_sweep", return_value=object()):
            self.assertFalse(self.page.sweep_preview.resolve())
            self.page.primary.click()
            self.assertEqual(self.harness.events, [])
        # Actual application reuses the same bounded scalar factory, no I/O.
        config = self.page.drawer.preview_configuration()
        request = self.page._sweep_request()
        with self.assertRaisesRegex(ValueError, "64-segment"):
            self.harness.presenter.preview_sweep(config, replace(request, stop_hz=6000e6))
        self.assertEqual(self.harness.events, [])

    def test_details_keyboard_and_geometry_without_rf_commands(self):
        self.assertEqual(self.page.visualization.spectrum_scene._empty_overlay._secondary.property(
            "ui2Role"), "utility-action")
        self.harness.select_and_apply()
        self.sweep()
        self.assertTrue(self.page.sweep_preview.resolve())
        preview = self.page.sweep_preview
        preview.details_button.setFocus()
        QTest.keyClick(preview.details_button, Qt.Key.Key_Space)
        self.assertTrue(preview.details.isVisible())
        self.assertIn(text("analyzer.preview.scope"), preview.details.text())
        for locale in UiLocale:
            self.harness.shell.select_appearance_locale(locale)
            for width, height in ((960, 540), (1920, 1080), (2560, 1440)):
                with self.subTest(locale=locale, size=(width, height)):
                    self.harness.shell.resize(width, height)
                    self.harness.app.processEvents()
                    self.assertTrue(self.page.rect().contains(preview.geometry()))
                    self.assertLessEqual(preview.minimumSizeHint().width(), self.page.width())
        preview.details_button.setFocus()
        QTest.keyClick(preview.details_button, Qt.Key.Key_Space)
        self.assertFalse(preview.details.isVisible())
        self.assertEqual(self.harness.events, [])
