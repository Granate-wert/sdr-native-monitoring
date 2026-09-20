"""Control publications do not re-scan an already accepted measurement grid."""
from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analyzer import bundle_from_sweep
from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from sdr_monitor.ui.v2.i18n import text
from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
from sdr_monitor.ui.v2.workspaces import analyzer
from tests import test_app02_analyzer_workspace_product as product
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal


class AnalyzerControlGridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = f = product.AnalyzerWorkspaceProductTests("runTest")
        f.app = self.app
        f.setUp()
        self.addCleanup(f.doCleanups)
        self.addCleanup(f.tearDown)
        self.page = f.page

    def test_same_bundle_control_updates_keep_history_and_update_buttons_and_errors(self):
        page = self.page
        state = replace(page.model.state, mode=AnalyzerMode.SWEEP,
                        bundle=bundle_from_sweep(progress()), running=True)
        page._render(state)
        scene = page.visualization.spectrum_scene
        equal = Mock(wraps=np.array_equal)
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)), \
             patch.object(scene, "set_frame", wraps=scene.set_frame) as frames, \
             patch.object(page.visualization.waterfall_pane, "clear_history") as clear:
            for next_state in (replace(state, stopping=True),
                               replace(state, running=False, stopping=True),
                               replace(state, running=False, error="cleanup failed")):
                page._render(next_state)
            self.assertEqual(equal.call_count, 0)
            frames.assert_not_called()
            clear.assert_not_called()
            self.assertEqual(page.error.text(), "cleanup failed")
            self.assertTrue(page.mode.isEnabled())
            self.assertEqual(page.primary.text(), text("analyzer.start"))
            self.assertIs(scene.latest_frame, state.bundle)

    def test_each_new_bundle_is_compared_even_when_grid_storage_is_shared(self):
        page = self.page
        state = replace(page.model.state, mode=AnalyzerMode.SWEEP,
                        bundle=bundle_from_sweep(progress()), running=True)
        page._render(state)
        equal = Mock(wraps=np.array_equal)
        with patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)), \
             patch.object(page.visualization.waterfall_pane, "clear_history") as clear:
            next_bundle = bundle_from_sweep(state.bundle.spectrum)
            page._render(replace(state, bundle=next_bundle))
            self.assertEqual(equal.call_count, 1)
            clear.assert_not_called()
            changed_grid = progress().frequencies_hz.copy()
            changed_grid[1] += .25  # same endpoints/count, changed interior
            changed_grid.setflags(write=False)
            changed = bundle_from_sweep(replace(progress(), frequencies_hz=changed_grid))
            page._render(replace(state, bundle=changed))
            self.assertEqual(equal.call_count, 2)
            clear.assert_called_once_with(reset_kind=True)
            page._render(replace(state, bundle=None))
            self.assertIsNone(page.visualization.spectrum_scene.latest_frame)
            page._render(replace(state, bundle=changed))
            self.assertIs(page.visualization.spectrum_scene.latest_frame, changed)

    def test_actual_stop_retains_final_gap_and_all_control_acknowledgements(self):
        f, page = self.fixture, self.page
        f.select_and_apply()
        page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
        presenter = f.composition.analyzer_presenter
        initial = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), progress())
        with patch.object(presenter._service, "poll_latest", return_value=initial):
            page.primary.click()
            f.wait(lambda: page._last_bundle is not None and not presenter.is_starting)
            presenter._timer.setInterval(100000)
            f.wait(lambda: presenter._poll_future is None)
        final = ContinuousSweepDisplaySnapshot(terminal(2, gap=True),
                    ContinuousSweepDisplayMetrics(terminal_control_gaps=1))
        states = []
        unsubscribe = page.model.subscribe(states.append)
        equal = Mock(wraps=np.array_equal)
        try:
            with patch.object(presenter._service, "poll_latest", return_value=final), \
                 patch.object(analyzer, "np", SimpleNamespace(array_equal=equal)):
                page.primary.click()
                f.wait(presenter.can_close)
            delivered = [s for s in states if s.bundle is not None and s.bundle.spectrum is final.line]
            self.assertEqual([(s.running, s.stopping) for s in delivered],
                             [(True, True), (False, True), (False, False)])
            # Final preparation already verified the same owned grid off GUI.
            self.assertEqual(equal.call_count, 0)
            self.assertIs(page.visualization.spectrum_scene.latest_frame.spectrum, final.line)
            self.assertTrue(page.visualization.spectrum_scene.latest_frame.terminal_sweep)
            self.assertEqual(page.visualization.waterfall_pane.history_rows, 2)
            self.assertTrue(page.primary.isEnabled())
            self.assertTrue(page.mode.isEnabled())
            self.assertEqual(f.events, ["sweep-start", "sweep-stop"])
        finally:
            unsubscribe()
