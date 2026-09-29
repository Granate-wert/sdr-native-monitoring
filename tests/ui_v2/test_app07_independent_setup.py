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
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session
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
            for future in handle.pump.stop_all().values():
                future.result(timeout=5)
            editor.close_applied_layout(handle)
            self._wait(lambda: handle.shutdown_complete and bool(uninstalled))
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
            editor.release_after_shutdown()
            editor.close()
            for graph in graphs:
                graph.live.shutdown()


if __name__ == "__main__":
    unittest.main()
