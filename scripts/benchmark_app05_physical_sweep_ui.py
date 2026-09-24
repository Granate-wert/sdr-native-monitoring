"""Opt-in physical Pluto progressive Sweep -> visible product UI V2 gate.

Use an explicit USB/IP URI and a new output path. The normal Analyzer controls
select, apply, start and stop the receiver. Qt paint-return is neither changed
pixels nor desktop scanout. This script performs no RF transmission.
"""

from __future__ import annotations

import argparse
import cProfile
from contextlib import nullcontext
from dataclasses import asdict
import hashlib
import io
import json
import math
from pathlib import Path
import pstats
import subprocess
import sys
import threading
from time import perf_counter_ns
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
_MAX_IDENTITIES = 4096
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--uri", required=True, help="explicit physical Pluto usb: or ip: URI")
    result.add_argument("--output", type=Path, required=True, help="new JSON evidence path")
    result.add_argument("--start-mhz", type=float, default=2300.0)
    result.add_argument("--stop-mhz", type=float, default=2600.0)
    result.add_argument("--sample-rate-msps", type=float, default=61.44)
    result.add_argument("--fft", type=int, default=4096)
    result.add_argument("--window-width", type=int, default=1400)
    result.add_argument("--window-height", type=int, default=850)
    result.add_argument("--run-timeout", type=float, default=35.0,
                        help="maximum time to observe a same-pass partial/final paint pair")
    result.add_argument("--profile-terminal-gap", action="store_true",
                        help="diagnostic cProfile of one upper-plot gap paint; invalidates latency comparison")
    result.add_argument("--theme", choices=("dark", "light", "high_contrast"), default="dark")
    result.add_argument("--capture-surface", type=Path,
                        help="optional post-Stop QWidget surface PNG, not desktop/DWM evidence")
    return result


def sweep_key(frame: object | None) -> tuple[str, int, int, str, int] | None:
    """Exact source, epoch, pass, state and revision; never arrival order."""
    if frame is None:
        return None
    source = getattr(frame, "source_id", None)
    epoch = getattr(frame, "epoch", None)
    sequence = getattr(frame, "sequence", None)
    if not isinstance(source, str) or type(epoch) is not int or type(sequence) is not int:
        return None
    revision = getattr(frame, "revision", None)
    if revision is not None:
        return source, epoch, sequence, "partial", int(revision)
    state = getattr(frame, "state", None)
    return source, epoch, sequence, str(getattr(state, "value", state)), 0


def waterfall_update_key(update: object) -> tuple[str, int, int, str, int] | None:
    """Recover the producer identity carried by the immutable Sweep row."""
    row = getattr(update, "row", None)
    generation = getattr(row, "configuration_generation", None)
    stamp = getattr(update, "stamp", None)
    if not isinstance(generation, str) or not generation.startswith("sweep:") or stamp is None:
        return None
    try:
        identity = json.loads(generation[len("sweep:"):])
    except (ValueError, TypeError):
        return None
    if (not isinstance(identity, list) or len(identity) != 2 or not isinstance(identity[0], str)
            or type(identity[1]) is not int or type(stamp.sequence) is not int
            or type(stamp.revision) is not int):
        return None
    state = str(getattr(stamp.state, "value", stamp.state))
    if state not in ("partial", "complete", "gap"):
        return None
    return identity[0], identity[1], stamp.sequence, state, stamp.revision if state == "partial" else 0


def applied_matches_request(applied: object, *, sample_rate_msps: float, fft: int) -> bool:
    """A PASS must describe the configuration actually accepted by the SDR."""
    configuration = getattr(applied, "applied", None)
    backend = getattr(getattr(configuration, "backend", None), "value", None)
    actual_rate = getattr(configuration, "sample_rate_hz", None)
    return bool(configuration is not None and backend == "cpu"
                and getattr(configuration, "fft_size", None) == fft
                and type(actual_rate) in (int, float)
                and math.isclose(actual_rate, sample_rate_msps * 1e6,
                                 rel_tol=0.0, abs_tol=1.0))


def uploaded_waterfall_key(*, attempted: tuple[str, int, int, str, int] | None,
                           uploaded: tuple[str, int, int, str, int] | None,
                           stamp: object | None, uploads: int, upload_count: int
                           ) -> tuple[str, int, int, str, int] | None:
    """Never identify a paint using a stamp newer than its actual image upload."""
    if stamp is None or uploaded is None or attempted != uploaded or uploads < upload_count:
        return None
    state = str(getattr(getattr(stamp, "state", None), "value", getattr(stamp, "state", None)))
    revision = getattr(stamp, "revision", None) if state == "partial" else 0
    visible_stamp = (getattr(stamp, "sequence", None), state, revision)
    return uploaded if uploaded[2:] == visible_stamp else None


def first_progressive_pair(events: list[dict[str, object]],
                           complete_models: dict[tuple[str, int, int], int]) -> dict[str, object] | None:
    """Require both canvases to paint the same partial then complete pass.

    A partial paint must precede the terminal model publication itself, not
    merely a later terminal paint. A completed pass from N+1 cannot be paired
    with a partial from N. Return only scalar event dictionaries.
    """
    by_pass: dict[tuple[str, int, int], dict[tuple[str, int, str], dict[str, object]]] = {}
    for event in events:
        key = tuple(event["key"])
        if len(key) != 5 or key[3] not in ("partial", "complete"):
            continue
        identity = key[:3]
        stages = by_pass.setdefault(identity, {})
        stages[(key[3], key[4], event["pane"])] = event
    for identity, stages in by_pass.items():
        complete_ns = complete_models.get(identity)
        if complete_ns is None:
            continue
        finals = {pane: stages.get(("complete", 0, pane)) for pane in ("spectrum", "waterfall")}
        if any(value is None for value in finals.values()):
            continue
        revisions = {revision for state, revision, _pane in stages if state == "partial" and revision > 0}
        for revision in sorted(revisions):
            partial = {pane: stages.get(("partial", revision, pane))
                       for pane in ("spectrum", "waterfall")}
            if any(value is None for value in partial.values()):
                continue
            if (partial["spectrum"]["coverage_runs"] > 0
                    and all(partial[pane]["when_ns"] < complete_ns < finals[pane]["when_ns"]
                            for pane in partial)):
                return dict(source_id=identity[0], epoch=identity[1], sequence=identity[2],
                            revision=revision, partial=partial, complete=finals,
                            complete_model_ns=complete_ns)
    return None


def terminal_gap_paints(events: list[dict[str, object]],
                        terminal_key: tuple[str, int, int, str, int] | None,
                        stop_intent_ns: int | None) -> dict[str, dict[str, object]] | None:
    """Require both canvases to paint the exact post-Stop control gap."""
    if terminal_key is None or terminal_key[3] != "gap" or stop_intent_ns is None:
        return None
    painted: dict[str, dict[str, object]] = {}
    for event in events:
        if (tuple(event["key"]) == terminal_key and event["when_ns"] > stop_intent_ns
                and event["pane"] in ("spectrum", "waterfall")):
            painted.setdefault(event["pane"], event)
    return painted if set(painted) == {"spectrum", "waterfall"} else None


def main() -> int:
    args = parser().parse_args()
    if sys.platform != "win32" or not args.uri.startswith(("usb:", "ip:")):
        raise SystemExit("physical Sweep gate requires Windows and an explicit usb:/ip: URI")
    if (not all(map(math.isfinite, (args.start_mhz, args.stop_mhz, args.sample_rate_msps,
                                    args.run_timeout)))
            or not 0.001 <= args.start_mhz < args.stop_mhz <= 100_000.0
            or any(not math.isclose(value, round(value, 3), rel_tol=0.0, abs_tol=1e-9)
                   for value in (args.start_mhz, args.stop_mhz))
            or not 36.0 <= args.sample_rate_msps <= 100.0
            or not math.isclose(args.sample_rate_msps, round(args.sample_rate_msps, 3),
                                rel_tol=0.0, abs_tol=1e-9)
            or not 256 <= args.fft <= 262_144 or args.fft & (args.fft - 1)
            or not 1 <= args.run_timeout <= 120
            or not 800 <= args.window_width <= 3840 or not 500 <= args.window_height <= 2160):
        raise SystemExit("invalid bounded physical Sweep request")
    output = args.output.resolve()
    if not output.parent.is_dir() or output.exists():
        raise SystemExit("output parent must exist and evidence path must be new")
    capture_path = None if args.capture_surface is None else args.capture_surface.resolve()
    if capture_path is not None and (not capture_path.parent.is_dir() or capture_path.exists()
                                     or capture_path.suffix.lower() != ".png"):
        raise SystemExit("surface PNG parent must exist and capture path must be new")

    import pyqtgraph as pg
    import PySide6
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from sdr_monitor.domain.live import BackendKind
    from sdr_monitor.domain.sweep_speed import SweepSpeedProfile
    from sdr_monitor.services import build_default_sdr_services
    from sdr_monitor.services.native_live import NativeLiveSessionService
    from sdr_monitor.ui.v2.view_models.analyzer_view_model import AnalyzerMode
    from sdr_monitor.ui.v2.spectrum.contracts import TraceKind
    from sdr_monitor.ui.v2.design import ThemeId
    from sdr_monitor.ui.v2.waterfall.pane import WaterfallPane
    from sdr_monitor.ui.v2.workspaces.analyzer import AnalyzerWorkspaceV2
    from sdr_monitor.ui.v2_composition import build_v2_shell
    from scripts.benchmark_app05_physical_ui import paint_intersects_item

    try:
        source_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                                text=True, timeout=2).strip()
        status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"],
                                         cwd=ROOT, text=True, timeout=2)
        tracked_dirty_paths = [line[3:] for line in status.splitlines()]
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        source_commit, tracked_dirty_paths = None, None
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        import sdr_monitor._sdr_native as native
        native_path = Path(native.__file__).resolve()
        native_binary = dict(path=str(native_path), sha256=hashlib.sha256(native_path.read_bytes()).hexdigest())
    except (ImportError, OSError, TypeError):
        native_binary = None

    page = None
    phase = "select"
    error = None
    events: list[dict[str, object]] = []
    painted_keys: set[tuple[str, int, int, str, int, str]] = set()
    complete_models: dict[tuple[str, int, int], int] = {}
    model_partial_keys: set[tuple[str, int, int, str, int]] = set()
    model_complete_keys: set[tuple[str, int, int, str, int]] = set()
    last_waterfall_update_key = None
    last_waterfall_upload_key = None
    waterfall_upload_count = 0
    first_pair = None
    stop_intent_ns = None
    stop_click_return_ns = None
    stop_ack_ns = None
    start_ack_ns = None
    deadline_ns = perf_counter_ns() + 15_000_000_000
    preview = None
    applied = None
    terminal_snapshot = None
    overflow = 0
    phase_events: list[dict[str, object]] = []
    last_logged_phase = None
    last_tick_ns = None
    max_tick_gap_ms = 0.0
    terminal_gap_profile = None
    terminal_gap_curve_paints: list[dict[str, object]] = []
    curve_profile_active = False
    capture_status = None

    original_render = AnalyzerWorkspaceV2._render
    original_set_sweep_line = WaterfallPane.set_sweep_line
    original_curve_paint = pg.PlotCurveItem.paint

    def observed_curve_paint(curve, *paint_args, **paint_kwargs):
        started_ns = perf_counter_ns()
        try:
            return original_curve_paint(curve, *paint_args, **paint_kwargs)
        finally:
            ended_ns = perf_counter_ns()
            if curve_profile_active and len(terminal_gap_curve_paints) < 16:
                try:
                    scene = page.visualization.spectrum_scene
                    name = next((kind.value for kind, item in scene._curves.items()
                                 if item.curve is curve), None)
                    if scene.sweep_coverage.history.curve is curve:
                        name = "sweep_history"
                    x_data = getattr(curve, "xData", None)
                    y_data = getattr(curve, "yData", None)
                    pen = curve.opts.get("pen")
                    terminal_gap_curve_paints.append(dict(
                        name=name, paint_ms=(ended_ns - started_ns) / 1e6,
                        x_points=0 if x_data is None else len(x_data),
                        y_points=0 if y_data is None else len(y_data),
                        pen_style=None if pen is None else pen.style().name,
                        pen_width=None if pen is None else pen.widthF()))
                except Exception as error:
                    terminal_gap_curve_paints.append(dict(error=f"{type(error).__name__}: {error}"))

    def observed_set_sweep_line(waterfall, update):
        nonlocal last_waterfall_update_key, last_waterfall_upload_key
        nonlocal waterfall_upload_count
        key = waterfall_update_key(update)
        if page is not None and waterfall is page.visualization.waterfall_pane:
            last_waterfall_update_key = key
        before = waterfall.metrics.image_uploads
        result = original_set_sweep_line(waterfall, update)
        if (page is not None and waterfall is page.visualization.waterfall_pane
                and key is not None and waterfall.metrics.image_uploads > before):
            last_waterfall_upload_key = key
            waterfall_upload_count = waterfall.metrics.image_uploads
        return result

    def observed_render(workspace, state):
        nonlocal overflow
        if workspace is page and state.mode is AnalyzerMode.SWEEP:
            now = perf_counter_ns()
            snapshot = state.sweep_snapshot
            if snapshot is not None:
                if snapshot.progress is not None:
                    key = sweep_key(snapshot.progress)
                    if key is not None:
                        if len(model_partial_keys) < _MAX_IDENTITIES:
                            model_partial_keys.add(key)
                        else:
                            overflow += 1
                if snapshot.line is not None:
                    key = sweep_key(snapshot.line)
                    if key is not None:
                        if len(model_complete_keys) < _MAX_IDENTITIES:
                            model_complete_keys.add(key)
                        else:
                            overflow += 1
                        if key[3] == "complete":
                            if len(complete_models) < _MAX_IDENTITIES:
                                complete_models.setdefault(key[:3], now)
                            else:
                                overflow += 1
        return original_render(workspace, state)

    def waterfall_key(waterfall):
        stamps = waterfall._time_axis._sweep_stamps
        stamp = stamps[-1] if stamps else None
        return uploaded_waterfall_key(
            attempted=last_waterfall_update_key, uploaded=last_waterfall_upload_key,
            stamp=stamp, uploads=waterfall.metrics.image_uploads, upload_count=waterfall_upload_count)

    class MeasuredGraphics(pg.GraphicsLayoutWidget):
        def paintEvent(self, event):
            nonlocal overflow, terminal_gap_profile, curve_profile_active
            paint_started_ns = perf_counter_ns()
            target = None
            if page is not None:
                spectrum = page.visualization.spectrum_scene
                waterfall = page.visualization.waterfall_pane
                if self is spectrum._graphics:
                    frame = spectrum.displayed_frame
                    target = ("spectrum", sweep_key(None if frame is None else frame.spectrum),
                              (spectrum._curves[TraceKind.CURRENT].curve,))
                elif self is waterfall._graphics:
                    target = ("waterfall", waterfall_key(waterfall),
                              tuple(item for item in waterfall._image_items if item.isVisible()))
            profile_this_paint = bool(args.profile_terminal_gap and terminal_gap_profile is None
                                      and target is not None and target[0] == "spectrum"
                                      and target[1] is not None and target[1][3] == "gap")
            profiler = cProfile.Profile() if profile_this_paint else None
            if profiler is not None:
                profiler.enable()
                curve_profile_active = True
            try:
                super().paintEvent(event)
            finally:
                if profiler is not None:
                    curve_profile_active = False
                    profiler.disable()
            paint_ended_ns = perf_counter_ns()
            if profiler is not None:
                buffer = io.StringIO()
                pstats.Stats(profiler, stream=buffer).strip_dirs().sort_stats("cumulative").print_stats(35)
                terminal_gap_profile = buffer.getvalue()
            if target is None or target[1] is None or not target[2] or page is None:
                return
            pane, key, items = target
            scene = page.visualization.spectrum_scene
            if pane == "spectrum":
                after = scene.displayed_frame
                after_key = sweep_key(None if after is None else after.spectrum)
            else:
                after_key = waterfall_key(page.visualization.waterfall_pane)
            if after_key != key or not any(paint_intersects_item(self, event, item) for item in items):
                return
            identity = (*key, pane)
            if identity in painted_keys:
                return
            if len(painted_keys) >= _MAX_IDENTITIES or len(events) >= _MAX_IDENTITIES:
                overflow += 1
                return
            painted_keys.add(identity)
            events.append(dict(pane=pane, key=list(key), when_ns=paint_ended_ns,
                               paint_started_ns=paint_started_ns,
                               paint_ms=(paint_ended_ns - paint_started_ns) / 1e6,
                               coverage_runs=len(scene.sweep_coverage.strip.runs)))

    with (patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics),
          (patch.object(pg.PlotCurveItem, "paint", observed_curve_paint)
           if args.profile_terminal_gap else nullcontext()),
          patch.object(AnalyzerWorkspaceV2, "_render", observed_render),
          patch.object(WaterfallPane, "set_sweep_line", observed_set_sweep_line)):
        app = QApplication.instance() or QApplication([])
        app.setQuitOnLastWindowClosed(False)
        settings_namespace = dict(organization="SDR Native Monitoring Bench",
                                  domain="local.sdr-native-monitoring-bench",
                                  application="APP05 Physical Sweep Observer")
        app.setOrganizationName(settings_namespace["organization"])
        app.setOrganizationDomain(settings_namespace["domain"])
        app.setApplicationName(settings_namespace["application"])
        services = build_default_sdr_services()
        if not isinstance(services.live_sdr, NativeLiveSessionService):
            raise RuntimeError("native Pluto service unavailable; no RX was attempted")
        shell = build_v2_shell(services)
        shell.set_theme(ThemeId(args.theme))
        shell.resize(args.window_width, args.window_height)
        shell.show()
        page = shell._workspace_pages["analyzer"]
        composition = shell._context.close_ports[0].shutdown.__self__
        presenter = composition.analyzer_presenter

        def fail(message: str) -> None:
            nonlocal error, phase, deadline_ns
            error = error or message
            print(f"APP05_SWEEP_FAILURE {message}", file=sys.stderr, flush=True)
            if phase not in ("stop", "wait_stopped", "wait_closed", "done"):
                phase = "stop"
                deadline_ns = perf_counter_ns() + 15_000_000_000

        def tick() -> None:
            nonlocal phase, deadline_ns, preview, applied, start_ack_ns
            nonlocal first_pair, stop_intent_ns, stop_click_return_ns
            nonlocal stop_ack_ns, terminal_snapshot, error, last_logged_phase
            nonlocal last_tick_ns, max_tick_gap_ms
            nonlocal capture_status
            now = perf_counter_ns()
            if last_tick_ns is not None:
                max_tick_gap_ms = max(max_tick_gap_ms, (now - last_tick_ns) / 1e6)
            last_tick_ns = now
            state = page.model.state
            if phase != last_logged_phase:
                phase_events.append(dict(phase=phase, when_ns=now))
                print(f"APP05_SWEEP_PHASE {now} {phase}", file=sys.stderr, flush=True)
                last_logged_phase = phase
            if now > deadline_ns and phase not in ("done", "stop", "wait_stopped", "wait_closed"):
                fail(f"phase {phase} exceeded its deadline")
            if overflow:
                fail("bounded Sweep observer identity/event capacity exceeded")
            try:
                if phase == "select":
                    page.drawer._uri.setText(args.uri)
                    page.drawer._use_uri.click()
                    phase = "wait_selected"
                elif phase == "wait_selected":
                    snapshot = state.live.snapshot
                    if snapshot is not None and snapshot.device is not None and not state.live.busy:
                        page.drawer._sample_rate.setValue(args.sample_rate_msps)
                        page.drawer._fft.setValue(args.fft)
                        cpu = page.drawer._backend.findData(BackendKind.CPU.value)
                        if cpu < 0:
                            fail("selected device has no CPU backend")
                        else:
                            page.drawer._backend.setCurrentIndex(cpu)
                            page.drawer._apply.click()
                            phase = "wait_applied"
                elif phase == "wait_applied":
                    applied = getattr(state.live.snapshot, "applied", None)
                    if applied is not None and not state.live.busy and not state.configuration_pending:
                        if not applied_matches_request(applied, sample_rate_msps=args.sample_rate_msps,
                                                       fft=args.fft):
                            fail("SDR applied Fs/FFT/backend differs from the requested CPU configuration")
                            return
                        page.mode.setCurrentIndex(page.mode.findData(AnalyzerMode.SWEEP))
                        page.start_frequency.setValue(args.start_mhz)
                        page.stop_frequency.setValue(args.stop_mhz)
                        if (not math.isclose(page.start_frequency.value(), args.start_mhz, abs_tol=1e-9)
                                or not math.isclose(page.stop_frequency.value(), args.stop_mhz, abs_tol=1e-9)):
                            fail("Sweep frequency controls clamped the requested range")
                            return
                        page.drawer.sweep_profile.choice.setCurrentIndex(
                            page.drawer.sweep_profile.choice.findData(SweepSpeedProfile.AVERAGED.value))
                        page.sweep_preview.resolve()
                        phase = "wait_preview"
                elif phase == "wait_preview":
                    preview = page.sweep_preview.result
                    if page.sweep_preview.has_error:
                        fail(f"Sweep preflight rejected: {page.sweep_preview.summary.text()}")
                    elif preview is not None and page.primary.isEnabled():
                        page.primary.click()
                        phase = "wait_running"
                        deadline_ns = now + 20_000_000_000
                elif phase == "wait_running":
                    if state.error:
                        fail(f"Sweep Start rejected: {state.error}")
                    elif state.running and not state.starting:
                        start_ack_ns = now
                        phase = "measure"
                        deadline_ns = now + int(args.run_timeout * 1e9)
                elif phase == "measure":
                    if state.error:
                        fail(f"Sweep failed: {state.error}")
                    elif not state.running:
                        fail("physical Sweep stopped before the partial/final witness")
                    else:
                        first_pair = first_progressive_pair(events, complete_models)
                        if first_pair is not None:
                            phase = "stop"
                            deadline_ns = now + 15_000_000_000
                elif phase == "stop":
                    if state.running or state.stop_required:
                        if not state.starting and not state.stopping:
                            stop_intent_ns = perf_counter_ns()
                            page.primary.click()
                            stop_click_return_ns = perf_counter_ns()
                            phase = "wait_stopped"
                    elif not state.starting and not state.stopping:
                        phase = "wait_stopped"
                elif phase == "wait_stopped":
                    if not state.running and not state.stopping and composition.can_close():
                        stop_ack_ns = now
                        terminal_snapshot = state.sweep_snapshot
                        if capture_path is not None:
                            try:
                                pixmap = shell.grab()
                                if pixmap.isNull() or not pixmap.save(str(capture_path), "PNG"):
                                    raise OSError("QWidget.grab/save returned no image")
                                capture_status = dict(path=str(capture_path),
                                                      sha256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                                                      width=pixmap.width(), height=pixmap.height())
                            except (OSError, RuntimeError) as caught:
                                error = error or f"post-Stop QWidget surface capture failed: {caught}"
                        shell.close()
                        phase = "wait_closed"
                elif phase == "wait_closed":
                    if shell._is_closed:
                        phase = "done"
                        timer.stop()
                        app.quit()
            except Exception as caught:
                fail(f"{type(caught).__name__}: {caught}")
            if phase in ("stop", "wait_stopped", "wait_closed") and now > deadline_ns:
                error = error or f"cleanup phase {phase} exceeded its deadline"
                print(f"APP05_SWEEP_TIMEOUT {error}; shell_closed={shell._is_closed}; "
                      f"running={state.running}; stopping={state.stopping}; "
                      f"presenter_can_close={presenter.can_close()}; "
                      f"all_ports_can_close={composition.can_close()}; "
                      "continuing safe Stop/close polling",
                      file=sys.stderr, flush=True)
                # Never quit the Qt loop while a receiver or close port may
                # still own work. The explicit Stop path above remains live.
                deadline_ns = now + 10_000_000_000

        timer = QTimer()
        timer.setInterval(25)
        timer.timeout.connect(tick)
        timer.start()
        app.exec()
        timer.stop()
        if phase == "wait_closed" and shell._is_closed:
            phase = "done"
        if phase != "done":
            error = error or f"incomplete shutdown at phase {phase}"
        screen = shell.screen()
        budget = composition.allocation_budget.snapshot()
        workers = [thread.name for thread in threading.enumerate()
                   if thread is not threading.main_thread()]
        first_pair = first_pair or first_progressive_pair(events, complete_models)
        terminal_key = sweep_key(None if terminal_snapshot is None else terminal_snapshot.line)
        gap_paints = terminal_gap_paints(events, terminal_key, stop_intent_ns)
        checks = dict(start_acknowledged=start_ack_ns is not None,
                      preview_valid=preview is not None,
                      partial_modeled=bool(model_partial_keys),
                      complete_modeled=bool(model_complete_keys),
                      partial_and_final_painted=first_pair is not None,
                      pair_precedes_stop=bool(first_pair is not None and stop_intent_ns is not None
                                              and all(first_pair["complete"][pane]["when_ns"] < stop_intent_ns
                                                      for pane in ("spectrum", "waterfall"))),
                      post_stop_gap_painted=gap_paints is not None,
                      native_terminal_gap=bool(terminal_snapshot is not None
                                               and terminal_snapshot.metrics.terminal_control_gaps >= 1),
                      exact_applied_configuration=bool(applied is not None and applied_matches_request(
                          applied, sample_rate_msps=args.sample_rate_msps, fft=args.fft)),
                      stopped_and_closed=phase == "done" and composition.can_close(),
                      bounded_presentation=budget.reserved_bytes == 0 and not workers and overflow == 0)
        report = dict(schema="app05-physical-sweep-ui-v1",
                      result="pass" if all(checks.values()) and not error else "fail",
                      error=error, phase=phase, checks=checks, source="physical-pluto-rx", uri=args.uri,
                      source_commit=source_commit, tracked_dirty_paths=tracked_dirty_paths,
                      script_sha256=script_hash, runtime=dict(python=sys.version, executable=sys.executable,
                          pyqtgraph=pg.__version__, pyside=PySide6.__version__, native_binary=native_binary),
                      settings_namespace=settings_namespace,
                      requested=dict(start_mhz=args.start_mhz, stop_mhz=args.stop_mhz,
                                     sample_rate_msps=args.sample_rate_msps, fft=args.fft,
                                     run_timeout_s=args.run_timeout, profile="averaged", window_mhz=36,
                                     overlap_mhz=2, theme=args.theme),
                      applied=None if applied is None else dict(sample_rate_hz=applied.applied.sample_rate_hz,
                                                                 fft_size=applied.applied.fft_size,
                                                                 backend=applied.applied.backend.value),
                      preview=None if preview is None else dict(segments=preview.segment_count,
                                                                 output_bins=preview.reduced.output_bins),
                      window=dict(logical_width=shell.width(), logical_height=shell.height(),
                                  dpr=shell.devicePixelRatioF(),
                                  screen_name=None if screen is None else screen.name()),
                      modeled=dict(partials=len(model_partial_keys), completes=len(model_complete_keys)),
                      first_pair=first_pair, stop_intent_ns=stop_intent_ns,
                      stop_click_return_ns=stop_click_return_ns,
                      stop_ack_ns=stop_ack_ns,
                      stop_handler_ms=(None if stop_intent_ns is None or stop_click_return_ns is None
                                       else (stop_click_return_ns - stop_intent_ns) / 1e6),
                      stop_ack_ms=(None if stop_intent_ns is None or stop_ack_ns is None
                                   else (stop_ack_ns - stop_intent_ns) / 1e6),
                      max_tick_gap_ms=max_tick_gap_ms,
                      post_stop_gap_paints=gap_paints,
                      post_stop_gap_paint_ms=(None if gap_paints is None or stop_intent_ns is None else
                                              {pane: (event["when_ns"] - stop_intent_ns) / 1e6
                                               for pane, event in gap_paints.items()}),
                      phase_events=phase_events,
                      terminal=None if terminal_snapshot is None else dict(
                          line_key=sweep_key(terminal_snapshot.line),
                          progress_key=sweep_key(terminal_snapshot.progress),
                          metrics=asdict(terminal_snapshot.metrics)),
                      paint_events=events, paint_overflow=overflow,
                      terminal_gap_profile=terminal_gap_profile,
                      terminal_gap_curve_paints=terminal_gap_curve_paints,
                      qwidget_surface_capture=capture_status,
                      allocation_budget=asdict(budget), workers_after_close=workers,
                      scope="One opt-in physical continuous-Sweep pass on visible Qt. Same-pass partial/final "
                            "paint-return is not exact pixels, DWM/scanout, RF timestamp or sustained LPS. "
                            "Completed-line LPS is not analytical FFT LPS. No product source is modified.")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps(dict(result=report["result"], output=str(output), checks=checks,
                          error=report["error"]), ensure_ascii=False))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
