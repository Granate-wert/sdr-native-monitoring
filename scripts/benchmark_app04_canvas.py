"""Actual V2 composition, synthetic partial Sweep -> Qt CPU paint completion.

Not SDR acquisition, native FFT throughput, EXE, DWM presentation, input-device
latency or Windows DPI evidence. Frame construction is outside the measured
presentation interval. The queued action is a Qt marker command, not OS input.
Closed-loop delivery waits for each partial paint; this is not an overload,
producer-coalescing or sustained-throughput benchmark. Persistence is absent.
Hidden mode navigates away, measures delivery and queued-command completion
without painting, then measures one resume-to-paint per grid. It does not stop
acquisition or report hidden delivery timings as paint/FPS measurements.
"""
import argparse
import cProfile
from dataclasses import replace
import hashlib
import json
import os
import platform
from pathlib import Path
import pstats
import subprocess
import sys
from time import perf_counter_ns, sleep
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--repeats", type=int, default=8)
    parser.add_argument("--bins", type=int, nargs="+", default=[65536, 262144, 2000000])
    parser.add_argument("--visibility", choices=("visible", "hidden"), default="visible")
    args = parser.parse_args()
    if not sys.flags.isolated or not 3 <= args.repeats <= 50:
        parser.error("use Python -I and 3..50 repeats")
    if any(not 256 <= count <= 2000000 for count in args.bins):
        parser.error("bins must be within 256..2000000")
    root = args.checkout.resolve(strict=True)
    sys.path.insert(0, str(root))
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    import numpy as np
    import pyqtgraph as pg
    import PySide6
    from PySide6.QtCore import QTimer
    from sdr_monitor.domain.sweep_progress import SweepProgressFrame
    from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot, ContinuousSweepDisplayMetrics
    from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
    from tests.test_app02_analyzer_workspace_product import AnalyzerWorkspaceProductTests

    paints = []

    class MeasuredGraphics(pg.GraphicsLayoutWidget):
        def paintEvent(self, event):
            began = perf_counter_ns()
            super().paintEvent(event)
            paints.append((id(self), began, perf_counter_ns()))

    def wait(predicate):
        deadline = perf_counter_ns() + 5_000_000_000
        while not predicate():
            harness.app.processEvents()
            if perf_counter_ns() > deadline:
                raise RuntimeError("bounded Qt paint/command wait expired")
            sleep(.001)

    rows = []
    resumes = []
    profiler = cProfile.Profile()
    with patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics):
        harness = AnalyzerWorkspaceProductTests("runTest")
        harness.setUpClass()
        harness.setUp()
    try:
        model = harness.composition.analyzer_view_model
        model.select_mode(AnalyzerMode.SWEEP)
        presenter = harness.composition.analyzer_presenter
        # Only the public presentation signal is exercised; no acquisition start.
        presenter.running_changed.emit(True)
        scene = harness.page.visualization.spectrum_scene
        waterfall = harness.page.visualization.waterfall_pane
        harness.shell.resize(1920, 1080)
        harness.app.processEvents()
        device_pixel_ratio = harness.shell.devicePixelRatioF()
        targets = (id(scene._graphics), id(waterfall._graphics))
        sequence = 0
        for count in args.bins:
            if args.visibility == "hidden":
                harness.shell.select_workspace("calibration")
                harness.app.processEvents()
                if harness.page.visualization.isVisible():
                    raise AssertionError("Analyzer must be hidden before delivery")
            uploads_before = waterfall.metrics.image_uploads
            frequencies = np.linspace(100e6, 200e6, count)
            frequencies.setflags(write=False)
            for sample in range(args.repeats + 2):
                sequence += 1
                filled = count // 2 if sample % 2 == 0 else 3 * count // 4
                values = np.full(count, np.nan, dtype=np.float32)
                values[:filled] = -80
                values[count // 4] = -25
                quality = np.zeros(count, dtype=np.uint32)
                quality[filled:] = 1 << 12
                owners = np.zeros(count, dtype=np.int32)
                owners[filled:] = -1
                for array in (values, quality, owners):
                    array.setflags(write=False)
                frame = SweepProgressFrame("synthetic-canvas", sequence, count, 1,
                    "dBFS/bin", frequencies, values, quality, owners, ((0, 1),), (1,))
                snapshot = ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), frame)
                paints.clear()
                command = []
                start = perf_counter_ns()

                def marker_command():
                    entered = perf_counter_ns()
                    marker = scene.place_marker("M1", 125e6)
                    command.append((entered, perf_counter_ns(), marker is not None))

                QTimer.singleShot(0, marker_command)
                presenter.snapshot_ready.emit(snapshot)
                submitted = perf_counter_ns()
                wait(lambda: bool(command) and (args.visibility == "hidden" or
                     all(any(row[0] == target for row in paints) for target in targets)))
                if scene.latest_frame.spectrum is not frame or not command[0][2]:
                    raise AssertionError("partial frame or marker was not delivered")
                if args.visibility == "hidden" and any(row[0] in targets for row in paints):
                    raise AssertionError("hidden Analyzer painted")
                if sample >= 2:
                    row = dict(bins=count, partial_fraction=filled/count,
                        dispatch_ms=(submitted-start)/1e6,
                        queued_marker_ms=(command[0][1]-start)/1e6,
                        marker_execution_ms=(command[0][1]-command[0][0])/1e6)
                    if args.visibility == "visible":
                        first = [next(item for item in paints if item[0] == target) for target in targets]
                        row.update(spectrum_paint_ms=(first[0][2]-first[0][1])/1e6,
                            waterfall_paint_ms=(first[1][2]-first[1][1])/1e6,
                            both_first_paints_ms=(max(item[2] for item in first)-start)/1e6)
                    rows.append(row)
            # Separate profiled sample: never mix cProfile overhead into timings.
            profiled = replace(frame, sequence=sequence+1)
            sequence += 1
            profiler.enable()
            presenter.snapshot_ready.emit(ContinuousSweepDisplaySnapshot(None, ContinuousSweepDisplayMetrics(), profiled))
            harness.app.processEvents()
            profiler.disable()
            if args.visibility == "hidden":
                if waterfall.metrics.image_uploads != uploads_before:
                    raise AssertionError("hidden Waterfall uploaded image data")
                paints.clear()
                start = perf_counter_ns()
                harness.shell.select_workspace("analyzer")
                resumed = perf_counter_ns()
                wait(lambda: all(any(row[0] == target for row in paints) for target in targets))
                if scene.latest_frame.spectrum is not profiled:
                    raise AssertionError("resume did not preserve the latest frame")
                first = [next(row for row in paints if row[0] == target) for target in targets]
                resumes.append(dict(bins=count, resume_dispatch_ms=(resumed-start)/1e6,
                    resume_both_first_paints_ms=(max(row[2] for row in first)-start)/1e6,
                    hidden_image_uploads=0, latest_frame_preserved=True))
        if harness.events:
            raise AssertionError(f"Unexpected acquisition commands: {harness.events}")
    finally:
        presenter.running_changed.emit(False)
        harness.tearDown()
        harness.doCleanups()
    summaries = []
    for count in args.bins:
        subset = [row for row in rows if row["bins"] == count]
        summary = {"bins": count, "samples": len(subset)}
        for metric in (key for key in subset[0] if key.endswith("_ms")):
            values = [row[metric] for row in subset]
            if any(not np.isfinite(value) or value < 0 for value in values):
                raise AssertionError(f"invalid clock/duration for {metric}")
            summary[metric] = dict(zip(("p50", "p95", "p99", "max"),
                                      map(float, (*np.percentile(values, [50, 95, 99]), max(values)))))
        summaries.append(summary)
    outside = [name for name, module in list(sys.modules.items())
               if name.startswith(("sdr_monitor", "tests")) and getattr(module, "__file__", None)
               and not Path(module.__file__).resolve().is_relative_to(root)]
    if outside:
        raise RuntimeError(f"imports outside selected checkout: {outside}")
    stats = pstats.Stats(profiler)
    top = [{"function": f"{Path(key[0]).name}:{key[1]}:{key[2]}", "calls": value[1],
            "total_s": value[2], "cumulative_s": value[3]}
           for key, value in sorted(stats.stats.items(), key=lambda pair: pair[1][3], reverse=True)[:35]]
    source = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted((root/'sdr_monitor/ui/v2').rglob('*.py'))}
    report = dict(scope=__doc__, checkout_head=subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        benchmark_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        python=sys.version.split()[0], numpy=np.__version__, pyside=PySide6.__version__,
        pyqtgraph=pg.__version__, host_platform=platform.platform(), processor=platform.processor(),
        platform="Qt offscreen", logical_size=[1920, 1080], device_pixel_ratio=device_pixel_ratio,
        visibility=args.visibility, resumes=resumes,
        warmups_per_grid=2,
        product_imports_outside_checkout=outside, source_sha256=source,
        samples=rows, summaries=summaries, separate_cprofile_top=top)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
