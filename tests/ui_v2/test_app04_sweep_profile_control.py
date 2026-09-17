"""Next-Start profile selection is inert, localized and locked during work."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
from sdr_monitor.ui.v2.design import ThemeId, contrast_ratio, tokens_for_theme
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from tests import test_app02_analyzer_workspace_product as product_fixture
from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay


class SweepProfileControlTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_active_locale, current_locale())

    def test_drawer_surface_follows_all_themes_without_a_native_white_viewport(self):
        harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.setUpClass()
        harness.setUp()
        try:
            harness.page.mode.setCurrentIndex(harness.page.mode.findData(AnalyzerMode.SWEEP))
            harness.page.settings.click()
            drawer = harness.page.drawer
            for theme in ThemeId:
                with self.subTest(theme=theme):
                    harness.shell.set_theme(theme)
                    harness.app.processEvents()
                    colors = tokens_for_theme(theme).colors
                    contents = drawer.scroll_area.widget()
                    image = contents.grab().toImage()
                    self.assertEqual(image.pixelColor(2, 2).name().lower(), colors.panel.lower())
                    self.assertGreaterEqual(contrast_ratio(colors.secondary_text, colors.panel), 4.5)
            self.assertEqual(harness.events, [])
        finally:
            try:
                harness.tearDown()
            finally:
                harness.doCleanups()

    def test_profile_drawer_uses_available_height_and_keeps_actions_reachable(self):
        harness = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        harness.setUpClass()
        harness.setUp()
        try:
            page = harness.page
            drawer = page.drawer
            for locale in UiLocale:
                harness.shell.select_appearance_locale(locale)
                for width, height in ((960, 540), (1920, 1080), (2560, 1440)):
                    harness.shell.resize(width, height)
                    for mode in (AnalyzerMode.SWEEP, AnalyzerMode.RTBW, AnalyzerMode.SWEEP):
                        with self.subTest(locale=locale, size=(width, height), mode=mode):
                            page.mode.setCurrentIndex(page.mode.findData(mode))
                            if not drawer.isVisible():
                                page.settings.click()
                            harness.app.processEvents()
                            self.assertTrue(page.rect().contains(drawer.geometry()))
                            scroll = drawer.scroll_area
                            self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                            if height >= 1080:
                                self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
                            scroll.ensureWidgetVisible(drawer._cancel)
                            harness.app.processEvents()
                            bottom = drawer._cancel.mapTo(scroll.viewport(), QPoint(0, drawer._cancel.height()))
                            self.assertLessEqual(bottom.y(), scroll.viewport().height())
                            self.assertFalse(drawer.geometry().intersects(page.primary.geometry()))
            self.assertEqual(harness.events, [])
        finally:
            try:
                harness.tearDown()
            finally:
                harness.doCleanups()

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
