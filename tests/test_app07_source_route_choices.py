"""Low-rate discovered Pluto route choices; no hardware/RX or UI ownership."""

from __future__ import annotations

from dataclasses import replace
import unittest

from sdr_monitor.application.analyzer_sources import _descriptor_operational_routes
from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.device_capabilities import DeviceCapabilityBinding, DeviceFamily
from sdr_monitor.domain.pluto_route_intent import PlutoOperationalRouteIntent as Route
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


USB = "usb:2.25.5"
IP = "ip:pluto-app07.local"


class SourceRouteChoiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.native, self.graph = _ad_graph(uri=USB)
        self.addCleanup(self.graph.live.shutdown)

    def test_exact_descriptor_routes_preserve_binding_and_no_extra_observation(self) -> None:
        self.native.routes = (USB, IP)
        choice = self.graph.live.discover(local_only=True)[0]
        descriptor = self.graph.services.live_sdr.discovered_devices()[0]
        self.assertEqual(choice.operational_routes, (Route(USB), Route(IP)))
        self.assertEqual((descriptor.uri, *descriptor.alternate_uris), (USB, IP))
        self.assertIs(choice.binding, self.graph.services.device_catalog.snapshot().binding_for_source(choice.device_id))
        count = len(self.native.created)
        for _ in range(3):
            self.assertIs(self.graph.sources.current().choices[0], choice)
            self.assertEqual(_descriptor_operational_routes(descriptor), choice.operational_routes)
        self.assertEqual(len(self.native.created), count)
        self.assertEqual(self.native.engines, [])

    def test_typed_routes_are_immutable_unique_and_family_scoped(self) -> None:
        choice = self.graph.live.discover()[0]
        for routes in ([Route(USB)], (USB,), (Route(USB), Route(USB)), (Route(USB),) * 33):
            with self.subTest(routes=routes), self.assertRaises(ValueError):
                replace(choice, operational_routes=routes)
        binding = DeviceCapabilityBinding("foreign", DeviceFamily.HACKRF, "fixture.hackrf")
        with self.assertRaises(ValueError):
            AnalyzerSourceChoice(binding, None, "HackRF", "USB", operational_routes=(Route(USB),))

    def test_invalid_legacy_or_oversized_descriptor_publishes_no_route_evidence(self) -> None:
        self.graph.live.discover()
        descriptor = self.graph.services.live_sdr.discovered_devices()[0]
        for invalid in (
            replace(descriptor, uri="usb:fixture"),
            replace(descriptor, alternate_uris=("ip:",)),
            replace(descriptor, alternate_uris=tuple(f"ip:host{index}" for index in range(32))),
        ):
            with self.subTest(invalid=invalid):
                self.assertEqual(_descriptor_operational_routes(invalid), ())
        self.assertEqual(_descriptor_operational_routes(None), ())
        repeated = replace(descriptor, alternate_uris=(USB, IP, IP))
        self.assertEqual(_descriptor_operational_routes(repeated), (Route(USB), Route(IP)))

    def test_manual_uri_is_exact_only_and_does_not_publish_inferred_aliases(self) -> None:
        self.graph.live.discover()
        self.graph.live.select_manual_uri(IP)
        state = self.graph.sources.current()
        self.assertIsNotNone(state.selected)
        self.assertEqual(state.selected.operational_routes, (Route(IP),))
        self.assertEqual(self.native.engines, [])

    def test_new_discovery_replaces_route_choices_without_changing_old_snapshot(self) -> None:
        self.native.routes = (USB, IP)
        first = self.graph.live.discover(local_only=True)[0]
        old_state = self.graph.sources.current()
        self.native.routes = (USB,)
        current = self.graph.live.discover(local_only=True)[0]
        self.assertGreater(self.graph.sources.current().revision, old_state.revision)
        self.assertEqual(first.operational_routes, (Route(USB), Route(IP)))
        self.assertEqual(current.operational_routes, (Route(USB),))
        self.assertIsNot(current, first)


if __name__ == "__main__":
    unittest.main()
