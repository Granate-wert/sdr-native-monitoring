"""A queued persistence paint keeps the receipt for the image still visible."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from threading import Event, Lock, Thread
import unittest

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import QApplication

from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.identity import TimestampQuality
from sdr_monitor.domain.live import LiveSpectrumFrame
from sdr_monitor.domain.layer_journal import LayerCreationEvent
from sdr_monitor.domain.layer_ready import (
    DensityLayerIdentity,
    LayerReadyKind,
    LayerReadyReceipt,
    ReadyClockMapping,
)
from sdr_monitor.domain.pane_delivery_obligation import (
    PaneDeliveryObligationRef,
    PaneDeliveryStage,
)
from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView, PaneLayerAnalyticalIdentity
from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
from sdr_monitor.ui.v2.spectrum.persistence_contracts import DensityValueMode, PersistenceDensityFrame
from sdr_monitor.ui.v2.spectrum.persistence_projector import PersistenceProjector
from sdr_monitor.ui.v2.spectrum.scene import SpectrumScene


def density_frame(value: float) -> PersistenceDensityFrame:
    density = np.full((16, 16), value, dtype=np.float32)
    frequencies = np.linspace(98.0, 102.0, 17, dtype=np.float64)
    levels = np.linspace(-100.0, -40.0, 17, dtype=np.float64)
    density.setflags(write=False)
    frequencies.setflags(write=False)
    levels.setflags(write=False)
    return PersistenceDensityFrame(
        density, frequencies, levels, DensityValueMode.PROBABILITY, "probability")


def persistence_ref(sequence: int) -> PaneDeliveryObligationRef:
    scope = OwnerJournalScope(
        "qt-test-clock", os.getpid(), "qt-test-owner", "qt-test-source", "RX1",
        "qt-test-session", 1, 1)
    layer = DensityLayerIdentity(
        "qt-test-source", 1, sequence, sequence, "qt-test-session", "RX1", 1, sequence)
    ready = LayerReadyReceipt(
        LayerReadyKind.DENSITY, layer, "qt-test-clock", os.getpid(), 41, sequence,
        150 + sequence, ReadyClockMapping.OUTSIDE_SAMPLES,
        owner_run_id="qt-test-owner", session_id="qt-test-session")
    event = LayerCreationEvent(
        LayerReadyKind.DENSITY, 41, sequence, 150 + sequence, False,
        0, 0, 0, 1, sequence, sequence, sequence)
    identity = PaneLayerAnalyticalIdentity(
        scope, ready, event, "qt-test-resource", "qt-test-capture", "RX1",
        "one", 1, 1)
    return PaneDeliveryObligationRef(
        "qt-test-graph", sequence, identity, PaneDeliveryView.PERSISTENCE)


def live_frame() -> LiveSpectrumFrame:
    return LiveSpectrumFrame(
        sequence=1, timestamp_ns=1_000_000_000, center_frequency_hz=100.0,
        sample_rate_hz=4.0, fft_size=4, hop_size=2,
        frequencies_hz=np.array((98.5, 99.5, 100.5, 101.5)),
        values=np.array((-90.0, -80.0, -70.0, -60.0), dtype=np.float32),
        unit="dBm", source_id="qt-test-source", config_generation=1,
        receiver_id="RX1", acquisition_epoch=1, clock_domain="unix_ns",
        timestamp_quality=TimestampQuality.HARDWARE)


class PersistenceVisibleCustodyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        if self._testMethodName != "test_new_pending_density_does_not_take_uploaded_image_a_custody":
            return
        trace_rows = []
        watched_images = set()
        observer_errors = [0]
        coverage_incomplete = [False]

        def observe(label, scene=None, *, candidate=None, ref=None, image_id=0,
                    host_match=None, region_hit=None, error=None):
            try:
                if len(trace_rows) >= 64:
                    coverage_incomplete[0] = True
                    return
                persistence = None if scene is None else scene._persistence
                visible = None if scene is None else scene._persistence_visible_delivery
                uploaded = None if persistence is None else persistence.uploaded_image
                image = None if persistence is None else persistence.image_item.image
                candidate_items = tuple(candidate) if isinstance(candidate, tuple) else (candidate,)
                trace_rows.append((label,
                    int(id(scene)) if scene is not None else 0,
                    tuple(int(id(item)) for item in candidate)
                          if isinstance(candidate, tuple) else
                          ((int(id(candidate)),) if candidate is not None else ()),
                    tuple(bool(item is not None and item.identity.ready.host_process_id == os.getpid())
                          for item in candidate_items if item is not None),
                    int(id(ref)) if ref is not None else 0,
                    int(id(uploaded)) if uploaded is not None else 0,
                    int(id(uploaded.view.source_frame)) if uploaded is not None else 0,
                    int(id(visible[0])) if visible is not None else 0,
                    int(id(visible[1])) if visible is not None else 0,
                    int(id(image)) if image is not None else int(image_id),
                    int(scene._delivery_custody_generation) if scene is not None else -1,
                    int(id(scene._persistence_paint_snapshot))
                    if scene is not None and scene._persistence_paint_snapshot is not None else 0,
                    bool(scene is not None and scene._presentation_active
                         and scene._graphics.isVisible() and scene._graphics.viewport().isVisible()
                         and scene._persistence.image_item.isVisible()),
                    region_hit,
                    host_match,
                    type(error).__name__ if error is not None else ""))
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True

        original_candidate = SpectrumScene._spectrum_custody_candidate
        original_timed = SpectrumScene._spectrum_paint_returned_timed
        original_legacy = SpectrumScene._spectrum_custody_returned
        original_paint = pg.ImageItem.paint

        def candidate(scene, event):
            try:
                result = original_candidate(scene, event)
            except BaseException as error:
                observe("candidate_error", scene, candidate=None, error=error)
                raise
            try:
                watched_images.add(int(id(scene._persistence.image_item)))
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True
            region_hit = None
            try:
                image_item = scene._persistence.image_item
                scene_rect = image_item.mapRectToScene(image_item.boundingRect()).intersected(
                    scene._view_box.sceneBoundingRect())
                viewport_rect = (scene._graphics.mapFromScene(scene_rect).boundingRect().intersected(
                    scene._graphics.viewport().rect()) if not scene_rect.isEmpty() else None)
                region_hit = bool(viewport_rect is not None and not viewport_rect.isEmpty()
                                  and not event.region().intersected(QRegion(viewport_rect)).isEmpty())
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True
            observe("candidate", scene, candidate=result, region_hit=region_hit)
            return result

        def receipt_host_match(receipt):
            try:
                return receipt.host_clock.process_id == receipt.ref.identity.ready.host_process_id
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True
                return None

        def receipt_ref(receipt):
            try:
                return receipt.ref
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True
                return None

        def timed(scene, receipt):
            try:
                result = original_timed(scene, receipt)
            except BaseException as error:
                observe("timed_gate_error", scene, ref=receipt_ref(receipt),
                        host_match=receipt_host_match(receipt), error=error)
                raise
            observe("timed_gate", scene, ref=receipt_ref(receipt),
                    host_match=receipt_host_match(receipt))
            return result

        def legacy(scene, refs):
            try:
                result = original_legacy(scene, refs)
            except BaseException as error:
                observe("legacy_gate_error", scene, candidate=refs, error=error)
                raise
            observe("legacy_gate", scene, candidate=refs)
            return result

        def image_paint(item, painter, *args):
            watched = int(id(item)) in watched_images
            before_id = 0
            if watched:
                try:
                    before_id = int(id(item.image)) if item.image is not None else 0
                except BaseException:
                    observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                    coverage_incomplete[0] = True
            try:
                result = original_paint(item, painter, *args)
            except BaseException as error:
                if watched:
                    observe("image_paint_error", image_id=before_id, error=error)
                raise
            if watched:
                observe("image_paint", image_id=before_id)
            return result

        def report_trace():
            result = getattr(getattr(self, "_outcome", None), "result", None)
            try:
                if result is not None and any(test is self for test, _ in result.failures + result.errors):
                    print("APP07-195 standalone custody trace:", trace_rows,
                          "observer_errors=", observer_errors[0],
                          "coverage_incomplete=", coverage_incomplete[0])
            except BaseException:
                observer_errors[0] = min(observer_errors[0] + 1, 2147483647)
                coverage_incomplete[0] = True

        self.addCleanup(report_trace)
        SpectrumScene._spectrum_custody_candidate = candidate
        SpectrumScene._spectrum_paint_returned_timed = timed
        SpectrumScene._spectrum_custody_returned = legacy
        pg.ImageItem.paint = image_paint

        def restore_observers():
            SpectrumScene._spectrum_custody_candidate = original_candidate
            SpectrumScene._spectrum_paint_returned_timed = original_timed
            SpectrumScene._spectrum_custody_returned = original_legacy
            pg.ImageItem.paint = original_paint

        self.addCleanup(restore_observers)

    def test_new_pending_density_does_not_take_uploaded_image_a_custody(self):
        release_b = Event()
        b_started = Event()
        worker_lock = Lock()
        worker_number = 0
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="persistence-custody-test")
        scene = None
        projector = None
        loop = QEventLoop()
        paint_timeout = QTimer()
        paint_timeout.setSingleShot(True)
        timed_receipts = []
        stage_events = []
        actual_paints = []
        callback_failures = []
        timeout_fired = []
        cleanup_errors = []
        image_a = {}

        def submit(operation):
            nonlocal worker_number
            with worker_lock:
                worker_number += 1
                number = worker_number

            def run():
                if number == 2:
                    b_started.set()
                    if not release_b.wait(3.0):
                        raise TimeoutError("test did not release pending density B")
                return operation()

            return pool.submit(run)

        def cleanup():
            release_b.set()
            if scene is not None:
                scene.quiesce_persistence_projection()
            if projector is not None:
                projector.dispose()

            joined = Event()

            def join_pool_off_gui():
                try:
                    pool.shutdown(wait=True, cancel_futures=True)
                except BaseException as error:
                    cleanup_errors.append(error)
                finally:
                    joined.set()

            joiner = Thread(target=join_pool_off_gui,
                            name="persistence-custody-test-shutdown", daemon=True)
            joiner.start()
            self._wait_for_event(joined, 3000, "projection worker did not join off the GUI thread")
            joiner.join(timeout=1.0)
            if joiner.is_alive():
                raise AssertionError("projection shutdown coordinator remained alive")
            if cleanup_errors:
                raise cleanup_errors[0]
            if projector is not None:
                projector.release_after_shutdown()
            self.assertTrue(all(not worker.is_alive() for worker in pool._threads))
            if scene is not None:
                scene.disconnect_persistence_projection_after_shutdown()
                scene.release_graphics_after_shutdown()
                scene.close()
            deleted = []
            if scene is not None:
                scene_deleted = Event()
                scene.destroyed.connect(lambda *_args: scene_deleted.set())
                deleted.append(scene_deleted)
                QTimer.singleShot(0, scene.deleteLater)
            if projector is not None:
                self.assertFalse(projector.has_active)
                self.assertFalse(projector.has_pending)
                self.assertEqual(projector.retained_bytes, 0)
                self.assertEqual(projector.allocation_budget.snapshot().reserved_bytes, 0)
                projector_deleted = Event()
                projector.destroyed.connect(lambda *_args: projector_deleted.set())
                deleted.append(projector_deleted)
                QTimer.singleShot(0, projector.deleteLater)
            self._wait_for_events(deleted, 3000, "deferred Qt QObject deletion was not acknowledged")

        # Cleanup is installed before any assertion or worker admission.
        self.addCleanup(cleanup)

        projector = PersistenceProjector(
            submit, allocation_budget=PresentationAllocationBudget(4 * 1024 * 1024))
        scene = SpectrumScene()
        scene.set_persistence_projection_port(projector)
        scene.resize(720, 420)
        scene._view_box.setRange(xRange=(98.0, 102.0), yRange=(-100.0, -40.0), padding=0)
        scene.set_delivery_stage_callback(lambda ref, stage: stage_events.append((ref, stage)))
        scene.set_paint_return_callback(lambda receipt: timed_receipts.append(receipt))

        a = density_frame(0.2)
        b = density_frame(0.8)
        ref_a = persistence_ref(1)
        ref_b = persistence_ref(2)
        original_upload_callback = scene._persistence_image_uploaded

        def uploaded(view):
            try:
                original_upload_callback(view)
                if view.source_frame is a:
                    image_a["image"] = scene._persistence.image_item.image
                    scene.set_persistence_delivery_ref(ref_b, b)
                    scene.set_persistence_frame(b)
            except Exception as error:
                # The production upload notifier intentionally shields callback errors.
                callback_failures.append(error)

        scene._persistence.on_image_uploaded = uploaded

        image_item_paint = pg.ImageItem.paint

        def observe_original_image_item_paint(item, painter, *args):
            is_target = scene is not None and item is scene._persistence.image_item
            source_before = item.image if is_target else None
            result = image_item_paint(item, painter, *args)
            source_after = item.image if is_target else None
            if is_target:
                request = scene._persistence.worker_request
                pending_view = scene._persistence.pending_view
                queued_source = (request.view.source_frame if request is not None else
                                 pending_view.source_frame if pending_view is not None else None)
                actual_paints.append((source_before, source_after, queued_source))
                if source_before is image_a.get("image") and queued_source is b:
                    QTimer.singleShot(0, loop.quit)
            return result

        pg.ImageItem.paint = observe_original_image_item_paint
        self.addCleanup(setattr, pg.ImageItem, "paint", image_item_paint)

        def stop_on_timeout():
            timeout_fired.append(True)
            loop.quit()

        paint_timeout.timeout.connect(stop_on_timeout)
        scene.set_frame(live_frame())
        scene.set_persistence_delivery_ref(ref_a, a)
        scene.set_persistence_frame(a)
        scene.show()
        paint_timeout.start(3000)
        loop.exec()
        paint_timeout.stop()

        self.assertFalse(callback_failures, callback_failures)
        self.assertFalse(timeout_fired, "ordinary Qt paint did not reach uploaded A while B was pending")
        self.assertTrue(actual_paints, "the original ImageItem.paint was not observed")
        self.assertIs(actual_paints[-1][0], image_a["image"])
        self.assertIs(actual_paints[-1][1], image_a["image"])
        self.assertIs(actual_paints[-1][2], b, "B was neither queued nor active at A's actual paint")
        self.assertIs(scene._persistence._uploaded_density, a.density)
        self.assertIs(scene._persistence.latest_view.density, b.density)

        # This is the defect assertion: A pixels really returned from the original
        # ImageItem.paint while B was pending or active, so A must keep its own receipt.
        self.assertIn((ref_a, PaneDeliveryStage.PAINT_RETURNED), stage_events)
        self.assertTrue(any(receipt.ref is ref_a for receipt in timed_receipts))
        self.assertFalse(any(receipt.ref is ref_b for receipt in timed_receipts))

    def _wait_for_event(self, event: Event, timeout_ms: int, message: str) -> None:
        self._wait_for_events((event,), timeout_ms, message)

    def _wait_for_events(self, events: tuple[Event, ...] | list[Event],
                         timeout_ms: int, message: str) -> None:
        loop = QEventLoop()
        poll = QTimer()
        poll.setSingleShot(True)
        timed_out = []

        def check():
            if all(event.is_set() for event in events):
                loop.quit()
            else:
                poll.start(10)

        def timeout():
            timed_out.append(True)
            loop.quit()

        poll.timeout.connect(check)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(timeout)
        deadline.start(timeout_ms)
        poll.start(0)
        loop.exec()
        deadline.stop()
        poll.stop()
        self.assertFalse(timed_out, message)

    def test_actual_prepared_pending_no_density_keeps_uploaded_a(self):
        from dataclasses import replace

        from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
        from sdr_monitor.domain.device_capabilities import stable_identity_key
        from sdr_monitor.domain.pane_delivery_obligation import PaneDeliveryStage
        from sdr_monitor.domain.pane_layer_identity import PaneDeliveryView
        from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode, PaneLayoutSlot, compile_pane_layout
        from sdr_monitor.domain.receiver_topology import ReceiverBindingMode
        from sdr_monitor.services.pane_resource_session import PaneResourceSession
        from sdr_monitor.services.receiver_lease_manager import ReceiverLeaseManager
        from sdr_monitor.ui.v2.spectrum.allocation_budget import PresentationAllocationBudget
        from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
        from sdr_monitor.ui.v2_pane_presentation import PaneDeliveryPreparer
        from sdr_monitor.ui.v2_pane_runtime import PaneFairDeliveryQueue, _ResourceWorker
        from sdr_monitor.ui.v2.workspaces.analyzer_pane import AnalyzerPaneViewV2
        from tests import test_app07_layer_ready_admission as layer_fixtures
        from tests import test_app07_pane_analytical_identity as analytical_fixtures
        from tests import test_app07_pane_layer_custody as layer_identity_fixtures
        from tests import test_app07_pane_resource_session as graph_fixtures
        from tests.test_app07_shared_capture_schedule import group, pane as pane_request, profile

        group_spec = group("device", "rx")
        layout = compile_pane_layout((PaneLayoutSlot(1, pane_request(
            "one", "rx", 100e6, 108e6, ReceiverBindingMode.DEDICATED_PARALLEL)),),
            (group_spec,), {"capture": profile(36e6, CaptureMeasurementMode.RTBW)})

        class Owner(analytical_fixtures.ScopedOwner):
            def start_capture(self, job):
                admission = super().start_capture(job)
                self.scope = replace(self.scope, receiver_id=None, host_process_id=os.getpid())
                return replace(admission, owner_journal_scopes=(("rx", self.scope),))

        owner = Owner()
        session = PaneResourceSession(layout.schedule, (group_spec,), {"device": owner},
            ReceiverLeaseManager(),
            source_identity_keys={"device:source": stable_identity_key("device:source")})
        session.apply()
        activation = session.start_resource("device")
        preparer = PaneDeliveryPreparer(layout, (group_spec,), PresentationAllocationBudget())
        stage_events = []
        timed_receipts = []
        paint_acceptances = []
        callback_failures = []
        loop = QEventLoop()
        timeout = QTimer()
        timeout.setSingleShot(True)
        timeout_fired = []
        trace_events = []
        trace_overflow = []
        trace_observer_errors = [0]
        trace_coverage_incomplete = [False]
        trace_phase = ["setup"]
        trace_ids = {"refs": {}, "sources": {}}

        def trace_sample(label, *, candidate=None, ref=None, source=None, accepted=None,
                         host_match=None, region_hit=None, error=None):
            if len(trace_events) >= 64:
                if not trace_overflow:
                    trace_overflow.append(True)
                trace_coverage_incomplete[0] = True
                return
            visible = scene._persistence_visible_delivery
            upload = scene._persistence.uploaded_image
            uploaded_source = None if upload is None else upload.view.source_frame
            snapshot = scene._persistence_paint_snapshot
            image = scene._persistence.image_item.image
            known_refs = trace_ids["refs"]
            known_sources = trace_ids["sources"]
            candidate_refs = tuple(candidate) if isinstance(candidate, tuple) else (candidate,)
            trace_events.append((
                label, trace_phase[0],
                tuple((name, any(item is not None and id(item) == known_refs.get(name)
                                  for item in candidate_refs))
                      for name in ("A", "B", "C", "D")),
                tuple((name, any(item is not None and id(item) == known_refs.get(name)
                                 and item.identity.ready.host_process_id == os.getpid()
                                 for item in candidate_refs))
                      for name in ("A", "B", "C", "D")),
                tuple((name, bool(visible is not None and id(visible[1]) == known_refs.get(name)))
                      for name in ("A", "B", "C", "D")),
                tuple((name, uploaded_source is not None and id(uploaded_source) == known_sources.get(name))
                      for name in ("A", "B", "C", "D")),
                tuple((name, source is not None and id(source) == known_sources.get(name))
                      for name in ("A", "B", "C", "D")),
                int(id(image)) if image is not None else 0,
                int(id(upload)) if upload is not None else 0,
                int(id(snapshot[0])) if snapshot is not None else 0,
                int(snapshot[1]) if snapshot is not None else -1,
                int(scene._delivery_custody_generation),
                bool(scene._presentation_active and scene._graphics.isVisible()
                     and scene._graphics.viewport().isVisible()
                     and scene._persistence.image_item.isVisible()),
                region_hit,
                tuple((name, ref is not None and id(ref) == known_refs.get(name))
                      for name in ("A", "B", "C", "D")),
                host_match, accepted, type(error).__name__ if error is not None else ""))

        def trace(label, *, candidate=None, ref=None, source=None, accepted=None,
                  host_match=None, region_hit=None, error=None):
            try:
                trace_sample(label, candidate=candidate, ref=ref, source=source,
                             accepted=accepted, host_match=host_match,
                             region_hit=region_hit, error=error)
            except BaseException:
                trace_observer_errors[0] = min(trace_observer_errors[0] + 1, 2147483647)
                trace_coverage_incomplete[0] = True

        def report_trace_if_failed():
            outcome = getattr(self, "_outcome", None)
            result = getattr(outcome, "result", None)
            try:
                if result is not None and any(test is self for test, _ in result.failures + result.errors):
                    print("APP07-195 bounded custody trace:", trace_events,
                          "trace_overflow=", bool(trace_overflow),
                          "observer_errors=", trace_observer_errors[0],
                          "coverage_incomplete=", trace_coverage_incomplete[0])
            except BaseException:
                trace_observer_errors[0] = min(trace_observer_errors[0] + 1, 2147483647)
                trace_coverage_incomplete[0] = True

        self.addCleanup(report_trace_if_failed)
        release_density_b = Event()
        density_b_started = Event()
        release_density_c = Event()
        density_c_started = Event()
        pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pane-custody-projector")
        projection_calls = 0

        def submit_projection(operation):
            nonlocal projection_calls
            projection_calls += 1
            if projection_calls not in {2, 3}:
                return pool.submit(operation)
            started, release, label = ((density_b_started, release_density_b, "B")
                                       if projection_calls == 2 else
                                       (density_c_started, release_density_c, "C"))

            def held_operation():
                started.set()
                if not release.wait(3.0):
                    raise TimeoutError(f"test did not release density {label} projection")
                return operation()

            return pool.submit(held_operation)

        projector = PersistenceProjector(submit_projection,
            allocation_budget=PresentationAllocationBudget(4 * 1024 * 1024))
        view = AnalyzerPaneViewV2(AnalyzerMode.RTBW, allocation_budget=projector.allocation_budget)
        scene = view.spectrum_scene
        scene.set_persistence_projection_port(projector)
        def record_stage(ref, stage):
            stage_events.append((ref, stage))
            return session.record_pane_delivery_stage(ref, stage)

        def record_paint_return(receipt):
            timed_receipts.append(receipt)
            try:
                accepted = session.record_pane_paint_return(receipt)
            except BaseException as error:
                trace("session_timed_receipt_error", ref=receipt.ref, error=error)
                raise
            paint_acceptances.append((receipt.ref, accepted))
            trace("session_timed_receipt", ref=receipt.ref, accepted=accepted)
            return accepted

        view._delivery_stage_callback = session.record_pane_delivery_stage
        scene.set_delivery_stage_callback(record_stage)
        scene.set_paint_return_callback(record_paint_return)
        queue = PaneFairDeliveryQueue(("one",), stage_callback=session.record_pane_delivery_stage)
        worker = _ResourceWorker(layout.schedule.resources[0], ("one",), session,
                                 preparer, queue, 0.01,
                                 stage_callback=session.record_pane_delivery_stage)
        worker._state = replace(worker._state, activation=activation)

        # Cleanup is registered before worker admission or assertions.
        def cleanup():
            release_density_b.set()
            release_density_c.set()
            scene.quiesce_persistence_projection()
            projector.dispose()
            joined = Event()
            join_errors = []

            def shutdown_pool():
                try:
                    pool.shutdown(wait=True, cancel_futures=True)
                except BaseException as error:
                    join_errors.append(error)
                finally:
                    joined.set()

            joiner = Thread(target=shutdown_pool, name="pane-custody-projector-join", daemon=True)
            joiner.start()
            self._wait_for_event(joined, 3000, "pane persistence worker failed to join off the GUI thread")
            joiner.join(timeout=1.0)
            if joiner.is_alive():
                raise AssertionError("pane persistence shutdown coordinator remained alive")
            if join_errors:
                raise join_errors[0]
            projector.release_after_shutdown()
            self.assertFalse(projector.has_active)
            self.assertFalse(projector.has_pending)
            self.assertEqual(projector.retained_bytes, 0)
            self.assertEqual(projector.allocation_budget.snapshot().reserved_bytes, 0)
            scene.disconnect_persistence_projection_after_shutdown()
            view.release_presentation_after_shutdown()
            view.close()
            session.stop_all()
            preparer.clear()
            deleted = []
            view_deleted = Event()
            projector_deleted = Event()
            view.destroyed.connect(lambda *_args: view_deleted.set())
            projector.destroyed.connect(lambda *_args: projector_deleted.set())
            deleted.extend((view_deleted, projector_deleted))
            QTimer.singleShot(0, view.deleteLater)
            QTimer.singleShot(0, projector.deleteLater)
            self._wait_for_events(deleted, 3000, "application fixture QObject deletion was not acknowledged")

        self.addCleanup(cleanup)

        base = graph_fixtures.live_frame(owner.scope.source_id, owner.scope.session_id,
                                         owner.scope.acquisition_epoch)
        spectrum_a = replace(base.spectrum, detector_ready=analytical_fixtures.ready(owner.scope))
        density_a = replace(
            layer_fixtures.density_frame(), source_id=spectrum_a.source_id,
            config_generation=spectrum_a.config_generation, source_frame_sequence=spectrum_a.sequence,
            frequency_bins=len(spectrum_a.values), frequencies_hz=spectrum_a.frequencies_hz,
            density=np.zeros((2, len(spectrum_a.values)), dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id,
            acquisition_epoch=owner.scope.acquisition_epoch, native_accumulation_sequence=3)
        density_a, journal = layer_identity_fixtures.packet(density_a, scope=owner.scope)
        owner.layer_journal_snapshots = lambda: (journal,)
        bundle_a = AnalyzerFrameBundle(spectrum_a, base.session_id, base.receiver_id,
                                       base.acquisition_epoch, base.rtbw, persistence=density_a)
        owner.publications.append(("rx", bundle_a))
        worker._poll_and_advance()
        prepared_a = queue.drain()
        self.assertEqual(len(prepared_a), 1)
        prepared_density_a = prepared_a[0].persistence
        ref_a = next(ref for ref in prepared_a[0].active_obligation_refs
                     if ref.view is PaneDeliveryView.PERSISTENCE)
        trace_ids["refs"]["A"] = id(ref_a)
        trace_ids["sources"]["A"] = id(prepared_density_a)

        spectrum_b = replace(base.spectrum, sequence=base.spectrum.sequence + 1,
                             timestamp_ns=base.spectrum.timestamp_ns + 1,
                             detector_ready=analytical_fixtures.ready(owner.scope, offer=2))
        bundle_b = AnalyzerFrameBundle(
            spectrum_b, base.session_id, base.receiver_id, base.acquisition_epoch, base.rtbw,
            coherence_issues=("persistence_pending",))
        owner.publications.append(("rx", bundle_b))
        worker._poll_and_advance()
        prepared_b = queue.drain()
        self.assertEqual(len(prepared_b), 1)
        self.assertIsNone(prepared_b[0].persistence)
        self.assertNotIn(PaneDeliveryView.PERSISTENCE,
                         {ref.view for ref in prepared_b[0].active_obligation_refs})

        pane_paints = []
        image_a = {}
        image_b = {}
        image_c = {}
        image_d = {}
        paint_target = {"image": None}
        original_upload_callback = scene._persistence_image_uploaded
        original_candidate = scene._graphics.custody_candidate
        original_legacy_return = scene._graphics.custody_returned
        original_timed_return = scene._graphics.paint_returned

        def trace_candidate(event):
            try:
                result = original_candidate(event)
            except BaseException as error:
                trace("candidate_error", error=error)
                raise
            region_hit = None
            try:
                image_item = scene._persistence.image_item
                scene_rect = image_item.mapRectToScene(image_item.boundingRect()).intersected(
                    scene._view_box.sceneBoundingRect())
                viewport_rect = (scene._graphics.mapFromScene(scene_rect).boundingRect().intersected(
                    scene._graphics.viewport().rect()) if not scene_rect.isEmpty() else None)
                region_hit = bool(viewport_rect is not None and not viewport_rect.isEmpty()
                                  and not event.region().intersected(QRegion(viewport_rect)).isEmpty())
            except BaseException as error:
                trace("candidate_region_error", error=error)
                trace_coverage_incomplete[0] = True
            try:
                visible = scene._persistence_visible_delivery
                visible_known = (visible is not None and
                                 id(visible[0].view.source_frame) in trace_ids["sources"].values())
                if result is not None or visible_known:
                    trace("candidate", candidate=result, region_hit=region_hit)
            except BaseException:
                trace_observer_errors[0] = min(trace_observer_errors[0] + 1, 2147483647)
                trace_coverage_incomplete[0] = True
            return result

        def trace_legacy_return(refs):
            try:
                result = original_legacy_return(refs)
            except BaseException as error:
                trace("legacy_return_error", candidate=refs, error=error)
                raise
            trace("legacy_return", candidate=refs, accepted=result)
            return result

        def trace_timed_return(receipt):
            try:
                result = original_timed_return(receipt)
            except BaseException as error:
                try:
                    host_match = receipt.host_clock.process_id == receipt.ref.identity.ready.host_process_id
                    trace("timed_return_error", ref=receipt.ref, host_match=host_match, error=error)
                except BaseException:
                    trace_observer_errors[0] = min(trace_observer_errors[0] + 1, 2147483647)
                    trace_coverage_incomplete[0] = True
                raise
            try:
                host_match = receipt.host_clock.process_id == receipt.ref.identity.ready.host_process_id
                trace("timed_return", ref=receipt.ref, host_match=host_match, accepted=result)
            except BaseException:
                trace_observer_errors[0] = min(trace_observer_errors[0] + 1, 2147483647)
                trace_coverage_incomplete[0] = True
            return result

        scene._graphics.custody_candidate = trace_candidate
        scene._graphics.custody_returned = trace_legacy_return
        scene._graphics.paint_returned = trace_timed_return
        self.addCleanup(setattr, scene._graphics, "paint_returned", original_timed_return)
        self.addCleanup(setattr, scene._graphics, "custody_returned", original_legacy_return)
        self.addCleanup(setattr, scene._graphics, "custody_candidate", original_candidate)
        trace_phase[0] = "A"

        def uploaded(image_view):
            try:
                trace("upload_before", source=image_view.source_frame)
                original_upload_callback(image_view)
                trace("upload_after", source=image_view.source_frame)
                if image_view.source_frame is prepared_density_a:
                    image_a["image"] = scene._persistence.image_item.image
                    paint_target["image"] = image_a["image"]
                    view.apply_prepared_pane_delivery(prepared_b[0])
                elif image_view.source_frame is image_b.get("source"):
                    image_b["image"] = scene._persistence.image_item.image
                    paint_target["image"] = image_b["image"]
                elif image_view.source_frame is image_c.get("source"):
                    image_c["image"] = scene._persistence.image_item.image
                    paint_target["image"] = image_c["image"]
                elif image_view.source_frame is image_d.get("source"):
                    image_d["image"] = scene._persistence.image_item.image
                    paint_target["image"] = image_d["image"]
            except BaseException as error:
                callback_failures.append(error)
                trace("upload_error", source=image_view.source_frame, error=error)
                raise

        scene._persistence.on_image_uploaded = uploaded
        original_paint = pg.ImageItem.paint

        def observe_paint(item, painter, *args):
            is_target = scene is not None and item is scene._persistence.image_item
            before = item.image if is_target else None
            if is_target:
                trace("image_paint_before")
            try:
                result = original_paint(item, painter, *args)
            except BaseException as error:
                if is_target:
                    trace("image_paint_error", error=error)
                raise
            after = item.image if is_target else None
            if is_target:
                pane_paints.append((before, after))
                trace("image_paint_after")
                if before is paint_target["image"]:
                    QTimer.singleShot(0, loop.quit)
            return result

        pg.ImageItem.paint = observe_paint
        self.addCleanup(setattr, pg.ImageItem, "paint", original_paint)

        def timed_out():
            timeout_fired.append(True)
            loop.quit()

        timeout.timeout.connect(timed_out)
        view.resize(800, 600)
        view.show()
        view.apply_prepared_pane_delivery(prepared_a[0])
        timeout.start(3000)
        loop.exec()
        timeout.stop()

        self.assertFalse(callback_failures, callback_failures)
        self.assertFalse(timeout_fired, "ordinary paint did not reach uploaded A after prepared B")
        self.assertTrue(pane_paints)
        self.assertIs(pane_paints[-1][0], image_a["image"])
        self.assertIs(pane_paints[-1][1], image_a["image"])
        self.assertIs(scene._persistence._uploaded_density, prepared_density_a.density)
        self.assertTrue(any(receipt.ref is ref_a for receipt in timed_receipts))
        self.assertTrue(any(ref is ref_a and accepted for ref, accepted in paint_acceptances))
        self.assertFalse(any(receipt.ref is not ref_a
                             and receipt.ref.view is PaneDeliveryView.PERSISTENCE
                             for receipt in timed_receipts))

        spectrum_b = replace(base.spectrum, sequence=base.spectrum.sequence + 2,
                             timestamp_ns=base.spectrum.timestamp_ns + 2,
                             detector_ready=analytical_fixtures.ready(owner.scope, offer=3))
        live_density_b = replace(
            layer_fixtures.density_frame(), source_id=spectrum_b.source_id,
            config_generation=spectrum_b.config_generation, source_frame_sequence=spectrum_b.sequence,
            frequency_bins=len(spectrum_b.values), frequencies_hz=spectrum_b.frequencies_hz,
            density=np.full((2, len(spectrum_b.values)), 0.5, dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id,
            acquisition_epoch=owner.scope.acquisition_epoch, native_accumulation_sequence=4)
        live_density_b, journal_b = layer_identity_fixtures.packet(
            live_density_b, scope=owner.scope, creation=2)
        owner.layer_journal_snapshots = lambda: (journal_b,)
        bundle_density_b = AnalyzerFrameBundle(
            spectrum_b, base.session_id, base.receiver_id, base.acquisition_epoch,
            base.rtbw, persistence=live_density_b)
        owner.publications.append(("rx", bundle_density_b))
        worker._poll_and_advance()
        prepared_density_b = queue.drain()
        self.assertEqual(len(prepared_density_b), 1)
        density_b_frame = prepared_density_b[0].persistence
        image_b["source"] = density_b_frame
        ref_b = next(ref for ref in prepared_density_b[0].active_obligation_refs
                     if ref.view is PaneDeliveryView.PERSISTENCE)
        trace_ids["refs"]["B"] = id(ref_b)
        trace_ids["sources"]["B"] = id(density_b_frame)
        trace_phase[0] = "B"
        view.apply_prepared_pane_delivery(prepared_density_b[0])
        self._wait_for_event(density_b_started, 3000, "new density B projection did not reach its held worker")
        self.assertIs(scene._persistence._uploaded_density, prepared_density_a.density)
        self.assertIs(scene._persistence.image_item.image, image_a["image"])
        release_density_b.set()
        paint_target["image"] = None
        timeout_fired.clear()
        timeout.start(3000)
        loop.exec()
        timeout.stop()
        self.assertFalse(timeout_fired, "ordinary paint did not reach the uploaded replacement B")
        self.assertIsNotNone(image_b.get("image"))
        self.assertIs(scene._persistence._uploaded_density, density_b_frame.density)
        self.assertTrue(any(receipt.ref is ref_b for receipt in timed_receipts))
        self.assertTrue(any(ref is ref_b and accepted for ref, accepted in paint_acceptances))

        spectrum_c = replace(base.spectrum, sequence=base.spectrum.sequence + 3,
                             timestamp_ns=base.spectrum.timestamp_ns + 3,
                             detector_ready=analytical_fixtures.ready(owner.scope, offer=4))
        live_density_c = replace(
            layer_fixtures.density_frame(), source_id=spectrum_c.source_id,
            config_generation=spectrum_c.config_generation, source_frame_sequence=spectrum_c.sequence,
            frequency_bins=len(spectrum_c.values), frequencies_hz=spectrum_c.frequencies_hz,
            density=np.full((2, len(spectrum_c.values)), 0.7, dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id,
            acquisition_epoch=owner.scope.acquisition_epoch, native_accumulation_sequence=5)
        live_density_c, journal_c = layer_identity_fixtures.packet(
            live_density_c, scope=owner.scope, creation=3)
        owner.layer_journal_snapshots = lambda: (journal_c,)
        bundle_density_c = AnalyzerFrameBundle(
            spectrum_c, base.session_id, base.receiver_id, base.acquisition_epoch,
            base.rtbw, persistence=live_density_c)
        owner.publications.append(("rx", bundle_density_c))
        worker._poll_and_advance()
        prepared_density_c = queue.drain()
        self.assertEqual(len(prepared_density_c), 1)
        density_c_frame = prepared_density_c[0].persistence
        image_c["source"] = density_c_frame
        ref_c = next(ref for ref in prepared_density_c[0].active_obligation_refs
                     if ref.view is PaneDeliveryView.PERSISTENCE)
        trace_ids["refs"]["C"] = id(ref_c)
        trace_ids["sources"]["C"] = id(density_c_frame)
        trace_phase[0] = "C"
        view.apply_prepared_pane_delivery(prepared_density_c[0])
        self._wait_for_event(density_c_started, 3000, "pending density C projection did not reach its held worker")
        self.assertIs(scene._persistence._uploaded_density, density_b_frame.density)

        spectrum_d = replace(base.spectrum, sequence=base.spectrum.sequence + 4,
                             timestamp_ns=base.spectrum.timestamp_ns + 4,
                             detector_ready=analytical_fixtures.ready(owner.scope, offer=5))
        live_density_d = replace(
            layer_fixtures.density_frame(), source_id=spectrum_d.source_id,
            config_generation=spectrum_d.config_generation, source_frame_sequence=spectrum_d.sequence,
            frequency_bins=len(spectrum_d.values), frequencies_hz=spectrum_d.frequencies_hz,
            density=np.full((2, len(spectrum_d.values)), 0.9, dtype=np.float32), receiver_id=None,
            accumulation_id=owner.scope.session_id,
            acquisition_epoch=owner.scope.acquisition_epoch, native_accumulation_sequence=6)
        live_density_d, journal_d = layer_identity_fixtures.packet(
            live_density_d, scope=owner.scope, creation=4)
        owner.layer_journal_snapshots = lambda: (journal_d,)
        bundle_density_d = AnalyzerFrameBundle(
            spectrum_d, base.session_id, base.receiver_id, base.acquisition_epoch,
            base.rtbw, persistence=live_density_d)
        owner.publications.append(("rx", bundle_density_d))
        worker._poll_and_advance()
        prepared_density_d = queue.drain()
        self.assertEqual(len(prepared_density_d), 1)
        density_d_frame = prepared_density_d[0].persistence
        image_d["source"] = density_d_frame
        ref_d = next(ref for ref in prepared_density_d[0].active_obligation_refs
                     if ref.view is PaneDeliveryView.PERSISTENCE)
        trace_ids["refs"]["D"] = id(ref_d)
        trace_ids["sources"]["D"] = id(density_d_frame)
        trace_phase[0] = "D"
        reentrant_failure = []
        reentrant_done = []
        original_stage_callback = session.record_pane_delivery_stage

        def reentrant_stage(ref, stage):
            accepted = original_stage_callback(ref, stage)
            stage_events.append((ref, stage))
            if ref is ref_c and stage is PaneDeliveryStage.PAINT_SUPERSEDED and not reentrant_done:
                reentrant_done.append(True)
                try:
                    view.apply_prepared_pane_delivery(prepared_density_d[0])
                except BaseException as error:
                    reentrant_failure.append(error)
            return accepted

        scene.set_delivery_stage_callback(reentrant_stage)
        scene.clear_persistence_display()
        self.assertEqual(reentrant_done, [True], "clear did not reenter with the exact newer prepared delivery")
        self.assertFalse(reentrant_failure, reentrant_failure)
        self.assertIs(scene._persistence.latest_view.source_frame, density_d_frame)
        release_density_c.set()
        paint_target["image"] = None
        timeout_fired.clear()
        timeout.start(3000)
        loop.exec()
        timeout.stop()
        self.assertFalse(timeout_fired, "ordinary paint did not reach the reentrant density D upload")
        self.assertIsNotNone(image_d.get("image"))
        self.assertIs(scene._persistence._uploaded_density, density_d_frame.density)
        self.assertTrue(any(receipt.ref is ref_d for receipt in timed_receipts))
        self.assertTrue(any(ref is ref_d and accepted for ref, accepted in paint_acceptances))
        self.assertIn((ref_c, PaneDeliveryStage.PAINT_SUPERSEDED), stage_events)
        self.assertNotIn((ref_c, PaneDeliveryStage.PAINT_RETURNED), stage_events)
        self.assertFalse(any(receipt.ref is ref_c for receipt in timed_receipts))
        self.assertFalse(callback_failures, callback_failures)

    def test_image_paint_witness_is_required_again_for_same_buffer_visual_commit(self):
        from dataclasses import replace

        from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode

        scene = SpectrumScene()
        receipts = []
        stages = []
        skipped_paints = Event()
        completed_image_paints = Event()
        paint_counts = [0]
        suppress_image_item = [True]
        image_item = scene._persistence.image_item
        image_item_type = type(image_item)
        original_item_paint = image_item_type.paint
        original_base_paint = pg.ImageItem.paint

        def cleanup_scene():
            scene.release_graphics_after_shutdown()
            scene.close()
            deleted = Event()
            scene.destroyed.connect(lambda *_args: deleted.set())
            QTimer.singleShot(0, scene.deleteLater)
            self._wait_for_event(deleted, 3000, "persistence scene QObject deletion was not acknowledged")

        self.addCleanup(cleanup_scene)

        def controlled_item_paint(item, painter, *args):
            if item is image_item and suppress_image_item[0]:
                skipped_paints.set()
                return None
            return original_item_paint(item, painter, *args)

        def observe_base_paint(item, painter, *args):
            result = original_base_paint(item, painter, *args)
            if item is image_item:
                paint_counts[0] += 1
                completed_image_paints.set()
            return result

        image_item_type.paint = controlled_item_paint
        pg.ImageItem.paint = observe_base_paint
        self.addCleanup(setattr, image_item_type, "paint", original_item_paint)
        self.addCleanup(setattr, pg.ImageItem, "paint", original_base_paint)

        scene.set_delivery_stage_callback(lambda ref, stage: stages.append((ref, stage)))

        def capture_return(receipt):
            receipts.append(receipt)
            return False

        scene.set_paint_return_callback(capture_return)
        scene.set_persistence_render_mode(PersistenceRenderMode.VISUAL)
        scene.set_frame(live_frame())
        density_a = density_frame(0.2)
        ref_a = persistence_ref(1)
        scene.set_persistence_delivery_ref(ref_a, density_a)
        scene.set_persistence_frame(density_a)
        upload_a = scene._persistence.uploaded_image
        self.assertIsNotNone(upload_a)
        scene.show()

        self._wait_for_event(skipped_paints, 3000, "ordinary Qt paint did not reach the controlled image-item seam")
        self.assertIsNone(scene._persistence.image_paint_witness)
        self.assertFalse(any(receipt.ref is ref_a for receipt in receipts))
        self.assertIs(scene._persistence.uploaded_image, upload_a)

        suppress_image_item[0] = False
        completed_image_paints.clear()
        image_item.update()
        self._wait_for_event(completed_image_paints, 3000, "ordinary image-item paint did not resume after seam restoration")
        self.assertIsNotNone(scene._persistence.image_paint_witness)
        witness_a = scene._persistence.image_paint_witness
        self.assertTrue(any(receipt.ref is ref_a for receipt in receipts))
        self.assertIn((ref_a, PaneDeliveryStage.PAINT_RETURNED), stages)

        # A new source frame may reuse the exact density storage. A VISUAL
        # hide/show rematerialization commits a new upload over that buffer.
        density_b = replace(density_a, density=density_a.density)
        ref_b = persistence_ref(2)
        scene.set_presentation_active(False)
        scene.set_persistence_delivery_ref(ref_b, density_b)
        scene.set_persistence_frame(density_b)
        skipped_paints.clear()
        suppress_image_item[0] = True
        scene.set_presentation_active(True)
        upload_b = scene._persistence.uploaded_image
        self.assertIsNotNone(upload_b)
        self.assertIsNot(upload_b, upload_a)
        self.assertTrue(np.shares_memory(upload_b.image, upload_a.image))
        self.assertEqual(upload_b.image.__array_interface__["data"][0],
                         upload_a.image.__array_interface__["data"][0])
        self.assertEqual(upload_b.image.shape, upload_a.image.shape)
        self.assertEqual(upload_b.image.strides, upload_a.image.strides)
        self.assertEqual(upload_b.image.dtype, upload_a.image.dtype)
        self.assertIs(upload_b.view.source_frame, density_b)
        self.assertIsNone(scene._persistence.image_paint_witness)
        self.assertFalse(any(receipt.ref is ref_b for receipt in receipts))

        self._wait_for_event(skipped_paints, 3000, "ordinary Qt paint did not reach the second controlled image-item seam")
        self.assertIsNone(scene._persistence.image_paint_witness)
        self.assertFalse(any(receipt.ref is ref_b for receipt in receipts))

        suppress_image_item[0] = False
        completed_image_paints.clear()
        image_item.update()
        self._wait_for_event(completed_image_paints, 3000, "ordinary image-item paint did not settle the new visual upload")
        witness_b = scene._persistence.image_paint_witness
        self.assertIsNotNone(witness_b)
        self.assertIsNot(witness_b, witness_a)
        self.assertTrue(any(receipt.ref is ref_b for receipt in receipts))
        self.assertIn((ref_b, PaneDeliveryStage.PAINT_RETURNED), stages)
        self.assertGreaterEqual(paint_counts[0], 2)


if __name__ == "__main__":
    unittest.main()
