"""Real Qt editor, fake current owners: explicit mixed-source Stage→Apply."""

from __future__ import annotations

import os
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneHostTiming
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PanePumpResourceState
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture


class IndependentPaneSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, timeout=8.0) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(0.01)
        self.fail("pane editor did not reach the expected state")

    def test_user_three_sources_and_empty_preview_apply_without_hidden_rx(self) -> None:
        native, ad_graph = _ad_graph(serial="")
        hf = hackrf_fixture()
        ts = tinysa_fixture()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        ts_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        graphs = (ad_graph, hf_graph, ts_graph)
        choices = tuple(next(choice for choice in graph.live.discover(startup=True)
                             if choice.family is family)
                        for graph, family in zip(graphs,
                            (DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.TINYSA), strict=True))
        pools = []
        pane_ui = None

        def stage(drafts):
            def pool_factory():
                pool = PaneProductGraphPool(lambda resource: {
                    "pane-resource-1": ad_graph,
                    "pane-resource-2": hf_graph,
                    "pane-resource-3": ts_graph,
                }[resource])
                pools.append(pool)
                return pool
            return prepare_user_pane_session(drafts, pool_factory=pool_factory)

        installed = []
        uninstalled = []
        editor = IndependentPaneSetupV2(install=installed.append,
                                        uninstall=lambda: uninstalled.append(True))
        editor.update_sources(AnalyzerSourceSelection(revision=1, choices=choices))
        editor.resize(1380, 350)
        editor.show()
        self.app.processEvents()
        try:
            for row, choice in zip(editor._rows, choices, strict=False):
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
            self.assertIsNone(editor._rows[3].source.currentData())
            self.assertFalse(editor.blocks_single_source)
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                       side_effect=stage):
                editor.prepare.click()
                self._wait(lambda: editor._prepared is not None)
            self.assertTrue(editor.blocks_single_source)
            self.assertIn("1", editor.preview.text())
            self.assertIn("2", editor.preview.text())
            self.assertIn("3", editor.preview.text())
            self.assertIn("HackRF", editor.preview.text())
            self.assertFalse(installed)
            self.assertEqual(native.engines, [])
            self.assertEqual(hf.factory.controls, [])
            self.assertEqual(ts.serials, [])
            editor.apply.click()
            self._wait(lambda: len(installed) == 1)
            handle = installed[0]
            self.assertTrue(handle.applied)
            self.assertEqual(handle.layout.empty_slots, (4,))
            self.assertEqual(handle.session.retained_resource_count, 3)
            self.assertIn("HackRF", handle.source_labels[choices[1].device_id])
            self.assertEqual(native.engines, [])  # Apply is not Start.
            self.assertEqual(hf.factory.controls, [])
            self.assertEqual(ts.serials, [])
            pane_ui = IndependentPaneSessionV2(handle)
            self.assertIn("остановлен", pane_ui.board._timing_labels[1].text())
            self.assertNotIn(4, pane_ui.board._timing_labels)
            active = tuple(PanePumpResourceState(item.physical_stream_resource_id,
                                                 phase=PanePumpPhase.RUNNING)
                           for item in handle.pump.snapshot())
            with patch.object(handle.pump, "snapshot", return_value=active), \
                 patch.object(handle.session, "pane_host_timing",
                              return_value=PaneHostTiming(1.6, 2.4)):
                pane_ui._refresh()
                self.assertIn("без чередования ресурса", pane_ui.board._timing_labels[1].text())
                self.assertIn("Возраст: 1 с", pane_ui.board._timing_labels[2].text())
                self.assertIn("не частота FFT", pane_ui.board._timing_labels[1].toolTip())
            pane_ui._refresh()
            self.assertIn("остановлен", pane_ui.board._timing_labels[1].text())
            for future in handle.pump.stop_all().values():
                future.result(timeout=5)
            editor.close_applied_layout(handle)
            self._wait(lambda: handle.shutdown_complete and bool(uninstalled))
            pane_ui.release_presentation_after_shutdown()
            pane_ui.close()
            self.assertEqual(pools[0].staged_resource_ids, ())
            self.assertTrue(editor.can_close)
        finally:
            if installed and not installed[0].shutdown_complete:
                for future in installed[0].pump.stop_all().values():
                    future.result(timeout=5)
                installed[0].shutdown_after_stop()
            elif not installed:
                for pool in pools:
                    if pool.staged_resource_ids:
                        pool.close()
            if pane_ui is not None:
                if installed and installed[0].shutdown_complete:
                    pane_ui.release_presentation_after_shutdown()
                    pane_ui.close()
                else:
                    pane_ui._state_timer.stop()
                    pane_ui.delivery.stop()
            editor.release_after_shutdown()
            editor.close()
            for graph in graphs:
                graph.live.shutdown()

    def test_one_rx_two_disjoint_panes_show_distinct_observed_revisit_and_age(self) -> None:
        native, graph = _ad_graph(serial="")
        source_id = graph.live.discover(startup=True)[0].device_id
        prepared = None
        pane_ui = None
        pools = []

        def pool_factory():
            pool = PaneProductGraphPool(lambda _resource: graph)
            pools.append(pool)
            return pool

        try:
            prepared = prepare_user_pane_session((
                PaneSlotDraft(1, source_id, 100e6, 108e6),
                PaneSlotDraft(2, source_id, 200e6, 208e6),
                PaneSlotDraft(3), PaneSlotDraft(4),
            ), pool_factory=pool_factory)
            apply_user_pane_session(prepared)
            handle = prepared.handle
            assert handle.layout.schedule is not None
            self.assertEqual(len(handle.layout.schedule.resources), 1)
            self.assertEqual(len(handle.layout.schedule.resources[0].jobs), 2)
            self.assertEqual(handle.layout.empty_slots, (3, 4))
            pane_ui = IndependentPaneSessionV2(handle)
            self.assertIs(pane_ui._pane_revisits["pane-1"].mode, ReceiverBindingMode.TIME_SLICED)
            self.assertIs(pane_ui._pane_revisits["pane-2"].mode, ReceiverBindingMode.TIME_SLICED)
            self.assertEqual(tuple(pane_ui.board._timing_labels), (1, 2))
            pane_ui.resize(1366, 768)
            pane_ui.show()
            self.app.processEvents()
            self.assertGreater(pane_ui.board.pane(1).height(), 160)
            resource_id = handle.pump.snapshot()[0].physical_stream_resource_id
            observed = {"pane-1": PaneHostTiming(0.3, 2.4),
                        "pane-2": PaneHostTiming(3.2, 2.5)}
            with patch.object(handle.pump, "snapshot", return_value=(
                    PanePumpResourceState(resource_id, phase=PanePumpPhase.RUNNING),)), \
                 patch.object(handle.session, "pane_host_timing",
                              side_effect=lambda pane_id: observed[pane_id]):
                pane_ui._refresh()
                first = pane_ui.board._timing_labels[1].text()
                second = pane_ui.board._timing_labels[2].text()
                self.assertIn("Возраст: <1 с", first)
                self.assertIn("возврат 2.40 с", first)
                self.assertIn("план макс.", first)
                self.assertIn("Возраст: 3 с", second)
                self.assertIn("возврат 2.50 с", second)
                for number, summary in ((1, first), (2, second)):
                    label = pane_ui.board._timing_labels[number]
                    self.assertLessEqual(label.fontMetrics().horizontalAdvance(summary), label.width())
                observed["pane-2"] = PaneHostTiming(None, None)
                pane_ui._refresh()
                self.assertIn("Нет принятого кадра", pane_ui.board._timing_labels[2].text())
                self.assertIn("модель возврата", pane_ui.board._timing_labels[2].text())
                pending = pane_ui.board._timing_labels[2]
                self.assertLessEqual(pending.fontMetrics().horizontalAdvance(pending.text()), pending.width())
                observed["pane-2"] = PaneHostTiming(0.5, None)
                pane_ui._refresh()
                first_visit = pane_ui.board._timing_labels[2]
                self.assertIn("ждём второй визит", first_visit.text())
                self.assertLessEqual(first_visit.fontMetrics().horizontalAdvance(first_visit.text()),
                                     first_visit.width())
                previous_locale = current_locale()
                try:
                    set_active_locale(UiLocale.EN)
                    pane_ui.set_locale()
                    self.assertIn("Data age", pane_ui.board._timing_labels[1].text())
                    self.assertIn("model max", pane_ui.board._timing_labels[1].text())
                    for label in pane_ui.board._timing_labels.values():
                        self.assertLessEqual(label.fontMetrics().horizontalAdvance(label.text()), label.width())
                finally:
                    set_active_locale(previous_locale)
                    pane_ui.set_locale()
            self.assertEqual(native.engines, [])  # A visible status does not Start RX.
        finally:
            if prepared is not None:
                handle = prepared.handle
                if not handle.shutdown_complete:
                    if handle.applied:
                        for future in handle.pump.stop_all().values():
                            future.result(timeout=5)
                    handle.shutdown_after_stop()
                if pane_ui is not None and handle.shutdown_complete:
                    pane_ui.release_presentation_after_shutdown()
                    pane_ui.close()
            elif pools:
                pools[0].close()
            graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
