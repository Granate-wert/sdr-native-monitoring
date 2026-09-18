"""Synthetic fast source -> real Sweep domain adapter/presenter -> actual V2.

Includes worker drain/conversion, Qt delivery, canvas and queued Stop under load.
Not native FFT throughput, RF, EXE, DWM, OS input, persistence or Windows DPI.
The fake coordinator owns one preview and four terminals; immutable array
templates are reused (producer allocation and DSP costs are NOT measured).
"""
import argparse
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
from time import perf_counter, sleep
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=5)
    parser.add_argument("--bins", type=int, nargs="+", default=[65536, 262144, 2000000])
    args = parser.parse_args()
    if not sys.flags.isolated or not 1 <= args.seconds <= 20:
        parser.error("use Python -I and 1..20 seconds per grid")
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

        producer = Producer()
        service = NativeContinuousSweepDisplayService(
            SimpleNamespace(NativeContinuousSweepCoordinator=lambda *_: producer), "usb:fake")
        config = SimpleNamespace(epoch=count, segments=(SimpleNamespace(
            fixed_band=SimpleNamespace(device=SimpleNamespace(source_id="synthetic-overload"))),))
        polls, beats, paints, publications, stop_times = [], [], [], [], []
        poll_ends = {}
        gui_thread = threading.get_ident()

        class MeasuredGraphics(pg.GraphicsLayoutWidget):
            def paintEvent(self, event):
                began = perf_counter()
                super().paintEvent(event)
                paints.append((perf_counter() - began) * 1000)

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
            try:
                harness.select_and_apply()
                harness.shell.resize(1920, 1080)
                page = harness.page
                page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
                presenter = harness.composition.analyzer_presenter
                errors = []
                presenter.task_failed.connect(errors.append)

                def delivered(snapshot):
                    ended = poll_ends.pop(id(snapshot))
                    publications.append((perf_counter()-ended)*1000)

                presenter.snapshot_ready.connect(delivered)
                heartbeat.start()
                began = perf_counter()

                def request_stop():
                    entered = perf_counter()
                    page.primary.click()
                    stop_times.extend((entered, perf_counter()))

                stop_timer.timeout.connect(request_stop)
                page.primary.click()
                harness.wait(lambda: not presenter.is_starting)
                due = perf_counter() + args.seconds
                stop_timer.start(round(args.seconds*1000))
                deadline = due + 5
                while not stop_times or not presenter.can_close():
                    harness.app.processEvents()
                    if perf_counter() > deadline:
                        raise RuntimeError("overload Stop deadline exceeded")
                    sleep(.001)
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
                    worker_return_to_gui_delivery_ms=summary(publications),
                    heartbeat_interval_ms=summary(list(np.diff(beats)*1000)),
                    cpu_paint_ms=summary(paints),
                    stop_timer_lateness_ms=(stop_times[0]-due)*1000,
                    stop_click_return_ms=(stop_times[1]-stop_times[0])*1000,
                    stop_to_idle_ms=(ended-stop_times[0])*1000,
                    terminal_control_gaps=producer.control_gaps,
                    device_pixel_ratio=harness.shell.devicePixelRatioF()))
            finally:
                heartbeat.stop()
                stop_timer.stop()
                harness.tearDown()
                harness.doCleanups()
                service.close()
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
        product_imports_outside_checkout=outside, results=rows)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


if __name__ == "__main__":
    main()
