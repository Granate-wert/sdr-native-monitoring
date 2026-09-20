"""Synthetic RTBW -> normal V2 presenter/shared canvases, with live-source Stop.

No SDR, DSP/transport/native ingress, EXE, DWM or OS-input claim. Immutable
arrays are reused by a one-latest-slot fake source; persistence is disabled.
Unknown-clock timestamp_ns is a unique SYNTHETIC token, not a time estimate.
Age uses a separate host perf_counter witness. Waterfall identity is captured
only after an actual image upload, never from a newer hidden ring admission.
Two paints of the same token count once; both is same-token, not atomic display.
Control callbacks are Qt-timer delivered programmatic clicks, not OS events.
The optional fake driver delay keeps the producer active until worker Stop.
Real QEventLoop runs acquisition observation and owner close/deferred deletes.
Timeouts are GUI observations, not a hard watchdog for a blocked driver.
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


def main():
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
    args = parser.parse_args()
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

    age = PaintAgeTracker()
    beats = deque(maxlen=8192)
    paints = {name: deque(maxlen=8192) for name in ("spectrum", "waterfall")}
    callbacks = {name: deque(maxlen=8192) for name in ("page", "viewport")}
    keys, upload_tokens = {}, {}
    failures, stops, driver_entries = [], [], []
    gui = threading.get_ident()
    generated = page_changes = viewport_changes = 0
    producer = None
    halt = threading.Event()
    snapshot_lock = threading.RLock()
    original_upload = WaterfallPane._upload_tiles
    original_start_method, original_stop_method = _AtomicFakeLive.start, _AtomicFakeLive.stop
    control = {}

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
                    age.painted(name, key, ended)
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
        try:
            f.select_and_apply()
            config = replace(f.live.latest_snapshot().applied.applied, fft_size=args.bins,
                             persistence_enabled=False, persistence_mode="disabled")
            f.presenter.apply_configuration(config)
            f.wait(lambda: not f.composition.view_model.state.busy)
            f.shell.resize(1920, 1080)
            scene, waterfall = f.page.visualization.spectrum_scene, f.page.visualization.waterfall_pane
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
                    nonlocal generated
                    try:
                        while not halt.is_set():
                            generated += 1
                            frame = LiveSpectrumFrame(sequence=generated, timestamp_ns=generated,
                                source_id="fake-pluto-usb", config_generation=snapshot.generation,
                                center_frequency_hz=config.center_hz, sample_rate_hz=config.sample_rate_hz,
                                fft_size=args.bins, hop_size=args.bins, frequencies_hz=frequencies, values=values)
                            offered = replace(snapshot, sequence=generated, spectrum=frame)
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
                    threading.Event().wait(args.driver_stop_ms / 1000)
                    halt.set()
                    producer.join(timeout=2)
                    if producer.is_alive():
                        raise RuntimeError("synthetic producer failed to terminate")
                with snapshot_lock:
                    return original_stop_method(f.live)

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
                nonlocal page_changes
                began = perf_counter()
                f.shell.select_workspace("calibration" if f.page.visualization.isVisible() else "analyzer")
                callbacks["page"].append((perf_counter() - began) * 1000)
                page_changes += 1

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
            with patch.dict(control, start=start, stop=stop):
                for cycle in range(args.cycles):
                    f.shell.select_workspace("analyzer")
                    f.page.primary.click()
                    run_qt_until(lambda: checked(lambda: f.live.is_running()
                        and not f.composition.view_model.state.busy), 5)
                    count_before = generated
                    due = perf_counter() + args.seconds
                    intent = []
                    def request_stop():
                        entered = perf_counter()
                        if producer is None or not producer.is_alive() or generated <= count_before:
                            raise AssertionError("Stop was not tested under an active producer")
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
                    page_timer.stop()
                    view_timer.stop()
                    if len(driver_entries) != cycle + 1 or producer.is_alive():
                        raise AssertionError("control did not stop exactly one active producer")
                    worker_entry, off_gui = driver_entries[-1]
                    stops.append(dict(cycle=cycle, producer_active_at_intent=True,
                        generated_at_intent=intent[2], generated_at_idle=generated,
                        timer_lateness_ms=(intent[0] - due) * 1000,
                        click_return_ms=(intent[1] - intent[0]) * 1000,
                        intent_to_worker_ms=(worker_entry - intent[0]) * 1000,
                        intent_to_idle_ms=(idle - intent[0]) * 1000, worker_off_gui=off_gui,
                        heartbeat_ticks_during_driver_delay=sum(worker_entry < beat <
                            worker_entry + args.driver_stop_ms / 1000 for beat in beats)))
                    f.shell.select_workspace("analyzer")
                    run_qt_until(lambda: checked(lambda: scene.displayed_frame is not None), 2)
                if f.events != [event for _ in range(args.cycles) for event in ("rtbw-start", "rtbw-stop")]:
                    raise AssertionError(f.events)
            if age.missing or age.changed_during_paint or not all(age.counts.values()):
                raise AssertionError((age.missing, age.changed_during_paint, age.counts))
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
                    ("timer_lateness_ms", "click_return_ms", "intent_to_worker_ms", "intent_to_idle_ms")},
                navigation_call_ms={name: summary(data) for name, data in callbacks.items()},
                waterfall_rows=waterfall.history_rows, waterfall_metrics=asdict(waterfall.metrics),
                allocation_budget=asdict(f.composition.allocation_budget.snapshot()),
                display_metrics=asdict(f.presenter.display_metrics),
                preparation_superseded=f.presenter.preparation_superseded,
                preparation_stale=f.presenter.preparation_stale)
        finally:
            for t in timers:
                t.stop()
            halt.set()
            if producer is not None:
                producer.join(timeout=3)
            f.tearDown()
            f.doCleanups()
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
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
