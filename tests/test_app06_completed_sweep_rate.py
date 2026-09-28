"""The common Sweep pass-period source must survive zero-delta UI polls."""

import unittest
from types import SimpleNamespace

from sdr_monitor.services.completed_line_rate import CompletedLineRateObservation
from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService


class CompletedLineRateTests(unittest.TestCase):
    def test_recent_completed_rate_survives_empty_polls_then_expires(self) -> None:
        rate = CompletedLineRateObservation()
        self.assertEqual(rate.observe(0, 0.0), 0.0)
        self.assertEqual(rate.observe(4, 0.2), 20.0)
        self.assertEqual(rate.observe(4, 0.3), 20.0)
        self.assertEqual(rate.observe(4, 1.19), 20.0)
        self.assertEqual(rate.observe(4, 1.2), 0.0)
        self.assertAlmostEqual(rate.observe(5, 1.4), 1.0 / 1.2)

    def test_slow_pass_keeps_its_own_three_period_expiry(self) -> None:
        rate = CompletedLineRateObservation()
        self.assertEqual(rate.observe(0, 0.0), 0.0)
        self.assertEqual(rate.observe(1, 5.0), 0.2)
        self.assertEqual(rate.observe(1, 19.9), 0.2)
        self.assertEqual(rate.observe(1, 20.0), 0.0)

    def test_first_observation_does_not_invent_pre_poll_history(self) -> None:
        rate = CompletedLineRateObservation()
        self.assertEqual(rate.observe(3, 10.0), 0.0)
        self.assertEqual(rate.observe(3, 10.2), 0.0)
        self.assertEqual(rate.observe(4, 11.0), 1.0)
        rate.reset()
        self.assertEqual(rate.observe(1, 20.0), 0.0)
        self.assertEqual(rate.observe(2, 20.5), 2.0)

    def test_same_owner_counter_or_clock_regression_fails_closed(self) -> None:
        rate = CompletedLineRateObservation()
        rate.observe(4, 10.0)
        with self.assertRaisesRegex(ValueError, "count regressed"):
            rate.observe(3, 11.0)
        with self.assertRaisesRegex(ValueError, "time regressed"):
            rate.observe(4, 10.5)
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rate.observe(value, 12.0)
        with self.assertRaises(ValueError):
            rate.observe(4, float("nan"))

    def test_pluto_bridge_keeps_rate_on_progress_only_poll(self) -> None:
        counter = SimpleNamespace(completed_lines=0, gapped_lines=0, output_queue=None)
        service = object.__new__(NativeContinuousSweepDisplayService)
        service._coordinator = SimpleNamespace(metrics=lambda: counter)
        service._completed_rate = CompletedLineRateObservation()
        service._ui_superseded = 0
        self.assertEqual(service._metrics(0.0).completed_line_lps, 0.0)
        counter.completed_lines = 2
        self.assertEqual(service._metrics(0.2).completed_line_lps, 10.0)
        self.assertEqual(service._metrics(0.3).completed_line_lps, 10.0)
        self.assertEqual(service._metrics(1.3).completed_line_lps, 0.0)


if __name__ == "__main__":
    unittest.main()
