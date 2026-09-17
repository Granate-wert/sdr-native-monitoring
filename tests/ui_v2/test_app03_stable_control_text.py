"""Repeated publications must not rewrite unchanged Analyzer controls."""

import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import tests.test_app02_analyzer_workspace_product as product_fixture


class StableControlTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_actual_workspace_skips_unchanged_text_and_delivers_error_immediately(self):
        from dataclasses import replace

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            page = fixture.page
            state = page.model.state
            page._render(state)
            with patch.object(page.primary, "setText", wraps=page.primary.setText) as primary, \
                    patch.object(page.applied, "setText", wraps=page.applied.setText) as applied, \
                    patch.object(page.error, "setText", wraps=page.error.setText) as error:
                for _ in range(100):
                    page._render(state)
                primary.assert_not_called()
                applied.assert_not_called()
                error.assert_not_called()
                page._render(replace(state, error="receiver disconnected"))
                error.assert_called_once_with("receiver disconnected")
                self.assertEqual(page.error.text(), "receiver disconnected")
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_primary_remains_start_instead_of_duplicate_discover_or_settings(self):
        from sdr_monitor.ui.v2.i18n import text

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            page = fixture.page
            self.assertEqual(page.primary.text(), text("analyzer.start"))
            self.assertFalse(page.primary.isEnabled())
            self.assertTrue(page.discover.isEnabled())
            self.assertTrue(page.settings.isEnabled())
            fixture.select_and_apply()
            self.assertEqual(page.primary.text(), text("analyzer.start"))
            self.assertTrue(page.primary.isEnabled())
            page.frequency_bar.gain.setValue(22)
            self.assertEqual(page.primary.text(), text("analyzer.start"))
            self.assertFalse(page.primary.isEnabled())
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_clean_configuration_is_not_reloaded_for_each_measurement(self):
        from dataclasses import replace

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            fixture.select_and_apply()
            drawer = fixture.page.drawer
            state = fixture.page.model.state
            with patch.object(drawer, "_load", wraps=drawer._load) as load:
                for _ in range(100):
                    drawer._render(state)
                load.assert_not_called()
                snapshot = state.live.snapshot
                applied = replace(snapshot.applied,
                                  applied=replace(snapshot.applied.applied, gain_db=22))
                changed = replace(snapshot, applied=applied)
                drawer._render(replace(state, live=replace(state.live, snapshot=changed)))
                load.assert_called_once_with(applied.applied)
                self.assertEqual(drawer._gain.value(), 22)
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_status_height_and_primary_width_survive_text_length_changes(self):
        from dataclasses import replace
        from sdr_monitor.ui.v2.i18n import UiLocale

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            for locale in (UiLocale.RU, UiLocale.EN):
                fixture.shell.select_appearance_locale(locale)
                page = fixture.page
                state = page.model.state
                width = page.primary.width()
                height = page.status.height()
                for changes in ({"starting": True}, {"stopping": True}, {"running": True},
                                {"configuration_pending": True}):
                    page._render(replace(state, **changes))
                    self.app.processEvents()
                    self.assertEqual(page.primary.width(), width)
                for message in ("1", "quality loss / " * 100):
                    page.status.setText(message)
                    self.app.processEvents()
                    self.assertEqual(page.status.height(), height)
                    self.assertEqual(page.status.text(), message)
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_pending_density_retains_last_layer_but_identity_conflict_clears_it(self):
        from dataclasses import replace
        from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
        from tests.test_app03_persistence_cadence import PersistenceCadenceTests

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            page = fixture.page
            snapshot = PersistenceCadenceTests().snapshot()

            def deliver(value):
                live = build_live_view_state(value)
                page._render(replace(page.model.state, live=live, bundle=live.analyzer_bundle, running=True))

            deliver(snapshot)
            previous = page._last_persistence
            self.assertIsNotNone(previous)
            future = replace(snapshot, persistence=replace(snapshot.persistence, source_frame_sequence=8))
            with patch.object(page.visualization.spectrum_scene, "clear_persistence_display",
                              wraps=page.visualization.spectrum_scene.clear_persistence_display) as clear:
                deliver(future)
                clear.assert_not_called()
                self.assertIs(page._last_persistence, previous)
                wrong = replace(future, persistence=replace(future.persistence, acquisition_epoch=99))
                deliver(wrong)
                clear.assert_called_once()
                self.assertIsNone(page._last_persistence)
        finally:
            fixture.tearDown()
            fixture.doCleanups()

    def test_pending_density_never_retains_another_accumulation_or_clock(self):
        from dataclasses import replace
        from sdr_monitor.ui.v2.state.live_view_state import build_live_view_state
        from tests.test_app03_persistence_cadence import PersistenceCadenceTests

        fixture = product_fixture.AnalyzerWorkspaceProductTests("runTest")
        fixture.app = self.app
        fixture.setUp()
        try:
            page = fixture.page

            def deliver(value):
                live = build_live_view_state(value)
                page._render(replace(page.model.state, live=live, bundle=live.analyzer_bundle, running=True))

            for field in ("accumulation_id", "clock_domain"):
                with self.subTest(field=field):
                    base = PersistenceCadenceTests().snapshot()
                    old = replace(base, spectrum=replace(base.spectrum, **{field: "old"}),
                                  persistence=replace(base.persistence, **{field: "old"}))
                    deliver(old)
                    self.assertIsNotNone(page._last_persistence)
                    new = replace(old, spectrum=replace(old.spectrum, **{field: "new"}),
                                  persistence=replace(old.persistence, source_frame_sequence=8,
                                                      **{field: "new"}))
                    deliver(new)
                    self.assertIsNone(page._last_persistence)
        finally:
            fixture.tearDown()
            fixture.doCleanups()
