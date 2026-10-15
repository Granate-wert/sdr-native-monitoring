"""Successful NativeLive release caches final service-owned bridge counters."""

from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace
import unittest

from sdr_monitor.domain import BackendKind, LiveConfiguration
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_s15_live_rx_bridge import _FakeEngine, _FakeNative, _make_frame


class _TerminalEngine(_FakeEngine):
    def __init__(self, uri: str, timeout_ms: int) -> None:
        super().__init__(uri, timeout_ms)
        self.applied.active_backend = SimpleNamespace(name="CPU")
        self.tail_entered = Event()
        self.release_tail = Event()
        self.stop_requested = Event()
        self.disconnect_entered = Event()
        self.release_disconnect = Event()
        self._drain_count = 0
        self.fail_disconnect = False
        self.hold_disconnect = False

    def request_stop(self) -> None:
        super().request_stop()
        self.stop_requested.set()

    def drain_latest_spectrum_frame(self) -> object:
        self._drain_count += 1
        if self._drain_count == 1:
            return SimpleNamespace(frame=_make_frame(1), coalesced_frames=2)
        if self._drain_count == 2:
            self.tail_entered.set()
            if not self.release_tail.wait(3):
                raise RuntimeError("test tail barrier expired")
            # Stop has set its cancellation event before this tail is returned.
            return SimpleNamespace(frame=_make_frame(2), coalesced_frames=4)
        return SimpleNamespace(frame=None, coalesced_frames=0)

    def disconnect(self) -> None:
        if self.hold_disconnect:
            self.disconnect_entered.set()
            if not self.release_disconnect.wait(3):
                raise RuntimeError("test disconnect barrier expired")
        if self.fail_disconnect:
            raise RuntimeError("controlled native disconnect refusal")
        super().disconnect()


class _TerminalNative(_FakeNative):
    def __init__(self) -> None:
        super().__init__()
        self.ComputeBackendKind = SimpleNamespace(AUTO="auto", CPU="cpu")

    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _TerminalEngine:
        engine = _TerminalEngine(uri, timeout_ms)
        engine.configure_error = self.engine_configure_error
        self.engines.append(engine)
        return engine


class NativeTerminalBridgeTests(unittest.TestCase):
    def _started(self) -> tuple[_TerminalNative, NativeLiveSessionService, _TerminalEngine]:
        native = _TerminalNative()
        service = NativeLiveSessionService(native)
        self.addCleanup(self._cleanup_service, service)
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        service.apply_configuration(LiveConfiguration(sample_rate_hz=2e6, backend=BackendKind.CPU))
        started = service.start()
        self.assertIsNone(started.error, f"TEST native activation failed: {started.error}")
        engine = native.engines[-1]
        self.addCleanup(self._cleanup_engine, engine)
        if not engine.tail_entered.wait(2):
            self.fail("actual NativeLive poller did not enter its controlled tail")
        self.assertIsNotNone(service.latest_snapshot().spectrum)
        return native, service, engine

    @staticmethod
    def _sentinel(service: NativeLiveSessionService) -> object:
        with service._lock:
            snapshot = service._snapshot
            performance = replace(snapshot.performance,
                bridge_native_frames_polled=91,
                bridge_frames_coalesced=37,
                bridge_frames_published=19,
                analytical_fft_rate_hz=123.5,
                spectrum_snapshot_rate_hz=87.25,
                iq_block_rate_hz=6.75,
                rate_observation_interval_s=0.375,
                fft_frames_computed=456)
            service._snapshot = replace(snapshot, performance=performance)
            return performance

    def _cleanup_service(self, service: NativeLiveSessionService) -> None:
        worker = getattr(service, "_test_stop_worker", None)
        if worker is not None and worker.is_alive():
            worker.join(7)
            if worker.is_alive():
                raise AssertionError("test Stop worker remained alive; refusing competing cleanup Stop")
        engine = service._engine
        if engine is not None:
            engine.release_tail.set()
            engine.release_disconnect.set()
            engine.fail_disconnect = False
            engine.hold_disconnect = False
        errors: list[str] = []
        try:
            service.stop()
        except BaseException as error:
            errors.append(f"explicit Stop cleanup failed: {type(error).__name__}: {error}")
        poller = service._poller
        if poller is not None:
            poller.join(2)
            if poller.is_alive():
                errors.append("actual NativeLive poller remained alive after cleanup Stop")
        if service._engine is not None or service._poller is not None:
            errors.append("NativeLive owner remained after cleanup Stop")
        if errors:
            raise AssertionError("; ".join(errors))

    @staticmethod
    def _cleanup_engine(engine: _TerminalEngine) -> None:
        engine.release_tail.set()
        engine.release_disconnect.set()
        engine.fail_disconnect = False
        engine.hold_disconnect = False

    @staticmethod
    def _cleanup_stop_worker(worker: Thread, engine: _TerminalEngine) -> None:
        engine.release_tail.set()
        engine.release_disconnect.set()
        engine.fail_disconnect = False
        engine.hold_disconnect = False
        if worker.is_alive():
            worker.join(3)
        if worker.is_alive():
            raise AssertionError("test Stop worker remained alive after bounded cleanup")

    def _stop_after_releasing_tail(self, service: NativeLiveSessionService,
                                   engine: _TerminalEngine, *, at_disconnect=None
                                   ) -> tuple[Thread, list[object], list[BaseException]]:
        results: list[object] = []
        errors: list[BaseException] = []

        def stop() -> None:
            try:
                results.append(service.stop())
            except BaseException as error:
                errors.append(error)

        worker = Thread(target=stop, name="test-native-stop")
        self.addCleanup(self._cleanup_stop_worker, worker, engine)
        service._test_stop_worker = worker
        worker.start()
        if not engine.stop_requested.wait(2):
            engine.release_tail.set()
            worker.join(2)
            self.fail("Stop did not request cancellation before joining the poller")
        engine.release_tail.set()
        if at_disconnect is not None:
            at_disconnect()
        worker.join(3)
        self.assertFalse(worker.is_alive(), "Stop worker did not finish")
        return worker, results, errors

    def test_successful_stop_caches_exact_final_counts_and_preserves_other_metrics(self) -> None:
        _native, service, engine = self._started()
        engine.hold_disconnect = True
        prior: list[object] = []

        def mark_quiescent_sentinel() -> None:
            if not engine.disconnect_entered.wait(2):
                engine.release_disconnect.set()
                self.fail("release did not reach cleanup after poller join")
            prior.append(self._sentinel(service))
            engine.release_disconnect.set()

        _worker, results, errors = self._stop_after_releasing_tail(service, engine,
            at_disconnect=mark_quiescent_sentinel)
        self.assertFalse(errors)
        self.assertEqual(len(results), 1)
        performance = results[0].performance
        # Initial batch published one of three frames; the in-flight tail was
        # counted as polled/coalesced after cancellation, not published.
        expected = replace(prior[0], bridge_native_frames_polled=8,
            bridge_frames_coalesced=6, bridge_frames_published=1)
        self.assertEqual(performance, expected)
        self.assertIsNone(service._poller)
        self.assertIsNone(service._engine)

    def test_join_refusal_keeps_owner_and_cache_until_explicit_recovery(self) -> None:
        _native, service, engine = self._started()
        prior = self._sentinel(service)
        poller = service._poller
        with self.assertRaises(RuntimeError):
            service._release_stream(timeout_s=0)
        self.assertIs(service._poller, poller)
        self.assertIs(service._engine, engine)
        self.assertTrue(service._stream_release_failed)
        self.assertEqual(service.latest_snapshot().performance, prior)
        engine.release_tail.set()
        result = service.stop()
        self.assertEqual((result.performance.bridge_native_frames_polled,
                          result.performance.bridge_frames_coalesced,
                          result.performance.bridge_frames_published), (8, 6, 1))
        self.assertIsNone(service._poller)
        self.assertIsNone(service._engine)

    def test_disconnect_refusal_retains_cache_and_owner_then_recovery_finalizes(self) -> None:
        _native, service, engine = self._started()
        engine.hold_disconnect = True
        prior: list[object] = []

        def mark_quiescent_sentinel() -> None:
            if not engine.disconnect_entered.wait(2):
                engine.release_disconnect.set()
                self.fail("release did not reach refused cleanup after poller join")
            prior.append(self._sentinel(service))
            engine.release_disconnect.set()

        engine.fail_disconnect = True
        _worker, results, errors = self._stop_after_releasing_tail(service, engine,
            at_disconnect=mark_quiescent_sentinel)
        self.assertFalse(results)
        self.assertEqual(len(errors), 1)
        self.assertTrue(service._stream_release_failed)
        self.assertIs(service._engine, engine)
        self.assertIsNotNone(service._poller)
        self.assertEqual(service.latest_snapshot().performance, prior[0])
        engine.fail_disconnect = False
        result = service.stop()
        self.assertEqual((result.performance.bridge_native_frames_polled,
                          result.performance.bridge_frames_coalesced,
                          result.performance.bridge_frames_published), (8, 6, 1))
        self.assertIsNone(service._poller)
        self.assertIsNone(service._engine)

    def test_repeated_stop_and_failed_activation_without_poller_do_not_reinject_old_counts(self) -> None:
        native, service, engine = self._started()
        self._stop_after_releasing_tail(service, engine)
        old_counts = service.latest_snapshot().performance
        repeated = service.stop()
        self.assertEqual(repeated.performance, old_counts)

        service.apply_configuration(LiveConfiguration(center_hz=2.401e9,
            sample_rate_hz=2e6, backend=BackendKind.CPU))
        prior = self._sentinel(service)
        native.engine_configure_error = RuntimeError("controlled activation refusal")
        failed = service.start()
        self.assertIsNone(service._poller)
        self.assertIsNone(service._engine)
        self.assertEqual(failed.performance, prior)
        stopped = service.stop()
        self.assertEqual(stopped.performance, prior)


if __name__ == "__main__":
    unittest.main()
