"""Real pool/Stage refusal taxonomy; synthetic native facts only, no RF."""
from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import PaneUserStageError, prepare_user_pane_session
from tests.ui_v2.test_app07_usb_ip_alias_exclusion import ETH3, USB3, USB4, fixture


class ParallelIdentityRefusalTests(unittest.TestCase):
    def test_pool_codes_require_enum_and_legacy_failure_remains_generic(self):
        self.assertIs(PaneGraphPoolError("legacy").reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        with self.assertRaises(TypeError):
            PaneGraphPoolError("untyped", reason="parallel_identity_unconfirmed")

    def test_missing_known_usb_alias_both_orders_is_typed_and_no_rx(self):
        for known_first in (False, True):
            with self.subTest(known_first=known_first):
                usb = fixture(USB4, "")
                eth = fixture(USB3, "ad3-known-serial")
                # Ethernet remains independently observable; no USB candidate
                # for it is advertised. Same scenario as the current HIL refusal.
                eth[0].routes = ()
                ordered = (eth, usb) if known_first else (usb, eth)
                pool = PaneProductGraphPool(lambda resource: ordered[int(resource.rsplit("-", 1)[1]) - 1][1])
                drafts = tuple(PaneSlotDraft(index, item[2], 100e6, 108e6,
                    operational_route=Route(ETH3 if item is eth else USB4))
                    for index, item in enumerate(ordered, 1))
                with self.assertRaises(PaneUserStageError) as caught:
                    prepare_user_pane_session(drafts, pool_factory=lambda: pool)
                error = caught.exception
                self.assertIs(error.reason, PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED)
                self.assertIsNone(error.failed_reason)
                self.assertIsNone(error.pool)
                self.assertTrue(error.__suppress_context__)
                self.assertNotIn("USB alias", str(error))
                self.assertNotIn("ad3-known-serial", str(error))
                self.assertEqual(pool.staged_resource_ids, ())
                self.assertEqual(pool.cleanup_pending_resource_ids, ())
                self.assertIsNone(pool.composed_session)
                self.assertEqual(usb[0].engines + eth[0].engines, [])
                self.assertTrue(all(device.calls[-1] == "disconnect"
                    for device in usb[0].created + eth[0].created))

    def test_cleanup_precedence_retains_typed_first_refusal_without_driver_text(self):
        pool = Mock()
        pool.stage.side_effect = PaneGraphPoolError("private-driver/serial/path",
            reason=PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED)
        pool.close.side_effect = RuntimeError("private-cleanup")
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),),
                                      pool_factory=lambda: pool)
        error = caught.exception
        self.assertIs(error.reason, PaneUserRefusal.CLEANUP_REQUIRED)
        self.assertIs(error.failed_reason, PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED)
        self.assertIs(error.pool, pool)
        self.assertTrue(error.__suppress_context__)
        self.assertNotIn("private", str(error))
        pool.close.assert_called_once_with()
        pool.compose.assert_not_called()

    def test_real_pool_uncertain_close_retains_graph_and_original_category(self):
        for known_first in (False, True):
            with self.subTest(known_first=known_first):
                usb = fixture(USB4, "")
                eth = fixture(USB3, "ad3-known-serial")
                eth[0].routes = ()
                ordered = (eth, usb) if known_first else (usb, eth)
                pool = PaneProductGraphPool(lambda resource: ordered[int(resource.rsplit("-", 1)[1]) - 1][1])
                drafts = tuple(PaneSlotDraft(index, item[2], 100e6, 108e6,
                    operational_route=Route(ETH3 if item is eth else USB4))
                    for index, item in enumerate(ordered, 1))
                try:
                    with patch.object(ordered[1][1].live, "shutdown",
                                      side_effect=RuntimeError("private-cleanup")), self.assertRaises(PaneUserStageError) as caught:
                        prepare_user_pane_session(drafts, pool_factory=lambda: pool)
                    error = caught.exception
                    self.assertIs(error.reason, PaneUserRefusal.CLEANUP_REQUIRED)
                    self.assertIs(error.failed_reason, PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED)
                    self.assertIs(error.pool, pool)
                    self.assertEqual(pool.cleanup_pending_resource_ids, ("pane-resource-2",))
                    self.assertIs(pool._cleanup_pending["pane-resource-2"], ordered[1][1])
                    self.assertIsNone(pool.composed_session)
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                    self.assertNotIn("private", str(error))
                    self.assertTrue(error.__suppress_context__)
                finally:
                    pool.close()
                self.assertEqual(pool.cleanup_pending_resource_ids, ())

    def test_arbitrary_backend_reason_attribute_is_not_trusted(self):
        pool = Mock()
        error = RuntimeError("parallel_identity_unconfirmed private-driver")
        error.reason = PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED
        pool.stage.side_effect = error
        with self.assertRaises(PaneUserStageError) as caught:
            prepare_user_pane_session((PaneSlotDraft(1, "source", 100e6, 108e6),),
                                      pool_factory=lambda: pool)
        self.assertIs(caught.exception.reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        self.assertNotIn("private", str(caught.exception))
        pool.close.assert_called_once_with()

    def test_real_pool_does_not_promote_sdk_text_or_reason_attribute(self):
        native, graph, source = fixture(USB4, "")
        pool = PaneProductGraphPool(lambda _resource: graph)
        error = RuntimeError("parallel_identity_unconfirmed private-sdk")
        error.reason = PaneUserRefusal.PARALLEL_IDENTITY_UNCONFIRMED
        graph.live.discover = Mock(side_effect=error)
        with self.assertRaises(PaneGraphPoolError) as caught:
            pool.stage("resource", source)
        self.assertIs(caught.exception.reason, PaneUserRefusal.STAGE_NOT_CONFIRMED)
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertNotIn("private", str(caught.exception))
        self.assertEqual(native.engines, [])
        self.assertEqual(pool.staged_resource_ids, ())
        pool.close()


if __name__ == "__main__":
    unittest.main()
