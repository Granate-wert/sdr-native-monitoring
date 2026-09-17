"""Next-Start profile selection is inert, localized and locked during work."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay


class SweepProfileControlTests(unittest.TestCase):
    def test_keyboard_choice_is_inert_preserved_on_locale_and_applied_only_by_start(self):
        harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.app = QApplication.instance() or QApplication([])
        harness.setUp()
        try:
            harness.select_and_apply()
            page = harness.page
            applied = harness.live.latest_snapshot().applied.applied
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
            page.settings.click()
            control = page.drawer.sweep_profile
            self.assertTrue(control.isVisible())
            self.assertIs(control.profile, SweepSpeedProfile.APPLIED)
            control.choice.setFocus()
            QTest.keyClick(control.choice, Qt.Key.Key_Home)
            QTest.keyClick(control.choice, Qt.Key.Key_Down)
            self.assertIs(control.profile, SweepSpeedProfile.QUICK)
            self.assertEqual(harness.events, [])
            self.assertFalse(page.drawer.dirty)
            harness.shell.select_appearance_locale(UiLocale.EN)
            self.assertIs(control.profile, SweepSpeedProfile.QUICK)
            self.assertEqual(control.choice.accessibleName(), text("analyzer.speed.title", UiLocale.EN))
            self.assertEqual(harness.live.latest_snapshot().applied.applied, applied)
            page._hide_settings()
            requests = []
            original = _FakeAnalyzerDisplay.start
            def capture(display, request):
                requests.append(request)
                return original(display, request)
            with patch.object(_FakeAnalyzerDisplay, "start", capture):
                for epoch in (0, 1):
                    page.primary.click()
                    harness.wait(lambda: harness.composition.analyzer_presenter._timer.isActive())
                    self.assertFalse(control.isEnabled())
                    self.assertEqual(requests[-1].speed_profile, SweepSpeedProfile.QUICK)
                    self.assertEqual(requests[-1].epoch, epoch)
                    page.primary.click()
                    harness.wait(lambda: harness.composition.analyzer_presenter.can_close())
                    self.assertTrue(control.isEnabled())
            page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.RTBW))
            self.assertTrue(control.isHidden())
            self.assertEqual(harness.live.latest_snapshot().applied.applied, applied)
            self.assertEqual(harness.events, ["sweep-start", "sweep-stop"] * 2)
        finally:
            try:
                harness.tearDown()
            finally:
                harness.doCleanups()
