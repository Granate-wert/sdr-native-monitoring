"""Independent-pane typed USB/IP route controls (mock/offscreen only)."""

from __future__ import annotations

import os
import unittest
from dataclasses import replace
from types import SimpleNamespace
from typing import ClassVar, cast
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid as is_qobject_valid

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_stage import discard_user_pane_session, prepare_user_pane_session

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

                restored = replace(original, operational_routes=(Route(USB), Route(IP)))
                editor.update_sources(AnalyzerSourceSelection(revision=3, choices=(restored,)))
                self.assertEqual(row._route_intent, Route(IP))
                self.assertFalse(row._route_unavailable)

                editor.update_sources(AnalyzerSourceSelection(revision=4, choices=()))
                self.assertEqual(row.source.currentData(), original.device_id)
                self.assertEqual(row._route_intent, Route(IP))
                self.assertIsNotNone(editor._route_refusal_key(row))
                row.source.setCurrentIndex(0)
                self.assertIsNone(row._route_intent)
                self.assertIsNone(editor._route_refusal_key(row))
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

    def test_preview_uses_staged_route_not_current_widget(self) -> None:
        native, graph = _ad_graph(uri=USB, serial="app07-known")
        try:
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
                row.route.setCurrentIndex(0)
                self.assertIn("USB", editor.preview.text())
            finally:
                if prepared is not None:
                    discard_user_pane_session(prepared)
                    editor._prepared = None
                self._close(editor)
        finally:
            graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
