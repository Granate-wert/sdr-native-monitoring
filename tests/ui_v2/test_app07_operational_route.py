"""Exact route intent on SAME graph/native control; mock SDK, never hardware."""
from __future__ import annotations

from dataclasses import replace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.live import LiveAdmissionRejected, LiveConfiguration
from sdr_monitor.domain.pane_user_refusal import PaneUserRefusal
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, PaneUserPlanError, compile_user_pane_plan
from sdr_monitor.ui.v2_pane_user_stage import PaneUserStageError, apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.services.pane_resource_session import PaneResourceError
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


USB = "usb:2.25.5"
ETH = "ip:192.168.1.54"


def graph():
    native, product = _ad_graph(uri=USB)
    native.routes = (USB, "ip:pluto-ad9363.local")
    choice = product.live.discover(local_only=True)[0]
    return native, product, choice


class OperationalRouteTests(unittest.TestCase):
    def test_typed_bounded_exact_uri(self):
        for uri in (USB, ETH, "ip:pluto-ad9363.local", "ip:[fe80::1%4]:30431"):
            with self.subTest(uri=uri):
                self.assertEqual(Route(uri).uri, uri)
        for uri in (None, True, "", "ip:", "ip: x", "IP:host", "usb:fixture",
                    "usb:2.128.5", "usb:256.25.5", "usb:2.25.256", "usb:02.25.5",
                    "ip:host\x00", "ip:плуто", "ip:" + "x" * 320):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                Route(uri)

    def test_draft_empty_and_untyped_routes_refuse(self):
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, operational_route=Route(ETH))
        with self.assertRaises(PaneUserPlanError):
            PaneSlotDraft(1, "sdr", 100e6, 108e6, operational_route=ETH)

    def test_fresh_usb_preferred_source_selects_exact_eth_without_rx(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        try:
            selected = pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
            self.assertEqual(selected.device_id, discovered.device_id)
            self.assertEqual(selected.binding.identity_key, discovered.binding.identity_key)
            self.assertIsNone(selected.usb_connection)
            snapshot = product.live.current_snapshot()
            self.assertEqual(snapshot.device.uri, ETH)
            self.assertEqual(product.services.live_sdr._native_uri, ETH)
            self.assertEqual(snapshot.device.alternate_uris, ())
            product.services.live_sdr.validate_operational_route(Route(ETH), source_id=selected.device_id)
            self.assertFalse(product.live.is_running())
            self.assertEqual(native.engines, [])
            self.assertTrue(all(device.calls[-1] == "disconnect" for device in native.created))
        finally:
            pool.close()

    def test_explicit_eth_only_needs_fresh_owned_identity_not_usb_or_broadcast(self):
        native, product, discovered = graph()
        native.routes = ()
        pool = PaneProductGraphPool(lambda _: product)
        try:
            selected = pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
            self.assertEqual(selected.binding.identity_key, discovered.binding.identity_key)
            self.assertEqual(product.live.current_snapshot().device.uri, ETH)
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_unadvertised_unknown_identity_is_not_admitted_as_stable_ip(self):
        native, product = _ad_graph(uri=ETH, serial="")
        source = product.live.discover(local_only=True)[0].device_id
        native.routes = ()
        pool = PaneProductGraphPool(lambda _: product)
        try:
            with self.assertRaises(PaneGraphPoolError):
                pool.stage("pane-resource-1", source, operational_route=Route(ETH))
            self.assertEqual(pool.staged_resource_ids, ())
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_exact_eth_start_failure_does_not_open_usb_fallback(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        try:
            pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
            product.live.apply_configuration(LiveConfiguration(center_hz=104e6, sample_rate_hz=20e6,
                analog_bandwidth_hz=10e6, fft_size=4096, gain_db=20,
                persistence_enabled=False, persistence_mode="disabled"))
            attempted = []

            def refused(uri, *_args, **_kwargs):
                attempted.append(uri)
                raise RuntimeError("mock Ethernet context unavailable")

            with patch.object(native, "PlutoFixedBandEngine", side_effect=refused):
                snapshot = product.services.live_sdr.start()
            self.assertIsNotNone(snapshot.error)
            self.assertEqual(attempted, [ETH])
            self.assertFalse(product.live.is_running())
        finally:
            pool.close()

    def test_route_mutation_refuses_before_configure_start_or_sweep(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        try:
            pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
            live = product.services.live_sdr
            original = live.latest_snapshot()
            live._native_uri = USB
            for operation in (lambda: live.apply_configuration(LiveConfiguration()), live.start,
                              live.acquire_native_sweep_lease):
                with self.subTest(operation=operation), self.assertRaises(LiveAdmissionRejected):
                    operation()
            self.assertIs(live.latest_snapshot(), original)
            self.assertEqual(native.engines, [])
            self.assertFalse(live._sweep_lease_active)
            live._native_uri = ETH
        finally:
            pool.close()

    def test_manual_route_with_changed_serial_refuses_and_closes(self):
        native, product, discovered = graph()
        observe = product.live.select_manual_uri

        def changed(uri):
            native.serial = native.topology_serial = "different-physical-device"
            return observe(uri)

        pool = PaneProductGraphPool(lambda _: product)
        with patch.object(product.live, "select_manual_uri", side_effect=changed):
            with self.assertRaises(PaneGraphPoolError):
                pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
        self.assertEqual(pool.staged_resource_ids, ())
        self.assertEqual(native.engines, [])
        pool.close()

    def test_staged_uri_or_serial_cache_mutation_refuses_compose(self):
        for field in ("uri", "serial"):
            with self.subTest(field=field):
                native, product, discovered = graph()
                pool = PaneProductGraphPool(lambda _: product)
                try:
                    selected = pool.stage("pane-resource-1", discovered.device_id, operational_route=Route(ETH))
                    revision = product.live.current_source_selection().revision
                    plan = compile_user_pane_plan((PaneSlotDraft(1, selected.device_id, 100e6, 108e6,
                        operational_route=Route(ETH)),), {selected.device_id:selected}, {selected.device_id:revision})
                    service = product.services.live_sdr
                    old = service.latest_snapshot()
                    changed = replace(old.device, **{field:USB if field == "uri" else "changed-serial"})
                    service._snapshot = replace(old, device=changed)
                    from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
                    with self.assertRaisesRegex(PaneGraphPoolError, "route changed"):
                        pool.compose(plan.layout, plan.groups, ReceiverLeaseManager(max_active_resources=4))
                    self.assertEqual(native.engines, [])
                    service._snapshot = old
                finally:
                    pool.close()

    def test_shared_route_conflict_is_rejected_before_discovery(self):
        native, product, discovered = graph()
        before = len(native.created)
        drafts = (PaneSlotDraft(1, discovered.device_id, 100e6, 108e6, operational_route=Route(ETH)),
                  PaneSlotDraft(2, discovered.device_id, 110e6, 118e6, operational_route=Route(USB)))
        pool = PaneProductGraphPool(lambda _: product)
        with self.assertRaises(PaneUserStageError) as error:
            prepare_user_pane_session(drafts, pool_factory=lambda: pool)
        self.assertIs(error.exception.reason, PaneUserRefusal.INVALID_PLAN)
        self.assertEqual(len(native.created), before)
        self.assertEqual(native.engines, [])
        with self.assertRaises(PaneUserPlanError):
            compile_user_pane_plan(drafts, {discovered.device_id: discovered}, {discovered.device_id: 1})

    def test_source_stage_retains_route_in_rf_context_and_compose_checks_pin(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        intent = Route(ETH)
        prepared = prepare_user_pane_session((PaneSlotDraft(1, discovered.device_id, 100e6, 108e6,
            operational_route=intent),), pool_factory=lambda: pool)
        try:
            self.assertIs(prepared.handle.rf_context.drafts[0].operational_route, intent)
            self.assertFalse(prepared.handle.applied)
            self.assertFalse(prepared.handle.pump.activated)
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_route_reselection_after_stage_refuses_apply_before_configuration(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        prepared = prepare_user_pane_session((PaneSlotDraft(1, discovered.device_id, 100e6, 108e6,
            operational_route=Route(ETH)),), pool_factory=lambda: pool)
        try:
            product.live.select_manual_uri(USB)
            self.assertEqual(product.live.current_snapshot().device.uri, USB)
            with patch.object(product.live, "apply_configuration") as configure:
                with self.assertRaises(PaneUserStageError) as error:
                    apply_user_pane_session(prepared)
                self.assertIs(error.exception.reason, PaneUserRefusal.SELECTION_CHANGED)
                configure.assert_not_called()
            self.assertFalse(prepared.handle.applied)
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_route_reselection_after_compose_refuses_same_source_capture_owner(self):
        native, product, discovered = graph()
        pool = PaneProductGraphPool(lambda _: product)
        prepared = prepare_user_pane_session((PaneSlotDraft(1, discovered.device_id, 100e6, 108e6,
            operational_route=Route(ETH)),), pool_factory=lambda: pool)
        try:
            product.live.select_manual_uri(USB)
            self.assertEqual(product.live.current_snapshot().device.device_id, discovered.device_id)
            with self.assertRaises(PaneResourceError):
                prepared.handle.session.apply()
            self.assertEqual(prepared.handle.session.retained_resource_count, 0)
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_exact_route_for_other_family_is_refused_without_manual_selection(self):
        from types import SimpleNamespace
        from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
        from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
        fixture = hackrf_fixture()
        product = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=fixture.live, device_catalog=fixture.catalog, analyzer_hackrf=fixture.hackrf))
        pool = PaneProductGraphPool(lambda _: product)
        try:
            with patch.object(product.live, "select_manual_uri") as select:
                with self.assertRaises(PaneGraphPoolError):
                    pool.stage("pane-resource-1", "source-hackrf", operational_route=Route(ETH))
                select.assert_not_called()
            self.assertEqual(fixture.factory.controls, [])
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
