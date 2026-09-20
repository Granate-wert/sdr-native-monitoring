"""GPU diagnostic guards are testable without a driver/context or device RX."""
from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("axis_gpu_probe",
    Path(__file__).resolve().parents[2] / "scripts/probe_app05_axis_gpu.py")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class GpuProbeGuardTests(unittest.TestCase):
    def capture(self):
        return dict(axis_replay_diagnostic=dict(qpicture_sha256=hashlib.sha256(b"trusted").hexdigest(),
            state=dict(clipping=False, view_enabled=False, dpr=1.,
                composition="CompositionMode_SourceOver", opacity=1., width=640, height=480)))

    def test_checksum_bounds_and_unsupported_state_fail_explicitly(self):
        report = self.capture()
        self.assertEqual(PROBE.validate_capture(report, b"trusted")["width"], 640)
        for data in (b"", b"wrong", b"x" * (1024 * 1024 + 1)):
            with self.subTest(data_length=len(data)), self.assertRaises(ValueError):
                PROBE.validate_capture(report, data)
        for key, value in (("width", 4097), ("height", 2161), ("dpr", 1.5),
                           ("clipping", True), ("view_enabled", True), ("opacity", .5),
                           ("composition", "CompositionMode_Source")):
            candidate = deepcopy(report)
            candidate["axis_replay_diagnostic"]["state"][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                PROBE.validate_capture(candidate, b"trusted")

    def test_two_equal_but_wrong_images_cannot_pass(self):
        self.assertFalse(PROBE.reference_gate(context_created=True, reference_matches=False,
            cpu_gpu_equal=True, gl_error=0))
        self.assertTrue(PROBE.reference_gate(context_created=True, reference_matches=True,
            cpu_gpu_equal=True, gl_error=0))
        for field, value in (("context_created", False), ("cpu_gpu_equal", False), ("gl_error", 1280)):
            candidate = dict(context_created=True, reference_matches=True, cpu_gpu_equal=True, gl_error=0)
            candidate[field] = value
            self.assertFalse(PROBE.reference_gate(**candidate))


if __name__ == "__main__":
    unittest.main()
