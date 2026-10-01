"""Per-pane tinySA intent through the real common owner; fake serial only."""

from __future__ import annotations

import os
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.domain.tinysa_settings import TinySaInputMode, TinySaRbwMode, TinySaSweepSettingsPlan
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError, TinySaPaneIntent, compile_user_pane_plan
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session

from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app06_tinysa_runtime_settings import settings_graph, writes
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as unknown_graph
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


def _v2(g):
    return build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument))


def _rbw(value=300_000):
    return TinySaPaneIntent(settings=TinySaSweepSettingsPlan(
        rbw_mode=TinySaRbwMode.MANUAL, rbw_hz=value), input_mode=TinySaInputMode.LOW)


class TinySaPanePlanTests(unittest.TestCase):
    def setUp(self):
        self.g = settings_graph()
        self.v2 = _v2(self.g)
        candidate = self.v2.live.discover(startup=True)[0]
        self.v2.live.select_device(candidate.device_id)
        self.selection = self.v2.live.current_source_selection()
        self.source = self.selection.selected
        self.addCleanup(self.v2.live.shutdown)

    def compile(self, intents):
        return compile_user_pane_plan(tuple(PaneSlotDraft(i, self.source.device_id,
            100e6, 300e6, points=1001, tinysa=intent) for i, intent in enumerate(intents, 1)),
            {self.source.device_id: self.source}, {self.source.device_id: self.selection.revision})

    def test_exact_200mhz_1001point_300k_intent_forces_same_ui_readback(self):
        intent = _rbw()
        plan = self.compile((intent,))
        request = plan.layout.schedule.resources[0].jobs[0].profile.request_template
        self.assertEqual((request.start_hz, request.stop_hz, request.points), (100e6, 300e6, 1001))
        self.assertIs(request.settings, intent.settings)
        self.assertIs(request.source, self.source)
        self.assertEqual(request.selection_revision, self.selection.revision)
        self.assertTrue(request.readback_settings)
        self.assertTrue(request.repeat_until_stop)
        self.assertIs(request.input_mode, TinySaInputMode.LOW)
        self.assertEqual(request.settings.commands, ("rbw 300",))
        self.assertEqual(self.g.serials, [])
        preserved = self.compile((None,)).layout.schedule.resources[0].jobs[0].profile.request_template
        self.assertFalse(preserved.readback_settings)
        self.assertEqual(preserved.settings.commands, ())

    def test_different_rbw_is_one_serial_resource_time_sliced_not_hidden_shared_merge(self):
        sliced = self.compile((_rbw(300_000), _rbw(10_000)))
        self.assertEqual(len(sliced.groups), 1)
        resource = sliced.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 2)
        self.assertTrue(all(job.mode is ReceiverBindingMode.TIME_SLICED for job in resource.jobs))
        self.assertEqual({job.profile.request_template.settings.rbw_hz for job in resource.jobs}, {300_000, 10_000})
        shared = self.compile((_rbw(), _rbw()))
        self.assertEqual(len(shared.layout.schedule.resources[0].jobs), 1)
        self.assertIs(shared.layout.schedule.resources[0].jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual(self.g.serials, [])

    def test_empty_sdr_and_wrong_typed_intent_refuse_without_io(self):
        for intent in ({}, True, "rbw 300"):
            with self.subTest(intent=intent), self.assertRaises(PaneUserPlanError):
                PaneSlotDraft(1, self.source.device_id, 100e6, 300e6, tinysa=intent)
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, tinysa=_rbw())
        for intent in ({"settings": {}}, {"input_mode": "low"}, {"readback": 1}):
            with self.subTest(intent=intent), self.assertRaises(PaneUserPlanError):
                TinySaPaneIntent(**intent)
        _, ad = _ad_graph(serial="")
        self.addCleanup(ad.live.shutdown)
        source = ad.live.discover(startup=True)[0]
        ad.live.select_device(source.device_id)
        selected = ad.live.current_source_selection()
        with self.assertRaisesRegex(PaneUserPlanError, "SDR pane"):
            compile_user_pane_plan((PaneSlotDraft(1, source.device_id, 100e6, 108e6, tinysa=_rbw()),),
                {source.device_id: selected.selected}, {source.device_id: selected.revision})
        self.assertEqual(self.g.serials, [])

    def test_observed_unknown_firmware_refuses_requested_settings_before_serial(self):
        g = unknown_graph()
        v2 = _v2(g)
        self.addCleanup(v2.live.shutdown)
        source = v2.live.discover(startup=True)[0]
        v2.live.select_device(source.device_id)
        selection = v2.live.current_source_selection()
        args = ({source.device_id: selection.selected}, {source.device_id: selection.revision})
        with self.assertRaises(ValueError):
            compile_user_pane_plan((PaneSlotDraft(1, source.device_id, 100e6, 300e6,
                                                   points=1001, tinysa=_rbw()),), *args)
        preserved = compile_user_pane_plan((PaneSlotDraft(1, source.device_id, 100e6, 300e6),), *args)
        self.assertFalse(preserved.layout.schedule.resources[0].jobs[0].profile.request_template.readback_settings)
        self.assertEqual(g.serials, [])

    def test_time_sliced_common_owner_applies_each_request_without_cross_pane_settings(self):
        pool = PaneProductGraphPool(lambda _resource: self.v2)
        prepared = prepare_user_pane_session(tuple(PaneSlotDraft(i, self.source.device_id,
            100e6, 300e6, points=1001, tinysa=_rbw(rbw))
            for i, rbw in ((1, 300_000), (2, 10_000))), pool_factory=lambda: pool)
        session = prepared.handle.session
        try:
            self.assertEqual(self.g.serials, [])
            prepared.handle.apply()
            for action in (session.start_resource, session.advance_resource):
                action("pane-resource-1")
                deadline = monotonic() + 4
                while self.g.instrument.poll_latest().line is None:
                    if monotonic() > deadline:
                        self.fail("same-owner tinySA fake pass did not finish")
                    sleep(.001)
                line = self.g.instrument.poll_latest().line
                deliveries = session.poll_resource("pane-resource-1")
                self.assertEqual(len(deliveries), 1)
                expected = 300_000 if deliveries[0].pane_id == "pane-1" else 10_000
                self.assertEqual(line.instrument.settings.plan.rbw_hz, expected)
                self.assertEqual(line.instrument.settings.actual_rbw_hz, 30_000)
            self.assertEqual(len(self.g.serials), 2)
            self.assertFalse(self.g.serials[0].is_open)
            self.assertEqual({next(command for command in writes(serial)
                                  if command in (b"rbw 300\r", b"rbw 10\r"))
                              for serial in self.g.serials}, {b"rbw 300\r", b"rbw 10\r"})
        finally:
            self.assertEqual(session.stop_all(), ())
            for future in prepared.handle.pump.stop_all().values():
                future.result(timeout=5)
            prepared.handle.shutdown_after_stop()
        self.assertFalse(self.g.serials[1].is_open)


class TinySaPaneSettingsActualRootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.locale = current_locale()
        self.fixture = product.AnalyzerWorkspaceProductTests("runTest")
        self.fixture.app = self.app
        self.fixture.setUp()
        self.editor = self.fixture.page._independent_setup
        self.g = settings_graph()
        self.v2 = _v2(self.g)
        self.candidate = self.v2.live.discover(startup=True)[0]
        self.selection = AnalyzerSourceSelection(revision=1, choices=(self.candidate,))
        self.fixture.page._toggle_independent_setup()
        self.fixture.presenter.source_selection_changed.emit(self.selection)
        self.row = self.editor._rows[2]
        self.row.source.setCurrentIndex(self.row.source.findData(self.candidate.device_id))
        self.row.start.setValue(100)
        self.row.stop.setValue(300)
        self.row.points.setValue(1001)
        self.pane_ui = None
        self.handle = None

    def wait(self, predicate):
        deadline = monotonic() + 7
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(.002)
        self.fail("per-pane tinySA actual-root operation did not finish")

    def stage(self):
        def prepare(drafts):
            pool = PaneProductGraphPool(lambda _resource: self.v2)
            return prepare_user_pane_session(drafts, pool_factory=lambda: pool)
        with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                   side_effect=prepare):
            self.editor.prepare.click()
            self.wait(lambda: self.editor._future is None)
        self.assertIsNotNone(self.editor._prepared, self.editor.error.text())

    def change_rbw(self):
        drawer = self.row.tinysa_settings
        drawer.input.setCurrentIndex(drawer.input.findData("low"))
        drawer.rbw_mode.setCurrentIndex(drawer.rbw_mode.findData("manual"))
        drawer.rbw.setValue(300)
        return drawer

    def tearDown(self):
        try:
            if self.handle is not None:
                for future in self.handle.pump.stop_all().values():
                    future.result(timeout=5)
                self.handle.shutdown_after_stop()
                if self.pane_ui is not None:
                    self.pane_ui.release_presentation_after_shutdown()
                    self.pane_ui.close()
            if self.editor._prepared is not None or self.editor._retained_pool is not None:
                self.editor.discard.click()
                self.wait(lambda: self.editor.can_close)
            self.fixture.tearDown()
        finally:
            self.v2.live.shutdown()
            self.fixture.doCleanups()
            set_active_locale(self.locale)

    def test_unobserved_candidate_is_intent_only_and_staged_locale_discard_keeps_draft(self):
        drawer = self.change_rbw()
        self.assertIn(text("tinysa.settings.unobserved_draft"), drawer.contract.text())
        self.assertTrue(drawer.rbw.isEnabled())
        self.assertTrue(self.editor._read_drafts()[2].tinysa.readback_settings)
        self.assertEqual(self.g.serials, [])
        self.stage()
        prepared = self.editor._prepared
        self.assertIn("rbw 300", self.editor.preview.text())
        self.assertIn(text("tinysa.settings.known"), drawer.contract.text())
        self.assertIn("1001", self.editor.preview.text())
        for locale in (UiLocale.EN, UiLocale.RU):
            self.fixture.shell.select_appearance_locale(locale)
            self.assertIs(self.editor._prepared, prepared)
            self.assertIs(self.editor._selection, self.selection)
            self.assertFalse(drawer.rbw.isEnabled())
            self.assertEqual(drawer.plan().rbw_hz, 300_000)
            self.assertEqual(drawer.input.currentData(), "low")
            self.assertIn(text("analyzer.pane.setup.preview_tinysa_scope"), self.editor.preview.text())
        self.editor.discard.click()
        self.wait(lambda: self.editor.can_close)
        self.assertTrue(drawer.rbw.isEnabled())
        self.assertEqual(drawer.plan().rbw_hz, 300_000)
        self.assertEqual(drawer.input.currentData(), "low")
        self.assertEqual(self.g.serials, [])

    def test_two_pane_drawers_keep_independent_values_and_bounds(self):
        first = self.change_rbw()
        peer = self.editor._rows[0]
        peer.source.setCurrentIndex(peer.source.findData(self.candidate.device_id))
        second = peer.tinysa_settings
        second.rbw_mode.setCurrentIndex(second.rbw_mode.findData("manual"))
        second.rbw.setValue(10)
        self.editor.tinysa_toggle.click()
        for number, drawer, rbw in ((1, second, 10_000), (3, first, 300_000)):
            self.editor.tinysa_pane.setCurrentIndex(self.editor.tinysa_pane.findData(number))
            self.app.processEvents()
            self.assertIs(self.editor.tinysa_stack.currentWidget(), drawer)
            self.assertEqual(drawer.plan().rbw_hz, rbw)
        self.assertEqual(self.editor._read_drafts()[0].tinysa.settings.rbw_hz, 10_000)
        self.assertEqual(self.editor._read_drafts()[2].tinysa.settings.rbw_hz, 300_000)
        self.assertEqual(self.row.points.maximum(), 10001)
        peer.source.setCurrentIndex(0)
        self.assertIsNone(self.editor._read_drafts()[0].tinysa)
        self.assertEqual(first.plan().rbw_hz, 300_000)
        self.assertEqual(self.g.serials, [])

    def test_bounded_drawer_scroll_and_pinned_commands_in_actual_root(self):
        self.change_rbw()
        self.editor.tinysa_toggle.click()
        for locale in (UiLocale.RU, UiLocale.EN):
            self.fixture.shell.select_appearance_locale(locale)
            for width, height in ((1440, 912), (1280, 720), (960, 540)):
                self.fixture.shell.setFixedSize(width, height)
                for _ in range(4):
                    self.app.processEvents()
                self.assertLessEqual(self.editor.height(), 480)
                for button in (self.editor.prepare, self.editor.apply, self.editor.discard):
                    self.assertTrue(self.fixture.shell.rect().contains(
                        button.mapTo(self.fixture.shell, button.rect().bottomRight())))
                self.editor.scroll_area.ensureWidgetVisible(self.editor.tinysa_stack)
                self.app.processEvents()
                drawer = self.row.tinysa_settings
                drawer.scroll_area.verticalScrollBar().setValue(0)
                self.assertGreaterEqual(drawer.rbw.height(), drawer.rbw.minimumSizeHint().height())
                self.assertTrue(drawer.input.isEnabled())
                drawer.scroll_area.verticalScrollBar().setValue(drawer.scroll_area.verticalScrollBar().maximum())
                point = drawer.scope.mapTo(drawer.scroll_area.viewport(), QPoint(1, drawer.scope.height() - 1))
                self.assertGreaterEqual(point.y(), 0)
                self.assertLess(point.y(), drawer.scroll_area.viewport().height())
        self.assertEqual(self.g.serials, [])

    def test_same_common_owner_postpass_readout_distinguishes_300k_target_from_30k_fake_actual(self):
        self.change_rbw()
        self.stage()
        prepared = self.editor._prepared
        self.assertIn(text("tinysa.settings.known"), self.row.tinysa_settings.contract.text())
        self.editor.apply.click()
        self.wait(lambda: self.fixture.page._independent_session is not None)
        self.pane_ui = self.fixture.page._independent_session
        self.handle = prepared.handle
        self.assertEqual(self.g.serials, [])  # Stage + Apply are NOT Start.
        self.pane_ui.start_all.click()
        self.wait(lambda: self.pane_ui.board.pane(3).last_bundle is not None)
        frame = self.pane_ui.board.pane(3).last_bundle.spectrum
        self.assertEqual(frame.instrument.settings.plan.rbw_hz, 300_000)
        self.assertEqual(frame.instrument.settings.actual_rbw_hz, 30_000)
        self.pane_ui._refresh()
        label = self.pane_ui.board._timing_labels[3]
        self.assertIn(text("analyzer.independent.tinysa.rbw_actual", value="30"), label.text())
        self.assertIn(text("analyzer.pane.setup.preview_tinysa_scope"), label.toolTip())
        self.assertEqual(label.toolTip(), label.accessibleDescription())
        commands = writes(self.g.serials[0])
        self.assertLess(commands.index(b"mode low input\r"), commands.index(b"rbw 300\r"))
        scan = next(command for command in commands if command.startswith(b"scanraw "))
        self.assertIn(b"100000000 300000000 1001", scan)
        self.assertLess(commands.index(scan), commands.index(b"rbw ?\r"))
        self.assertEqual(len(prepared.plan.groups), 1)


if __name__ == "__main__":
    unittest.main()
