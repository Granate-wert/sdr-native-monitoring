"""Stage admits one explicit presentation ledger before any graph mutation."""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import discard_user_pane_session, prepare_user_pane_session

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class PaneStageBudgetTests(unittest.TestCase):
    def test_budget_identity_is_preserved_from_stage_into_preparer(self) -> None:
        native, graph = _ad_graph(serial="stage-budget")
        choice = graph.live.discover(startup=True)[0]
        budget = PresentationAllocationBudget()
        pool = PaneProductGraphPool(lambda _resource: graph)
        prepared = prepare_user_pane_session(
            (PaneSlotDraft(1, choice.device_id, 100e6, 108e6),),
            pool_factory=lambda: pool, allocation_budget=budget)
        try:
            self.assertIs(prepared.handle.preparer.allocation_budget, budget)
            self.assertFalse(prepared.handle.applied)
            self.assertEqual(native.engines, [])
            self.assertEqual(budget.snapshot().reserved_bytes, 0)
        finally:
            discard_user_pane_session(prepared)

    def test_invalid_budget_refuses_before_graph_factory(self) -> None:
        factory = Mock()
        for invalid in (False, 1, object()):
            with self.subTest(invalid=type(invalid).__name__):
                with self.assertRaises(TypeError):
                    prepare_user_pane_session((), pool_factory=factory,
                                              allocation_budget=invalid)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
