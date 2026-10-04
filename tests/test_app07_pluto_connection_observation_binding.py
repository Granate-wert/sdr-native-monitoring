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
