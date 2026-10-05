"""Causal SAME-owner fake I/O witnesses; not physical or latency qualification."""

from __future__ import annotations

from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock

from sdr_monitor.services.tinysa_acquisition_diagnostics import (
    TinySaDiagnosticClock as Clock,
    TinySaDiagnosticOperation as Operation,
    TinySaDiagnosticPhase as Phase,
    TinySaDiagnosticRecorder,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaCapabilityAdapter, TinySaModel, TinySaReadOnlyProbe
from sdr_monitor.services.tinysa_owned_acquisition import TinySaOwnedAcquisition
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest, TinySaTraceCollectionCancelled, TinySaTraceCollectionError,
)
from tests.test_app06_tinysa_owned_acquisition import _pnp, _Serial


class TinySaDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.serial = _Serial()
        self.backend = TinySaSerialSourceBackend(inventory_provider=lambda: (_pnp(),))
        endpoint = self.backend.discover_endpoints()[0]
        expected = TinySaCapabilityAdapter.map_probe(TinySaReadOnlyProbe(
            TinySaModel.ULTRA, endpoint.identity_key, "tinySA4 v1.4-fixture"))
        self.factory = Mock(return_value=self.serial)
        self.owner = TinySaOwnedAcquisition(self.backend, endpoint, expected, serial_factory=self.factory)
        self.request = TinySaScanRawRequest(TinySaModel.ULTRA, 100_000_000, 300_000_000, 3)
        self.errors = []

    def worker(self, call):
        def execute():
            try:
                call()
            except Exception as error:
                self.errors.append(error)
        thread = threading.Thread(target=execute)
        thread.start()
        return thread

    def release_worker(self, thread, release):
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.owner.close()

    def test_inert_scalar_snapshot_never_opens_queries_clocks_or_retains_payload(self):
        self.owner._monotonic = Mock(side_effect=AssertionError("getter clock"))
        self.owner._monotonic_ns = Mock(side_effect=AssertionError("getter clock"))
        self.backend.resolve_endpoint = Mock(side_effect=AssertionError("getter query"))
        initial = self.owner.acquisition_diagnostics
        for _ in range(100):
            self.assertEqual(self.owner.acquisition_diagnostics, initial)
        self.factory.assert_not_called()
        self.assertNotIn("COM31", repr(initial))
        self.assertEqual(initial.progress.phase, Phase.PREPARED)
        self.assertIsNone(initial.progress.deadline_value)

    def test_blocked_scan_read_snapshot_available_without_io_and_cancel_finishes_same_prompt(self):
        entered, release = threading.Event(), threading.Event()
        original_read = self.serial.read
        def read(size):
            if self.serial.response.startswith(b"{"):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test barrier timeout")
            return original_read(size)
        self.serial.read = read
        thread = self.worker(lambda: self.owner.collect(self.request))
        self.addCleanup(self.release_worker, thread, release)
        self.assertTrue(entered.wait(3))
        before = tuple(self.serial.calls)
        snapshot = self.owner.acquisition_diagnostics
        self.assertEqual((snapshot.progress.phase, snapshot.progress.operation, snapshot.progress.in_flight),
                         (Phase.SCAN, Operation.READ, True))
        self.assertEqual((snapshot.progress.command_id, snapshot.progress.pass_id), (3, 1))
        self.assertEqual((snapshot.progress.read_entered, snapshot.progress.read_returned), (1, 0))
        self.assertEqual(snapshot.progress.deadline_clock, Clock.MONOTONIC_NS)
        self.assertIsNotNone(snapshot.progress.deadline_value)
        self.owner._monotonic_ns = Mock(side_effect=AssertionError("getter clock"))
        for _ in range(20):
            self.assertEqual(self.owner.acquisition_diagnostics, snapshot)
        self.assertEqual(tuple(self.serial.calls), before)
        # Restore worker clock before releasing it; cancellation still drains consumed response.
        import time
        self.owner._monotonic_ns = time.monotonic_ns
        self.owner.cancel()
        release.set()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(self.errors), 1)
        self.assertIsInstance(self.errors[0], TinySaTraceCollectionCancelled)
        final = self.owner.acquisition_diagnostics
        self.assertTrue(final.progress.cancelled)
        self.assertIsNone(final.first_fault)
        self.assertTrue(final.progress.prompt_confirmed)
        self.assertEqual(self.serial.calls.count("close"), 1)
        self.factory.assert_called_once()

    def test_flush_wait_is_not_given_a_response_deadline_before_flush_returns(self):
        entered, release = threading.Event(), threading.Event()
        original_flush = self.serial.flush
        def flush():
            if self.serial.response.startswith(b"{"):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test barrier timeout")
            original_flush()
        self.serial.flush = flush
        thread = self.worker(lambda: self.owner.collect(self.request))
        self.addCleanup(self.release_worker, thread, release)
        self.assertTrue(entered.wait(3))
        p = self.owner.acquisition_diagnostics.progress
        self.assertEqual((p.phase, p.operation, p.in_flight), (Phase.SCAN, Operation.FLUSH, True))
        self.assertTrue(p.command_accepted)
        self.assertIsNone(p.deadline_value)
        release.set()
        thread.join(3)
        self.assertEqual(self.errors, [])

    def test_blocked_version_read_has_original_seconds_deadline_not_ns_conversion(self):
        entered, release = threading.Event(), threading.Event()
        original_read = self.serial.read
        def read(size):
            if self.serial.response.startswith(b"tinySA"):
                entered.set()
                if not release.wait(3):
                    raise AssertionError("test barrier timeout")
            return original_read(size)
        self.serial.read = read
        self.owner._monotonic = Mock(return_value=10.125)
        thread = self.worker(lambda: self.owner.collect(self.request))
        self.addCleanup(self.release_worker, thread, release)
        self.assertTrue(entered.wait(3))
        p = self.owner.acquisition_diagnostics.progress
        self.assertEqual((p.phase, p.operation, p.in_flight), (Phase.VERSION, Operation.READ, True))
        self.assertEqual((p.deadline_clock, p.deadline_value, p.observed_value),
                         (Clock.MONOTONIC_SECONDS, 12.125, 10.125))
        release.set()
        thread.join(3)
        self.assertEqual(self.errors, [])

    def test_blocked_publish_is_distinct_from_completed_scan(self):
        entered, release = threading.Event(), threading.Event()
        def publish(_result):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test barrier timeout")
            self.owner.cancel()
        thread = self.worker(lambda: self.owner.collect_repeated(self.request, publish, interval_s=.05))
        self.addCleanup(self.release_worker, thread, release)
        self.assertTrue(entered.wait(3))
        p = self.owner.acquisition_diagnostics.progress
        self.assertEqual((p.phase, p.operation, p.in_flight), (Phase.PUBLISH, Operation.PUBLISH, True))
        self.assertEqual((p.passes_completed, p.publications_completed), (1, 0))
        release.set()
        thread.join(3)
        self.assertEqual(self.owner.acquisition_diagnostics.progress.publications_completed, 1)
        self.assertIsNone(self.owner.acquisition_diagnostics.first_fault)

    def test_first_frame_fault_preserved_before_close_error_without_changing_exception_precedence(self):
        self.serial.payload = b"{" + b"x\x80\x0c" * 3 + b"!"
        self.serial.close_error = True
        with self.assertRaises(TinySaTraceCollectionError) as error:
            self.owner.collect(self.request)
        # Existing one-shot policy still reports the failed close, not an invented success.
        self.assertIn("close", str(error.exception))
        first = self.owner.acquisition_diagnostics.first_fault
        self.assertIsNotNone(first)
        self.assertEqual((first.reason, first.progress.phase), ("framing", Phase.SCAN))
        self.assertEqual(self.owner.acquisition_diagnostics.progress.phase, Phase.CLOSE)
        self.assertTrue(self.owner.cleanup_pending)
        self.serial.close_error = False
        self.owner.close()
        self.assertEqual(self.owner.acquisition_diagnostics.first_fault, first)

    def test_repeated_frame_split_reads_and_exact_command_counts(self):
        original_read = self.serial.read
        self.serial.read = lambda size: original_read(min(2, size))
        def publish(result):
            self.assertTrue(result.prompt_confirmed)
            self.assertEqual(result.response_bytes_read, 11 + len(b"\r\nch> "))
            self.owner.cancel()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect_repeated(self.request, publish)
        p = self.owner.acquisition_diagnostics.progress
        self.assertEqual((p.command_id, p.passes_completed, p.publications_completed), (3, 1, 1))
        self.assertGreater(p.read_returned, 1)
        self.assertEqual(p.read_entered, p.read_returned)
        self.assertEqual(p.response_bytes, 17)
        self.assertTrue(p.prompt_confirmed)
        self.assertTrue(p.complete)

    def test_diagnostic_overflow_and_clock_failure_do_not_change_measurement(self):
        recorder = self.owner._diagnostics
        recorder._state = replace(recorder._state, command_id=(1 << 64) - 1)
        result = self.owner.collect(self.request)
        self.assertTrue(result.port_closed)
        p = self.owner.acquisition_diagnostics.progress
        self.assertFalse(p.complete)
        self.assertGreaterEqual(p.diagnostic_failures, 1)
        self.assertIsNone(self.owner.acquisition_diagnostics.first_fault)
        self.assertEqual(p.command_id, (1 << 64) - 1)

    def test_invalid_clock_and_regression_are_explicit_unknown_not_clipped(self):
        for clock, observed, deadline in ((Clock.MONOTONIC_SECONDS, float("nan"), 2.0),
                                          (Clock.MONOTONIC_NS, -1, 2),
                                          (Clock.MONOTONIC_NS, 1, 1 << 63)):
            recorder = TinySaDiagnosticRecorder()
            recorder.clock(clock, observed, deadline=deadline)
            p = recorder.snapshot().progress
            self.assertFalse(p.complete)
            self.assertIsNone(p.deadline_value)
            self.assertIsNone(p.observed_value)
        recorder = TinySaDiagnosticRecorder()
        recorder.clock(Clock.MONOTONIC_NS, 10, deadline=100)
        recorder.clock(Clock.MONOTONIC_NS, 9)
        self.assertFalse(recorder.snapshot().progress.complete)
        self.assertIsNone(recorder.snapshot().progress.deadline_value)


if __name__ == "__main__":
    unittest.main()
