"""Start refresh must not wait on close authority while owners are unsettled."""

from __future__ import annotations

from concurrent.futures import Future
from contextlib import contextmanager
from threading import Event, Thread
import os
from shiboken6 import isValid as is_qobject_valid
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
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
        return SimpleNamespace(native=native, graph=graph, pool=pool,
                               handle=prepared.handle, surface=surface, closed=closed)

    @contextmanager
    def _owned_surface(self):
        product = self._surface()
        body_error = None
        body_traceback = None
        try:
            yield product
        except BaseException as error:
            # Teardown must never replace the first assertion or fixture error.
            body_error = error
            body_traceback = error.__traceback__
        finally:
            try:
                cleanup_error = self._dispose(product.graph, product.pool, product.handle, product.surface)
            except BaseException as error:
                # Even an unexpected test-fixture cleanup error must not mask
                # the assertion that first failed inside the owned surface.
                cleanup_error = error
            if body_error is not None:
                if cleanup_error is not None:
                    body_error.add_note(f"owned-surface cleanup also failed: {cleanup_error}")
                raise body_error.with_traceback(body_traceback)
            if cleanup_error is not None:
                raise cleanup_error

    def _dispose(self, graph, pool, handle, surface):
        errors = []

        def attempt(label, operation) -> None:
            try:
                operation()
            except BaseException as error:
                errors.append((label, error))

        if handle is not None and not handle.shutdown_complete:
            futures = {}
            attempt("request Stop", lambda: futures.update(handle.pump.stop_all()))
            for resource_id, future in futures.items():
                attempt(f"join Stop for {resource_id}", lambda future=future: future.result(timeout=5))
            # A failed Stop still must not prevent presentation and graph cleanup
            # attempts; the first body error remains authoritative in the fixture.
            attempt("shutdown product handle", handle.shutdown_after_stop)

        if surface is not None:
            if handle is not None and handle.shutdown_complete:
                attempt("release presentation", surface.release_presentation_after_shutdown)
                attempt("close presentation", surface.close)
            else:
                # The terminal owner boundary was not confirmed. Stop only the
                # Qt-side producers before retiring the QObject below.
                attempt("stop presentation timer", surface._state_timer.stop)
                attempt("stop presentation delivery", surface.delivery.stop)
            attempt("schedule presentation deletion", surface.deleteLater)
            attempt("flush deferred presentation deletion",
                    lambda: QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete))

        def close_pool_if_needed() -> None:
            if pool is not None and (pool.staged_resource_ids or pool.cleanup_pending_resource_ids):
                pool.close()

        attempt("close graph pool", close_pool_if_needed)
        if graph is not None:
            # Keep a direct graph shutdown attempt even if pool.close reported an
            # error, so a failed normal cleanup cannot strand the fake owner.
            attempt("shutdown graph live owner", graph.live.shutdown)
        attempt("process Qt events", self.app.processEvents)

        checks = (
            ("staged resources", lambda: self.assertEqual(pool.staged_resource_ids, ())),
            ("pending graph cleanup", lambda: self.assertEqual(pool.cleanup_pending_resource_ids, ())),
            ("presentation QObject deletion", lambda: self.assertFalse(is_qobject_valid(surface))),
        )
        if handle is not None:
            checks += (
                ("retained claims", lambda: self.assertEqual(handle.session.retained_resource_count, 0)),
                ("worker retirement", lambda: self.assertTrue(all(
                    not worker._thread.is_alive() for worker in handle.pump._workers.values()))),
                ("control work retirement", lambda: self.assertFalse(handle.pump.control_pending())),
                ("delivery queue retirement", lambda: self.assertEqual(handle.queue.pending_count, 0)),
                ("operation future retirement", lambda: self.assertTrue(all(
                    future.done() for future in surface._futures))),
            )
        for label, check in checks:
            attempt(f"verify {label}", check)

        if not errors:
            return None
        details = "; ".join(f"{label}: {type(error).__name__}: {error}" for label, error in errors)
        return AssertionError(f"owned-surface cleanup failed ({details})")

    def test_selected_and_all_start_return_while_session_state_lock_is_held(self) -> None:
        for command in ("selected", "all"):
            with self.subTest(command=command):
                with self._owned_surface() as product:
                    handle, surface = product.handle, product.surface
                    lock_acquired = Event()
                    release_lock = Event()

                    def hold_lock_with_watchdog() -> None:
                        with handle.session._state_lock:
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
                                surface.start_selected.click()
                            else:
                                surface.start_all.click()
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

                    self._wait(lambda: all(future.done() for future in surface._futures))
                    for future in handle.pump.stop_all().values():
                        future.result(timeout=5)
                    self._wait(handle.can_close)
                    surface._refresh()
                    self.assertTrue(surface.close_layout.isEnabled())
                    surface.close_layout.click()
                    self.assertEqual(product.closed, [handle])

    def test_pending_future_or_unstopped_snapshot_skips_close_authority(self) -> None:
        with self._owned_surface() as product:
            handle, surface = product.handle, product.surface
            resource_id = handle.pump.snapshot()[0].physical_stream_resource_id
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

    def test_all_stopped_preserves_can_close_as_final_authority(self) -> None:
        with self._owned_surface() as product:
            handle, surface = product.handle, product.surface
            resource_id = handle.pump.snapshot()[0].physical_stream_resource_id
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
            self.assertEqual(product.closed, [])

    def test_fixture_retires_graph_even_when_assertion_fails(self) -> None:
        with self.assertRaisesRegex(AssertionError, "injected fixture failure"):
            with self._owned_surface() as product:
                self.fail("injected fixture failure")
        self.assertEqual(product.pool.staged_resource_ids, ())
        self.assertEqual(product.pool.cleanup_pending_resource_ids, ())
        self.assertEqual(product.handle.session.retained_resource_count, 0)
        self.assertTrue(all(not worker._thread.is_alive()
                            for worker in product.handle.pump._workers.values()))
        self.assertFalse(product.handle.pump.control_pending())
        self.assertEqual(product.handle.queue.pending_count, 0)
        self.assertTrue(all(future.done() for future in product.surface._futures))
        self.assertFalse(is_qobject_valid(product.surface))

    def test_cleanup_failure_does_not_mask_body_assertion(self) -> None:
        body_error = AssertionError("injected body assertion")
        close_error = RuntimeError("injected close cleanup failure")
        original_close = IndependentPaneSessionV2.close

        def close_then_fail(surface) -> None:
            original_close(surface)
            raise close_error

        with self.assertRaises(AssertionError) as raised, \
             patch.object(IndependentPaneSessionV2, "close", new=close_then_fail):
            with self._owned_surface() as product:
                raise body_error
        self.assertIs(raised.exception, body_error)
        self.assertTrue(any("close presentation" in note and str(close_error) in note
                            for note in body_error.__notes__))
        self.assertEqual(product.pool.staged_resource_ids, ())
        self.assertEqual(product.pool.cleanup_pending_resource_ids, ())
        self.assertEqual(product.handle.session.retained_resource_count, 0)
        self.assertTrue(all(not worker._thread.is_alive()
                            for worker in product.handle.pump._workers.values()))
        self.assertFalse(product.handle.pump.control_pending())
        self.assertEqual(product.handle.queue.pending_count, 0)
        self.assertTrue(all(future.done() for future in product.surface._futures))
        self.assertFalse(is_qobject_valid(product.surface))

    def test_normal_cleanup_failure_is_reported(self) -> None:
        close_error = RuntimeError("injected normal close cleanup failure")
        original_close = IndependentPaneSessionV2.close

        def close_then_fail(surface) -> None:
            original_close(surface)
            raise close_error

        with self.assertRaisesRegex(AssertionError, "owned-surface cleanup failed.*close presentation"), \
             patch.object(IndependentPaneSessionV2, "close", new=close_then_fail):
            with self._owned_surface() as product:
                pass
        self.assertFalse(is_qobject_valid(product.surface))


if __name__ == "__main__":
    unittest.main()
