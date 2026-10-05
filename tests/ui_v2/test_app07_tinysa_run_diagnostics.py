"""Actual common-service SAME-owner diagnostic lifecycle with fake serial."""

from dataclasses import replace
import threading
import time
import unittest
from unittest.mock import Mock

from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.tinysa_acquisition_diagnostics import (
    TinySaDiagnosticOperation as Operation, TinySaDiagnosticPhase as Phase,
)
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph
from tests.ui_v2.test_app06_tinysa_runtime_settings import settings_graph


class TinySaRunDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        choice = self.g.application.discover()[0]
        self.g.application.select_device(choice.device_id)
        self.selection = self.g.sources.current()
        self.request = TinySaSweepRequest(self.selection.selected, self.selection.revision,
                                         100_000_000, 300_000_000, 3)
        self.addCleanup(self.g.application.shutdown)
        self.addCleanup(self.g.instrument.stop)

    def wait(self, predicate):
        end = time.monotonic() + 3
        while not predicate():
            if time.monotonic() >= end:
                self.fail("test worker timeout")
            time.sleep(.001)

    def test_stop_preserves_scalar_terminal_and_new_run_does_not_borrow_old_generation(self):
        service = self.g.instrument
        self.assertIsNone(service.acquisition_diagnostics.generation)
        self.assertIsNone(service.acquisition_diagnostics.acquisition)
        service.start(self.request, self.selection)
        self.wait(lambda: service.poll_latest().metrics.acquisition_finished)
        self.wait(lambda: not service.acquisition_worker_alive)
        before = service.acquisition_diagnostics
        self.assertTrue(before.owner_retained)
        self.assertEqual(before.generation, service.instrument_run_identity.configuration_generation)
        self.assertEqual(before.acquisition.progress.publications_completed, 1)
        service.stop()
        terminal = service.acquisition_diagnostics
        self.assertFalse(terminal.owner_retained)
        self.assertEqual(terminal.acquisition.progress, replace(before.acquisition.progress, cancelled=True))
        self.assertEqual(terminal.acquisition.first_fault, before.acquisition.first_fault)
        self.assertIsNone(service.acquisition_worker_alive)
        self.assertFalse(service.stop_required)
        service._clock = Mock(side_effect=AssertionError("getter clock"))
        service._clock_ns = Mock(side_effect=AssertionError("getter clock"))
        for _ in range(50):
            self.assertEqual(service.acquisition_diagnostics, terminal)
        service._clock, service._clock_ns = time.monotonic, time.monotonic_ns
        service.start(self.request, self.selection)
        self.wait(lambda: service.poll_latest().metrics.acquisition_finished)
        newer = service.acquisition_diagnostics
        self.assertGreater(newer.generation, terminal.generation)
        self.assertEqual(newer.acquisition.progress.command_id, 3)
        self.assertEqual(newer.acquisition.progress.pass_id, 1)
        self.assertIsNone(newer.acquisition.first_fault)
        service.stop()
        self.assertEqual(len(self.g.serials), 2)
        self.assertTrue(all(serial.calls.count("close") == 1 for serial in self.g.serials))

    def test_delayed_service_publication_is_visible_without_waiting_for_measurement_or_display_lock(self):
        service = self.g.instrument
        entered, release = threading.Event(), threading.Event()
        original_line = service._line
        def line(*args, **kwargs):
            entered.set()
            if not release.wait(3):
                raise AssertionError("test publication barrier timeout")
            return original_line(*args, **kwargs)
        service._line = line
        self.addCleanup(release.set)
        service.start(self.request, self.selection)
        self.assertTrue(entered.wait(3))
        diagnostic = service.acquisition_diagnostics
        p = diagnostic.acquisition.progress
        self.assertEqual((p.phase, p.operation, p.in_flight), (Phase.PUBLISH, Operation.PUBLISH, True))
        self.assertEqual((p.passes_completed, p.publications_completed), (1, 0))
        self.assertIsNone(p.deadline_value)
        self.assertTrue(service.acquisition_worker_alive)
        # Getter neither borrows the display lock nor asks the SDK/clock.
        service._lock.acquire()
        try:
            self.assertEqual(service.acquisition_diagnostics, diagnostic)
        finally:
            service._lock.release()
        release.set()
        self.wait(lambda: service.poll_latest().metrics.acquisition_finished)
        service.stop()

    def test_repeated_service_keeps_same_owner_before_explicit_stop(self):
        service = self.g.instrument
        request = replace(self.request, repeat_until_stop=True, interval_s=.05)
        service.start(request, self.selection)
        self.wait(lambda: service.poll_latest().metrics.completed_lines >= 2)
        generation = service.acquisition_diagnostics.generation
        self.assertTrue(service.acquisition_diagnostics.owner_retained)
        self.assertTrue(service.acquisition_worker_alive)
        self.assertEqual(len(self.g.serials), 1)
        service.stop()
        terminal = service.acquisition_diagnostics
        self.assertEqual(terminal.generation, generation)
        self.assertGreaterEqual(terminal.acquisition.progress.publications_completed, 2)
        self.assertTrue(terminal.acquisition.progress.cancelled)
        self.assertIsNone(terminal.acquisition.first_fault)
        self.assertIsNone(service.acquisition_worker_alive)

    def test_readback_wait_remains_distinct_from_scan_with_settings_deadline(self):
        g = settings_graph()
        self.addCleanup(g.application.shutdown)
        self.addCleanup(g.instrument.stop)
        choice = g.application.discover()[0]
        g.application.select_device(choice.device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision,
                                    100_000_000, 300_000_000, 3, readback_settings=True)
        entered, release = threading.Event(), threading.Event()
        prepare = g.provider._acquisition_factory
        def acquisition(*args):
            owner = prepare(*args)
            factory = owner._factory
            def serial_factory(route):
                serial = factory(route)
                read = serial.read
                def blocked_read(size):
                    if serial.response.startswith(b"rbw ?"):
                        entered.set()
                        if not release.wait(3):
                            raise AssertionError("test readback barrier timeout")
                    return read(size)
                serial.read = blocked_read
                return serial
            owner._factory = serial_factory
            return owner
        g.provider._acquisition_factory = acquisition
        self.addCleanup(release.set)
        g.instrument.start(request, selection)
        self.assertTrue(entered.wait(3))
        p = g.instrument.acquisition_diagnostics.acquisition.progress
        self.assertEqual((p.phase, p.operation, p.in_flight), (Phase.READBACK, Operation.READ, True))
        self.assertEqual(p.deadline_clock.value, "monotonic_seconds")
        self.assertTrue(p.command_accepted)
        self.assertEqual(p.passes_completed, 0)  # readback/identity admission has not finished
        release.set()
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        g.instrument.stop()

    def test_publication_failure_diagnostics_survive_stop_without_serial_failure_fabrication(self):
        service = self.g.instrument
        service._line = Mock(side_effect=RuntimeError("PRIVATE publication"))
        service.start(self.request, self.selection)
        self.wait(lambda: service.poll_latest().metrics.has_error)
        fault = service.acquisition_diagnostics.acquisition.first_fault
        self.assertEqual((fault.progress.phase, fault.progress.operation), (Phase.PUBLISH, Operation.PUBLISH))
        self.assertIsNone(service.acquisition_failure)  # unchanged service policy, not a vendor fault
        self.assertNotIn("PRIVATE", repr(fault))
        service.stop()
        self.assertEqual(service.acquisition_diagnostics.acquisition.first_fault, fault)


if __name__ == "__main__":
    unittest.main()
