"""Explicit staged native/mock-only receiver gain binding contract.

Set SDR_APP07_TEST_NATIVE_MODULE to the matching extension. No physical RX,
canonical artifact replacement, or inference from a fake dual scan layout.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
MOCK = ROOT / "native/sdr_core/out/build/windows-msvc-cpu-hackrf/libiio.dll"


@unittest.skipUnless(MODULE and MOCK.is_file(), "requires explicit staged native and built mock DLL")
class PlutoReceiverGainBindingTests(unittest.TestCase):
    def test_selected_gain_readbacks_and_legacy_overload_are_truthful_readonly(self) -> None:
        code = r'''
import importlib.util
import pathlib
import sys

path = pathlib.Path(sys.argv[1]).resolve(strict=True)
spec = importlib.util.spec_from_file_location("_sdr_native", path)
native = importlib.util.module_from_spec(spec)
spec.loader.exec_module(native)
assert pathlib.Path(native.__file__).resolve() == path
rx = native.PlutoReceiverSelection

def config(gain):
    return native.DeviceConfig("binding-gain", "usb:mock", 2_450_000_000.,
        61_440_000., 56_000_000., native.GainMode.MANUAL, gain, 0, 1024,
        native.CONTRACT_SCHEMA_VERSION)

device = native.PlutoDevice("usb:mock")
try:
    first = device.configure(config(17), 4)  # integer retains old pool overload
    assert first.receiver_selection == rx.RX1
    assert len(first.receiver_gains) == 1
    assert first.receiver_gains[0].receiver == rx.RX1
    assert first.receiver_gains[0].manual_gain_db == first.manual_gain_db == 17
    second = device.configure(config(43), rx.RX2, 4)
    assert second.receiver_selection == rx.RX2
    assert len(second.receiver_gains) == 1
    assert second.receiver_gains[0].receiver == rx.RX2
    assert second.receiver_gains[0].manual_gain_db == second.manual_gain_db == 43
    assert second.receiver_gains[0].gain_mode == second.gain_mode == native.GainMode.MANUAL
    both = device.configure(config(23), rx.BOTH, 4)
    assert both.receiver_selection == rx.BOTH
    assert [v.receiver for v in both.receiver_gains] == [rx.RX1, rx.RX2]
    assert [v.manual_gain_db for v in both.receiver_gains] == [23, 23]
    for obj, name, value in [(both, "receiver_selection", rx.RX1),
            (both, "receiver_gains", []), (both.receiver_gains[1], "manual_gain_db", 99)]:
        try:
            setattr(obj, name, value)
        except AttributeError:
            pass
        else:
            raise AssertionError("readback field unexpectedly writable: " + name)
    returned = both.receiver_gains
    returned.clear()
    assert len(device.applied_config().receiver_gains) == 2
    assert device.streaming is False
finally:
    device.disconnect()
assert device.connected is False
print("selected RX readback, immutable fields, legacy overload PASS; mock only")
'''
        environment = dict(os.environ)
        environment["LIBIIO_DLL_PATH"] = str(MOCK)
        environment["SDR_MOCK_LIBIIO_TOPOLOGY_DUAL"] = "1"
        result = subprocess.run(
            [sys.executable, "-c", code, MODULE], cwd=ROOT, env=environment,
            text=True, capture_output=True, timeout=30, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
