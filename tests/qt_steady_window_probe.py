"""Short software-only steady observer on ORIGINAL Qt V2 / two MOCK producers.

Short durations qualify the harness, not the 15+60s physical baseline. No RF,
native FFT-rate, paint completeness, DWM or release-performance conclusion.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread, get_ident
from time import perf_counter_ns

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sdr_monitor.services.pane_paint_diagnostics import capture_pane_paint_observation
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2
from tests.qt_blocked_two_source_probe import _graph, StartRefreshResponsiveTests
from tests.steady_window_probe import (
    CounterReading, Observation, SteadyWindowProbe, WindowPlan, close_after_observer_join,
)


def main():
    StartRefreshResponsiveTests.setUpClass()
    fixture = StartRefreshResponsiveTests()
    triples = tuple(_graph(n) for n in (1, 2))
    sources = tuple(graph.live.discover(startup=True)[0].device_id for _, _, graph in triples)
    by_resource = dict(zip(("pane-resource-1", "pane-resource-2"), triples))
    pool = PaneProductGraphPool(lambda resource: by_resource[resource][2])
    handle = surface = probe = None
    feeders, failures, cleanup_errors = [], [], []
    stop = Event()
    body_error = None
    report = None
    callback_threads, main_thread = [], get_ident()
    try:
        prepared = prepare_user_pane_session((PaneSlotDraft(1, sources[0], 100e6, 108e6),
            PaneSlotDraft(2, sources[1], 140e6, 148e6), PaneSlotDraft(3), PaneSlotDraft(4)),
            pool_factory=lambda: pool)
        handle = prepared.handle
        apply_user_pane_session(prepared)
        surface = IndependentPaneSessionV2(handle)
        surface.resize(1280, 800)
        surface.show()
        surface.start_all.click()
        fixture._wait(lambda: all(state.phase is PanePumpPhase.RUNNING for state in handle.pump.snapshot()))
        engines = tuple(native.engines[0] for native, _, _ in triples)
        for engine in engines:
            def feed(engine=engine):
                try:
                    while not stop.is_set():
                        engine.emit()
                        stop.wait(.01)
                except BaseException as error:
                    failures.append(error)
                    stop.set()
            thread = Thread(target=feed, name="m78-steady-mock-feeder")
            feeders.append(thread)
            thread.start()
        fixture._wait(lambda: all(surface.board.pane(n).last_bundle is not None for n in (1, 2)))
        initial_sequences = tuple(surface.board.pane(n).last_bundle.spectrum.sequence for n in (1, 2))

        def capture():
            before = perf_counter_ns()
            callback_threads.append(get_ident())
            states = handle.pump.snapshot()
            resources = []
            for state, engine in zip(states, engines, strict=True):
                if state.phase is not PanePumpPhase.RUNNING:
                    raise ValueError("original resource no longer RUNNING")
                with engine._lock:
                    count = engine.produced
                resources.append(CounterReading(state.physical_stream_resource_id,
                    (json.dumps(asdict(state.activation), sort_keys=True, default=str),),
                    (("mock_producer_publications", count), ("prepared_publications", state.prepared_publications))))
            queue = handle.queue.metrics()
            resources.append(CounterReading("original-presentation-queue", ("same-original-graph",),
                tuple(asdict(queue).items()), (("pending", handle.queue.pending_count),)))
            paint = capture_pane_paint_observation(handle.session)
            return Observation(before, perf_counter_ns(), tuple(resources), paint)

        probe = SteadyWindowProbe(capture, plan=WindowPlan(.05, .3, .05))
        probe.start()
        # Normal Qt delivery/paint continues; no callback wait/audit/output on Qt.
        fixture._wait(probe.done.is_set)
        report = probe.result()
        fixture.assertTrue(all(thread != main_thread for thread in callback_threads))
        fixture.assertGreater(report["resources"][0]["counters"]["mock_producer_publications"]["delta"], 0)
        fixture.assertGreater(report["resources"][1]["counters"]["prepared_publications"]["delta"], 0)
        fixture.assertGreater(report["resources"][2]["counters"]["delivered"]["delta"], 0)
        fixture.assertTrue(all(surface.board.pane(n).last_bundle.spectrum.sequence > initial_sequences[n-1]
                               for n in (1, 2)))
        fixture.assertIsNotNone(report["paint_audit"])
        fixture.assertFalse(report["paint_audit"].lifetime_complete)
    except BaseException as error:
        body_error = error
    finally:
        stop.set()
        for thread in feeders:
            thread.join(5)
            if thread.is_alive():
                cleanup_errors.append(RuntimeError("mock feeder retained"))
        def close_readers():
            if handle is not None and surface is not None:
                try:
                    error = fixture._dispose(None, pool, handle, surface)
                    if error is not None:
                        cleanup_errors.append(error)
                except BaseException as error:
                    cleanup_errors.append(error)
            for native, service, graph in triples:
                try:
                    graph.live.shutdown()
                    fixture.assertIsNone(service._poller)
                    fixture.assertIsNone(service._engine)
                    fixture.assertTrue(all(not engine.thread.is_alive() for engine in native.engines))
                except BaseException as error:
                    cleanup_errors.append(error)

        try:
            # Failed join retains ALL readers; no _dispose/owner-close on that path.
            close_after_observer_join(probe, close_readers)
        except BaseException as error:
            cleanup_errors.append(error)
    if body_error is not None:
        for error in cleanup_errors:
            body_error.add_note(str(error))
        raise body_error
    fixture.assertEqual(cleanup_errors, [])
    fixture.assertEqual(failures, [])
    print("M78_STEADY_ORIGINAL " + json.dumps(dict(observations=report["observation_count"],
        declared_window_s=report["declared_window_s"], callback_off_qt=True,
        paint_unique_retained=report["paint_audit"].unique_events,
        queue_delivered_delta=report["resources"][2]["counters"]["delivered"]["delta"],
        cleanup_confirmed=True, scope="short original composition / MOCK only / not physical performance")), flush=True)


if __name__ == "__main__":
    main()
