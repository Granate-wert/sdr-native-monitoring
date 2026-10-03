"""SAME NativeLive control authority; fake SDK and explicitly compiled mock lanes."""
from __future__ import annotations

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from sdr_monitor.domain import BackendKind, LiveConfiguration, RecordingOptions
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from sdr_monitor.services.native_continuous_sweep_factory import NativeLiveContinuousSweepDisplayService
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.native_test_dependencies import explicit_native_dependencies
from tests.test_native_live_discovery import _FakeNative

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK_DLL = os.environ.get("SDR_APP07_TEST_SWEEP_MOCK", "")


def selected(native=None):
    native = _FakeNative() if native is None else native
    service = NativeLiveSessionService(native)
    device = service.discover_devices()[0]
    service.select_device(device.device_id)
    result = service.apply_configuration(LiveConfiguration(
        sample_rate_hz=2e6, analog_bandwidth_hz=2e6, backend=BackendKind.CPU))
    assert result.error is None, result.error
    return service, native


class NativeSweepOwnerAuthorityTests(unittest.TestCase):
    def test_old_lease_cannot_assert_new_lease_authority(self):
        service, _ = selected()
        old = service.acquire_native_sweep_lease()
        old.release()
        current = service.acquire_native_sweep_lease()
        try:
            with self.assertRaisesRegex(RuntimeError, "lease"):
                old.assert_active()
            current.assert_active()
        finally:
            current.release()
            service.close_live()

    def test_idempotent_old_release_does_not_release_new_lease(self):
        service, _ = selected()
        old = service.acquire_native_sweep_lease()
        old.release()
        current = service.acquire_native_sweep_lease()
        try:
            old.release()
            current.assert_active()
            with self.assertRaises(RuntimeError):
                service.acquire_native_sweep_lease()
        finally:
            current.release()
            service.close_live()

    def test_recording_refuses_before_any_mutation_while_sweep_owns_route(self):
        for command in ("arm_native_recording", "start_native_recording_now"):
            with self.subTest(command=command):
                service, native = selected()
                lease = service.acquire_native_sweep_lease()
                before = service.latest_snapshot()
                health = service.native_recording_health()
                epoch = service._native_recording_epoch
                try:
                    with self.assertRaisesRegex(RuntimeError, "Sweep"):
                        getattr(service, command)(RecordingOptions(output_path="unused-owner-guard"))
                    self.assertEqual(service.native_recording_health(), health)
                    self.assertEqual(service._native_recording_epoch, epoch)
                    self.assertIs(service.latest_snapshot(), before)
                    self.assertEqual(native.engines, [])
                    lease.assert_active()
                finally:
                    lease.release()
                    service.close_live()

    def test_live_stop_and_shutdown_cannot_invalidate_owned_sweep_route(self):
        for command in ("stop", "close_live", "stop_and_wait"):
            with self.subTest(command=command):
                service, _ = selected()
                lease = service.acquire_native_sweep_lease()
                before = service.latest_snapshot()
                uri = service._native_uri
                try:
                    with self.assertRaisesRegex(RuntimeError, "Sweep"):
                        if command == "stop_and_wait":
                            service.stop_and_wait(1.)
                        else:
                            getattr(service, command)()
                    self.assertIs(service.latest_snapshot(), before)
                    self.assertEqual(service._native_uri, uri)
                    lease.assert_active()
                finally:
                    lease.release()
                    service.close_live()

    def test_apply_refuses_before_changing_the_leased_profile(self):
        service, native = selected()
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        before = service.latest_snapshot()
        try:
            with self.assertRaisesRegex(RuntimeError, "Sweep"):
                service.apply_configuration(replace(before.applied.applied, center_hz=150e6))
            self.assertIs(service.latest_snapshot(), before)
            factory._lease.assert_active()
            self.assertEqual(native.engines, [])
        finally:
            factory.close()
            service.close_live()

    def test_changed_owner_snapshot_refuses_before_constructor(self):
        for field in ("applied", "device", "session_id"):
            with self.subTest(field=field):
                service, native = selected()
                native.NativeContinuousSweepCoordinator = Mock()
                factory = NativeContinuousSweepPlanFactory.from_native_live(service)
                before = service.latest_snapshot()
                if field == "applied":
                    changed = replace(before.applied, applied=replace(before.applied.applied, center_hz=150e6))
                elif field == "device":
                    changed = replace(before.device, device_id="other")
                else:
                    changed = "other-session"
                service._snapshot = replace(before, **{field: changed})
                try:
                    with self.assertRaisesRegex(RuntimeError, "lease"):
                        factory.create_coordinator()
                    native.NativeContinuousSweepCoordinator.assert_not_called()
                finally:
                    service._snapshot = before
                    factory.close()
                    service.close_live()

    def test_stale_factory_refuses_both_constructor_paths_before_open(self):
        for command in ("create_display_service", "create_coordinator"):
            with self.subTest(command=command):
                service, native = selected()
                native.NativeContinuousSweepCoordinator = Mock()
                factory = NativeContinuousSweepPlanFactory.from_native_live(service)
                factory._lease.release()
                try:
                    with self.assertRaisesRegex(RuntimeError, "lease"):
                        getattr(factory, command)()
                    native.NativeContinuousSweepCoordinator.assert_not_called()
                finally:
                    factory.close()
                    service.close_live()

    def test_same_lease_cannot_construct_a_second_continuous_owner(self):
        service, native = selected()
        coordinator = SimpleNamespace(disconnect=Mock())
        native.NativeContinuousSweepCoordinator = Mock(return_value=coordinator)
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        alias = NativeContinuousSweepPlanFactory(factory._lease)
        try:
            self.assertIs(factory.create_coordinator(), coordinator)
            with self.assertRaisesRegex(RuntimeError, "owner"):
                alias.create_coordinator()
            self.assertEqual(native.NativeContinuousSweepCoordinator.call_count, 1)
            factory.close()
            coordinator.disconnect.assert_called_once()
        finally:
            factory.close()
            alias.close()
            service.close_live()

    def test_failed_disconnect_retains_same_owner_and_lease_until_retry(self):
        service, native = selected()
        coordinator = SimpleNamespace(disconnect=Mock(side_effect=[RuntimeError("close failed"), None]))
        native.NativeContinuousSweepCoordinator = Mock(return_value=coordinator)
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        factory.create_coordinator()
        try:
            with self.assertRaisesRegex(RuntimeError, "close failed"):
                factory.close()
            factory._lease.assert_active()
            with self.assertRaises(RuntimeError):
                service.acquire_native_sweep_lease()
            with self.assertRaises(RuntimeError):
                service.start_admitted()
            with self.assertRaises(RuntimeError):
                factory.create_coordinator()
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                with factory.control_transaction():
                    self.fail("failed cleanup allowed a new Start")
            self.assertEqual(native.NativeContinuousSweepCoordinator.call_count, 1)
            factory.close()
            self.assertEqual(coordinator.disconnect.call_count, 2)
            with self.assertRaises(RuntimeError):
                factory._lease.assert_active()
        finally:
            factory.close()
            service.close_live()

    def test_guarded_start_transaction_cannot_use_released_lease(self):
        service, _ = selected()
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        factory._lease.release()
        try:
            with self.assertRaises(RuntimeError):
                with factory.control_transaction():
                    self.fail("released owner entered Start transaction")
        finally:
            factory.close()
            service.close_live()

    def test_constructor_failure_retains_no_owner_and_no_hidden_retry(self):
        service, native = selected()
        native.NativeContinuousSweepCoordinator = Mock(side_effect=RuntimeError("constructor refused"))
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        try:
            with self.assertRaisesRegex(RuntimeError, "constructor refused"):
                factory.create_coordinator()
            native.NativeContinuousSweepCoordinator.assert_called_once()
            factory._lease.assert_active()
            factory.close()
            self.assertFalse(service._sweep_lease_active)
        finally:
            factory.close()
            service.close_live()

    def test_reentrant_release_during_constructor_cannot_retire_lease(self):
        service, native = selected()
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        coordinator = SimpleNamespace(disconnect=Mock())

        def construct(*_args):
            with self.assertRaisesRegex(RuntimeError, "construction"):
                factory._lease.release()
            return coordinator

        native.NativeContinuousSweepCoordinator = Mock(side_effect=construct)
        try:
            factory.create_coordinator()
            factory._lease.assert_active()
        finally:
            factory.close()
            service.close_live()
        coordinator.disconnect.assert_called_once()

    def test_start_control_transaction_serializes_release_until_start_returns(self):
        service, native = selected()
        coordinator = SimpleNamespace(disconnect=Mock())
        native.NativeContinuousSweepCoordinator = Mock(return_value=coordinator)
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        factory.create_coordinator()
        entered = threading.Event()

        def release():
            entered.set()
            factory.close()

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                with factory.control_transaction():
                    future = pool.submit(release)
                    self.assertTrue(entered.wait(2))
                    self.assertFalse(future.done())
                    coordinator.disconnect.assert_not_called()
                    factory._lease.assert_active()
                future.result(timeout=3)
            coordinator.disconnect.assert_called_once()
        finally:
            factory.close()
            service.close_live()


@explicit_native_dependencies
def run_compiled_case(path: str) -> None:
    import ctypes
    module_path = Path(path).resolve(strict=True)
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    assert spec is not None and spec.loader is not None
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
    service = NativeLiveSessionService(native)
    current = None
    factory = None
    display = None

    def refuses(command):
        try:
            command()
        except RuntimeError:
            return
        raise AssertionError("operation must refuse before I/O or control mutation")

    try:
        snapshot = service.select_manual_uri("usb:mock")
        assert snapshot.error is None
        snapshot = service.apply_configuration(LiveConfiguration(center_hz=2450e6,
            sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
            fft_size=4096, backend=BackendKind.CPU))
        assert snapshot.error is None and snapshot.device.serial == "MOCK"
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        writes = hooks.mock_iio_rf_mutation_calls()
        old = service.acquire_native_sweep_lease()
        old.release()
        current = service.acquire_native_sweep_lease()
        old.release()
        current.assert_active()
        refuses(old.assert_active)
        before, health = service.latest_snapshot(), service.native_recording_health()
        options = RecordingOptions(output_path="unused-compiled-owner-guard")
        refuses(lambda: service.arm_native_recording(options))
        refuses(lambda: service.start_native_recording_now(options))
        refuses(lambda: service.apply_configuration(replace(snapshot.applied.applied, center_hz=150e6)))
        refuses(service.close_live)
        refuses(lambda: service.stop_and_wait(1.))
        assert service.latest_snapshot() is before and service.native_recording_health() == health
        assert hooks.mock_iio_rf_mutation_calls() == writes
        factory = NativeContinuousSweepPlanFactory(current)
        factory.create_coordinator()
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_live_buffers() == 0
        refuses(lambda: NativeContinuousSweepPlanFactory(current).create_coordinator())
        assert hooks.mock_iio_live_contexts() == 1 and hooks.mock_iio_rf_mutation_calls() == writes
        factory.close()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
        fresh = service.acquire_native_sweep_lease()
        try:
            current.release()
            fresh.assert_active()
            refuses(factory.create_coordinator)
        finally:
            fresh.release()
        # Actual deferred product composition: SAME lease, constructor, Configure,
        # explicit Start, bounded reduced publication, Stop/close/release.
        display = NativeLiveContinuousSweepDisplayService(service)
        created_buffers = hooks.mock_iio_created_buffers()
        display.start(ContinuousSweepPlanRequest(100e6, 148e6, analysis_bins_per_usable_window=1024))
        # Start schedules the coordinator worker, not an immediate buffer-ready
        # receipt. Retuning can also briefly close the ONE buffer between steps.
        assert hooks.mock_iio_live_contexts() == 1
        assert hooks.mock_iio_live_buffers() in (0, 1)
        refuses(lambda: service.arm_native_recording(options))
        deadline = time.monotonic() + 10
        while True:
            publication = display.poll_latest()
            assert hooks.mock_iio_live_contexts() == 1
            assert hooks.mock_iio_live_buffers() in (0, 1)
            if publication.line is not None or publication.progress is not None:
                assert hooks.mock_iio_created_buffers() > created_buffers
                break
            assert time.monotonic() < deadline, "bounded continuous owner publication timeout"
            time.sleep(.005)
        display.stop()
        assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    finally:
        if display is not None:
            display.close()
        if factory is not None:
            factory.close()
        if current is not None:
            current.release()
        service.close_live()
    assert hooks.mock_iio_live_contexts() == hooks.mock_iio_live_buffers() == 0
    print("SAME NativeLive lease/continuous owner explicit Start compiled MOCK; no physical RF or paired product admission")


@unittest.skipUnless(MODULE and Path(MOCK_DLL).is_file(), "requires explicit matching native/mock")
class CompiledNativeSweepOwnerTests(unittest.TestCase):
    def test_actual_native_context_closed_before_lease_release_and_no_second_open(self):
        env = dict(os.environ, LIBIIO_DLL_PATH=MOCK_DLL, SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1")
        command = "from tests.test_app07_native_sweep_owner_authority import run_compiled_case; import sys; run_compiled_case(sys.argv[1])"
        result = subprocess.run([sys.executable, "-c", command, MODULE], cwd=ROOT,
            env=env, text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
