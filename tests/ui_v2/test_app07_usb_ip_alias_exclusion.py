"""SAME application admission and observer, mocked SDK only; no physical RX."""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.domain.pluto_connection import PlutoUsbConnectionExpectation, normalized_pluto_serial
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.parallel_receiver_identity import validate_parallel_receiver_identity
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneGraphPoolError, PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session, PaneUserStageError
from tests.test_app07_ad936x_rtbw_pane_owner import _ReadbackEngine, _UnusedSweep
from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative


USB4 = "usb:2.24.5"
USB3 = "usb:2.25.5"
ETH3 = "ip:192.168.1.54"


class UsbNative(_ObservedReadbackNative):
    PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION = 1
    PlutoExpectedUsbConnection = SimpleNamespace

    def PlutoDevice(self, uri, timeout_ms, *, expected_serial=None, expected_usb_connection=None):
        device = super().PlutoDevice(uri, timeout_ms, expected_serial=expected_serial)
        original = device.probe

        def probe():
            result = original()
            result.context_name = "usb" if uri.startswith("usb:") else "network"
            result.backend_uri = uri
            result.usb_vendor_id, result.usb_product_id = "0456", "b673"
            result.usb_serial = self.serial
            if uri.startswith("usb:"):
                result.backend_uri = getattr(self, "backend_override", uri)
            return result

        device.probe = probe
        topology = device.receiver_topology

        def receiver_topology():
            result = topology()
            if uri.startswith("usb:"):
                for element in result.input_scan_elements:
                    element.significant_bits = getattr(self, "usb_bits", element.significant_bits)
            return result

        device.receiver_topology = receiver_topology
        if expected_usb_connection is not None:
            actual = PlutoUsbConnectionExpectation.from_probe(probe())
            if any(getattr(actual, field) != getattr(expected_usb_connection, field)
                   for field in actual.__dataclass_fields__):
                device.disconnect()
                raise RuntimeError("mock owned USB connection changed")
        return device

    def PlutoFixedBandEngine(self, uri, timeout_ms, *, expected_serial=None, expected_usb_connection=None):
        if expected_serial != normalized_pluto_serial(self.serial):
            raise RuntimeError("mock engine identity changed")
        if uri.startswith("ip:") and expected_usb_connection is not None:
            raise AssertionError("Ethernet owner must not receive USB acquisition expectation")
        engine = _ReadbackEngine(uri, timeout_ms)
        self.engines.append(engine)
        return engine


def fixture(uri, serial):
    native = UsbNative(uri=uri, serial=serial)
    live = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),),
                                      control_transaction=live.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=live, device_catalog=catalog, analyzer_display=_UnusedSweep()))
    source = graph.live.discover(local_only=True)[0].device_id
    return native, graph, source


def staged(*, known_first=False):
    usb = fixture(USB4, "")
    eth = fixture(USB3, "ad3-known-serial")
    graphs = {"pane-resource-1": eth[1] if known_first else usb[1],
              "pane-resource-2": usb[1] if known_first else eth[1]}
    pool = PaneProductGraphPool(graphs.__getitem__)
    unknown = PaneSlotDraft(1, usb[2], 100e6, 108e6, operational_route=Route(USB4))
    known = PaneSlotDraft(2, eth[2], 140e6, 148e6, operational_route=Route(ETH3))
    drafts = (replace(known, number=1), replace(unknown, number=2)) if known_first else (unknown, known)
    prepared = prepare_user_pane_session(drafts,
                                         pool_factory=lambda: pool)
    return usb, eth, pool, prepared


def close(pool, prepared):
    if prepared.handle.pump.activated:
        for future in prepared.handle.pump.stop_all().values():
            future.result(5.0)
    else:
        assert prepared.handle.session.stop_all() == ()
    prepared.handle.shutdown_after_stop()
    assert pool.staged_resource_ids == ()


class UsbIpAliasExclusionTests(unittest.TestCase):
    def test_both_staging_orders_use_one_existing_owner_per_resource(self):
        for known_first in (False, True):
            with self.subTest(known_first=known_first):
                usb, eth, pool, prepared = staged(known_first=known_first)
                try:
                    witness = pool._usb_aliases[eth[2]]
                    known_resource = "pane-resource-1" if known_first else "pane-resource-2"
                    selected = pool._selections[known_resource].selected
                    self.assertIsNone(selected.usb_connection)
                    self.assertEqual(witness.usb_uri, USB3)
                    self.assertEqual(witness.route.uri, ETH3)
                    self.assertEqual(witness.calibration_identity, selected.binding.calibration_identity)
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                    self.assertTrue(all(device.calls[-1] == "disconnect"
                                        for device in usb[0].created + eth[0].created))
                    apply_user_pane_session(prepared)
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                    prepared.handle.pump.start_resource("pane-resource-1").result(5.0)
                    prepared.handle.pump.start_resource("pane-resource-2").result(5.0)
                    self.assertEqual(len(usb[0].engines), 1)
                    self.assertEqual(len(eth[0].engines), 1)
                    self.assertEqual(eth[0].engines[0].uri, ETH3)
                    self.assertIs(eth[1].services.live_sdr._operational_usb_alias, witness)
                finally:
                    close(pool, prepared)

    def test_old_guard_still_refuses_without_separate_witness(self):
        usb, eth, pool, prepared = staged()
        try:
            choices = [selection.selected for selection in pool._selections.values()]
            sources = {item.device_id for item in choices}
            args = (sources, {item.device_id: item.binding.identity_key for item in choices},
                    {item.device_id: item.family for item in choices},
                    {item.device_id: item.usb_connection for item in choices})
            with self.assertRaisesRegex(ValueError, "unidentified"):
                validate_parallel_receiver_identity(*args)
            validate_parallel_receiver_identity(*args, pool._usb_aliases)
            with self.assertRaises(ValueError):
                validate_parallel_receiver_identity(*args, {usb[2]: pool._usb_aliases[eth[2]]})
        finally:
            close(pool, prepared)

    def test_no_known_usb_observation_keeps_mixed_layout_refused(self):
        usb = fixture(USB4, "")
        eth = fixture(USB3, "ad3-known-serial")
        eth[0].routes = ()
        pool = PaneProductGraphPool(lambda resource: usb[1] if resource == "first" else eth[1])
        try:
            pool.stage("first", usb[2])
            with self.assertRaises(PaneGraphPoolError):
                pool.stage("second", eth[2], operational_route=Route(ETH3))
            self.assertEqual(usb[0].engines + eth[0].engines, [])
        finally:
            pool.close()

    def test_two_interfaces_on_same_usb_device_refuse_even_with_known_ip_witness(self):
        usb = fixture(USB4, "")
        eth = fixture("usb:2.24.6", "ad3-known-serial")
        pool = PaneProductGraphPool(lambda resource: usb[1] if resource == "first" else eth[1])
        try:
            pool.stage("first", usb[2])
            with self.assertRaises(PaneGraphPoolError):
                pool.stage("second", eth[2], operational_route=Route(ETH3))
            self.assertEqual(usb[0].engines + eth[0].engines, [])
        finally:
            pool.close()

    def test_witness_cannot_be_forged_by_copy_or_attached_to_unknown_source(self):
        usb, eth, pool, prepared = staged()
        try:
            owner = eth[1].services.live_sdr
            witness = pool._usb_aliases[eth[2]]
            with self.assertRaises(LiveAdmissionRejected):
                owner.validate_operational_usb_alias(replace(witness))
            for change in ({"source_id": usb[2]},
                           {"route": Route(USB4)},
                           {"connection": replace(witness.connection, usb_serial="")},
                           {"connection": replace(witness.connection, usb_serial="other")}):
                with self.subTest(change=change), self.assertRaises((ValueError, LiveAdmissionRejected)):
                    changed = replace(witness, **change)
                    owner.validate_operational_usb_alias(changed)
            owner.validate_operational_usb_alias(witness)
        finally:
            close(pool, prepared)

    def test_peer_selection_revision_change_refuses_apply_before_any_configuration(self):
        for resource in ("pane-resource-1", "pane-resource-2"):
            with self.subTest(resource=resource):
                usb, eth, pool, prepared = staged()
                try:
                    graph = pool.graph_for(resource)
                    captured = graph.live.current_source_selection()
                    with (patch.object(graph.live, "current_source_selection", return_value=replace(
                            captured, revision=captured.revision + 1)),
                          patch.object(usb[1].live, "apply_configuration") as first,
                          patch.object(eth[1].live, "apply_configuration") as second):
                        with self.assertRaises(PaneUserStageError):
                            apply_user_pane_session(prepared)
                        first.assert_not_called()
                        second.assert_not_called()
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                finally:
                    close(pool, prepared)

    def test_peer_reselection_after_apply_refuses_capture_without_resetting_other_graph(self):
        for changed_resource, started_resource in (("pane-resource-1", "pane-resource-2"),
                                                  ("pane-resource-2", "pane-resource-1")):
            with self.subTest(changed_resource=changed_resource):
                usb, eth, pool, prepared = staged()
                try:
                    apply_user_pane_session(prepared)
                    graph = pool.graph_for(changed_resource)
                    captured = graph.live.current_source_selection()
                    before = pool.graph_for(started_resource).live.current_source_selection()
                    graph.sources._state = replace(captured, revision=captured.revision + 1)
                    with self.assertRaisesRegex(RuntimeError, "pane receiver Start did not confirm"):
                        prepared.handle.pump.start_resource(started_resource).result(5.0)
                    self.assertIs(pool.graph_for(started_resource).live.current_source_selection(), before)
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                finally:
                    close(pool, prepared)

    def test_changed_native_alias_before_apply_revokes_witness_and_no_engine_starts(self):
        usb, eth, pool, prepared = staged()
        try:
            native, graph, _ = eth
            witness = pool._usb_aliases[eth[2]]
            old_snapshot = graph.services.live_sdr.latest_snapshot()
            native.serial = native.topology_serial = "changed-device"
            with (patch.object(usb[1].live, "apply_configuration") as first,
                  patch.object(graph.live, "apply_configuration") as second):
                with self.assertRaises(PaneUserStageError):
                    apply_user_pane_session(prepared)
                first.assert_not_called()
                second.assert_not_called()
            self.assertIs(graph.services.live_sdr.latest_snapshot(), old_snapshot)
            self.assertIsNone(graph.services.live_sdr._operational_usb_alias)
            with self.assertRaises(LiveAdmissionRejected):
                graph.services.live_sdr.validate_operational_usb_alias(witness)
            self.assertEqual(usb[0].engines + native.engines, [])
        finally:
            close(pool, prepared)

    def test_usb_capability_change_before_start_is_not_silently_accepted(self):
        usb, eth, pool, prepared = staged()
        try:
            apply_user_pane_session(prepared)
            eth[0].conflicting_routes.add(USB3)
            with self.assertRaisesRegex(RuntimeError, "pane receiver Start did not confirm"):
                prepared.handle.pump.start_resource("pane-resource-2").result(5.0)
            self.assertIsNone(eth[1].services.live_sdr._operational_usb_alias)
            self.assertEqual(usb[0].engines + eth[0].engines, [])
        finally:
            close(pool, prepared)

    def test_usb_alias_cleanup_failure_remains_reachable_and_refuses_rx(self):
        usb, eth, pool, prepared = staged()
        try:
            apply_user_pane_session(prepared)
            eth[0].disconnect_error = True
            with self.assertRaisesRegex(RuntimeError, "pane receiver Start did not confirm"):
                prepared.handle.pump.start_resource("pane-resource-2").result(5.0)
            self.assertTrue(eth[1].services.live_sdr.capability_cleanup_pending)
            self.assertEqual(usb[0].engines + eth[0].engines, [])
            # Repair only the MOCK SDK object to prove explicit retained close.
            pending = eth[1].services.live_sdr._observation_owner._pending
            pending.disconnect_error = False
            eth[0].disconnect_error = False
        finally:
            close(pool, prepared)

    def test_actual_usb_scan_format_or_connection_change_refuses_before_engine(self):
        for changed in ("usb_bits", "backend_override"):
            with self.subTest(changed=changed):
                usb, eth, pool, prepared = staged()
                try:
                    apply_user_pane_session(prepared)
                    setattr(eth[0], changed, 11 if changed == "usb_bits" else "usb:2.26.5")
                    with self.assertRaisesRegex(RuntimeError, "pane receiver Start did not confirm"):
                        prepared.handle.pump.start_resource("pane-resource-2").result(5.0)
                    self.assertIsNone(eth[1].services.live_sdr._operational_usb_alias)
                    self.assertEqual(usb[0].engines + eth[0].engines, [])
                finally:
                    close(pool, prepared)

    def test_reselect_same_ip_revokes_old_witness_even_when_strings_match(self):
        usb, eth, pool, prepared = staged()
        try:
            witness = pool._usb_aliases[eth[2]]
            eth[1].live.select_manual_uri(ETH3)
            with self.assertRaises(LiveAdmissionRejected):
                eth[1].services.live_sdr.validate_operational_usb_alias(witness)
            with self.assertRaises(PaneUserStageError):
                apply_user_pane_session(prepared)
            self.assertEqual(usb[0].engines + eth[0].engines, [])
        finally:
            close(pool, prepared)


if __name__ == "__main__":
    unittest.main()
