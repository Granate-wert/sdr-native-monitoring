"""Synthetic RTBW -> normal V2 presenter/shared canvases, with live-source Stop.

No SDR, DSP/transport/native ingress, EXE, DWM or OS-input claim. Immutable
Spectrum arrays are reused by a one-latest-slot fake source. Optional persistence
allocates a fresh histogram at a declared cadence: up to four identical spectra
in a rolling window, not native DSP performance or persistence paint-FPS evidence.
Unknown-clock timestamp_ns is a unique SYNTHETIC token, not a time estimate.
Age uses a separate host perf_counter witness. Waterfall identity is captured
only after an actual image upload, never from a newer hidden ring admission.
Two paints of the same token count once; both is same-token, not atomic display.
Control callbacks are Qt-timer delivered programmatic clicks, not OS events.
The optional fake driver delay keeps the producer active until worker Stop.
Real QEventLoop runs acquisition observation and owner close/deferred deletes.
Timeouts are GUI observations, not a hard watchdog for a blocked driver.
Optional memory sampling perturbs timings; such runs are NOT latency baselines.
Phase labels describe paint-return context, not the cause of a delayed frame.
"""
import argparse
from collections import deque
from dataclasses import asdict, replace
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import weakref
from time import perf_counter
from unittest.mock import patch


def rtbw_key(timestamp_token, generation):
    """One fixed-source run: generation plus globally unique synthetic token."""
    return (int(timestamp_token), "rtbw", int(generation))


def uploaded_key(pane, uploads_before, previous):
    """Observe successful upload metadata; a hidden newer ring is NOT a paint."""
    if not any(item.isVisible() for item in pane.image_items):
        return None
    if pane.metrics.image_uploads == uploads_before:
        return previous
    timestamps = pane._renderer.timestamps_ns()
    signature = pane.grid_signature
    if not len(timestamps) or signature is None:
        raise AssertionError("uploaded image without row/grid witness")
    return rtbw_key(timestamps[-1], signature.configuration_generation)


def paint_phase(control_phase, visible, now, resumed_until):
    if control_phase != "running":
        return control_phase
    if not visible:
        return "hidden"
    return "resume" if now < resumed_until else "steady"


def future_phase(future):
    """Read-only instantaneous public Future state, not a worker lock/barrier."""
    if future is None:
        return "idle"
    if future.cancelled():
        return "cancelled-awaiting-gui"
    if future.done():
        return "done-awaiting-gui"
    return "running" if future.running() else "queued"


def stop_phase(presenter, projector):
    return dict(preparation=future_phase(presenter._preparation_future),
                projection=future_phase(projector._future),
                preparation_pending=presenter._pending_preparation is not None,
                projection_pending=projector._pending is not None)


def phase_matches(phase, requested):
    if requested == "any":
        return True
    if requested == "idle":
        return phase["preparation"] == phase["projection"] == "idle"
    owner, state = requested.split(":", 1)
    return phase[owner] == state


def synthetic_persistence(frame, power_bins, update_sequence, processed_frames):
    """Fresh immutable exact-window histogram of repeated synthetic spectra.

    No producer history is retained: the synthetic spectrum is constant and
    every occupied cell contains the number of repeated frames in this window.
    Domain imports occur only after the CLI has selected its isolated checkout.
    """
    import numpy as np
    from sdr_monitor.domain.live import LivePersistenceFrame
    if not 16 <= power_bins <= 128 or not 1 <= processed_frames <= 4:
        raise ValueError("synthetic histogram requires 16..128 power bins and 1..4 frames")
    if not np.all(np.isfinite(frame.values)) or np.any((frame.values < -140) | (frame.values >= 20)):
        raise ValueError("synthetic spectrum must be finite and inside histogram range")
    rows = ((frame.values + 140) * (power_bins / 160)).astype(np.intp)
    density = np.zeros((power_bins, frame.fft_size), dtype=np.float32)
    density[rows, np.arange(frame.fft_size)] = processed_frames
    density.setflags(write=False)
    return LivePersistenceFrame(update_sequence=update_sequence, timestamp_ns=frame.timestamp_ns,
        source_frame_sequence=frame.sequence, power_min_db=-140, power_max_db=20,
        power_bins=power_bins, frequency_bins=frame.fft_size, processed_frames=processed_frames,
        exponential_decay=False, frequencies_hz=frame.frequencies_hz, density=density,
        probability_scale=1 / processed_frames, count_scale=1,
        source_id=frame.source_id, config_generation=frame.config_generation,
        timestamp_quality=frame.timestamp_quality, unit=frame.unit, producer_identity_available=True,
        receiver_id=frame.receiver_id, acquisition_epoch=frame.acquisition_epoch,
        clock_domain=frame.clock_domain, accumulation_id=frame.accumulation_id)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--cycles", type=int, default=5)
    parser.add_argument("--bins", type=int, default=65536)
    parser.add_argument("--source-hz", type=float, default=200)
    parser.add_argument("--page-seconds", type=float, default=0)
    parser.add_argument("--viewport-seconds", type=float, default=0)
    parser.add_argument("--driver-stop-ms", type=float, default=0)
    parser.add_argument("--persistence-power-bins", type=int, default=0,
                        help="0 disables; 16..128 enables fresh synthetic rolling-four-frame histograms")
    parser.add_argument("--persistence-every", type=int, default=10,
                        help="Histogram at first spectrum after Start, then every N source publications")
    parser.add_argument("--stop-phase", default="any", choices=("any", "idle",
        "preparation:running", "preparation:done-awaiting-gui",
        "projection:running", "projection:done-awaiting-gui"),
        help="After due time observe up to 2s for this Future phase; producer stays active. Not a worker barrier.")
    parser.add_argument("--memory-seconds", type=float, default=0,
                        help="0 disables; 0.25..60 second scalar inventory/process samples (perturbs timing)")
    parser.add_argument("--collect-after-context", action="store_true",
                        help="Diagnostic GC only AFTER normal post-context sample; requires memory mode")
    args = parser.parse_args(argv)
    if not sys.flags.isolated or not 1 <= args.seconds <= 1200 or not 1 <= args.cycles <= 100:
        parser.error("Python -I, 1..1200 seconds and 1..100 cycles required")
    if not 256 <= args.bins <= 262144 or args.bins & (args.bins - 1):
        parser.error("bins must be a supported power of two in 256..262144")
    if not 1 <= args.source_hz <= 2000 or not 0 <= args.driver_stop_ms <= 2000:
        parser.error("source-hz 1..2000; driver-stop-ms 0..2000")
    if any(value != 0 and not .1 <= value <= 60 for value in (args.page_seconds, args.viewport_seconds)):
        parser.error("churn intervals must be zero or 0.1..60 seconds")
    if args.output.exists():
        parser.error("output must be new")
    if args.memory_seconds != 0 and not .25 <= args.memory_seconds <= 60:
        parser.error("memory-seconds must be zero or 0.25..60")
    if args.collect_after_context and not args.memory_seconds:
        parser.error("collect-after-context requires memory sampling")
    if args.persistence_power_bins != 0 and not 16 <= args.persistence_power_bins <= 128:
        parser.error("persistence-power-bins must be 0 or 16..128")
    if not 1 <= args.persistence_every <= 1000 or args.bins * args.persistence_power_bins * 4 > 64 * 1024**2:
        parser.error("persistence-every 1..1000; histogram must not exceed 64MiB")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    import PySide6
    import pyqtgraph as pg
    from PySide6.QtCore import QTimer, Qt
    from sdr_monitor.domain.live import LiveSpectrumFrame
    from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
    from scripts.benchmark_app04_poll_overload import PaintAgeTracker, run_qt_until
    from tests.test_app01_product_analyzer import _AtomicFakeLive
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests

    def summary(values):
        return None if not len(values) else dict(zip(("p50", "p95", "p99", "max"),
            map(float, (*np.percentile(values, [50, 95, 99]), max(values)))))

    # Memory runs need only short timing context; do not warm tens of thousands
    # of diagnostic float samples while attributing process memory growth.
    timing_capacity = 512 if args.memory_seconds else 8192
    age = PaintAgeTracker(timing_capacity)
    beats = deque(maxlen=timing_capacity)
    paints = {name: deque(maxlen=timing_capacity) for name in ("spectrum", "waterfall")}
    callbacks = {name: deque(maxlen=timing_capacity) for name in ("page", "viewport")}
    keys, upload_tokens = {}, {}
    failures, stops, driver_entries, driver_returns = [], [], [], []
    gui = threading.get_ident()
    generated = page_changes = viewport_changes = 0
    persistence_generated = persistence_accepted = last_persistence_accepted = 0
    producer = None
    halt = threading.Event()
    snapshot_lock = threading.RLock()
    original_upload = WaterfallPane._upload_tiles
    original_start_method, original_stop_method = _AtomicFakeLive.start, _AtomicFakeLive.stop
    control = {}
    control_phase, resumed_until = "idle", 0.0
    phase_ages = {phase: {name: deque(maxlen=timing_capacity) for name in age.ages}
                  for phase in ("starting", "stopping", "idle", "hidden", "resume", "steady")}
    phase_counts = {phase: dict.fromkeys(age.ages, 0) for phase in phase_ages}
    memory_rows = deque(maxlen=2048)
    memory_total = 0
    if args.memory_seconds:
        from tests.ui_v2.run_app05_memory_inventory import process_memory, qt_wrapper_counts

    def dispatch_start(service):
        return control["start"]() if "start" in control else original_start_method(service)

    def dispatch_stop(service):
        return control["stop"]() if "stop" in control else original_stop_method(service)

    def upload(pane):
        before = pane.metrics.image_uploads
        original_upload(pane)
        upload_tokens[id(pane)] = uploaded_key(pane, before, upload_tokens.get(id(pane)))

    class MeasuredGraphics(pg.GraphicsLayoutWidget):
        def paintEvent(self, event):
            tracked = keys.get(id(self))
            key = None if tracked is None else tracked[1]()
            began = perf_counter()
            super().paintEvent(event)
            ended = perf_counter()
            if tracked is not None:
                name, current = tracked
                paints[name].append((ended - began) * 1000)
                if key == current():
                    counts_before = dict(age.counts)
                    age.painted(name, key, ended)
                    phase = paint_phase(control_phase, self.isVisible(), ended, resumed_until)
                    for target in age.counts:
                        if age.counts[target] != counts_before[target]:
                            phase_ages[phase][target].append(age.ages[target][-1])
                            phase_counts[phase][target] += 1
                else:
                    age.changed_during_paint += 1

    with patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics), \
         patch.object(WaterfallPane, "_upload_tiles", upload), \
         patch.object(_AtomicFakeLive, "start", dispatch_start), \
         patch.object(_AtomicFakeLive, "stop", dispatch_stop):
        f = AnalyzerWorkspaceProductTests("runTest")
        f.setUpClass()
        # Use real nested loops also for fixture lifecycle, not manual pumping.
        f.wait = lambda predicate: run_qt_until(predicate, 5)
        f.setUp()
        timers = []
        def unsubscribe_density():
            pass
        try:
            f.select_and_apply()
            persistence_enabled = args.persistence_power_bins != 0
            config = replace(f.live.latest_snapshot().applied.applied, fft_size=args.bins,
                             persistence_enabled=persistence_enabled,
                             persistence_mode="rolling-exact" if persistence_enabled else "disabled",
                             persistence_power_bins=args.persistence_power_bins or 256,
                             persistence_window_frames=4)
            f.presenter.apply_configuration(config)
            f.wait(lambda: not f.composition.view_model.state.busy)
            f.shell.resize(1920, 1080)
            scene, waterfall = f.page.visualization.spectrum_scene, f.page.visualization.waterfall_pane

            def observe_density(state):
                nonlocal persistence_accepted, last_persistence_accepted
                bundle = state.analyzer_bundle
                raw = None if bundle is None else bundle.persistence
                density = state.persistence_frame
                if raw is None or density is None:
                    return
                if (raw.source_frame_sequence > bundle.spectrum.sequence
                        or density.density.shape != (args.persistence_power_bins, args.bins)
                        or density.level_unit != bundle.unit or bundle.coherence_issues):
                    failures.append("incoherent synthetic persistence accepted")
                    return
                if raw.update_sequence != last_persistence_accepted:
                    persistence_accepted += 1
                    last_persistence_accepted = int(raw.update_sequence)

            if persistence_enabled:
                unsubscribe_density = f.composition.view_model.subscribe(observe_density)
            frequencies = config.center_hz + (np.arange(args.bins) - args.bins / 2) * config.sample_rate_hz / args.bins
            values = np.full(args.bins, -80, np.float32)
            values[args.bins // 2 + 17] = -20
            frequencies.setflags(write=False)
            values.setflags(write=False)

            def start():
                nonlocal producer
                snapshot = original_start_method(f.live)
                snapshot = replace(snapshot, spectrum=None, persistence=None)
                with snapshot_lock:
                    f.live._snapshot = snapshot
                halt.clear()
                def produce():
                    nonlocal generated, persistence_generated
                    cycle_frames = 0
                    density = None
                    try:
                        while not halt.is_set():
                            generated += 1
                            cycle_frames += 1
                            frame = LiveSpectrumFrame(sequence=generated, timestamp_ns=generated,
                                source_id="fake-pluto-usb", config_generation=snapshot.generation,
                                center_frequency_hz=config.center_hz, sample_rate_hz=config.sample_rate_hz,
                                fft_size=args.bins, hop_size=args.bins, frequencies_hz=frequencies, values=values)
                            if persistence_enabled and (cycle_frames == 1 or cycle_frames % args.persistence_every == 0):
                                persistence_generated += 1
                                density = synthetic_persistence(frame, args.persistence_power_bins,
                                    persistence_generated, min(cycle_frames, 4))
                            offered = replace(snapshot, sequence=generated, spectrum=frame, persistence=density)
                            with snapshot_lock:
                                age.publish(rtbw_key(frame.timestamp_ns, frame.config_generation), perf_counter())
                                f.live._snapshot = offered
                            halt.wait(1 / args.source_hz)
                    except Exception as error:
                        failures.append(repr(error))
                producer = threading.Thread(target=produce, name="synthetic-rtbw-publications")
                producer.start()
                return snapshot

            def stop():
                active = producer is not None and producer.is_alive()
                if active:
                    driver_entries.append((perf_counter(), threading.get_ident() != gui))
                    # Simulated blocking driver, on the existing control worker.
                    # Windows timed waits may return slightly before their
                    # requested interval. Record/enforce an actual host deadline
                    # without a sub-ms busy spin; not a real driver guarantee.
                    driver_deadline = perf_counter() + args.driver_stop_ms / 1000
                    delay = threading.Event()
                    while (remaining := driver_deadline - perf_counter()) > 0:
                        delay.wait(max(.001, remaining))
                    halt.set()
                    producer.join(timeout=2)
                    if producer.is_alive():
                        raise RuntimeError("synthetic producer failed to terminate")
                with snapshot_lock:
                    result = original_stop_method(f.live)
                if active:
                    driver_returns.append(perf_counter())
                return result

            def spectrum_key():
                bundle = scene.displayed_frame
                if bundle is None:
                    return None
                frame = bundle.spectrum
                if frame.source_id != "fake-pluto-usb":
                    raise AssertionError("unexpected source on measured canvas")
                return rtbw_key(frame.timestamp_ns, frame.config_generation)

            def waterfall_key():
                if not any(item.isVisible() for item in waterfall.image_items):
                    return None
                return upload_tokens.get(id(waterfall))

            keys[id(scene._graphics)] = ("spectrum", spectrum_key)
            keys[id(waterfall._graphics)] = ("waterfall", waterfall_key)
            f.presenter.task_failed.connect(failures.append)

            def checked(predicate):
                if failures or f._qt_errors:
                    raise AssertionError((failures, f._qt_errors))
                return predicate()

            def guard(callback):
                @wraps(callback)
                def call():
                    try:
                        callback()
                    except Exception as error:
                        failures.append(repr(error))
                return call

            def timer(interval, callback, *, once=False):
                t = QTimer()
                t.setTimerType(Qt.TimerType.PreciseTimer)
                t.setSingleShot(once)
                t.setInterval(max(1, round(interval * 1000)))
                t.timeout.connect(guard(callback))
                timers.append(t)
                return t

            def cycle_page():
                nonlocal page_changes, resumed_until
                began = perf_counter()
                f.shell.select_workspace("calibration" if f.page.visualization.isVisible() else "analyzer")
                callbacks["page"].append((perf_counter() - began) * 1000)
                page_changes += 1
                if f.page.visualization.isVisible():
                    resumed_until = perf_counter() + .25

            def cycle_viewport():
                nonlocal viewport_changes
                began = perf_counter()
                span = config.sample_rate_hz * (.25 if viewport_changes % 2 else .5)
                offset = config.sample_rate_hz * (.1 if viewport_changes % 2 else 0)
                scene.view_box.setXRange(config.center_hz + offset - span,
                                        config.center_hz + offset + span, padding=0)
                callbacks["viewport"].append((perf_counter() - began) * 1000)
                viewport_changes += 1

            heartbeat = timer(.01, lambda: beats.append(perf_counter()))
            page_timer = timer(args.page_seconds, cycle_page)
            view_timer = timer(args.viewport_seconds, cycle_viewport)
            heartbeat.start()
            began = perf_counter()

            def sample_memory(label="timer", *, workspace=True):
                nonlocal memory_total
                sampled = perf_counter()
                inventory = f.composition.memory_snapshot(f.page if workspace else None)
                row = dict(index=memory_total, seconds=sampled - began, label=label,
                    page=f.shell.active_workspace_id if workspace else "closed-context-alive",
                    control_phase=control_phase, **process_memory(), inventory=asdict(inventory))
                row["sample_ms"] = (perf_counter() - sampled) * 1000
                memory_rows.append(row)
                memory_total += 1

            if args.memory_seconds:
                sample_memory("before-start")
                census_before = qt_wrapper_counts()
                memory_timer = timer(args.memory_seconds, sample_memory)
                memory_timer.start()
            with patch.dict(control, start=start, stop=stop):
                for cycle in range(args.cycles):
                    f.shell.select_workspace("analyzer")
                    control_phase = "starting"
                    f.page.primary.click()
                    run_qt_until(lambda: checked(lambda: f.live.is_running()
                        and not f.composition.view_model.state.busy), 5)
                    control_phase = "running"
                    resumed_until = perf_counter() + .25
                    count_before = generated
                    due = perf_counter() + args.seconds
                    intent = []
                    intent_phase = {}
                    phase_observations = 0
                    def request_stop():
                        nonlocal control_phase, phase_observations
                        entered = perf_counter()
                        if producer is None or not producer.is_alive() or generated <= count_before:
                            raise AssertionError("Stop was not tested under an active producer")
                        phase = stop_phase(f.presenter, f.composition.spectrum_projector)
                        phase_observations += 1
                        if not phase_matches(phase, args.stop_phase):
                            if entered - due > 2:
                                raise AssertionError(f"Requested Stop phase not observed: {args.stop_phase}; last={phase}")
                            stop_timer.start(1)
                            return
                        intent_phase.update(phase)
                        control_phase = "stopping"
                        f.page.primary.click()
                        intent.extend((entered, perf_counter(), generated))
                    stop_timer = timer(args.seconds, request_stop, once=True)
                    if args.page_seconds:
                        page_timer.start()
                    if args.viewport_seconds:
                        view_timer.start()
                    stop_timer.start()
                    run_qt_until(lambda: checked(lambda: bool(intent) and not f.live.is_running()
                        and not f.composition.view_model.state.busy), args.seconds + 5)
                    idle = perf_counter()
                    control_phase = "idle"
                    page_timer.stop()
                    view_timer.stop()
                    if len(driver_entries) != cycle + 1 or len(driver_returns) != cycle + 1 or producer.is_alive():
                        raise AssertionError("control did not stop exactly one active producer")
                    worker_entry, off_gui = driver_entries[-1]
                    stops.append(dict(cycle=cycle, producer_active_at_intent=True,
                        phase_at_intent=intent_phase, phase_observations=phase_observations,
                        generated_at_intent=intent[2], generated_at_idle=generated,
                        timer_lateness_ms=(intent[0] - due) * 1000,
                        click_return_ms=(intent[1] - intent[0]) * 1000,
                        intent_to_worker_ms=(worker_entry - intent[0]) * 1000,
                        driver_elapsed_ms=(driver_returns[-1] - worker_entry) * 1000,
                        driver_return_to_idle_ms=(idle - driver_returns[-1]) * 1000,
                        intent_to_idle_ms=(idle - intent[0]) * 1000, worker_off_gui=off_gui,
                        heartbeat_ticks_during_driver_delay=sum(worker_entry < beat <
                            worker_entry + args.driver_stop_ms / 1000 for beat in beats)))
                    f.shell.select_workspace("analyzer")
                    run_qt_until(lambda: checked(lambda: scene.displayed_frame is not None), 2)
                    if args.memory_seconds:
                        sample_memory("cycle-idle")
                if f.events != [event for _ in range(args.cycles) for event in ("rtbw-start", "rtbw-stop")]:
                    raise AssertionError(f.events)
            if age.missing or age.changed_during_paint or not all(age.counts.values()):
                raise AssertionError((age.missing, age.changed_during_paint, age.counts))
            if persistence_enabled and (not persistence_accepted or not scene._persistence.metrics.image_uploads):
                raise AssertionError("enabled persistence was not accepted and uploaded")
            report = dict(scope=__doc__, seconds=perf_counter() - began, bins=args.bins,
                cycles=args.cycles, requested_source_hz=args.source_hz, generated=generated,
                page_changes=page_changes, viewport_changes=viewport_changes,
                driver_stop_ms=args.driver_stop_ms, heartbeat_ms=summary(np.diff(beats) * 1000),
                cpu_paint_ms={name: summary(data) for name, data in paints.items()},
                host_publication_to_first_paint_ms={name: summary(data) for name, data in age.ages.items()},
                first_paint_publications=age.counts, repeated_paints=age.repeated,
                witness_misses=age.missing, witness_evictions=age.evicted,
                changed_during_paint=age.changed_during_paint,
                controls=stops, control_distributions={name: summary([s[name] for s in stops]) for name in
                    ("timer_lateness_ms", "click_return_ms", "intent_to_worker_ms", "driver_elapsed_ms",
                     "driver_return_to_idle_ms", "intent_to_idle_ms")},
                navigation_call_ms={name: summary(data) for name, data in callbacks.items()},
                waterfall_rows=waterfall.history_rows, waterfall_metrics=asdict(waterfall.metrics),
                allocation_budget=asdict(f.composition.allocation_budget.snapshot()),
                display_metrics=asdict(f.presenter.display_metrics),
                preparation_superseded=f.presenter.preparation_superseded,
                preparation_stale=f.presenter.preparation_stale)
            report["persistence"] = dict(enabled=persistence_enabled,
                power_bins=args.persistence_power_bins, every_source_frames=args.persistence_every,
                generated=persistence_generated, accepted=persistence_accepted,
                last_accepted_update=last_persistence_accepted,
                overlay_metrics=asdict(scene._persistence.metrics),
                scope="Synthetic rolling histogram of up to four identical spectra, fresh immutable array per update. Accepted and ImageItem uploads counted, not persistence paint/RF/native DSP FPS.")
            report["requested_stop_phase"] = args.stop_phase
            report["stop_phase_scope"] = "Instantaneous GUI-side Future observation before click; worker may finish before click. Phase wait is in timer_lateness, not intent_to_idle. No worker barrier/driver deadline."
            phase_names = sorted({(s["phase_at_intent"]["preparation"], s["phase_at_intent"]["projection"])
                                  for s in stops})
            report["control_by_phase"] = {
                f"preparation={a},projection={b}": dict(count=len(rows),
                    **{name: summary([s[name] for s in rows]) for name in
                       ("intent_to_worker_ms", "driver_elapsed_ms", "driver_return_to_idle_ms", "intent_to_idle_ms")})
                for a, b in phase_names
                if (rows := [s for s in stops if s["phase_at_intent"]["preparation"] == a
                             and s["phase_at_intent"]["projection"] == b])}
            report["paint_return_phases"] = {phase: dict(counts=phase_counts[phase],
                age_ms={name: summary(data) for name, data in samples.items()})
                for phase, samples in phase_ages.items()}
            report["timing_sample_capacity"] = timing_capacity
            report["phase_scope"] = "Paint-return context; resume is first 250ms after show/Start ack, not causal attribution; bounded last samples per phase/canvas"
            if args.memory_seconds:
                memory_timer.stop()
                sample_memory("before-close")
                census_before_close = qt_wrapper_counts()
        finally:
            unsubscribe_density()
            for t in timers:
                t.stop()
                t.timeout.disconnect()
                t.deleteLater()
            halt.set()
            if producer is not None:
                producer.join(timeout=3)
            f.tearDown()
            f.doCleanups()
            # PySide can keep the dynamically registered observer subclass
            # alive. Its paint closure must not root the fixture/scene after
            # close through callbacks in this observer-owned lookup table.
            keys.clear()
            upload_tokens.clear()
    report["post_close_allocation_budget"] = asdict(f.composition.allocation_budget.snapshot())
    if report["post_close_allocation_budget"]["reserved_bytes"]:
        raise AssertionError("presentation reservation survived owner cleanup")
    if args.memory_seconds:
        sample_memory("after-close", workspace=False)
        report["memory"] = dict(interval_seconds=args.memory_seconds, capacity=2048,
            collect_after_context=args.collect_after_context,
            total_samples=memory_total, samples=list(memory_rows),
            qt_census_before=census_before, qt_census_before_close=census_before_close,
            qt_census_after_close=qt_wrapper_counts(),
            scope="Instrumented run, NOT timing baseline. Owner bytes overlap. Post-close fixture/scene/source locals still alive; omitted workspace is not freed-memory proof.")
    outside = [n for n, m in tuple(sys.modules.items()) if n.startswith(("sdr_monitor", "tests", "scripts"))
               and getattr(m, "__file__", None) and not Path(m.__file__).resolve().is_relative_to(root)]
    workers = [t.name for t in threading.enumerate() if any(s in t.name.lower() for s in ("sdr", "synthetic"))]
    if outside or workers:
        raise AssertionError((outside, workers))
    report.update(checkout_head=subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        shared_observer_sha256=hashlib.sha256((root / "scripts/benchmark_app04_poll_overload.py").read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__, pyside=PySide6.__version__, pyqtgraph=pg.__version__,
        host_platform=platform.platform(), processor=platform.processor(), platform="Qt offscreen",
        logical_size=[1920, 1080], event_pump="QEventLoop.exec", product_imports_outside_checkout=outside,
        remaining_workers=workers)
    # The ledger holds weak roots only. Returning it lets the CLI sample after
    # this function's fixture/scene/producer closure references leave scope.
    owners = {name: weakref.ref(value) for name, value in (
        ("fixture", f), ("composition", f.composition), ("presenter", f.presenter),
        ("scene", scene), ("waterfall", waterfall))}
    return report, args.output, f.composition.allocation_budget, owners


if __name__ == "__main__":
    result, output, budget, owners = main()
    if "memory" in result:
        from scripts.benchmark_app04_poll_overload import run_qt_until
        from tests.ui_v2.run_app05_memory_inventory import process_memory, qt_wrapper_counts
        until = perf_counter() + .5
        run_qt_until(lambda: perf_counter() >= until, 2)
        result["memory"]["after_context_return"] = dict(
            process=process_memory(), allocation_budget=asdict(budget.snapshot()),
            weak_owner_alive={name: reference() is not None for name, reference in owners.items()},
            qt_census=qt_wrapper_counts(), scope="After benchmark locals return and 500ms real Qt loop; normal GC, no forced collection or product state clearing")
        if result["memory"]["collect_after_context"]:
            import gc
            collected = gc.collect()
            until = perf_counter() + .5
            run_qt_until(lambda: perf_counter() >= until, 2)
            result["memory"]["diagnostic_after_collection"] = dict(collected=collected,
                process=process_memory(), allocation_budget=asdict(budget.snapshot()),
                weak_owner_alive={name: reference() is not None for name, reference in owners.items()},
                qt_census=qt_wrapper_counts(), scope="Explicit diagnostic GC AFTER unmodified normal samples; not product behavior, release deadline or leak fix")
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
