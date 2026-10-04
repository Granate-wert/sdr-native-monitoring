"""Staged matching-native MOCK only: read-only optional context observations."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = Path(os.environ.get("SDR_APP07_TEST_MOCK_LIBIIO",
                          str(ROOT / "native/sdr_core/out/build/windows-msvc-cpu-hackrf/libiio.dll")))


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit matching staged native/mock")
class PlutoConnectionObservationBindingTests(unittest.TestCase):
    def test_live_product_path_asserts_same_compiled_context_before_rf(self) -> None:
        code = r"""
import ctypes
import importlib.util
import os
import pathlib
import sys
from sdr_monitor.domain import BackendKind, LiveConfiguration, LiveSessionState
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.ad936x_identity_admission import create_identity_bound_owner
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
cookie = os.add_dll_directory(str(path.parent))
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
for name in ("mock_iio_created_contexts", "mock_iio_live_contexts", "mock_iio_rf_mutation_calls",
             "mock_iio_created_buffers"):
    getattr(hooks, name).restype = ctypes.c_int
os.environ["SDR_MOCK_LIBIIO_CONTEXT_NAME"] = "usb"
os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.42.5"
service = NativeLiveSessionService(native)
try:
    descriptor = service.discover_devices()[0]
    selected = service.select_device(descriptor.device_id)
    assert selected.state is LiveSessionState.CONNECTED, selected.error
    assert selected.device.usb_connection.device_address == 42
    assert hooks.mock_iio_live_contexts() == 0
    requested = LiveConfiguration(center_hz=2450e6, sample_rate_hz=20e6,
                                  analog_bandwidth_hz=10e6, gain_db=20,
                                  fft_size=1024, backend=BackendKind.CPU)
    service.apply_configuration(requested)
    writes = hooks.mock_iio_rf_mutation_calls()
    buffers = hooks.mock_iio_created_buffers()
    os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.43.5"
    refused = service.start_admitted()
    assert refused.state is LiveSessionState.ERROR
    assert service._engine is None
    assert hooks.mock_iio_rf_mutation_calls() == writes
    assert hooks.mock_iio_created_buffers() == buffers
    assert hooks.mock_iio_live_contexts() == 0
    os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.42.5"
    selected = service.select_device(descriptor.device_id)
    assert selected.state is LiveSessionState.CONNECTED, selected.error
    service.apply_configuration(requested)
    running = service.start_admitted()
    assert running.state is LiveSessionState.RUNNING, running.error
    assert hooks.mock_iio_live_contexts() == 1
    before = hooks.mock_iio_created_contexts()
    try:
        create_identity_bound_owner(native, "PlutoDevice", selected.device.uri, 3000,
                                    expected_serial=selected.device.serial,
                                    expected_usb_connection=selected.device.usb_connection)
    except RuntimeError as error:
        assert "already held" in str(error)
    else:
        raise AssertionError("product Live owner did not hold the native USB claim")
    assert hooks.mock_iio_created_contexts() == before
finally:
    service.close_live()
assert hooks.mock_iio_live_contexts() == 0
print("actual Live product -> compiled SAMEowner/stale-beforeRF/Start claim/StopClose PASS; MOCK only")
"""
        environment = dict(os.environ, LIBIIO_DLL_PATH=str(MOCK))
        for key in tuple(environment):
            if key.startswith("SDR_MOCK_LIBIIO_"):
                environment.pop(key)
        result = subprocess.run([sys.executable, "-c", code, MODULE], cwd=ROOT, env=environment,
                                text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_typed_usb_assertion_and_lifetime_claim_reach_all_native_owners(self) -> None:
        code = r"""
import ctypes
import importlib.util
import os
import pathlib
import sys
from sdr_monitor.services.ad936x_identity_admission import PlutoUsbConnectionExpectation, create_identity_bound_owner
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
cookie = os.add_dll_directory(str(path.parent))
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
assert native.PLUTO_USB_CONNECTION_ADMISSION_PROTOCOL_VERSION == 1
hooks = ctypes.CDLL(os.environ["LIBIIO_DLL_PATH"])
for name in ("mock_iio_created_contexts", "mock_iio_destroyed_contexts", "mock_iio_live_contexts",
             "mock_iio_rf_mutation_calls", "mock_iio_created_buffers"):
    getattr(hooks, name).restype = ctypes.c_int
os.environ["SDR_MOCK_LIBIIO_CONTEXT_NAME"] = "usb"
os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.42.5"
temporary = native.probe_pluto_context("usb:caller-alias")
assertion = PlutoUsbConnectionExpectation.from_probe(temporary)
writes = hooks.mock_iio_rf_mutation_calls()
buffers = hooks.mock_iio_created_buffers()
names = ("PlutoDevice", "PlutoFixedBandEngine", "NativeContinuousSweepCoordinator")
for name in names:
    owner = create_identity_bound_owner(native, name, "usb:caller-alias", 3000,
                                       expected_serial="MOCK", expected_usb_connection=assertion)
    try:
        before = hooks.mock_iio_created_contexts()
        for other in names:
            try:
                create_identity_bound_owner(native, other, "usb:other-interface", 3000,
                                            expected_usb_connection=assertion)
            except RuntimeError as error:
                assert "already held" in str(error)
            else:
                raise AssertionError("asserted physical resource opened twice")
        assert hooks.mock_iio_created_contexts() == before
        if name == "PlutoDevice":
            owner.stop_stream()
        elif name == "PlutoFixedBandEngine":
            # No RX was started in this metadata-only test. Preserve the
            # engine's explicit RUNNING-only Stop guard, rather than changing
            # product lifecycle just to exercise connection ownership.
            try:
                owner.stop()
            except native.ConfigurationError as error:
                assert "requires RUNNING state" in str(error)
            else:
                raise AssertionError("idle engine Stop unexpectedly admitted")
        try:
            create_identity_bound_owner(native, "PlutoDevice", "usb:alias", 3000,
                                        expected_usb_connection=assertion)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Stop released connection ownership")
    finally:
        owner.disconnect()
    assert hooks.mock_iio_live_contexts() == 0

# A stale temporary observation must not admit a new, different owned context.
os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.43.5"
for name in names:
    before = hooks.mock_iio_created_contexts()
    try:
        create_identity_bound_owner(native, name, "usb:caller-alias", 3000,
                                    expected_usb_connection=assertion)
    except RuntimeError as error:
        assert "not confirmed" in str(error)
    else:
        raise AssertionError("stale connection assertion admitted")
    assert hooks.mock_iio_created_contexts() == before + 1
    assert hooks.mock_iio_live_contexts() == 0
os.environ["SDR_MOCK_LIBIIO_BACKEND_URI"] = "usb:2.42.5"
owner = create_identity_bound_owner(native, "PlutoDevice", "usb:alias", 3000,
                                   expected_usb_connection=assertion)
owner.disconnect()
assert hooks.mock_iio_live_contexts() == 0
assert hooks.mock_iio_rf_mutation_calls() == writes
assert hooks.mock_iio_created_buffers() == buffers
print("typed SAME-context assertion/3 native owners/duplicate-before-open/device Stop retains/idle engine Stop refuses/cleanup/reopen PASS; mock only")
"""
        environment = dict(os.environ, LIBIIO_DLL_PATH=str(MOCK))
        for key in tuple(environment):
            if key.startswith("SDR_MOCK_LIBIIO_"):
                environment.pop(key)
        result = subprocess.run([sys.executable, "-c", code, MODULE], cwd=ROOT, env=environment,
                                text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_optional_observations_are_raw_readonly_and_owned(self) -> None:
        code = r"""
import importlib.util
import os
import pathlib
import sys
path = pathlib.Path(sys.argv[1]).resolve(strict=True)
cookie = os.add_dll_directory(str(path.parent))
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
fields = ("backend_uri", "usb_vendor_id", "usb_product_id", "usb_serial")
for mode, expected in [
    ("missing", (None, None, None, None)),
    ("empty", ("", "", "", "")),
    ("raw", ("not-a-USB-URI", "wrong-vendor", "", "CONTRADICTS-HARDWARE")),
]:
    os.environ["SDR_MOCK_LIBIIO_CONNECTION_ATTR_MODE"] = mode
    temporary = native.probe_pluto_context("usb:mock")
    device = native.PlutoDevice("usb:mock")
    try:
        owned = device.probe()
        assert owned.uri == temporary.uri == "usb:mock"
        assert tuple(getattr(owned, field) for field in fields) == expected
        assert tuple(getattr(temporary, field) for field in fields) == expected
        assert device.receiver_topology().context.backend_uri == owned.backend_uri
        assert device.streaming is False
        for field in fields:
            try:
                setattr(owned, field, "invented")
            except AttributeError:
                pass
            else:
                raise AssertionError("unexpected writable field: " + field)
    finally:
        device.disconnect()
    assert device.connected is False
os.environ.pop("SDR_MOCK_LIBIIO_CONNECTION_ATTR_MODE")
temporary = native.probe_pluto_context("usb:mock")
device = native.PlutoDevice("usb:mock")
try:
    observed = device.probe()
    assert observed.uri == "usb:mock"
    assert observed.backend_uri != temporary.backend_uri
    assert observed.usb_vendor_id == "0456" and observed.usb_product_id == "b673"
    assert observed.usb_serial == "MOCK"
    assert device.receiver_topology().context.backend_uri == observed.backend_uri
finally:
    device.disconnect()
print("optional connection fields/read-only/SAME owner PASS; mock only")
"""
        environment = dict(os.environ, LIBIIO_DLL_PATH=str(MOCK))
        environment.pop("SDR_MOCK_LIBIIO_EMPTY_SERIAL", None)
        result = subprocess.run([sys.executable, "-c", code, MODULE], cwd=ROOT, env=environment,
                                text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
