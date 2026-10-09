"""Finite host-window accounting tests; not physical performance acceptance."""
from dataclasses import replace
from threading import Event, get_ident
from time import perf_counter_ns
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.live import LivePerformance
from tests.steady_window_probe import (
    CounterReading, Observation, SteadyWindowProbe, WindowPlan, cached_live_reading,
    close_after_observer_join, summarize_window,
)


def row(count=10, *, anchor=("session-1", "epoch-1", "profile-exact"), unknown=None):
    return CounterReading("resource-1", anchor, (("fft", count), ("unobserved_samples", unknown)),
                          (("pending", count),))


def observation(before, after, count=10, **kwargs):
    return Observation(before, after, (row(count, **kwargs),))


class SteadyWindowTests(unittest.TestCase):
    def test_defaults_preserve_fixed_window_and_existing_bound(self):
        self.assertEqual(WindowPlan(), WindowPlan(15, 60, 1))
        for arguments in ((0, 60, .5), (True, 60, 1), (0, float("inf"), 1), (0, 60, 0)):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                WindowPlan(*arguments)

    def test_counter_delta_uses_read_envelopes_not_nominal_fs_or_snapshot_rate(self):
        report = summarize_window((observation(0, 100, 100), observation(1_000_000_000, 1_000_000_100, 150)),
                                  start_ns=100, end_ns=1_000_000_000)
        fft = report["resources"][0]["counters"]["fft"]
        self.assertEqual(fft["delta"], 50)
        self.assertLess(fft["cached_read_rate_bounds_per_s"][0], 50)
        self.assertGreater(fft["cached_read_rate_bounds_per_s"][1], 50)
        self.assertIsNone(report["resources"][0]["counters"]["unobserved_samples"]["delta"])
        self.assertFalse(report["lifetime_complete"])
        self.assertIsNone(report["paint_audit"])

    def test_missing_middle_counter_cannot_become_known_zero(self):
        report = summarize_window((observation(0, 10, 10, unknown=2),
                                   observation(50, 60, 12), observation(100, 110, 15, unknown=3)),
                                  start_ns=10, end_ns=100)
        counter = report["resources"][0]["counters"]["unobserved_samples"]
        self.assertIsNone(counter["delta"])
        self.assertEqual(counter["observed_samples"], 2)

    def test_counter_reset_epoch_profile_and_roster_changes_refuse(self):
        bad = (observation(100, 110, 9), observation(100, 110, 20, anchor=("new-epoch",)),
               Observation(100, 110, (replace(row(20), resource_id="different"),)),
               Observation(100, 110, (replace(row(20), counters=(("other", 20),)),)))
        for last in bad:
            with self.subTest(last=last), self.assertRaises(ValueError):
                summarize_window((observation(0, 10), last), start_ns=10, end_ns=100)

    def test_middle_reset_cannot_hide_behind_larger_endpoint(self):
        with self.assertRaisesRegex(ValueError, "regressed"):
            summarize_window((observation(0, 10, 10), observation(50, 60, 3), observation(100, 110, 20)),
                             start_ns=10, end_ns=100)

    def test_schema_duplicate_counter_and_duplicate_resource_refuse(self):
        for counter in (-1, True, float("nan"), 1 << 63):
            with self.subTest(counter=counter), self.assertRaises(ValueError):
                row(counter)
        with self.assertRaises(ValueError):
            CounterReading("r", ("a",), (("same", 1), ("same", 1)))
        with self.assertRaises(ValueError):
            Observation(0, 10, (row(), row()))

    def test_shared_stream_not_summed_and_gauges_are_sample_extrema(self):
        # One physical resource even if two panes display it; no per-pane duplicate.
        report = summarize_window((observation(0, 10, 10), observation(50, 60, 30), observation(100, 110, 50)),
                                  start_ns=10, end_ns=100)
        self.assertEqual(len(report["resources"]), 1)
        self.assertEqual(report["resources"][0]["counters"]["fft"]["delta"], 40)
        gauge = report["resources"][0]["gauges"]["pending"]
        self.assertEqual((gauge["sampled_min"], gauge["sampled_max"], gauge["observed_samples"]), (10, 50, 3))

    def test_window_overlap_and_missing_brackets_refuse(self):
        for data, start, end in (((observation(0, 20), observation(10, 100, 20)), 20, 80),
                                 ((observation(0, 20), observation(100, 110, 20)), 10, 100),
                                 ((observation(0, 20), observation(100, 110, 20)), 20, 105)):
            with self.subTest(data=data), self.assertRaises(ValueError):
                summarize_window(data, start_ns=start, end_ns=end)

    def test_worker_callbacks_are_not_caller_thread_and_have_finite_samples(self):
        identities, counts = [], []
        def capture():
            before = perf_counter_ns()
            identities.append(get_ident())
            counts.append(len(counts))
            return Observation(before, perf_counter_ns(), (row(counts[-1]),))
        probe = SteadyWindowProbe(capture, plan=WindowPlan(.02, .06, .02))
        probe.start()
        try:
            self.assertTrue(probe.done.wait(3))
            report = probe.result()
            self.assertGreaterEqual(report["observation_count"], 2)
            self.assertLessEqual(report["observation_count"], 5)
            self.assertTrue(all(identity != get_ident() for identity in identities))
            self.assertEqual(report["resources"][0]["counters"]["fft"]["delta"], len(counts)-1)
            with self.assertRaises(RuntimeError):
                probe.start()
        finally:
            probe.stop_and_join()
        self.assertFalse(probe.thread.is_alive())

    def test_cancellation_does_not_return_partial_success(self):
        probe = SteadyWindowProbe(lambda: self.fail("cancelled warmup read"), plan=WindowPlan(5, 1, 1))
        probe.start()
        probe.stop_and_join()
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            probe.result()

    def test_callback_failure_is_retained_and_thread_joins(self):
        error = ValueError("original cached read failure")
        def capture():
            raise error
        probe = SteadyWindowProbe(capture, plan=WindowPlan(0, .05, .02))
        probe.start()
        self.assertTrue(probe.done.wait(3))
        probe.stop_and_join()
        with self.assertRaises(ValueError) as raised:
            probe.result()
        self.assertIs(raised.exception, error)

    def test_blocked_callback_refuses_join_success_and_is_released(self):
        entered, release = Event(), Event()
        def capture():
            entered.set()
            release.wait(3)
            before = perf_counter_ns()
            return Observation(before, before, (row(),))
        probe = SteadyWindowProbe(capture, plan=WindowPlan(0, .05, .02))
        probe.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(TimeoutError):
                probe.stop_and_join(.01)
        finally:
            release.set()
            probe.stop_and_join()
        with self.assertRaises(RuntimeError):
            probe.result()

    def test_family_authority_does_not_treat_unused_defaults_or_rolling_rates_as_counters(self):
        snapshot = SimpleNamespace(state=SimpleNamespace(value="running"), error=None, spectrum=object(),
            performance=LivePerformance(fft_frames_computed=250, snapshots_emitted=100,
                iq_blocks_received=7, analytical_fft_rate_hz=999999, rate_observation_interval_s=.25))
        for family in ("ad936x", "hackrf", "rtl_sdr"):
            reading = cached_live_reading("r", family, snapshot, anchor=("exact-profile",))
            counters = dict(reading.counters)
            self.assertEqual(counters["fft_frames_computed"], 250)
            self.assertIsNone(counters["iq_samples_received"])
            self.assertNotIn("analytical_fft_rate_hz", counters)
            if family == "rtl_sdr":
                self.assertIsNone(counters["snapshots_emitted"])
                self.assertIsNone(counters["persistence_updates"])
            if family == "hackrf":
                self.assertIsNone(counters["iq_blocks_dropped"])
            snapshot.performance = replace(snapshot.performance, rate_observation_interval_s=None)
            self.assertTrue(all(value is None for _, value in cached_live_reading("r", family,
                snapshot, anchor=("exact-profile",)).counters))
            snapshot.performance = replace(snapshot.performance, rate_observation_interval_s=.25)

    def test_reader_and_owner_close_are_not_called_after_failed_join(self):
        entered, release = Event(), Event()
        closed = []
        def capture():
            entered.set()
            release.wait(3)
            before = perf_counter_ns()
            return Observation(before, before, (row(),))
        def close():
            self.assertFalse(probe.thread.is_alive())
            closed.extend(("reader_close", "owner_close"))
        probe = SteadyWindowProbe(capture, plan=WindowPlan(0, .05, .02))
        probe.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaises(TimeoutError):
                close_after_observer_join(probe, close, timeout_s=.01)
            self.assertEqual(closed, [])
            self.assertTrue(probe.thread.is_alive())
        finally:
            release.set()
            close_after_observer_join(probe, close)
        self.assertEqual(closed, ["reader_close", "owner_close"])

    def test_cached_reader_rejects_nonrunning_error_and_missing_frame(self):
        snapshot = SimpleNamespace(state=SimpleNamespace(value="stopped"), error=None, spectrum=object(),
                                   performance=LivePerformance())
        with self.assertRaises(ValueError):
            cached_live_reading("r", "ad936x", snapshot, anchor=("a",))
        snapshot.state.value = "running"
        snapshot.error = "failure"
        with self.assertRaises(ValueError):
            cached_live_reading("r", "ad936x", snapshot, anchor=("a",))
        snapshot.error = None
        snapshot.spectrum = None
        with self.assertRaises(ValueError):
            cached_live_reading("r", "ad936x", snapshot, anchor=("a",))


if __name__ == "__main__":
    unittest.main()
