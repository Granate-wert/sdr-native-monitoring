"""Start refresh must not wait on close authority while owners are unsettled."""

from __future__ import annotations

from concurrent.futures import Future
from threading import Event, Thread
import os
from time import monotonic, sleep
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase, PanePumpResourceState
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2

from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph


class StartRefreshResponsiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def _wait(self, predicate, timeout: float = 8.0) -> None:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            sleep(0.01)
        self.fail("pane UI did not reach the expected state")

    def _surface(self):
        native, graph = _ad_graph(serial="")
        source_id = graph.live.discover(startup=True)[0].device_id
        pool = PaneProductGraphPool(lambda _resource: graph)
        prepared = prepare_user_pane_session((
            PaneSlotDraft(1, source_id, 100e6, 108e6),
            PaneSlotDraft(2), PaneSlotDraft(3), PaneSlotDraft(4),
        ), pool_factory=lambda: pool)
        apply_user_pane_session(prepared)
        closed = []
        surface = IndependentPaneSessionV2(prepared.handle,
            close_layout=lambda handle: closed.append(handle))
        return native, graph, pool, prepared.handle, surface, closed

    def _dispose(self, graph, pool, handle, surface) -> None:
        if handle is not None and not handle.shutdown_complete:
            for future in handle.pump.stop_all().values():
                future.result(timeout=5)
            handle.shutdown_after_stop()
        if surface is not None:
            if handle is not None and handle.shutdown_complete:
                surface.release_presentation_after_shutdown()
                surface.close()
            else:
                surface._state_timer.stop()
                surface.delivery.stop()
                surface.deleteLater()
        if pool.staged_resource_ids:
            pool.close()
        graph.live.shutdown()
        self.app.processEvents()

    def test_selected_and_all_start_return_while_session_state_lock_is_held(self) -> None:
        for command in ("selected", "all"):
            with self.subTest(command=command):
                native, graph, pool, handle, surface, _closed = self._surface()
                lock_acquired = Event()
                release_lock = Event()
                lock = handle.session._state_lock

                def hold_lock_with_watchdog() -> None:
                    with lock:
                        lock_acquired.set()
                        # A watchdog bounds a regression: baseline _refresh can
                        # block in can_close(), but never hangs this test.
                        release_lock.wait(0.35)

                holder = Thread(target=hold_lock_with_watchdog, daemon=True)
                holder.start()
                try:
                    self.assertTrue(lock_acquired.wait(1.0))
                    calls = []
                    original_can_close = handle.can_close

                    def observed_can_close() -> bool:
                        calls.append(monotonic())
                        return original_can_close()

                    with patch.object(handle, "can_close", side_effect=observed_can_close):
                        started = monotonic()
                        if command == "selected":
                            surface._start_selected()
                        else:
                            surface._start_all()
                        elapsed = monotonic() - started
                        self.assertLess(elapsed, 0.25,
                            "Start refresh waited on the session lock")
                        self.assertEqual(calls, [],
                            "close authority is unnecessary while Start is unsettled")
                        self.assertTrue(surface._futures)
                finally:
                    release_lock.set()
                    holder.join(timeout=1.0)
                    self.assertFalse(holder.is_alive(), "lock-holder watchdog failed to release")

                try:
                    self._wait(lambda: all(future.done() for future in surface._futures))
                    for future in handle.pump.stop_all().values():
                        future.result(timeout=5)
                    self._wait(handle.can_close)
                finally:
                    self._dispose(graph, pool, handle, surface)

    def test_pending_future_or_unstopped_snapshot_skips_close_authority(self) -> None:
        native, graph, pool, handle, surface, _closed = self._surface()
        resource_id = handle.pump.snapshot()[0].physical_stream_resource_id
        try:
            pending = Future()
            surface._futures = [pending]
            calls = []
            with patch.object(handle.pump, "snapshot", return_value=(
                    PanePumpResourceState(resource_id, phase=PanePumpPhase.STOPPED),)), \
                 patch.object(handle, "can_close", side_effect=lambda: calls.append(True) or True):
                surface._refresh()
            self.assertEqual(calls, [])
            self.assertFalse(surface.close_layout.isEnabled())
            pending.set_result(None)

            for phase in (PanePumpPhase.STARTING, PanePumpPhase.RUNNING,
                          PanePumpPhase.STOPPING, PanePumpPhase.STOP_REQUIRED):
                calls.clear()
                with self.subTest(phase=phase), \
                     patch.object(handle.pump, "snapshot", return_value=(
                         PanePumpResourceState(resource_id, phase=phase),)), \
                     patch.object(handle, "can_close", side_effect=lambda: calls.append(True) or True):
                    surface._refresh()
                    self.assertEqual(calls, [])
                    self.assertFalse(surface.close_layout.isEnabled())
        finally:
            self._dispose(graph, pool, handle, surface)

    def test_all_stopped_preserves_can_close_as_final_authority(self) -> None:
        native, graph, pool, handle, surface, closed = self._surface()
        resource_id = handle.pump.snapshot()[0].physical_stream_resource_id
        try:
            snapshot = (PanePumpResourceState(resource_id, phase=PanePumpPhase.STOPPED),)
            for allowed in (False, True):
                calls = []

                def can_close() -> bool:
                    calls.append(True)
                    return allowed

                with self.subTest(allowed=allowed), \
                     patch.object(handle.pump, "snapshot", return_value=snapshot), \
                     patch.object(handle, "can_close", side_effect=can_close):
                    surface._refresh()
                    self.assertEqual(calls, [True])
                    self.assertEqual(surface.close_layout.isEnabled(), allowed)
            self.assertEqual(closed, [])
        finally:
            self._dispose(graph, pool, handle, surface)


if __name__ == "__main__":
    unittest.main()
