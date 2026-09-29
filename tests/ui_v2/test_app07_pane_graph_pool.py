"""APP-07 current V2 source graph pool, fake SDK/serial and no RX on Stage."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.identity import SourceId
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
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_product_session import PaneProductSessionHandle

from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep
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

    def test_failed_selection_keeps_failed_close_for_explicit_retry(self) -> None:
        _, graph = _ad_graph()
        pool = PaneProductGraphPool(lambda _resource: graph)
        with patch.object(graph.live, "shutdown", side_effect=RuntimeError("private SDK close")):
            with self.assertRaisesRegex(PaneGraphPoolError, "did not confirm"):
                pool.stage("first", "absent-source")
        self.assertEqual(pool.cleanup_pending_resource_ids, ("first",))
        pool.close()
        self.assertEqual(pool.cleanup_pending_resource_ids, ())


if __name__ == "__main__":
    unittest.main()
