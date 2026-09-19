"""Rejected shared-budget resize is not applied, persisted or retried by data."""
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.ui.v2.i18n import UiLocale, text
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.state.analyzer_layers import waterfall_line_from_sweep
from tests.ui_v2.test_app04_progressive_waterfall import progress, terminal
from tests.ui_v2 import test_waterfall as fixture


class HistoryAdmissionTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WaterfallPaneTests.setUpClass.__func__)
    setUp = fixture.WaterfallPaneTests.setUp
    tearDown = fixture.WaterfallPaneTests.tearDown
    _pane = fixture.WaterfallPaneTests._pane
    _settings = fixture.WaterfallPaneTests._settings

    def test_rejected_history_and_rate_changes_preserve_controls_settings_and_live_rows(self):
        for option in ("history", "rate"):
            with self.subTest(option=option):
                pane = self._pane()
                pane.set_history_seconds(1)
                budget = PresentationAllocationBudget(100000)
                pane._renderer.allocation_budget = budget
                pane.set_line(fixture._line(-90, timestamp_ns=1_000_000_000))
                old = pane.config
                ring = pane._renderer.buffer
                pane.flush_settings()
                budget.limit_bytes = 1
                if option == "history":
                    pane._history_seconds.setValue(2)
                else:
                    pane._rows_per_second.setCurrentIndex(pane._rows_per_second.findData(60))
                self.assertEqual(pane.config, old)
                self.assertIs(pane._renderer.buffer, ring)
                self.assertEqual(pane._history_seconds.value(), old.history_seconds)
                self.assertEqual(pane._rows_per_second.currentData(), old.rows_per_second)
                self.assertEqual(pane.metrics.configuration_rejections, 1)
                self.assertEqual(budget.snapshot().reserved_bytes, 0)
                pane.flush_settings()
                self.assertEqual(int(pane._settings.value("ui_v2/live/waterfall/v1/history_seconds")), 1)
                self.assertEqual(int(pane._settings.value("ui_v2/live/waterfall/v1/rows_per_second")), 30)
                with patch.object(pane._renderer, "_new_ring", side_effect=AssertionError("hidden retry")):
                    pane.set_line(fixture._line(-80, timestamp_ns=2_000_000_000))
                self.assertEqual(pane.history_rows, 2)
                np.testing.assert_array_equal(pane._renderer.tiles()[0][:, 0], [-90, -80])
                for locale in (UiLocale.EN, UiLocale.RU):
                    pane.set_locale(locale)
                    self.assertEqual(pane._status.text(), text("waterfall.capacity_rejected", locale))
                # Explicit retry after capacity is available preserves retained rows.
                budget.limit_bytes = 100000
                if option == "history":
                    pane.set_history_seconds(2)
                else:
                    pane.set_rows_per_second(60)
                self.assertEqual(pane._renderer.buffer.rows, 60)
                self.assertEqual(pane.history_rows, 2)
                self.assertFalse(pane._capacity_change_rejected)
                np.testing.assert_array_equal(pane._renderer.tiles()[0][:, 0], [-90, -80])

    def test_sweep_rejection_keeps_partial_row_terminal_update_and_stamp_history(self):
        pane = self._pane()
        pane.set_history_seconds(1)
        budget = PresentationAllocationBudget(100000)
        pane._renderer.allocation_budget = budget
        pane.set_sweep_line(waterfall_line_from_sweep(progress()))
        ring = pane._renderer.buffer
        budget.limit_bytes = 1
        pane.set_history_seconds(2)
        self.assertEqual(pane.config.history_seconds, 1)
        with patch.object(pane._renderer, "_new_ring", side_effect=AssertionError("hidden retry")):
            pane.set_sweep_line(waterfall_line_from_sweep(terminal()))
            pane.set_sweep_line(waterfall_line_from_sweep(progress(2)))
        self.assertIs(pane._renderer.buffer, ring)
        stamps = pane._renderer.sweep_stamps()
        self.assertEqual(len(stamps), 2)
        self.assertEqual([stamp.sequence for stamp in stamps], [1, 2])
        budget.limit_bytes = 100000
        pane.set_history_seconds(2)
        self.assertEqual(pane._renderer.sweep_stamps(), stamps)
        self.assertEqual(pane.history_rows, 2)
        self.assertEqual(pane._renderer.buffer.rows, 60)


if __name__ == "__main__":
    unittest.main()
