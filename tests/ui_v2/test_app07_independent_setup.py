"""Real Qt editor, fake current owners: explicit mixed-source Stage→Apply."""

from __future__ import annotations

import os
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
from sdr_monitor.services.pane_resource_session import PaneHostTiming
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PanePumpResourceState
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, RtbwBandPolicy
from sdr_monitor.ui.v2_pane_user_stage import (
    apply_user_pane_session, discard_user_pane_session, prepare_user_pane_session,
)
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_hackrf_sweep_common_analyzer import FakeHackrfSweep
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from tests.ui_v2.test_app07_ad936x_sweep_pane_owner import ad_sweep_graph


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

    def test_explicit_wide_band_stage_preview_lock_locale_discard_and_mode_restore(self) -> None:
        native, ad = _ad_graph(serial="")
        hf_fixture = hackrf_fixture()
        hf = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf_fixture.live, device_catalog=hf_fixture.catalog,
            analyzer_hackrf=hf_fixture.hackrf))
        graphs = (ad, hf)
        choices = tuple(next(item for item in graph.live.discover(startup=True)
                             if item.family is family)
                        for graph, family in zip(graphs,
                            (DeviceFamily.AD936X, DeviceFamily.HACKRF), strict=True))
        pools, installed = [], []
        editor = IndependentPaneSetupV2(install=installed.append, uninstall=lambda: None)
        locale = current_locale()

        def stage(drafts):
            pool = PaneProductGraphPool(lambda resource: graphs[int(resource.rsplit("-", 1)[-1]) - 1])
            pools.append(pool)
            return prepare_user_pane_session(drafts, pool_factory=lambda: pool)

        try:
            editor.resize(1100, 480)
            editor.update_sources(AnalyzerSourceSelection(revision=1, choices=choices))
            editor.show()
            self.app.processEvents()
            editor.band_toggle.click()
            for row, choice, rate, start, stop in zip(editor._rows[:2], choices,
                    (61.44e6, 20e6), (100, 140), (156, 160), strict=True):
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
                row.rate.setCurrentIndex(row.rate.findData(rate))
                row.start.setValue(start)
                row.stop.setValue(stop)
                self.assertIs(RtbwBandPolicy(row.band.currentData()), RtbwBandPolicy.FULL_RECEIVE)
                row.band.setCurrentIndex(row.band.findData(RtbwBandPolicy.FULL_RECEIVE.value))
                self.assertTrue(row.band.isEnabled())
                row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
                self.assertFalse(row.band.isEnabled())
                self.assertIs(editor._read_drafts()[row.number - 1].rtbw_band,
                              RtbwBandPolicy.EDGE_TRIMMED)
                row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.RTBW.value))
                self.assertIs(editor._read_drafts()[row.number - 1].rtbw_band,
                              RtbwBandPolicy.FULL_RECEIVE)
            self.assertTrue(all(not row.band.isEnabled() for row in editor._rows[2:]))
            draft = editor._read_drafts()
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                       side_effect=stage):
                editor.prepare.click()
                self._wait(lambda: editor._prepared is not None or editor._error_key is not None)
            self.assertIsNotNone(editor._prepared, editor.error.text())
            staged = editor._prepared
            for language, prefix, filter_text, scope in (
                    (UiLocale.EN, "Pane 1, RTBW plan:", "RF filter 56 MHz", "half-open"),
                    (UiLocale.RU, "Окно 1, план RTBW:", "RF-фильтр 56 МГц", "полуоткрытую")):
                set_active_locale(language)
                editor.set_locale()
                self.app.processEvents()
                self.assertIs(editor._prepared, staged)
                self.assertEqual(editor._read_drafts(), draft)
                self.assertTrue(all(not row.band.isEnabled() for row in editor._rows))
                line = next(line for line in editor.preview.text().splitlines() if line.startswith(prefix))
                self.assertIn(filter_text, line)
                self.assertIn("61.44", line)
                self.assertIn("4096", line)
                self.assertIn(scope, editor.preview.text())
                self.assertEqual(editor.preview.accessibleName(), editor.preview.text())
                self.assertTrue(editor.apply.isEnabled())
                self.assertGreaterEqual(editor._rows[0].band.height(),
                                        editor._rows[0].band.minimumSizeHint().height())
            self.assertEqual(native.engines, [])
            self.assertEqual(hf_fixture.factory.controls, [])
            self.assertFalse(installed)
            editor.discard.click()
            self._wait(lambda: editor.can_close)
            self.assertEqual(editor._read_drafts(), draft)
            self.assertTrue(editor._rows[0].band.isEnabled())
        finally:
            self._wait(lambda: editor._future is None)
            if editor._prepared is not None:
                discard_user_pane_session(editor._prepared)
                editor._prepared = None
            set_active_locale(locale)
            editor.release_after_shutdown()
            editor.close()
            for pool in pools:
                if pool.staged_resource_ids:
                    pool.close()
            for graph in graphs:
                graph.live.shutdown()

    def test_three_sweep_2x2_ad_progress_is_visible_before_complete_pass(self) -> None:
        native, ad_fake, ad = ad_sweep_graph()
        ad_fake.hold_terminal = True
        hf_fixture = hackrf_fixture()
        hf_fake = FakeHackrfSweep()
        hf = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf_fixture.live, device_catalog=hf_fixture.catalog,
            analyzer_hackrf=hf_fixture.hackrf, analyzer_hackrf_sweep=hf_fake))
        ts_fixture = tinysa_fixture()
        ts = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts_fixture.live, device_catalog=ts_fixture.catalog,
            analyzer_tinysa=ts_fixture.instrument))
        graphs = (ad, hf, ts)
        choices = tuple(next(item for item in graph.live.discover(startup=True)
                             if item.family is family)
                        for graph, family in zip(graphs,
                            (DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.TINYSA), strict=True))
        pools, installed = [], []
        editor = IndependentPaneSetupV2(install=installed.append, uninstall=lambda: None)
        pane_ui = None
        locale = current_locale()

        def stage(drafts):
            pool = PaneProductGraphPool(lambda resource: graphs[int(resource.rsplit("-", 1)[-1]) - 1])
            pools.append(pool)
            return prepare_user_pane_session(drafts, pool_factory=lambda: pool)

        try:
            editor.resize(1100, 480)
            editor.update_sources(AnalyzerSourceSelection(revision=1, choices=choices))
            editor.show()
            self.app.processEvents()
            for row, choice in zip(editor._rows[:3], choices, strict=True):
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
                row.start.setValue(100)
                row.stop.setValue(300 if choice.family is DeviceFamily.TINYSA else 220)
                if choice.family is DeviceFamily.TINYSA:
                    row.points.setValue(1001)
                else:
                    row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            ad_row = editor._rows[0]
            self.assertTrue(ad_row.mode.isEnabled())
            self.assertFalse(ad_row.rate.isEnabled())
            self.assertEqual(ad_row.rate.currentData(), 61_440_000.0)
            self.assertEqual(tuple(ad_row.fft.itemData(i) for i in range(ad_row.fft.count())),
                             (1024, 4096, 16384))
            for language in (UiLocale.EN, UiLocale.RU):
                set_active_locale(language)
                editor.set_locale()
                self.assertIs(editor._selected_mode(ad_row), CaptureMeasurementMode.SWEEP)
                self.assertEqual(ad_row.rate.currentData(), 61_440_000.0)
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                       side_effect=stage):
                editor.prepare.click()
                self._wait(lambda: editor._prepared is not None)
            self.assertIn("8192", editor.preview.text())
            self.assertIn("4096", editor.preview.text())
            self.assertIn("36", editor.preview.text())
            for language, ad_prefix, hf_prefix, crop_text, step_text in (
                    (UiLocale.EN, "Pane 1, AD936x Sweep plan:", "Pane 2, HackRF Sweep plan:",
                     "2 disjoint 5 MHz windows", "tuning step 20 MHz"),
                    (UiLocale.RU, "Окно 1, план сканирования AD936x:",
                     "Окно 2, план сканирования HackRF:", "2 раздельных окна по 5 МГц",
                     "шаг настройки 20 МГц")):
                set_active_locale(language)
                editor.set_locale()
                ad_line = next(line for line in editor.preview.text().splitlines()
                               if line.startswith(ad_prefix))
                hf_line = next(line for line in editor.preview.text().splitlines()
                               if line.startswith(hf_prefix))
                self.assertIn("N 4096", ad_line)
                self.assertIn("8192", ad_line)
                self.assertIn("4096", hf_line)
                self.assertIn(crop_text, hf_line)
                self.assertIn(step_text, hf_line)
                self.assertNotIn("AD936x", hf_line)
                self.assertNotIn("N ", hf_line)
                self.assertNotIn("W ", hf_line)
            self.assertEqual(ad_fake.events, [])
            self.assertEqual(hf_fake.events, [])
            self.assertEqual(native.engines, [])
            self.assertEqual(ts_fixture.serials, [])
            editor.apply.click()
            self._wait(lambda: bool(installed))
            handle = installed[0]
            self.assertEqual(handle.layout.empty_slots, (4,))
            self.assertEqual(ad_fake.events, [])
            pane_ui = IndependentPaneSessionV2(handle)
            pane_ui.resize(1280, 850)
            pane_ui.show()
            self.app.processEvents()
            pane_ui.start_all.click()
            self._wait(lambda: all(pane_ui.board.pane(number).spectrum_scene.latest_frame is not None
                                   for number in (1, 2, 3)))
            self._wait(lambda: all(pane_ui.board.pane(number).waterfall_pane.history_rows > 0
                                   for number in (1, 2, 3)))
            self.assertIsNone(pane_ui.board.pane(4))
            self.assertIsNotNone(ad_fake.progress)
            self.assertEqual(len(ad_fake.publications), 1)  # terminal still withheld
            self.assertIs(ad_fake.publications[0].line, ad_fake.line)
            self.assertEqual(ad_fake.events.count("start"), 1)
            self.assertEqual(hf_fake.events.count("start"), 1)
            ad_fake.hold_terminal = False
            self._wait(lambda: not ad_fake.publications)
            pane_ui.board.select_slot(3)
            pane_ui.stop_selected.click()
            self._wait(lambda: next(state for state in handle.pump.snapshot()
                                    if state.physical_stream_resource_id == "pane-resource-3").phase
                       is PanePumpPhase.STOPPED)
            self.assertNotIn("stop", ad_fake.events)
            self.assertNotIn("stop", hf_fake.events)
            self.assertEqual({state.phase for state in handle.pump.snapshot()
                              if state.physical_stream_resource_id in {"pane-resource-1", "pane-resource-2"}},
                             {PanePumpPhase.RUNNING})
            pane_ui.stop_all.click()
            self._wait(handle.can_close)
            self.assertEqual(ad_fake.events.count("stop"), 1)
            self.assertEqual(hf_fake.events.count("stop"), 1)
            editor.close_applied_layout(handle)
            self._wait(lambda: handle.shutdown_complete and editor.can_close)
        finally:
            if installed and not installed[0].shutdown_complete:
                for future in installed[0].pump.stop_all().values():
                    future.result(timeout=5)
                installed[0].shutdown_after_stop()
            elif not installed:
                for pool in pools:
                    if pool.staged_resource_ids:
                        pool.close()
            if pane_ui is not None and installed and installed[0].shutdown_complete:
                pane_ui.release_presentation_after_shutdown()
                pane_ui.close()
            if editor._prepared is not None:
                editor.discard.click()
                self._wait(editor.can_close)
            editor.release_after_shutdown()
            editor.close()
            for graph in graphs:
                graph.live.shutdown()
            set_active_locale(locale)

    def test_shared_stop_dialog_accepts_equal_yes_value_not_only_same_object(self) -> None:
        parent = QWidget()
        try:
            with patch.object(QMessageBox, "question", return_value=int(QMessageBox.StandardButton.Yes)):
                self.assertTrue(IndependentPaneSessionV2._ask_shared_stop(
                    parent, ("pane-1", "pane-2")))
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                self.assertFalse(IndependentPaneSessionV2._ask_shared_stop(
                    parent, ("pane-1", "pane-2")))
        finally:
            parent.close()

    def test_shared_stop_real_qt_dialog_defaults_to_no_and_accepts_explicit_yes(self) -> None:
        parent = QWidget()
        observed_defaults = []
        real_question = QMessageBox.question
        try:
            for response, expected in ((QMessageBox.StandardButton.No, False),
                                       (QMessageBox.StandardButton.Yes, True)):
                choose = QTimer(parent)
                guard = QTimer(parent)
                guard.setSingleShot(True)

                def choose_button():
                    box = self.app.activeModalWidget()
                    if isinstance(box, QMessageBox):
                        observed_defaults.append(box.standardButton(box.defaultButton()))
                        choose.stop()
                        box.button(response).click()

                def cancel_on_timeout():
                    box = self.app.activeModalWidget()
                    if isinstance(box, QMessageBox):
                        box.reject()

                choose.timeout.connect(choose_button)
                guard.timeout.connect(cancel_on_timeout)
                choose.start(5)
                guard.start(2000)
                try:
                    with patch.object(QMessageBox, "question", wraps=real_question) as question:
                        self.assertEqual(IndependentPaneSessionV2._ask_shared_stop(
                            parent, ("pane-1", "pane-2")), expected)
                        question.assert_called_once()
                finally:
                    choose.stop()
                    guard.stop()
            self.assertEqual(observed_defaults, [QMessageBox.StandardButton.No] * 2)
        finally:
            parent.close()

    def test_shared_hackrf_rtbw_sweep_stop_selected_needs_ack_and_releases_one_rx(self) -> None:
        hf = hackrf_fixture()
        fake = FakeHackrfSweep()
        graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog,
            analyzer_hackrf=hf.hackrf, analyzer_hackrf_sweep=fake))
        choice = next(item for item in graph.live.discover(startup=True)
                      if item.family is DeviceFamily.HACKRF)
        pool = PaneProductGraphPool(lambda _resource: graph)
        prepared = None
        pane_ui = None
        acknowledged = False
        impacts = []

        def confirm(impact):
            impacts.append(impact)
            return acknowledged

        try:
            prepared = prepare_user_pane_session((
                PaneSlotDraft(1, choice.device_id, 100e6, 108e6),
                PaneSlotDraft(2, choice.device_id, 140e6, 180e6,
                              measurement_mode=CaptureMeasurementMode.SWEEP),
                PaneSlotDraft(3), PaneSlotDraft(4),
            ), pool_factory=lambda: pool)
            apply_user_pane_session(prepared)
            handle = prepared.handle
            assert handle.layout.schedule is not None
            self.assertEqual(len(handle.layout.schedule.resources), 1)
            self.assertEqual(len(handle.layout.schedule.resources[0].jobs), 2)
            pane_ui = IndependentPaneSessionV2(handle, confirm_shared_stop=confirm)
            pane_ui.resize(1280, 700)
            pane_ui.show()
            self.app.processEvents()
            pane_ui.start_all.click()
            self._wait(lambda: pane_ui.board.pane(1).spectrum_scene.latest_frame is not None
                       and pane_ui.board.pane(2).spectrum_scene.latest_frame is not None)
            self.assertEqual(handle.pump.snapshot()[0].phase, PanePumpPhase.RUNNING)
            pane_ui.stop_selected.click()
            self.app.processEvents()
            self.assertEqual(impacts, [("pane-1", "pane-2")])
            self.assertEqual(handle.pump.snapshot()[0].phase, PanePumpPhase.RUNNING)
            acknowledged = True
            pane_ui.stop_selected.click()
            self._wait(handle.can_close)
            self.assertEqual(impacts[-1], ("pane-1", "pane-2"))
            self.assertEqual(handle.pump.snapshot()[0].phase, PanePumpPhase.STOPPED)
            self.assertEqual(handle.session.retained_resource_count, 0)
            prepared.handle.shutdown_after_stop()
            pane_ui.release_presentation_after_shutdown()
            pane_ui.close()
        finally:
            if prepared is not None and not prepared.handle.shutdown_complete:
                if prepared.handle.applied:
                    for future in prepared.handle.pump.stop_all().values():
                        future.result(timeout=5)
                prepared.handle.shutdown_after_stop()
            elif prepared is None and pool.staged_resource_ids:
                pool.close()
            if pane_ui is not None and prepared is not None and prepared.handle.shutdown_complete:
                pane_ui.release_presentation_after_shutdown()
                pane_ui.close()
            graph.live.shutdown()

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
        editor.resize(1380, 480)
        editor.show()
        self.app.processEvents()
        try:
            for row, choice in zip(editor._rows, choices, strict=False):
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
            self.assertIsNone(editor._rows[3].source.currentData())
            mode = editor._rows[0].mode
            self.assertTrue(mode.isEnabled())
            original_mode = mode.currentData()
            center = mode.rect().center()
            wheel = QWheelEvent(QPointF(center), QPointF(mode.mapToGlobal(center)), QPoint(),
                                QPoint(0, -120), Qt.MouseButton.NoButton,
                                Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
            self.app.sendEvent(mode, wheel)
            self.assertEqual(mode.currentData(), original_mode)
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
            self.assertIn("3", editor.impact_summary.text())
            self.assertIn("4", editor.impact_summary.text())
            self.assertEqual(editor.impact_summary.accessibleName(), editor.impact_summary.text())
            self.assertTrue(editor.impact_summary.isVisible())
            self.assertTrue(editor.apply.isEnabled())
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
            pane_ui = IndependentPaneSessionV2(handle, close_layout=lambda _handle: None)
            pane_ui.resize(1280, 700)
            pane_ui.show()
            self.app.processEvents()
            original_graphs = tuple(pane_ui.board.pane(number) for number in (1, 2, 3))
            self.assertFalse(pane_ui.board.stacked_layout)
            self.assertEqual(pane_ui.board_scroll.verticalScrollBar().maximum(), 0)
            pane_ui.resize(960, 460)
            self.app.processEvents()
            self.assertEqual((pane_ui.width(), pane_ui.height()), (960, 460))
            self.assertTrue(pane_ui.board.stacked_layout)
            self.assertGreater(pane_ui.board_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(pane_ui.board_scroll.horizontalScrollBar().maximum(), 0)
            cells = tuple(pane_ui.board._cells.values())
            self.assertEqual({cell.x() for cell in cells}, {0})
            self.assertEqual(tuple(sorted(cell.y() for cell in cells)), tuple(cell.y() for cell in cells))
            pane_ui.board_scroll.ensureWidgetVisible(cells[-1])
            self.app.processEvents()
            self.assertGreater(pane_ui.board_scroll.verticalScrollBar().value(), 0)
            pane_ui.resize(640, 280)
            self.app.processEvents()
            self.assertEqual((pane_ui.width(), pane_ui.height()), (640, 280))
            self.assertTrue(pane_ui.board.stacked_layout)
            self.assertEqual(pane_ui.board_scroll.horizontalScrollBar().maximum(), 0)
            for control in (pane_ui.start_selected, pane_ui.start_all,
                            pane_ui.stop_selected, pane_ui.stop_all, pane_ui.close_layout):
                if not control.isVisible():
                    continue
                self.assertLessEqual(control.mapTo(pane_ui, control.rect().bottomRight()).x(), 640)
                self.assertLessEqual(control.fontMetrics().horizontalAdvance(control.text()) + 16,
                                     control.width(), control.text())
            pane_ui.resize(1280, 700)
            self.app.processEvents()
            self.assertFalse(pane_ui.board.stacked_layout)
            self.assertEqual(pane_ui.board_scroll.verticalScrollBar().maximum(), 0)
            self.assertEqual(original_graphs, tuple(pane_ui.board.pane(number) for number in (1, 2, 3)))
            self.assertEqual(native.engines, [])
            self.assertEqual(hf.factory.controls, [])
            self.assertEqual(ts.serials, [])
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
                self.assertIn("модель 0.16 с", first)
                self.assertNotIn("макс.", first)
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
                    self.assertIn("revisit 2.40 s / model 0.16 s",
                                  pane_ui.board._timing_labels[1].text())
                    self.assertIn("not a guaranteed device maximum",
                                  pane_ui.board._timing_labels[1].toolTip())
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

    def test_user_hackrf_sweep_uses_same_tab_graph_and_explicit_start_stop(self) -> None:
        hf = hackrf_fixture()
        fake = FakeHackrfSweep()
        graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog,
            analyzer_hackrf=hf.hackrf, analyzer_hackrf_sweep=fake))
        choice = next(item for item in graph.live.discover(startup=True)
                      if item.family is DeviceFamily.HACKRF)
        pools = []
        installed = []
        editor = IndependentPaneSetupV2(install=installed.append, uninstall=lambda: None)
        pane_ui = None

        def stage(drafts):
            pool = PaneProductGraphPool(lambda _resource: graph)
            pools.append(pool)
            return prepare_user_pane_session(drafts, pool_factory=lambda: pool)

        try:
            editor.update_sources(AnalyzerSourceSelection(revision=1, choices=(choice,)))
            editor.show()
            self.app.processEvents()
            row = editor._rows[0]
            row.source.setCurrentIndex(row.source.findData(choice.device_id))
            row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
            self.assertIs(editor._selected_mode(row), CaptureMeasurementMode.SWEEP)
            self.assertFalse(row.rate.isEnabled())
            self.assertEqual(row.rate.currentData(), 20_000_000.0)
            self.assertEqual(tuple(row.fft.itemData(i) for i in range(row.fft.count())),
                             (1024, 2048, 4096))
            editor.prepare.click()  # 140–148 MHz cannot be a host Sweep.
            self.assertIsNone(editor._future)
            self.assertTrue(editor.error.isVisible())
            self.assertFalse(pools)
            self.assertEqual(fake.events, [])
            row.start.setValue(100.0)
            row.stop.setValue(220.0)
            row.fft.setCurrentIndex(row.fft.findData(2048))
            self.assertEqual(editor._read_drafts()[0].fft_size, 2048)
            with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session",
                       side_effect=stage):
                editor.prepare.click()
                self._wait(lambda: editor._prepared is not None)
            self.assertEqual(fake.events, [])
            editor.apply.click()
            self._wait(lambda: bool(installed))
            handle = installed[0]
            self.assertEqual(handle.layout.empty_slots, (2, 3, 4))
            self.assertEqual(fake.events, [])  # Apply reserved but did not Start.
            pane_ui = IndependentPaneSessionV2(handle)
            pane_ui.resize(1280, 700)
            pane_ui.show()
            self.app.processEvents()
            pane_ui.start_all.click()
            self._wait(lambda: pane_ui.board.pane(1).spectrum_scene.latest_frame is not None)
            self.assertIsNotNone(fake.request)
            self.assertEqual(fake.request.fft_size, 2048)
            self.assertEqual((fake.request.start_hz, fake.request.stop_hz),
                             (100_000_000, 220_000_000))
            self.assertEqual(handle.queue.pane_ids, ("pane-1",))
            self.assertIsNone(pane_ui.board.pane(4))
            pane_ui.stop_all.click()
            self._wait(handle.can_close)
            self.assertIn("stop", fake.events)
            editor.close_applied_layout(handle)
            self._wait(lambda: handle.shutdown_complete and editor.can_close)
            pane_ui.release_presentation_after_shutdown()
            pane_ui.close()
        finally:
            if installed and not installed[0].shutdown_complete:
                for future in installed[0].pump.stop_all().values():
                    future.result(timeout=5)
                installed[0].shutdown_after_stop()
            elif not installed:
                for pool in pools:
                    if pool.staged_resource_ids:
                        pool.close()
            if pane_ui is not None and installed and installed[0].shutdown_complete:
                pane_ui.release_presentation_after_shutdown()
                pane_ui.close()
            editor.release_after_shutdown()
            editor.close()
            graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
