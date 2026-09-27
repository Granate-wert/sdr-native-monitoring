"""Retained common provider lifecycle and actual production DI; no hardware."""

from __future__ import annotations

import threading
import unittest
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    DeviceCapabilityBinding,
    DeviceFamily,
    build_device_capability_inventory,
)
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.hackrf_capability_adapter import HackrfBoardKind, HackrfCapabilityAdapter, HackrfReadOnlyProbe
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import (
    CapabilityCatalogError,
    CapabilityCatalogReason,
    SourceCapabilityCatalog,
)
from sdr_monitor.services.source_capability_providers import (
    HackrfCapabilityProvider,
    NativeLiveCapabilityProvider,
    TinySaCapabilityProvider,
)
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from tests.test_app06_pluto_observation_catalog import _Native
from tests.test_device_capability_contracts import _snapshot


class _Provider:
    def __init__(self, name="fixture.adapter", family=DeviceFamily.AD936X):
        self.adapter_id, self.family = name, family
        self.calls = []
        self.cleanup_pending = False
        self.fail = False
        self.close_fail = False
        self.runtime = AdapterRuntimeSnapshot(name, family, AdapterRuntimeAvailability.AVAILABLE, "fixture-contract")
        self.value = build_device_capability_inventory((), runtimes=(self.runtime,))

    def runtime_snapshot(self):
        return self.runtime

    def discover(self, *, startup_only):
        self.calls.append(("discover", startup_only))
        if self.fail:
            raise RuntimeError("PRIVATE route/SDK detail")
        return self.value

    def observe_source(self, source_id):
        self.calls.append(("observe", source_id))
        if self.fail:
            raise RuntimeError("PRIVATE route/SDK detail")
        return self.value

    def close(self):
        self.calls.append("close")
        if self.close_fail:
            raise RuntimeError("PRIVATE close detail")
        self.cleanup_pending = False


def _entry(provider):
    from tests.test_app06_capability_inventory_join import _identity
    snapshot = replace(_snapshot(), adapter_id=provider.adapter_id, family=provider.family)
    provider.value = build_device_capability_inventory((snapshot,),
        bindings=(DeviceCapabilityBinding("source-one", provider.family, provider.adapter_id, snapshot, _identity(snapshot)),),
        runtimes=(provider.runtime,))


class RetainedCapabilityCatalogTests(unittest.TestCase):
    def test_constructor_runtime_lookup_and_snapshot_open_no_provider(self):
        providers = (_Provider(), _Provider("fixture.second", DeviceFamily.HACKRF))
        catalog = SourceCapabilityCatalog(providers, control_transaction=nullcontext)
        self.assertEqual(len(catalog.snapshot().runtimes), 2)
        self.assertTrue(all(not provider.calls for provider in providers))

    def test_refresh_uses_shared_control_and_finite_exact_existing_inventory(self):
        provider = _Provider()
        _entry(provider)
        events = []
        @contextmanager
        def control():
            events.append("enter")
            yield
            events.append("exit")
        catalog = SourceCapabilityCatalog((provider,), control_transaction=control)
        result = catalog.refresh(startup_only=True)
        self.assertEqual(result, provider.value)
        self.assertEqual(events, ["enter", "exit"])
        self.assertEqual(provider.calls, [("discover", True)])
        self.assertIs(catalog.snapshot(), result)
        self.assertEqual(catalog.last_failures, ())

    def test_failed_close_blocks_all_other_families_and_no_hidden_retry(self):
        first, second = _Provider(), _Provider("fixture.second", DeviceFamily.HACKRF)
        def fail(*, startup_only):
            first.calls.append("discover")
            first.cleanup_pending = True
            raise RuntimeError("PRIVATE failed release")
        first.discover = fail
        first.close_fail = True
        catalog = SourceCapabilityCatalog((first, second), control_transaction=nullcontext)
        with self.assertRaises(CapabilityCatalogError) as failed:
            catalog.refresh()
        self.assertIs(failed.exception.reason, CapabilityCatalogReason.RELEASE_PENDING)
        self.assertNotIn("PRIVATE", str(failed.exception))
        self.assertEqual(second.calls, [])
        for call in (catalog.refresh, catalog.snapshot, lambda: catalog.observe_source("source-one")):
            with self.assertRaises(CapabilityCatalogError):
                call()
        self.assertEqual(first.calls, ["discover"])
        with self.assertRaises(CapabilityCatalogError):
            catalog.close()
        self.assertEqual(second.calls, [])
        first.close_fail = False
        catalog.close()
        self.assertFalse(catalog.cleanup_pending)
        self.assertEqual(first.calls, ["discover", "close", "close"])

    def test_closed_provider_error_does_not_erase_other_family_or_publish_stale_facts(self):
        first, second = _Provider(), _Provider("fixture.second", DeviceFamily.HACKRF)
        _entry(first)
        catalog = SourceCapabilityCatalog((first, second), control_transaction=nullcontext)
        self.assertEqual(len(catalog.refresh().snapshots), 1)
        first.fail = True
        result = catalog.refresh()
        self.assertEqual(result.snapshots, ())
        self.assertEqual(len(result.runtimes), 2)
        self.assertEqual(second.calls, [("discover", False), ("discover", False)])
        self.assertEqual(catalog.last_failures, ((first.adapter_id, CapabilityCatalogReason.PROVIDER_FAILED),))

    def test_foreign_family_or_runtime_cannot_enter_a_provider_slot(self):
        provider = _Provider()
        wrong = _Provider("other", DeviceFamily.HACKRF)
        provider.value = wrong.value
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        result = catalog.refresh()
        self.assertEqual(result.snapshots, ())
        self.assertEqual(result.runtimes, (provider.runtime,))
        self.assertEqual(catalog.last_failures, ((provider.adapter_id, CapabilityCatalogReason.PROVIDER_CONTRACT),))

    def test_constructor_bounds_provider_consumption_and_refuses_duplicate_ids(self):
        consumed = []
        def providers():
            for index in range(1000):
                consumed.append(index)
                yield _Provider(f"fixture.adapter{index}")
        with self.assertRaises(ValueError):
            SourceCapabilityCatalog(providers(), control_transaction=nullcontext)
        self.assertEqual(len(consumed), 17)
        for values in ((), (_Provider(), _Provider())):
            with self.subTest(count=len(values)), self.assertRaises(ValueError):
                SourceCapabilityCatalog(values, control_transaction=nullcontext)

    def test_global_binding_conflict_clears_previously_published_cache(self):
        first, second = _Provider(), _Provider("fixture.second", DeviceFamily.HACKRF)
        first.value = build_device_capability_inventory((), runtimes=(first.runtime,),
            bindings=(DeviceCapabilityBinding("source-one", first.family, first.adapter_id),))
        catalog = SourceCapabilityCatalog((first, second), control_transaction=nullcontext)
        self.assertEqual(len(catalog.refresh().bindings), 1)
        second.value = build_device_capability_inventory((), runtimes=(second.runtime,),
            bindings=(DeviceCapabilityBinding("source-one", second.family, second.adapter_id),))
        with self.assertRaises(CapabilityCatalogError) as error:
            catalog.refresh()
        self.assertIs(error.exception.reason, CapabilityCatalogReason.PROVIDER_CONTRACT)
        self.assertEqual(catalog.snapshot().bindings, ())
        self.assertEqual(len(catalog.snapshot().runtimes), 2)

    def test_missing_selected_source_has_no_provider_side_effect(self):
        provider = _Provider()
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        with self.assertRaises(CapabilityCatalogError) as error:
            catalog.observe_source("missing")
        self.assertIs(error.exception.reason, CapabilityCatalogReason.SOURCE_NOT_FOUND)
        self.assertEqual(provider.calls, [])

    def test_selected_probe_drops_old_facts_before_refusal(self):
        provider = _Provider()
        _entry(provider)
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        catalog.refresh()
        provider.fail = True
        with self.assertRaises(CapabilityCatalogError):
            catalog.observe_source("source-one")
        self.assertEqual(catalog.snapshot().snapshots, ())

    def test_concurrent_refresh_lookup_and_close_refuse_without_wait_or_other_factory(self):
        provider = _Provider()
        reached, proceed = threading.Event(), threading.Event()
        def hold(*, startup_only):
            reached.set()
            self.assertTrue(proceed.wait(3))
            return provider.value
        provider.discover = hold
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        worker = threading.Thread(target=catalog.refresh)
        worker.start()
        try:
            self.assertTrue(reached.wait(1))
            for call in (catalog.refresh, catalog.snapshot, catalog.close):
                with self.assertRaises(CapabilityCatalogError) as failed:
                    call()
                self.assertIs(failed.exception.reason, CapabilityCatalogReason.BUSY)
        finally:
            proceed.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_native_common_control_excludes_engine_and_cannot_stop_it(self):
        native = _Native()
        live = NativeLiveSessionService(native)
        catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),), control_transaction=live.capability_control_transaction)
        live._engine = object()
        try:
            for call in (catalog.refresh, catalog.close):
                with self.assertRaises(LiveAdmissionRejected):
                    call()
            self.assertEqual(native.created, [])
            self.assertEqual(native.scans, [])
            self.assertIsNotNone(live._engine)
        finally:
            live._engine = None

    def test_native_control_is_shared_with_raw_start_not_queued(self):
        live = NativeLiveSessionService(_Native())
        held, proceed = threading.Event(), threading.Event()
        def hold():
            with live._recording_transaction_lock:
                held.set()
                self.assertTrue(proceed.wait(3))
        worker = threading.Thread(target=hold)
        worker.start()
        try:
            self.assertTrue(held.wait(1))
            with self.assertRaises(LiveAdmissionRejected), live.capability_control_transaction():
                self.fail("must not queue")
        finally:
            proceed.set()
            worker.join(3)

    def test_native_control_refuses_sweep_poller_and_unresolved_stream_release(self):
        live = NativeLiveSessionService(_Native())
        for name, busy, cleared in (("_sweep_lease_active", True, False), ("_poller", object(), None),
                                    ("_stream_release_failed", True, False)):
            with self.subTest(owner=name):
                setattr(live, name, busy)
                try:
                    with self.assertRaises(LiveAdmissionRejected), live.capability_control_transaction():
                        self.fail("must not take foreign ownership")
                    self.assertIs(getattr(live, name), busy)
                    self.assertEqual(live._native.created, [])
                finally:
                    setattr(live, name, cleared)

    def test_pending_pluto_observation_can_be_explicitly_closed_without_live_stop(self):
        native = _Native()
        native.disconnect_error = True
        live = NativeLiveSessionService(native)
        catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),), control_transaction=live.capability_control_transaction)
        with self.assertRaises(CapabilityCatalogError):
            catalog.refresh()
        self.assertTrue(catalog.cleanup_pending)
        self.assertEqual(len(native.created), 1)
        native.created[-1].disconnect_error = False
        native.disconnect_error = False
        with patch.object(live, "stop", side_effect=AssertionError("No Live Stop")):
            catalog.close()
        self.assertFalse(catalog.cleanup_pending)
        self.assertEqual(len(native.created), 1)
        self.assertEqual(len(catalog.refresh(startup_only=True).bindings), 1)
        live.close_live()

    def test_hackrf_retained_adapter_is_not_replaced_after_failed_release(self):
        class Port:
            def __init__(self):
                self.fail = True
                self.calls = []
            def probe(self):
                self.calls.append("probe")
                return HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, (0, 0, 1, 2), "fw", 0x0107)
            def close(self):
                self.calls.append("close")
                if self.fail:
                    raise RuntimeError("PRIVATE cleanup")
        port = Port()
        adapter = HackrfCapabilityAdapter(lambda: port)
        runtime = AdapterRuntimeSnapshot("native.libhackrf.hackrf_one.v1", DeviceFamily.HACKRF,
                                          AdapterRuntimeAvailability.AVAILABLE, "fixture-factory2")
        # Use the actual adapter ID rather than a synthesized registry namespace.
        from sdr_monitor.services.hackrf_capability_adapter import HACKRF_LIBHACKRF_ADAPTER_ID
        runtime = replace(runtime, adapter_id=HACKRF_LIBHACKRF_ADAPTER_ID)
        catalog = SourceCapabilityCatalog((HackrfCapabilityProvider(adapter, runtime),), control_transaction=nullcontext)
        with self.assertRaises(CapabilityCatalogError):
            catalog.refresh()
        with self.assertRaises(CapabilityCatalogError):
            catalog.refresh()
        self.assertEqual(port.calls, ["probe", "close"])
        port.fail = False
        catalog.close()
        self.assertEqual(len(catalog.refresh().snapshots), 1)

    def test_tinysa_discovery_is_pnp_only_and_preserves_unverified_candidate(self):
        pnp = SimpleNamespace(device="COM31", vid=0x0483, pid=0x5740, serial_number=None, location="fixture")
        backend = TinySaSerialSourceBackend(inventory_provider=lambda: (pnp,))
        provider = TinySaCapabilityProvider(backend, port_factory=lambda _: self.fail("No version probe on Discover"))
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        result = catalog.refresh()
        self.assertEqual(result.snapshots, ())
        self.assertEqual(len(result.bindings), 1)
        self.assertIsNone(result.bindings[0].identity_key)
        self.assertNotIn("COM31", repr(result))

    def test_actual_default_service_graph_contains_one_catalog_without_discovery(self):
        live = NativeLiveSessionService(_Native())
        from sdr_monitor.services.sdr_application_services import build_default_sdr_services
        with patch("sdr_monitor.services.sdr_application_services._default_live_service", return_value=live), \
             patch("sdr_monitor.services.tinysa_serial_source_backend.list_ports.comports", side_effect=AssertionError("No PnP at construction")):
            services = build_default_sdr_services()
        self.assertIs(services.live_sdr, live)
        self.assertIsNotNone(services.device_catalog)
        self.assertEqual(len(services.device_catalog.snapshot().runtimes), 3)
        self.assertEqual(live._native.created, [])
        self.assertEqual(live._native.scans, [])


if __name__ == "__main__":
    unittest.main()
