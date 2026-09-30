"""Real V2 scheduler controls and common staged owners; fake SDK, no RF proof."""

from __future__ import annotations

import os
from time import monotonic, sleep
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.services.pane_resource_session import PaneHostTiming
from sdr_monitor.ui.v2.design import ThemeId
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class PaneSchedulerControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.locale = current_locale()
        self.native, self.graph = _ad_graph(serial="")
        self.choice = self.graph.live.discover(startup=True)[0]
        self.pools = []
        self.installed = []
        self.pane_ui = None
        self.fail_stage_cleanup = False
        self.editor = IndependentPaneSetupV2(install=self.installed.append, uninstall=lambda: None)
        self.editor.update_sources(AnalyzerSourceSelection(revision=1, choices=(self.choice,)))
        self.editor.resize(1280, 850)
        self.editor.show()
        self.app.processEvents()

    def _wait(self, predicate) -> None:
        deadline = monotonic() + 8
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(0.005)
        self.fail("scheduler editor did not reach a bounded terminal state")

    def _assign(self, number: int, start: float, stop: float, *, priority=1, target=0):
        row = self.editor._rows[number - 1]
        row.source.setCurrentIndex(row.source.findData(self.choice.device_id))
        row.start.setValue(start)
        row.stop.setValue(stop)
        row.priority.setValue(priority)
        row.maximum_revisit.setValue(target)
        return row

    def _stage(self, drafts):
        pool = PaneProductGraphPool(lambda _resource: self.graph)
        self.pools.append(pool)
        if self.fail_stage_cleanup:
            with patch.object(pool, "close", side_effect=RuntimeError("PRIVATE SDK uri and payload")):
                return prepare_user_pane_session(drafts, pool_factory=lambda: pool)
        return prepare_user_pane_session(drafts, pool_factory=lambda: pool)

    def _prepare(self) -> None:
        with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                   side_effect=self._stage):
            self.editor.prepare.click()
            self._wait(lambda: self.editor._future is None)

    def tearDown(self) -> None:
        try:
            self._wait(lambda: self.editor._future is None)
            for handle in self.installed:
                if not handle.shutdown_complete:
                    for future in handle.pump.stop_all().values():
                        future.result(timeout=5)
                    handle.shutdown_after_stop()
            if self.pane_ui is not None:
                self.pane_ui.release_presentation_after_shutdown()
                self.pane_ui.close()
            if self.editor._prepared is not None or self.editor._retained_pool is not None:
                self.editor.discard.click()
                self._wait(lambda: self.editor.can_close)
            self.editor.release_after_shutdown()
            self.editor.close()
        finally:
            self.graph.live.shutdown()
            set_active_locale(self.locale)
            self.app.processEvents()

    def test_collapsed_controls_empty_disable_and_locales_preserve_user_intent(self) -> None:
        self.assertTrue(self.editor.scheduler_controls.isHidden())
        self.assertFalse(self.editor._rows[0].priority.isEnabled())
        row = self._assign(1, 100, 108, priority=7, target=0.2)
        self.editor.scheduler_toggle.click()
        self.app.processEvents()
        self.assertFalse(self.editor.scheduler_controls.isHidden())
        for locale in (UiLocale.EN, UiLocale.RU):
            set_active_locale(locale)
            self.editor.set_locale()
            draft = self.editor._read_drafts()[0]
            self.assertEqual((draft.priority, draft.maximum_revisit_s), (7, 0.2))
            self.assertEqual((row.start.value(), row.stop.value()), (100, 108))
            self.assertEqual(row.priority.accessibleName(), text("analyzer.pane.setup.priority_name", pane=1))
            self.assertEqual(row.maximum_revisit.accessibleName(), text("analyzer.pane.setup.target_name", pane=1))
            self.assertTrue(row.priority.isEnabled())
            self.assertFalse(self.editor._rows[1].maximum_revisit.isEnabled())
        self.editor.scheduler_toggle.click()
        self.assertTrue(self.editor.scheduler_controls.isHidden())
        self.assertEqual(self.editor._read_drafts()[0].priority, 7)
        self.assertEqual(self.native.engines, [])

    def test_failed_deadlines_explain_all_panes_before_apply_and_no_rx(self) -> None:
        self._assign(1, 100, 108, target=0.01)
        self._assign(2, 200, 208, target=0.01)
        self._prepare()
        self.assertIsNone(self.editor._prepared)
        self.assertTrue(self.editor.prepare.isEnabled())
        self.assertFalse(self.editor.apply.isEnabled())
        self.assertEqual(self.native.engines, [])
        self.assertEqual(self.installed, [])
        self.assertEqual(self.pools[0].staged_resource_ids, ())
        for locale in (UiLocale.RU, UiLocale.EN):
            set_active_locale(locale)
            self.editor.set_locale()
            detail = self.editor.error.text()
            self.assertIn(text("analyzer.pane.setup.deadline_refused"), detail)
            for number in (1, 2):
                self.assertIn(text("analyzer.pane.setup.deadline_detail",
                    pane=number, modeled="0.160", target="0.01"), detail)
            self.assertEqual(self.editor.error.accessibleName(), detail)
            self.assertEqual(self.editor.error.toolTip(), detail)
            self.assertNotIn("PRIVATE", detail)
            self.assertNotIn(self.choice.device_id, detail)
        self.assertTrue(self.editor.can_close)

    def test_all_themes_use_common_numeric_roles_without_spreading_mode_rows(self) -> None:
        row = self._assign(1, 100, 108, priority=7, target=0.2)
        self.editor.scheduler_toggle.click()
        for theme in ThemeId:
            self.editor.set_theme(theme)
            self.app.processEvents()
            for field in (row.start, row.stop, row.points, row.priority, row.maximum_revisit):
                self.assertEqual(field.property("ui2Role"), "range-control")
            for label in (row.number_label, row.schedule_number_label):
                self.assertEqual(label.property("ui2Role"), "secondary")
            self.assertLessEqual(row.mode_points.height(), row.source.height() + 4)
            self.assertEqual((row.priority.value(), row.maximum_revisit.value()), (7, 0.2))
            self.assertTrue(row.priority.isEnabled())
            self.assertTrue(row.maximum_revisit.isEnabled())
        self.assertEqual(self.native.engines, [])

    def test_refused_plan_with_failed_cleanup_retains_owner_and_redacted_discard_action(self) -> None:
        self._assign(1, 100, 108, target=0.001)
        self.fail_stage_cleanup = True
        self._prepare()
        self.assertIs(self.editor._retained_pool, self.pools[0])
        self.assertFalse(self.editor.prepare.isEnabled())
        self.assertFalse(self.editor.apply.isEnabled())
        self.assertTrue(self.editor.discard.isEnabled())
        self.assertFalse(self.editor.can_close)
        self.assertFalse(self.editor._rows[0].priority.isEnabled())
        self.assertIn(text("analyzer.pane.setup.deadline_cleanup"), self.editor.error.text())
        self.assertNotIn("PRIVATE", self.editor.error.text())
        self.assertEqual(self.native.engines, [])
        self.editor.discard.click()
        self._wait(lambda: self.editor.can_close)
        self.assertEqual(self.pools[0].staged_resource_ids, ())
        self.assertFalse(self.editor._cleanup_required)
        self.assertNotIn(text("analyzer.pane.setup.deadline_cleanup"), self.editor.error.text())

    def test_shared_preview_exposes_merge_and_locks_both_scheduling_fields(self) -> None:
        self._assign(1, 100, 104, target=0.09)
        self._assign(2, 105, 108, priority=4, target=0.08)
        self._prepare()
        prepared = self.editor._prepared
        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.preview[0].capture_job_count, 1)
        self.assertEqual(self.native.engines, [])
        for row in self.editor._rows[:2]:
            self.assertFalse(row.priority.isEnabled())
            self.assertFalse(row.maximum_revisit.isEnabled())
        for locale in (UiLocale.EN, UiLocale.RU):
            set_active_locale(locale)
            self.editor.set_locale()
            preview = self.editor.preview.text()
            self.assertIn(text("analyzer.pane.setup.preview_scheduler",
                pane=1, priority=1, effective_priority=4,
                target=text("analyzer.pane.setup.target_value", value="0.09"),
                effective_target=text("analyzer.pane.setup.target_value", value="0.08"),
                modeled="0.060", visits=4), preview)
            self.assertIn(text("analyzer.pane.setup.preview_scheduler",
                pane=2, priority=4, effective_priority=4,
                target=text("analyzer.pane.setup.target_value", value="0.08"),
                effective_target=text("analyzer.pane.setup.target_value", value="0.08"),
                modeled="0.060", visits=4), preview)
        self.assertTrue(self.editor.apply.isEnabled())

    def test_default_unset_target_is_not_displayed_as_a_measured_number(self) -> None:
        self._assign(1, 100, 108)
        self._prepare()
        for locale in (UiLocale.EN, UiLocale.RU):
            set_active_locale(locale)
            self.editor.set_locale()
            unset = text("analyzer.pane.setup.target_unset")
            self.assertIn(text("analyzer.pane.setup.preview_scheduler",
                pane=1, priority=1, effective_priority=1, target=unset,
                effective_target=unset, modeled="0.060", visits=1), self.editor.preview.text())
            self.assertNotIn("≤ " + unset, self.editor.preview.text())
        self.assertEqual(self.native.engines, [])

    def test_weighted_apply_first_job_and_honest_observed_target_readout(self) -> None:
        self._assign(1, 100, 108, target=0.4)
        self._assign(2, 200, 208, priority=3, target=0.4)
        self._prepare()
        self.editor.apply.click()
        self._wait(lambda: bool(self.installed))
        handle = self.installed[0]
        self.assertEqual(self.native.engines, [])
        schedule = handle.layout.schedule
        first = schedule.resources[0].slots[0].capture_id
        jobs = {item.capture_id: item for item in schedule.resources[0].jobs}
        self.assertEqual(jobs[first].crops[0].pane_id, "pane-2")
        self.pane_ui = IndependentPaneSessionV2(handle)
        for locale in (UiLocale.EN, UiLocale.RU):
            set_active_locale(locale)
            self.pane_ui.set_locale()
            for observed, key in ((None, "target_unknown"), (0.5, "target_missed"),
                                  (0.3, "target_last_within")):
                with patch.object(handle.session, "pane_host_timing",
                                  return_value=PaneHostTiming(0.1, observed)):
                    value = self.pane_ui._timing_text("pane-1", PanePumpPhase.RUNNING)
                self.assertIn(text("analyzer.independent.timing." + key,
                    target=text("analyzer.independent.timing.seconds", value="0.4")), value)
            stopped = self.pane_ui._timing_text("pane-1", PanePumpPhase.STOPPED)
            self.assertEqual(stopped, text("analyzer.independent.timing.stopped"))
        self.assertEqual(self.native.engines, [])


if __name__ == "__main__":
    unittest.main()
