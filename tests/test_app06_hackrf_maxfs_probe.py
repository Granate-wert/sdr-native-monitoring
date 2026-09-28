"""No-device probe validation and exact counter-delta accounting."""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.probe_app06_hackrf_native_maxfs import counter_delta, identity_negative_gate, worker_stage_delta
from tests.test_r11n_hackrf_native_factory import _NativeFactory, _permit

ROOT = Path(__file__).resolve().parents[1]


class NativeMaxFsProbeTests(unittest.TestCase):
    def test_negative_identity_requires_exact_open_failure_and_leaves_permit_unchanged(self):
        class DeviceError(RuntimeError):
            pass

        native = _NativeFactory()
        native.DeviceError = DeviceError
        calls = []

        def reject(**values):
            calls.append(values)
            raise DeviceError("HackRF RX stage failed: open_exactly_one (status -30004)")

        native.create_hackrf_runtime_dsp_control = reject
        permit = _permit()
        original = permit._serial_words
        result = identity_negative_gate(native, permit)
        self.assertTrue(result["passed"])
        self.assertEqual(permit._serial_words, original)
        self.assertEqual(calls[0]["expected_serial_words"], (*original[:3], original[3] ^ 1))
        self.assertFalse(calls[0]["rf_amplifier_enabled"])
        self.assertFalse(calls[0]["bias_tee_enabled"])

        def wrong_failure(**values):
            raise DeviceError("HackRF RX stage failed: configure (status -30004)")

        native.create_hackrf_runtime_dsp_control = wrong_failure
        with self.assertRaisesRegex(RuntimeError, "identity-open gate"):
            identity_negative_gate(native, _permit())

    def test_unexpected_owner_is_stopped_and_is_not_a_passing_negative_gate(self):
        native = _NativeFactory()
        stops = []
        native.control = SimpleNamespace(stop=lambda timeout: stops.append(timeout))
        with self.assertRaisesRegex(RuntimeError, "unexpectedly returned"):
            identity_negative_gate(native, _permit())
        self.assertEqual(stops, [5000])

    def test_counter_delta_rejects_invalid_or_regressed_not_zero(self):
        self.assertEqual(counter_delta(SimpleNamespace(count=4), SimpleNamespace(count=10), "count"), 6)
        for first, last in ((10, 4), (False, 4), (4, True), (4, 4.0)):
            with self.subTest(first=first, last=last), self.assertRaises(ValueError):
                counter_delta(SimpleNamespace(count=first), SimpleNamespace(count=last), "count")

    def test_worker_stage_deltas_keep_absence_and_partition_separate(self):
        names = ("locked_push_ns", "dsp_push_poll_ns", "persistence_call_ns", "publication_queue_ns")
        nested = ("persistence_histogram_update_ns", "persistence_snapshot_build_ns",
                  "persistence_snapshot_count")
        absent = SimpleNamespace(stage_timing_available=False, **dict.fromkeys(names + nested, 0))
        self.assertEqual(worker_stage_delta(absent, absent), {"available": False})
        before = SimpleNamespace(stage_timing_available=True, locked_push_ns=10,
                                 dsp_push_poll_ns=5, persistence_call_ns=3,
                                 publication_queue_ns=1, persistence_histogram_update_ns=1,
                                 persistence_snapshot_build_ns=1, persistence_snapshot_count=1)
        after = SimpleNamespace(stage_timing_available=True, locked_push_ns=110,
                                dsp_push_poll_ns=55, persistence_call_ns=33,
                                publication_queue_ns=11, persistence_histogram_update_ns=19,
                                persistence_snapshot_build_ns=8, persistence_snapshot_count=4)
        self.assertEqual(worker_stage_delta(before, after), {
            "available": True, "locked_push_ns": 100, "dsp_push_poll_ns": 50,
            "persistence_call_ns": 30, "publication_queue_ns": 10,
            "unattributed_locked_ns": 10, "persistence_histogram_update_ns": 18,
            "persistence_snapshot_build_ns": 7, "persistence_snapshot_count": 3,
            "unattributed_persistence_ns": 5,
        })
        with self.assertRaisesRegex(ValueError, "availability changed"):
            worker_stage_delta(absent, after)
        with self.assertRaisesRegex(ValueError, "exceed locked push"):
            worker_stage_delta(before, SimpleNamespace(stage_timing_available=True,
                               locked_push_ns=20, dsp_push_poll_ns=55,
                               persistence_call_ns=33, publication_queue_ns=11,
                               persistence_histogram_update_ns=19,
                               persistence_snapshot_build_ns=8,
                               persistence_snapshot_count=4))
        with self.assertRaisesRegex(ValueError, "exceed outer call"):
            worker_stage_delta(before, SimpleNamespace(stage_timing_available=True,
                               locked_push_ns=110, dsp_push_poll_ns=55,
                               persistence_call_ns=33, publication_queue_ns=11,
                               persistence_histogram_update_ns=29,
                               persistence_snapshot_build_ns=18,
                               persistence_snapshot_count=4))

    def test_rx_switch_is_required_before_importing_native_or_sdk(self):
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "scripts/probe_app06_hackrf_native_maxfs.py"),
                "--module",
                "missing.pyd",
                "--manifest",
                "missing.json",
                "--output",
                "must-not-create.json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("--rx and seconds5..120 required", result.stderr)
        self.assertFalse((ROOT / "must-not-create.json").exists())
