"""Actual V2 Qt dialogs/pump/owners; fake SDK, no physical RF claim."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.pane_scheduler import PaneControlGapReason
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale, text
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.workspaces.rf_shift_dialog import RfImpactDialog

from tests.ui_v2 import test_app07_independent_failures as fixtures
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app07_rf_gesture import middle_move, plot_points


class RfShiftUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.harness = fixtures.IndependentPaneFailureTests()
        self.harness.app = self.app

    def wait(self, predicate, timeout=5):
        until = monotonic() + timeout
        while monotonic() < until:
            self.app.processEvents()
            if predicate():
                return
            sleep(.005)
        self.fail("bounded RF UI observation timed out")

    def start_and_publish(self, product):
        product.ui.start_all.click()
        self.wait(lambda: all(item.phase is PanePumpPhase.RUNNING for item in product.handle.pump.snapshot()))
        self.harness.publish_ad(product, 1)
        self.wait(lambda: product.ui.rf_shift.isEnabled())

    def preview(self, product, offset=10e6):
        ui = product.ui
        anchor = ui.board.rf_anchor(1)
        ui._request_rf_shift(1, offset, anchor)
        self.wait(lambda: isinstance(ui._rf_dialog, RfImpactDialog))
        return ui._rf_dialog

    def state(self, product, resource="pane-resource-1"):
        return next(item for item in product.handle.pump.snapshot()
                    if item.physical_stream_resource_id == resource)

    def test_actual_middle_input_opens_inert_default_cancel_preview(self):
        with self.harness.product(ad_rate=61.44e6) as product:
            self.start_and_publish(product)
            old_layout = product.handle.layout
            old_activation = self.state(product).activation
            count = len(product.native.engines)
            graphics, first, last = plot_points(product.ui.board.pane(1).spectrum_scene, -50)
            QTest.mousePress(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=first)
            middle_move(graphics, last)
            self.assertIsNone(product.ui._rf_phase)
            QTest.mouseRelease(graphics.viewport(), Qt.MouseButton.MiddleButton, pos=last)
            self.wait(lambda: isinstance(product.ui._rf_dialog, RfImpactDialog))
            dialog = product.ui._rf_dialog
            self.assertTrue(dialog.cancel_button.isDefault())
            self.assertFalse(dialog.confirm_button.autoDefault())
            self.assertIs(product.handle.layout, old_layout)
            self.assertEqual(len(product.native.engines), count)
            self.assertEqual(self.state(product).activation, old_activation)
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
            self.wait(lambda: product.ui._rf_phase is None)
            self.assertIs(product.handle.layout, old_layout)
            self.assertEqual(self.state(product).activation, old_activation)

    def test_approved_live_change_installs_caption_before_new_start_and_keeps_peers(self):
        with self.harness.product(ad_rate=61.44e6) as product:
            self.start_and_publish(product)
            old = self.state(product).activation
            peers = tuple(item.activation for item in product.handle.pump.snapshot()[1:])
            peer_binding = product.handle.preparer.bindings["pane-2"]
            dialog = self.preview(product, 20e6)
            self.assertIn("100–108", dialog.details.toPlainText())
            self.assertIn("120–128", dialog.details.toPlainText())
            self.assertFalse(product.ui.start_all.isEnabled())
            seen_at_start = []
            original = product.handle.pump.start_resource

            def start(resource):
                seen_at_start.append((resource, product.ui.board._captions[1].text(),
                                      product.ui.board.pane(1).last_bundle))
                return original(resource)

            with patch.object(product.handle.pump, "start_resource", side_effect=start):
                dialog.confirm_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
            self.assertEqual(len(seen_at_start), 1)
            self.assertIn("120–128", seen_at_start[0][1])
            self.assertIsNone(seen_at_start[0][2])
            new = self.state(product).activation
            self.assertGreater(new.host_activation_serial, old.host_activation_serial)
            self.assertIs(new.planned_control_gap.reason, PaneControlGapReason.PROFILE_OR_RF_PLAN_CHANGE)
            self.assertEqual(tuple(item.activation for item in product.handle.pump.snapshot()[1:]), peers)
            self.assertIs(product.handle.preparer.bindings["pane-2"], peer_binding)
            self.assertEqual(product.hf.factory.controls[-1].stops, [])
            applied = product.graphs[0].live.current_snapshot().applied.applied
            self.assertEqual((applied.center_hz, applied.sample_rate_hz, applied.fft_size), (124e6, 61.44e6, 4096))

    def test_stopped_apply_arms_without_rx_and_manual_start_is_distinct(self):
        with self.harness.product() as product:
            previous_locale = current_locale()
            self.addCleanup(set_active_locale, previous_locale)
            self.start_and_publish(product)
            product.ui.stop_selected.click()
            self.wait(lambda: self.state(product).phase is PanePumpPhase.STOPPED)
            self.assertIsNotNone(product.ui.board.pane(1).last_bundle)
            for locale in (UiLocale.EN, UiLocale.RU):
                set_active_locale(locale)
                product.ui.set_locale()
                self.assertEqual(product.ui.board._timing_labels[1].text(),
                                 text("analyzer.independent.timing.stopped"))
            count = len(product.native.engines)
            dialog = self.preview(product)
            self.assertFalse(product.ui._rf_preview.resource.restart_required)
            dialog.confirm_button.click()
            self.wait(lambda: product.ui._rf_phase is None)
            self.assertEqual(len(product.native.engines), count)
            self.assertIs(self.state(product).phase, PanePumpPhase.STOPPED)
            self.assertIn("110–118", product.ui.board._captions[1].text())
            self.assertIsNone(product.ui.board.pane(1).last_bundle)
            self.assertEqual(product.ui.board._timing_labels[1].text(),
                             text("analyzer.independent.timing.stopped_empty"))
            for locale in (UiLocale.EN, UiLocale.RU):
                set_active_locale(locale)
                product.ui.set_locale()
                self.assertEqual(product.ui.board._timing_labels[1].text(),
                                 text("analyzer.independent.timing.stopped_empty"))
            product.ui.start_selected.click()
            self.wait(lambda: self.state(product).phase is PanePumpPhase.RUNNING)
            self.assertEqual(len(product.native.engines), count + 1)

    def test_changed_source_after_preview_refuses_before_stop(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            dialog = self.preview(product)
            old = self.state(product).activation
            selection = product.graphs[0].live.current_source_selection()
            with patch.object(product.graphs[0].live, "current_source_selection",
                              return_value=replace(selection, revision=selection.revision + 1)):
                dialog.confirm_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
            self.assertEqual(self.state(product).activation, old)
            self.assertIs(self.state(product).phase, PanePumpPhase.RUNNING)
            self.assertTrue(product.ui.error.isVisible())

    def test_recording_race_refuses_approved_rf_change_but_ordinary_stop_still_works(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            dialog = self.preview(product)
            old = self.state(product).activation
            owner = product.handle.session._runtimes["pane-resource-1"].owner
            with patch.object(owner, "recording_active", return_value=True):
                dialog.confirm_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
                self.assertEqual(self.state(product).activation, old)
                product.ui.stop_selected.click()
                self.wait(lambda: self.state(product).phase is PanePumpPhase.STOPPED)

    def test_normal_new_frames_do_not_change_rf_anchor_but_new_epoch_does(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            old = product.ui.board.rf_anchor(1)
            self.harness.publish_ad(product, 2)
            self.assertEqual(product.ui.board.rf_anchor(1), old)
            product.ui.stop_selected.click()
            self.wait(lambda: self.state(product).phase is PanePumpPhase.STOPPED
                      and product.ui.start_selected.isEnabled())
            product.ui.start_selected.click()
            self.wait(lambda: self.state(product).phase is PanePumpPhase.RUNNING)
            self.harness.publish_ad(product, 3)
            self.assertNotEqual(product.ui.board.rf_anchor(1), old)

    def test_preview_details_scroll_in_bounded_small_dialog_without_hiding_cancel(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            dialog = self.preview(product)
            dialog.resize(500, 300)
            self.app.processEvents()
            self.assertIsNotNone(dialog.impact_summary)
            self.assertTrue(dialog.impact_summary.isVisible())
            self.assertTrue(dialog.rect().contains(dialog.impact_summary.mapTo(
                dialog, dialog.impact_summary.rect().center())))
            self.assertGreater(dialog.details.verticalScrollBar().maximum(), 0)
            self.assertTrue(dialog.rect().contains(dialog.cancel_button.mapTo(dialog, dialog.cancel_button.rect().center())))
            self.assertTrue(dialog.cancel_button.isVisible())
            dialog.cancel_button.click()
            self.wait(lambda: product.ui._rf_phase is None)

    def test_stop_all_during_apply_observes_receipt_and_does_not_restart(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            dialog = self.preview(product)
            count = len(product.native.engines)
            entered, release = Event(), Event()
            original = product.handle.session.replace_stopped_resource_plan

            def held(preview):
                entered.set()
                if not release.wait(4):
                    raise RuntimeError("test Apply release timed out")
                return original(preview)

            with patch.object(product.handle.session, "replace_stopped_resource_plan", side_effect=held):
                dialog.confirm_button.click()
                self.wait(entered.is_set)
                # Qt input remains live while the same resource worker is busy.
                product.ui.stop_all.click()
                self.assertTrue(product.ui._rf_cancelled)
                self.assertFalse(product.handle.can_close())
                release.set()
                self.wait(lambda: product.ui._rf_phase is None
                          and all(item.phase is PanePumpPhase.STOPPED for item in product.handle.pump.snapshot()))
            self.assertEqual(len(product.native.engines), count)
            self.assertIn("110–118", product.ui.board._captions[1].text())
            self.assertTrue(product.handle.can_close())

    def test_gui_receipt_failure_bars_target_start_but_stop_close_are_available(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            dialog = self.preview(product)
            count = len(product.native.engines)
            with patch.object(product.ui.board, "refresh_resource_plan", side_effect=RuntimeError("PRIVATE")):
                dialog.confirm_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
            self.assertEqual(product.ui._rf_fault_resource, "pane-resource-1")
            self.assertFalse(product.ui.start_selected.isEnabled())
            self.assertEqual(len(product.native.engines), count)
            self.assertNotIn("PRIVATE", product.ui.error.text())
            product.ui.stop_all.click()
            self.wait(product.handle.can_close)

    def test_infeasible_shift_refuses_without_stop_or_clamping(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            old = self.state(product).activation
            old_layout = product.handle.layout
            product.ui._request_rf_shift(1, -200e6, product.ui.board.rf_anchor(1))
            self.wait(lambda: product.ui._rf_phase is None)
            self.assertIsNone(product.ui._rf_dialog)
            self.assertEqual(self.state(product).activation, old)
            self.assertIs(product.handle.layout, old_layout)

    def test_numeric_entry_reuses_preview_and_cancel_keeps_rx(self):
        with self.harness.product() as product:
            self.start_and_publish(product)
            old = self.state(product).activation
            product.ui.rf_shift.click()
            self.assertIsNotNone(product.ui._rf_dialog)
            product.ui._rf_dialog.offset.setValue(5)
            product.ui._rf_dialog.accept()
            self.wait(lambda: isinstance(product.ui._rf_dialog, RfImpactDialog))
            self.assertEqual(product.ui._rf_preview.proposal.effective_shift_hz, 5e6)
            product.ui._rf_dialog.cancel_button.click()
            self.wait(lambda: product.ui._rf_phase is None)
            self.assertEqual(self.state(product).activation, old)

    @contextmanager
    def shared_product(self):
        native, ad = _ad_graph()
        hf = hackrf_fixture()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        choice = ad.live.discover(startup=True)[0]
        pool = PaneProductGraphPool(lambda key: ad if key == "pane-resource-1" else hf_graph)
        staged = prepare_user_pane_session((
            PaneSlotDraft(1, choice.device_id, 100e6, 108e6, priority=1),
            PaneSlotDraft(2, choice.device_id, 100e6, 108e6, priority=3),
            PaneSlotDraft(3, "source-hackrf", 140e6, 148e6), PaneSlotDraft(4)), pool_factory=lambda: pool)
        apply_user_pane_session(staged)
        temporary = TemporaryDirectory()
        settings = QSettings(str(Path(temporary.name) / "shared.ini"), QSettings.Format.IniFormat)
        with patch("sdr_monitor.ui.v2.workspaces.independent_pane_board.QSettings", return_value=settings):
            ui = IndependentPaneSessionV2(staged.handle)
        ui.resize(1400, 850)
        ui.show()
        try:
            yield SimpleNamespace(native=native, hf=hf, handle=staged.handle, ui=ui,
                                  choices=(choice,), graphs=(ad, hf_graph))
        finally:
            ui._cancel_rf_change()
            for future in staged.handle.pump.stop_all().values():
                future.result(timeout=4)
            self.wait(lambda: ui._rf_phase is None)
            staged.handle.shutdown_after_stop()
            ui.release_presentation_after_shutdown()
            ui.close()
            ui.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            ad.live.shutdown()
            hf_graph.live.shutdown()
            temporary.cleanup()

    def test_shared_to_sliced_impact_updates_both_histories_maps_and_preserves_weights(self):
        with self.shared_product() as product:
            self.start_and_publish(product)
            self.wait(lambda: product.ui.board.pane(2).last_bundle is not None)
            peer = self.state(product, "pane-resource-2").activation
            dialog = self.preview(product, 20e6)
            self.assertEqual(product.ui._rf_preview.resource.affected_pane_ids, ("pane-1", "pane-2"))
            self.assertIn("1, 2", dialog.details.toPlainText())
            dialog.confirm_button.click()
            self.wait(lambda: product.ui._rf_phase is None)
            self.assertIn("120–128", product.ui.board._captions[1].text())
            self.assertIn("100–108", product.ui.board._captions[2].text())
            self.assertIsNone(product.ui.board.pane(2).last_bundle)
            self.assertEqual(product.ui.board._time_sliced_pane_ids, {"pane-1", "pane-2"})
            self.assertIn("pane-resource-1", product.ui._time_sliced_resources)
            self.assertEqual(tuple(draft.priority for draft in product.handle.rf_context.drafts[:2]), (1, 3))
            self.assertEqual(self.state(product, "pane-resource-2").activation, peer)

    def test_shared_to_sliced_impact_summary_is_distinct_and_localized(self):
        previous_locale = current_locale()
        self.addCleanup(set_active_locale, previous_locale)
        with self.shared_product() as product:
            self.start_and_publish(product)
            for locale, expected in (
                (UiLocale.EN, ("Only pane 1: 100.000000–108.000000 → 120.000000–128.000000 MHz",
                               "Spectrum and Waterfall histories in panes 1, 2",
                               "One physical RX: shared capture, 1 job for multiple panes → time-sliced, 2 capture jobs",
                               "Independent receivers and their panes stay unchanged",
                               "Approval: Stop this RX → Apply → new Start")),
                (UiLocale.RU, ("Только окно 1: 100.000000–108.000000 → 120.000000–128.000000 МГц",
                               "история спектра и водопада будет очищена в окнах 1, 2",
                               "Один физический RX: общий захват, 1 задание для нескольких окон → чередование во времени, заданий 2",
                               "Независимые RX и их окна не меняются",
                               "Подтверждение: Стоп этого RX → Применить → новый Старт")),
            ):
                set_active_locale(locale)
                dialog = self.preview(product, 20e6)
                summary = dialog.impact_summary
                self.assertIsNotNone(summary)
                self.assertTrue(summary.isVisible())
                self.assertEqual(summary.objectName(), "rfImpactSummaryV2")
                for phrase in expected:
                    self.assertIn(phrase, summary.text())
                self.assertNotIn("pane-1", summary.text())
                detail = dialog.details.toPlainText()
                self.assertIn("Weight:" if locale is UiLocale.EN else "Вес:", detail)
                self.assertIn(text("analyzer.rf.pane_range", number=1, old_start="100", old_stop="108",
                                   start="120", stop="128"), detail)
                self.assertIn(text("analyzer.rf.pane_range", number=2, old_start="100", old_stop="108",
                                   start="100", stop="108"), detail)
                self.assertIn("modeled max revisit" if locale is UiLocale.EN else "расчётный макс. revisit",
                              detail)
                self.assertIn("sample_rate_hz: 20000000.0", detail)
                self.assertIn("fft_size: 4096", detail)
                self.assertTrue(dialog.cancel_button.isDefault())
                self.assertFalse(dialog.confirm_button.autoDefault())
                dialog.cancel_button.click()
                self.wait(lambda: product.ui._rf_phase is None)

    def test_negative_one_hz_dedicated_summary_and_running_stopped_actions_in_both_locales(self):
        previous_locale = current_locale()
        self.addCleanup(set_active_locale, previous_locale)
        for locale, target, topology, running, stopped in (
            (UiLocale.EN, "Only pane 1: 100.000000–108.000000 → 99.999999–107.999999 MHz",
             "One physical RX: dedicated single-pane capture, 1 job → dedicated single-pane capture, 1 job",
             "Approval: Stop this RX → Apply → new Start",
             "Approval: Apply only; the next Start is separate"),
            (UiLocale.RU, "Только окно 1: 100.000000–108.000000 → 99.999999–107.999999 МГц",
             "Один физический RX: отдельный захват одного окна, 1 задание → отдельный захват одного окна, 1 задание",
             "Подтверждение: Стоп этого RX → Применить → новый Старт",
             "Подтверждение: только Применить; следующий Старт — отдельно"),
        ):
            with self.subTest(locale=locale), self.harness.product() as product:
                set_active_locale(locale)
                self.start_and_publish(product)
                old_activation = self.state(product).activation
                running_dialog = self.preview(product, -1.0)
                summary = running_dialog.impact_summary.text()
                self.assertIn(target, summary)
                self.assertIn(topology, summary)
                self.assertIn(running, summary)
                self.assertIn("1.", summary)
                self.assertNotIn("pane-1", summary)
                self.assertIn("-0.000001", running_dialog.details.toPlainText())
                running_dialog.cancel_button.click()
                self.wait(lambda: product.ui._rf_phase is None)
                self.assertEqual(self.state(product).activation, old_activation)
                product.ui.stop_selected.click()
                self.wait(lambda: self.state(product).phase is PanePumpPhase.STOPPED)
                stopped_dialog = self.preview(product, -1.0)
                self.assertIn(stopped, stopped_dialog.impact_summary.text())
                self.assertEqual(stopped_dialog.confirm_button.text(), text("analyzer.rf.confirm_apply"))
                self.assertTrue(stopped_dialog.cancel_button.isDefault())
                stopped_dialog.cancel_button.click()
                self.wait(lambda: product.ui._rf_phase is None)


if __name__ == "__main__":
    unittest.main()
