"""Synthetic fast source -> real Sweep domain adapter/presenter -> actual V2.

Includes worker drain/conversion, Qt delivery, canvas and queued Stop under load.
Not native FFT throughput, RF, EXE, DWM, OS input, persistence or Windows DPI.
The fake coordinator owns one preview and four terminals; immutable array
templates are reused (producer allocation and DSP costs are NOT measured).
Optional page cycling exercises hidden delivery/history without stopping the
source. Timing samples are bounded to the last 8192 observations; optional
tracemalloc observes Python-traced allocations, NOT total/native/GPU RSS, and
its instrumentation overhead means that run is not a latency baseline.
service_return_to_snapshot_subscriber_ms starts when the domain service returns
and ends after earlier synchronous V2 snapshot subscribers have run. It includes
optional worker presentation preparation, Qt queueing and GUI delivery/render
preparation; it is not pure worker-to-GUI scheduling latency.
The measured interval uses QEventLoop.exec, not a processEvents-only loop.
Age witnesses use the synthetic publisher's host monotonic clock and exact
pass/state/revision. They exclude RF/transport/DSP age and DWM presentation.
Waterfall age is for the newest uploaded row; older row corrections are not
counted. First-paint distributions exclude repeated paints of the same key.
"""
import argparse
from collections import deque, OrderedDict
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import tracemalloc
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch


def run_qt_until(predicate, timeout_seconds):
    """Deliver real Qt deferred lifecycle events; timeout is checked on GUI.

    A blocked GUI can delay this timeout: it is not a hard driver watchdog.
    Callback exceptions are re-raised outside Qt instead of being swallowed.
    """
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    loop = QEventLoop()
    check, deadline = QTimer(loop), QTimer(loop)
    check.setTimerType(Qt.TimerType.PreciseTimer)
    check.setInterval(5)
    deadline.setSingleShot(True)
    failure = []

    def poll():
        try:
            if predicate():
                loop.quit()
        except Exception as error:
            failure.append(error)
            loop.quit()

    def expired():
        failure.append(TimeoutError("Qt observation deadline exceeded"))
        loop.quit()

    check.timeout.connect(poll)
    deadline.timeout.connect(expired)
    check.start()
    deadline.start(max(1, round(timeout_seconds * 1000)))
    try:
        loop.exec()
    finally:
        check.stop()
        deadline.stop()
    if failure:
        raise failure[0]


class PaintAgeTracker:
    """Bounded scalar-only host publication -> first paint-return witness.

    Keys distinguish partial revision from terminal publication of the SAME
    pass. No RF timestamp inference; both means the SAME key painted in each
    canvas, not simultaneous paints or an atomic screen presentation.
    """
    def __init__(self, capacity=8192):
        if capacity < 1:
            raise ValueError("positive witness capacity required")
        self.capacity = capacity
        self.lock = threading.Lock()
        self.sources = OrderedDict()
        self.ages = {name: deque(maxlen=capacity) for name in ("spectrum", "waterfall", "both")}
        self.counts = {name: 0 for name in self.ages}
        self.partial_counts = {name: 0 for name in self.ages}
        self.missing = self.repeated = self.evicted = self.changed_during_paint = 0

    def publish(self, key, when):
        with self.lock:
            if key in self.sources:
                raise ValueError("duplicate publication identity")
            self.sources[key] = (when, set())
            if len(self.sources) > self.capacity:
                self.sources.popitem(last=False)
                self.evicted += 1

    def painted(self, pane, key, when):
        if key is None:
            return
        if pane not in ("spectrum", "waterfall"):
            raise ValueError("unknown canvas")
        with self.lock:
            source = self.sources.get(key)
            if source is None:
                self.missing += 1
                return
            began, seen = source
            if when < began:
                raise ValueError("paint precedes publication")
            if pane in seen:
                self.repeated += 1
                return
            seen.add(pane)
            targets = (pane, "both") if len(seen) == 2 else (pane,)
            for target in targets:
                self.ages[target].append((when - began) * 1000)
                self.counts[target] += 1
                self.partial_counts[target] += key[1] == "partial"


def sweep_key(frame):
    """Only actual domain metadata, never the latest offered frame's identity."""
    if frame is None:
        return None
    if hasattr(frame, "revision"):
        return (frame.sequence, "partial", frame.revision)
    return (frame.sequence, frame.state.value, 0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--bins", type=int, nargs="+", default=[65536, 262144, 2000000])
    parser.add_argument("--page-cycle-seconds", type=float, default=0)
    parser.add_argument("--trace-memory", action="store_true")
    args = parser.parse_args()
    if not sys.flags.isolated or not 1 <= args.seconds <= 1200:
        parser.error("use Python -I and 1..1200 seconds per grid")
    if args.page_cycle_seconds != 0 and not 1 <= args.page_cycle_seconds <= 60:
        parser.error("page cycle must be zero (off) or 1..60 seconds")
    if any(not 256 <= n <= 2000000 for n in args.bins):
        parser.error("grid must have 256..2000000 bins")
    if args.output.exists():
        parser.error("output exists")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    import PySide6
    import pyqtgraph as pg
    from PySide6.QtCore import QTimer, Qt
    from sdr_monitor.services.native_continuous_sweep import NativeContinuousSweepDisplayService
    from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
    from tests.test_app01_product_analyzer import _FakeAnalyzerDisplay
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests

    def summary(values):
        if not values:
            raise AssertionError("no timing samples")
        return dict(zip(("p50", "p95", "p99", "max"), map(float,
                    (*np.percentile(values, [50, 95, 99]), max(values)))))

    rows = []
    for count in args.bins:
        arrays = []
        freq = np.linspace(100e6, 200e6, count)
        for partial in (True, False):
            filled = count // 2 if partial else count
            values = np.full(count, -80., np.float32)
            values[count // 4] = -25
            values[filled:] = np.nan
            quality = np.zeros(count, np.uint32)
            quality[filled:] = 4096
            owners = np.zeros(count, np.int32)
            owners[count // 2:] = -1 if partial else 1
            for array in (freq, values, quality, owners):
                array.setflags(write=False)
            arrays.append((values, quality, owners))

        class Producer:
            def __init__(self):
                self.lock = threading.Lock()
                self.done = threading.Event()
                self.preview = None
                self.lines = deque(maxlen=4)
                self.seq = self.completed = self.dropped = self.preview_dropped = 0
                self.high_water = self.control_gaps = 0
                self.thread = None

            def frame(self, partial, *, gap=False):
                key = (self.seq, "gap" if gap else "partial" if partial else "complete",
                       1 if partial and not gap else 0)
                age_tracker.publish(key, perf_counter())
                values, quality, owners = arrays[int(not partial)]
                return SimpleNamespace(source_id="synthetic-overload", epoch=count,
                    line_sequence=self.seq, revision=1, unit="dBFS/bin", frequencies_hz=freq,
                    values=values, quality_flags_per_bin=quality, source_segment_indices=owners,
                    acquired_segment_generations=((0, 1),), pending_segment_indices=(1,),
                    state="gap" if gap else "complete", completed_ns=0,
                    missing_segment_indices=(1,) if gap else (),
                    segment_config_generations=((0, 1),) if gap else ((0, 1), (1, 2)),
                    gap_reasons=("cancellation",) if gap else ())

            def append(self, frame):
                self.dropped += len(self.lines) == self.lines.maxlen
                self.lines.append(frame)
                self.high_water = max(self.high_water, len(self.lines))

            def configure(self, _config):
                pass

            def start(self):
                def produce():
                    while not self.done.wait(.002):
                        with self.lock:
                            self.seq += 1
                            self.preview_dropped += self.preview is not None
                            self.preview = self.frame(True)
                        if self.done.wait(.002):
                            break
                        with self.lock:
                            self.append(self.frame(False))
                            self.completed += 1
                self.thread = threading.Thread(target=produce, name="synthetic-sweep-publications")
                self.thread.start()

            def poll_lines(self):
                with self.lock:
                    result = tuple(self.lines)
                    self.lines.clear()
                    return result

            def poll_progress(self):
                with self.lock:
                    result, self.preview = self.preview, None
                    return result

            def metrics(self):
                with self.lock:
                    return SimpleNamespace(completed_lines=self.completed,
                        output_snapshots_superseded=self.dropped, terminal_control_gaps=self.control_gaps,
                        output_queue=SimpleNamespace(depth=len(self.lines), capacity=4))

            def stop(self):
                self.done.set()
                if self.thread is not None:
                    self.thread.join(timeout=2)
                    if self.thread.is_alive():
                        raise RuntimeError("synthetic producer failed to stop")
                with self.lock:
                    self.seq += 1
                    self.append(self.frame(True, gap=True))
                    self.preview = None
                    self.control_gaps += 1

            def disconnect(self):
                pass

        age_tracker = PaintAgeTracker()
        producer = Producer()
        service = NativeContinuousSweepDisplayService(
            SimpleNamespace(NativeContinuousSweepCoordinator=lambda *_: producer), "usb:fake")
        config = SimpleNamespace(epoch=count, segments=(SimpleNamespace(
            fixed_band=SimpleNamespace(device=SimpleNamespace(source_id="synthetic-overload"))),))
        polls, beats, paints, publications = (deque(maxlen=8192) for _ in range(4))
        stop_times = []
        memory_samples = []
        page_switches = []
        poll_ends = {}
        gui_thread = threading.get_ident()
        canvas_keys = {}
        canvas_paints = {name: deque(maxlen=8192) for name in ("spectrum", "waterfall")}

        class MeasuredGraphics(pg.GraphicsLayoutWidget):
            def paintEvent(self, event):
                tracked = canvas_keys.get(id(self))
                key = None if tracked is None else tracked[1]()
                began = perf_counter()
                super().paintEvent(event)
                ended = perf_counter()
                paints.append((ended - began) * 1000)
                if tracked is not None:
                    pane, current_key = tracked
                    canvas_paints[pane].append((ended - began) * 1000)
                    if key == current_key():
                        age_tracker.painted(pane, key, ended)
                    else:
                        age_tracker.changed_during_paint += 1

        def start(display, _request):
            display.events.append("sweep-start")
            service.start(config)

        def stop(display):
            display.events.append("sweep-stop")
            service.stop()

        def poll(_display):
            began = perf_counter()
            snapshot = service.poll_latest()
            ended = perf_counter()
            polls.append(((ended-began)*1000, threading.get_ident() != gui_thread))
            poll_ends[id(snapshot)] = ended
            return snapshot

        with patch.object(_FakeAnalyzerDisplay, "start", start), \
             patch.object(_FakeAnalyzerDisplay, "stop", stop), \
             patch.object(_FakeAnalyzerDisplay, "poll_latest", poll), \
             patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics):
            harness = AnalyzerWorkspaceProductTests("runTest")
            harness.setUpClass()
            harness.setUp()
            heartbeat = QTimer()
            heartbeat.setTimerType(Qt.TimerType.PreciseTimer)
            heartbeat.setInterval(10)
            heartbeat.timeout.connect(lambda: beats.append(perf_counter()))
            stop_timer = QTimer()
            stop_timer.setTimerType(Qt.TimerType.PreciseTimer)
            stop_timer.setSingleShot(True)
            cycle_timer = QTimer()
            memory_timer = QTimer()
            try:
                harness.select_and_apply()
                harness.shell.resize(1920, 1080)
                page = harness.page
                page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
                presenter = harness.composition.analyzer_presenter
                scene = page.visualization.spectrum_scene
                waterfall = page.visualization.waterfall_pane

                def spectrum_key():
                    bundle = scene.displayed_frame
                    return sweep_key(None if bundle is None else bundle.spectrum)

                def waterfall_key():
                    # Metadata installed alongside the uploaded image, not a
                    # newer producer frame or the hidden page's retained ring.
                    if not any(item.isVisible() for item in waterfall._image_items):
                        return None
                    stamps = waterfall._time_axis._sweep_stamps
                    stamp = stamps[-1] if stamps else None
                    return None if stamp is None else (stamp.sequence, stamp.state.value,
                        stamp.revision if stamp.state.value == "partial" else 0)

                canvas_keys[id(scene._graphics)] = ("spectrum", spectrum_key)
                canvas_keys[id(waterfall._graphics)] = ("waterfall", waterfall_key)
                errors = []
                presenter.task_failed.connect(errors.append)

                def delivered(snapshot):
                    ended = poll_ends.pop(id(snapshot))
                    publications.append((perf_counter()-ended)*1000)

                presenter.snapshot_ready.connect(delivered)
                def cycle_page():
                    hidden = page.visualization.isVisible()
                    harness.shell.select_workspace("calibration" if hidden else "analyzer")
                    page_switches.append(hidden)

                def sample_memory():
                    current, peak = tracemalloc.get_traced_memory()
                    memory_samples.append(dict(elapsed_s=perf_counter()-began,
                        traced_current_bytes=current, traced_peak_bytes=peak,
                        waterfall_rows=page.visualization.waterfall_pane.history_rows))

                if args.trace_memory:
                    tracemalloc.start()
                    memory_timer.timeout.connect(sample_memory)
                    memory_timer.start(5000)
                if args.page_cycle_seconds:
                    cycle_timer.timeout.connect(cycle_page)
                    cycle_timer.start(round(args.page_cycle_seconds * 1000))
                heartbeat.start()
                began = perf_counter()

                def request_stop():
                    entered = perf_counter()
                    page.primary.click()
                    stop_times.extend((entered, perf_counter()))

                stop_timer.timeout.connect(request_stop)
                page.primary.click()
                run_qt_until(lambda: not presenter.is_starting, 3)
                due = perf_counter() + args.seconds
                stop_timer.start(round(args.seconds*1000))
                run_qt_until(lambda: bool(stop_times) and presenter.can_close(), args.seconds + 5)
                ended = perf_counter()
                if errors or harness.events != ["sweep-start", "sweep-stop"]:
                    raise AssertionError((errors, harness.events))
                if not page.visualization.spectrum_scene.latest_frame.terminal_sweep:
                    raise AssertionError("terminal gap missing from V2 canvas")
                if poll_ends or producer.high_water > 4:
                    raise AssertionError("undelivered snapshot or unbounded queue")
                rows.append(dict(bins=count, seconds=ended-began,
                    generated_complete_lines=producer.completed,
                    source_queue_high_water=producer.high_water, source_superseded=producer.dropped,
                    preview_superseded=producer.preview_dropped,
                    ui_superseded=service._ui_superseded, poll_count=len(polls),
                    all_polls_off_gui=all(item[1] for item in polls),
                    domain_poll_ms=summary([item[0] for item in polls]),
                    service_return_to_snapshot_subscriber_ms=summary(publications),
                    heartbeat_interval_ms=summary(list(np.diff(beats)*1000)),
                    cpu_paint_ms=summary(paints),
                    canvas_cpu_paint_ms={name: summary(values) for name, values in canvas_paints.items()},
                    host_publication_to_first_paint_return_ms={name: summary(values) if values else None
                        for name, values in age_tracker.ages.items()},
                    first_paint_publications=age_tracker.counts,
                    first_partial_paint_publications=age_tracker.partial_counts,
                    paint_witness_misses=age_tracker.missing,
                    paint_witness_evicted=age_tracker.evicted,
                    repeated_paints=age_tracker.repeated,
                    changed_during_paint=age_tracker.changed_during_paint,
                    stop_timer_lateness_ms=(stop_times[0]-due)*1000,
                    stop_click_return_ms=(stop_times[1]-stop_times[0])*1000,
                    stop_to_idle_ms=(ended-stop_times[0])*1000,
                    terminal_control_gaps=producer.control_gaps,
                    page_switches=len(page_switches),
                    waterfall_rows=page.visualization.waterfall_pane.history_rows,
                    memory_samples=memory_samples,
                    device_pixel_ratio=harness.shell.devicePixelRatioF()))
            finally:
                cycle_timer.stop()
                memory_timer.stop()
                heartbeat.stop()
                stop_timer.stop()
                harness.tearDown()
                harness.doCleanups()
                service.close()
                if args.trace_memory:
                    tracemalloc.stop()
        print(json.dumps(rows[-1]))
    outside = [name for name, module in list(sys.modules.items())
               if name.startswith(("sdr_monitor", "tests")) and getattr(module, "__file__", None)
               and not Path(module.__file__).resolve().is_relative_to(root)]
    if outside:
        raise RuntimeError(f"imports outside selected checkout: {outside}")
    report = dict(scope=__doc__, checkout_head=subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__, pyside=PySide6.__version__,
        pyqtgraph=pg.__version__, host_platform=platform.platform(), processor=platform.processor(),
        platform="Qt offscreen", logical_size=[1920, 1080],
        event_pump="QEventLoop.exec (normal deferred-delete delivery)",
        age_scope="host synthetic publication to first paint return; newest uploaded Waterfall row; same-key both is not atomic/DWM/RF age",
        timing_window_samples=8192, trace_memory=args.trace_memory,
        page_cycle_seconds=args.page_cycle_seconds,
        product_imports_outside_checkout=outside, results=rows)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
