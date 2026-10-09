"""G03 software witness: mock producer death through ORIGINAL Live/pane owners.

The dependency replaced here is the native engine, not poll_bundles/session/pump.
An actual mock producer thread exits on an injected exception and exposes ERROR.
This is not real C++/SDK worker, USB-loss, RF, Qt-paint or release qualification.
"""

from __future__ import annotations

from dataclasses import replace
from threading import Event, Lock, Thread, get_ident
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.device_capabilities import stable_identity_key
from sdr_monitor.domain.live import LiveConfiguration, LiveSessionState
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection, ReceiverEndpoint,
)
from sdr_monitor.services.ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.pane_resource_diagnostics import PaneFailureStage
from sdr_monitor.services.pane_resource_session import PaneResourceSession
from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2_pane_delivery_queue import PaneFairDeliveryQueue
from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PaneResourcePump

from tests.test_app07_ad936x_rtbw_pane_owner import _ReadbackEngine, _ReadbackNative, _UnusedSweep
from tests.test_app07_shared_capture_schedule import pane, profile
from tests.test_s15_live_rx_bridge import _make_frame, _wait_for


class _ThreadedEngine(_ReadbackEngine):
    """One bounded mock reduced-frame slot, produced ONLY on its worker thread."""

    def __init__(self, uri: str, timeout_ms: int) -> None:
        super().__init__(uri, timeout_ms)
        self._lock = Lock()
        self._wake = Event()
        self._stop = Event()
        self._crash = Event()
        self.terminated = Event()
        self.thread: Thread | None = None
        self.failure_thread_id: int | None = None
        self.failure: str | None = None
        self.join_refused = False
        self.produced = 0
        self._latest: object | None = None

    def configure(self, config):
        self.last_config = config
        return super().configure(config)

    def start(self) -> None:
        super().start()
        self.thread = Thread(target=self._produce, name=f"g03-mock-producer-{self.uri}")
        self.thread.start()

    def emit(self) -> None:
        self._wake.set()

    def crash(self) -> None:
        self._crash.set()
        self._wake.set()

    def _produce(self) -> None:
        try:
            while True:
                self._wake.wait()
                self._wake.clear()
                if self._stop.is_set():
                    return
                if self._crash.is_set():
                    raise RuntimeError("test-only producer failure")
                device = self.last_config.args[0].args
                with self._lock:
                    self.produced += 1
                    frame = _make_frame(
                        self.produced, center_hz=device[2], sample_rate_hz=device[3],
                        source_id=device[0], config_generation=self.configure_calls,
                    )
                    frame.frequencies_hz = device[2] + (np.arange(4096) - 2048) * (device[3] / 4096)
                    self._latest = frame
        except RuntimeError as error:
            # Mirrors the native worker boundary's published ERROR, not a caller exception.
            with self._lock:
                self.failure_thread_id = get_ident()
                self.failure = str(error)
                self._states = [LiveSessionState.ERROR]
        finally:
            self.terminated.set()

    def poll_spectrum_frames(self, max_items: int) -> list[object]:
        del max_items
        with self._lock:
            frame, self._latest = self._latest, None
            return [] if frame is None else [frame]

    def state(self) -> object:
        with self._lock:
            return self._states[0]

    def request_stop(self) -> None:
        super().request_stop()
        self._stop.set()
        self._wake.set()

    def join(self) -> None:
        super().join()
        if self.join_refused:
            raise RuntimeError("test-only missing producer join receipt")
        assert self.thread is not None
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("mock producer did not terminate")

    def disconnect(self) -> None:
        if self.thread is None or self.thread.is_alive() or self.join_calls == 0:
            raise AssertionError("disconnect before confirmed producer join")
        super().disconnect()


class _ThreadedNative(_ReadbackNative):
    def PlutoFixedBandEngine(self, uri: str, timeout_ms: int) -> _ThreadedEngine:
        engine = _ThreadedEngine(uri, timeout_ms)
        self.engines.append(engine)
        return engine


class ProducerWorkerFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.natives = []
        self.services = []
        self.lives = []
        groups, slots, owners, keys, factories = [], [], {}, {}, {}
        for index, name in enumerate(("failed", "peer"), 1):
            native = _ThreadedNative(f"ip:g03-mock-{name}.local")
            service = NativeLiveSessionService(native)
            device = service.discover_devices()[0]
            service.select_device(device.device_id)
            service.apply_configuration(LiveConfiguration(
                center_hz=104e6, sample_rate_hz=20e6, analog_bandwidth_hz=10e6,
                gain_db=20, fft_size=4096, detector="sample", persistence_enabled=False,
                persistence_mode="disabled",
            ))
            analyzer = AnalyzerSessionApplicationService(service, _UnusedSweep(),
                                                         start_live=service.start_admitted)
            live = LiveSessionApplicationService(service, analyzer=analyzer)
            endpoint = f"rx-{name}"
            groups.append(AcquisitionGroup(f"group-{name}", name, (
                ReceiverEndpoint(endpoint, device.device_id, name, ReceiverChainSelection.RX1),)))
            start = 100e6 if index == 1 else 140e6
            slots.append(PaneLayoutSlot(index, pane(name, endpoint, start, start + 8e6,
                                                   ReceiverBindingMode.DEDICATED_PARALLEL)))
            owners[name] = Ad936xRtbwPaneOwner(live, physical_stream_resource_id=name,
                                              source_id=device.device_id, receiver_endpoint_id=endpoint)
            def make_owner(live=live, name=name, source_id=device.device_id, endpoint=endpoint):
                return Ad936xRtbwPaneOwner(live, physical_stream_resource_id=name,
                                           source_id=source_id, receiver_endpoint_id=endpoint)
            factories[name] = make_owner
            # Mock stable identities, not discovery/parallel physical-independence evidence.
            keys[device.device_id] = stable_identity_key(f"g03-mock-physical-{name}")
            self.natives.append(native)
            self.services.append(service)
            self.lives.append(live)
        layout = compile_pane_layout(tuple(slots) + (PaneLayoutSlot(3), PaneLayoutSlot(4)),
                                    tuple(groups), {"capture": replace(
                                        profile(8e6, CaptureMeasurementMode.RTBW),
                                        sample_rate_hz=20e6, analog_bandwidth_hz=10e6, detector="sample")})
        assert layout.schedule is not None
        self.leases = ReceiverLeaseManager(max_active_resources=4)
        self.session = PaneResourceSession(layout.schedule, tuple(groups), owners, self.leases,
                                           source_identity_keys=keys, owner_factories=factories)
        self.queue = PaneFairDeliveryQueue(("failed", "peer"))
        self.pump = PaneResourcePump(self.session, layout, PaneDeliveryPreparer(
            layout, tuple(groups), PresentationAllocationBudget()), self.queue, poll_interval_s=0.004)
        self.session.apply()
        self.pump.activate()
        self.addCleanup(self._cleanup)
        self.activations = {name: self.pump.start_resource(name).result(timeout=3)
                            for name in ("failed", "peer")}
        self.peer_poller = self.services[1]._poller

    def _cleanup(self) -> None:
        for native in self.natives:
            for engine in native.engines:
                engine.join_refused = False
        for future in self.pump.stop_all().values():
            future.result(timeout=3)
        self.pump.join_after_stop(3)
        self.assertEqual(self.leases.active_resource_count, 0)
        self.assertEqual(self.queue.pending_count, 0)
        self.assertTrue(all(state.phase is PanePumpPhase.STOPPED for state in self.pump.snapshot()))
        self.assertTrue(all(service._poller is None and service._engine is None for service in self.services))
        for native in self.natives:
            for engine in native.engines:
                self.assertTrue(engine.terminated.is_set())
                self.assertFalse(engine.thread.is_alive())
                self.assertEqual(engine.disconnect_calls, 1)

    def _state(self, name: str):
        return next(state for state in self.pump.snapshot() if state.physical_stream_resource_id == name)

    def _deliver(self, index: int):
        self.queue.drain()
        engine = self.natives[index].engines[-1]
        engine.emit()
        name = ("failed", "peer")[index]
        packets = []

        def received() -> bool:
            packets.extend(self.queue.drain())
            return any(packet.delivery.pane_id == name for packet in packets)

        self.assertTrue(_wait_for(received), f"no original-owner delivery for {name}: "
                        f"{self._state(name)!r}; cached={self.services[index].latest_snapshot().state}")
        return next(packet for packet in packets if packet.delivery.pane_id == name)

    def _crash_target(self) -> None:
        target = self.natives[0].engines[0]
        target.crash()
        self.assertTrue(target.terminated.wait(3))
        target.thread.join(timeout=3)
        self.assertFalse(target.thread.is_alive())
        self.assertEqual(target.failure_thread_id, target.thread.ident)
        self.assertNotEqual(target.failure_thread_id, get_ident())
        self.assertEqual(target.failure, "test-only producer failure")
        self.assertTrue(_wait_for(lambda: self._state("failed").phase is PanePumpPhase.STOP_REQUIRED),
                        f"worker died, pump={self._state('failed')!r}, "
                        f"Live={self.services[0].latest_snapshot()!r}")
        self.assertIs(self.services[0].latest_snapshot().state, LiveSessionState.ERROR)
        self.assertIsNone(self._state("failed").activation)
        self.assertIs(self._state("failed").first_failure.stage, PaneFailureStage.OWNER_POLL)
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertEqual((target.join_calls, target.disconnect_calls), (0, 0))
        self.assertEqual(len(self.natives[0].engines), 1)
        with self.assertRaisesRegex(RuntimeError, "not idle"):
            self.pump.start_resource("failed")

    def _peer_advances(self, previous) -> object:
        current = self._deliver(1)
        self.assertGreater(current.bundle.spectrum.sequence, previous.bundle.spectrum.sequence)
        self.assertEqual(current.delivery.host_activation_serial, previous.delivery.host_activation_serial)
        self.assertEqual(current.bundle.acquisition_epoch, previous.bundle.acquisition_epoch)
        for field in ("source_id", "session_id", "receiver_id", "acquisition_epoch",
                      "config_generation", "clock_domain", "accumulation_id", "unit"):
            self.assertEqual(getattr(current.bundle.identity, field), getattr(previous.bundle.identity, field))
        np.testing.assert_array_equal(current.bundle.frequencies_hz, previous.bundle.frequencies_hz)
        self.assertEqual(len(self.natives[1].engines), 1)
        self.assertEqual(self.natives[1].engines[0].disconnect_calls, 0)
        self.assertIs(self.services[1]._poller, self.peer_poller)
        self.assertTrue(self.peer_poller.is_alive())
        self.assertIs(self._state("peer").phase, PanePumpPhase.RUNNING)
        return current

    def test_producer_death_requires_explicit_stop_join_then_explicit_fresh_start(self) -> None:
        initial = self._deliver(0)
        peer = self._deliver(1)
        self._crash_target()
        peer = self._peer_advances(peer)
        self.pump.stop_resource("failed").result(timeout=3)
        target = self.natives[0].engines[0]
        self.assertEqual((target.join_calls, target.disconnect_calls), (1, 1))
        self.assertEqual(self.leases.active_resource_count, 1)
        self.assertIsNone(self.services[0]._poller)
        self.assertIsNone(self.services[0]._engine)
        peer = self._peer_advances(peer)
        # No hidden rebind; ONLY this explicit command constructs another producer.
        self.assertEqual(len(self.natives[0].engines), 1)
        activation = self.pump.start_resource("failed").result(timeout=3)
        self.assertGreater(activation.host_activation_serial, initial.delivery.host_activation_serial)
        self.assertEqual(len(self.natives[0].engines), 2)
        restarted = self._deliver(0)
        self.assertGreater(restarted.bundle.acquisition_epoch, initial.bundle.acquisition_epoch)
        self.assertEqual(self.leases.active_resource_count, 2)
        self._peer_advances(peer)

    def test_unconfirmed_join_retains_target_custody_and_peer_until_explicit_stop_retry(self) -> None:
        self._deliver(0)
        peer = self._deliver(1)
        self._crash_target()
        target = self.natives[0].engines[0]
        target.join_refused = True
        with self.assertRaises(RuntimeError):
            self.pump.stop_resource("failed").result(timeout=3)
        self.assertIs(self._state("failed").phase, PanePumpPhase.STOP_REQUIRED)
        self.assertIs(self._state("failed").first_failure.stage, PaneFailureStage.OWNER_POLL)
        self.assertIs(self._state("failed").cleanup_failure.stage, PaneFailureStage.STOP)
        self.assertEqual(self.leases.active_resource_count, 2)
        self.assertIs(self.services[0]._engine, target)
        self.assertEqual(target.disconnect_calls, 0)
        self.assertEqual(len(self.natives[0].engines), 1)
        with self.assertRaisesRegex(RuntimeError, "not idle"):
            self.pump.start_resource("failed")
        peer = self._peer_advances(peer)
        target.join_refused = False
        self.pump.stop_resource("failed").result(timeout=3)
        self.assertEqual((target.join_calls, target.disconnect_calls), (2, 1))
        self.assertEqual(self.leases.active_resource_count, 1)
        self._peer_advances(peer)

    def test_worker_error_before_first_spectrum_is_not_an_empty_healthy_poll(self) -> None:
        peer = self._deliver(1)
        self.assertIsNone(self.services[0].latest_snapshot().spectrum)
        self.assertIs(self._state("failed").phase, PanePumpPhase.RUNNING)
        self._crash_target()
        self.assertIsNone(self.services[0].latest_snapshot().spectrum)
        self.assertEqual(self.natives[0].engines[0].produced, 0)
        self._peer_advances(peer)
        self.pump.stop_resource("failed").result(timeout=3)
        self.assertEqual(self.natives[0].engines[0].disconnect_calls, 1)
        self.assertEqual(self.leases.active_resource_count, 1)

    def test_error_during_frame_poll_rejects_pre_error_batch_before_preparation(self) -> None:
        self._deliver(0)
        peer = self._deliver(1)
        target = self.natives[0].engines[0]
        original_poll = self.lives[0].poll_published_snapshots
        observed = []
        calls = []

        def poll_then_worker_dies():
            calls.append(True)
            # A scheduling barrier ONLY: original frame port returns a real
            # cached RUNNING batch; the independent producer then actually dies.
            snapshots = original_poll()
            self.assertTrue(snapshots)
            self.assertTrue(all(snapshot.state is LiveSessionState.RUNNING for snapshot in snapshots))
            prepared_before = self._state("failed").prepared_publications
            target.crash()
            self.assertTrue(target.terminated.wait(3))
            self.assertTrue(_wait_for(lambda: self.services[0].latest_snapshot().state is LiveSessionState.ERROR))
            # Assertions on this pump thread can be mapped to OWNER_POLL too.
            # Commit the observation ONLY after every barrier condition succeeds;
            # the test thread below refuses a swallowed assertion/timeout.
            observed.append(prepared_before)
            return snapshots

        with patch.object(self.lives[0], "poll_published_snapshots", side_effect=poll_then_worker_dies):
            self.assertTrue(_wait_for(lambda: self._state("failed").phase is PanePumpPhase.STOP_REQUIRED))
        self.assertEqual((len(calls), len(observed)), (1, 1))
        self.assertIs(self.services[0].latest_snapshot().state, LiveSessionState.ERROR)
        self.assertEqual(target.failure, "test-only producer failure")
        self.assertEqual(self._state("failed").prepared_publications, observed[0])
        self.assertIs(self._state("failed").first_failure.stage, PaneFailureStage.OWNER_POLL)
        self.assertEqual(target.failure_thread_id, target.thread.ident)
        self.assertEqual((target.join_calls, target.disconnect_calls), (0, 0))
        self._peer_advances(peer)
        self.pump.stop_resource("failed").result(timeout=3)
        self.assertEqual(target.disconnect_calls, 1)


if __name__ == "__main__":
    unittest.main()
