"""Actual V2 root with a long staged Sweep plan; fake SDK and no RX.

Logical widget sizes are not Windows per-monitor DPI, RF or raster FPS proof.
"""

from __future__ import annotations

import os
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_hackrf_sweep_common_analyzer import FakeHackrfSweep
from tests.ui_v2.test_app07_ad936x_sweep_pane_owner import ad_sweep_graph


class StagedPaneEditorGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.locale = current_locale()
        self.fixture = product.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self.editor = self.fixture.page._independent_setup
        self.assertIsNotNone(self.editor)
        self.native, self.ad_sweep, self.ad = ad_sweep_graph()
        self.hf_fixture = hackrf_fixture()
        self.hf_sweep = FakeHackrfSweep()
        self.hf = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=self.hf_fixture.live, device_catalog=self.hf_fixture.catalog,
            analyzer_hackrf=self.hf_fixture.hackrf, analyzer_hackrf_sweep=self.hf_sweep))
        self.graphs = (self.ad, self.hf)
        self.choices = tuple(next(choice for choice in graph.live.discover(startup=True)
                                 if choice.family is family)
                             for graph, family in zip(self.graphs,
                                (DeviceFamily.AD936X, DeviceFamily.HACKRF), strict=True))
        self.selection = AnalyzerSourceSelection(
            revision=1, choices=self.choices, selected_id=self.choices[0].device_id)
        self.pools = []

    def _wait(self, predicate) -> None:
        deadline = monotonic() + 8
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(0.005)
        self.fail("staged editor did not reach a bounded terminal state")

    def _stage(self, drafts):
        pool = PaneProductGraphPool(lambda resource: self.graphs[
            int(resource.rsplit("-", 1)[-1]) - 1])
        self.pools.append(pool)
        return prepare_user_pane_session(drafts, pool_factory=lambda: pool)

    def prepare_long_preview(self, *, refused=False) -> None:
        self.fixture.page._toggle_independent_setup()
        # Publish through the actual root, not just this editor: otherwise its
        # state_changed callback replaces our private choices with an empty
        # base catalogue as soon as Discard unlocks the form.
        self.fixture.presenter.source_selection_changed.emit(self.selection)
        self.assertIs(self.fixture.page.model.state.source_selection, self.selection)
        for row, choice, start, stop in (
                (self.editor._rows[0], self.choices[0], 100, 220),
                (self.editor._rows[1], self.choices[0], 300, 420),
                (self.editor._rows[2], self.choices[1], 1, 6000)):
            row.source.setCurrentIndex(row.source.findData(choice.device_id))
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            row.start.setValue(start)
            row.stop.setValue(stop)
        self.editor._rows[2].fft.setCurrentIndex(self.editor._rows[2].fft.findData(2048))
        self.editor._rows[1].priority.setValue(3)
        self.editor._rows[0].maximum_revisit.setValue(1 if refused else 4.08)
        self.editor._rows[1].maximum_revisit.setValue(1 if refused else 2.06)
        self.editor.scheduler_toggle.click()
        with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                   side_effect=self._stage):
            self.editor.prepare.click()
            self._wait(lambda: self.editor._future is None)
        if refused:
            self.assertIsNone(self.editor._prepared)
            self.assertFalse(self.editor.apply.isEnabled())
            self.assertTrue(self.editor.error.isVisible())
        else:
            self.assertIsNotNone(self.editor._prepared, self.editor.error.text())
            self.assertGreaterEqual(len(self.editor.preview.text().splitlines()), 11)
            self.assertTrue(self.editor.apply.isEnabled())
        self.assertEqual(self.native.engines, [])
        self.assertEqual(self.ad_sweep.events, [])
        self.assertEqual(self.hf_sweep.events, [])
        self.assertEqual(self.hf_fixture.factory.controls, [])

    def tearDown(self) -> None:
        try:
            if self.editor._prepared is not None or self.editor._retained_pool is not None:
                self.editor.discard.click()
                self._wait(lambda: self.editor.can_close)
            self.fixture.tearDown()
        finally:
            for graph in self.graphs:
                graph.live.shutdown()
            self.fixture.doCleanups()
            set_active_locale(self.locale)

    def test_expanded_staged_editor_does_not_compress_controls_in_actual_root(self) -> None:
        self.fixture.shell.select_appearance_locale(UiLocale.EN)
        self.fixture.shell.setFixedSize(1440, 912)
        self.prepare_long_preview()
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual((self.fixture.shell.width(), self.fixture.shell.height()), (1440, 912))
        for row in self.editor._rows:
            for field in (row.source, row.start, row.stop, row.rate, row.fft,
                          row.mode_points, row.priority, row.maximum_revisit):
                self.assertGreaterEqual(field.height(), field.minimumSizeHint().height(),
                    f"pane{row.number}: {type(field).__name__} has clipped control content")
        self.assertGreaterEqual(self.editor.preview.height(),
                                self.editor.preview.heightForWidth(self.editor.preview.width()),
                                "long accepted impact preview is clipped")

    def _assert_readable_and_reachable(self) -> None:
        shell = self.fixture.shell
        for row in self.editor._rows:
            for field in (row.source, row.start, row.stop, row.rate, row.fft,
                          row.mode_points, row.priority, row.maximum_revisit):
                self.assertGreaterEqual(field.height(), field.minimumSizeHint().height())
        for button in (self.editor.prepare, self.editor.apply, self.editor.discard,
                       self.editor.scheduler_toggle, self.editor.details):
            self.assertTrue(shell.rect().contains(button.mapTo(shell, QPoint(0, 0))))
            self.assertTrue(shell.rect().contains(button.mapTo(shell, button.rect().bottomRight())))
            self.assertGreaterEqual(button.height(), button.minimumSizeHint().height())
        summary = self.editor.impact_summary
        self.assertIs(summary.parentWidget(), self.editor)
        self.assertTrue(summary.isVisible())
        self.assertEqual(summary.accessibleName(), summary.text())
        self.assertGreaterEqual(summary.height(), summary.heightForWidth(summary.width()))
        self.assertTrue(shell.rect().contains(summary.mapTo(shell, QPoint(0, 0))))
        self.assertTrue(shell.rect().contains(summary.mapTo(shell, summary.rect().bottomRight())))
        self.assertGreaterEqual(self.editor.preview.height(),
                                self.editor.preview.heightForWidth(self.editor.preview.width()))
        scroll = self.editor.scroll_area
        self.editor.details.click()
        self.app.processEvents()
        self.assertTrue(scroll.hasFocus())
        for _ in range(20):
            if scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum():
                break
            QTest.keyClick(scroll, Qt.Key.Key_PageDown)
            self.app.processEvents()
        last_line = self.editor.preview.mapTo(scroll.viewport(),
                                            QPoint(1, self.editor.preview.height() - 1))
        self.assertGreaterEqual(last_line.y(), 0)
        self.assertLess(last_line.y(), scroll.viewport().height(),
                        "last impact/coverage limitation is not reachable by keyboard scrolling")
        self.assertEqual(self.editor.preview.accessibleName(), self.editor.preview.text())
        self.assertLessEqual(self.editor.height(), 480)

    def test_staged_fields_preview_and_commands_fit_logical_size_theme_locale_matrix(self) -> None:
        self.prepare_long_preview()
        prepared = self.editor._prepared
        preview_plan = prepared.plan
        for locale in (UiLocale.EN, UiLocale.RU):
            self.fixture.shell.select_appearance_locale(locale)
            for theme in ThemeId:
                self.fixture.shell.set_theme(theme)
                for width, height in ((1440, 912), (1280, 720), (1920, 1080),
                                      (1707, 960), (960, 540)):
                    for expanded in (True, False):
                        with self.subTest(locale=locale, theme=theme, size=(width, height),
                                          expanded=expanded):
                            self.fixture.shell.setFixedSize(width, height)
                            self.editor.scheduler_toggle.setChecked(expanded)
                            for _ in range(4):
                                self.app.processEvents()
                            self.assertEqual((self.fixture.shell.width(), self.fixture.shell.height()),
                                             (width, height))
                            self._assert_readable_and_reachable()
                            self.assertIs(self.editor._prepared, prepared)
                            self.assertIs(prepared.plan, preview_plan)
                            self.assertFalse(self.editor._rows[0].source.isEnabled())
                            self.assertFalse(self.editor._rows[1].maximum_revisit.isEnabled())
                            for row in self.editor._rows[:3]:
                                self.assertEqual(row.mode.currentText(),
                                                 text("analyzer.pane.setup.mode_sweep"))
        self.assertEqual(self.native.engines, [])
        self.assertEqual(self.ad_sweep.events, [])
        self.assertEqual(self.hf_sweep.events, [])
        self.assertEqual(self.fixture.events, [])

    def test_discard_clears_accessible_preview_and_returns_unlocked_draft_without_rx(self) -> None:
        self.fixture.shell.setFixedSize(1280, 720)
        self.prepare_long_preview()
        self.editor.details.click()
        self.app.processEvents()
        self.assertTrue(self.editor.details.isEnabled())
        self.editor.discard.click()
        self._wait(lambda: self.editor.can_close)
        self.assertEqual(self.editor.preview.text(), "")
        self.assertEqual(self.editor.preview.accessibleName(), "")
        self.assertEqual(self.editor.impact_summary.text(), "")
        self.assertFalse(self.editor.details.isEnabled())
        self.assertFalse(self.editor.apply.isEnabled())
        self.assertTrue(self.editor.prepare.isEnabled())
        self.assertTrue(self.editor._rows[0].source.isEnabled())
        self.assertIs(self.editor._selection, self.selection)
        self.assertEqual(self.editor._rows[0].source.currentData(), self.choices[0].device_id)
        self.assertEqual(self.editor._selected_mode(self.editor._rows[0]), CaptureMeasurementMode.SWEEP)
        self.editor.scroll_area.verticalScrollBar().setValue(0)
        priority = self.editor._rows[0].priority
        target = self.editor._rows[0].maximum_revisit
        priority.setFocus()
        self.app.processEvents()
        self.assertTrue(priority.hasFocus(), "priority field is not focusable after Discard")
        QTest.keyClick(priority, Qt.Key.Key_Tab)
        self.app.processEvents()
        focused = self.app.focusWidget()
        self.assertTrue(target.hasFocus(), f"Tab from priority reached {focused!r}; "
                        f"name={'' if focused is None else focused.accessibleName()}")
        self.assertTrue(self.editor.scroll_area.viewport().rect().contains(
            target.mapTo(self.editor.scroll_area.viewport(), target.rect().center())))
        self.assertEqual((priority.value(), target.value()), (1, 4.08))
        self.assertEqual(self.native.engines, [])
        self.assertEqual(self.ad_sweep.events, [])
        self.assertEqual(self.hf_sweep.events, [])

    def test_refusal_reasons_scroll_without_clipping_and_do_not_apply_or_start(self) -> None:
        self.fixture.shell.select_appearance_locale(UiLocale.RU)
        self.fixture.shell.setFixedSize(960, 540)
        self.prepare_long_preview(refused=True)
        for _ in range(4):
            self.app.processEvents()
        self.assertEqual(self.editor.preview.text(), "")
        self.assertEqual(self.editor.preview.accessibleName(), "")
        self.assertEqual(self.editor.impact_summary.text(), self.editor.error.text().splitlines()[0])
        self.assertTrue(self.editor.details.isEnabled())
        self.editor.details.click()
        scroll = self.editor.scroll_area
        for _ in range(20):
            QTest.keyClick(scroll, Qt.Key.Key_PageDown)
            self.app.processEvents()
            if scroll.verticalScrollBar().value() == scroll.verticalScrollBar().maximum():
                break
        self.assertGreaterEqual(self.editor.error.height(),
                                self.editor.error.heightForWidth(self.editor.error.width()))
        last_reason = self.editor.error.mapTo(scroll.viewport(),
                                            QPoint(1, self.editor.error.height() - 1))
        self.assertGreaterEqual(last_reason.y(), 0)
        self.assertLess(last_reason.y(), scroll.viewport().height())
        self.assertIn("4.080", self.editor.error.text())
        self.assertIn("2.060", self.editor.error.text())
        self.assertTrue(self.editor.prepare.isEnabled())
        self.assertFalse(self.editor.apply.isEnabled())
        self.assertEqual(self.pools[0].staged_resource_ids, ())
        self.assertEqual(self.native.engines, [])
        self.assertEqual(self.ad_sweep.events, [])
        self.assertEqual(self.hf_sweep.events, [])


if __name__ == "__main__":
    unittest.main()
