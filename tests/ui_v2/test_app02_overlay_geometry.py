"""Actual Analyzer overlay containment and scroll reachability matrix."""

from __future__ import annotations

import unittest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from sdr_monitor.ui.v2.i18n import UiLocale, set_active_locale
from tests import test_app02_analyzer_workspace_product as product_fixture


class AnalyzerOverlayGeometryTests(unittest.TestCase):
    """Reuse the actual product fixture without inheriting its test methods."""

    def setUp(self) -> None:
        self.owner = product_fixture.AnalyzerWorkspaceProductTests(methodName="runTest")
        self.owner.setUpClass()
        self.owner.setUp()

    def tearDown(self) -> None:
        self.owner.tearDown()
        self.owner.doCleanups()
        set_active_locale(UiLocale.RU)

    def test_drawers_stay_inside_and_all_controls_are_scroll_reachable(self) -> None:
        page = self.owner.page
        for locale in (UiLocale.RU, UiLocale.EN):
            set_active_locale(locale)
            page.set_locale()
            for width, height in ((960, 540), (1280, 720), (1366, 768), (1920, 1080)):
                with self.subTest(locale=locale, size=(width, height)):
                    self.owner.shell.resize(width, height)
                    self.owner.app.processEvents()
                    for overlay, toggle, last_control in (
                        (page.drawer, page.settings, page.drawer._cancel),
                        (page.display_controls, page.display, page.visualization.waterfall_pane._follow_spectrum),
                    ):
                        toggle.click()
                        self.owner.app.processEvents()
                        self.assertTrue(overlay.isVisible())
                        self.assertTrue(page.rect().contains(overlay.geometry()))
                        scroll = overlay.scroll_area
                        scroll.ensureWidgetVisible(last_control)
                        self.owner.app.processEvents()
                        point = last_control.mapTo(scroll.viewport(), QPoint(0, 0))
                        self.assertGreaterEqual(point.y(), 0)
                        self.assertLessEqual(point.y() + last_control.height(), scroll.viewport().height())
                        self.assertGreaterEqual(point.x(), 0)
                        self.assertLessEqual(point.x() + last_control.width(), scroll.viewport().width())
                        self.assertTrue(page.primary.isVisible())
                        QTest.keyClick(overlay, Qt.Key.Key_Escape)
                        self.owner.app.processEvents()
                        self.assertFalse(overlay.isVisible())
                        self.assertTrue(toggle.hasFocus())


if __name__ == "__main__":
    unittest.main()
