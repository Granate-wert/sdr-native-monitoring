"""Optional density retirement is distinct from receiver shutdown; no RF proof."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread
from time import monotonic, sleep
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtWidgets import QApplication

from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceDensityView
from sdr_monitor.ui.v2.spectrum.persistence_projector import PersistenceWork
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene
from sdr_monitor.ui.v2.workspaces.independent_pane_persistence import IndependentPanePersistenceLanes
from sdr_monitor.ui.v2.workspaces.independent_pane_setup import IndependentPaneSetupV2
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from sdr_monitor.ui.v2.product_live import V2LiveProductComposition, compose_v2_live_product
from sdr_monitor.ui.v2.shell.close_lifecycle import CloseLifecycle
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session, apply_user_pane_session
from tests.ui_v2.test_app07_pane_graph_pool import _ad_graph
from tests.ui_v2.test_app05_persistence_worker import view
from tests.ui_v2.test_app07_independent_persistence_lane import LaneFixture
from tests.ui_v2.test_live_product_composition import FakePresenter


class IndependentPersistenceLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.fixture = LaneFixture()

    def tearDown(self):
        self.fixture.close(self.app)
        self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)

    def offer(self, pane_id="left", value=.3):
        scene = self.fixture.scene(pane_id)
        frame = view(np.full((256, 4096), value, np.float32))
        scene._persistence._last_upload_ns = None
        scene.set_persistence_frame(frame.source_frame)
        return frame

    def test_active_not_done_and_queued_retirement_never_waits_for_future(self):
        self.offer()
        self.offer("right")
        scene = self.fixture.scene()
        future = scene._density_port._future
        self.assertTrue(future.set_running_or_notify_cancel())
        self.fixture.lanes.quiesce()
        with patch.object(future, "result", side_effect=AssertionError("Qt wait")):
            self.assertFalse(self.fixture.lanes.poll_retired())
        self.assertFalse(self.fixture.lanes.retired)
        self.assertIsNotNone(scene._density_port._future)
        # Complete the already-cancelled active worker; its result must not
        # upload. A queued Future was cancelled, no settled signal required.
        self.fixture.worker.jobs.pop(0)
        future.set_result(None)
        self.assertTrue(self.fixture.lanes.poll_retired())
        self.assertEqual(scene.persistence_metrics.image_uploads, 0)
        self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)
        self.app.processEvents()  # Late queued _done is now harmless.
        self.assertTrue(self.fixture.lanes.poll_retired())

    def test_graphics_release_refuses_before_gui_ack_even_if_future_done(self):
        self.offer()
        scene = self.fixture.scene()
        self.fixture.worker.finish()
        self.fixture.lanes.quiesce()
        with self.assertRaises(RuntimeError):
            scene.release_graphics_after_shutdown()
        self.assertTrue(self.fixture.lanes.poll_retired())
        # Board release owns clear-before-axes retirement. Do not retire a
        # single scene and later clear that scene again during board teardown.
        self.fixture.base.board.release_presentation_after_shutdown()

    def test_retirement_does_not_depend_on_settled(self):
        self.offer()
        settled = []
        self.fixture.scene()._density_port.settled.connect(settled.append)
        self.fixture.lanes.quiesce()
        self.fixture.drain(self.app)
        self.assertTrue(self.fixture.lanes.poll_retired())
        self.assertFalse(settled)

    def test_terminal_release_requires_quiesce(self):
        with self.assertRaises(RuntimeError):
            self.fixture.lanes.release_after_shutdown()

    def test_stop_cancellation_cannot_resubmit_before_ack_even_explicit_show(self):
        self.offer()
        scene = self.fixture.scene()
        work = scene._density_port._active
        scene.set_ui_stop_pending(True)
        scene.set_persistence_visible(False)
        scene.set_persistence_visible(True)
        scene._persistence_projection_settled(PersistenceWork(scene._projection_owner, work.request))
        self.fixture.drain(self.app)
        self.assertFalse(self.fixture.worker.jobs)
        self.assertEqual(scene.persistence_metrics.image_uploads, 0)
        self.assertIsNone(scene._persistence.worker_request)
        scene.set_ui_stop_pending(False)
        self.fixture.drain(self.app)
        self.assertEqual(scene.persistence_metrics.image_uploads, 1)

    def test_count_labels_property_is_never_reduced_on_gui(self):
        source = view(np.full((256, 4096), 10., np.float32), DensityValueMode.COUNT)
        with patch.object(PersistenceDensityView, "quantitative_labels", new=property(
                lambda _view: (_ for _ in ()).throw(AssertionError("synchronous Count reduction")))):
            self.fixture.scene().set_persistence_frame(source.source_frame)
            self.fixture.drain(self.app)
        self.assertEqual(self.fixture.scene().persistence_metrics.image_uploads, 1)

    def test_invalid_density_refuses_before_discarding_last_valid_history(self):
        self.offer()
        self.fixture.drain(self.app)
        scene = self.fixture.scene()
        old = scene._persistence.image_item.image
        frame = scene._persistence.latest_view.source_frame
        values = np.full((256, 4096), .3, np.float32)
        values[-1, -1] = -1.
        invalid = SimpleNamespace(density=values, frequency_edges_hz=frame.frequency_edges_hz,
            level_edges=frame.level_edges, value_mode="probability", level_unit="dBm")
        with self.assertRaises(ValueError):
            scene.set_persistence_frame(invalid)
        self.assertIs(scene._persistence.image_item.image, old)

    def test_submit_failure_is_optional_denial_and_no_retry_spin(self):
        scene = self.fixture.scene()
        with patch.object(scene._density_port, "_submit", side_effect=RuntimeError("submit refused")):
            self.offer()
        self.assertEqual(scene.persistence_metrics.allocation_denials, 1)
        self.assertIsNone(scene._persistence.image_item.image)
        self.assertIsNone(scene._density_port._future)
        self.assertIsNone(scene._persistence.pending_view)
        self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)
        for _ in range(8):
            self.app.processEvents()
        self.assertFalse(self.fixture.worker.jobs)

    def test_shared_pressure_denies_output_and_explicit_show_recovers(self):
        ticket = self.fixture.budget.reserve(self.fixture.budget.limit_bytes - 8_500_000)
        try:
            self.offer()
            self.offer("right")
            self.assertGreater(self.fixture.scene("right").persistence_metrics.allocation_denials, 0)
        finally:
            ticket.close()
        self.fixture.drain(self.app)
        denied = self.fixture.scene("right")
        denied.set_persistence_visible(False)
        denied.set_persistence_visible(True)
        self.fixture.drain(self.app)
        self.assertEqual(denied.persistence_metrics.image_uploads, 1)
        self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)

    def test_partial_attach_after_mutation_retires_all_created_ports(self):
        owner = IndependentPanePersistenceLanes(self.fixture.handle,
            submit=self.fixture.worker.submit, allocation_budget=self.fixture.budget)
        scene = SpectrumScene()
        original = scene.set_persistence_projection_port
        def failed(port):
            original(port)
            raise RuntimeError("after assignment")
        try:
            with patch.object(scene, "set_persistence_projection_port", side_effect=failed):
                with self.assertRaisesRegex(RuntimeError, "after assignment"):
                    owner.attach("left", scene)
            self.assertTrue(owner.retired)
            self.assertTrue(scene._density_disconnected)
        finally:
            scene.release_graphics_after_shutdown()
            scene.close()

    def test_cleanup_attempts_other_ports_if_one_scene_quiesce_fails(self):
        with patch.object(self.fixture.scene(), "quiesce_persistence_projection",
                          side_effect=RuntimeError("scene cleanup")):
            with self.assertRaisesRegex(RuntimeError, "scene cleanup"):
                self.fixture.lanes.quiesce()
        self.assertTrue(all(port._closed for _scene, port in self.fixture.lanes._lanes.values()))
        self.assertTrue(self.fixture.scene("right")._density_quiesced)

    def test_foreign_thread_lifecycle_refuses_before_mutation(self):
        errors = []
        def foreign():
            try:
                self.fixture.lanes.quiesce()
            except Exception as error:
                errors.append(error)
        thread = Thread(target=foreign)
        thread.start()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertFalse(self.fixture.lanes._retiring)

    def test_controller_polls_without_uninstall_until_optional_ack(self):
        ready = [False]
        installed = []
        editor = IndependentPaneSetupV2(install=lambda _handle: None,
            uninstall=lambda: installed.append("uninstall"),
            begin_retirement=lambda _handle: None,
            poll_retirement=lambda _handle: ready[0], allocation_budget=self.fixture.budget)
        try:
            editor._closing_handle = self.fixture.handle
            editor._operation = "retire_presentation"
            editor._timer.start()
            editor._poll()
            self.assertFalse(installed)
            self.assertFalse(editor.can_close)
            self.assertTrue(editor._timer.isActive())
            ready[0] = True
            editor._poll()
            self.assertEqual(installed, ["uninstall"])
            self.assertTrue(editor.can_close)
        finally:
            editor._closing_handle = None
            editor._operation = None
            editor.release_after_shutdown()
            editor.close()

    def test_controller_retains_failed_retirement_as_nonterminal(self):
        editor = IndependentPaneSetupV2(install=lambda _handle: None, uninstall=lambda: None,
            begin_retirement=lambda _handle: None,
            poll_retirement=lambda _handle: (_ for _ in ()).throw(RuntimeError("ack failed")))
        try:
            editor._closing_handle = self.fixture.handle
            editor._operation = "retire_presentation"
            editor._poll()
            self.assertFalse(editor.can_close)
            self.assertIs(editor._closing_handle, self.fixture.handle)
            self.assertFalse(editor._timer.isActive())
        finally:
            editor._closing_handle = None
            editor._operation = None
            editor.release_after_shutdown()
            editor.close()

    def test_real_executor_active_job_retained_until_offqt_join_then_gui_release(self):
        entered, finish = Event(), Event()
        executor = ThreadPoolExecutor(max_workers=1)
        def submit(operation):
            def delayed():
                entered.set()
                if not finish.wait(3):
                    raise RuntimeError("bounded test release missing")
                return operation()
            return executor.submit(delayed)
        owner = IndependentPanePersistenceLanes(self.fixture.handle,
            submit=submit, allocation_budget=self.fixture.budget)
        scene = SpectrumScene()
        owner.attach("left", scene)
        try:
            scene.set_persistence_frame(view(np.full((256, 4096), .3, np.float32)).source_frame)
            self.assertTrue(entered.wait(3))
            owner.quiesce()
            self.assertFalse(owner.poll_retired())
            finish.set()
            joined = Event()
            thread = Thread(target=lambda: (executor.shutdown(wait=True, cancel_futures=True), joined.set()))
            thread.start()
            thread.join(3)
            self.assertTrue(joined.is_set())
            owner.release_after_shutdown()
            self.assertTrue(owner.retired)
            self.assertEqual(scene.persistence_metrics.image_uploads, 0)
            self.assertEqual(self.fixture.budget.snapshot().reserved_bytes, 0)
        finally:
            finish.set()
            executor.shutdown(wait=True, cancel_futures=True)
            owner.quiesce()
            owner.release_after_shutdown()
            scene.release_graphics_after_shutdown()
            scene.close()

    def test_actual_staged_handle_session_attaches_before_first_frame_same_ledger(self):
        _native, graph = _ad_graph(serial="")
        pool = PaneProductGraphPool(lambda _resource: graph)
        session = None
        lanes = None
        handle = None
        try:
            choice = graph.live.discover(startup=True)[0]  # Existing fake SDK only.
            drafts = tuple(PaneSlotDraft(number, choice.device_id, 100e6, 108e6)
                           for number in (1, 2, 3)) + (PaneSlotDraft(4),)
            staged = prepare_user_pane_session(drafts, pool_factory=lambda: pool,
                allocation_budget=self.fixture.budget)
            apply_user_pane_session(staged)
            handle = staged.handle
            self.assertIs(handle.preparer.allocation_budget, self.fixture.budget)
            lanes = IndependentPanePersistenceLanes(handle, submit=self.fixture.worker.submit,
                allocation_budget=self.fixture.budget)
            session = IndependentPaneSessionV2(handle, density_lanes=lanes)
            self.assertEqual(len(lanes._lanes), 3)
            self.assertTrue(all(scene._projector is None for scene, _port in lanes._lanes.values()))
            session.quiesce_presentation()
            self.assertTrue(session.poll_presentation_retired())
            for future in handle.pump.stop_all().values():
                future.result(timeout=5)
            handle.shutdown_after_stop()
            session.release_presentation_after_shutdown()
        finally:
            if session is not None:
                session.quiesce_presentation()
            elif lanes is not None:
                lanes.quiesce()
            self.fixture.drain(self.app)
            if handle is not None and not handle.shutdown_complete:
                for future in handle.pump.stop_all().values():
                    future.result(timeout=5)
                handle.shutdown_after_stop()
            if session is not None:
                session.release_presentation_after_shutdown()
                session.close()
            pool.close()
            graph.live.shutdown()

    def test_async_quiesce_error_still_attempts_resource_and_optional_executor_join(self):
        calls = []
        class SplitPresenter(FakePresenter):
            def prepare_shutdown(self):
                calls.append("prepare-live")
            def finish_shutdown(self):
                calls.append("join-live-optional")
        owner = compose_v2_live_product(SplitPresenter(), async_shutdown=True)
        owner._pane_handle = SimpleNamespace(shutdown_after_stop=lambda: calls.append("resource-shutdown"))
        original = RuntimeError("density quiesce failed")
        with patch.object(owner, "begin_independent_presentation_retirement", side_effect=original):
            lifecycle = CloseLifecycle(owner._prepare_async_shutdown)
            state = lifecycle.request()
        deadline = monotonic() + 3
        while state.phase == "pending" and monotonic() < deadline:
            state = lifecycle.poll()
            sleep(.001)
        self.assertEqual(state.phase, "failed")
        self.assertIn("resource-shutdown", calls)
        self.assertIn("join-live-optional", calls)
        self.assertIn("independent-presentation-quiesce", state.detail)
        self.assertFalse(owner._terminal_presentation_released)
        self.assertIsNone(lifecycle._worker)

    def test_density_release_error_attempts_unrelated_terminal_presentation(self):
        calls = []
        owner = compose_v2_live_product(FakePresenter())
        owner._independent_density_lanes = SimpleNamespace(release_after_shutdown=
            lambda: (_ for _ in ()).throw(RuntimeError("density release failed")))
        with patch.object(owner.view_model, "release_presentation_after_shutdown",
                          side_effect=lambda: calls.append("view-release")):
            with self.assertRaisesRegex(RuntimeError, "density release failed"):
                owner._release_terminal_presentation()
        self.assertEqual(calls, ["view-release"])
        self.assertFalse(owner._terminal_presentation_released)
        owner._independent_density_lanes = None
        owner.shutdown()

    def test_async_density_release_fault_returns_failed_not_complete(self):
        owner = compose_v2_live_product(FakePresenter(), async_shutdown=True)
        # Join phase is already complete; final GUI release remains independent.
        owner.close_lifecycle.state = SimpleNamespace(phase="complete")
        original = RuntimeError("density release acknowledgement failed")
        with patch.object(owner, "_release_terminal_presentation", side_effect=original):
            state = owner.poll_shutdown()
        self.assertEqual(state.phase, "failed")
        self.assertIn("density release acknowledgement failed", state.detail)
        self.assertFalse(owner._is_shutdown)

    def test_outer_composition_preserves_primary_and_independent_cleanup_errors(self):
        calls = []
        original = RuntimeError("primary widget install")
        lanes = SimpleNamespace(retired=False,
            quiesce=lambda: (calls.append("quiesce"), (_ for _ in ()).throw(ValueError("quiesce error"))),
            release_after_shutdown=lambda: (calls.append("release"), (_ for _ in ()).throw(ValueError("release error"))))
        owner = V2LiveProductComposition.__new__(V2LiveProductComposition)
        owner._pane_handle = owner._failed_independent_density_lanes = None
        owner._is_shutdown = owner._presentation_disposed = False
        owner.analyzer_view_model = object()
        owner.allocation_budget = self.fixture.budget
        owner._independent_persistence_submit = self.fixture.worker.submit
        owner.live_calibration_view_model = None
        widget = SimpleNamespace(install_independent_pane_session=lambda *args, **kwargs:
            (_ for _ in ()).throw(original))
        owner._analyzer_workspace_ref = lambda: widget
        with patch.object(V2LiveProductComposition, "can_close", return_value=True), \
             patch("sdr_monitor.ui.v2.product_live.IndependentPanePersistenceLanes", return_value=lanes):
            with self.assertRaises(RuntimeError) as caught:
                owner.install_independent_pane_session(self.fixture.handle)
        self.assertIs(caught.exception, original)
        self.assertEqual(calls, ["quiesce", "release"])
        self.assertEqual(len(caught.exception.__cause__.exceptions), 2)
        self.assertIs(owner._failed_independent_density_lanes, lanes)
        self.assertIsNone(owner._pane_handle)

    def test_outer_session_preserves_attach_error_and_retains_unretired_surface(self):
        # Reuse a real minimal Session constructor, but provide its normal
        # qualified typed handle via the existing Stage fake-graph fixture.
        _native, graph = _ad_graph(serial="")
        pool = PaneProductGraphPool(lambda _resource: graph)
        handle = None
        lanes = None
        original = RuntimeError("primary outer attach")
        try:
            choice = graph.live.discover(startup=True)[0]
            staged = prepare_user_pane_session((PaneSlotDraft(1, choice.device_id, 100e6, 108e6),
                PaneSlotDraft(2), PaneSlotDraft(3), PaneSlotDraft(4)), pool_factory=lambda: pool,
                allocation_budget=self.fixture.budget)
            apply_user_pane_session(staged)
            handle = staged.handle
            lanes = IndependentPanePersistenceLanes(handle, submit=self.fixture.worker.submit,
                allocation_budget=self.fixture.budget)
            with patch.object(lanes, "attach", side_effect=original), \
                 patch.object(lanes, "quiesce", side_effect=ValueError("outer quiesce")), \
                 patch.object(lanes, "release_after_shutdown", side_effect=ValueError("outer release")):
                with self.assertRaises(RuntimeError) as caught:
                    IndependentPaneSessionV2(handle, density_lanes=lanes)
            self.assertIs(caught.exception, original)
            self.assertEqual(len(caught.exception.__cause__.exceptions), 2)
            self.assertIsNotNone(lanes._failed_surface)
            self.assertFalse(lanes.retired)
            self.assertFalse(lanes._failed_surface.delivery._timer.isActive())
        finally:
            if handle is not None:
                for future in handle.pump.stop_all().values():
                    future.result(timeout=5)
                handle.shutdown_after_stop()
            if lanes is not None:
                lanes.quiesce()
                lanes.release_after_shutdown()
                surface = getattr(lanes, "_failed_surface", None)
                if surface is not None:
                    surface.release_presentation_after_shutdown()
                    surface.setParent(None)
                    surface.deleteLater()
                    lanes._failed_surface = None
            pool.close()
            graph.live.shutdown()
