"""One native diagnostic thread, never a real Qt/SDR stall or recovery proof."""
from __future__ import annotations

import subprocess
import sys
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog


class QtStallWatchdogTests(unittest.TestCase):
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
