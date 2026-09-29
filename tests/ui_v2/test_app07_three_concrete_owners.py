"""Three existing family graphs + Empty, with fake SDK/serial rather than RF."""

from __future__ import annotations

import os
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.live import LiveConfiguration
from sdr_monitor.domain.pane_scheduler import (
    CaptureEpochCost, HackrfRtbwPaneProfile, PaneCaptureProfile,
    PaneLayoutSlot, TinySaTracePaneProfile, compile_pane_layout,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
    SpectrumTraceEndpoint,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_session import PaneResourceError
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_composition import compose_v2_pane_resource_session
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer, PreparedPaneDelivery
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2
from sdr_monitor.ui.v2.workspaces.independent_pane_board import IndependentPaneBoardV2
from sdr_monitor.ui.v2.workspaces.independent_pane_delivery import IndependentPaneDeliveryPort

from tests.test_app06_pluto_observation_catalog import _Native as _ObservedNative
from tests.test_app07_ad936x_rtbw_pane_owner import _ReadbackEngine, _UnusedSweep
from tests.test_app07_shared_capture_schedule import pane
from tests.test_s15_live_rx_bridge import _make_frame
from tests.ui_v2.test_app06_hackrf_common_analyzer import graph as hackrf_graph
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph as tinysa_graph


_COST = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)


class _ObservedReadbackNative(_ObservedNative):
    """Fake coherent identity/readback and buffered RX on the SAME native owner."""

    def __init__(self, *, serial="app07-distinct-pluto", uri="ip:pluto-app07.local") -> None:
        super().__init__(serial=serial)
        self.routes = (uri,)
        self.engines: list[_ReadbackEngine] = []

    def PlutoFixedBandEngine(self, uri, timeout_ms, *, expected_serial=None):
        if expected_serial != self.serial:
            raise RuntimeError("fake receiver identity differs")
        engine = _ReadbackEngine(uri, timeout_ms)
        self.engines.append(engine)
        return engine


def _ad936x_graph(*, serial="app07-distinct-pluto", uri="ip:pluto-app07.local"):
    native = _ObservedReadbackNative(serial=serial, uri=uri)
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
                                      control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, analyzer_display=_UnusedSweep(), device_catalog=catalog))
    choice = graph.live.discover()[0]
    graph.live.select_device(choice.device_id)
    graph.live.apply_configuration(LiveConfiguration(
        center_hz=104e6, sample_rate_hz=20e6, analog_bandwidth_hz=10e6,
        gain_db=20, fft_size=4096, detector="sample",
        persistence_enabled=False, persistence_mode="disabled"))
    return native, service, graph, choice.device_id


class ThreeConcreteOwnerTests(unittest.TestCase):
    @staticmethod
    def wait(predicate) -> None:
        deadline = time.monotonic() + 4.0
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("bounded three-family publication timeout")
            time.sleep(0.001)

    def test_resource_graph_construction_is_inert_before_explicit_discover(self) -> None:
        native = _ObservedReadbackNative()
        service = NativeLiveSessionService(native)
        catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
                                          control_transaction=service.capability_control_transaction)
        graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=service, analyzer_display=_UnusedSweep(), device_catalog=catalog))
        try:
            self.assertIs(graph.live.analyzer_state, graph.analyzer.state)
            self.assertEqual(native.created, [])
            self.assertEqual(native.engines, [])
        finally:
            graph.live.shutdown()

    def test_all_empty_layout_creates_no_resource_or_capture_owner(self) -> None:
        layout = compile_pane_layout(tuple(PaneLayoutSlot(index) for index in range(1, 5)), (), {})
        leases = ReceiverLeaseManager(max_active_resources=4)
        self.assertIsNone(compose_v2_pane_resource_session(layout, (), {}, leases))
        self.assertEqual(leases.active_resource_count, 0)

    def test_two_pluto_routes_with_same_canonical_identity_cannot_run_as_parallel_resources(self) -> None:
        _, _, first, first_source = _ad936x_graph(uri="usb:pluto-fixture")
        _, _, second, second_source = _ad936x_graph(uri="ip:pluto-fixture")
        try:
            self.assertEqual(first_source, second_source)
            groups = (
                AcquisitionGroup("first", "resource-usb", (ReceiverEndpoint(
                    "rx-usb", first_source, "resource-usb", ReceiverChainSelection.RX1),)),
                AcquisitionGroup("second", "resource-ip", (ReceiverEndpoint(
                    "rx-ip", second_source, "resource-ip", ReceiverChainSelection.RX1),)),
            )
            slots = (
                PaneLayoutSlot(1, pane("pane-usb", "rx-usb", 100e6, 108e6,
                                       ReceiverBindingMode.DEDICATED_PARALLEL)),
                PaneLayoutSlot(2, pane("pane-ip", "rx-ip", 140e6, 148e6,
                                       ReceiverBindingMode.DEDICATED_PARALLEL)),
            )
            profile = PaneCaptureProfile(20e6, 10e6, "manual", 20.0, 4096, 2048,
                                         "hann", "sample", None, 10e6, _COST)
            layout = compile_pane_layout(slots, groups, {"capture": profile})
            leases = ReceiverLeaseManager(max_active_resources=4)
            with self.assertRaisesRegex(PaneResourceError, "cannot share one application"):
                compose_v2_pane_resource_session(layout, groups, {
                    "resource-usb": first, "resource-ip": first,
                }, leases)
            with self.assertRaises(PaneResourceError):
                compose_v2_pane_resource_session(layout, groups, {
                    "resource-usb": first, "resource-ip": second,
                }, leases)
            self.assertEqual(leases.active_resource_count, 0)
        finally:
            first.live.shutdown()
            second.live.shutdown()

    def test_parallel_three_family_owners_and_empty_fourth_slot(self) -> None:
        ad_native, ad_service, ad_graph, ad_source = _ad936x_graph()
        hf = hackrf_graph()
        ts = tinysa_graph()
        hf_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=hf.live, device_catalog=hf.catalog, analyzer_hackrf=hf.hackrf))
        ts_graph = build_v2_analyzer_application_graph(SimpleNamespace(
            live_sdr=ts.live, device_catalog=ts.catalog, analyzer_tinysa=ts.instrument))
        hf_graph.live.discover()
        hf_graph.live.select_device("source-hackrf")
        ts_choice = ts_graph.live.discover()[0]
        ts_graph.live.select_device(ts_choice.device_id)
        ts_selection = ts_graph.live.current_source_selection()
        assert ts_selection is not None
        assert ts_selection.selected is not None
        groups = (
            AcquisitionGroup("ad:group", "ad:physical", (
                ReceiverEndpoint("ad:rx1", ad_source, "ad:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("hf:group", "hf:physical", (
                ReceiverEndpoint("hf:rx1", "source-hackrf", "hf:physical", ReceiverChainSelection.RX1),)),
            AcquisitionGroup("ts:group", "ts:physical", (
                SpectrumTraceEndpoint("ts:trace", ts_selection.selected.device_id, "ts:physical"),)),
        )
        slots = (
            PaneLayoutSlot(1, pane("ad-pane", "ad:rx1", 100e6, 108e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(2, pane("hf-pane", "hf:rx1", 140e6, 148e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(3, pane("ts-pane", "ts:trace", 200e6, 210e6,
                                   ReceiverBindingMode.DEDICATED_PARALLEL)),
            PaneLayoutSlot(4),
        )
        profiles = {
            "ad": PaneCaptureProfile(20e6, 10e6, "manual", 20.0,
                                     4096, 2048, "hann", "sample", None, 10e6, _COST),
            "hf": HackrfRtbwPaneProfile(
                HackrfLiveRequest(144e6, 20e6, 15_000_000, 16, 20,
                                  fft_size=4096, hop_size=2048, detector="peak",
                                  source_id="source-hackrf"), 10e6, _COST),
            "ts": TinySaTracePaneProfile(
                3, 11e6, "ultra-default", _COST,
                request_template=TinySaSweepRequest(ts_selection.selected,
                    ts_selection.revision, 200_000_000, 210_000_000, 3)),
        }
        # Each occupied slot names its own typed profile; Empty has no request.
        slots = tuple(PaneLayoutSlot(slot.number, replace(slot.request,
            profile_id={1: "ad", 2: "hf", 3: "ts"}[slot.number]))
            if slot.request is not None else slot for slot in slots)
        layout = compile_pane_layout(slots, groups, profiles)
        self.assertEqual(layout.empty_slots, (4,))
        assert layout.schedule is not None
        leases = ReceiverLeaseManager(max_active_resources=4)
        session = compose_v2_pane_resource_session(layout, groups, {
            "ad:physical": ad_graph, "hf:physical": hf_graph, "ts:physical": ts_graph,
        }, leases)
        assert session is not None
        board = None
        delivery_port = None
        temporary = TemporaryDirectory()
        try:
            session.apply()
            for resource in ("ad:physical", "hf:physical", "ts:physical"):
                session.start_resource(resource)
            self.assertEqual(leases.active_resource_count, 3)
            self.assertTrue(ad_service.is_running() and hf.hackrf.is_running())
            self.assertTrue(ts.instrument.stop_required)
            center = ad_service.latest_snapshot().applied.applied.center_hz
            frame = _make_frame(1, center_hz=center, sample_rate_hz=20e6,
                                source_id=ad_source, config_generation=1)
            frame.frequencies_hz = center + (np.arange(4096) - 2048) * (20e6 / 4096)
            ad_native.engines[0].frames.append(frame)
            self.wait(lambda: ad_service.latest_snapshot().spectrum is not None)
            self.wait(lambda: hf.hackrf.current_snapshot().spectrum is not None)
            self.wait(lambda: ts.instrument.poll_latest().line is not None)
            deliveries = {
                resource: session.poll_resource(resource)
                for resource in ("ad:physical", "hf:physical", "ts:physical")
            }
            self.assertEqual(tuple(item.pane_id for item in deliveries["ad:physical"]), ("ad-pane",))
            self.assertEqual(tuple(item.pane_id for item in deliveries["hf:physical"]), ("hf-pane",))
            self.assertEqual(tuple(item.pane_id for item in deliveries["ts:physical"]), ("ts-pane",))
            self.assertEqual(deliveries["ad:physical"][0].bundle.unit, "dBFS/bin")
            self.assertEqual(deliveries["hf:physical"][0].bundle.unit, "dBFS/bin")
            self.assertEqual(deliveries["ts:physical"][0].bundle.unit, "dBm")
            application = QApplication.instance() or QApplication([])
            preparer = PaneDeliveryPreparer(layout, groups, PresentationAllocationBudget())
            settings = QSettings(str(Path(temporary.name) / "panes.ini"), QSettings.Format.IniFormat)
            board = IndependentPaneBoardV2(preparer, settings=settings)
            board.resize(1600, 920)
            board.show()
            application.processEvents()
            self.assertEqual((board.width(), board.height()), (1600, 920))
            prepared = tuple(preparer.prepare(deliveries[resource][0])
                             for resource in ("ad:physical", "hf:physical", "ts:physical"))
            queue = PaneFairDeliveryQueue(("ad-pane", "hf-pane", "ts-pane"))
            delivery_port = IndependentPaneDeliveryPort(board, queue)
            rendered: list[str] = []
            delivery_port.rendered.connect(rendered.append)
            for item in prepared:
                self.assertTrue(queue.offer(item))
                self.assertFalse(queue.offer(item))  # stale duplicate cannot queue
            delivery_port.start()
            self.wait(lambda: (application.processEvents() or True) and len(rendered) == 3)
            delivery_port.stop()
            self.assertEqual(rendered, ["ad-pane", "hf-pane", "ts-pane"])
            self.assertEqual(queue.metrics().delivered, 3)
            self.assertEqual(board.empty_slots, (4,))
            self.assertIsNone(board.pane(4))
            self.assertEqual(len(board.findChildren(AnalyzerPaneViewV2)), 3)
            for number, resource, pane_id in (
                    (1, "ad:physical", "ad-pane"),
                    (2, "hf:physical", "hf-pane"),
                    (3, "ts:physical", "ts-pane")):
                pane_view = board.pane(number)
                assert pane_view is not None
                self.assertIs(pane_view.last_bundle, deliveries[resource][0].bundle)
                self.assertEqual(preparer.bindings[pane_id].slot_number, number)
                self.assertEqual(pane_view.spectrum_scene.latest_frame.unit,
                                 "dBm" if number == 3 else "dBFS/bin")
                observed_start, observed_stop = pane_view.spectrum_scene.view_box.viewRange()[0]
                crop = deliveries[resource][0].crop
                self.assertAlmostEqual(observed_start, crop.start_hz, delta=1.0)
                self.assertAlmostEqual(observed_stop, crop.stop_hz, delta=1.0)
                self.assertGreaterEqual(pane_view.waterfall_pane.history_rows, 1)
            self.assertIsNot(board.pane(1).last_bundle, board.pane(2).last_bundle)
            self.assertIsNot(board.pane(2).last_bundle, board.pane(3).last_bundle)
            # A newer frame may update the line, but must not reset this
            # pane's user-chosen zoom or any neighboring pane's crop.
            first_pane = board.pane(1)
            assert first_pane is not None
            first_pane.spectrum_scene.view_box.setXRange(102e6, 106e6, padding=0)
            old_delivery = deliveries["ad:physical"][0]
            new_frame = replace(old_delivery.bundle.spectrum,
                                sequence=old_delivery.bundle.spectrum.sequence + 1)
            new_bundle = replace(old_delivery.bundle, spectrum=new_frame, identity=None,
                                 persistence=None, waterfall_line=None)
            self.assertTrue(queue.offer(preparer.prepare(replace(old_delivery, bundle=new_bundle))))
            delivery_port.tick_once()
            zoom_start, zoom_stop = first_pane.spectrum_scene.view_box.viewRange()[0]
            self.assertAlmostEqual(zoom_start, 102e6, delta=1.0)
            self.assertAlmostEqual(zoom_stop, 106e6, delta=1.0)
            second_pane = board.pane(2)
            assert second_pane is not None
            other_start, other_stop = second_pane.spectrum_scene.view_box.viewRange()[0]
            self.assertAlmostEqual(other_start, 140e6, delta=1.0)
            self.assertAlmostEqual(other_stop, 148e6, delta=1.0)
            screenshot = os.environ.get("SDR_APP07_PANE_SCREENSHOT")
            if screenshot:
                application.processEvents()
                self.assertTrue(board.grab().save(screenshot))
            board.select_slot(3)
            self.assertEqual(board.selected_slot, 3)
            with self.assertRaisesRegex(ValueError, "exact slot"):
                preparer.prepare(replace(deliveries["ad:physical"][0],
                                         crop=deliveries["hf:physical"][0].crop))
            with self.assertRaisesRegex(ValueError, "binding"):
                PreparedPaneDelivery(preparer.bindings["hf-pane"],
                                     deliveries["ad:physical"][0], prepared[0].spectrum,
                                     prepared[0].waterfall, prepared[0].persistence)
            self.assertEqual(session.stop_selected("ts-pane"), ("ts-pane",))
            self.assertEqual(leases.active_resource_count, 2)
            self.assertTrue(ad_service.is_running() and hf.hackrf.is_running())
            self.assertFalse(ts.instrument.stop_required)
            self.assertIsNotNone(board.pane(1).last_bundle)
            self.assertIsNotNone(board.pane(2).last_bundle)
        finally:
            self.assertEqual(session.stop_all(), ())
            self.assertEqual(leases.active_resource_count, 0)
            ad_graph.live.shutdown()
            hf_graph.live.shutdown()
            ts_graph.live.shutdown()
            if board is not None:
                if delivery_port is not None and delivery_port.running:
                    delivery_port.stop()
                board.release_presentation_after_shutdown()
                board.close()
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
