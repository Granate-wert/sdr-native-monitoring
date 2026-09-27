"""Concrete version-only serial seam with fake serial/PnP, not RF evidence."""

from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sdr_monitor.services.source_capability_providers import TinySaCapabilityProvider
from sdr_monitor.services.tinysa_capability_adapter import (
    TinySaCapabilityAdapter,
    TinySaCapabilityObservationError,
    TinySaModel,
)
from sdr_monitor.services.tinysa_readonly_port import TinySaSerialReadOnlyPort
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend


def _pnp(serial="fixture-one", route="COM31", location="fixture-location"):
    return SimpleNamespace(device=route, vid=0x0483, pid=0x5740,
                           serial_number=serial, location=location)


class _Serial:
    def __init__(self):
        self.is_open = False
        self.dtr = self.rts = True
        self.open_error = self.close_error = self.claim_open = False
        self.short_write = False
        self.calls = []

    def open(self):
        self.calls.append(("open", self.dtr, self.rts))
        self.is_open = True
        if self.open_error:
            raise RuntimeError("PRIVATE partial open")

    def close(self):
        self.calls.append("close")
        if self.close_error:
            raise RuntimeError("PRIVATE close")
        self.is_open = self.claim_open

    def write(self, data):
        self.calls.append(("write", data))
        return len(data) - int(self.short_write)

    def flush(self):
        self.calls.append("flush")


class TinySaRetainedPortTests(unittest.TestCase):
    def setUp(self):
        self.inventory = [_pnp()]
        self.backend = TinySaSerialSourceBackend(inventory_provider=lambda: tuple(self.inventory))
        self.endpoint = self.backend.discover_endpoints()[0]
        self.serial = _Serial()
        self.factory = Mock(return_value=self.serial)
        self.port = TinySaSerialReadOnlyPort(self.backend, self.endpoint, serial_factory=self.factory)
        self.adapter = TinySaCapabilityAdapter(lambda: self.port)

    def _observe(self, response=b"version\r\ntinySA4 v1.4-fixture\r\nch> "):
        with patch("sdr_monitor.services.tinysa_readonly_port._read_bounded_response",
                   side_effect=(b"ch> ", response)) as read:
            result = self.adapter.observe()
        self.assertEqual(read.call_args_list[0].kwargs, {"deadline_seconds": .25, "limit": 4096})
        self.assertEqual(read.call_args_list[1].kwargs, {"deadline_seconds": 2.0, "limit": 4096})
        return result

    def test_constructor_is_lazy_then_exactly_one_version_command_and_mandatory_close(self):
        self.factory.assert_not_called()
        result = self._observe()
        self.factory.assert_called_once_with("COM31")
        self.assertEqual(self.serial.calls, [("open", False, False), ("write", b"version\r"), "flush", "close"])
        self.assertFalse(self.serial.is_open)
        self.assertFalse(self.adapter.cleanup_pending)
        self.assertEqual(result.snapshot.model_id, TinySaModel.ULTRA.value)
        self.assertFalse(result.snapshot.raw_iq_available)
        self.assertEqual(result.analyzer_semantics.reported_unit, "dBm")
        self.assertNotIn("COM31", repr(result))
        with self.assertRaises(RuntimeError):
            self.port.probe()

    def test_invalid_responses_are_closed_and_never_retried(self):
        for response in (b"tinySA4 v1.4", b"other instrument\rch> ", b"tinySA4 " + b"x" * 4100 + b"ch> ",
                         b"tinySA4 \xff\rch> ", b"tinySA4 " + b"x" * 130 + b"\rch> "):
            with self.subTest(response_length=len(response)):
                self.setUp()
                with self.assertRaises(TinySaCapabilityObservationError) as error:
                    self._observe(response)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertEqual(self.serial.calls[-1], "close")
                self.factory.assert_called_once()
                self.assertFalse(self.adapter.cleanup_pending)

    def test_short_command_write_closes_without_response_read(self):
        self.serial.short_write = True
        with patch("sdr_monitor.services.tinysa_readonly_port._read_bounded_response", return_value=b"ch> ") as read, \
             self.assertRaises(TinySaCapabilityObservationError):
            self.adapter.observe()
        self.assertEqual(read.call_count, 1)
        self.assertNotIn("flush", self.serial.calls)
        self.assertEqual(self.serial.calls[-1], "close")

    def test_partial_open_failure_keeps_owner_until_confirmed_public_close(self):
        self.serial.open_error = self.serial.close_error = True
        with self.assertRaises(TinySaCapabilityObservationError):
            self.adapter.observe()
        self.assertTrue(self.adapter.cleanup_pending)
        self.assertIs(self.port._port, self.serial)
        with self.assertRaises(TinySaCapabilityObservationError):
            self.adapter.observe()
        self.factory.assert_called_once()
        self.serial.close_error = False
        self.adapter.close()
        self.assertFalse(self.adapter.cleanup_pending)
        self.assertIsNone(self.port._port)
        self.assertEqual(self.serial.calls, [("open", False, False), "close", "close"])

    def test_unconfirmed_serial_close_retains_same_object_and_refuses_new_probe(self):
        for failure in ("close_error", "claim_open"):
            with self.subTest(failure=failure):
                self.setUp()
                setattr(self.serial, failure, True)
                with self.assertRaises(TinySaCapabilityObservationError):
                    self._observe()
                self.assertTrue(self.adapter.cleanup_pending)
                with self.assertRaises(TinySaCapabilityObservationError):
                    self.adapter.observe()
                self.factory.assert_called_once()
                setattr(self.serial, failure, False)
                self.adapter.close()
                self.assertFalse(self.adapter.cleanup_pending)
                self.assertIsNone(self.port._port)

    def test_location_or_endpoint_candidates_refuse_before_serial_factory(self):
        for location in ("fixture-location", None):
            self.inventory[:] = [_pnp(serial=None, location=location)]
            endpoint = self.backend.discover_endpoints()[0]
            port = TinySaSerialReadOnlyPort(self.backend, endpoint, serial_factory=self.factory)
            with self.assertRaises(RuntimeError):
                port.probe()
            port.close()
        self.factory.assert_not_called()

    def test_absent_or_ambiguous_usb_serial_refuses_before_serial_factory(self):
        for entries in ((), (_pnp(), _pnp(route="COM32"))):
            self.inventory[:] = entries
            port = TinySaSerialReadOnlyPort(self.backend, self.endpoint, serial_factory=self.factory)
            with self.assertRaises(RuntimeError):
                port.probe()
            port.close()
        self.factory.assert_not_called()

    def test_route_change_during_transaction_refuses_and_closes(self):
        before = self.endpoint
        after = replace(before, route="COM32")
        with patch.object(self.backend, "resolve_endpoint", side_effect=(before, after)), \
             patch("sdr_monitor.services.tinysa_readonly_port._read_bounded_response",
                   side_effect=(b"ch> ", b"tinySA4 v1.4\rch> ")), self.assertRaises(TinySaCapabilityObservationError):
            self.adapter.observe()
        self.assertFalse(self.serial.is_open)
        self.assertFalse(self.adapter.cleanup_pending)

    def test_provider_keeps_two_confirmed_sources_in_the_same_family(self):
        self.inventory[:] = [_pnp(), _pnp(serial="fixture-two", route="COM32")]
        ports = []
        def make(endpoint):
            serial = _Serial()
            port = TinySaSerialReadOnlyPort(self.backend, endpoint, serial_factory=lambda _: serial)
            ports.append((port, serial))
            return port
        provider = TinySaCapabilityProvider(self.backend, port_factory=make)
        candidates = provider.discover(startup_only=False)
        ids = tuple(binding.source_id for binding in candidates.bindings)
        self.assertEqual(len(ids), 2)
        for source_id in ids:
            with patch("sdr_monitor.services.tinysa_readonly_port._read_bounded_response",
                       side_effect=(b"ch> ", b"tinySA4 v1.4\rch> ")):
                result = provider.observe_source(source_id)
        self.assertEqual(len(result.snapshots), 2)
        self.assertEqual(tuple(binding.source_id for binding in result.bindings), ids)
        self.assertTrue(all(binding.identity_key is not None for binding in result.bindings))
        self.assertTrue(all(not serial.is_open for _, serial in ports))
        self.assertEqual(provider.discover(startup_only=True).snapshots, ())
        provider.close()


if __name__ == "__main__":
    unittest.main()
