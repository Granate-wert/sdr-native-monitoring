"""Actual composition keeps unapplied RF draft through appearance changes."""

import unittest

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.i18n import UiLocale
from sdr_monitor.ui.v2.design import ThemeId
import tests.test_app02_analyzer_workspace_product as product_fixture


class DirtyAppearanceTests(unittest.TestCase):
    def test_dirty_rf_draft_survives_language_and_theme_without_device_commands(self):
        app = QApplication.instance() or QApplication([])
        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = app
        fixture.setUp()
        try:
            fixture.select_and_apply()
            baseline = fixture.live.latest_snapshot().applied
            canvas = fixture.page.visualization
            fixture.page.settings.click()
            drawer = fixture.page.drawer
            self.assertTrue(drawer._center.isEnabled())
            drawer._center.setValue(915)
            drawer._gain.setValue(23)
            self.assertTrue(drawer.dirty)
            for locale, theme in ((UiLocale.EN, ThemeId.HIGH_CONTRAST),
                                  (UiLocale.RU, ThemeId.DARK)):
                fixture.shell.select_appearance_locale(locale)
                fixture.shell.select_appearance_theme(theme)
                app.processEvents()
                self.assertIs(fixture.page.visualization, canvas)
                self.assertEqual(drawer._center.value(), 915)
                self.assertEqual(drawer._gain.value(), 23)
                self.assertTrue(drawer.dirty)
                self.assertEqual(fixture.live.latest_snapshot().applied, baseline)
                self.assertEqual(fixture.events, [])
        finally:
            try:
                fixture.tearDown()
            finally:
                fixture.doCleanups()
