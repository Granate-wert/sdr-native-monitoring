"""Read-only component benchmark for the Heatmap Spectrum processing engine.

Example (PowerShell):
    .\\.venv\\Scripts\\python.exe benchmark_heatmap_pipeline.py `
        "<path-to-reference.dfl>"

The index-build time is reported separately and never mixed into steady-state
frame throughput.  The script does not write DFL data, sidecar indices or
exports.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable, TypeVar, cast

import numpy as np

if TYPE_CHECKING:
    from esw_dfl.frame_navigation import NavigationReason

from esw_dfl.heatmap import HeatmapAccumulator
from esw_dfl.heatmap_persistence import (
    PersistenceConfig,
    PersistenceEngine,
    PersistenceMode,
    PersistenceSourceKey,
    PersistenceTarget,
    PersistenceWorkRequest,
)
from esw_dfl.parser import DflParser
from esw_dfl.spectrogram import (
    SpectrogramFrameReader,
    _decode_line,
    _read_regular_sector_blob_from_handle,
    load_spectrogram_preview_with_index,
    native_decoder_available,
)

T = TypeVar("T")


def _median_microseconds(call: Callable[[], T], *, repeats: int, items: int) -> float:
    values: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter_ns()
        call()
        values.append((time.perf_counter_ns() - started) / 1_000.0 / items)
    return float(statistics.median(values))


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dfl", type=Path, help="read-only ESW DFL containing a spectrogram")
    parser.add_argument("--spectrogram", type=int, default=0, help="spectrogram ordinal in the DFL")
    parser.add_argument("--frame", type=int, default=5_000, help="initial full-window target frame")
    parser.add_argument("--count", type=int, default=1_000, help="sequential entering frames to measure")
    parser.add_argument("--window", type=int, default=500, help="Rolling Exact window size in frames")
    parser.add_argument("--repeats", type=int, default=3, help="median repetitions per stage")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    if not args.dfl.is_file():
        raise SystemExit(f"DFL not found: {args.dfl}")
    if args.count <= 0 or args.window <= 0 or args.repeats <= 0:
        raise SystemExit("count, window and repeats must be positive")

    document = DflParser().parse(args.dfl)
    try:
        info = document.spectrograms[args.spectrogram]
    except IndexError as error:
        raise SystemExit(f"spectrogram {args.spectrogram} is unavailable") from error

    index_started = time.perf_counter_ns()
    _preview, index = load_spectrogram_preview_with_index(args.dfl, info, max_rows=1)
    index_seconds = (time.perf_counter_ns() - index_started) / 1_000_000_000.0
    frame = max(args.window - 1, args.frame)
    final_frame = frame + args.count
    if final_frame >= index.frame_count:
        raise SystemExit(
            f"frame + count must be below {index.frame_count}; got {final_frame}"
        )
    if index.sector_chain is None or index.sector_size is None:
        raise SystemExit("component decoder benchmark requires a regular CFB sector chain")

    reader = SpectrogramFrameReader(args.dfl, index)
    try:
        reader.read_frame(frame)
        offset = int(index.offsets[frame])
        length = int(index.lengths[frame])
        with args.dfl.open("rb") as source:
            blob = _read_regular_sector_blob_from_handle(
                source, index.sector_chain, index.sector_size, offset, length
            )
        batch_count = min(128, args.count)
        rows = [reader.read_frame(frame + number).values for number in range(batch_count)]
        storage = np.empty((batch_count, info.point_count), dtype=np.uint16)
        accumulator = HeatmapAccumulator(info.point_count, -120.0, 0.0, 256)

        decoder_us = _median_microseconds(
            lambda: [_decode_line(blob, info.point_count) for _ in range(args.count)],
            repeats=args.repeats,
            items=args.count,
        )
        reader_us = _median_microseconds(
            lambda: [reader.read_frame(frame + number) for number in range(args.count)],
            repeats=args.repeats,
            items=args.count,
        )
        quantize_us = _median_microseconds(
            lambda: accumulator.quantize_rows_into(rows, storage),
            repeats=max(3, args.repeats),
            items=batch_count,
        )

        density_accumulator = HeatmapAccumulator(info.point_count, -120.0, 0.0, 256)
        density_us = _median_microseconds(
            lambda: density_accumulator.apply_exact_bin_matrices((storage,), ()),
            repeats=max(3, args.repeats),
            items=batch_count,
        )
        frequencies = np.linspace(info.start_hz, info.stop_hz, info.point_count, dtype=np.float64)
        config = PersistenceConfig(
            mode=PersistenceMode.ROLLING_EXACT,
            window_frames=args.window,
            power_min_dbm=-120.0,
            power_max_dbm=0.0,
            power_bins=256,
        )
        key = PersistenceSourceKey("benchmark", "waterfall", info.source_stream, "grid")
        request = PersistenceWorkRequest(
            key,
            config,
            1,
            1,
            frame,
            float(index.timestamps[frame]),
            index.frame_count,
            frequencies,
            timestamps=index.timestamps,
        )
        target = PersistenceTarget(
            key,
            final_frame,
            float(index.timestamps[final_frame]),
            2,
            2,
            cast("NavigationReason", "api"),
        )
        engine_values: list[float] = []
        ring_bytes = 0
        for _ in range(args.repeats):
            engine = PersistenceEngine()
            state, _snapshot = engine.rebuild_exact(request, reader)
            ring_bytes = state.contribution_ring.nbytes if state.contribution_ring is not None else 0
            started = time.perf_counter_ns()
            engine.advance_exact_range(state, target, reader, publish_snapshot=False)
            engine_values.append((time.perf_counter_ns() - started) / 1_000.0 / args.count)
        engine_us = float(statistics.median(engine_values))
        print(
            json.dumps(
                {
                    "dfl": str(args.dfl),
                    "spectrogram": info.source_stream,
                    "frame_count": index.frame_count,
                    "point_count": info.point_count,
                    "native_decoder": native_decoder_available(),
                    "index_build_seconds": round(index_seconds, 6),
                    "native_decode_us_per_frame": round(decoder_us, 3),
                    "reader_plus_decode_us_per_frame": round(reader_us, 3),
                    "quantize_us_per_frame": round(quantize_us, 3),
                    "density_us_per_frame": round(density_us, 3),
                    "full_exact_ring_us_per_frame": round(engine_us, 3),
                    "full_exact_ring_fps": round(1_000_000.0 / engine_us, 1),
                    "contribution_ring_bytes": ring_bytes,
                    "snapshot_or_qt_included": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        reader.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






