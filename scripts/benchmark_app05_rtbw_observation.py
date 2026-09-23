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
The optional persistence catch-up pulse pauses only synthetic publications while
the Live owner and Qt event loop remain active; its bounded drain is not a Live
stop, per-update delivery guarantee, native persistence rate or RF evidence.
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


def persistence_caught_up(state, target_update_sequence):
    """Require exact latest accepted density to be the uploaded image and idle."""
    return bool(
        state["last_viewmodel_update"] == target_update_sequence
        and state["latest_density_update_sequence"] == target_update_sequence
        and state["uploaded_density_update_sequence"] == target_update_sequence
        and state["latest_view_is_latest_accepted_density"]
        and state["latest_view_is_uploaded"]
        and not state["worker_request_pending"]
        and not state["pending_view"]
    )


def persistence_catchup_gate_passed(pulses, requested_repetitions):
    """Require every frozen pulse to settle exactly, monotonically and on time."""
    return bool(len(pulses) == requested_repetitions and all(
        pulse["deadline_met"]
        and pulse["no_stale_upload_observed"] is True
        and pulse["source_publications_during_drain"] == 0
        and pulse["persistence_updates_during_drain"] == 0
        and pulse["accepted_update_sequence_at_end"] == pulse["target_update_sequence"]
        and pulse["latest_view_update_sequence_at_end"] == pulse["target_update_sequence"]
        and pulse["uploaded_update_sequence_at_end"] == pulse["target_update_sequence"]
        and not pulse["final_worker_request_pending"]
        and not pulse["final_pending_view"]
        for pulse in pulses))


def persistence_uploads_monotonic(start_sequence, events, end_sequence, expected_uploads):
    """Validate every successful ImageItem commit, not sampled timer snapshots."""
    sequences = [start_sequence]
    sequences.extend(event.get("uploaded_update_sequence") for event in events)
    sequences.append(end_sequence)
    return bool(len(events) == expected_uploads
        and all(isinstance(sequence, int) for sequence in sequences)
        and all(after >= before for before, after in zip(sequences, sequences[1:])))


def persistence_upload_events_since_counter(events, start_counter, end_counter):
    """Use the GUI-observed counter snapshot as the exact event-log boundary."""
    return [event for event in events if start_counter < event["counter"] <= end_counter]


def persistence_catchup_exit_code(result):
    """Keep a failed catch-up report on disk, but never return success for it."""
    catchup = result.get("persistence_catchup")
    return 0 if catchup is None or catchup.get("freshness_gate_passed") is True else 1


def _rect_xywh(rect):
    return [round(float(rect.x()), 3), round(float(rect.y()), 3),
            round(float(rect.width()), 3), round(float(rect.height()), 3)]


def qt_display_metadata(shell, app, scene, waterfall, *, requested_size, requested_platform):
    """Describe the actual Qt target; never equate QWidget paint with DWM scanout."""
    window = shell.windowHandle()
    screen = window.screen() if window is not None else app.primaryScreen()
    if screen is None:
        raise AssertionError("Qt did not expose a screen for the benchmark window")
    screen_dpr = float(screen.devicePixelRatio())
    logical_dpi = float(screen.logicalDotsPerInch())
    spectrum = scene._graphics
    water = waterfall._graphics
    spectrum_rect = scene.view_box.sceneBoundingRect()
    water_rect = waterfall.view_box.sceneBoundingRect()
    return dict(
        requested_platform=requested_platform,
        actual_platform=str(app.platformName()),
        qt_widget_visible=bool(shell.isVisible()),
        visible_native_window=bool(str(app.platformName()).casefold() == "windows" and shell.isVisible()),
        window_maximized=bool(shell.isMaximized()),
        requested_window_size_logical=list(requested_size),
        actual_window_geometry_logical=_rect_xywh(shell.geometry()),
        screen_name=str(screen.name()),
        screen_geometry_logical=_rect_xywh(screen.geometry()),
        screen_available_geometry_logical=_rect_xywh(screen.availableGeometry()),
        screen_device_pixel_ratio=screen_dpr,
        screen_logical_dpi=logical_dpi,
        screen_physical_dpi=float(screen.physicalDotsPerInch()),
        windows_scale_estimate_percent=round(logical_dpi / 96 * 100, 1),
        spectrum_canvas_logical_size=[int(spectrum.width()), int(spectrum.height())],
        spectrum_canvas_dpr=float(spectrum.devicePixelRatioF()),
        spectrum_plot_scene_rect=_rect_xywh(spectrum_rect),
        spectrum_plot_estimated_physical_size=[round(spectrum_rect.width() * spectrum.devicePixelRatioF()),
                                               round(spectrum_rect.height() * spectrum.devicePixelRatioF())],
        waterfall_canvas_logical_size=[int(water.width()), int(water.height())],
        waterfall_canvas_dpr=float(water.devicePixelRatioF()),
        waterfall_plot_scene_rect=_rect_xywh(water_rect),
        waterfall_plot_estimated_physical_size=[round(water_rect.width() * water.devicePixelRatioF()),
                                                round(water_rect.height() * water.devicePixelRatioF())],
        scope="Qt widget/window paint target only; not DWM/compositor scanout or SDR cadence")


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
    parser.add_argument("--seconds", type=float, default=5,
                        help="Stop delay for ordinary cycles; per steady block when --persistence-abba is enabled")
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
    parser.add_argument("--persistence-display", choices=("direct", "visual"), default="direct",
                        help="Select the actual V2 persistence rendering policy for normal paint profiling")
    parser.add_argument("--persistence-abba", action="store_true",
                        help="Visible single-session Direct→Visual→Visual→Direct matched blocks; --seconds is per steady block")
    parser.add_argument("--persistence-catchup", action="store_true",
                        help="Visible Visual-only burst/freeze/drain freshness pulse; --seconds is each burst")
    parser.add_argument("--catchup-repetitions", type=int, default=3,
                        help="Independent synthetic burst/freeze/drain pulses; default 3")
    parser.add_argument("--catchup-deadline-ms", type=int, default=1000,
                        help="Predeclared maximum Qt-observed quiescent drain time per pulse; default 1000ms")
    parser.add_argument("--abba-warmup-updates", type=int, default=8,
                        help="Fresh accepted AND uploaded persistence updates to wait after Start/mode change")
    parser.add_argument("--abba-warmup-seconds", type=float, default=1.0,
                        help="Minimum excluded warm-up duration after Start/mode change")
    parser.add_argument("--qt-platform", choices=("offscreen", "windows"), default="offscreen",
                        help="Explicit windows mode creates a visible desktop Qt window; offscreen remains the default")
    parser.add_argument("--window-size", nargs=2, type=int, metavar=("WIDTH", "HEIGHT"),
                        default=(1920, 1080), help="Requested logical client size for this benchmark run")
    parser.add_argument("--capture-window", action="store_true",
                        help="Save a post-measurement QWidget capture beside the JSON; requires visible Windows Qt")
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
    if any(value < 640 or value > 8192 for value in args.window_size):
        parser.error("window dimensions must be in 640..8192 logical pixels")
    if args.qt_platform == "windows" and sys.platform != "win32":
        parser.error("the visible Windows Qt platform is available only on Windows")
    if args.persistence_abba and args.qt_platform != "windows":
        parser.error("--persistence-abba requires --qt-platform windows")
    if args.persistence_catchup and args.qt_platform != "windows":
        parser.error("--persistence-catchup requires --qt-platform windows")
    if args.persistence_abba and args.persistence_catchup:
        parser.error("--persistence-abba and --persistence-catchup are separate experiments")
    if args.persistence_abba and (args.cycles != 1 or not args.persistence_power_bins
            or args.persistence_display != "direct"
            or args.page_seconds or args.viewport_seconds or args.driver_stop_ms
            or args.stop_phase != "any" or args.memory_seconds or args.collect_after_context):
        parser.error("ABBA requires one cycle starting in Direct, generated persistence, and no churn, delayed Stop, or memory sampling")
    if args.persistence_abba and (args.seconds > 120 or not 1 <= args.abba_warmup_updates <= 1000
            or not 0 <= args.abba_warmup_seconds <= 60):
        parser.error("ABBA steady blocks must be <=120s; warm-up updates 1..1000 and seconds 0..60")
    if args.persistence_catchup and (args.cycles != 1 or not args.persistence_power_bins
            or args.persistence_display != "visual" or args.page_seconds or args.viewport_seconds
            or args.driver_stop_ms or args.stop_phase != "any" or args.memory_seconds
            or args.collect_after_context):
        parser.error("persistence catch-up requires one Visual session with generated persistence and no churn, delayed Stop, or memory sampling")
    if args.persistence_catchup and (args.seconds > 30 or not 1 <= args.catchup_repetitions <= 10
            or not 50 <= args.catchup_deadline_ms <= 10000
            or not 1 <= args.abba_warmup_updates <= 1000
            or not 0 <= args.abba_warmup_seconds <= 60):
        parser.error("catch-up requires bursts <=30s, repetitions 1..10, deadline 50..10000ms, and warm-up updates 1..1000 / seconds 0..60")
    if args.capture_window and args.qt_platform != "windows":
        parser.error("--capture-window requires --qt-platform windows")
    capture_path = args.output.with_suffix(".png")
    if args.capture_window and capture_path.exists():
        parser.error("window capture path must be new")
    if args.persistence_display == "visual" and not args.persistence_power_bins:
        parser.error("Visual persistence profiling requires a generated histogram")
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
    os.environ["QT_QPA_PLATFORM"] = args.qt_platform
    import numpy as np
    import PySide6
    import pyqtgraph as pg
    from PySide6.QtCore import QTimer, Qt
    from sdr_monitor.domain.live import LiveSpectrumFrame
    from sdr_monitor.ui.v2.spectrum.persistence_contracts import PersistenceRenderMode
    from sdr_monitor.ui.v2.spectrum.persistence_overlay import PersistenceOverlay
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
    last_persistence_density_identity = None
    persistence_density_sequence_by_identity = {}
    persistence_identity_order = deque()
    persistence_generated_at, persistence_accepted_at = {}, {}
    persistence_upload_probe = dict(overlay=None, events=None)
    persistence_generated_order = deque()
    producer = None
    halt = threading.Event()
    producer_pause_requested, producer_paused = threading.Event(), threading.Event()
    snapshot_lock = threading.RLock()
    original_upload = WaterfallPane._upload_tiles
    original_accept_persistence_image = PersistenceOverlay.accept_worker_image
    original_start_method, original_stop_method = _AtomicFakeLive.start, _AtomicFakeLive.stop
    control = {}
    control_phase, resumed_until = "idle", 0.0
    abba_current_block = None
    abba_order = ("A1", "B1", "B2", "A2")
    abba_blocks = {
        name: dict(started_s=None, ended_s=None, mode=None, overlay_start=None, overlay_end=None,
            started_perf=None, ended_perf=None,
            generated_start=None, generated_end=None,
            waterfall_uploads_start=None, waterfall_uploads_end=None,
            source_to_first_paint_ms={target: deque(maxlen=timing_capacity)
                                      for target in age.ages},
            paint_return_ms={target: deque(maxlen=timing_capacity) for target in paints},
            boundary_excluded_paints=dict.fromkeys(age.ages, 0),
            heartbeat_times_s=deque(maxlen=timing_capacity),
            persistence_update_sequences=deque(maxlen=timing_capacity),
            first_displayed_sequence=None, last_displayed_sequence=None)
        for name in abba_order}
    abba_warmups, abba_transitions = [], []
    source_session = {}
    persistence_catchup = None
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

    def accept_persistence_image(overlay, request, result, error):
        before = overlay.metrics.image_uploads
        accepted = original_accept_persistence_image(overlay, request, result, error)
        events = persistence_upload_probe["events"]
        if (overlay is persistence_upload_probe["overlay"] and events is not None
                and overlay.metrics.image_uploads > before):
            latest = overlay.latest_view
            events.append(dict(perf_time=perf_counter(), counter=overlay.metrics.image_uploads,
                uploaded_update_sequence=persistence_density_sequence_by_identity.get(
                    id(overlay._uploaded_density)),
                latest_update_sequence=(None if latest is None else
                    persistence_density_sequence_by_identity.get(id(latest.density))),
                accepted_update_sequence=last_persistence_accepted,
                worker_request_pending=overlay.worker_request is not None,
                pending_view=overlay._pending_view is not None))
        return accepted

    def source_publication_time(key):
        with age.lock:
            publication = age.sources.get(key)
            return None if publication is None else publication[0]

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
                if abba_current_block is not None:
                    abba_blocks[abba_current_block]["paint_return_ms"][name].append((ended - began) * 1000)
                if key == current():
                    counts_before = dict(age.counts)
                    age.painted(name, key, ended)
                    phase = paint_phase(control_phase, self.isVisible(), ended, resumed_until)
                    for target in age.counts:
                        if age.counts[target] != counts_before[target]:
                            phase_ages[phase][target].append(age.ages[target][-1])
                            phase_counts[phase][target] += 1
                            if abba_current_block is not None:
                                block = abba_blocks[abba_current_block]
                                published_at = source_publication_time(key)
                                if published_at is not None and published_at >= block["started_perf"]:
                                    block["source_to_first_paint_ms"][target].append(age.ages[target][-1])
                                    # Synthetic timestamp token equals sequence; the current
                                    # key is the exact spectrum token or uploaded waterfall row.
                                    sequence = int(key[0])
                                    if block["first_displayed_sequence"] is None:
                                        block["first_displayed_sequence"] = sequence
                                    block["last_displayed_sequence"] = sequence
                                else:
                                    block["boundary_excluded_paints"][target] += 1
                else:
                    age.changed_during_paint += 1

    with patch.object(pg, "GraphicsLayoutWidget", MeasuredGraphics), \
         patch.object(WaterfallPane, "_upload_tiles", upload), \
         patch.object(PersistenceOverlay, "accept_worker_image", accept_persistence_image), \
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
            f.shell.resize(*args.window_size)
            scene, waterfall = f.page.visualization.spectrum_scene, f.page.visualization.waterfall_pane
            scene.set_persistence_render_mode(PersistenceRenderMode(args.persistence_display))
            persistence_upload_probe["overlay"] = scene._persistence

            def observe_density(state):
                nonlocal persistence_accepted, last_persistence_accepted, last_persistence_density_identity
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
                    last_persistence_density_identity = id(density.density)
                    persistence_accepted_at[last_persistence_accepted] = perf_counter()
                    persistence_density_sequence_by_identity[last_persistence_density_identity] = last_persistence_accepted
                    persistence_identity_order.append((last_persistence_density_identity, last_persistence_accepted))
                    while len(persistence_identity_order) > 2048:
                        old_identity, old_sequence = persistence_identity_order.popleft()
                        if persistence_density_sequence_by_identity.get(old_identity) == old_sequence:
                            del persistence_density_sequence_by_identity[old_identity]
                        persistence_generated_at.pop(old_sequence, None)
                        persistence_accepted_at.pop(old_sequence, None)
                    if abba_current_block is not None:
                        abba_blocks[abba_current_block]["persistence_update_sequences"].append(
                            last_persistence_accepted)

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
                start_count = int(source_session.get("start_count", 0)) + 1
                source_session.update(start_count=int(source_session.get("start_count", 0)) + 1,
                    config_generation=int(snapshot.generation),
                    live_snapshot_acquisition_epoch=getattr(snapshot, "acquisition_epoch", None),
                    receiver_id=getattr(snapshot, "receiver_id", None),
                    source_id="fake-pluto-usb",
                    synthetic_frame_receiver_id="synthetic-receiver"
                        if args.persistence_abba or args.persistence_catchup else None,
                    synthetic_frame_acquisition_epoch=start_count
                        if args.persistence_abba or args.persistence_catchup else None,
                    validated_display_frames=0)
                with snapshot_lock:
                    f.live._snapshot = snapshot
                halt.clear()
                producer_pause_requested.clear()
                producer_paused.clear()
                def produce():
                    nonlocal generated, persistence_generated
                    cycle_frames = 0
                    density = None
                    try:
                        while not halt.is_set():
                            if producer_pause_requested.is_set():
                                producer_paused.set()
                                halt.wait(.005)
                                continue
                            producer_paused.clear()
                            generated += 1
                            cycle_frames += 1
                            frame = LiveSpectrumFrame(sequence=generated, timestamp_ns=generated,
                                source_id="fake-pluto-usb", config_generation=snapshot.generation,
                                center_frequency_hz=config.center_hz, sample_rate_hz=config.sample_rate_hz,
                                fft_size=args.bins, hop_size=args.bins, frequencies_hz=frequencies, values=values,
                                receiver_id=source_session.get("synthetic_frame_receiver_id"),
                                acquisition_epoch=source_session.get("synthetic_frame_acquisition_epoch"))
                            if persistence_enabled and (cycle_frames == 1 or cycle_frames % args.persistence_every == 0):
                                persistence_generated += 1
                                density = synthetic_persistence(frame, args.persistence_power_bins,
                                    persistence_generated, min(cycle_frames, 4))
                                persistence_generated_at[persistence_generated] = perf_counter()
                                persistence_generated_order.append(persistence_generated)
                                if len(persistence_generated_order) > 4096:
                                    persistence_generated_at.pop(persistence_generated_order.popleft(), None)
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
                if args.persistence_abba or args.persistence_catchup:
                    expected = (source_session.get("config_generation"),
                                source_session.get("synthetic_frame_receiver_id"),
                                source_session.get("synthetic_frame_acquisition_epoch"))
                    actual = (int(frame.config_generation), frame.receiver_id, frame.acquisition_epoch)
                    if actual != expected:
                        raise AssertionError(("synthetic source identity changed", actual, expected))
                    if int(frame.sequence) != int(frame.timestamp_ns):
                        raise AssertionError("synthetic publication token no longer matches its frame sequence")
                    source_session["validated_display_frames"] += 1
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

            def heartbeat_tick():
                now = perf_counter()
                beats.append(now)
                if abba_current_block is not None:
                    abba_blocks[abba_current_block]["heartbeat_times_s"].append(now)

            heartbeat = timer(.01, heartbeat_tick)
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

            def persistence_state():
                overlay = scene._persistence
                latest = overlay.latest_view
                latest_sequence = (None if latest is None else
                    persistence_density_sequence_by_identity.get(id(latest.density)))
                uploaded_sequence = (None if overlay._uploaded_density is None else
                    persistence_density_sequence_by_identity.get(id(overlay._uploaded_density)))
                persistence_projector = getattr(scene._projector, "persistence_projector", None)
                return dict(mode=overlay.render_mode.value,
                    last_viewmodel_update=last_persistence_accepted,
                    latest_density_update_sequence=latest_sequence,
                    uploaded_density_update_sequence=uploaded_sequence,
                    latest_view_is_latest_accepted_density=bool(latest is not None
                        and id(latest.density) == last_persistence_density_identity),
                    latest_view_is_uploaded=bool(latest is not None
                        and overlay._uploaded_density is latest.density),
                    image_uploads=overlay.metrics.image_uploads,
                    worker_request_pending=overlay.worker_request is not None,
                    pending_view=overlay._pending_view is not None,
                    persistence_projector_active=bool(persistence_projector
                        and persistence_projector.has_active),
                    persistence_projector_pending=bool(persistence_projector
                        and persistence_projector.has_pending),
                    spectrum_projector_pending=bool(scene._projector and scene._projector.has_pending))

            def wait_for_persistence_warmup(label, mode, baseline_update, baseline_uploads):
                target_update = baseline_update + args.abba_warmup_updates
                began_warmup = perf_counter()
                earliest_ready = began_warmup + args.abba_warmup_seconds
                gate_snapshot = {}

                def ready():
                    state = persistence_state()
                    matched = (state["mode"] == mode.value
                        and last_persistence_accepted >= target_update
                        and state["latest_view_is_latest_accepted_density"]
                        and state["latest_view_is_uploaded"]
                        and state["image_uploads"] > baseline_uploads
                        and not state["worker_request_pending"] and not state["pending_view"]
                        and perf_counter() >= earliest_ready)
                    if matched:
                        gate_snapshot.update(state=state, at_s=perf_counter() - began)
                    return matched

                try:
                    run_qt_until(lambda: checked(ready), max(15, args.abba_warmup_seconds + 15))
                except TimeoutError as error:
                    raise TimeoutError(f"ABBA warm-up did not settle ({label}); "
                        f"target_update={target_update}, state={persistence_state()}") from error
                post_gate = persistence_state()
                gate_state = gate_snapshot.get("state")
                return dict(label=label, mode=mode.value,
                    began_s=began_warmup - began,
                    ended_s=perf_counter() - began,
                    duration_ms=(perf_counter() - began_warmup) * 1000,
                    baseline_accepted_update=baseline_update,
                    target_accepted_update=target_update,
                    baseline_image_uploads=baseline_uploads,
                    gate_satisfied_state=gate_state,
                    gate_satisfied_at_s=gate_snapshot.get("at_s"),
                    post_gate_state=post_gate,
                    superseded_after_gate=bool(gate_state
                        and (not post_gate["latest_view_is_uploaded"] or post_gate["pending_view"])))

            def transition_persistence_mode(mode, label):
                old_mode = scene._persistence.render_mode
                before = persistence_state()
                switch_started = perf_counter()
                scene.set_persistence_render_mode(mode)
                switch_returned = perf_counter()
                after_callback = persistence_state()
                baseline_update = last_persistence_accepted
                baseline_uploads = scene._persistence.metrics.image_uploads
                warmup = wait_for_persistence_warmup(label, mode, baseline_update, baseline_uploads)
                abba_transitions.append(dict(label=label, from_mode=old_mode.value,
                    to_mode=mode.value, began_s=switch_started - began,
                    callback_ms=(switch_returned - switch_started) * 1000,
                    before=before, after_callback=after_callback, warmup=warmup))

            def measure_abba_block(name, mode):
                nonlocal abba_current_block
                block = abba_blocks[name]
                if scene._persistence.render_mode.value != mode.value:
                    raise AssertionError((name, scene._persistence.render_mode, mode))
                block["mode"] = mode.value
                block["started_s"] = perf_counter() - began
                block["started_perf"] = perf_counter()
                block["generated_start"] = generated
                block["overlay_start"] = persistence_state()
                block["waterfall_uploads_start"] = waterfall.metrics.image_uploads
                due = perf_counter() + args.seconds
                abba_current_block = name
                run_qt_until(lambda: checked(lambda: perf_counter() >= due), args.seconds + 5)
                abba_current_block = None
                block["ended_s"] = perf_counter() - began
                block["ended_perf"] = perf_counter()
                block["generated_end"] = generated
                block["overlay_end"] = persistence_state()
                block["waterfall_uploads_end"] = waterfall.metrics.image_uploads
                for target in age.ages:
                    if len(block["source_to_first_paint_ms"][target]) < 2:
                        raise AssertionError(f"ABBA {name} block lacks two fresh {target} paint samples; "
                                             f"boundary_excluded={block['boundary_excluded_paints'][target]}")
                if len(block["persistence_update_sequences"]) < 2:
                    raise AssertionError(f"ABBA {name} block lacks fresh accepted persistence updates")

            def run_visible_abba():
                nonlocal control_phase, resumed_until
                if scene._persistence.render_mode is not PersistenceRenderMode.DIRECT:
                    raise AssertionError("ABBA must begin in Direct mode")
                f.shell.select_workspace("analyzer")
                control_phase = "starting"
                f.page.primary.click()
                run_qt_until(lambda: checked(lambda: f.live.is_running()
                    and not f.composition.view_model.state.busy), 5)
                control_phase = "running"
                resumed_until = perf_counter() + .25

                initial_warmup = wait_for_persistence_warmup("initial-direct", PersistenceRenderMode.DIRECT,
                    last_persistence_accepted, scene._persistence.metrics.image_uploads)
                abba_warmups.append(initial_warmup)
                measure_abba_block("A1", PersistenceRenderMode.DIRECT)
                transition_persistence_mode(PersistenceRenderMode.VISUAL, "Direct-to-Visual")
                measure_abba_block("B1", PersistenceRenderMode.VISUAL)
                # B1 and B2 form one continuous Visual interval; no second mode switch/reset.
                measure_abba_block("B2", PersistenceRenderMode.VISUAL)
                transition_persistence_mode(PersistenceRenderMode.DIRECT, "Visual-to-Direct")
                measure_abba_block("A2", PersistenceRenderMode.DIRECT)

                end_snapshot = f.live.latest_snapshot()
                end_live_epoch = getattr(end_snapshot, "acquisition_epoch", None)
                start_live_epoch = source_session.get("live_snapshot_acquisition_epoch")
                if (end_snapshot.generation != source_session.get("config_generation")
                        or (start_live_epoch is not None and end_live_epoch != start_live_epoch)):
                    raise AssertionError("source configuration/epoch changed during single-session ABBA")
                if producer is None or not producer.is_alive() or generated <= 0:
                    raise AssertionError("synthetic source did not remain active for the complete ABBA sequence")
                if (source_session.get("synthetic_frame_acquisition_epoch") is None
                        or source_session.get("validated_display_frames", 0) == 0):
                    raise AssertionError("no explicit synthetic frame receiver/epoch was validated on the canvas")
                source_session.update(end_config_generation=end_snapshot.generation,
                    end_live_snapshot_acquisition_epoch=end_live_epoch,
                    live_snapshot_epoch_comparable=start_live_epoch is not None,
                    final_publication_sequence=generated)

                stop_entered = perf_counter()
                if producer is None or not producer.is_alive():
                    raise AssertionError("Stop was not tested under an active producer after ABBA")
                stop_phase_observed = stop_phase(f.presenter, f.composition.spectrum_projector)
                control_phase = "stopping"
                f.page.primary.click()
                click_returned = perf_counter()
                run_qt_until(lambda: checked(lambda: not f.live.is_running()
                    and not f.composition.view_model.state.busy), 5)
                idle = perf_counter()
                if len(driver_entries) != 1 or len(driver_returns) != 1 or producer.is_alive():
                    raise AssertionError("ABBA must stop exactly one active producer exactly once")
                stops.append(dict(cycle=0, producer_active_at_intent=True,
                    phase_at_intent=stop_phase_observed, phase_observations=1,
                    generated_at_intent=generated, generated_at_idle=generated,
                    timer_lateness_ms=0.0, click_return_ms=(click_returned - stop_entered) * 1000,
                    intent_to_worker_ms=(driver_entries[-1][0] - stop_entered) * 1000,
                    driver_elapsed_ms=(driver_returns[-1] - driver_entries[-1][0]) * 1000,
                    driver_return_to_idle_ms=(idle - driver_returns[-1]) * 1000,
                    intent_to_idle_ms=(idle - stop_entered) * 1000,
                    worker_off_gui=driver_entries[-1][1],
                    heartbeat_ticks_during_driver_delay=sum(driver_entries[-1][0] < beat <
                        driver_entries[-1][0] + args.driver_stop_ms / 1000 for beat in beats)))
                control_phase = "idle"

            def run_visible_persistence_catchup():
                nonlocal control_phase, resumed_until
                mode = PersistenceRenderMode.VISUAL
                if scene._persistence.render_mode is not mode:
                    raise AssertionError("persistence catch-up pulse requires Visual mode")
                f.shell.select_workspace("analyzer")
                control_phase = "starting"
                f.page.primary.click()
                run_qt_until(lambda: checked(lambda: f.live.is_running()
                    and not f.composition.view_model.state.busy), 5)
                control_phase = "running"
                resumed_until = perf_counter() + .25
                warmups = [wait_for_persistence_warmup("catchup-start", mode,
                    last_persistence_accepted, scene._persistence.metrics.image_uploads)]
                pulses = []

                for pulse_index in range(args.catchup_repetitions):
                    burst_started = perf_counter()
                    generated_start = generated
                    accepted_start = last_persistence_accepted
                    uploads_start = scene._persistence.metrics.image_uploads
                    due = burst_started + args.seconds
                    run_qt_until(lambda: checked(lambda: perf_counter() >= due), args.seconds + 5)

                    upload_event_log = []
                    persistence_upload_probe["events"] = upload_event_log
                    pause_requested_at = perf_counter()
                    producer_pause_requested.set()
                    run_qt_until(lambda: checked(producer_paused.is_set), 2)
                    pause_acknowledged_at = perf_counter()
                    generated_at_pause = generated
                    persistence_generated_at_pause = persistence_generated
                    with snapshot_lock:
                        frozen_snapshot = f.live.latest_snapshot()
                    frozen_frame = frozen_snapshot.persistence
                    if frozen_frame is None:
                        raise AssertionError("producer freeze has no synthetic persistence frame")
                    target_update = int(frozen_frame.update_sequence)
                    if target_update != persistence_generated_at_pause:
                        raise AssertionError(("frozen persistence sequence mismatch",
                            target_update, persistence_generated_at_pause))

                    state_at_pause = persistence_state()
                    drain_started_at = perf_counter()
                    uploads_at_pause = state_at_pause["image_uploads"]
                    poll_count = 0

                    def catchup_poll():
                        nonlocal poll_count
                        state = persistence_state()
                        poll_count += 1
                        return persistence_caught_up(state, target_update)

                    settled = False
                    try:
                        run_qt_until(lambda: checked(catchup_poll), args.catchup_deadline_ms / 1000)
                        settled = True
                    except TimeoutError:
                        # A censored observation is valid evidence; do not turn it into
                        # a successful run or silently extend the preregistered bound.
                        catchup_poll()
                    settled_at = perf_counter()
                    state_at_end = persistence_state()
                    generated_during_drain = generated - generated_at_pause
                    persistence_generated_during_drain = persistence_generated - persistence_generated_at_pause
                    exact_drain_events = persistence_upload_events_since_counter(upload_event_log,
                        uploads_at_pause, state_at_end["image_uploads"])
                    upload_observations = [dict(
                        at_ms=(event["perf_time"] - drain_started_at) * 1000,
                        counter=event["counter"],
                        uploaded_update_sequence=event["uploaded_update_sequence"],
                        latest_update_sequence=event["latest_update_sequence"],
                        accepted_update_sequence=event["accepted_update_sequence"],
                        worker_request_pending=event["worker_request_pending"],
                        pending_view=event["pending_view"])
                        for event in exact_drain_events]
                    persistence_upload_probe["events"] = None
                    if generated_during_drain or persistence_generated_during_drain:
                        raise AssertionError(("synthetic source advanced after pause acknowledgement",
                            generated_during_drain, persistence_generated_during_drain))

                    upload_count_during_drain = max(0,
                        state_at_end["image_uploads"] - uploads_at_pause)
                    no_stale_upload_observed = persistence_uploads_monotonic(
                        state_at_pause["uploaded_density_update_sequence"], upload_observations,
                        state_at_end["uploaded_density_update_sequence"], upload_count_during_drain)
                    accepted_at_target = persistence_accepted_at.get(target_update)
                    generated_at_target = persistence_generated_at.get(target_update)
                    pulses.append(dict(
                        repetition=pulse_index + 1,
                        mode=mode.value,
                        burst_duration_ms=(pause_acknowledged_at - burst_started) * 1000,
                        source_publications=dict(count=generated_at_pause - generated_start,
                            rate_hz=(generated_at_pause - generated_start) /
                                max(.001, pause_acknowledged_at - burst_started)),
                        accepted_updates_during_burst=max(0, state_at_pause["last_viewmodel_update"] - accepted_start),
                        image_uploads_during_burst=max(0, uploads_at_pause - uploads_start),
                        pause_request_to_ack_ms=(pause_acknowledged_at - pause_requested_at) * 1000,
                        target_update_sequence=target_update,
                        accepted_update_sequence_at_freeze=state_at_pause["last_viewmodel_update"],
                        latest_view_update_sequence_at_freeze=state_at_pause["latest_density_update_sequence"],
                        uploaded_update_sequence_at_freeze=state_at_pause["uploaded_density_update_sequence"],
                        sequence_lag_at_freeze=(None if state_at_pause["uploaded_density_update_sequence"] is None
                            else target_update - state_at_pause["uploaded_density_update_sequence"]),
                        pause_ack_to_state_ms=(drain_started_at - pause_acknowledged_at) * 1000,
                        freeze_to_settled_ms=(settled_at - drain_started_at) * 1000,
                        pause_ack_to_settled_ms=(settled_at - pause_acknowledged_at) * 1000,
                        generated_to_settled_ms=(None if generated_at_target is None else
                            (settled_at - generated_at_target) * 1000),
                        accepted_to_settled_ms=(None if accepted_at_target is None else
                            (settled_at - accepted_at_target) * 1000),
                        deadline_ms=args.catchup_deadline_ms,
                        deadline_met=settled,
                        poll_count=poll_count,
                        poll_interval_ms=5,
                        upload_count_during_drain=upload_count_during_drain,
                        exact_upload_event_count=len(upload_observations),
                        upload_event_count_matches_counter=(len(upload_observations)
                            == upload_count_during_drain),
                        accepted_updates_during_drain=max(0,
                            state_at_end["last_viewmodel_update"] -
                            state_at_pause["last_viewmodel_update"]),
                        upload_observations=upload_observations,
                        accepted_update_sequence_at_end=state_at_end["last_viewmodel_update"],
                        latest_view_update_sequence_at_end=state_at_end["latest_density_update_sequence"],
                        uploaded_update_sequence_at_end=state_at_end["uploaded_density_update_sequence"],
                        sequence_lag_at_end=(None if state_at_end["uploaded_density_update_sequence"] is None
                            else target_update - state_at_end["uploaded_density_update_sequence"]),
                        no_stale_upload_observed=no_stale_upload_observed,
                        source_publications_during_drain=generated_during_drain,
                        persistence_updates_during_drain=persistence_generated_during_drain,
                        final_latest_is_uploaded=state_at_end["latest_view_is_uploaded"],
                        final_worker_request_pending=state_at_end["worker_request_pending"],
                        final_pending_view=state_at_end["pending_view"],
                        final_state=state_at_end))

                    if not settled:
                        break
                    if pulse_index + 1 < args.catchup_repetitions:
                        pause_baseline_update = last_persistence_accepted
                        pause_baseline_uploads = scene._persistence.metrics.image_uploads
                        producer_pause_requested.clear()
                        warmups.append(wait_for_persistence_warmup(
                            f"catchup-resume-{pulse_index + 1}", mode,
                            pause_baseline_update, pause_baseline_uploads))

                if producer is None or not producer.is_alive():
                    raise AssertionError("producer ended before catch-up Stop")
                if producer_pause_requested.is_set():
                    resume_baseline = generated
                    producer_pause_requested.clear()
                    run_qt_until(lambda: checked(lambda: generated > resume_baseline), 2)

                end_snapshot = f.live.latest_snapshot()
                end_live_epoch = getattr(end_snapshot, "acquisition_epoch", None)
                start_live_epoch = source_session.get("live_snapshot_acquisition_epoch")
                if (end_snapshot.generation != source_session.get("config_generation")
                        or (start_live_epoch is not None and end_live_epoch != start_live_epoch)):
                    raise AssertionError("source configuration/epoch changed during catch-up pulses")
                if (source_session.get("synthetic_frame_acquisition_epoch") is None
                        or source_session.get("validated_display_frames", 0) == 0):
                    raise AssertionError("no explicit synthetic frame receiver/epoch was validated")
                source_session.update(end_config_generation=end_snapshot.generation,
                    end_live_snapshot_acquisition_epoch=end_live_epoch,
                    live_snapshot_epoch_comparable=start_live_epoch is not None,
                    final_publication_sequence=generated)

                stop_entered = perf_counter()
                stop_phase_observed = stop_phase(f.presenter, f.composition.spectrum_projector)
                control_phase = "stopping"
                f.page.primary.click()
                click_returned = perf_counter()
                run_qt_until(lambda: checked(lambda: not f.live.is_running()
                    and not f.composition.view_model.state.busy), 5)
                idle = perf_counter()
                if len(driver_entries) != 1 or len(driver_returns) != 1 or producer.is_alive():
                    raise AssertionError("catch-up must stop exactly one synthetic Live producer once")
                stops.append(dict(cycle=0, producer_active_at_intent=True,
                    phase_at_intent=stop_phase_observed, phase_observations=1,
                    generated_at_intent=generated, generated_at_idle=generated,
                    timer_lateness_ms=0.0, click_return_ms=(click_returned - stop_entered) * 1000,
                    intent_to_worker_ms=(driver_entries[-1][0] - stop_entered) * 1000,
                    driver_elapsed_ms=(driver_returns[-1] - driver_entries[-1][0]) * 1000,
                    driver_return_to_idle_ms=(idle - driver_returns[-1]) * 1000,
                    intent_to_idle_ms=(idle - stop_entered) * 1000,
                    worker_off_gui=driver_entries[-1][1],
                    heartbeat_ticks_during_driver_delay=sum(driver_entries[-1][0] < beat <
                        driver_entries[-1][0] + args.driver_stop_ms / 1000 for beat in beats)))
                control_phase = "idle"
                freshness_gate_passed = persistence_catchup_gate_passed(
                    pulses, args.catchup_repetitions)
                return dict(mode=mode.value, requested_repetitions=args.catchup_repetitions,
                    completed_repetitions=len(pulses), burst_seconds=args.seconds,
                    deadline_ms=args.catchup_deadline_ms, warmups=warmups, pulses=pulses,
                    all_repetitions_settled=(len(pulses) == args.catchup_repetitions
                        and all(pulse["deadline_met"] for pulse in pulses)),
                    freshness_gate_passed=freshness_gate_passed,
                    source_session=dict(source_session, events=list(f.events), start_stop_count=len(stops)),
                    observation_scope="Visual-only synthetic publication freeze; Live owner/Qt loop remain active. Deadline is polled by a 5ms Qt timer and is not a hard watchdog. ImageItem upload identity is verified, not actual paint/DWM scanout. Intermediate persistence updates may be coalesced; no RF/DSP/LPS claim.")

            with patch.dict(control, start=start, stop=stop):
                if args.persistence_catchup:
                    persistence_catchup = run_visible_persistence_catchup()
                    if f.events != ["rtbw-start", "rtbw-stop"]:
                        raise AssertionError(f"catch-up unexpectedly started/stopped more than once: {f.events}")
                elif args.persistence_abba:
                    run_visible_abba()
                    if f.events != ["rtbw-start", "rtbw-stop"]:
                        raise AssertionError(f"ABBA unexpectedly started/stopped more than once: {f.events}")
                else:
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
                                    raise AssertionError(
                                        f"Requested Stop phase not observed: {args.stop_phase}; last={phase}")
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
                        if (len(driver_entries) != cycle + 1 or len(driver_returns) != cycle + 1
                                or producer.is_alive()):
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
            display_metadata = qt_display_metadata(f.shell, f.app, scene, waterfall,
                requested_size=args.window_size, requested_platform=args.qt_platform)
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
                qt_display_environment=display_metadata,
                preparation_superseded=f.presenter.preparation_superseded,
                preparation_stale=f.presenter.preparation_stale)
            report["persistence"] = dict(enabled=persistence_enabled,
                display_mode=args.persistence_display,
                power_bins=args.persistence_power_bins, every_source_frames=args.persistence_every,
                generated=persistence_generated, accepted=persistence_accepted,
                last_accepted_update=last_persistence_accepted,
                overlay_metrics=asdict(scene._persistence.metrics),
                scope="Synthetic rolling histogram of up to four identical spectra, fresh immutable array per update. Accepted and ImageItem uploads counted, not persistence paint/RF/native DSP FPS.")
            if args.persistence_abba:
                def abba_block_report(name):
                    block = abba_blocks[name]
                    heartbeat_intervals = np.diff(block["heartbeat_times_s"]) * 1000
                    updates = list(block["persistence_update_sequences"])
                    elapsed = block["ended_s"] - block["started_s"]
                    source_publications = block["generated_end"] - block["generated_start"]
                    persistence_upload_delta = block["overlay_end"]["image_uploads"] - \
                        block["overlay_start"]["image_uploads"]
                    waterfall_upload_delta = block["waterfall_uploads_end"] - block["waterfall_uploads_start"]
                    return dict(mode=block["mode"],
                        began_s=block["started_s"], ended_s=block["ended_s"],
                        elapsed_s=elapsed,
                        first_displayed_source_sequence=block["first_displayed_sequence"],
                        last_displayed_source_sequence=block["last_displayed_sequence"],
                        source_publications=dict(count=source_publications,
                            rate_hz=source_publications / elapsed,
                            first_sequence=block["generated_start"] + 1,
                            last_sequence=block["generated_end"]),
                        boundary_excluded_paint_counts=block["boundary_excluded_paints"],
                        source_to_first_paint_sample_counts={target: len(values)
                            for target, values in block["source_to_first_paint_ms"].items()},
                        source_to_first_paint_ms={target: summary(values) for target, values
                                                  in block["source_to_first_paint_ms"].items()},
                        paint_return_ms={target: summary(values) for target, values
                                         in block["paint_return_ms"].items()},
                        heartbeat_intervals_ms=summary(heartbeat_intervals),
                        heartbeat_tick_count=len(block["heartbeat_times_s"]),
                        accepted_persistence_updates=dict(count=len(updates),
                            first=None if not updates else updates[0],
                            last=None if not updates else updates[-1], rate_hz=len(updates) / elapsed),
                        persistence_image_uploads=dict(delta=persistence_upload_delta,
                            rate_hz=persistence_upload_delta / elapsed,
                            latest_density_uploaded=block["overlay_end"]["latest_view_is_uploaded"],
                            request_pending=block["overlay_end"]["worker_request_pending"],
                            pending_view=block["overlay_end"]["pending_view"]),
                        waterfall_image_uploads=dict(delta=waterfall_upload_delta,
                            rate_hz=waterfall_upload_delta / elapsed),
                        overlay_start=block["overlay_start"], overlay_end=block["overlay_end"])

                def combined_age(block_names, target):
                    parts = [np.asarray(abba_blocks[name]["source_to_first_paint_ms"][target])
                             for name in block_names]
                    parts = [part for part in parts if len(part)]
                    return summary(np.concatenate(parts)) if parts else None

                report["persistence_abba"] = dict(
                    sequence=[dict(block=name, mode=abba_blocks[name]["mode"])
                              for name in abba_order],
                    blocks={name: abba_block_report(name) for name in abba_order},
                    transitions=abba_transitions,
                    startup_warmup=abba_warmups,
                    combined_steady_source_to_first_paint_ms={
                        "direct": {target: combined_age(("A1", "A2"), target) for target in age.ages},
                        "visual": {target: combined_age(("B1", "B2"), target) for target in age.ages}},
                    source_session=dict(source_session, events=list(f.events), start_stop_count=len(stops),
                        frame_identity_scope="Explicit synthetic receiver/epoch labels attached to each observed LiveSpectrumFrame; not physical SDR provenance."),
                    interpretation="One synthetic Live session, Direct A1 -> Visual B1/B2 -> Direct A2. B1/B2 are contiguous halves of one Visual interval, not independent replications. Mode changes, accepted/uploaded update catch-up and declared warm-up are recorded separately and excluded from steady samples.",
                    limitations="Visible Windows Qt QWidget paint/heartbeat on one host display. Synthetic constant spectrum and rolling histogram only; not DWM/compositor scanout, SDR/RF/LPS, visual smoothing fidelity, physical FHD/QHD coverage, or a general performance acceptance.")
            if args.persistence_catchup:
                report["persistence_catchup"] = persistence_catchup
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
            if args.capture_window:
                image = f.shell.grab()
                if image.isNull() or not image.save(str(capture_path), "PNG"):
                    raise AssertionError("could not save the post-measurement Qt window capture")
                report["window_capture"] = dict(path=str(capture_path.resolve()),
                    sha256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                    size_px=[image.width(), image.height()],
                    scope="Post-measurement QWidget.grab after RTBW Stop; app surface, not desktop/compositor screenshot")
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
        host_platform=platform.platform(), processor=platform.processor(),
        platform=f"Qt {args.qt_platform}", logical_size=list(args.window_size),
        event_pump="QEventLoop.exec", product_imports_outside_checkout=outside,
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
    if persistence_catchup_exit_code(result):
        raise SystemExit(1)
