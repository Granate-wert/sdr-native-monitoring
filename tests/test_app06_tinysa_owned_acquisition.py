"""Same-owner fake-serial acquisition/retention; not physical or GUI evidence."""

from __future__ import annotations

import threading
import unittest
from contextlib import nullcontext
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from sdr_monitor.services.source_capability_catalog import CapabilityCatalogError, SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import TinySaCapabilityProvider
from sdr_monitor.services.tinysa_capability_adapter import (
    TinySaCapabilityAdapter,
    TinySaModel,
    TinySaReadOnlyProbe,
)
from sdr_monitor.services.tinysa_owned_acquisition import TinySaOwnedAcquisition
from sdr_monitor.services.tinysa_serial_settings_port import TinySaSerialSettingsCommandPort
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
    collect_tinysa_scanraw_trace,
)
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    R11W_TINYSA_SETTINGS_CONFIRMATION,
    TinySaSettingsApplyError,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
)


def _pnp(serial="fixture-one", route="COM31"):
    return SimpleNamespace(device=route, vid=0x0483, pid=0x5740, serial_number=serial, location="fixture-location")


class _Serial:
    def __init__(self):
        self.is_open = False
        self.dtr = self.rts = True
        self.open_error = self.close_error = self.claim_open = False
        self.report_open_on_error = True
        self.calls = []
        self.version = b"tinySA4 v1.4-fixture\rch> "
        self.payload = b""
        self.response = b""
        self.short_command = None
        self.zero = 174
        self.on_write = lambda _: None

    def open(self):
        self.calls.append(("open", self.dtr, self.rts))
        self.is_open = True
        if self.open_error:
            self.is_open = self.report_open_on_error
            raise RuntimeError("PRIVATE partial open")

    def close(self):
        self.calls.append("close")
        if self.close_error:
            raise RuntimeError("PRIVATE close")
        self.is_open = self.claim_open

    def reset_input_buffer(self):
        self.calls.append("reset")
        self.response = b""

    def write(self, data):
        self.calls.append(("write", data))
        self.on_write(data)
        if data == b"version\r":
            self.response = self.version
        elif data == b"zero ?\r":
            self.response = f"zero ?\r\n{self.zero}dBm\r\nch> ".encode("ascii")
        elif data.startswith(b"scanraw "):
            points = int(data.split()[3])
            self.response = self.payload or b"{" + b"x\x80\x0c" * points + b"}"
        else:
            self.response = data + b"\nch> "
        return len(data) - int(data == self.short_command)

    def flush(self):
        self.calls.append("flush")

    def read(self, size):
        self.calls.append(("read", size))
        chunk, self.response = self.response[:size], self.response[size:]
        return chunk


class TinySaOwnedAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.inventory = [_pnp()]
        self.backend = TinySaSerialSourceBackend(inventory_provider=lambda: tuple(self.inventory))
        self.endpoint = self.backend.discover_endpoints()[0]
        self.expected = TinySaCapabilityAdapter.map_probe(TinySaReadOnlyProbe(
            TinySaModel.ULTRA, self.endpoint.identity_key, "tinySA4 v1.4-fixture"))
        self.serial = _Serial()
        self.factory = Mock(return_value=self.serial)
        self.owner = TinySaOwnedAcquisition(self.backend, self.endpoint, self.expected, serial_factory=self.factory)
        self.request = TinySaScanRawRequest(TinySaModel.ULTRA, 87_500_000, 108_000_000, 3)

    def test_inert_reservation_then_one_same_handle_version_zero_and_measurement(self):
        self.factory.assert_not_called()
        self.assertTrue(self.owner.cleanup_pending)
        result = self.owner.collect(self.request)
        self.factory.assert_called_once_with("COM31")
        writes = [call[1] for call in self.serial.calls if isinstance(call, tuple) and call[0] == "write"]
        self.assertEqual(writes, [b"version\r", b"zero ?\r", self.request.command])
        self.assertEqual(self.serial.calls[0], ("open", False, False))
        self.assertEqual(self.serial.calls.count("close"), 1)
        self.assertFalse(self.owner.cleanup_pending)
        self.assertFalse(self.serial.is_open)
        self.assertTrue(result.port_closed)
        self.assertEqual((result.measurement_commands, result.device_scans_requested, result.retries), (1, 1, 0))
        self.assertEqual(result.trace.unit, "dBm")
        self.assertEqual(result.trace.value_provenance, "device_reported_trace")
        self.assertFalse(result.trace.raw_iq_available)
        self.assertFalse(result.trace.values_dbm.flags.writeable)
        self.assertNotIn("COM31", repr(result))
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.owner.close()
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_exact_10001_points_30005_bytes_stop_exclusive_and_observed_zero(self):
        self.serial.zero = 128
        request = replace(self.request, points=10_001)
        result = self.owner.collect(request)
        self.assertEqual(result.response_bytes_read, 30_005)
        self.assertEqual(result.trace.values_dbm.size, 10_001)
        self.assertTrue(np.all(result.trace.values_dbm == -28))
        step = (request.stop_frequency_hz - request.start_frequency_hz) // request.points
        self.assertEqual(result.trace.frequencies_hz[-1], request.start_frequency_hz + 10_000 * step)
        self.assertLess(result.trace.frequencies_hz[-1], request.stop_frequency_hz)
        self.assertEqual(max(call[1] for call in self.serial.calls if isinstance(call, tuple) and call[0] == "read"), 1024)

    def test_model_and_range_mismatch_refuse_before_serial(self):
        for request in (replace(self.request, model=TinySaModel.BASIC),
                        TinySaScanRawRequest(TinySaModel.BASIC, 300_000_000, 400_000_000, 3)):
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.owner.collect(request)
        self.factory.assert_not_called()
        self.owner.close()

    def test_changed_model_or_firmware_closes_without_zero_or_scan(self):
        for version in (b"tinySA v1.4-fixture\rch> ", b"tinySA4 v1.5-fixture\rch> "):
            with self.subTest(version=version):
                self.setUp()
                self.serial.version = version
                with self.assertRaises(TinySaTraceCollectionError) as error:
                    self.owner.collect(self.request)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertFalse(self.owner.cleanup_pending)
                self.assertEqual([c[1] for c in self.serial.calls if isinstance(c, tuple) and c[0] == "write"], [b"version\r"])

    def test_bounded_version_rejects_extra_prompt_binary_path_and_long_text(self):
        for version in (b"tinySA4\rch> ch> ", b"tinySA4\rch> \x00", b"tinySA4 C:\\PRIVATE\rch> ",
                        b"tinySA4 " + b"x" * 130 + b"\rch> ", b"tinySA4 " + b"x" * 4100 + b"\rch> "):
            with self.subTest(length=len(version)):
                self.setUp()
                self.serial.version = version
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.assertFalse(self.owner.cleanup_pending)
                self.assertEqual(self.serial.calls.count("close"), 1)

    def test_version_deadline_and_incomplete_frame_are_absolute_not_retries(self):
        ticks = iter(range(1, 1000))
        self.serial.version = b"tinySA4 v1.4-fixture\r"
        self.owner = TinySaOwnedAcquisition(self.backend, self.endpoint, self.expected,
            serial_factory=self.factory, monotonic=lambda: next(ticks) / 10)
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.assertEqual([c[1] for c in self.serial.calls if isinstance(c, tuple) and c[0] == "write"], [b"version\r"])
        self.assertLess(len(self.serial.calls), 40)
        self.assertFalse(self.owner.cleanup_pending)
        self.setUp()
        self.serial.payload = b"{x\x80\x0c"
        ticks_ns = iter(range(1, 1000))
        self.owner = TinySaOwnedAcquisition(self.backend, self.endpoint, self.expected,
            serial_factory=self.factory, monotonic_ns=lambda: next(ticks_ns) * 10_000_000)
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(replace(self.request, deadline_seconds=.05))
        self.factory.assert_called_once()
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_control_or_unicode_probe_text_cannot_become_a_firmware_identity(self):
        for text in ("tinySA4\x00private", "tinySA4\tprivate", "tinySA4\x7fprivate", "tinySA4Ж"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                TinySaReadOnlyProbe(TinySaModel.ULTRA, self.endpoint.identity_key, text)

    def test_invalid_partial_factory_object_stays_owned_until_close_contract_is_valid(self):
        invalid = SimpleNamespace(close=Mock(), is_open="unknown")
        self.factory.return_value = invalid
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.assertTrue(self.owner.cleanup_pending)
        self.assertIs(self.owner._port, invalid)
        self.factory.assert_called_once()
        invalid.is_open = False
        self.owner.close()
        self.assertFalse(self.owner.cleanup_pending)

    def test_partial_open_failure_retains_even_when_is_open_false_then_explicit_close(self):
        for reported_open in (False, True):
            with self.subTest(reported_open=reported_open):
                self.setUp()
                self.serial.open_error = self.serial.close_error = True
                self.serial.report_open_on_error = reported_open
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.assertTrue(self.owner.cleanup_pending)
                self.assertIs(self.owner._port, self.serial)
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.factory.assert_called_once()
                self.serial.close_error = False
                self.owner.close()
                self.assertFalse(self.owner.cleanup_pending)
                self.assertEqual(self.serial.calls.count("close"), 2)

    def test_close_error_or_unconfirmed_close_never_publishes_a_trace_or_reopens(self):
        for attribute in ("close_error", "claim_open"):
            with self.subTest(attribute=attribute):
                self.setUp()
                setattr(self.serial, attribute, True)
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.assertTrue(self.owner.cleanup_pending)
                self.assertIs(self.owner._port, self.serial)
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.assertEqual(self.serial.calls.count("close"), 1)
                setattr(self.serial, attribute, False)
                self.owner.close()
                self.factory.assert_called_once()

    def test_missing_or_ambiguous_serial_refuses_before_factory(self):
        for entries in ([], [_pnp(), _pnp(route="COM32")], [_pnp(serial="different")]):
            with self.subTest(entries=len(entries)):
                self.setUp()
                self.inventory[:] = entries
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.factory.assert_not_called()
                self.assertFalse(self.owner.cleanup_pending)

    def test_location_only_refuses_inert_constructor(self):
        self.inventory[:] = [_pnp(serial=None)]
        endpoint = self.backend.discover_endpoints()[0]
        with self.assertRaises(ValueError):
            TinySaOwnedAcquisition(self.backend, endpoint, self.expected, serial_factory=self.factory)
        self.factory.assert_not_called()

    def test_route_can_move_before_open_but_never_during_measurement(self):
        self.inventory[:] = [_pnp(route="COM32")]
        self.owner.collect(self.request)
        self.factory.assert_called_once_with("COM32")
        self.setUp()
        self.serial.on_write = lambda data: self.inventory.__setitem__(slice(None), [_pnp(route="COM32")]) if data == b"version\r" else None
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.assertEqual(self.serial.calls.count("close"), 1)
        self.assertNotIn(("write", self.request.command), self.serial.calls)

    def test_removed_endpoint_after_complete_frame_is_not_published(self):
        self.serial.on_write = lambda data: self.inventory.clear() if data.startswith(b"scanraw ") else None
        with self.assertRaises(RuntimeError):
            self.owner.collect(self.request)
        self.assertFalse(self.owner.cleanup_pending)
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_cancel_before_factory_and_between_zero_and_measurement(self):
        self.owner.cancel()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect(self.request)
        self.factory.assert_not_called()
        self.assertFalse(self.owner.cleanup_pending)
        self.setUp()
        self.serial.on_write = lambda data: self.owner.cancel() if data == b"zero ?\r" else None
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect(self.request)
        self.assertNotIn(("write", self.request.command), self.serial.calls)
        self.assertFalse(self.owner.cleanup_pending)

    def test_foreign_close_cannot_steal_running_read_cancel_then_join(self):
        entered, resume = threading.Event(), threading.Event()
        read = self.serial.read
        def blocked_read(size):
            entered.set()
            if not resume.wait(3):
                raise TimeoutError("test waiter")
            return read(size)
        self.serial.read = blocked_read
        failures = []
        def collect():
            try:
                self.owner.collect(self.request)
            except TinySaTraceCollectionError as error:
                failures.append(error)
        worker = threading.Thread(target=collect)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            with self.assertRaisesRegex(TinySaTraceCollectionError, "join"):
                self.owner.close()
            self.assertNotIn("close", self.serial.calls)
            self.owner.cancel()
        finally:
            resume.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.assertIsInstance(failures[0], TinySaTraceCollectionCancelled)
        self.owner.close()
        self.assertFalse(self.owner.cleanup_pending)

    def test_short_write_and_malformed_frame_close_without_retry(self):
        for mode in ("version", "zero", "scan", "frame"):
            with self.subTest(mode=mode):
                self.setUp()
                if mode == "frame":
                    self.serial.payload = b"{" + b"y\x80\x0c" * 3 + b"}"
                else:
                    self.serial.short_command = {"version": b"version\r", "zero": b"zero ?\r", "scan": self.request.command}[mode]
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect(self.request)
                self.factory.assert_called_once()
                self.assertFalse(self.owner.cleanup_pending)


class TinySaCatalogAcquisitionTests(unittest.TestCase):
    setUp = TinySaOwnedAcquisitionTests.setUp

    def _catalog(self):
        class Probe:
            def probe(inner):
                return TinySaReadOnlyProbe(TinySaModel.ULTRA, self.endpoint.identity_key, "tinySA4 v1.4-fixture")
            def close(inner):
                pass
        provider = TinySaCapabilityProvider(self.backend, port_factory=lambda _: Probe(),
            acquisition_factory=lambda backend, endpoint, observed: TinySaOwnedAcquisition(
                backend, endpoint, observed, serial_factory=self.factory))
        catalog = SourceCapabilityCatalog((provider,), control_transaction=nullcontext)
        candidates = catalog.refresh()
        source = candidates.bindings[0].source_id
        inventory = catalog.observe_source(source)
        return catalog, provider, inventory.bindings[0], inventory.runtimes[0]

    def test_catalog_prepares_exact_same_backend_without_serial_then_blocks_all_probes(self):
        catalog, provider, binding, runtime = self._catalog()
        owner = catalog.prepare_tinysa_acquisition(binding, runtime)
        self.assertIs(owner._backend, self.backend)
        self.assertIs(owner, provider._acquisitions[binding.source_id])
        self.factory.assert_not_called()
        for action in (catalog.snapshot, catalog.refresh, lambda: catalog.observe_source(binding.source_id),
                       lambda: catalog.prepare_tinysa_acquisition(binding, runtime)):
            with self.assertRaises(CapabilityCatalogError):
                action()
        catalog.close()
        self.assertFalse(catalog.cleanup_pending)
        self.factory.assert_not_called()

    def test_copied_equal_binding_or_runtime_is_not_current_reference(self):
        catalog, _, binding, runtime = self._catalog()
        for copied_binding, copied_runtime in ((replace(binding), runtime), (binding, replace(runtime))):
            with self.assertRaises(CapabilityCatalogError):
                catalog.prepare_tinysa_acquisition(copied_binding, copied_runtime)
        self.factory.assert_not_called()
        catalog.close()

    def test_catalog_retains_failed_measurement_close_and_recovers_same_owner(self):
        catalog, provider, binding, runtime = self._catalog()
        owner = catalog.prepare_tinysa_acquisition(binding, runtime)
        self.serial.close_error = True
        with self.assertRaises(TinySaTraceCollectionError):
            owner.collect(self.request)
        self.assertTrue(catalog.cleanup_pending)
        with self.assertRaises(CapabilityCatalogError):
            catalog.close()
        self.assertIs(provider._acquisitions[binding.source_id], owner)
        self.serial.close_error = False
        catalog.close()
        self.factory.assert_called_once()
        self.assertFalse(catalog.cleanup_pending)

    def test_failed_acquisition_close_still_attempts_independent_observation_cleanup(self):
        catalog, provider, binding, runtime = self._catalog()
        owner = catalog.prepare_tinysa_acquisition(binding, runtime)
        self.serial.close_error = True
        with self.assertRaises(TinySaTraceCollectionError):
            owner.collect(self.request)
        adapter = provider._adapters[binding.source_id]
        with patch.object(adapter, "close") as independent_close, self.assertRaises(CapabilityCatalogError):
            catalog.close()
        independent_close.assert_called_once()
        self.assertTrue(catalog.cleanup_pending)
        self.serial.close_error = False
        catalog.close()


class TinySaSettingsRetentionTests(unittest.TestCase):
    def test_transport_error_quarantines_commands_even_before_close(self):
        serial = _Serial()
        serial.short_command = b"lna on\r"
        factory = Mock(return_value=serial)
        port = TinySaSerialSettingsCommandPort("COM31", serial_factory=factory)
        with self.assertRaises(RuntimeError) as error:
            port.command(b"lna on\r")
        self.assertNotIn("PRIVATE", str(error.exception))
        count = len(serial.calls)
        with self.assertRaises(RuntimeError):
            port.command(b"lna off\r")
        self.assertEqual(len(serial.calls), count)
        port.close()
        self.assertFalse(port.cleanup_pending)
        factory.assert_called_once()

    def test_partial_open_and_unconfirmed_close_keep_object_then_explicit_close(self):
        for mode in ("partial", "close", "unconfirmed"):
            with self.subTest(mode=mode):
                serial = _Serial()
                factory = Mock(return_value=serial)
                port = TinySaSerialSettingsCommandPort("COM31", serial_factory=factory)
                if mode == "partial":
                    serial.open_error = serial.close_error = True
                    serial.report_open_on_error = False
                    with self.assertRaises(RuntimeError):
                        port.command(b"lna on\r")
                else:
                    port.command(b"lna on\r")
                    serial.close_error = mode == "close"
                    serial.claim_open = mode == "unconfirmed"
                with self.assertRaises(RuntimeError):
                    port.close()
                self.assertTrue(port.cleanup_pending)
                self.assertIs(port._port, serial)
                with self.assertRaises(RuntimeError):
                    port.command(b"lna on\r")
                factory.assert_called_once()
                serial.close_error = serial.claim_open = False
                port.close()
                self.assertFalse(port.cleanup_pending)

    def test_bound_executor_retains_failed_port_no_new_settings_or_factory(self):
        serial = _Serial()
        serial.close_error = True
        port = TinySaSerialSettingsCommandPort("COM31", serial_factory=lambda _: serial)
        factory = Mock(return_value=port)
        backend = TinySaSerialSourceBackend(inventory_provider=lambda: (_pnp(),), settings_port_factory=factory)
        executor = backend.make_settings_executor(backend.discover_endpoints()[0])
        plan = TinySaSweepSettingsPlan(lna=TinySaSwitchPolicy.ON)
        with self.assertRaises(TinySaSettingsApplyError):
            executor.apply(plan, confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION)
        self.assertTrue(executor.cleanup_pending)
        with self.assertRaises(RuntimeError):
            executor.apply(plan, confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION)
        factory.assert_called_once()
        serial.close_error = False
        executor.close()
        self.assertFalse(executor.cleanup_pending)

    def test_legacy_helper_also_attempts_partial_open_cleanup_when_flag_false(self):
        serial = _Serial()
        serial.open_error = True
        serial.report_open_on_error = False
        request = TinySaScanRawRequest(TinySaModel.ULTRA, 87_500_000, 108_000_000, 3)
        with self.assertRaises(TinySaTraceCollectionError):
            collect_tinysa_scanraw_trace("COM31", request, serial_factory=lambda _: serial)
        self.assertEqual(serial.calls[-1], "close")


if __name__ == "__main__":
    unittest.main()
