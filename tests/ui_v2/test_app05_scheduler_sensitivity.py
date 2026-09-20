"""Diagnostic scheduling factors cannot silently alter normal runner defaults."""
import unittest
from unittest.mock import patch

from scripts import benchmark_app05_persistence_delivery as runner


class SchedulerSensitivityTests(unittest.TestCase):
    def test_default_does_not_set_runtime_interval(self):
        with patch.object(runner.sys, "getswitchinterval", return_value=.005), \
             patch.object(runner.sys, "setswitchinterval") as setting:
            with runner.diagnostic_switch_interval(None) as record:
                self.assertFalse(record["diagnostic_only"])
                self.assertEqual(record["active_ms"], 5)
            setting.assert_not_called()
            self.assertEqual(record["restored_ms"], 5)

    def test_explicit_factor_is_recorded_and_restored_after_failure(self):
        state = [.005]
        with patch.object(runner.sys, "getswitchinterval", side_effect=lambda: state[0]), \
             patch.object(runner.sys, "setswitchinterval", side_effect=lambda v: state.__setitem__(0, v)) as setting:
            with self.assertRaisesRegex(RuntimeError, "expected"):
                with runner.diagnostic_switch_interval(1.0) as record:
                    self.assertTrue(record["diagnostic_only"])
                    self.assertEqual(record["active_ms"], 1)
                    raise RuntimeError("expected")
            self.assertEqual(state, [.005])
            self.assertEqual(record["restored_ms"], 5)
            self.assertEqual([c.args[0] for c in setting.call_args_list], [.001, .005])

    def test_unplanned_factor_is_rejected_without_runtime_mutation(self):
        with patch.object(runner.sys, "setswitchinterval") as setting:
            for value in (0, -1, .01, 2, float("nan"), float("inf")):
                with self.assertRaises(ValueError):
                    with runner.diagnostic_switch_interval(value):
                        self.fail("invalid factor entered")
            setting.assert_not_called()


if __name__ == "__main__":
    unittest.main()
