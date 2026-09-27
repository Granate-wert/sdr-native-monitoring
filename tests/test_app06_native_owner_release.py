"""Native owner release is confirmed before another Analyzer RX may start."""

from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.application.analyzer_session import (
    AnalyzerMode,
    AnalyzerPhase,
    AnalyzerSessionApplicationService,
)
from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSessionState
from sdr_monitor.domain.live import LiveAdmissionRejected
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class _Engine:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failure: str | None = None

    def request_stop(self) -> None:
        self.calls.append("request_stop")

    def join(self) -> None:
        self.calls.append("join")
        if self.failure == "join":
            raise RuntimeError("private SDK details")

    def disconnect(self) -> None:
        self.calls.append("disconnect")
        if self.failure == "disconnect":
            raise RuntimeError("private SDK details")


class _Poller:
    def __init__(self) -> None:
        self.alive = True
        self.join_timeouts: list[float] = []

    def join(self, timeout: float) -> None:
        self.join_timeouts.append(timeout)

    def is_alive(self) -> bool:
        return self.alive


def _selected() -> tuple[NativeLiveSessionService, _FakeNative]:
    native = _FakeNative()
    service = NativeLiveSessionService(native)
    descriptor = service.discover_devices()[0]
    service.select_device(descriptor.device_id)
    service.apply_configuration(LiveConfiguration(sample_rate_hz=2e6, backend=BackendKind.CPU))
    return service, native


class App06NativeOwnerReleaseTests(unittest.TestCase):
    def test_join_or_disconnect_failure_keeps_owner_and_requires_explicit_stop(self) -> None:
        for failure in ("join", "disconnect"):
            with self.subTest(failure=failure):
                service, native = _selected()
                engine = _Engine()
                engine.failure = failure
                service._engine = engine
                service._snapshot = replace(service.latest_snapshot(), state=LiveSessionState.RUNNING)
                with self.assertRaises(RuntimeError) as raised:
                    service.stop()
                self.assertNotIn("private", str(raised.exception))
                self.assertIs(service._engine, engine)
                calls = list(engine.calls)
                with self.assertRaises(LiveAdmissionRejected):
                    service.start_admitted()
                raw = service.start()
                self.assertEqual(raw.state, LiveSessionState.ERROR)
                self.assertEqual(engine.calls, calls)  # No hidden cleanup/restart.
                self.assertEqual(native.engines, [])
                engine.failure = None
                stopped = service.stop()
                self.assertEqual(stopped.state, LiveSessionState.CONNECTED)
                self.assertIsNone(service._engine)
                self.assertIsNone(service._poller)
                service.stop()
                self.assertEqual(engine.calls[-2:], ["join", "disconnect"])

    def test_unjoined_poller_prevents_disconnect_and_new_rx_until_explicit_retry(self) -> None:
        service, native = _selected()
        engine, poller = _Engine(), _Poller()
        service._engine, service._poller = engine, poller
        with self.assertRaises(RuntimeError):
            service.stop_and_wait(0.01)
        self.assertIs(service._engine, engine)
        self.assertIs(service._poller, poller)
        self.assertEqual(engine.calls, ["request_stop"])
        self.assertEqual(poller.join_timeouts, [0.01])
        with self.assertRaises(LiveAdmissionRejected):
            service.start_admitted()
        self.assertEqual(native.engines, [])
        poller.alive = False
        service.stop_and_wait(0.02)
        self.assertIsNone(service._engine)
        self.assertIsNone(service._poller)
        self.assertEqual(engine.calls[-2:], ["join", "disconnect"])

    def test_selection_cannot_replace_owned_route_or_mutate_running_snapshot(self) -> None:
        service, native = _selected()
        service._engine = _Engine()
        service._snapshot = replace(service.latest_snapshot(), state=LiveSessionState.RUNNING)
        before = service.latest_snapshot()
        opens = len(native.created)
        with self.assertRaises(LiveAdmissionRejected):
            service.select_device(before.device.device_id)
        self.assertIs(service.latest_snapshot(), before)
        self.assertEqual(service._native_uri, before.device.uri)
        self.assertEqual(len(native.created), opens)
        service.stop()
        self.assertEqual(service.select_device(before.device.device_id).state, LiveSessionState.CONNECTED)

    def test_sweep_lease_cannot_ignore_an_unjoined_poller(self) -> None:
        service, _native = _selected()
        service._poller = _Poller()
        with self.assertRaises(RuntimeError):
            service.acquire_native_sweep_lease()
        self.assertFalse(service._sweep_lease_active)

    def test_manual_selection_is_refused_before_any_probe_when_rx_is_owned(self) -> None:
        service, native = _selected()
        service._sweep_lease_active = True
        opens = len(native.created)
        with patch.object(native, "probe_pluto_context") as probe, self.assertRaises(LiveAdmissionRejected):
            service.select_manual_uri("ip:other.fixture")
        probe.assert_not_called()
        self.assertEqual(len(native.created), opens)
        service._sweep_lease_active = False

    def test_application_keeps_stop_required_and_forbids_mode_change_after_cleanup_failure(self) -> None:
        service, _native = _selected()
        engine = _Engine()

        def start():
            service._engine = engine
            service._snapshot = replace(service.latest_snapshot(), state=LiveSessionState.RUNNING)
            return service.latest_snapshot()

        owner = AnalyzerSessionApplicationService(service, SimpleNamespace(), start_live=service.start_admitted)
        with patch.object(service, "_start_unlocked", side_effect=start):
            owner.start()
        engine.failure = "disconnect"
        with self.assertRaises(RuntimeError):
            owner.stop()
        self.assertEqual(owner.state.phase, AnalyzerPhase.ERROR)
        self.assertTrue(owner.stop_required)
        with self.assertRaises(RuntimeError):
            owner.select_mode(AnalyzerMode.SWEEP)
        engine.failure = None
        owner.stop()
        self.assertEqual(owner.state.phase, AnalyzerPhase.IDLE)
        self.assertFalse(owner.stop_required)
        owner.select_mode(AnalyzerMode.SWEEP)

    def test_route_selection_waits_for_stop_then_refuses_an_unreleased_owner(self) -> None:
        service, native = _selected()
        engine = _Engine()
        service._engine = engine
        entered, proceed, select_called = threading.Event(), threading.Event(), threading.Event()
        descriptor = service.latest_snapshot().device
        opens = len(native.created)

        def blocked_join():
            entered.set()
            if not proceed.wait(3):
                raise RuntimeError("test barrier expired")
            raise RuntimeError("join failed")

        def select():
            select_called.set()
            return service.select_device(descriptor.device_id)

        with patch.object(engine, "join", side_effect=blocked_join), ThreadPoolExecutor(max_workers=2) as pool:
            stopping = pool.submit(service.stop)
            try:
                self.assertTrue(entered.wait(1))
                selecting = pool.submit(select)
                self.assertTrue(select_called.wait(1))
                with self.assertRaises(FutureTimeoutError):
                    selecting.result(timeout=0.02)
            finally:
                proceed.set()
            with self.assertRaises(RuntimeError):
                stopping.result(timeout=2)
            with self.assertRaises(LiveAdmissionRejected):
                selecting.result(timeout=2)
        self.assertEqual(len(native.created), opens)
        service.stop()

    def test_late_publication_is_rejected_while_failed_stop_retains_owner(self) -> None:
        service, _native = _selected()
        engine = _Engine()
        service._engine = engine
        service._stop_event.set()
        before = service.latest_snapshot()
        # Invalid data must not even be converted on a stopped publication lane.
        self.assertFalse(service._publish_frame(SimpleNamespace(), expected_engine=engine))
        service._publish_persistence(SimpleNamespace(), expected_engine=engine)
        self.assertIs(service.latest_snapshot(), before)
        service.stop()

    def test_failed_start_cleanup_retains_candidate_and_does_not_try_next_route(self) -> None:
        service, native = _selected()
        service._snapshot = replace(service.latest_snapshot(), device=replace(
            service.latest_snapshot().device, alternate_uris=("usb:fixture",),
        ))
        factory = native.PlutoFixedBandEngine

        def failing_factory(uri: str, timeout_ms: int):
            engine = factory(uri, timeout_ms)
            engine.configure_error = RuntimeError("configuration failed")
            engine.join = lambda: (_ for _ in ()).throw(RuntimeError("private join"))
            return engine

        with patch.object(native, "PlutoFixedBandEngine", side_effect=failing_factory), self.assertRaises(RuntimeError) as raised:
            service.start_admitted()
        self.assertNotIn("private", str(raised.exception))
        self.assertEqual(len(native.engines), 1)
        self.assertIs(service._engine, native.engines[0])
        self.assertEqual(native.engines[0].disconnect_calls, 0)
        native.engines[0].join = lambda: None
        service.stop()


if __name__ == "__main__":
    unittest.main()
