"""Terminal HackRF bridge accounting; fake owner only, no Qt or native RX."""

from __future__ import annotations

import threading
import unittest
from dataclasses import replace

from tests.ui_v2.test_app06_hackrf_common_analyzer import graph, stage


class HackrfTerminalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.g = graph()
        self.g.application.discover()
        self.g.application.select_device("source-hackrf")
        stage(self.g)

    def tearDown(self):
        self.g.observation.close_fail = False
        for control in self.g.factory.controls:
            control.stop_fail = False
        if self.g.coordinator._quarantined_control is None:
            self.g.application.shutdown()

    def _block_drain(self, call_number: int, *, coalesced: int = 0):
        original_create = self.g.factory.create
        reached = threading.Event()
        release = threading.Event()

        def create(permit):
            control = original_create(permit)
            control.drain_coalesced = coalesced
            original_drain = control.drain_latest_spectrum_frame
            calls = 0

            def drain():
                nonlocal calls
                result = original_drain()
                calls += 1
                if calls == call_number:
                    reached.set()
                    if not release.wait(2):
                        raise RuntimeError("test drain release timeout")
                return result

            control.drain_latest_spectrum_frame = drain
            return control

        self.g.factory.create = create
        return reached, release

    def _publication_event(self):
        journal = self.g.hackrf._journal
        original = journal.observe_adapter_result
        published = threading.Event()

        def observe(frame, coalesced, disposition, *, expected_scope):
            result = original(frame, coalesced, disposition, expected_scope=expected_scope)
            if getattr(disposition, "name", None) == "PUBLISHED":
                published.set()
            return result

        journal.observe_adapter_result = observe
        return published

    def _stop_while_drain_blocked(self, release: threading.Event):
        service = self.g.hackrf
        stop_entered = threading.Event()
        original_set = service._cancel.set

        def signal_stop():
            stop_entered.set()
            original_set()

        service._cancel.set = signal_stop
        result = []
        finished = threading.Event()

        def stop():
            try:
                result.append(self.g.application.stop())
            finally:
                finished.set()

        worker = threading.Thread(target=stop)
        worker.start()
        self.assertTrue(stop_entered.wait(2), "Stop did not reach cancellation")
        release.set()
        self.assertTrue(finished.wait(2), "Stop did not finish after worker release")
        worker.join()
        return result[0]

    def test_stop_caches_joined_tail_counts_without_claiming_cancelled_frame(self):
        reached, release = self._block_drain(3, coalesced=2)
        self.g.application.start()
        # Block after the third drain has returned. The first two frames have
        # completed publication; the third is still in-flight at Stop.
        self.assertTrue(reached.wait(2), "publication worker did not reach controlled tail")
        before = self.g.hackrf.current_snapshot()
        self.assertGreaterEqual(before.sequence, 2)
        stale = replace(before.performance, bridge_native_frames_polled=901,
            bridge_frames_coalesced=902, bridge_frames_published=903,
            source_sequence_discontinuities=17, rate_observation_interval_s=0.123)
        with self.g.hackrf._lock:
            self.g.hackrf._snapshot = replace(before, performance=stale)

        stopped = self._stop_while_drain_blocked(release)
        self.assertIsNone(stopped.error)
        self.assertFalse(stopped.stop_required)
        performance = stopped.performance
        # Three native drains each represent three native frames. The last was
        # cancelled at the publication guard, so only two are published.
        self.assertEqual((performance.bridge_native_frames_polled,
                          performance.bridge_frames_coalesced,
                          performance.bridge_frames_published), (9, 6, 2))
        self.assertEqual(performance.source_sequence_discontinuities, 17)
        self.assertEqual(performance.rate_observation_interval_s, 0.123)
        self.assertEqual(self.g.live._external_analyzer_owner, None)

    def test_join_or_native_stop_refusal_retains_owner_without_final_counts(self):
        reached, release = self._block_drain(3)
        self.g.application.start()
        control = self.g.factory.controls[0]
        self.assertTrue(reached.wait(2), "publication worker did not reach controlled tail")
        before = self.g.hackrf.current_snapshot()
        sentinel = replace(before.performance, bridge_native_frames_polled=71,
            bridge_frames_coalesced=72, bridge_frames_published=73)
        with self.g.hackrf._lock:
            self.g.hackrf._snapshot = replace(before, performance=sentinel)
        control.stop_fail = True

        failed = self._stop_while_drain_blocked(release)
        self.assertTrue(failed.stop_required)
        self.assertEqual((failed.performance.bridge_native_frames_polled,
                          failed.performance.bridge_frames_coalesced,
                          failed.performance.bridge_frames_published), (71, 72, 73))
        self.assertIsNotNone(self.g.live._external_analyzer_owner)
        self.assertTrue(self.g.coordinator.snapshot().active)

        control.stop_fail = False
        stopped = self.g.application.stop()
        self.assertIsNone(stopped.error)

    def test_join_refusal_does_not_finalize_counters_or_release_owner(self):
        reached, release = self._block_drain(1)
        self.g.application.start()
        self.assertTrue(reached.wait(2), "publication worker did not reach controlled drain")
        service = self.g.hackrf
        actual_thread = service._poller
        self.assertIsNotNone(actual_thread)
        sentinel = replace(service.current_snapshot().performance,
            bridge_native_frames_polled=31, bridge_frames_coalesced=32, bridge_frames_published=33)
        with service._lock:
            service._snapshot = replace(service._snapshot, performance=sentinel)

        class RefusingJoin:
            ident = actual_thread.ident

            @staticmethod
            def join(timeout=None):
                return None

            @staticmethod
            def is_alive():
                return True

        service._poller = RefusingJoin()
        failed = self.g.application.stop()
        self.assertTrue(failed.stop_required)
        self.assertEqual((failed.performance.bridge_native_frames_polled,
                          failed.performance.bridge_frames_coalesced,
                          failed.performance.bridge_frames_published), (31, 32, 33))
        self.assertIsNotNone(self.g.live._external_analyzer_owner)

        service._poller = actual_thread
        release.set()
        actual_thread.join(timeout=2)
        self.assertFalse(actual_thread.is_alive())
        stopped = self.g.application.stop()
        self.assertIsNone(stopped.error)

    def test_stage_failed_activation_and_repeated_stop_do_not_reinject_old_epoch(self):
        published = self._publication_event()
        self.g.application.start()
        self.assertTrue(published.wait(2), "first frame was not published")
        self.assertIsNone(self.g.application.stop().error)
        terminal = self.g.application.current_snapshot().performance
        self.assertGreater(terminal.bridge_frames_published, 0)

        repeated = self.g.application.stop()
        self.assertEqual(repeated.performance.bridge_frames_published,
                         terminal.bridge_frames_published)

        stage(self.g)
        staged = self.g.application.current_snapshot().performance
        self.assertEqual((staged.bridge_native_frames_polled,
                          staged.bridge_frames_coalesced,
                          staged.bridge_frames_published), (0, 0, 0))
        self.g.observation.serial = (0, 0, 1, 2)
        failed = self.g.application.start()
        self.assertTrue(failed.stop_required)
        stopped = self.g.application.stop()
        self.assertIsNone(stopped.error)
        self.assertEqual((stopped.performance.bridge_native_frames_polled,
                          stopped.performance.bridge_frames_coalesced,
                          stopped.performance.bridge_frames_published), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
