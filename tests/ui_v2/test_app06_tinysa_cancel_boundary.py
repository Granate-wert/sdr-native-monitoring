"""Finish a consumed response, never retry it; no firmware/RF timing claims."""

import threading
import unittest
from dataclasses import replace
from unittest.mock import Mock

from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.tinysa_owned_acquisition import TinySaAcquisitionPhase
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
    TinySaTraceFailureReason,
)
from tests import test_app06_tinysa_owned_acquisition as owner_fixtures
from tests.ui_v2 import test_app06_tinysa_common_analyzer as common_fixtures
from tests.ui_v2 import test_app06_tinysa_repeated_sweep as repeated_fixtures


class TinySaCancelBoundaryOwnerTests(unittest.TestCase):
    setUp = owner_fixtures.TinySaOwnedAcquisitionTests.setUp

    def _cancel_inside_scan(self):
        read = self.serial.read
        self.serial.closed_with_unread = False
        close = self.serial.close
        def close_at_boundary():
            self.serial.closed_with_unread |= bool(self.serial.response)
            close()
        def fragmented(size):
            if self.owner.measurement_pending and not self.owner.cancellation_requested:
                self.owner.cancel()
            return read(min(size, 1))
        self.serial.read, self.serial.close = fragmented, close_at_boundary

    def test_one_shot_cancel_finishes_fragmented_frame_and_prompt_before_close(self):
        self._cancel_inside_scan()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect(replace(self.request, deadline_seconds=.1))
        self.assertTrue(self.owner.measurement_pending)  # consumed but NOT admitted
        self.assertFalse(self.serial.closed_with_unread)
        self.assertEqual(self.serial.response, b"")
        self.assertIsNone(self.owner.failure)
        self.assertFalse(self.owner.cleanup_pending)
        self.factory.assert_called_once()
        self.assertEqual(self.serial.calls.count("close"), 1)
        writes = [c[1] for c in self.serial.calls if isinstance(c, tuple) and c[0] == "write"]
        self.assertEqual(writes, [b"version\r", b"zero ?\r", self.request.command])

    def test_repeated_cancel_finishes_response_without_callback_or_next_scan(self):
        self._cancel_inside_scan()
        publish = Mock()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect_repeated(replace(self.request, deadline_seconds=.1), publish)
        publish.assert_not_called()
        self.assertFalse(self.serial.closed_with_unread)
        self.assertEqual(self.serial.calls.count("reset"), 1)
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_missing_prompt_is_bounded_deadline_not_a_successful_cancel(self):
        write = self.serial.write
        def no_prompt(data):
            n = write(data)
            if data.startswith(b"scanraw "):
                self.serial.response = self.serial.response.removesuffix(b"\r\nch> ")
                self.owner.cancel()
            return n
        self.serial.write = no_prompt
        with self.assertRaises(TinySaTraceCollectionError) as failure:
            self.owner.collect(replace(self.request, deadline_seconds=.05))
        self.assertNotIsInstance(failure.exception, TinySaTraceCollectionCancelled)
        self.assertEqual(self.owner.failure.phase, TinySaAcquisitionPhase.SCAN)
        self.assertEqual(self.owner.failure.reason, TinySaTraceFailureReason.DEADLINE)
        self.assertFalse(self.owner.cleanup_pending)  # transport close, NOT RF recovery
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_fresh_version_timeout_is_route_free_fixed_stage_reason(self):
        self.serial.version = b""
        ticks = iter((0.0, 0.0, 3.0))
        self.owner._monotonic = lambda: next(ticks)
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.assertEqual(self.owner.failure.phase, TinySaAcquisitionPhase.VERSION)
        self.assertEqual(self.owner.failure.reason, TinySaTraceFailureReason.DEADLINE)
        self.assertNotIn(("write", self.request.command), self.serial.calls)

    def test_close_failure_retains_object_and_is_never_a_successful_cancel(self):
        self._cancel_inside_scan()
        self.serial.close_error = True
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(replace(self.request, deadline_seconds=.1))
        self.assertIs(self.owner._port, self.serial)
        self.assertTrue(self.owner.cleanup_pending)
        self.assertEqual(self.owner.failure.phase, TinySaAcquisitionPhase.CLOSE)
        self.assertEqual(self.owner.failure.reason, TinySaTraceFailureReason.CLOSE)
        before = len(self.serial.calls)
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        self.assertEqual(len(self.serial.calls), before)
        self.serial.close_error = False
        self.owner.close()
        self.factory.assert_called_once()

    def test_zero_and_scan_framing_have_fixed_reason_without_raw_vendor_data(self):
        for phase in ("zero", "scan"):
            with self.subTest(phase=phase):
                self.setUp()
                write = self.serial.write
                def malformed(data, phase=phase, write=write):
                    n = write(data)
                    if phase == "zero" and data == b"zero ?\r":
                        self.serial.response = b"PRIVATE invalid zero\r\nch> "
                    if phase == "scan" and data.startswith(b"scanraw "):
                        self.serial.response = b"{" + b"y\x80\x0c" * 3 + b"}\r\nch> "
                    return n
                self.serial.write = malformed
                with self.assertRaises(TinySaTraceCollectionError) as error:
                    self.owner.collect(replace(self.request, deadline_seconds=.1))
                self.assertEqual(self.owner.failure.phase.value, phase)
                self.assertEqual(self.owner.failure.reason, TinySaTraceFailureReason.FRAMING)
                self.assertNotIn("PRIVATE", str(error.exception))
                self.assertNotIn("PRIVATE", repr(self.owner.failure))

    def test_changed_firmware_diagnostics_survive_explicit_close(self):
        self.serial.version = b"tinySA4 v1.6-changed\rch> "
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect(self.request)
        failure = self.owner.failure
        self.assertEqual(failure.phase, TinySaAcquisitionPhase.VERSION)
        self.assertEqual(failure.reason, TinySaTraceFailureReason.IDENTITY)
        self.owner.close()
        self.assertIs(self.owner.failure, failure)
        self.assertNotIn(("write", self.request.command), self.serial.calls)


class TinySaCancelBoundaryCommonTests(unittest.TestCase):
    wait = repeated_fixtures.TinySaRepeatedCommonTests.wait

    def test_cancel_finishes_one_response_then_explicit_next_start_new_epoch(self):
        g = common_fixtures.graph()
        self.addCleanup(g.application.shutdown)
        entered, resume = threading.Event(), threading.Event()
        factory = g.provider._acquisition_factory
        shared = {"pending": False, "unread_close": False}
        owners = []
        def owned(*args):
            owner = factory(*args)
            owners.append(owner)
            def raw(_route):
                serial = owner_fixtures._Serial()
                g.serials.append(serial)
                read, write, close = serial.read, serial.write, serial.close
                first = len(g.serials) == 1
                def command(data):
                    if data == b"version\r" and shared["pending"]:
                        raise RuntimeError("PRIVATE firmware still writing previous response")
                    n = write(data)
                    if data.startswith(b"scanraw "):
                        shared["pending"] = True
                    return n
                def receive(size):
                    if first and owner.measurement_pending and not resume.is_set():
                        entered.set()
                        if not resume.wait(3):
                            raise RuntimeError("fixture response barrier")
                    chunk = read(min(size, 1))
                    if owner.measurement_pending and not serial.response:
                        shared["pending"] = False
                    return chunk
                def release():
                    shared["unread_close"] |= shared["pending"]
                    close()
                serial.read, serial.write, serial.close = receive, command, release
                return serial
            owner._factory = raw
            return owner
        g.provider._acquisition_factory = owned
        g.application.select_device(g.application.discover()[0].device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision, 87_500_000, 108_000_000, 3,
                                     timeout_s=1)
        g.application.start_sweep(request)
        self.assertTrue(entered.wait(2))
        stopped = []
        stopper = threading.Thread(target=lambda: stopped.append(g.application.stop()))
        stopper.start()
        try:
            self.wait(lambda: owners[0].cancellation_requested)
            self.assertTrue(stopper.is_alive())
            self.assertIs(g.live._external_analyzer_owner, g.instrument)
            self.assertEqual(g.serials[0].calls.count("close"), 0)
        finally:
            resume.set()
            stopper.join(3)
        self.assertFalse(stopper.is_alive())
        self.assertFalse(stopped[0].stop_required)
        gap = g.instrument.poll_latest().line
        self.assertFalse(gap.is_complete)
        self.assertEqual(g.instrument.poll_latest().metrics.completed_lines, 0)
        self.assertFalse(shared["unread_close"])
        self.assertFalse(shared["pending"])
        g.application.start_sweep(request)  # separate explicit operation, not a retry
        self.wait(lambda: g.instrument.poll_latest().metrics.acquisition_finished)
        complete = g.instrument.poll_latest().line
        self.assertTrue(complete.is_complete)
        self.assertGreater(complete.epoch, gap.epoch)
        self.assertGreater(complete.instrument.configuration_generation, gap.instrument.configuration_generation)
        self.assertEqual(len(g.serials), 2)
        self.assertFalse(shared["unread_close"])
        g.application.stop()


class TinySaCancelBoundaryActualUiTests(unittest.TestCase):
    wait = repeated_fixtures.TinySaRepeatedActualUiTests.wait
    setUpClass = classmethod(repeated_fixtures.TinySaRepeatedActualUiTests.setUpClass.__func__)

    def test_stop_keeps_qt_responsive_while_finishing_response(self):
        import time
        from types import SimpleNamespace
        from unittest.mock import patch

        from PySide6.QtCore import QTimer

        from sdr_monitor.ui.v2.product_live import compose_v2_live_product
        from sdr_monitor.ui.v2_composition import build_v2_shell

        g = common_fixtures.graph()
        entered, resume = threading.Event(), threading.Event()
        factory = g.provider._acquisition_factory
        def owned(*args):
            owner = factory(*args)
            raw_factory = owner._factory
            def raw(route):
                serial = raw_factory(route)
                read = serial.read
                def receive(size):
                    if owner.measurement_pending and not resume.is_set():
                        entered.set()
                        if not resume.wait(3):
                            raise RuntimeError("fixture response barrier")
                    return read(size)
                serial.read = receive
                return serial
            owner._factory = raw
            return owner
        g.provider._acquisition_factory = owned
        captures = []
        def compose(*args, **kwargs):
            c = compose_v2_live_product(*args, **kwargs)
            captures.append(c)
            return c
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument,
            analyzer_hackrf=None, sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=compose):
            shell = build_v2_shell(services)
        c, page = captures[0], shell._workspace_pages["analyzer"]
        try:
            page.discover.click()
            self.wait(lambda: page.source.count() == 2 and not c.view_model.state.busy)
            page.source.setCurrentIndex(1)
            self.wait(lambda: c.analyzer_view_model.state.tinysa_controls_available and not c.view_model.state.busy)
            page.tinysa_bar.points.setValue(3)
            page.primary.click()
            self.wait(lambda: entered.is_set() and c.analyzer_view_model.state.running
                      and not c.analyzer_view_model.state.starting)
            started = time.monotonic()
            page.primary.click()
            self.assertLess(time.monotonic() - started, .5)  # synthetic GUI call, NOT RF Stop SLA
            self.wait(lambda: g.instrument._owner.cancellation_requested)
            heartbeat = []
            QTimer.singleShot(0, lambda: heartbeat.append(True))
            self.wait(lambda: bool(heartbeat))
            self.assertTrue(c.analyzer_view_model.state.stopping)
            self.assertIs(g.live._external_analyzer_owner, g.instrument)
            self.assertEqual(g.serials[0].calls.count("close"), 0)
            resume.set()
            self.wait(lambda: not c.analyzer_view_model.state.controls_locked)
            self.assertFalse(c.analyzer_view_model.state.bundle.spectrum.is_complete)
            self.assertEqual(g.serials[0].response, b"")
            self.assertIsNone(g.instrument._thread)
            self.assertIsNone(g.live._external_analyzer_owner)
        finally:
            resume.set()
            shell.close()
            self.wait(lambda: shell._is_closed)
            c.shutdown()
            shell.deleteLater()
            self.app.processEvents()
