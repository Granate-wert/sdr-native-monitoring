"""Explicit host-loop owner/prompt/history tests; no hardware timing claims."""

import os
import threading
import time
import unittest
from dataclasses import MISSING, fields, replace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.tinysa_capability_adapter import TinySaCapabilityAdapter, TinySaModel, TinySaReadOnlyProbe
from sdr_monitor.services.tinysa_owned_acquisition import TinySaOwnedAcquisition
from sdr_monitor.services.tinysa_serial_source_backend import TinySaSerialSourceBackend
from sdr_monitor.services.tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
    TinySaTraceCollectionCancelled,
    TinySaTraceCollectionError,
)
from sdr_monitor.ui.v2.product_live import compose_v2_live_product
from sdr_monitor.ui.v2_composition import build_v2_shell
from tests.test_app06_tinysa_owned_acquisition import _pnp, _Serial
from tests.ui_v2.test_app06_tinysa_common_analyzer import graph


class _PromptSerial(_Serial):
    def __init__(self):
        super().__init__()
        self.suffix = b"\r\nch> "
        self.chunk_limit = 7
        self.scans = 0
        self.unread_command = False

    def write(self, data):
        if self.response:
            self.unread_command = True
            raise RuntimeError("PRIVATE unread previous response")
        result = super().write(data)
        if data.startswith(b"scanraw "):
            self.scans += 1
            self.response = self.response.removesuffix(b"\r\nch> ") + self.suffix
        return result

    def read(self, size):
        return super().read(min(size, self.chunk_limit))


class TinySaRepeatedOwnerTests(unittest.TestCase):
    def setUp(self):
        self.pnp = [_pnp()]
        self.backend = TinySaSerialSourceBackend(inventory_provider=lambda: tuple(self.pnp))
        endpoint = self.backend.discover_endpoints()[0]
        expected = TinySaCapabilityAdapter.map_probe(TinySaReadOnlyProbe(
            TinySaModel.ULTRA, endpoint.identity_key, "tinySA4 v1.4-fixture"))
        self.serial = _PromptSerial()
        self.factory = Mock(return_value=self.serial)
        self.owner = TinySaOwnedAcquisition(self.backend, endpoint, expected, serial_factory=self.factory)
        self.request = TinySaScanRawRequest(TinySaModel.ULTRA, 87_500_000, 108_000_000, 3, .05)

    def test_three_passes_one_open_fresh_versions_complete_prompts_no_discard(self):
        results = []
        def received(result):
            results.append(result)
            self.assertTrue(self.serial.is_open)
            self.assertTrue(result.prompt_confirmed)
            self.assertFalse(hasattr(result, "port_closed"))
            self.assertFalse(result.trace.values_dbm.flags.writeable)
            if len(results) == 3:
                self.owner.cancel()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect_repeated(self.request, received, interval_s=.05)
        self.assertEqual(len(results), 3)
        self.factory.assert_called_once()
        self.assertEqual(self.serial.calls.count(("open", False, False)), 1)
        self.assertEqual(self.serial.calls.count("reset"), 1)
        self.assertEqual(self.serial.calls.count("close"), 1)
        writes = [c[1] for c in self.serial.calls if isinstance(c, tuple) and c[0] == "write"]
        self.assertEqual(writes, [b"version\r", b"zero ?\r", self.request.command] * 3)
        self.assertFalse(self.serial.unread_command)
        self.assertFalse(self.owner.cleanup_pending)
        self.assertFalse(self.owner.measurement_pending)

    def test_original_closed_collection_position_and_repeat_request_types_are_preserved(self):
        self.assertEqual(fields(TinySaTraceCollection)[9].name, "port_closed")
        self.assertIs(fields(TinySaTraceCollection)[9].default, MISSING)
        g = graph()
        self.addCleanup(g.application.shutdown)
        g.application.select_device(g.application.discover()[0].device_id)
        selected = g.sources.current()
        request = TinySaSweepRequest(selected.selected, selected.revision, 87_500_000, 108_000_000, 3)
        for values in ({"repeat_until_stop": 1}, {"interval_s": True}, {"interval_s": float("nan")},
                       {"interval_s": .01}, {"interval_s": 61}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                replace(request, **values)
        self.assertEqual(g.serials, [])

    def test_missing_double_oversized_and_binary_prompt_fail_without_next_pass(self):
        for suffix in (b"", b"ch> ch> ", b"X" * 513, b"{", b"PRIVATE ch> "):
            with self.subTest(suffix=suffix[:16]):
                self.setUp()
                self.serial.suffix = suffix
                received = Mock()
                with self.assertRaises(TinySaTraceCollectionError):
                    self.owner.collect_repeated(self.request, received, interval_s=.05)
                received.assert_not_called()
                self.assertEqual(self.serial.scans, 1)
                self.assertEqual(self.serial.calls.count("close"), 1)
                self.factory.assert_called_once()

    def test_changed_firmware_refuses_second_measurement_and_does_not_retry(self):
        results = []
        def received(result):
            results.append(result)
            self.serial.version = b"tinySA4 v1.6-changed\rch> "
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect_repeated(self.request, received, interval_s=.05)
        self.assertEqual(len(results), 1)
        self.assertEqual(self.serial.scans, 1)
        self.factory.assert_called_once()
        self.assertEqual(self.serial.calls.count("close"), 1)

    def test_removed_endpoint_does_not_publish_complete_but_foreign_trace(self):
        self.serial.on_write = lambda data: self.pnp.clear() if data.startswith(b"scanraw ") else None
        received = Mock()
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect_repeated(self.request, received, interval_s=.05)
        received.assert_not_called()
        self.assertEqual(self.serial.scans, 1)
        self.assertFalse(self.owner.cleanup_pending)

    def test_failed_close_retains_same_object_no_poll_or_measurement_retry(self):
        def received(_result):
            self.serial.close_error = True
            self.owner.cancel()
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect_repeated(self.request, received, interval_s=.05)
        self.assertTrue(self.owner.cleanup_pending)
        self.assertIs(self.owner._port, self.serial)
        self.assertEqual(self.serial.calls.count("close"), 1)
        with self.assertRaises(TinySaTraceCollectionError):
            self.owner.collect_repeated(self.request, received, interval_s=.05)
        self.assertEqual(self.serial.calls.count("close"), 1)
        self.serial.close_error = False
        self.owner.close()
        self.assertFalse(self.owner.cleanup_pending)
        self.factory.assert_called_once()

    def test_invalid_repeat_values_and_cancel_before_open_have_no_effects(self):
        for value in (True, .01, 61, float("nan")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.owner.collect_repeated(self.request, Mock(), interval_s=value)
        self.factory.assert_not_called()
        self.owner.cancel()
        with self.assertRaises(TinySaTraceCollectionCancelled):
            self.owner.collect_repeated(self.request, Mock())
        self.factory.assert_not_called()
        self.assertFalse(self.owner.cleanup_pending)


def repeated_graph():
    g = graph()
    factory = g.provider._acquisition_factory
    def create(*args):
        owner = factory(*args)
        def raw(_route):
            serial = _PromptSerial()
            g.serials.append(serial)
            return serial
        owner._factory = raw
        return owner
    g.provider._acquisition_factory = create
    return g


class TinySaRepeatedCommonTests(unittest.TestCase):
    def wait(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            if time.monotonic() > deadline:
                self.fail("repeat common owner timeout")
            time.sleep(.001)

    def test_common_sequence_epoch_generation_rate_and_between_pass_stop(self):
        g = repeated_graph()
        self.addCleanup(g.application.shutdown)
        g.application.select_device(g.application.discover()[0].device_id)
        selection = g.sources.current()
        request = TinySaSweepRequest(selection.selected, selection.revision, 87_500_000, 108_000_000, 3,
                                     repeat_until_stop=True, interval_s=.5)
        g.application.start_sweep(request)
        self.wait(lambda: g.instrument.poll_latest().metrics.completed_lines >= 3)
        latest = g.instrument.poll_latest()
        self.assertGreaterEqual(latest.line.sequence, 2)
        self.assertEqual(latest.line.instrument.configuration_generation, 1)
        self.assertGreater(latest.metrics.completed_line_lps, 0)
        self.assertFalse(latest.metrics.acquisition_finished)
        self.assertIs(g.live._external_analyzer_owner, g.instrument)
        stopped = g.application.stop()
        self.assertFalse(stopped.stop_required)
        terminal = g.instrument.poll_latest()
        self.assertTrue(terminal.line.is_complete)  # no RF command in the wait interval
        self.assertEqual(terminal.metrics.gapped_lines, 0)
        self.assertEqual(len(g.serials), 1)
        self.assertEqual(g.serials[0].calls.count("close"), 1)
        self.assertIsNone(g.instrument._thread)
        self.assertIsNone(g.live._external_analyzer_owner)

    def test_stop_during_second_read_keeps_previous_pass_count_and_adds_gap(self):
        g = repeated_graph()
        self.addCleanup(g.application.shutdown)
        entered, resume = threading.Event(), threading.Event()
        original = g.provider._acquisition_factory
        def create(*args):
            owner = original(*args)
            raw = owner._factory
            def serial_factory(route):
                serial = raw(route)
                read = serial.read
                def blocked(size):
                    if serial.scans == 2:
                        entered.set()
                        resume.wait(5)
                        return read(size)  # cancelled pass finishes its response, not publication
                    return read(size)
                serial.read = blocked
                return serial
            owner._factory = serial_factory
            return owner
        g.provider._acquisition_factory = create
        g.application.select_device(g.application.discover()[0].device_id)
        selection = g.sources.current()
        g.application.start_sweep(TinySaSweepRequest(selection.selected, selection.revision,
            87_500_000, 108_000_000, 3, repeat_until_stop=True))
        self.assertTrue(entered.wait(2))
        stopped = []
        thread = threading.Thread(target=lambda: stopped.append(g.application.stop()))
        thread.start()
        self.wait(lambda: g.instrument._owner._cancel.is_set())
        self.assertIs(g.live._external_analyzer_owner, g.instrument)
        resume.set()
        thread.join(4)
        self.assertFalse(thread.is_alive())
        self.assertFalse(stopped[0].stop_required)
        terminal = g.instrument.poll_latest()
        self.assertEqual(terminal.metrics.completed_lines, 1)
        self.assertEqual(terminal.metrics.gapped_lines, 1)
        self.assertEqual(terminal.line.sequence, 1)
        self.assertFalse(terminal.line.is_complete)
        self.assertIsNone(terminal.line.instrument.observed_zero_db)


class TinySaRepeatedActualUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate):
        deadline = time.monotonic() + 6
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("actual repeat UI timeout")
            time.sleep(.001)
        self.app.processEvents()

    def test_same_root_repeat_history_live_until_explicit_stop(self):
        from types import SimpleNamespace
        g = repeated_graph()
        captures = []
        def compose(*args, **kwargs):
            c = compose_v2_live_product(*args, **kwargs)
            captures.append(c)
            return c
        services = SimpleNamespace(live_sdr=g.live, device_catalog=g.catalog, analyzer_tinysa=g.instrument,
            analyzer_hackrf=None, sweep=Mock(), calibration=Mock(), diagnostics=Mock(), replay=Mock())
        with patch("sdr_monitor.ui.v2.product_live.compose_v2_live_product", side_effect=compose):
            shell = build_v2_shell(services)
        c = captures[0]
        page = shell._workspace_pages["analyzer"]
        failures = []
        try:
            with patch("sys.excepthook", side_effect=lambda *args: failures.append(args)):
                page.discover.click()
                self.wait(lambda: page.source.count() == 2 and not c.view_model.state.busy)
                page.source.setCurrentIndex(1)
                self.wait(lambda: c.analyzer_view_model.state.tinysa_controls_available and not c.view_model.state.busy)
                self.assertFalse(page.tinysa_bar.repeat.isChecked())
                page.tinysa_bar.points.setValue(3)
                page.tinysa_bar.repeat.setChecked(True)
                page.primary.click()
                self.wait(lambda: page.visualization.waterfall_pane.history_rows >= 3)
                state = c.analyzer_view_model.state
                self.assertTrue(state.running)
                self.assertFalse(page.tinysa_bar.repeat.isEnabled())
                self.assertGreaterEqual(state.bundle.spectrum.sequence, 2)
                self.assertEqual(state.bundle.unit, "dBm")
                self.assertEqual(state.bundle.spectrum.instrument.configuration_generation, 1)
                self.assertEqual(len(g.serials), 1)
                page.primary.click()
                self.wait(lambda: not c.analyzer_view_model.state.controls_locked)
                self.assertIsNone(g.instrument._thread)
                self.assertTrue(page.tinysa_bar.repeat.isChecked())
                self.assertIsNone(g.live._external_analyzer_owner)
            self.assertEqual(failures, [])
        finally:
            shell.close()
            self.wait(lambda: shell._is_closed)
            c.shutdown()
            shell.deleteLater()
            self.app.processEvents()
