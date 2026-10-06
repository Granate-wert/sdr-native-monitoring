"""Independent-pane typed USB/IP route controls (mock/offscreen only)."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar, cast
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid as is_qobject_valid

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_stage import discard_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.i18n import UiLocale, current_locale, set_active_locale

from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


USB = "usb:2.25.5"
IP = "ip:pluto-app07.local"


class IndependentPaneRouteTests(unittest.TestCase):
    app: ClassVar[QApplication]
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = cast(QApplication, QApplication.instance()) if QApplication.instance() else QApplication([])

    def _close(self, editor: IndependentPaneSetupV2) -> None:
        editor.release_after_shutdown()
        editor.close()
        if is_qobject_valid(editor):
            editor.deleteLater()
            QCoreApplication.sendPostedEvents(editor, QEvent.Type.DeferredDelete)
        self.assertFalse(is_qobject_valid(editor))

    def _editor(self, selection: AnalyzerSourceSelection) -> IndependentPaneSetupV2:
        editor = IndependentPaneSetupV2(install=lambda _handle: None, uninstall=lambda: None)
        editor.update_sources(selection)
        editor.show()
        self.app.processEvents()
        return editor

    def test_typed_pin_is_copied_and_shared_source_peers_are_read_only(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
            choice = replace(graph.live.discover(local_only=True)[0],
                             operational_routes=(Route(USB), Route(IP)))
            self.assertEqual(choice.operational_routes, (Route(USB), Route(IP)))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(choice,)))
            try:
                for row in editor._rows[:2]:
                    row.source.setCurrentIndex(row.source.findData(choice.device_id))
                owner, peer = editor._rows[:2]
                self.assertTrue(owner.route.isVisible())
                self.assertEqual(owner.route.itemData(0), None)
                self.assertIn(Route(IP), tuple(owner.route.itemData(i)
                                               for i in range(owner.route.count())))
                ip_index = next(i for i in range(owner.route.count())
                                if owner.route.itemData(i) == Route(IP))
                owner.route.setCurrentIndex(ip_index)
                drafts = editor._read_drafts()
                self.assertEqual(drafts[0].operational_route, Route(IP))
                self.assertEqual(drafts[1].operational_route, Route(IP))
                self.assertFalse(peer.route.isEnabled())
                self.assertIn("IP", peer.route.currentText())
                self.assertIn("1, 2", owner.route.toolTip())
                self.assertEqual(native.engines, [])
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_source_group_adopts_destination_route_in_both_directions_and_departure(self) -> None:
        native_a, graph_a = _ad_graph(uri=USB, serial="app07-group-a")
        native_b, graph_b = _ad_graph(uri=USB, serial="app07-group-b")
        try:
            choice_a = replace(graph_a.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB), Route(IP)))
            choice_b = replace(graph_b.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB),))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(choice_a, choice_b)))
            try:
                first, second, third = editor._rows[:3]
                first.source.setCurrentIndex(first.source.findData(choice_a.device_id))
                first.route.setCurrentIndex(next(i for i in range(first.route.count())
                                                 if first.route.itemData(i) == Route(IP)))
                second.source.setCurrentIndex(second.source.findData(choice_b.device_id))
                second.route.setCurrentIndex(next(i for i in range(second.route.count())
                                                  if second.route.itemData(i) == Route(USB)))
                third.source.setCurrentIndex(third.source.findData(choice_b.device_id))
                third.route.setCurrentIndex(next(i for i in range(third.route.count())
                                                 if third.route.itemData(i) == Route(USB)))

                # Later peer joins the earlier group and adopts its pin.
                second.source.setCurrentIndex(second.source.findData(choice_a.device_id))
                self.assertEqual(first._route_intent, Route(IP))
                self.assertEqual(second._route_intent, Route(IP))
                self.assertEqual(editor._read_drafts()[1].operational_route, Route(IP))
                self.assertTrue(first.route_scope.isVisible())
                self.assertFalse(second.route.isEnabled())

                # The peer deliberately leaves, restoring an independent B group.
                second.source.setCurrentIndex(second.source.findData(choice_b.device_id))
                self.assertEqual(second._route_intent, Route(USB))

                # Earlier leader joins the destination group and adopts USB.
                first.source.setCurrentIndex(first.source.findData(choice_b.device_id))
                self.assertEqual(first._route_intent, Route(USB))
                self.assertEqual(second._route_intent, Route(USB))
                self.assertEqual(editor._read_drafts()[0].operational_route, Route(USB))
                self.assertTrue(first.route_scope.isVisible())
                self.assertFalse(second.route.isEnabled())

                # Departure leaves the remaining destination group unchanged.
                second.source.setCurrentIndex(0)
                self.assertEqual(first._route_intent, Route(USB))
                self.assertEqual(third._route_intent, Route(USB))
                self.assertIsNone(second._route_intent)
                self.assertTrue(first.route.isEnabled())
                self.assertTrue(third.route.isVisible())
            finally:
                self._close(editor)
        finally:
            graph_a.live.shutdown()
            graph_b.live.shutdown()

    def test_refresh_retains_unavailable_pin_and_deliberate_empty_clears_it(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
            original = replace(graph.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB), Route(IP)))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(original,)))
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(original.device_id))
                ip_index = next(i for i in range(row.route.count())
                                if row.route.itemData(i) == Route(IP))
                row.route.setCurrentIndex(ip_index)
                self.assertEqual(row._route_intent, Route(IP))

                usb_only = replace(original, operational_routes=(Route(USB),))
                editor.update_sources(AnalyzerSourceSelection(revision=2, choices=(usb_only,)))
                self.assertEqual(row._route_intent, Route(IP))
                self.assertTrue(row._route_unavailable)
                self.assertIsNotNone(editor._route_refusal_key(row))
                self.assertFalse(editor.prepare.isEnabled())
                with patch("sdr_monitor.ui.v2.workspaces.independent_pane_setup.prepare_user_pane_session") as stage:
                    editor._begin_prepare()
                    stage.assert_not_called()

                # Automatic is a deliberate recovery for a still-published
                # source; it cannot turn an unpublished source into evidence.
                row.route.setCurrentIndex(0)
                self.assertIsNone(row._route_intent)
                self.assertIsNone(editor._route_refusal_key(row))
                self.assertTrue(editor.prepare.isEnabled())

                restored = replace(original, operational_routes=(Route(USB), Route(IP)))
                editor.update_sources(AnalyzerSourceSelection(revision=3, choices=(restored,)))
                self.assertIsNone(row._route_intent)
                self.assertFalse(row._route_unavailable)
                row.route.setCurrentIndex(next(i for i in range(row.route.count())
                                               if row.route.itemData(i) == Route(IP)))

                editor.update_sources(AnalyzerSourceSelection(revision=4, choices=()))
                self.assertEqual(row.source.currentData(), original.device_id)
                self.assertEqual(row._route_intent, Route(IP))
                self.assertIsNotNone(editor._route_refusal_key(row))
                row.route.setCurrentIndex(0)
                self.assertIsNone(row._route_intent)
                self.assertIsNotNone(editor._route_refusal_key(row))
                row.source.setCurrentIndex(0)
                self.assertIsNone(row._route_intent)
                self.assertIsNone(editor._route_refusal_key(row))
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_missing_source_refresh_preserves_ad_profile_until_return(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
            original = replace(graph.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB), Route(IP)))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(original,)))
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(original.device_id))
                row.mode.setCurrentIndex(row.mode.findData(CaptureMeasurementMode.SWEEP.value))
                row.start.setValue(112.0)
                row.stop.setValue(136.0)
                rate_index = row.rate.findData(20_000_000.0)
                if rate_index >= 0:
                    row.rate.setCurrentIndex(rate_index)
                row.fft.setCurrentIndex(row.fft.findData(4096))
                row.sweep_window.setValue(3.0)
                row.priority.setValue(7)
                row.maximum_revisit.setValue(2.5)
                before = editor._read_drafts()[0]
                self.assertIs(before.measurement_mode, CaptureMeasurementMode.SWEEP)
                self.assertEqual(before.sweep_window_hz, 3_000_000.0)

                editor.update_sources(AnalyzerSourceSelection(revision=2, choices=()))
                missing = editor._read_drafts()[0]
                self.assertEqual(missing.source_id, before.source_id)
                self.assertEqual(missing.measurement_mode, before.measurement_mode)
                self.assertEqual(missing.start_hz, before.start_hz)
                self.assertEqual(missing.stop_hz, before.stop_hz)
                self.assertEqual(missing.sample_rate_hz, before.sample_rate_hz)
                self.assertEqual(missing.fft_size, before.fft_size)
                self.assertEqual(missing.sweep_window_hz, before.sweep_window_hz)
                self.assertEqual(missing.priority, before.priority)
                self.assertEqual(missing.maximum_revisit_s, before.maximum_revisit_s)
                self.assertIsNotNone(editor._route_refusal_key(row))

                editor.update_sources(AnalyzerSourceSelection(revision=3, choices=(original,)))
                returned = editor._read_drafts()[0]
                self.assertEqual(returned, before)
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_non_ad_source_has_no_pluto_route_selector(self) -> None:
        fixture = hackrf_fixture()
        graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=fixture.live, device_catalog=fixture.catalog,
            analyzer_hackrf=fixture.hackrf))
        try:
            choice = cast(AnalyzerSourceChoice, next(
                item for item in graph.live.discover(startup=True)
                if getattr(item, "family", None) is DeviceFamily.HACKRF))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(choice,)))
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
                self.assertFalse(row.route.isVisible())
                self.assertEqual(row.route.count(), 0)
                self.assertIsNone(editor._read_drafts()[0].operational_route)
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_no_route_guidance_localization_accessibility_and_narrow_layout(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        previous_locale = current_locale()
        try:
            set_active_locale(UiLocale.EN)
            original = graph.live.discover(local_only=True)[0]
            choice = replace(original, operational_routes=())
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(choice,)))
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(choice.device_id))
                editor.resize(640, 280)
                self.app.processEvents()
                self.assertEqual(row.route.count(), 1)
                self.assertIn("Automatic", row.route.currentText())
                self.assertIn("No USB/IP route metadata", row.route.toolTip())
                self.assertIn("Pane 1 route", row.route.accessibleName())
                set_active_locale(UiLocale.RU)
                editor.set_locale()
                self.assertIn("Автоматический", row.route.currentText())
                self.assertIn("USB/IP", row.route.toolTip())
                self.assertTrue(row.route.accessibleName())
            finally:
                self._close(editor)
        finally:
            set_active_locale(previous_locale)
            graph.live.shutdown()

    def test_route_selector_is_keyboard_reachable(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
            original = replace(graph.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB), Route(IP)))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(original,)))
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(original.device_id))
                row.route.setCurrentIndex(0)
                row.route.setFocus()
                QTest.keyClick(row.route, Qt.Key.Key_Down)
                self.assertEqual(row.route.currentData(), Route(USB))
                self.assertTrue(row.route.hasFocus())
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_manual_ip_selection_publishes_exact_choice_to_editor(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
            graph.live.discover(local_only=True)
            graph.live.select_manual_uri(IP)
            selection = graph.sources.current()
            self.assertIsNotNone(selection)
            assert selection is not None and selection.selected is not None
            self.assertEqual(selection.selected.operational_routes, (Route(IP),))
            editor = self._editor(selection)
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(selection.selected_id))
                self.assertEqual(tuple(row.route.itemData(i) for i in range(row.route.count())),
                                 (None, Route(IP)))
                self.assertNotIn(Route(USB), tuple(row.route.itemData(i)
                                                   for i in range(row.route.count())))
            finally:
                self._close(editor)
        finally:
            graph.live.shutdown()

    def test_preview_uses_staged_route_not_current_widget(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        previous_locale = current_locale()
        try:
            set_active_locale(UiLocale.EN)
            original = replace(graph.live.discover(local_only=True)[0],
                               operational_routes=(Route(USB), Route(IP)))
            editor = self._editor(AnalyzerSourceSelection(revision=1, choices=(original,)))
            prepared = None
            try:
                row = editor._rows[0]
                row.source.setCurrentIndex(row.source.findData(original.device_id))
                usb_index = next(i for i in range(row.route.count())
                                 if row.route.itemData(i) == Route(USB))
                row.route.setCurrentIndex(usb_index)
                prepared = prepare_user_pane_session(
                    editor._read_drafts(),
                    pool_factory=lambda: PaneProductGraphPool(lambda _resource: graph),
                )
                editor._prepared = prepared
                editor._refresh_preview()
                self.assertIn("USB", editor.preview.text())
                self.assertIn("Stage-confirmed intent", editor.preview.text())
                self.assertIn("Apply revalidates", editor.preview.text())
                row.route.setCurrentIndex(0)
                editor._refresh_preview()
                self.assertIn("USB", editor.preview.text())
            finally:
                if prepared is not None:
                    discard_user_pane_session(prepared)
                    editor._prepared = None
                self._close(editor)
        finally:
            set_active_locale(previous_locale)
            graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
