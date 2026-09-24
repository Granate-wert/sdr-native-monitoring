"""Opt-in physical Pluto RTBW -> visible product UI V2 timing observer.

RX only. This uses the normal V2 shell, source drawer and Start/Stop controls.
The observer subclasses only the two pyqtgraph viewport widgets and records
scalar identities; it never changes the backend, FFT, presentation scheduler or
renderer. Native timestamps are host-wall estimates, not hardware capture time.
Qt paint-return is not DWM presentation or monitor scanout. Run on Windows with
an explicit URI and a unique output path; no device is opened at import time.
"""

from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, deque
from contextlib import nullcontext
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from time import perf_counter_ns, time_ns
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NATIVE_COUNTER_FIELDS = (
    "fft_frames_computed", "fft_frames_dropped", "iq_samples_dropped", "iq_blocks_dropped",
    "source_samples_dropped", "source_blocks_dropped",
    "acquisition_queue_samples_dropped", "acquisition_queue_blocks_dropped",
    "snapshots_emitted", "snapshots_superseded", "persistence_updates",
    "persistence_snapshots_superseded",
)


def counter_deltas(before: object, after: object, fields: tuple[str, ...]) -> dict[str, int]:
    """Subtract cumulative counters across the measured interval only."""
    return {field: int(getattr(after, field)) - int(getattr(before, field)) for field in fields}


def projector_counters(projector: object | None) -> dict[str, int] | None:
    if projector is None:
        return None
    result = {field: int(getattr(projector, field)) for field in ("completed", "cancelled", "superseded")}
    optional = getattr(projector, "persistence_projector", None)
    if optional is not None:
        result.update({f"optional_{field}": int(getattr(optional, field))
                       for field in ("completed", "cancelled", "superseded")})
    return result


def distribution(values: deque[float], dropped: int) -> dict[str, float] | None:
    """Never report a bounded tail as a whole-run percentile."""
    if dropped or not values:
        return None
    import numpy as np

    p50, p95, p99 = np.percentile(tuple(values), (50, 95, 99))
    return dict(p50=float(p50), p95=float(p95), p99=float(p99), max=float(max(values)))


class ScalarSeries:
    def __init__(self, capacity: int = 8192) -> None:
        self.values: deque[float] = deque(maxlen=capacity)
        self.count = 0

    def add(self, value: float) -> None:
        self.count += 1
        self.values.append(float(value))

    def report(self) -> dict[str, object]:
        dropped = self.count - len(self.values)
        return dict(count=self.count, retained=len(self.values), dropped=dropped,
                    ms=distribution(self.values, dropped))


class WorkerSeries:
    """One bounded scalar timing series safe to report after worker shutdown."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._samples = ScalarSeries()

    def add(self, value: float) -> None:
        with self._lock:
            self._samples.add(value)

    def report(self) -> dict[str, object]:
        with self._lock:
            return self._samples.report()


class PaintObserver:
    """Bounded GUI-only sequence/time witness; no frame or ndarray retention."""

    def __init__(self) -> None:
        self.workspace = None
        self.measuring = False
        self.model_events = 0
        self.model_first: OrderedDict[tuple[int, int, int], int] = OrderedDict()
        self.model_keys_evicted = 0
        self.unique_paints = 0
        self.repeat_paints = 0
        self.changed_during_paint = 0
        self.unmapped_paints = 0
        self.wall_clock_invalid = 0
        self.first_key = self.last_key = None
        self.last_unique_paint_ns: int | None = None
        self.paint_duration = ScalarSeries()
        self.model_to_paint = ScalarSeries()
        self.estimated_source_to_paint = ScalarSeries()
        self.unique_paint_gap = ScalarSeries()
        self.waterfall_paint_duration = ScalarSeries()
        self.gui_render_handler = ScalarSeries()
        self.worker_preparation = WorkerSeries()
        self.worker_projection_required = WorkerSeries()
        self.worker_projection_optional_only = WorkerSeries()
        self.worker_projection_total = WorkerSeries()
        self.worker_density_image = WorkerSeries()
        self.timestamp_qualities: Counter[str] = Counter()
        self.loss_reasons_seen: set[str] = set()

    @staticmethod
    def key(bundle: object) -> tuple[int, int, int] | None:
        from sdr_monitor.domain.live import LiveSpectrumFrame

        spectrum = getattr(bundle, "spectrum", None)
        if not isinstance(spectrum, LiveSpectrumFrame):
            return None
        return (int(spectrum.acquisition_epoch or 0),
                int(spectrum.config_generation), int(spectrum.sequence))

    def model_enter(self, workspace: object, state: object) -> None:
        if not self.measuring or workspace is not self.workspace:
            return
        key = self.key(getattr(state, "bundle", None))
        if key is None:
            return
        self.model_events += 1
        if key not in self.model_first:
            self.model_first[key] = perf_counter_ns()
            if len(self.model_first) > 8192:
                self.model_first.popitem(last=False)
                self.model_keys_evicted += 1

    def painted(self, widget: object, begin_ns: int, end_ns: int,
                before: object, after: object) -> None:
        if not self.measuring or self.workspace is None:
            return
        pane = self.workspace.visualization.waterfall_pane
        if widget is pane._graphics:
            self.waterfall_paint_duration.add((end_ns - begin_ns) / 1e6)
            return
        scene = self.workspace.visualization.spectrum_scene
        if widget is not scene._graphics:
            return
        self.paint_duration.add((end_ns - begin_ns) / 1e6)
        if before is not after:
            self.changed_during_paint += 1
            return
        key = self.key(after)
        if key is None:
            self.unmapped_paints += 1
            return
        if key == self.last_key:
            self.repeat_paints += 1
            return
        self.unique_paints += 1
        if self.first_key is None:
            self.first_key = key
        self.last_key = key
        if self.last_unique_paint_ns is not None:
            self.unique_paint_gap.add((end_ns - self.last_unique_paint_ns) / 1e6)
        self.last_unique_paint_ns = end_ns
        model_ns = self.model_first.pop(key, None)
        if model_ns is None:
            self.unmapped_paints += 1
        else:
            self.model_to_paint.add((end_ns - model_ns) / 1e6)
        spectrum = after.spectrum
        quality = getattr(spectrum, "timestamp_quality", None)
        self.timestamp_qualities[str(getattr(quality, "value", quality))] += 1
        self.loss_reasons_seen.update(str(getattr(reason, "value", reason))
                                      for reason in getattr(spectrum, "loss_reasons", ()))
        estimated_age_ms = (time_ns() - int(spectrum.timestamp_ns)) / 1e6
        if -1.0 <= estimated_age_ms <= 60_000.0:
            self.estimated_source_to_paint.add(estimated_age_ms)
        else:
            self.wall_clock_invalid += 1

    def report(self) -> dict[str, object]:
        return dict(model_events=self.model_events, unique_spectrum_paints=self.unique_paints,
                    repeat_spectrum_paints=self.repeat_paints,
                    changed_during_paint=self.changed_during_paint,
                    unmapped_paints=self.unmapped_paints,
                    model_keys_evicted=self.model_keys_evicted,
                    wall_clock_invalid=self.wall_clock_invalid,
                    first_key=self.first_key, last_key=self.last_key,
                    spectrum_paint_return_ms=self.paint_duration.report(),
                    waterfall_paint_return_ms=self.waterfall_paint_duration.report(),
                    gui_model_to_first_paint_return_ms=self.model_to_paint.report(),
                    estimated_source_timestamp_to_first_paint_return_ms=self.estimated_source_to_paint.report(),
                    unique_spectrum_paint_gap_ms=self.unique_paint_gap.report(),
                    gui_render_handler_ms=self.gui_render_handler.report(),
                    worker_preparation_ms=self.worker_preparation.report(),
                    worker_projection_required_ms=self.worker_projection_required.report(),
                    worker_projection_optional_only_ms=self.worker_projection_optional_only.report(),
                    worker_projection_total_ms=self.worker_projection_total.report(),
                    worker_density_image_ms=self.worker_density_image.report(),
                    timestamp_qualities=dict(self.timestamp_qualities),
                    loss_reasons_seen=sorted(self.loss_reasons_seen))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--uri", required=True, help="explicit Pluto usb: or ip: URI")
    result.add_argument("--output", type=Path, required=True, help="new JSON path; never overwritten")
    result.add_argument("--duration", type=float, default=10.0)
    result.add_argument("--warmup", type=float, default=2.0)
    result.add_argument("--center-mhz", type=float, default=2400.0)
    result.add_argument("--sample-rate-msps", type=float, default=3.0)
    result.add_argument("--fft", type=int, default=4096)
    result.add_argument("--buffer-samples", type=int, default=262144,
                        help="native RX refill geometry; 262144 is the product default")
    result.add_argument("--hide-persistence", action="store_true",
                        help="normal V2 display toggle; native histogram still runs")
    result.add_argument("--split-persistence", action="store_true",
                        help="opt-in existing second worker seam; not product wiring")
    result.add_argument("--render-mode", choices=("direct", "visual"), default="direct")
    result.add_argument("--display-fps", type=int, choices=(15, 30, 60, 120, 144, 240), default=120)
    result.add_argument("--window-width", type=int, default=1400)
    result.add_argument("--window-height", type=int, default=850)
    return result


def main() -> int:
    args = parser().parse_args()
    if os.name != "nt" or not args.uri.startswith(("usb:", "ip:")):
        raise SystemExit("physical UI observer requires Windows and an explicit usb:/ip: URI")
    if not 2 <= args.duration <= 120 or not 0 <= args.warmup <= 30:
        raise SystemExit("duration must be 2..120 s and warmup 0..30 s")
    if args.sample_rate_msps <= 0 or args.fft < 256 or args.fft & (args.fft - 1):
        raise SystemExit("positive Fs and a power-of-two FFT >=256 are required")
    if args.buffer_samples < 4096 or args.buffer_samples > 262144 or args.buffer_samples & (args.buffer_samples - 1):
        raise SystemExit("buffer-samples must be a power of two in 4096..262144")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("output already exists; choose a new evidence path")
    output.parent.mkdir(parents=True, exist_ok=True)
    os.environ["QT_QPA_PLATFORM"] = "windows"
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                  capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        source_commit = None
    else:
        source_commit = revision.stdout.strip() if revision.returncode == 0 else None
    script_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    projection_sha256 = hashlib.sha256((ROOT / "sdr_monitor/ui/v2/spectrum/persistence_projection.py")
                                        .read_bytes()).hexdigest()

    import pyqtgraph as pg
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from sdr_monitor.domain.live import BackendKind
    from sdr_monitor.services import build_default_sdr_services
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.services.native_recording import NativeLiveRecordingService
    from sdr_monitor.services.native_sweep import NativeLiveSweepService
    from sdr_monitor.ui.v2.workspaces.analyzer import AnalyzerWorkspaceV2
    from sdr_monitor.ui.v2.state.prepared_live import LiveSnapshotPreparer
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    import sdr_monitor.ui.v2.spectrum.projection as projection_module
    import sdr_monitor.ui.v2.spectrum.persistence_projector as persistence_projector_module
    import sdr_monitor.ui.v2.product_live as product_live_module
    from sdr_monitor.ui.v2_composition import build_v2_shell

    observer = PaintObserver()
    original_render = AnalyzerWorkspaceV2._render
    original_prepare = LiveSnapshotPreparer.prepare_cancellable
    original_project = projection_module.project_spectrum
    original_density_image = projection_module.prepare_persistence_image

    def observed_render(workspace, state):
        began = perf_counter_ns()
        observer.model_enter(workspace, state)
        try:
            return original_render(workspace, state)
        finally:
            if observer.measuring and workspace is observer.workspace:
                observer.gui_render_handler.add((perf_counter_ns() - began) / 1e6)

    def observed_prepare(preparer, snapshot, *, cancelled=None):
        began = perf_counter_ns()
        try:
            return original_prepare(preparer, snapshot, cancelled=cancelled)
        finally:
            if observer.measuring:
                observer.worker_preparation.add((perf_counter_ns() - began) / 1e6)

    def observed_project(*args, **kwargs):
        began = perf_counter_ns()
        ready = kwargs.get("spectrum_ready")
        required_work = bool(getattr(args[0], "required_work", False))
        emitted_required = False
        if ready is not None:
            def required_ready(result):
                nonlocal emitted_required
                emitted_required = True
                if observer.measuring:
                    observer.worker_projection_required.add((perf_counter_ns() - began) / 1e6)
                return ready(result)

            kwargs["spectrum_ready"] = required_ready
        try:
            return original_project(*args, **kwargs)
        finally:
            if observer.measuring:
                elapsed = (perf_counter_ns() - began) / 1e6
                observer.worker_projection_total.add(elapsed)
                if not required_work:
                    observer.worker_projection_optional_only.add(elapsed)
                elif not emitted_required:
                    observer.worker_projection_required.add(elapsed)

    def observed_density_image(*args, **kwargs):
        began = perf_counter_ns()
        try:
            return original_density_image(*args, **kwargs)
        finally:
            if observer.measuring:
                observer.worker_density_image.add((perf_counter_ns() - began) / 1e6)

    class MeasuredGraphics(pg.GraphicsLayoutWidget):
        def paintEvent(self, event):
            workspace = observer.workspace
            scene = None if workspace is None else workspace.visualization.spectrum_scene
            before = None if scene is None or self is not scene._graphics else scene.displayed_frame
            began = perf_counter_ns()
            super().paintEvent(event)
            ended = perf_counter_ns()
            after = None if scene is None or self is not scene._graphics else scene.displayed_frame
            observer.painted(self, began, ended, before, after)

    original_compose = product_live_module.compose_v2_live_product

    def observed_compose(presenter, **kwargs):
        kwargs["persistence_submit"] = presenter.submit_persistence_task
        return original_compose(presenter, **kwargs)

    report: dict[str, object] = {}
    split_patch = (patch.object(product_live_module, "compose_v2_live_product", observed_compose)
                   if args.split_persistence else nullcontext())
    with patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics), patch.object(
            AnalyzerWorkspaceV2, "_render", observed_render), patch.object(
            LiveSnapshotPreparer, "prepare_cancellable", observed_prepare), patch.object(
            projection_module, "project_spectrum", observed_project), patch.object(
            projection_module, "prepare_persistence_image", observed_density_image), patch.object(
            persistence_projector_module, "prepare_persistence_image", observed_density_image), split_patch:
        app = QApplication.instance() or QApplication([])
        # AppShellV2 persists geometry/theme/navigation on close. Never write
        # these benchmark window values into the user's product QSettings.
        settings_namespace = dict(organization="SDR Native Monitoring Bench",
                                  domain="local.sdr-native-monitoring-bench",
                                  application="APP05 Physical UI Observer")
        app.setOrganizationName(settings_namespace["organization"])
        app.setOrganizationDomain(settings_namespace["domain"])
        app.setApplicationName(settings_namespace["application"])
        services = build_default_sdr_services()
        if not isinstance(services.live_sdr, NativeLiveSessionService):
            raise RuntimeError("native Pluto service unavailable; no RX was attempted")
        if args.buffer_samples != 262144:
            # Explicit benchmark experiment only. All three receiver-facing
            # ports must retain the SAME replacement live owner; product
            # build_v2_shell() still uses its unchanged 262144-sample default.
            live = NativeLiveSessionService(
                services.live_sdr._native, device_buffer_samples=args.buffer_samples)
            services = replace(services, live_sdr=live,
                               sweep=NativeLiveSweepService(live),
                               recording=NativeLiveRecordingService(live))
        shell = build_v2_shell(services)
        shell.resize(args.window_width, args.window_height)
        shell.show()
        workspace = shell._workspace_pages["analyzer"]
        observer.workspace = workspace
        composition = shell._context.close_ports[0].shutdown.__self__
        composition._presenter.set_display_fps(args.display_fps)
        workspace.visualization.spectrum_scene.set_persistence_render_mode(
            PersistenceRenderMode(args.render_mode))
        if args.hide_persistence:
            workspace.visualization.spectrum_scene.set_persistence_visible(False)
        phase = "select"
        deadline_ns = perf_counter_ns() + 15_000_000_000
        measure_end_ns = None
        failure = None
        native_before_stop = None
        native_at_start = None
        display_before_stop = None
        persistence_before_stop = None
        persistence_at_start = None
        projector_before_stop = None
        projector_at_start = None
        initial_sequence = final_sequence = None
        applied = None
        measurement_started_ns = measurement_finished_ns = None

        def fail(message: str) -> None:
            nonlocal failure, phase, deadline_ns
            failure = message
            phase = "stop"
            deadline_ns = perf_counter_ns() + 15_000_000_000

        def tick() -> None:
            nonlocal phase, deadline_ns, measure_end_ns, applied, native_before_stop, failure
            nonlocal display_before_stop, persistence_before_stop, projector_before_stop
            nonlocal native_at_start, persistence_at_start, projector_at_start
            nonlocal initial_sequence, final_sequence
            nonlocal measurement_started_ns, measurement_finished_ns
            now = perf_counter_ns()
            state = workspace.model.state
            if now > deadline_ns and phase not in ("measure", "done"):
                if phase in ("stop", "wait_stopped", "wait_closed"):
                    failure = failure or f"cleanup phase {phase} exceeded its 15-s deadline"
                    timer.stop()
                    app.quit()
                    return
                fail(f"phase {phase} exceeded its 15-s deadline")
            try:
                if phase == "select":
                    workspace.drawer._uri.setText(args.uri)
                    workspace.drawer._use_uri.click()
                    phase = "wait_selected"
                elif phase == "wait_selected":
                    device = getattr(state.live.snapshot, "device", None)
                    if device is not None and not state.live.busy:
                        workspace.drawer._center.setValue(args.center_mhz)
                        workspace.drawer._sample_rate.setValue(args.sample_rate_msps)
                        workspace.drawer._fft.setValue(args.fft)
                        cpu_index = workspace.drawer._backend.findData(BackendKind.CPU.value)
                        if cpu_index < 0:
                            fail("selected device did not publish CPU backend")
                        else:
                            workspace.drawer._backend.setCurrentIndex(cpu_index)
                            if not workspace.drawer._apply.isEnabled():
                                fail("V2 drawer refused the requested configuration")
                            else:
                                workspace.drawer._apply.click()
                                phase = "wait_applied"
                elif phase == "wait_applied":
                    applied = getattr(state.live.snapshot, "applied", None)
                    if applied is not None and not state.live.busy and not state.configuration_pending:
                        if not workspace.primary.isEnabled():
                            fail("V2 Start disabled after applied configuration")
                        else:
                            workspace.primary.click()
                            phase = "wait_running"
                elif phase == "wait_running":
                    if state.live.primary_action.value == "stop" and state.bundle is not None:
                        deadline_ns = now + int((args.warmup + args.duration + 5) * 1e9)
                        measure_end_ns = now + int((args.warmup + args.duration) * 1e9)
                        phase = "warmup"
                elif phase == "warmup":
                    if measure_end_ns is not None and now >= measure_end_ns - int(args.duration * 1e9):
                        snapshot = services.live_sdr.latest_snapshot()
                        native_at_start = snapshot.performance
                        initial_sequence = int(snapshot.sequence)
                        persistence_at_start = workspace.visualization.spectrum_scene.persistence_metrics
                        projector_at_start = projector_counters(composition.spectrum_projector)
                        composition._presenter.reset_display_metrics()
                        measurement_started_ns = now
                        observer.measuring = True
                        phase = "measure"
                elif phase == "measure":
                    if not services.live_sdr.is_running():
                        observer.measuring = False
                        fail("physical RX left RUNNING during measurement")
                        return
                    if measure_end_ns is not None and now >= measure_end_ns:
                        observer.measuring = False
                        measurement_finished_ns = now
                        snapshot = services.live_sdr.latest_snapshot()
                        native_before_stop = snapshot.performance
                        final_sequence = int(snapshot.sequence)
                        applied = snapshot.applied
                        display_before_stop = composition._presenter.display_metrics
                        scene = workspace.visualization.spectrum_scene
                        persistence_before_stop = scene.persistence_metrics
                        projector_before_stop = projector_counters(composition.spectrum_projector)
                        if initial_sequence is None or final_sequence <= initial_sequence:
                            failure = "native spectrum sequence did not advance during measurement"
                        phase = "stop"
                        deadline_ns = now + 15_000_000_000
                elif phase == "stop":
                    if state.live.primary_action.value == "stop" and not state.live.busy:
                        workspace.primary.click()
                        phase = "wait_stopped"
                    elif state.live.primary_action.value != "stop" and not state.live.busy:
                        phase = "wait_stopped"
                elif phase == "wait_stopped":
                    if state.live.primary_action.value != "stop" and not state.live.busy and not state.stopping:
                        shell.close()
                        phase = "wait_closed"
                elif phase == "wait_closed":
                    if shell._is_closed:
                        phase = "done"
                        timer.stop()
                        app.quit()
            except Exception as error:
                if phase in ("stop", "wait_stopped", "wait_closed"):
                    timer.stop()
                    app.quit()
                    fail(f"cleanup failed: {error}")
                else:
                    fail(f"{type(error).__name__}: {error}")

        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(tick)
        timer.start()
        app.exec()
        # Last-window-closed can exit the Qt loop before the next timer tick.
        if shell._is_closed and phase == "wait_closed":
            phase = "done"
        if phase != "done":
            try:
                services.live_sdr.stop_and_wait(5.0)
            except Exception as error:
                failure = (failure or "incomplete UI close") + f"; emergency Stop failed: {error}"
        screen = shell.screen()
        window = dict(logical_width=shell.width(), logical_height=shell.height(),
                      dpr=shell.devicePixelRatioF(),
                      screen_name=None if screen is None else screen.name())
        budget = composition.allocation_budget.snapshot()
        workers = [thread.name for thread in threading.enumerate()
                   if thread is not threading.main_thread()]
        measured_elapsed_s = (None if measurement_started_ns is None or measurement_finished_ns is None
                              else (measurement_finished_ns - measurement_started_ns) / 1e9)
        native_interval = (None if native_at_start is None or native_before_stop is None else
                           counter_deltas(native_at_start, native_before_stop, NATIVE_COUNTER_FIELDS))
        if native_interval is not None and measured_elapsed_s is not None and measured_elapsed_s > 0:
            native_interval["computed_fft_per_s"] = native_interval["fft_frames_computed"] / measured_elapsed_s
        persistence_interval = None
        if persistence_at_start is not None and persistence_before_stop is not None:
            metric_names = tuple(key for key in asdict(persistence_before_stop)
                                 if key != "retained_extra_image_buffers")
            persistence_interval = counter_deltas(persistence_at_start, persistence_before_stop, metric_names)
            persistence_interval["retained_extra_image_buffers_at_end"] = (
                persistence_before_stop.retained_extra_image_buffers)
        projector_interval = None
        if projector_at_start is not None and projector_before_stop is not None:
            projector_interval = {key: value - projector_at_start[key]
                                  for key, value in projector_before_stop.items()}
        measurement_valid = (measured_elapsed_s is not None and native_interval is not None
                             and native_interval["fft_frames_computed"] > 0
                             and all(value >= 0 for key, value in native_interval.items()
                                     if key != "computed_fft_per_s")
                             and initial_sequence is not None and final_sequence is not None
                             and final_sequence > initial_sequence)
        report = dict(schema="app05-physical-visible-v2-v3", result="pass" if phase == "done" and
                      failure is None and measurement_valid and observer.unique_paints >= 2
                      and budget.reserved_bytes == 0 and not workers else "fail",
                      failure=failure, phase=phase, source="physical-pluto-rx", uri=args.uri,
                      source_commit=source_commit, script_sha256=script_sha256,
                      persistence_projection_sha256=projection_sha256,
                      settings_namespace=settings_namespace,
                      requested=dict(center_mhz=args.center_mhz, sample_rate_msps=args.sample_rate_msps,
                                     fft=args.fft, buffer_samples=args.buffer_samples,
                                     persistence_visible=not args.hide_persistence,
                                     split_persistence=args.split_persistence,
                                     render_mode=args.render_mode, display_fps=args.display_fps,
                                     backend="cpu", warmup_s=args.warmup,
                                     measurement_s=args.duration),
                      measured_elapsed_s=measured_elapsed_s,
                      native_sequence_interval=None if initial_sequence is None or final_sequence is None
                          else dict(first=initial_sequence, last=final_sequence),
                      applied=None if applied is None else dict(
                          center_hz=applied.applied.center_hz,
                          sample_rate_hz=applied.applied.sample_rate_hz,
                          analog_bandwidth_hz=applied.applied.analog_bandwidth_hz,
                          fft_size=applied.applied.fft_size,
                          backend=applied.applied.backend.value,
                          readback_fields=applied.readback_fields),
                      window=window,
                      native_performance=None if native_before_stop is None else dict(
                          analytical_fft_rate_hz=native_before_stop.analytical_fft_rate_hz,
                          spectrum_snapshot_rate_hz=native_before_stop.spectrum_snapshot_rate_hz,
                          iq_sample_rate_hz=native_before_stop.iq_sample_rate_hz,
                          fft_frames_computed=native_before_stop.fft_frames_computed,
                          fft_frames_dropped=native_before_stop.fft_frames_dropped,
                          iq_samples_dropped=native_before_stop.iq_samples_dropped,
                          iq_blocks_dropped=native_before_stop.iq_blocks_dropped,
                          source_samples_dropped=native_before_stop.source_samples_dropped,
                          source_blocks_dropped=native_before_stop.source_blocks_dropped,
                          acquisition_queue_samples_dropped=native_before_stop.acquisition_queue_samples_dropped,
                          acquisition_queue_blocks_dropped=native_before_stop.acquisition_queue_blocks_dropped,
                          snapshots_emitted=native_before_stop.snapshots_emitted,
                          snapshots_superseded=native_before_stop.snapshots_superseded,
                          persistence_updates=native_before_stop.persistence_updates,
                          persistence_snapshots_superseded=native_before_stop.persistence_snapshots_superseded,
                          end_to_end_latency_ms=native_before_stop.end_to_end_latency_ms),
                      native_measurement_deltas=native_interval,
                      display_scheduler=None if display_before_stop is None else asdict(display_before_stop),
                      persistence_overlay_measurement_deltas=persistence_interval,
                      projector_measurement_deltas=projector_interval,
                      observer=observer.report(),
                      allocation_budget=asdict(budget), workers_after_close=workers,
                      scope="Successful run means bounded RX/UI lifecycle evidence, not performance acceptance. "
                            "Visible Qt paint-return only; Pluto timestamp estimates sample start from refill "
                            "completion and nominal block duration. No hardware capture, DWM/scanout/FPS claim. "
                            "Observer subclasses viewport widgets and perturbs timings.")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(dict(result=report["result"], output=str(output),
                          observer=report["observer"], native_performance=report["native_performance"]),
                     ensure_ascii=False))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
