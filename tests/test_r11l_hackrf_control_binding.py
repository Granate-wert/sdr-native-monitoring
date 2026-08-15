from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


NATIVE_MODULE_ENV = "SDR_R11L_NATIVE_MODULE"


@unittest.skipUnless(os.environ.get(NATIVE_MODULE_ENV), "fresh R11-L native module was not supplied")
class R11LHackrfControlBindingTests(unittest.TestCase):
    def test_fresh_extension_exposes_only_bounded_fake_control_flow(self) -> None:
        module_path = Path(os.environ[NATIVE_MODULE_ENV])
        self.assertTrue(module_path.is_file(), module_path)
        script = r'''
import importlib.util
import sys
import time

module_path = sys.argv[1]
spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
assert spec is not None and spec.loader is not None
native = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = native
spec.loader.exec_module(native)

assert hasattr(native, "HackrfRuntimeDspControl")
assert hasattr(native, "_make_test_hackrf_runtime_dsp_control")
forbidden = ("start", "configure", "retune", "set_sample_rate", "raw_iq", "ci8")
for name in forbidden:
    assert not hasattr(native.HackrfRuntimeDspControl, name), name

try:
    native.HackrfRuntimeDspControl()
except TypeError:
    pass
else:
    raise AssertionError("public HackRF control constructor must remain unavailable")

control = native._make_test_hackrf_runtime_dsp_control(4)
try:
    for timeout_ms in (0, 5001):
        try:
            control.stop(timeout_ms)
        except native.ConfigurationError:
            pass
        else:
            raise AssertionError("out-of-range stop timeout must fail closed")

    frames = []
    deadline = time.monotonic() + 1.0
    while len(frames) < 4 and time.monotonic() < deadline:
        frames.extend(control.poll_spectrum_frames(4))
        if len(frames) < 4:
            time.sleep(0.005)
    assert len(frames) == 4, len(frames)
    assert all(frame.source.source_id == "hackrf-r11l-test-fixture" for frame in frames)
    assert all(frame.source.backend_id == "native.libhackrf.rx.v1" for frame in frames)
    assert control.metrics().lifecycle_open
    result = control.stop(1000)
    assert result.complete()
    assert result.source_quiesce.complete()
    assert result.processing.complete()
    assert result.source_finalize.complete()
    assert not control.metrics().lifecycle_open
    assert control.stop(1000).complete()
finally:
    if control.metrics().lifecycle_open:
        result = control.stop(1000)
        assert result.complete()
'''
        completed = subprocess.run(
            [sys.executable, "-c", script, str(module_path)],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            completed.returncode,
            0,
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
