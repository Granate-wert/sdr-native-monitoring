"""Failed read-only release remains reachable; no DLL/device access."""

from __future__ import annotations

import ctypes
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sdr_monitor.services.hackrf_activation_preflight import (
    HackrfActivationPreflightReason,
    HackrfActivationPreflightService,
    HackrfRuntimeIdentityProbe,
)
from sdr_monitor.services.hackrf_capability_adapter import (
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfCapabilityObservationError,
    HackrfReadOnlyProbe,
)
from sdr_monitor.services.libhackrf_read_only import LibhackrfReadOnlyPort
from sdr_monitor.services.libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort
from sdr_monitor.services.tinysa_capability_adapter import (
    TinySaCapabilityAdapter,
    TinySaCapabilityObservationError,
    TinySaModel,
    TinySaReadOnlyProbe,
)
from tests.test_r11o_hackrf_identity_activation_preflight import _plan


class _ProbePort:
    def __init__(self, value):
        self.value = value
        self.close_fails = True
        self.probe_fails = False
        self.calls = []

    def probe(self):
        self.calls.append("probe")
        if self.probe_fails:
            raise RuntimeError("PRIVATE probe detail")
        return self.value

    def close(self):
        self.calls.append("close")
        if self.close_fails:
            raise RuntimeError("PRIVATE close detail")


class _Dependency:
    def __init__(self, calls):
        self.calls = calls
        self.fail = False

    def close(self):
        self.calls.append("dependency_close")
        if self.fail:
            raise RuntimeError("PRIVATE dependency detail")


class _Dll:
    def __init__(self):
        self.calls = []
        self.failures = {}
        self.list = SimpleNamespace(contents=SimpleNamespace(devicecount=1, usb_board_ids=[0x6089]))

    def _call(self, name):
        self.calls.append(name)
        value = self.failures.get(name, 0)
        if isinstance(value, Exception):
            raise value
        return value

    def hackrf_init(self):
        return self._call("init")

    def hackrf_device_list(self):
        self._call("list")
        return self.list

    def hackrf_device_list_open(self, _list, _index, pointer):
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.c_void_p(123)
        return self._call("open")

    def hackrf_close(self, _device):
        return self._call("device_close")

    def hackrf_device_list_free(self, _list):
        return self._call("list_free")

    def hackrf_exit(self):
        return self._call("exit")


def _owned_port(port_class, dll, dependency):
    port = object.__new__(port_class)
    port._dll = dll
    port._list = dll.list
    port._device = ctypes.c_void_p(123)
    port._initialized = True
    port._closed = False
    port._release_started = False
    port._probe_failed = False
    port._device_close_ambiguous = False
    port._dependency_handle = dependency
    port._lock = threading.RLock()
    return port


class ReadOnlyProviderCleanupTests(unittest.TestCase):
    def test_capability_providers_retain_failed_close_and_block_new_factory(self):
        cases = (
            (HackrfCapabilityAdapter, HackrfCapabilityObservationError,
             HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, (0, 0, 1, 2), "fixture-fw", 0x0107)),
            (TinySaCapabilityAdapter, TinySaCapabilityObservationError,
             TinySaReadOnlyProbe(TinySaModel.ULTRA, "fixture-unit", "fixture-fw")),
        )
        for provider_class, error_class, value in cases:
            for probe_fails in (False, True):
                with self.subTest(provider=provider_class.__name__, probe_fails=probe_fails):
                    port = _ProbePort(value)
                    port.probe_fails = probe_fails
                    created = []
                    def factory(port=port, created=created):
                        created.append(port)
                        return port
                    provider = provider_class(factory)
                    with self.assertRaises(error_class) as caught:
                        provider.observe()
                    self.assertNotIn("PRIVATE", str(caught.exception))
                    with self.assertRaises(error_class):
                        provider.observe()
                    self.assertEqual(len(created), 1)
                    self.assertEqual(port.calls, ["probe", "close"])
                    self.assertTrue(provider.cleanup_pending)
                    with self.assertRaises(error_class):
                        provider.close()
                    self.assertTrue(provider.cleanup_pending)
                    port.close_fails = port.probe_fails = False
                    provider.close()
                    provider.close()
                    self.assertFalse(provider.cleanup_pending)
                    self.assertEqual(port.calls.count("close"), 3)
                    self.assertIsNotNone(provider.observe().snapshot)
                    self.assertEqual(len(created), 2)

    def test_preflight_cannot_issue_permit_or_reopen_until_explicit_release(self):
        port = _ProbePort(HackrfRuntimeIdentityProbe(HackrfBoardKind.HACKRF_ONE, (0, 0, 0x010961DC, 0x2B78454F)))
        created = []
        def factory():
            created.append(port)
            return port
        provider = HackrfActivationPreflightService(factory)
        plan = _plan()
        for _ in range(2):
            result = provider.verify(plan)
            self.assertIsNone(result.permit)
            self.assertIs(result.reason, HackrfActivationPreflightReason.RUNTIME_OBSERVATION)
        self.assertEqual(created, [port])
        self.assertEqual(port.calls, ["probe", "close"])
        self.assertTrue(provider.cleanup_pending)
        port.close_fails = False
        provider.close()
        provider.close()
        self.assertFalse(provider.cleanup_pending)
        self.assertTrue(provider.verify(plan).accepted)

    def test_successful_close_after_probe_error_allows_next_explicit_observation(self):
        port = _ProbePort(TinySaReadOnlyProbe(TinySaModel.BASIC, "fixture-unit", "fixture-fw"))
        port.close_fails = False
        port.probe_fails = True
        provider = TinySaCapabilityAdapter(lambda: port)
        with self.assertRaises(TinySaCapabilityObservationError):
            provider.observe()
        self.assertFalse(provider.cleanup_pending)
        port.probe_fails = False
        self.assertEqual(provider.observe().analyzer_semantics.reported_unit, "dBm")
        self.assertEqual(port.calls, ["probe", "close", "probe", "close"])

    def test_competing_observation_cannot_open_over_a_failed_release(self):
        port = _ProbePort(TinySaReadOnlyProbe(TinySaModel.ULTRA, "fixture-unit", "fixture-fw"))
        entered, proceed, competitor = threading.Event(), threading.Event(), threading.Event()
        original_probe = port.probe
        def blocking_probe():
            entered.set()
            if not proceed.wait(3):
                raise RuntimeError("fixture barrier expired")
            return original_probe()
        port.probe = blocking_probe
        created = []
        def factory():
            created.append(port)
            return port
        provider = TinySaCapabilityAdapter(factory)
        def observe(*, competing=False):
            if competing:
                competitor.set()
            try:
                provider.observe()
            except TinySaCapabilityObservationError:
                return "rejected"
            return "published"
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(observe)
            try:
                self.assertTrue(entered.wait(1))
                second = pool.submit(observe, competing=True)
                self.assertTrue(competitor.wait(1))
            finally:
                proceed.set()
            self.assertEqual(first.result(timeout=3), "rejected")
            self.assertEqual(second.result(timeout=3), "rejected")
        self.assertEqual(created, [port])
        self.assertEqual(port.calls, ["probe", "close"])
        port.close_fails = False
        provider.close()

    def test_factory_failure_leaves_no_owner_and_allows_next_explicit_attempt(self):
        attempts = []
        def factory():
            attempts.append(1)
            raise RuntimeError("PRIVATE factory detail")
        provider = HackrfCapabilityAdapter(factory)
        for _ in range(2):
            with self.assertRaises(HackrfCapabilityObservationError) as caught:
                provider.observe()
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertFalse(provider.cleanup_pending)
        provider.close()
        self.assertEqual(len(attempts), 2)


class ConcreteHackrfReadOnlyCleanupTests(unittest.TestCase):
    def test_error_return_consumes_device_and_retry_releases_only_dependencies(self):
        for failure in (-1, -1001):
            with self.subTest(failure=type(failure).__name__):
                dll = _Dll()
                dependency = _Dependency(dll.calls)
                port = _owned_port(LibhackrfReadOnlyPort, dll, dependency)
                dll.failures["device_close"] = failure
                with self.assertRaises(RuntimeError) as caught:
                    port.close()
                self.assertNotIn("PRIVATE", str(caught.exception))
                self.assertEqual(dll.calls, ["device_close"])
                self.assertFalse(port._device.value)
                self.assertFalse(port._closed)
                self.assertIs(port._dll, dll)
                self.assertIs(port._list, dll.list)
                self.assertTrue(port._initialized)
                with self.assertRaises(RuntimeError):
                    port.probe()
                self.assertEqual(dll.calls, ["device_close"])
                dll.failures.clear()
                port.close()
                port.close()
                self.assertEqual(dll.calls, ["device_close", "list_free", "exit", "dependency_close"])
                self.assertTrue(port._closed)
                self.assertFalse(port._device.value)
                self.assertIsNone(port._dll)

    def test_foreign_close_exception_is_quarantined_without_pointer_retry(self):
        dll = _Dll()
        dependency = _Dependency(dll.calls)
        port = _owned_port(LibhackrfReadOnlyPort, dll, dependency)
        dll.failures["device_close"] = RuntimeError("PRIVATE SDK detail")
        provider = HackrfCapabilityAdapter(lambda: port)
        with self.assertRaises(HackrfCapabilityObservationError):
            provider.observe()
        self.assertTrue(provider.cleanup_pending)
        dll.failures.clear()
        for _ in range(2):
            with self.assertRaises(HackrfCapabilityObservationError):
                provider.close()
            with self.assertRaises(HackrfCapabilityObservationError):
                provider.observe()
        self.assertTrue(provider.cleanup_pending)
        self.assertEqual(dll.calls, ["device_close"])
        self.assertEqual(port._device.value, 123)
        self.assertIs(port._dll, dll)
        self.assertIs(port._dependency_handle, dependency)
        self.assertFalse(port._closed)

    def test_close_phase_failures_preserve_cursor_without_repeating_completed_phases(self):
        for port_class in (LibhackrfReadOnlyPort, LibhackrfRuntimeIdentityPort):
            for stage in ("list_free", "exit", "dependency_close"):
                with self.subTest(port=port_class.__name__, stage=stage):
                    dll = _Dll()
                    dependency = _Dependency(dll.calls)
                    port = _owned_port(port_class, dll, dependency)
                    if stage == "dependency_close":
                        dependency.fail = True
                    else:
                        dll.failures[stage] = -1 if stage == "exit" else RuntimeError("PRIVATE phase detail")
                    with self.assertRaises(RuntimeError):
                        port.close()
                    self.assertFalse(port._closed)
                    self.assertIs(port._dll, dll)
                    calls_before = list(dll.calls)
                    with self.assertRaises(RuntimeError):
                        port.probe()
                    self.assertEqual(dll.calls, calls_before)
                    dll.failures.clear()
                    dependency.fail = False
                    port.close()
                    port.close()
                    self.assertTrue(port._closed)
                    self.assertEqual(dll.calls.count(stage), 2)
                    for completed in ("device_close", "list_free", "exit", "dependency_close"):
                        if completed != stage and completed in dll.calls:
                            self.assertEqual(dll.calls.count(completed), 1)

    def test_construction_is_sdk_free_so_partial_open_owner_can_be_retained(self):
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), \
             patch("sdr_monitor.services.libhackrf_read_only.ctypes.CDLL") as load:
            port = LibhackrfReadOnlyPort(Path("fake.dll"), Path("fake-runtime"))
            load.assert_not_called()
            port.close()
            load.assert_not_called()

    def test_partial_open_and_failed_close_stays_on_provider_without_new_sdk_calls(self):
        dll = _Dll()
        dll.failures.update(open=-1, device_close=-1)
        dependency = _Dependency(dll.calls)
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), \
             patch("sdr_monitor.services.libhackrf_read_only.ctypes.CDLL", return_value=dll), \
             patch("sdr_monitor.services.libhackrf_read_only.os_add_dll_directory", return_value=dependency), \
             patch.object(LibhackrfReadOnlyPort, "_bind_allowlist"):
            port = LibhackrfReadOnlyPort(Path("fake.dll"), Path("fake-runtime"))
            provider = HackrfCapabilityAdapter(lambda: port)
            with self.assertRaises(HackrfCapabilityObservationError):
                provider.observe()
            self.assertTrue(provider.cleanup_pending)
            self.assertEqual(dll.calls, ["init", "list", "open", "device_close"])
            with self.assertRaises(HackrfCapabilityObservationError):
                provider.observe()
            self.assertEqual(dll.calls, ["init", "list", "open", "device_close"])
            dll.failures.clear()
            provider.close()
            self.assertFalse(provider.cleanup_pending)
            self.assertTrue(port._closed)

    def test_failed_enumeration_open_keeps_partial_owner_until_explicit_close(self):
        dll = _Dll()
        dll.failures["list"] = RuntimeError("PRIVATE enumeration detail")
        dependency = _Dependency(dll.calls)
        with patch.object(Path, "is_file", return_value=True), patch.object(Path, "is_dir", return_value=True), \
             patch("sdr_monitor.services.libhackrf_runtime_identity.ctypes.CDLL", return_value=dll), \
             patch("sdr_monitor.services.libhackrf_runtime_identity.os_add_dll_directory", return_value=dependency), \
             patch.object(LibhackrfRuntimeIdentityPort, "_bind_allowlist"):
            port = LibhackrfRuntimeIdentityPort(Path("fake.dll"), Path("fake-runtime"))
            with self.assertRaises(RuntimeError) as caught:
                port.probe()
            self.assertNotIn("PRIVATE", str(caught.exception))
            self.assertTrue(port._initialized)
            self.assertIs(port._dll, dll)
            self.assertEqual(dll.calls, ["init", "list"])
            with self.assertRaises(RuntimeError):
                port.probe()
            self.assertEqual(dll.calls, ["init", "list"])
            dll.failures.clear()
            port.close()
            self.assertEqual(dll.calls, ["init", "list", "exit", "dependency_close"])
            self.assertTrue(port._closed)


if __name__ == "__main__":
    unittest.main()
