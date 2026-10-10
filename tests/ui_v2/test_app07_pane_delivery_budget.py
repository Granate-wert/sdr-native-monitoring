"""Finite tests for the pane port's soft GUI-turn application budget."""
from __future__ import annotations

import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2.workspaces import independent_pane_delivery as delivery_module
from sdr_monitor.ui.v2.workspaces.independent_pane_delivery import IndependentPaneDeliveryPort


def _packet(pane_id: str, token: str):
    return SimpleNamespace(delivery=SimpleNamespace(pane_id=pane_id, token=token),
                           binding=SimpleNamespace(slot_number=1))


class _FakeClock:
    def __init__(self):
        self.now_ns = 0

    def __call__(self):
        return self.now_ns


class _FakeQueue:
    def __init__(self, packets, clock=None, drain_cost_ns=0):
        self.pane_ids = tuple(dict.fromkeys(packet.delivery.pane_id for packet in packets))
        self.packets = list(packets)
        self.clock = clock
        self.drain_cost_ns = drain_cost_ns
        self.calls = []
        self.max_call_depth = 0
        self._call_depth = 0

    def drain(self, *, max_items=4, excluded_panes=()):
        self._call_depth += 1
        self.max_call_depth = max(self.max_call_depth, self._call_depth)
        try:
            self.calls.append((max_items, tuple(excluded_panes)))
            if self.clock is not None:
                self.clock.now_ns += self.drain_cost_ns
            for index, packet in enumerate(self.packets):
                if packet.delivery.pane_id not in excluded_panes:
                    return (self.packets.pop(index),)[:max_items]
            return ()
        finally:
            self._call_depth -= 1

    def clear(self, pane_id):
        self.packets[:] = [packet for packet in self.packets
                           if packet.delivery.pane_id != pane_id]


class _FakeTimer:
    def __init__(self):
        self.started = False
        self.stop_count = 0
        self.start_count = 0

    def start(self):
        self.started = True
        self.start_count += 1

    def stop(self):
        self.started = False
        self.stop_count += 1


class _PortHarness(QObject):
    rendered = Signal(str)
    render_failed = Signal(str, str)

    def __init__(self, queue, apply):
        super().__init__()
        self._queue = queue
        self._board = SimpleNamespace(apply_prepared=apply)
        self._failed_panes = set()
        self._turn_budget_ns = 8_000_000
        self._turn_active = False
        self._lifecycle_generation = 0
        self._timer = _FakeTimer()
        self.reported = []
        self.rejected_current = []

    def _report(self, prepared, stage):
        self.reported.append((prepared.delivery.token, stage))

    def _reject_uncommitted(self, prepared):
        self.rejected_current.append(prepared.delivery.token)


class PaneDeliveryBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def _port(packets, apply, clock=None, drain_cost_ns=0):
        queue = _FakeQueue(packets, clock, drain_cost_ns)
        return _PortHarness(queue, apply), queue

    def test_budget_equality_and_overrun_finish_current_but_do_not_start_next(self):
        for elapsed_ns in (8_000_000, 12_000_000):
            with self.subTest(elapsed_ns=elapsed_ns):
                clock = _FakeClock()
                packets = [_packet("a", "a1"), _packet("b", "b1")]

                def apply(_prepared):
                    clock.now_ns += elapsed_ns
                    return True

                port, queue = self._port(packets, apply, clock)
                with patch.object(delivery_module, "perf_counter_ns", clock):
                    IndependentPaneDeliveryPort.tick_once(port)
                self.assertEqual(queue.calls, [(1, ())])
                self.assertEqual(len(queue.packets), 1)
                self.assertEqual(port.rejected_current, [])

    def test_soft_budget_allows_first_taken_application_even_if_drain_cost_crosses_budget(self):
        clock = _FakeClock()
        packets = [_packet("a", "a1"), _packet("b", "b1")]
        applied = []
        port, queue = self._port(packets,
                                 lambda packet: applied.append(packet.delivery.token) or True,
                                 clock, drain_cost_ns=9_000_000)
        with patch.object(delivery_module, "perf_counter_ns", clock):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(applied, ["a1"])
        self.assertEqual(len(queue.calls), 1)
        self.assertEqual([packet.delivery.token for packet in queue.packets], ["b1"])

    def test_slow_pane_does_not_starve_fast_peer_on_later_turn(self):
        clock = _FakeClock()
        packets = [_packet("slow", "s1"), _packet("fast", "f1")]
        order = []

        def apply(packet):
            order.append(packet.delivery.token)
            if packet.delivery.pane_id == "slow":
                clock.now_ns += 9_000_000
            return True

        port, queue = self._port(packets, apply, clock)
        with patch.object(delivery_module, "perf_counter_ns", clock):
            IndependentPaneDeliveryPort.tick_once(port)
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(order, ["s1", "f1"])
        self.assertEqual(queue.packets, [])

    def test_terminal_latest_and_peer_take_at_most_once_per_pane_in_turn(self):
        clock = _FakeClock()
        packets = [_packet("a", "a-terminal"), _packet("a", "a-latest"),
                   _packet("b", "b-current")]
        order = []
        port, queue = self._port(packets, lambda packet: order.append(packet.delivery.token) or True, clock)
        with patch.object(delivery_module, "perf_counter_ns", clock):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(order, ["a-terminal", "b-current"])
        self.assertEqual([packet.delivery.token for packet in queue.packets], ["a-latest"])
        self.assertEqual(queue.calls[1], (1, ("a",)))
        with patch.object(delivery_module, "perf_counter_ns", clock):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(order[-1], "a-latest")

    def test_application_exception_latches_and_clears_only_failed_pane(self):
        packets = [_packet("bad", "bad1"), _packet("bad", "bad2"), _packet("peer", "peer1")]
        order = []

        def apply(packet):
            order.append(packet.delivery.token)
            if packet.delivery.pane_id == "bad":
                raise RuntimeError("synthetic application failure")
            return True

        port, queue = self._port(packets, apply)
        with patch.object(delivery_module, "perf_counter_ns", return_value=0):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(order, ["bad1", "peer1"])
        self.assertEqual(port._failed_panes, {"bad"})
        self.assertEqual([packet.delivery.token for packet in queue.packets], [])

    def test_baseexception_disposes_taken_packet_and_leaves_unattempted_queued(self):
        packets = [_packet("a", "a1"), _packet("b", "b1")]

        def apply(_packet):
            raise KeyboardInterrupt("synthetic BaseException")

        port, queue = self._port(packets, apply)
        with patch.object(delivery_module, "perf_counter_ns", return_value=0):
            with self.assertRaisesRegex(KeyboardInterrupt, "synthetic"):
                IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(port.rejected_current, ["a1"])
        self.assertEqual([packet.delivery.token for packet in queue.packets], ["b1"])
        self.assertEqual(port.reported, [])
        self.assertFalse(port._turn_active)

    def test_reentrant_tick_is_guarded_and_does_not_nest_queue_drain(self):
        packets = [_packet("a", "a1"), _packet("b", "b1")]
        port = None
        order = []

        def apply(packet):
            order.append(packet.delivery.token)
            if len(order) == 1:
                IndependentPaneDeliveryPort.tick_once(port)
            return True

        port, queue = self._port(packets, apply)
        with patch.object(delivery_module, "perf_counter_ns", return_value=0):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(queue.max_call_depth, 1)
        self.assertEqual(order, ["a1", "b1"])

    def test_stop_start_during_apply_retires_old_turn_after_current_disposition(self):
        packets = [_packet("a", "a1"), _packet("b", "b1")]
        port = None

        def apply(_packet):
            IndependentPaneDeliveryPort.stop(port)
            IndependentPaneDeliveryPort.start(port)
            return True

        port, queue = self._port(packets, apply)
        with patch.object(delivery_module, "perf_counter_ns", return_value=0):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(port._lifecycle_generation, 2)
        self.assertEqual([packet.delivery.token for packet in queue.packets], ["b1"])
        self.assertFalse(port._turn_active)

    def test_stop_from_rendered_signal_retires_turn_after_signal_delivery(self):
        packets = [_packet("a", "a1"), _packet("b", "b1")]
        port, queue = self._port(packets, lambda _packet: True)
        def restart_from_signal(_pane):
            IndependentPaneDeliveryPort.stop(port)
            IndependentPaneDeliveryPort.start(port)

        port.rendered.connect(restart_from_signal)
        with patch.object(delivery_module, "perf_counter_ns", return_value=0):
            IndependentPaneDeliveryPort.tick_once(port)
        self.assertEqual(port._lifecycle_generation, 2)
        self.assertEqual([packet.delivery.token for packet in queue.packets], ["b1"])

    def test_constructor_budget_validation_default_and_wrong_thread_entrypoints(self):
        class _ConstructorBoard(QObject):
            def __init__(self):
                super().__init__()
                self._preparer = SimpleNamespace(layout=SimpleNamespace(slots=(
                    SimpleNamespace(request=SimpleNamespace(pane_id="a")),)))

        queue = PaneFairDeliveryQueue(("a",))
        with patch.object(delivery_module, "IndependentPaneBoardV2", _ConstructorBoard):
            for invalid in (True, 0, 17):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError):
                        IndependentPaneDeliveryPort(_ConstructorBoard(), queue, turn_budget_ms=invalid)
            port = IndependentPaneDeliveryPort(_ConstructorBoard(), queue)
        self.assertEqual(port._turn_budget_ns, 8_000_000)
        self.assertEqual(port.interval_ms, 16)

        errors = []

        def wrong_thread_call():
            for method in (IndependentPaneDeliveryPort.tick_once,
                           IndependentPaneDeliveryPort.start,
                           IndependentPaneDeliveryPort.stop):
                try:
                    method(port)
                except BaseException as error:
                    errors.append(error)

        worker = threading.Thread(target=wrong_thread_call)
        worker.start()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 3)
        self.assertTrue(all(isinstance(error, RuntimeError) for error in errors))
        self.assertIn("Qt thread", str(errors[0]))
        self.assertIn("owning thread", str(errors[1]))
        self.assertIn("owning thread", str(errors[2]))
        port.stop()


if __name__ == "__main__":
    unittest.main()
