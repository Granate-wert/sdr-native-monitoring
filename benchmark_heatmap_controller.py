r"""Read-only production-controller benchmark for Rolling Exact.

Unlike ``benchmark_heatmap_pipeline.py``, this benchmark includes the
``HeatmapPersistenceController``, its long-lived reader worker, queued Qt
signals and immutable snapshot publication.  It intentionally does not create
widgets or write a sidecar index.

Example:
    .\.venv\Scripts\python.exe benchmark_heatmap_controller.py `
        "<path-to-reference.dfl>" --count 12000
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool
from PySide6.QtWidgets import QApplication

from esw_dfl.frame_navigation import FrameSpanEvent, NavigationReason
from esw_dfl.heatmap import frequency_grid_hash
from esw_dfl.heatmap_persistence import (
    PersistenceConfig,
    PersistenceMode,
    PersistenceSourceKey,
)
from esw_dfl.heatmap_persistence_controller import (
    HeatmapPersistenceController,
    PersistenceSourceContext,
)
from esw_dfl.parser import DflParser
from esw_dfl.spectrogram import (
    load_spectrogram_preview_with_index,
    native_decoder_available,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dfl", type=Path)
    parser.add_argument("--spectrogram", type=int, default=0)
    parser.add_argument("--frame", type=int, default=5000)
    parser.add_argument("--count", type=int, default=12000)
    parser.add_argument("--window", type=int, default=500)
    parser.add_argument("--power-bins", type=int, default=256)
    parser.add_argument("--render-fps", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument(
        "--paced",
        action="store_true",
        help="publish logical targets at render-fps using recorded frame time",
    )
    return parser.parse_args()


def _wait_until(
    app: QApplication,
    predicate: object,
    *,
    timeout_s: float,
) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():  # type: ignore[operator]
            return True
        time.sleep(0.0005)
    app.processEvents()
    return bool(predicate())  # type: ignore[operator]


def main() -> int:
    args = _arguments()
    source = args.dfl.resolve()
    if not source.is_file():
        raise SystemExit(f"DFL not found: {source}")
    if args.count < 1 or args.window < 1:
        raise SystemExit("count and window must be positive")

    app = QApplication.instance() or QApplication([])
    document = DflParser().parse(source)
    try:
        info = document.spectrograms[args.spectrogram]
    except IndexError as error:
        raise SystemExit(f"spectrogram {args.spectrogram} is unavailable") from error
    index_started = time.perf_counter()
    _preview, index = load_spectrogram_preview_with_index(source, info, max_rows=1)
    index_seconds = time.perf_counter() - index_started

    frequencies = np.linspace(info.start_hz, info.stop_hz, info.point_count, dtype=np.float64)
    source_key = PersistenceSourceKey(
        "benchmark",
        "waterfall",
        info.source_stream,
        frequency_grid_hash(frequencies),
    )
    positive_deltas = np.diff(index.timestamps)
    positive_deltas = positive_deltas[np.isfinite(positive_deltas) & (positive_deltas > 0)]
    frame_period_s = float(np.median(positive_deltas)) if positive_deltas.size else None
    start_frame = max(args.window - 1, int(args.frame))
    final_frame = min(index.frame_count - 1, start_frame + int(args.count))
    count = final_frame - start_frame
    if count < 1:
        raise SystemExit("frame/count resolve outside the recording")

    controller = HeatmapPersistenceController(
        thread_pool=QThreadPool.globalInstance(),
        audit=lambda *_args, **_details: None,
        render_fps=args.render_fps,
    )
    snapshots: list[tuple[int, float]] = []
    controller.snapshot_ready.connect(
        lambda snapshot: snapshots.append((snapshot.target_frame, time.perf_counter()))
    )
    controller.set_context(
        PersistenceSourceContext(
            session_id="benchmark",
            waterfall_id="waterfall",
            source_id=info.source_stream,
            source_path=source,
            frequencies_hz=frequencies,
            index=index,
            info=info,
            source_key=source_key,
            frame_period_s=frame_period_s,
        )
    )
    initial_started = time.perf_counter()
    controller.enable(
        PersistenceConfig(
            mode=PersistenceMode.ROLLING_EXACT,
            window_frames=args.window,
            power_min_dbm=-120.0,
            power_max_dbm=0.0,
            power_bins=args.power_bins,
        ),
        start_frame,
        float(index.timestamps[start_frame]),
    )
    if not _wait_until(
        app,
        lambda: (
            controller.applied_snapshot is not None
            and controller.applied_snapshot.target_frame == start_frame
        ),
        timeout_s=args.timeout,
    ):
        raise SystemExit("initial rebuild timeout")
    initial_seconds = time.perf_counter() - initial_started
    snapshots.clear()

    generation = 1
    target_started = time.perf_counter()
    if args.paced and frame_period_s is not None:
        next_tick = target_started
        target = start_frame
        while target < final_frame:
            now = time.perf_counter()
            if now < next_tick:
                app.processEvents()
                time.sleep(min(0.0005, next_tick - now))
                continue
            elapsed = now - target_started
            new_target = min(
                final_frame,
                start_frame + max(1, int(elapsed / frame_period_s)),
            )
            generation += 1
            controller.on_frame_span(
                FrameSpanEvent(
                    previous_target=target,
                    new_target=new_target,
                    direction=1,
                    reason=NavigationReason.PLAYBACK,
                    generation=generation,
                )
            )
            target = new_target
            next_tick += 1.0 / max(1, args.render_fps)
            app.processEvents()
    else:
        controller.on_frame_span(
            FrameSpanEvent(
                previous_target=start_frame,
                new_target=final_frame,
                direction=1,
                reason=NavigationReason.PLAYBACK,
                generation=generation,
            )
        )

    published_seconds = time.perf_counter() - target_started
    caught_up = _wait_until(
        app,
        lambda: (
            controller.applied_snapshot is not None
            and controller.applied_snapshot.target_frame == final_frame
        ),
        timeout_s=args.timeout,
    )
    total_seconds = time.perf_counter() - target_started
    diagnostics = controller.diagnostics()
    result = {
        "dfl": str(source),
        "frame_count": index.frame_count,
        "point_count": info.point_count,
        "native_decoder": native_decoder_available(),
        "frame_period_us": frame_period_s * 1e6 if frame_period_s is not None else None,
        "index_seconds": index_seconds,
        "initial_window_seconds": initial_seconds,
        "start_frame": start_frame,
        "final_frame": final_frame,
        "processed_entering_frames": count,
        "paced": bool(args.paced),
        "logical_publish_seconds": published_seconds,
        "catch_up_seconds": total_seconds,
        "catch_up_fps": count / total_seconds,
        "caught_up": caught_up,
        "snapshots_emitted": len(snapshots),
        "diagnostics": diagnostics,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    controller.shutdown()
    QThreadPool.globalInstance().waitForDone(5000)
    app.processEvents()
    return 0 if caught_up else 2


if __name__ == "__main__":
    raise SystemExit(main())
