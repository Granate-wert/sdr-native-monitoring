"""Backend lifecycle authority with fake engine allocation; no SDR/runtime I/O."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock, patch

from sdr_monitor.application.analyzer_session import AnalyzerSessionApplicationService
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveSessionState
from sdr_monitor.services.native_live import NativeLiveSessionService
from tests.test_native_live_discovery import _FakeNative


class NativeLiveAdmissionTests(unittest.TestCase):
    def test_competing_raw_start_is_not_owned_or_stopped_by_rejected_analyzer(self):
        service = NativeLiveSessionService(_FakeNative())
        reached, proceed = threading.Event(), threading.Event()
        foreign_engine = object()

        def fake_start():
            service._engine = foreign_engine
            service._snapshot = replace(service._snapshot, state=LiveSessionState.RUNNING)
            return service._snapshot

        def delayed_admission():
            # Controller has already observed is_running == False here.
            reached.set()
            if not proceed.wait(3):
                raise RuntimeError("test barrier expired")
            return service.start_admitted()

        owner = AnalyzerSessionApplicationService(service, Mock(), start_live=delayed_admission)
        with patch.object(service, "_start_unlocked", side_effect=fake_start) as start, \
             patch.object(service, "stop") as stop, ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(owner.start)
            try:
                self.assertTrue(reached.wait(1))
                service.start()
            finally:
                proceed.set()
            with self.assertRaises(LiveAdmissionRejected):
                future.result(timeout=2)
            self.assertFalse(owner.stop_required)
            owner.stop()
            stop.assert_not_called()
            start.assert_called_once()
            self.assertIs(service._engine, foreign_engine)
            self.assertTrue(service.is_running())

    def test_error_engine_and_sweep_lease_are_refused_before_any_recovery(self):
        for error_engine in (True, False):
            with self.subTest(error_engine=error_engine):
                service = NativeLiveSessionService(_FakeNative())
                service._snapshot = replace(service._snapshot, state=LiveSessionState.ERROR)
                service._engine = object() if error_engine else None
                service._sweep_lease_active = not error_engine
                before = service.latest_snapshot()
                with patch.object(service, "_start_unlocked") as start:
                    with self.assertRaises(LiveAdmissionRejected):
                        service.start_admitted()
                    start.assert_not_called()
                self.assertIs(service.latest_snapshot(), before)
