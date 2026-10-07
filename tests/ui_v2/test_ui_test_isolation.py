"""Real private Qt persistence and explicit locale ownership, without RX."""
from io import StringIO
from pathlib import Path
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QWidget

from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.shell import AppShellV2
from sdr_monitor.ui.v2.waterfall.contracts import WaterfallDirection, WaterfallPalette
from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
from sdr_monitor.ui.v2.waterfall.spectrum_view import SpectrumWaterfallView
from tests.ui_v2.ui_test_isolation import PrivateUiSettings, flush_deferred_widgets, own_locale


class UiTestIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        own_locale(self)

    def retire(self, widget):
        widget.close()
        widget.deleteLater()
        flush_deferred_widgets()

    def test_locale_restores_incoming_en_ru_after_success_failure_and_failed_setup(self):
        for incoming in UiLocale:
            for outcome in ("success", "failure", "setup-failure"):
                with self.subTest(incoming=incoming, outcome=outcome):
                    set_active_locale(incoming)
                    observed = []
                    class Probe(unittest.TestCase):
                        def setUp(inner):
                            own_locale(inner)
                            inner.addCleanup(lambda: observed.append(current_locale()))
                            if outcome == "setup-failure":
                                raise RuntimeError("intentional fixture setup failure")
                        def runTest(inner):
                            inner.assertIs(current_locale(), UiLocale.RU)
                            if outcome == "failure":
                                inner.fail("intentional assertion failure")
                    result = unittest.TextTestRunner(stream=StringIO()).run(Probe())
                    self.assertEqual(len(result.failures), int(outcome == "failure"))
                    self.assertEqual(len(result.errors), int(outcome == "setup-failure"))
                    self.assertEqual(observed, [UiLocale.RU])
                    self.assertIs(current_locale(), incoming)

    def test_real_private_files_round_trip_and_namespaces_do_not_leak(self):
        with PrivateUiSettings() as first:
            settings = first.settings("one")
            settings.setValue("ui_v2/shell/locale", "en")
            settings.setValue("ui_v2/live/waterfall/v1/visible", False)
            settings.setValue("ui_v2/live/waterfall/v1/history_seconds", 120)
            settings.sync()
            reopened = first.settings("one")
            self.assertFalse(reopened.fallbacksEnabled())
            self.assertEqual(reopened.value("ui_v2/shell/locale"), "en")
            self.assertFalse(reopened.value("ui_v2/live/waterfall/v1/visible", type=bool))
            self.assertEqual(reopened.value("ui_v2/live/waterfall/v1/history_seconds", type=int), 120)
            unrelated = first.settings("two")
            self.assertEqual(unrelated.allKeys(), [])
            with PrivateUiSettings() as second:
                self.assertEqual(second.settings("one").allKeys(), [])
                self.assertNotEqual(first.root, second.root)

    def test_scoped_factories_use_real_private_defaults_and_delegate_explicit_arguments(self):
        from sdr_monitor.ui.v2.shell import app_shell
        from sdr_monitor.ui.v2.waterfall import pane, spectrum_view
        originals = (app_shell.QSettings, pane.QSettings, spectrum_view.QSettings)
        with PrivateUiSettings(("shell", "waterfall-pane", "spectrum-view")) as stores:
            for module in (app_shell, pane, spectrum_view):
                value = module.QSettings()
                self.assertIsInstance(value, QSettings)
                self.assertTrue(Path(value.fileName()).is_relative_to(stores.root))
                self.assertFalse(value.fallbacksEnabled())
            explicit_path = str(stores.root / "explicit.ini")
            delegated = pane.QSettings(explicit_path, QSettings.Format.IniFormat)
            delegated.setFallbacksEnabled(False)
            delegated.setValue("explicit", 7)
            delegated.sync()
            self.assertEqual(Path(delegated.fileName()), Path(explicit_path))
            explicit_reopened = pane.QSettings(explicit_path, QSettings.Format.IniFormat)
            explicit_reopened.setFallbacksEnabled(False)
            self.assertEqual(explicit_reopened.value("explicit", type=int), 7)
            self.assertEqual([namespace for namespace, _ in stores.default_calls],
                             ["shell", "waterfall-pane", "spectrum-view"])
        self.assertEqual((app_shell.QSettings, pane.QSettings, spectrum_view.QSettings), originals)

    def test_failed_fixture_deletes_widget_before_temp_removal_and_locale_restore(self):
        set_active_locale(UiLocale.EN)
        observed = []
        class Probe(unittest.TestCase):
            def setUp(inner):
                own_locale(inner)
                stores = PrivateUiSettings()
                inner.addCleanup(stores.close)
                inner.root = stores.root
                widget = QWidget()
                widget.destroyed.connect(lambda: observed.append((current_locale(), stores.root.exists())))
                inner.addCleanup(lambda: self.retire(widget))
            def runTest(inner):
                inner.fail("intentional failure with a live widget")
        probe = Probe()
        result = unittest.TextTestRunner(stream=StringIO()).run(probe)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(observed, [(UiLocale.RU, True)])
        self.assertFalse(probe.root.exists())
        self.assertIs(current_locale(), UiLocale.EN)

    def test_actual_widgets_close_flush_and_reopen_private_preferences(self):
        with PrivateUiSettings(("shell", "waterfall-pane", "spectrum-view")) as stores:
            shell = AppShellV2()
            shell.select_appearance_locale(UiLocale.EN)
            shell.set_theme(ThemeId.HIGH_CONTRAST)
            self.retire(shell)
            reopened_shell = AppShellV2()
            try:
                self.assertIs(reopened_shell.current_locale, UiLocale.EN)
                self.assertIs(reopened_shell.current_theme, ThemeId.HIGH_CONTRAST)
            finally:
                self.retire(reopened_shell)
            pane = WaterfallPane()
            pane.set_render_visible(False)
            pane.set_history_seconds(120)
            pane.set_palette(WaterfallPalette.GRAYSCALE)
            pane.set_direction(WaterfallDirection.NEWEST_AT_BOTTOM)
            self.retire(pane)
            reopened_pane = WaterfallPane()
            try:
                self.assertFalse(reopened_pane.render_visible)
                self.assertEqual(reopened_pane.config.history_seconds, 120)
                self.assertIs(reopened_pane.config.palette, WaterfallPalette.GRAYSCALE)
                self.assertIs(reopened_pane.config.direction, WaterfallDirection.NEWEST_AT_BOTTOM)
            finally:
                self.retire(reopened_pane)
            view = SpectrumWaterfallView()
            try:
                self.assertTrue(view.waterfall_pane.render_visible)
            finally:
                self.retire(view)
            self.assertTrue(stores.default_calls)
            self.assertTrue(all(Path(path).is_relative_to(stores.root) for _, path in stores.default_calls))
