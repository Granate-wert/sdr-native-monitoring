"""APP-07 current V2 source graph pool, fake SDK/serial and no RX on Stage."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.identity import SourceId
from sdr_monitor.domain.pluto_connection import PlutoUsbConnectionExpectation
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, HackrfRtbwPaneProfile, PaneCaptureProfile,
    PaneLayoutSlot, PaneProfile, TinySaTracePaneProfile, compile_pane_layout,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection,
    ReceiverEndpoint, SpectrumTraceEndpoint,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from sdr_monitor.services.rtl_capability_provider import (
    RTL_SOURCE_ID, RtlCapabilityProvider, RtlRuntimeProvision,
)
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft, compile_user_pane_plan

from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep
from tests.test_app07_rtl_product_route import _Native as _MockRtlNative
from tests.test_app07_shared_capture_schedule import pane
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_fixture
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_fixture
from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative


_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


def _ad_graph(*, uri="ip:pluto-app07.local", serial="app07-distinct-pluto"):
    native = _ObservedReadbackNative(uri=uri, serial=serial)
    live = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),),
                                      control_transaction=live.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=live, analyzer_display=_UnusedSweep(), device_catalog=catalog))
    return native, graph


def _bind_mock_usb_selection(graph, expectation):
    """Attach a typed mock USB observation to the current selected choice."""
    real_select = graph.live.select_device
    real_source_current = graph.sources.current
    retained = {}

    def select(source_id):
        result = real_select(source_id)
        state = real_source_current()
        assert state is not None and state.selected is not None
        selected = replace(state.selected, usb_connection=expectation)
        retained["selection"] = replace(state, choices=(selected,), selected_id=source_id)
        return result

    def current_selection():
        if "selection" in retained:
            return retained["selection"]
        return real_source_current()

    patchers = (
        patch.object(graph.live, "select_device", side_effect=select),
        patch.object(graph.live, "current_source_selection", side_effect=current_selection),
        patch.object(graph.sources, "current", side_effect=current_selection),
    )
    for patcher in patchers:
        patcher.start()
    return retained, patchers


class PaneProductGraphPoolTests(unittest.TestCase):
    def test_inert_three_distinct_staged_sources_compose_3plus1(self) -> None:
        ad_native, ad_graph = _ad_graph(serial="")
        hf = hackrf_fixture()
        ts = tinysa_fixture()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        ts_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        ad_source = ad_graph.live.discover(startup=True)[0].device_id
        ts_source = ts_graph.live.discover(startup=True)[0].device_id
        created = []

        def factory(resource_id):
            created.append(resource_id)
            return {"ad:physical": ad_graph, "hf:physical": hf_graph,
                    "ts:physical": ts_graph}[resource_id]

        pool = PaneProductGraphPool(factory)
        self.assertEqual(created, [])
        self.assertEqual(ad_native.engines, [])
        session = None
        handle = None
        leases = ReceiverLeaseManager(max_active_resources=4)
        try:
            ad_choice = pool.stage("ad:physical", ad_source)
            hf_choice = pool.stage("hf:physical", "source-hackrf")
            ts_choice = pool.stage("ts:physical", ts_source)
            self.assertEqual(created, ["ad:physical", "hf:physical", "ts:physical"])
            self.assertEqual(tuple(choice.family.value for choice in
                                   (ad_choice, hf_choice, ts_choice)),
                             ("ad936x", "hackrf", "tinysa"))
            self.assertIsNone(ad_choice.binding.identity_key)
            self.assertEqual(ad_native.engines, [])
            self.assertEqual(hf.factory.controls, [])
            self.assertEqual(ts.serials, [])
            with self.assertRaisesRegex(PaneGraphPoolError, "twice"):
                pool.stage("ad:other", ad_source)
            groups = (
                AcquisitionGroup("ad:group", "ad:physical", (
                    ReceiverEndpoint("ad:rx1", ad_source, "ad:physical", ReceiverChainSelection.RX1),)),
                AcquisitionGroup("hf:group", "hf:physical", (
                    ReceiverEndpoint("hf:rx1", hf_choice.device_id, "hf:physical", ReceiverChainSelection.RX1),)),
                AcquisitionGroup("ts:group", "ts:physical", (
                    SpectrumTraceEndpoint("ts:trace", ts_source, "ts:physical"),)),
            )
            slots = (
                PaneLayoutSlot(1, replace(pane("ad", "ad:rx1", 100e6, 108e6,
                                            ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="ad")),
                PaneLayoutSlot(2, replace(pane("hf", "hf:rx1", 140e6, 148e6,
                                            ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="hf")),
                PaneLayoutSlot(3, replace(pane("ts", "ts:trace", 200e6, 210e6,
                                            ReceiverBindingMode.DEDICATED_PARALLEL), profile_id="ts")),
                PaneLayoutSlot(4),
            )
            selection = ts_graph.live.current_source_selection()
            assert selection is not None and selection.selected is ts_choice
            profiles: dict[str, PaneProfile] = {
                "ad": PaneCaptureProfile(20e6, 10e6, "manual", 20.0,
                                         4096, 2048, "hann", "sample", None, 10e6, _COST),
                "hf": HackrfRtbwPaneProfile(HackrfLiveRequest(
                    144e6, 20e6, 15_000_000, 16, 20, fft_size=4096,
                    hop_size=2048, detector="peak", source_id=SourceId(hf_choice.device_id)), 10e6, _COST),
                "ts": TinySaTracePaneProfile(3, 11e6, "ultra-default", _COST,
                    request_template=TinySaSweepRequest(ts_choice, selection.revision,
                                                        200_000_000, 210_000_000, 3)),
            }
            layout = compile_pane_layout(slots, groups, profiles)
            self.assertEqual(layout.empty_slots, (4,))
            session = pool.compose(layout, groups, leases)
            assert session is not None
            handle = PaneProductSessionHandle(pool, layout, groups, session)
            self.assertEqual(tuple(item.affected_pane_ids for item in handle.preview()),
                             (("ad",), ("hf",), ("ts",)))
            self.assertEqual(leases.active_resource_count, 0)
            handle.apply()
            self.assertEqual(leases.active_resource_count, 3)
            self.assertFalse(handle.can_close())
            with self.assertRaisesRegex(PaneGraphPoolError, "Stop"):
                pool.close()
        finally:
            if handle is not None and handle.applied:
                for future in handle.pump.stop_all().values():
                    future.result(timeout=3)
                handle.shutdown_after_stop()
            else:
                pool.close()
        self.assertEqual(leases.active_resource_count, 0)
        self.assertEqual(pool.staged_resource_ids, ())

    def test_reused_graph_refuses_before_second_discovery_or_owner_close(self) -> None:
        native, graph = _ad_graph()
        source_id = graph.live.discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        try:
            pool.stage("first", source_id)
            with self.assertRaisesRegex(PaneGraphPoolError, "reused"):
                pool.stage("second", "different-operational-id")
            self.assertEqual(pool.staged_resource_ids, ("first",))
            self.assertEqual(native.engines, [])
        finally:
            pool.close()

    def test_two_routes_of_one_physical_pluto_refuse_duplicate_source_stage(self) -> None:
        _, first = _ad_graph(uri="usb:pluto-app07")
        _, second = _ad_graph(uri="ip:pluto-app07")
        first_source = first.live.discover(startup=True)[0].device_id
        second_source = second.live.discover(startup=True)[0].device_id
        self.assertEqual(first_source, second_source)
        pool = PaneProductGraphPool(lambda resource: first if resource == "first" else second)
        try:
            pool.stage("first", first_source)
            with self.assertRaisesRegex(PaneGraphPoolError, "twice"):
                pool.stage("second", second_source)
            self.assertEqual(pool.staged_resource_ids, ("first",))
        finally:
            pool.close()

    def test_unidentified_pluto_can_pair_with_other_family_but_not_second_pluto(self) -> None:
        _, first = _ad_graph(uri="usb:pluto-app07-one", serial="")
        _, second = _ad_graph(uri="ip:pluto-app07-two", serial="")
        hf = hackrf_fixture()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        first_id = first.live.discover(startup=True)[0].device_id
        second_id = second.live.discover(startup=True)[0].device_id
        self.assertNotEqual(first_id, second_id)
        pool = PaneProductGraphPool(lambda resource: {
            "first": first, "second": second, "hackrf": hf_graph}[resource])
        try:
            selected = pool.stage("first", first_id)
            self.assertIsNone(selected.binding.identity_key)
            pool.stage("hackrf", "source-hackrf")
            with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                pool.stage("second", second_id)
            self.assertEqual(pool.staged_resource_ids, ("first", "hackrf"))
        finally:
            pool.close()

    def test_distinct_usb_plutos_stage_and_compose_in_both_orders(self) -> None:
        expectation_sets = (
            (PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "KNOWN-A"),
             PlutoUsbConnectionExpectation(2, 19, 5, 0x0456, 0xb673, "")),
            (PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, ""),
             PlutoUsbConnectionExpectation(2, 19, 5, 0x0456, 0xb673, "")),
        )
        for expectations_tuple in expectation_sets:
            for order in (("a", "b"), ("b", "a")):
                with self.subTest(order=order, expectations=expectations_tuple):
                    graphs = {
                        "a": _ad_graph(uri="usb:fixture-a", serial="")[1],
                        "b": _ad_graph(uri="usb:fixture-b", serial="")[1],
                    }
                    source_ids = {key: graph.live.discover(startup=True)[0].device_id
                                  for key, graph in graphs.items()}
                    expectations = dict(zip(("a", "b"), expectations_tuple, strict=True))
                    retained, patchers = {}, []
                    for key, graph in graphs.items():
                        selected, installed = _bind_mock_usb_selection(graph, expectations[key])
                        retained[key] = selected
                        patchers.extend(installed)
                    pool = PaneProductGraphPool(lambda resource: graphs[
                        "a" if resource.endswith("1") else "b"])
                    try:
                        staged = {}
                        for key in order:
                            resource_id = f"pane-resource-{1 if key == 'a' else 2}"
                            staged[source_ids[key]] = pool.stage(resource_id, source_ids[key])
                        self.assertEqual(tuple(pool.staged_resource_ids),
                                         tuple(f"pane-resource-{1 if key == 'a' else 2}"
                                               for key in order))
                        revisions = {source_id: retained[key]["selection"].revision
                                     for key, source_id in source_ids.items()}
                        plan = compile_user_pane_plan((
                            PaneSlotDraft(1, source_ids["a"], 100e6, 108e6, sample_rate_hz=20e6),
                            PaneSlotDraft(2, source_ids["b"], 120e6, 128e6, sample_rate_hz=20e6),
                        ), staged, revisions)
                        with patch("sdr_monitor.ui.v2_pane_composition.PaneResourceSession",
                                   wraps=PaneResourceSession) as session_factory:
                            session = pool.compose(plan.layout, plan.groups,
                                ReceiverLeaseManager(max_active_resources=4))
                        self.assertIsNotNone(session)
                        self.assertEqual(session_factory.call_args.kwargs["source_usb_connections"],
                                         {source_ids[key]: expectations[key] for key in source_ids})
                    finally:
                        pool.close()
                        for patcher in reversed(patchers):
                            patcher.stop()

    def test_parallel_usb_aliases_and_unresolved_usb_ip_fail_closed(self) -> None:
        cases = (
            (PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, ""),
             PlutoUsbConnectionExpectation(2, 18, 6, 0x0456, 0xb673, "")),
            (PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, "SAME"),
             PlutoUsbConnectionExpectation(2, 19, 5, 0x0456, 0xb673, "same")),
        )
        for first_usb, second_usb in cases:
            first_graph = _ad_graph(uri="usb:alias-a", serial="")[1]
            second_graph = _ad_graph(uri="usb:alias-b", serial="")[1]
            ids = (first_graph.live.discover(startup=True)[0].device_id,
                   second_graph.live.discover(startup=True)[0].device_id)
            _, first_patchers = _bind_mock_usb_selection(first_graph, first_usb)
            _, second_patchers = _bind_mock_usb_selection(second_graph, second_usb)
            pool = PaneProductGraphPool(lambda resource: first_graph if resource.endswith("1") else second_graph)
            try:
                pool.stage("pane-resource-1", ids[0])
                with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                    pool.stage("pane-resource-2", ids[1])
                self.assertEqual(pool.staged_resource_ids, ("pane-resource-1",))
            finally:
                pool.close()
                for patcher in reversed((*first_patchers, *second_patchers)):
                    patcher.stop()

        for order in (("usb", "ip"), ("ip", "usb")):
            usb_graph = _ad_graph(uri="usb:unresolved", serial="")[1]
            ip_graph = _ad_graph(uri="ip:unresolved.local", serial="")[1]
            graphs = {"usb": usb_graph, "ip": ip_graph}
            ids = {key: graph.live.discover(startup=True)[0].device_id
                   for key, graph in graphs.items()}
            resource_keys = {f"pane-resource-{index + 1}": key
                             for index, key in enumerate(order)}
            _, usb_patchers = _bind_mock_usb_selection(
                usb_graph, PlutoUsbConnectionExpectation(2, 18, 5, 0x0456, 0xb673, ""))
            pool = PaneProductGraphPool(lambda resource: graphs[resource_keys[resource]])
            try:
                for index, key in enumerate(order):
                    if index == 0:
                        pool.stage(f"pane-resource-{index + 1}", ids[key])
                    else:
                        with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                            pool.stage(f"pane-resource-{index + 1}", ids[key])
            finally:
                pool.close()
                for patcher in reversed(usb_patchers):
                    patcher.stop()

    def test_compose_rejects_changed_selection_with_same_operational_id(self) -> None:
        _, graph = _ad_graph()
        source_id = graph.live.discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        try:
            choice = pool.stage("pane-resource-1", source_id)
            captured = pool._selections["pane-resource-1"]
            plan = compile_user_pane_plan((
                PaneSlotDraft(1, source_id, 100e6, 108e6, sample_rate_hz=20e6),
            ), {source_id: choice}, {source_id: captured.revision})
            changed = replace(captured, revision=captured.revision + 1)
            with (patch.object(graph.live, "current_source_selection", return_value=changed),
                  patch.object(graph.sources, "current", return_value=changed)):
                with self.assertRaisesRegex(PaneGraphPoolError, "changed after Stage"):
                    pool.compose(plan.layout, plan.groups,
                                 ReceiverLeaseManager(max_active_resources=2))
        finally:
            pool.close()

    def test_failed_selection_keeps_failed_close_for_explicit_retry(self) -> None:
        _, graph = _ad_graph()
        factory_calls = []
        pool = PaneProductGraphPool(lambda resource: factory_calls.append(resource) or graph)
        with patch.object(graph.live, "shutdown", side_effect=RuntimeError("private SDK close")):
            with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                pool.stage("first", "absent-source")
            with self.assertRaisesRegex(PaneGraphPoolError, "cannot be staged"):
                pool.stage("second", "absent-source")
            self.assertEqual(factory_calls, ["first"])
        self.assertEqual(pool.cleanup_pending_resource_ids, ("first",))
        pool.close()
        self.assertEqual(pool.cleanup_pending_resource_ids, ())

    def test_failed_close_retains_staged_selection_until_explicit_retry(self) -> None:
        _, graph = _ad_graph()
        source_id = graph.live.discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        selected = pool.stage("pane-resource-1", source_id)
        captured = pool._selections["pane-resource-1"]
        with patch.object(graph.live, "shutdown", side_effect=RuntimeError("private SDK close")):
            with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                pool.close()
        self.assertEqual(pool.cleanup_pending_resource_ids, ("pane-resource-1",))
        self.assertEqual(pool.staged_resource_ids, ("pane-resource-1",))
        self.assertIs(pool._selections["pane-resource-1"], captured)
        self.assertEqual(captured.selected, selected)
        with self.assertRaisesRegex(PaneGraphPoolError, "cannot be staged"):
            pool.stage("pane-resource-2", source_id)
        pool.close()
        self.assertEqual(pool.cleanup_pending_resource_ids, ())
        self.assertEqual(pool.staged_resource_ids, ())
        self.assertEqual(pool._selections, {})

    def test_network_stage_is_explicit_and_uses_full_discovery_only_when_requested(self) -> None:
        _, graph = _ad_graph(uri="ip:pluto-app07-network")
        source_id = graph.live.discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        try:
            with patch.object(graph.live, "discover", wraps=graph.live.discover) as scan:
                pool.stage("network", source_id, include_network=True)
                scan.assert_called_once_with()
        finally:
            pool.close()

    def test_local_stage_passes_explicit_local_scope_not_startup_filter(self) -> None:
        _, graph = _ad_graph(uri="usb:pluto-app07-local")
        original_discover = graph.live.discover
        source_id = original_discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        try:
            # Keep the isolated UI writer independent of the root-owned backend
            # extension while asserting the exact graph-pool call boundary.
            with patch.object(graph.live, "discover",
                              side_effect=lambda **kwargs: original_discover(startup=True)) as scan:
                pool.stage("local", source_id)
                scan.assert_called_once_with(local_only=True)
        finally:
            pool.close()

    def test_local_stage_enumerates_mock_rtl_and_selects_current_route_without_rx(self) -> None:
        class RtlGraphNative(_ObservedReadbackNative, _MockRtlNative):
            DetectorType = _MockRtlNative.DetectorType

            def __init__(self) -> None:
                _ObservedReadbackNative.__init__(self, serial="mock-ad-unused",
                                                 uri="usb:mock-ad-unused")
                _MockRtlNative.__init__(self)
                self.DetectorType = _MockRtlNative.DetectorType

        native = RtlGraphNative()
        live = NativeLiveSessionService(native)
        runtime = object()
        provider = RtlCapabilityProvider(RtlRuntimeProvision(
            native, runtime, "b" * 64, "c" * 64))
        catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live), provider),
                                          control_transaction=live.capability_control_transaction)
        rtl_owner = RtlAnalyzerService(native, live, catalog.snapshot, catalog.rtl_provision_for)
        graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=live, device_catalog=catalog, analyzer_rtl=rtl_owner))
        pool = PaneProductGraphPool(lambda _resource: graph)
        try:
            with patch.object(live, "discover_startup_devices",
                              wraps=live.discover_startup_devices) as usb, \
                 patch.object(live, "discover_devices",
                              side_effect=AssertionError("network discovery is not local")) as network, \
                 patch.object(native, "rtl_enumerate_candidates",
                              wraps=native.rtl_enumerate_candidates) as enumerate_rtl:
                selected = pool.stage("rtl:physical", RTL_SOURCE_ID)
            usb.assert_called_once_with()
            network.assert_not_called()
            enumerate_rtl.assert_called_once_with(runtime)
            self.assertEqual(selected.device_id, RTL_SOURCE_ID)
            self.assertIsNotNone(selected.binding.rtl_session_route)
            selection = graph.live.current_source_selection()
            self.assertIsNotNone(selection)
            self.assertIs(selection.selected, selected)
            self.assertEqual(native.create_calls, 0)
            self.assertEqual(native.engines, [])
        finally:
            pool.close()


if __name__ == "__main__":
    unittest.main()
