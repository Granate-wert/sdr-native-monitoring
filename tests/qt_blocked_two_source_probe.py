"""Isolated G04 ORIGINAL UI V2 composition; threaded MOCK native dependencies only.

The Qt callback really blocks. No patched delivery/pump/owner/watchdog, SDK, RF,
paint-rate, transport loss or release claim. Parent captures the real watchdog.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from threading import Event, Thread, get_ident
from time import monotonic
from types import SimpleNamespace

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
from PySide6.QtCore import QTimer

from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.qt_stall_watchdog import QtStallWatchdog
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_runtime import PanePumpPhase
from sdr_monitor.ui.v2_pane_user_plan import PaneSlotDraft
from sdr_monitor.ui.v2_pane_user_stage import apply_user_pane_session, prepare_user_pane_session
from sdr_monitor.ui.v2.workspaces.independent_pane_session import IndependentPaneSessionV2

from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep
from tests.test_app07_producer_worker_failure import _ThreadedEngine
from tests.ui_v2.test_app07_start_refresh_responsive import StartRefreshResponsiveTests
from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative


class _Native(_ObservedReadbackNative):
    def PlutoFixedBandEngine(self, uri, timeout_ms, *, expected_serial=None):
        if expected_serial != self.serial:
            raise RuntimeError("mock receiver identity differs")
        engine = _ThreadedEngine(uri, timeout_ms)
        self.engines.append(engine)
        return engine


def _graph(number):
    native = _Native(serial=f"g04-mock-{number}", uri=f"ip:g04-mock-{number}.local")
    service = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(service),),
                                      control_transaction=service.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=service, analyzer_display=_UnusedSweep(), device_catalog=catalog))
    return native, service, graph


def main() -> None:
    StartRefreshResponsiveTests.setUpClass()
    fixture = StartRefreshResponsiveTests()
    triples = tuple(_graph(number) for number in (1, 2))
    sources = tuple(graph.live.discover(startup=True)[0].device_id for _, _, graph in triples)
    by_resource = dict(zip(("pane-resource-1", "pane-resource-2"), triples))
    pool = PaneProductGraphPool(lambda resource: by_resource[resource][2])
    handle = surface = None
    feed_stop, release, entered, phase_armed = Event(), Event(), Event(), Event()
    feeders = []
    supervisor = None
    body_error = None
    cleanup_errors = []
    result = {}
    try:
        # Original user Stage/Apply rather than a hand-assembled pump session.
        prepared = prepare_user_pane_session((
            PaneSlotDraft(1, sources[0], 100e6, 108e6),
            PaneSlotDraft(2, sources[1], 140e6, 148e6), PaneSlotDraft(3), PaneSlotDraft(4),
        ), pool_factory=lambda: pool)
        fixture.assertIsNotNone(prepared.handle, str(prepared))
        handle = prepared.handle
        apply_user_pane_session(prepared)
        surface = IndependentPaneSessionV2(handle)
        surface.start_all.click()
        fixture._wait(lambda: all(state.phase is PanePumpPhase.RUNNING
                                 for state in handle.pump.snapshot()))
        engines = tuple(native.engines[0] for native, _, _ in triples)
        pollers = tuple(service._poller for _, service, _ in triples)
        render_events, render_errors, feed_errors, observation_errors = [], [], [], []
        stage = {"value": "baseline"}
        panes = tuple(surface.board.pane(number) for number in (1, 2))
        pane_ids = tuple(slot.request.pane_id for slot in handle.layout.slots if slot.request)

        def delivered(pane_id):
            # Qt signal exceptions can be swallowed; commit observations only
            # after inspecting a real bundle, then assert them outside callbacks.
            try:
                pane = panes[pane_ids.index(pane_id)]
                render_events.append((stage["value"], pane_id, pane.last_bundle.spectrum.sequence, get_ident()))
            except BaseException as error:
                render_errors.append(error)

        surface.delivery.rendered.connect(delivered)
        for engine in engines:
            def feed(engine=engine):
                try:
                    while not feed_stop.is_set():
                        engine.emit()
                        feed_stop.wait(.01)
                except BaseException as error:
                    feed_errors.append(error)
                    feed_stop.set()
            thread = Thread(target=feed, name=f"g04-feeder-{engine.uri}")
            feeders.append(thread)
            thread.start()
        fixture._wait(lambda: all(pane.last_bundle is not None for pane in panes))
        main_thread = get_ident()
        blocked = {}
        callback_errors = []
        # Construction is inert. The one watchdog is armed only inside the
        # target Qt callback after its entry marker has been recorded.
        watchdog = QtStallWatchdog(sys.stderr, .08)

        def counts():
            values = []
            for engine in engines:
                with engine._lock:
                    values.append(engine.produced)
            return tuple(values)

        def prepared_counts():
            return tuple(state.prepared_publications for state in handle.pump.snapshot())

        def observe_block():
            try:
                if not phase_armed.wait(3):
                    raise AssertionError("watchdog phase was not armed inside Qt callback")
                deadline = monotonic() + 3
                pending_samples = []
                while monotonic() < deadline:
                    pending_samples.append(handle.queue.pending_count)
                    produced, prepared_now = counts(), prepared_counts()
                    if (monotonic() - blocked["watchdog_armed_at"] >= .25
                            and all(a >= b + 3 for a, b in zip(produced, blocked["produced"]))
                            and all(a >= b + 3 for a, b in zip(prepared_now, blocked["prepared"]))):
                        blocked.update(produced_after=produced, prepared_after=prepared_now,
                                       pending_samples=pending_samples,
                                       delivered_after=handle.queue.metrics().delivered,
                                       superseded_after=handle.queue.metrics().latest_superseded)
                        break
                    Event().wait(.005)
                else:
                    raise AssertionError("mock acquisition/preparation stalled behind blocked Qt")
            except BaseException as error:
                observation_errors.append(error)
            finally:
                release.set()

        def blocked_callback():
            blocked.update(started=monotonic(), produced=counts(), prepared=prepared_counts(),
                           sequences=tuple(pane.last_bundle.spectrum.sequence for pane in panes),
                           delivered_before=handle.queue.metrics().delivered,
                           superseded_before=handle.queue.metrics().latest_superseded,
                           thread=get_ident(), callback_entered=True)
            stage["value"] = "blocked"
            entered.set()
            try:
                # Arm and rearm the same instance only after entering the
                # callback, so its phase dump must observe this blocked stack.
                watchdog.arm()
                watchdog.rearm_phase("qt_blocked_two_producer_delivery")
                blocked.update(watchdog_phase_armed=True, watchdog_armed_at=monotonic())
                phase_armed.set()
                blocked["release_received"] = release.wait(5)
                blocked["elapsed"] = monotonic() - blocked["watchdog_armed_at"]
            except BaseException as error:
                callback_errors.append(error)
                release.set()
            finally:
                try:
                    watchdog.close()
                    blocked["watchdog_closed_before_callback_return"] = True
                except BaseException as error:
                    callback_errors.append(error)
                    blocked["watchdog_closed_before_callback_return"] = False
                stage["value"] = "resumed"

        supervisor = Thread(target=observe_block, name="g04-nonqt-supervisor")
        supervisor.start()
        try:
            QTimer.singleShot(0, blocked_callback)
            fixture.app.processEvents()
        finally:
            # Close is idempotent and also covers a callback that never starts.
            watchdog.close()
        supervisor.join(4)
        fixture.assertFalse(supervisor.is_alive())
        fixture.assertEqual(observation_errors, [])
        fixture.assertEqual(callback_errors, [])
        fixture.assertTrue(blocked["callback_entered"])
        fixture.assertTrue(blocked["watchdog_phase_armed"])
        fixture.assertTrue(blocked["watchdog_closed_before_callback_return"])
        fixture.assertTrue(blocked["release_received"])
        fixture.assertEqual(blocked["thread"], main_thread)
        fixture.assertGreaterEqual(blocked["elapsed"], .25)
        fixture.assertLessEqual(max(blocked["pending_samples"]), 2)
        fixture.assertEqual(blocked["delivered_after"], blocked["delivered_before"])
        fixture.assertGreater(blocked["superseded_after"], blocked["superseded_before"])
        fixture._wait(lambda: all(sum(event[:2] == ("resumed", pane_id)
                                     for event in render_events) >= 3 for pane_id in pane_ids))
        for index, pane_id in enumerate(pane_ids):
            events = [event for event in render_events if event[:2] == ("resumed", pane_id)]
            fixture.assertGreater(events[0][2], blocked["sequences"][index])
            fixture.assertEqual([event[2] for event in events], sorted(set(event[2] for event in events)))
            fixture.assertTrue(all(event[3] == main_thread for event in events))
        fixture.assertEqual(render_errors, [])
        fixture.assertEqual(surface.delivery.failed_panes(), ())

        peer_before = panes[1].last_bundle
        peer_state = handle.pump.snapshot()[1]
        peer_count = counts()[1]
        surface.board.select_slot(1)
        fixture.assertTrue(surface.stop_selected.isEnabled())
        surface.stop_selected.click()
        target_resource = handle.pump.snapshot()[0].physical_stream_resource_id
        fixture._wait(lambda: handle.pump.snapshot()[0].phase is PanePumpPhase.STOPPED
                      and not handle.ui_stop_pending(target_resource))
        fixture.assertEqual((engines[0].join_calls, engines[0].disconnect_calls), (1, 1))
        fixture.assertFalse(engines[0].thread.is_alive())
        fixture.assertEqual(handle.session.retained_resource_count, 1)
        fixture.assertEqual(handle.session._leases.active_resource_count, 1)
        fixture._wait(lambda: panes[1].last_bundle.spectrum.sequence > peer_before.spectrum.sequence
                      and counts()[1] > peer_count)
        peer_after = panes[1].last_bundle
        fixture.assertIs(handle.pump.snapshot()[1].phase, PanePumpPhase.RUNNING)
        fixture.assertEqual(handle.pump.snapshot()[1].activation, peer_state.activation)
        for name in ("source_id", "session_id", "receiver_id", "acquisition_epoch",
                     "config_generation", "clock_domain", "accumulation_id", "unit"):
            fixture.assertEqual(getattr(peer_before.identity, name), getattr(peer_after.identity, name))
        np.testing.assert_array_equal(peer_before.frequencies_hz, peer_after.frequencies_hz)
        fixture.assertIs(triples[1][1]._poller, pollers[1])
        fixture.assertTrue(pollers[1].is_alive() and engines[1].thread.is_alive())
        fixture.assertEqual(engines[1].disconnect_calls, 0)
        surface.stop_all.click()
        fixture._wait(handle.can_close)
        fixture.assertEqual(surface._ui_stop_handoffs, {})
        fixture.assertTrue(all(future.done() for future in surface._futures))
        fixture.assertEqual(handle.session._leases.active_resource_count, 0)
        result = {"scope": "originalQtV2/twoMOCKproducers/notRF-paint-performance-release",
                  "blocked_s": blocked["elapsed"], "pending_max": max(blocked["pending_samples"]),
                  "producer_deltas": [a-b for a,b in zip(blocked["produced_after"], blocked["produced"])],
                  "prepared_deltas": [a-b for a,b in zip(blocked["prepared_after"], blocked["prepared"])],
                  "superseded_delta": blocked["superseded_after"]-blocked["superseded_before"],
                  "watchdog_armed_inside_callback": blocked["watchdog_phase_armed"],
                  "watchdog_closed_before_callback_return": blocked["watchdog_closed_before_callback_return"],
                  "selected_stop_peer_continued": True}
    except BaseException as error:
        body_error = error
    finally:
        release.set()
        feed_stop.set()
        for thread in feeders + ([] if supervisor is None else [supervisor]):
            thread.join(5)
            if thread.is_alive():
                cleanup_errors.append(AssertionError(f"test thread did not join: {thread.name}"))
        if handle is not None and surface is not None:
            try:
                error = fixture._dispose(None, pool, handle, surface)
            except BaseException as error:
                cleanup_errors.append(error)
            else:
                if error is not None:
                    cleanup_errors.append(error)
        for native, service, graph in triples:
            try:
                graph.live.shutdown()
                fixture.assertIsNone(service._poller)
                fixture.assertIsNone(service._engine)
                for engine in native.engines:
                    fixture.assertFalse(engine.thread.is_alive())
                    fixture.assertEqual(engine.disconnect_calls, 1)
            except BaseException as error:
                cleanup_errors.append(error)
    if body_error is not None:
        for error in cleanup_errors:
            body_error.add_note(f"cleanup also failed: {error}")
        raise body_error
    fixture.assertEqual(cleanup_errors, [])
    fixture.assertEqual(feed_errors, [])
    result["cleanup_confirmed"] = True
    print("G04_ORIGINAL_COMPOSITION " + json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
