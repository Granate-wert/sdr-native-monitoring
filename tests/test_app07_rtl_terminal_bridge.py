"""Event-controlled RTL terminal bridge counter cache regressions; mocked only."""

from __future__ import annotations

from dataclasses import replace
from threading import Event, Thread
import unittest
from types import SimpleNamespace

from sdr_monitor.domain.live import LivePerformance, LiveSessionState
from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from sdr_monitor.services.rtl_capability_provider import RTL_SOURCE_ID
from tests import test_app07_rtl_product_route as route_fixture
from tests.test_app07_rtl_product_route import _Exclusion, _FrameControl, _Native


class _TailControl(_FrameControl):
    def __init__(self, center: int, rate: int, generation: int) -> None:
        super().__init__(center, rate, generation)
        self.drain_entered = Event()
        self.drain_release = Event()
        self.stop_entered = Event()
        self.stop_release = Event()
        self.stop_release.clear()
        self.tail_returned = False
        self.metrics_after_stop = 0

    def drain_latest_spectrum_frame(self):
        if not self.tail_returned:
            self.drain_entered.set()
            if not self.drain_release.wait(5.0):
                raise TimeoutError("bounded RTL tail release was not signaled")
            self.tail_returned = True
            return SimpleNamespace(frame=self.make_frame(1), coalesced_frames=2)
        return SimpleNamespace(frame=None, coalesced_frames=0)

    def metrics(self):
        if self.stops:
            self.metrics_after_stop += 1
        return super().metrics()

    def stop(self, timeout_ms: int):
        self.stop_entered.set()
        if not self.stop_release.wait(5.0):
            raise TimeoutError("bounded RTL native Stop release was not signaled")
        return super().stop(timeout_ms)


class _TailNative(_Native):
    def __init__(self) -> None:
        super().__init__()
        self.quarantined = False
        self.fail_next_create = False

    def rtl_process_is_quarantined(self):
        return self.quarantined

    def create_rtl_runtime_control(self, _runtime, center, rate, *_args, **_kwargs):
        self.create_calls += 1
        if self.fail_next_create:
            self.fail_next_create = False
            raise RuntimeError("controlled factory failure before control return")
        self.control = _TailControl(center, rate, int(_args[6]))
        return self.control


class _ReleaseFailureExclusion(_Exclusion):
    def __init__(self) -> None:
        super().__init__()
        self.refuse_release = False

    def release_external_analyzer_rx(self, owner):
        if self.refuse_release:
            raise RuntimeError("controlled graph claim release refusal")
        super().release_external_analyzer_rx(owner)


class RtlTerminalBridgeTests(unittest.TestCase):
    def _service(self, exclusion=None):
        native = _TailNative()
        _native, provider, inventory, selection = route_fixture.RtlProductRouteTests()._selected(native)
        exclusion = exclusion or _Exclusion()
        service = RtlAnalyzerService(native, exclusion, lambda: inventory,
            lambda binding, _runtime: provider.provision_for(binding))
        cleanup_threads: list[Thread] = []

        def cleanup():
            native.quarantined = False
            if hasattr(exclusion, "refuse_release"):
                exclusion.refuse_release = False
            control = native.control
            if isinstance(control, _TailControl):
                control.drain_release.set()
                control.stop_release.set()
            for worker in cleanup_threads:
                worker.join(timeout=6.0)
            if any(worker.is_alive() for worker in cleanup_threads):
                raise AssertionError("RTL test Stop helper did not retire")
            poller = service._poller
            if poller is not None:
                poller.join(timeout=6.0)
            try:
                stopped = service.stop()
            except BaseException as error:
                raise AssertionError(f"RTL cleanup Stop raised {type(error).__name__}: {error}") from error
            poller = service._poller
            if (stopped.state is not LiveSessionState.CONNECTED or stopped.stop_required
                    or exclusion.claimed or service._control is not None or poller is not None):
                raise AssertionError("RTL cleanup left an owner, claim, or worker active")

        self.addCleanup(cleanup)
        service.bind_selection(selection)
        request = RtlLiveRequest(150_000_000, 2_400_000,
            detector="peak", source_id=RTL_SOURCE_ID)
        service.stage(RtlConfigurationPatch(request, selection.revision, 0))
        self.assertIs(service.start().state, LiveSessionState.RUNNING)
        control = native.control
        self.assertIsInstance(control, _TailControl)
        return native, exclusion, service, control, cleanup_threads

    @staticmethod
    def _sentinel_performance() -> LivePerformance:
        return LivePerformance(fft_frames_computed=77, fft_frames_dropped=6,
            iq_samples_dropped=23, iq_blocks_dropped=4, acquisition_queue_samples_dropped=11,
            acquisition_queue_blocks_dropped=2, acquisition_queue_depth=3,
            spectrum_queue_depth=5, snapshots_superseded=9,
            bridge_native_frames_polled=90, bridge_frames_coalesced=80,
            bridge_frames_published=70, iq_blocks_received=17, iq_block_rate_hz=13.5,
            iq_sample_rate_hz=2_400_000.0, analytical_fft_rate_hz=25.0,
            rate_observation_interval_s=0.75)

    def _start_stop_thread(self, service, workers):
        result = []

        def run_stop():
            try:
                result.append(service.stop())
            except BaseException as error:
                result.append(error)

        worker = Thread(target=run_stop, daemon=False)
        workers.append(worker)
        worker.start()
        return worker, result

    def _assert_tail_processed(self, service, control, *, published: bool):
        self.assertTrue(control.tail_returned)
        self.assertIsNone(service.first_fault_diagnostic())
        self.assertEqual((service._bridge_polled, service._bridge_coalesced,
                          service._bridge_published), (3, 2, 1))
        snapshot = service.current_snapshot()
        if published:
            self.assertIs(snapshot.state, LiveSessionState.RUNNING)
            self.assertIsNotNone(snapshot.spectrum)
            self.assertEqual(int(snapshot.sequence), 1)
        else:
            self.assertIs(snapshot.state, LiveSessionState.ERROR)
            self.assertIsNone(snapshot.spectrum)

    def _join_stop_thread(self, worker, result):
        worker.join(timeout=3.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(result), 1)
        if isinstance(result[0], BaseException):
            raise result[0]
        return result[0]

    def test_successful_stop_caches_actual_poll_tail_and_preserves_other_metrics(self):
        _native, exclusion, service, control, workers = self._service()
        self.assertTrue(control.drain_entered.wait(2.0))
        worker, result = self._start_stop_thread(service, workers)
        control.drain_release.set()
        self.assertTrue(control.stop_entered.wait(3.0))  # poller joined before native Stop.
        self._assert_tail_processed(service, control, published=True)
        with service._lock:
            service._snapshot = replace(service._snapshot,
                performance=self._sentinel_performance())
        control.stop_release.set()
        stopped = self._join_stop_thread(worker, result)
        self.assertIs(stopped.state, LiveSessionState.CONNECTED)
        self.assertEqual((stopped.performance.bridge_native_frames_polled,
                          stopped.performance.bridge_frames_coalesced,
                          stopped.performance.bridge_frames_published), (3, 2, 1))
        self.assertEqual((stopped.performance.fft_frames_computed,
                          stopped.performance.fft_frames_dropped,
                          stopped.performance.iq_samples_dropped,
                          stopped.performance.iq_blocks_dropped,
                          stopped.performance.snapshots_superseded,
                          stopped.performance.iq_blocks_received,
                          stopped.performance.iq_sample_rate_hz,
                          stopped.performance.analytical_fft_rate_hz,
                          stopped.performance.rate_observation_interval_s),
                         (77, 6, 23, 4, 9, 17, 2_400_000.0, 25.0, 0.75))
        self.assertFalse(exclusion.claimed)
        self.assertEqual(control.metrics_after_stop, 0)

    def test_poller_join_refusal_retains_owner_and_does_not_finalize_cache(self):
        _native, exclusion, service, control, _workers = self._service()
        self.assertTrue(control.drain_entered.wait(2.0))
        failed = service.stop()  # Uses the existing one-second join bound.
        self.assertIs(failed.state, LiveSessionState.ERROR)
        self.assertTrue(failed.stop_required)
        self.assertTrue(exclusion.claimed)
        self.assertEqual(control.stops, 0)
        self.assertEqual((failed.performance.bridge_native_frames_polled,
                          failed.performance.bridge_frames_coalesced,
                          failed.performance.bridge_frames_published), (0, 0, 0))
        control.drain_release.set()
        worker, result = self._start_stop_thread(service, _workers)
        self.assertTrue(control.stop_entered.wait(3.0))
        self._assert_tail_processed(service, control, published=False)
        control.stop_release.set()
        recovered = self._join_stop_thread(worker, result)
        self.assertIs(recovered.state, LiveSessionState.CONNECTED)
        self.assertEqual((recovered.performance.bridge_native_frames_polled,
                          recovered.performance.bridge_frames_coalesced,
                          recovered.performance.bridge_frames_published), (3, 2, 1))
        self.assertFalse(exclusion.claimed)

    def test_quarantine_refusal_preserves_cache_then_explicit_stop_recovers(self):
        native, exclusion, service, control, workers = self._service()
        self.assertTrue(control.drain_entered.wait(2.0))
        worker, result = self._start_stop_thread(service, workers)
        control.drain_release.set()
        self.assertTrue(control.stop_entered.wait(3.0))
        self._assert_tail_processed(service, control, published=True)
        with service._lock:
            service._snapshot = replace(service._snapshot,
                performance=self._sentinel_performance())
        native.quarantined = True
        control.stop_release.set()
        failed = self._join_stop_thread(worker, result)
        self.assertTrue(failed.stop_required)
        self.assertTrue(exclusion.claimed)
        self.assertEqual((failed.performance.bridge_native_frames_polled,
                          failed.performance.bridge_frames_coalesced,
                          failed.performance.bridge_frames_published), (90, 80, 70))
        native.quarantined = False
        recovered = service.stop()
        self.assertIs(recovered.state, LiveSessionState.CONNECTED)
        self.assertEqual((recovered.performance.bridge_native_frames_polled,
                          recovered.performance.bridge_frames_coalesced,
                          recovered.performance.bridge_frames_published), (3, 2, 1))
        self.assertFalse(exclusion.claimed)
        self.assertEqual(control.metrics_after_stop, 0)

    def test_claim_release_refusal_preserves_cache_until_release_recovers(self):
        exclusion = _ReleaseFailureExclusion()
        _native, _exclusion, service, control, workers = self._service(exclusion)
        self.assertTrue(control.drain_entered.wait(2.0))
        worker, result = self._start_stop_thread(service, workers)
        control.drain_release.set()
        self.assertTrue(control.stop_entered.wait(3.0))
        self._assert_tail_processed(service, control, published=True)
        with service._lock:
            service._snapshot = replace(service._snapshot,
                performance=self._sentinel_performance())
        exclusion.refuse_release = True
        control.stop_release.set()
        failed = self._join_stop_thread(worker, result)
        self.assertTrue(failed.stop_required)
        self.assertTrue(exclusion.claimed)
        self.assertEqual((failed.performance.bridge_native_frames_polled,
                          failed.performance.bridge_frames_coalesced,
                          failed.performance.bridge_frames_published), (90, 80, 70))
        exclusion.refuse_release = False
        recovered = service.stop()
        self.assertIs(recovered.state, LiveSessionState.CONNECTED)
        self.assertEqual((recovered.performance.bridge_native_frames_polled,
                          recovered.performance.bridge_frames_coalesced,
                          recovered.performance.bridge_frames_published), (3, 2, 1))
        self.assertFalse(exclusion.claimed)

    def test_repeated_stop_and_new_failed_activation_do_not_reinject_old_counts(self):
        native, _exclusion, service, control, workers = self._service()
        self.assertTrue(control.drain_entered.wait(2.0))
        worker, result = self._start_stop_thread(service, workers)
        control.drain_release.set()
        self.assertTrue(control.stop_entered.wait(3.0))
        self._assert_tail_processed(service, control, published=True)
        control.stop_release.set()
        first_stop = self._join_stop_thread(worker, result)
        self.assertEqual((first_stop.performance.bridge_native_frames_polled,
                          first_stop.performance.bridge_frames_coalesced,
                          first_stop.performance.bridge_frames_published), (3, 2, 1))

        with service._lock:
            cleared = replace(service._snapshot.performance,
                bridge_native_frames_polled=0, bridge_frames_coalesced=0,
                bridge_frames_published=0)
            service._snapshot = replace(service._snapshot, performance=cleared)
        repeated = service.stop()
        self.assertEqual((repeated.performance.bridge_native_frames_polled,
                          repeated.performance.bridge_frames_coalesced,
                          repeated.performance.bridge_frames_published), (0, 0, 0))

        current = service.current_snapshot()
        request = current.rtl_request
        self.assertIsNotNone(request)
        service.stage(RtlConfigurationPatch(request, current.selection_revision,
            int(current.generation)))
        native.fail_next_create = True
        failed = service.start()
        self.assertIs(failed.state, LiveSessionState.ERROR)
        self.assertTrue(failed.stop_required)
        released = service.stop()
        self.assertIs(released.state, LiveSessionState.CONNECTED)
        self.assertEqual((released.performance.bridge_native_frames_polled,
                          released.performance.bridge_frames_coalesced,
                          released.performance.bridge_frames_published), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
