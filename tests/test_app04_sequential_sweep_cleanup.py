"""Sequential Sweep cleanup retains engine/lease through every failed phase."""

from concurrent.futures import ThreadPoolExecutor
import threading
from types import SimpleNamespace
import unittest

from sdr_monitor.domain import SweepConfiguration, SweepExecutionMode
from sdr_monitor.services.native_sweep import NativeLiveSweepService, NativeSweepLease, NativeSweepService
from tests.test_r10b_native_sweep import _Engine, _Native, _source


class _FailingEngine(_Engine):
    def __init__(self, uri, timeout):
        super().__init__(uri, timeout)
        self.calls = []
        self.failures = {}

    def _phase(self, name):
        self.calls.append(name)
        if self.failures.get(name, 0):
            self.failures[name] -= 1
            raise RuntimeError(f"injected {name} failure")

    def request_stop(self):
        self._phase("request_stop")
        super().request_stop()

    def join(self):
        self._phase("join")
        super().join()

    def disconnect(self):
        self._phase("disconnect")
        super().disconnect()


class SequentialSweepCleanupTests(unittest.TestCase):
    def setUp(self):
        self.engine = _FailingEngine("usb:fake", 100)
        self.native = _Native()
        self.native.PlutoFixedBandEngine = lambda *_: self.engine
        self.releases = []
        self.service = NativeSweepService(
            self.native, _source(), assert_exclusive=lambda: None,
            release_lease=lambda: self.releases.append("release"),
        )
        self.config = SweepConfiguration(
            start_hz=1000, stop_hz=1050, dc_margin_hz=5,
            settling_s=0.001, dwell_s=0.001, execution_mode=SweepExecutionMode.NATIVE,
        )

    def test_each_failed_phase_retains_owner_and_explicit_close_retries_only_that_phase(self):
        phases = ("request_stop", "join", "disconnect")
        for failed in phases:
            with self.subTest(failed=failed):
                self.setUp()
                self.engine.failures[failed] = 2
                publications = []
                with self.assertRaisesRegex(RuntimeError, failed):
                    self.service.execute(self.config, publications.append)
                first = list(phases[:phases.index(failed) + 1])
                self.assertEqual(self.engine.calls, first)
                self.assertEqual(self.releases, [])
                with self.assertRaises(RuntimeError):
                    self.service.execute(self.config, publications.append)
                with self.assertRaisesRegex(RuntimeError, failed):
                    self.service.close()
                self.assertEqual(self.engine.calls, first + [failed])
                self.assertEqual(self.releases, [])
                self.service.close()
                expected = first + [failed] + list(phases[phases.index(failed):])
                self.assertEqual(self.engine.calls, expected)
                self.assertEqual(self.releases, ["release"])
                self.service.close()
                self.assertEqual(self.engine.calls, expected)
                self.assertEqual(self.releases, ["release"])

    def test_concurrent_close_requests_cancel_but_never_releases_live_worker_lease(self):
        entered, resume = threading.Event(), threading.Event()

        def progress(_):
            entered.set()
            if not resume.wait(3):
                raise RuntimeError("test completion timeout")

        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.service.execute, self.config, progress)
            try:
                self.assertTrue(entered.wait(1))
                with self.assertRaisesRegex(RuntimeError, "pending|executing"):
                    self.service.close()
                self.assertEqual(self.releases, [])
                self.assertEqual(self.engine.calls, [])
            finally:
                resume.set()
            pending.result(timeout=3)
        self.assertEqual(self.releases, ["release"])
        self.service.close()
        self.assertEqual(self.releases, ["release"])

    def test_live_wrapper_preserves_failed_owner_until_explicit_close(self):
        acquire_calls = []

        def acquire():
            acquire_calls.append("acquire")
            return NativeSweepLease(self.native, _source(), lambda: None,
                                    lambda: self.releases.append("release"))

        wrapper = NativeLiveSweepService(SimpleNamespace(acquire_native_sweep_lease=acquire))
        self.engine.failures["join"] = 2
        with self.assertRaisesRegex(RuntimeError, "join"):
            wrapper.execute(self.config, lambda _: None)
        self.assertEqual(self.engine.calls, ["request_stop", "join"])
        self.assertEqual(self.releases, [])
        with self.assertRaises(RuntimeError):
            wrapper.execute(self.config, lambda _: None)
        self.assertEqual(acquire_calls, ["acquire"])
        with self.assertRaisesRegex(RuntimeError, "join"):
            wrapper.close()
        self.assertEqual(self.releases, [])
        wrapper.close()
        self.assertEqual(self.releases, ["release"])
        self.assertEqual(self.engine.calls, ["request_stop", "join", "join", "join", "disconnect"])

    def test_release_failure_can_be_retried_after_native_shutdown_succeeds(self):
        def release():
            self.releases.append("release")
            if len(self.releases) == 1:
                raise RuntimeError("release failed")

        self.service = NativeSweepService(self.native, _source(), assert_exclusive=lambda: None,
                                          release_lease=release)
        with self.assertRaisesRegex(RuntimeError, "release failed"):
            self.service.execute(self.config, lambda _: None)
        with self.assertRaisesRegex(RuntimeError, "cleanup"):
            self.service.execute(self.config, lambda _: None)
        self.service.close()
        self.assertEqual(self.releases, ["release", "release"])
        self.assertEqual(self.engine.calls, ["request_stop", "join", "disconnect"])

    def test_native_idle_and_stopped_states_follow_their_actual_cleanup_contract(self):
        for state, expected in (("CREATED", ["disconnect"]), ("CONFIGURED", ["disconnect"]),
                                ("STOPPED", ["join", "disconnect"])):
            with self.subTest(state=state):
                self.setUp()
                self.engine.state = lambda: SimpleNamespace(name=state)
                self.engine.configure = lambda _: (_ for _ in ()).throw(RuntimeError("bad config"))
                self.service.execute(self.config, lambda _: None)
                self.assertEqual(self.engine.calls, expected)
                self.assertEqual(self.releases, ["release"])
