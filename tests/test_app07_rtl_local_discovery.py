"""Explicit USB discovery is not startup; no real SDK, USB or RX in tests."""

from contextlib import nullcontext
import unittest
from unittest.mock import Mock

from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.services.rtl_capability_provider import (
    RtlCapabilityProvider, RtlRuntimeProvision,
)
from sdr_monitor.services.source_capability_catalog import (
    CapabilityCatalogReason, SourceCapabilityCatalog,
)
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from tests.test_app06_retained_capability_catalog import _Provider
from tests.test_app07_rtl_product_route import _Native
from tests.ui_v2.test_app06_common_source_selection import _graph


class LocalDiscoveryTests(unittest.TestCase):
    def rtl(self):
        native = _Native()
        native.rtl_enumerate_candidates = Mock(wraps=native.rtl_enumerate_candidates)
        return native, RtlCapabilityProvider(RtlRuntimeProvision(native, object(), "a" * 64, "b" * 64))

    def graph(self):
        graph = _graph()
        native, rtl = self.rtl()
        # Retain the real application/source service and existing control
        # transaction; substitute only the fake SDK provider set.
        graph.catalog._providers = (*graph.catalog._providers, rtl)
        self.addCleanup(graph.application.shutdown)
        return graph, native

    def test_user_usb_discovery_includes_rtl_without_ip_or_rx(self):
        graph, rtl = self.graph()
        choices = graph.application.discover(local_only=True)
        self.assertEqual(tuple(choice.family for choice in choices),
                         (DeviceFamily.AD936X, DeviceFamily.HACKRF,
                          DeviceFamily.TINYSA, DeviceFamily.RTL_SDR))
        self.assertEqual(graph.native.scans, ["usb"])
        rtl.rtl_enumerate_candidates.assert_called_once()
        self.assertEqual(rtl.create_calls, 0)
        self.assertIsNone(graph.sources.current().selected)
        self.assertEqual(graph.native.engines, [])

    def test_startup_still_skips_rtl_and_full_scan_still_uses_ip(self):
        graph, rtl = self.graph()
        startup = graph.application.discover(startup=True)
        self.assertNotIn(DeviceFamily.RTL_SDR, tuple(choice.family for choice in startup))
        rtl.rtl_enumerate_candidates.assert_not_called()
        choices = graph.application.discover()
        self.assertIn(DeviceFamily.RTL_SDR, tuple(choice.family for choice in choices))
        self.assertEqual(graph.native.scans, ["usb", "usb,ip"])
        rtl.rtl_enumerate_candidates.assert_called_once()
        self.assertEqual(rtl.create_calls, 0)

    def test_incompatible_discovery_intents_reject_before_effects(self):
        graph, rtl = self.graph()
        revision = graph.sources.current().revision
        for call in (graph.application.discover, graph.sources.discover):
            with self.assertRaises(ValueError):
                call(startup=True, local_only=True)
        with self.assertRaises(ValueError):
            graph.catalog.refresh(startup_only=True, local_only=True)
        with self.assertRaises(ValueError):
            graph.catalog.refresh(local_only=1)
        self.assertEqual(graph.sources.current().revision, revision)
        self.assertEqual(graph.native.scans, [])
        rtl.rtl_enumerate_candidates.assert_not_called()

    def test_missing_ad_local_contract_never_falls_back_to_network(self):
        ad = _Provider()
        native, rtl = self.rtl()
        catalog = SourceCapabilityCatalog((ad, rtl), control_transaction=nullcontext)
        result = catalog.refresh(local_only=True)
        self.assertEqual(ad.calls, [])
        self.assertEqual(catalog.last_failures,
                         ((ad.adapter_id, CapabilityCatalogReason.PROVIDER_CONTRACT),))
        self.assertEqual(tuple(binding.family for binding in result.bindings), (DeviceFamily.RTL_SDR,))
        native.rtl_enumerate_candidates.assert_called_once()

    def test_local_native_provider_keeps_usb_only_method_and_inventory(self):
        graph, _rtl = self.graph()
        provider = NativeLiveCapabilityProvider(graph.live)
        result = provider.discover_local()
        self.assertEqual(graph.native.scans, ["usb"])
        self.assertEqual(result, graph.live.capability_inventory())
        self.assertEqual(graph.native.engines, [])
