r"""Offscreen read-only benchmark of the complete GUI playback data path.

The benchmark opens a real DFL through ``MainWindow.load_file``, waits for the
streamed preview/index, enables Rolling Exact, runs timestamp playback and
reports the displayed, logical, analytical and rendered targets separately.
It never writes to the DFL and redirects QSettings/activity logging to a
temporary directory.

Example:
    .\.venv\Scripts\python.exe benchmark_gui_playback.py `
        "<path-to-reference.dfl>" --seconds 2
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QSettings, QThreadPool, QTimer
from PySide6.QtWidgets import QApplication

from esw_dfl.gui import MainWindow
from esw_dfl.heatmap_persistence import PersistenceMode


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dfl", type=Path)
    parser.add_argument("--seconds", type=float, default=2.0)
    parser.add_argument("--start-frame", type=int, default=5000)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--no-heatmap", action="store_true")
    return parser.parse_args()


def _wait_until(
    app: QApplication,
    predicate: Callable[[], bool],
    *,
    timeout_s: float,
) -> bool:
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.0005)
    app.processEvents()
    return predicate()


def main() -> int:
    args = _arguments()
    source = args.dfl.resolve()
    if not source.is_file():
        raise SystemExit(f"DFL not found: {source}")
    if args.seconds <= 0.0:
        raise SystemExit("seconds must be positive")

    with tempfile.TemporaryDirectory(prefix="esw-dfl-gui-benchmark-") as temporary:
        os.environ["ESW_DFL_ACTIVITY_LOG"] = str(Path(temporary) / "activity.jsonl")
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(
            QSettings.Format.IniFormat,
            QSettings.Scope.UserScope,
            temporary,
        )
        app = QApplication.instance() or QApplication([])
        window = MainWindow()
        window.load_file(source)
        loaded = _wait_until(
            app,
            lambda: (
                window.active_session() is not None
                and bool(window._spectrogram_indexes)
                and window._heatmap_controller_context() is not None
            ),
            timeout_s=args.timeout,
        )
        if not loaded:
            window.close()
            raise SystemExit("DFL/index load timeout")

        session = window.active_session()
        assert session is not None
        index = window._active_spectrogram_index(session)
        assert index is not None
        start_frame = min(max(0, args.start_frame), index.frame_count - 2)
        window.fps_combo.setCurrentText(str(args.fps))
        window.speed_combo.setCurrentText(f"{args.speed:g}×")
        window.no_skip_check.setChecked(False)
        window._combo_set_data(
            window.heatmap_range_mode,
            PersistenceMode.ROLLING_EXACT.value,
        )
        window._show_frame(start_frame)
        if not _wait_until(
            app,
            lambda: session.current_frame == start_frame,
            timeout_s=args.timeout,
        ):
            window.close()
            raise SystemExit("initial frame load timeout")
        if not args.no_heatmap:
            window.heatmap_enabled.setChecked(True)
            if not _wait_until(
                app,
                lambda: (
                    window._heatmap_controller.applied_snapshot is not None
                    and window._heatmap_controller.applied_snapshot.target_frame == start_frame
                ),
                timeout_s=args.timeout,
            ):
                window.close()
                raise SystemExit("initial heatmap build timeout")

        spectrum_snapshot_frames: list[int] = []
        playback_tick_times: list[float] = []
        playback_tick_targets: list[int] = []

        def record_playback_tick() -> None:
            playback_tick_times.append(time.perf_counter())
            playback_tick_targets.append(window._frame_nav.requested_frame)

        window.playback_timer.timeout.connect(record_playback_tick)
        window._frame_scheduler.apply_snapshot.connect(
            lambda snapshot: spectrum_snapshot_frames.append(snapshot.frame_index)
        )
        before = window.heatmap_diagnostics()
        playback_started = time.perf_counter()
        window.play()
        playback_loop = QEventLoop()
        QTimer.singleShot(max(1, round(args.seconds * 1000.0)), playback_loop.quit)
        playback_loop.exec()
        window.pause()
        app.processEvents()
        playback_elapsed = time.perf_counter() - playback_started
        desired = window._heatmap_controller.desired_target
        logical_target = (
            window._frame_nav.requested_frame
            if args.no_heatmap
            else desired.frame_index if desired is not None else start_frame
        )
        immediate = window.heatmap_diagnostics()
        if args.no_heatmap:
            analytics_caught_up = True
            render_caught_up = True
            analytics_catch_up_seconds = 0.0
            render_catch_up_seconds = 0.0
        else:
            catch_up_started = time.perf_counter()
            analytics_caught_up = _wait_until(
                app,
                lambda: (
                    window._heatmap_controller.applied_snapshot is not None
                    and window._heatmap_controller.applied_snapshot.target_frame >= logical_target
                ),
                timeout_s=args.timeout,
            )
            analytics_catch_up_seconds = time.perf_counter() - catch_up_started
            render_catch_up_started = time.perf_counter()
            render_caught_up = _wait_until(
                app,
                lambda: (
                    int(window.heatmap_diagnostics()["heatmap_rendered_target"])
                    >= logical_target
                ),
                timeout_s=args.timeout,
            )
            render_catch_up_seconds = time.perf_counter() - render_catch_up_started
        after = window.heatmap_diagnostics()
        applied = window._heatmap_controller.applied_snapshot
        result = {
            "dfl": str(source),
            "point_count": index.info.point_count,
            "frame_period_us": after["frame_period_s"] * 1e6,
            "requested_duration_s": args.seconds,
            "playback_elapsed_s": playback_elapsed,
            "fps_setting": args.fps,
            "heatmap_enabled": not args.no_heatmap,
            "playback_timer_ticks": len(playback_tick_times),
            "unique_playback_targets": len(set(playback_tick_targets)),
            "playback_tick_target_sample": playback_tick_targets[:20],
            "playback_timer_tick_rate_hz": (
                len(playback_tick_times) / playback_elapsed
            ),
            "speed_setting": args.speed,
            "start_frame": start_frame,
            "logical_target": logical_target,
            "displayed_target": session.current_frame,
            "analytical_target_at_pause": immediate["heatmap_analytical_target"],
            "rendered_target_at_pause": immediate["heatmap_rendered_target"],
            "lag_frames_at_pause": immediate["heatmap_lag_frames"],
            "visual_lag_frames_at_pause": immediate["heatmap_visual_lag_frames"],
            "analytics_caught_up": analytics_caught_up,
            "render_caught_up": render_caught_up,
            "analytics_catch_up_seconds_after_pause": analytics_catch_up_seconds,
            "render_catch_up_seconds_after_analytics": render_catch_up_seconds,
            "final_analytical_target": after["heatmap_analytical_target"],
            "final_rendered_target": after["heatmap_rendered_target"],
            "source_frames_per_wall_second": (
                (logical_target - start_frame) / playback_elapsed
            ),
            "frame_loader_diagnostics": dict(window._frame_loader._diagnostics),
            "spectrum_updates": len(spectrum_snapshot_frames),
            "spectrum_update_rate_hz": (
                len(spectrum_snapshot_frames) / playback_elapsed
            ),
            "playback_timer_interval_ms": window.playback_timer.interval(),
            "presentation_timer_interval_ms": window._frame_scheduler._timer.interval(),
            "heatmap_render_timer_interval_ms": (
                window._heatmap_controller._render_timer.interval()
            ),
            "analytics_frames_processed": (
                after["analytical_frames_processed"]
                - before["analytical_frames_processed"]
            ),
            "render_updates": after["render_applied"] - before["render_applied"],
            "applied_snapshot_target": applied.target_frame if applied is not None else None,
            "diagnostics": after,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        window.close()
        QThreadPool.globalInstance().waitForDone(5000)
        app.processEvents()
        return 0 if analytics_caught_up and render_caught_up else 2


if __name__ == "__main__":
    raise SystemExit(main())
