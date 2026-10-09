"""One native diagnostic thread, never a real Qt/SDR stall or recovery proof."""
from __future__ import annotations

import subprocess
import sys
import unittest
from io import StringIO
import json
from pathlib import Path
from unittest.mock import Mock, patch

from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog


class QtStallWatchdogTests(unittest.TestCase):
    def test_phase_rearms_same_owner_after_one_shot_and_records_bounded_marker(self):
        stream = StringIO()
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later") as dump, \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
            with QtStallWatchdog(stream, .1) as watchdog:
                watchdog.rearm_phase("explicit_Start_all")
                watchdog.rearm_phase("terminal_Stop_close")
                self.assertEqual(dump.call_count, 3)
                self.assertEqual(cancel.call_count, 2)
                markers = [json.loads(line) for line in stream.getvalue().splitlines()]
                self.assertEqual([marker["phase"] for marker in markers],
                                 ["explicit_Start_all", "terminal_Stop_close"])
                self.assertEqual([marker["sequence"] for marker in markers], [1, 2])
                self.assertTrue(all(marker["host_monotonic_ns"] > 0 for marker in markers))
                self.assertTrue(all(marker["deadline_enforced"] is False for marker in markers))
            self.assertEqual(cancel.call_count, 3)
            with self.assertRaises(RuntimeError):
                watchdog.rearm_phase("closed")

    def test_phase_rejects_invalid_and_foreign_without_cancel_or_file_write(self):
        stream = StringIO()
        owner, other = QtStallWatchdog(stream, .1), QtStallWatchdog(stream, .1)
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later"), \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
            with owner:
                for value in (None, True, "", "x" * 129, "line\nbreak", "nul\0", "tab\t"):
                    with self.subTest(value=value), self.assertRaises(ValueError):
                        owner.rearm_phase(value)
                with self.assertRaises(RuntimeError):
                    other.rearm_phase("foreign")
                cancel.assert_not_called()
                self.assertEqual(stream.getvalue(), "")

    def test_real_original_product_stop_refresh_dump_and_normal_cleanup(self):
        helper = Path(__file__).with_name("qt_watchdog_product_probe.py")
        child = subprocess.run([sys.executable, str(helper)], capture_output=True,
                               text=True, timeout=30, check=False)
        self.assertEqual(child.returncode, 0, child.stdout + child.stderr)
        self.assertIn("Timeout", child.stderr)
        self.assertGreaterEqual(child.stderr.count("Timeout"), 2)
        self.assertIn('"phase": "pre_refresh_observation"', child.stderr)
        self.assertIn('"phase": "stopped_original_refresh"', child.stderr)
        self.assertIn("independent_pane_session.py", child.stderr)
        self.assertIn("_refresh", child.stderr)
        self.assertIn("PRODUCT_START_STOP_NORMAL_CLEANUP", child.stdout)

    def test_failed_phase_io_or_rearm_releases_token_without_foreign_cancel(self):
        for failing_operation in ("write", "flush", "arm"):
            with self.subTest(failing_operation=failing_operation):
                stream = Mock()
                with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later") as dump, \
                        patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
                    first, second = QtStallWatchdog(stream, .1), QtStallWatchdog(Mock(), .1)
                    first.arm()
                    if failing_operation == "arm":
                        dump.side_effect = OSError("new phase timer failed")
                    else:
                        getattr(stream, failing_operation).side_effect = OSError("phase IO failed")
                    with self.assertRaises(OSError):
                        first.rearm_phase("failed_phase")
                    dump.side_effect = None
                    with second:
                        first.close()
                        self.assertEqual(cancel.call_count, 1)
                    self.assertEqual(cancel.call_count, 2)

    def test_phase_count_is_bounded_before_side_effects(self):
        stream = StringIO()
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later") as dump, \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
            with QtStallWatchdog(stream, .1) as watchdog:
                for _ in range(256):
                    watchdog.rearm_phase("bounded_phase")
                before = stream.getvalue()
                with self.assertRaises(RuntimeError):
                    watchdog.rearm_phase("overflow")
                self.assertEqual(stream.getvalue(), before)
                self.assertEqual(cancel.call_count, 256)
                self.assertEqual(dump.call_count, 257)
                self.assertLess(len(before.encode("utf-8")), 256 * 1024)

    def test_constructor_is_inert_and_deadline_is_bounded(self):
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later") as dump:
            QtStallWatchdog(Mock(), 1)
            dump.assert_not_called()
        for value in (True, 0, 0.01, -1, 3601, float("inf"), float("nan"), "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                QtStallWatchdog(Mock(), value)

    def test_exclusive_owner_and_nonfatal_one_shot_close(self):
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later") as dump, \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
            first, second = QtStallWatchdog(Mock(), 1), QtStallWatchdog(Mock(), 2)
            with first:
                dump.assert_called_once_with(1, repeat=False, file=first._file, exit=False)
                with self.assertRaises(RuntimeError):
                    second.arm()
                with self.assertRaises(RuntimeError):
                    first.arm()
                cancel.assert_not_called()
            first.close()
            cancel.assert_called_once()
            with self.assertRaises(RuntimeError):
                first.arm()
            with second:
                pass
            self.assertEqual(cancel.call_count, 2)

    def test_failed_arm_does_not_claim_or_cancel_another_watchdog(self):
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later",
                   side_effect=OSError("bad diagnostic FD")), \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later") as cancel:
            first = QtStallWatchdog(Mock(), 1)
            with self.assertRaises(OSError):
                first.arm()
            first.close()
            cancel.assert_not_called()
        with patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.dump_traceback_later"), \
                patch("sdr_monitor.services.qt_stall_watchdog.faulthandler.cancel_dump_traceback_later"):
            with QtStallWatchdog(Mock(), 1):
                pass

    def test_real_watchdog_dumps_while_main_waits_then_process_returns_normally(self):
        child = subprocess.run([sys.executable, "-c", "\n".join((
            "import sys, time",
            "from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog",
            "with QtStallWatchdog(sys.stderr, 0.08):",
            "    time.sleep(0.3)",
            "print('DIAGNOSTIC_CHILD_NORMAL_RETURN')",
        ))], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertIn("Timeout", child.stderr)
        self.assertIn("<string>", child.stderr)
        self.assertIn("DIAGNOSTIC_CHILD_NORMAL_RETURN", child.stdout)

    def test_real_explicit_close_cancels_only_before_deadline(self):
        child = subprocess.run([sys.executable, "-c", "\n".join((
            "import sys, time",
            "from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog",
            "with QtStallWatchdog(sys.stderr, 0.2):",
            "    pass",
            "time.sleep(0.3)",
            "print('CLOSED_DIAGNOSTIC_CHILD_NORMAL_RETURN')",
        ))], capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertNotIn("Timeout", child.stderr)
        self.assertIn("CLOSED_DIAGNOSTIC_CHILD_NORMAL_RETURN", child.stdout)


if __name__ == "__main__":
    unittest.main()
