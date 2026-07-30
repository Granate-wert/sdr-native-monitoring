"""Export helpers for computed Heatmap Spectrum results.

All writers follow the repository rule for large exports: data goes through a
temporary ``.part`` file (``_atomic_path``) that is atomically replaced on
success and removed after failure or cancellation. The source DFL is never
touched — exports consume only the in-memory :class:`HeatmapResult`.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable

import numpy as np

from . import __version__
from .domain_export import _atomic_path
from .heatmap import HeatmapResult
from .heatmap_persistence import HeatmapDisplayConfig, PersistenceSnapshot
from .spectrogram import OperationCancelled


Progress = Callable[[float, str], None]


def export_heatmap_png(
    result: HeatmapResult,
    normalized: np.ndarray,
    lut: np.ndarray,
    opacity: float,
    path: str | Path,
    *,
    levels: tuple[float, float] | None = None,
) -> Path:
    """Render the normalized density matrix with its palette LUT to a PNG.

    The image is the matrix itself (X = frequency bin, Y = power bin, highest
    power on top), not a screenshot of the plot widget. ``lut`` is the
    renderer-style ``(256, 4)`` uint8 RGBA table whose entry 0 is transparent;
    ``opacity`` additionally scales the alpha of non-empty cells. ``levels``
    pins the display range to the current on-screen levels (no recompute);
    without it the image maximum defines the range.
    """
    from PySide6.QtGui import QImage

    image = np.asarray(normalized, dtype=np.float64)
    if image.ndim != 2 or image.size == 0:
        raise ValueError("heatmap image is empty")
    table = np.asarray(lut, dtype=np.uint8)
    if table.shape != (256, 4):
        raise ValueError(f"lut must have shape (256, 4), got {table.shape}")
    if levels is not None and levels[1] > levels[0]:
        lo, hi = float(levels[0]), float(levels[1])
        scaled = np.rint(np.clip((image - lo) / (hi - lo), 0.0, 1.0) * 255.0).astype(np.uint8)
    else:
        finite = image[np.isfinite(image)]
        vmax = float(finite.max()) if finite.size else 0.0
        if vmax <= 0.0:
            scaled = np.zeros(image.shape, dtype=np.uint8)
        else:
            scaled = np.rint(np.clip(image / vmax, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgba = table[scaled].copy()
    if 0.0 <= opacity < 1.0:
        visible = rgba[..., 3] > 0
        rgba[..., 3] = np.where(visible, np.rint(rgba[..., 3] * opacity), 0).astype(np.uint8)
    rgba = np.ascontiguousarray(rgba[::-1])  # row 0 holds the lowest power; PNG wants it at the bottom
    height, width = rgba.shape[0], rgba.shape[1]
    qt_image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        # The PySide6 stub declares the format as bytes, but the runtime only
        # accepts str/None (verified against PySide6 6.x on this project).
        if not qt_image.save(str(temporary), "PNG"):  # type: ignore[call-overload]
            raise OSError(f"Qt не смог сохранить изображение: {temporary}")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_heatmap_csv(
    result: HeatmapResult,
    path: str | Path,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> Path:
    """Write the raw (normalization-independent) density matrix as CSV.

    Layout: a header row of frequency-bin centers (Hz), then one row per power
    bin (dBm) with raw hit counts.
    """
    target = Path(path)
    temporary = _atomic_path(target)
    rows = int(result.density.shape[0])
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["power_dbm\\frequency_hz", *result.frequencies_hz.tolist()])
            for row_index, power in enumerate(result.power_axis_dbm):
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Экспорт отменён")
                writer.writerow([float(power), *result.density[row_index].tolist()])
                if progress is not None and rows > 1 and row_index % 16 == 0:
                    progress(row_index / rows, f"Экспорт Heatmap CSV: {row_index}/{rows}")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_heatmap_npz(result: HeatmapResult, path: str | Path) -> Path:
    """Write the density matrix and both physical axes as a compressed NPZ."""
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(
                handle,
                density=result.density,
                frequencies_hz=result.frequencies_hz,
                power_axis_dbm=result.power_axis_dbm,
                processed_frames=np.int64(result.processed_frames),
                total_frames_in_range=np.int64(result.total_frames_in_range),
                exact=np.bool_(result.exact),
                approximate=np.bool_(result.approximate),
                sampling_policy=np.str_(result.sampling_policy.value),
            )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_heatmap_json(
    result: HeatmapResult,
    path: str | Path,
    *,
    source_path: str | Path,
    session_id: str,
    waterfall_id: str,
    source_id: str,
    frame_range: tuple[int, int] | None = None,
    display_config: HeatmapDisplayConfig,
    persistence_snapshot: PersistenceSnapshot | None = None,
) -> Path:
    """Write Heatmap metadata (no matrix payload) as JSON.

    ``raw_density`` always describes unnormalized counts; ``display`` carries
    the CURRENT UI styling (normalization/palette/opacity/color scale), which
    may differ from the computation-time config. ``persistence`` mirrors the
    applied snapshot when available. Stale snapshots are rejected by the
    caller, never serialized: ``stale`` is always false here.
    """
    config = result.config
    frequencies = result.frequencies_hz
    persistence: dict[str, Any] | None = None
    if persistence_snapshot is not None:
        snap_config = persistence_snapshot.config
        persistence = {
            "mode": snap_config.mode.value,
            "window_frames": int(snap_config.window_frames),
            "window_unit": snap_config.window_unit.value,
            "half_life_seconds": persistence_snapshot.half_life_seconds,
            "decay_cutoff_epsilon": persistence_snapshot.decay_cutoff_epsilon,
            "target_frame": int(persistence_snapshot.target_frame),
            "applied_frame": int(persistence_snapshot.applied_frame),
            "frame_start": int(persistence_snapshot.frame_start),
            "frame_end": int(persistence_snapshot.frame_end),
            "generation": int(persistence_snapshot.generation),
            "navigation_generation": int(persistence_snapshot.navigation_generation),
            "exact": bool(persistence_snapshot.exact),
            "approximate": bool(persistence_snapshot.approximate),
            "stale": False,
        }
    metadata: dict[str, Any] = {
        "source_dfl": str(source_path),
        "session_id": session_id,
        "waterfall_id": waterfall_id,
        "source_id": source_id,
        "frame_range": (
            {"start": int(frame_range[0]), "end": int(frame_range[1])} if frame_range is not None else None
        ),
        "range_mode": config.range_mode.value,
        "window_frames": int(config.window_frames),
        "processed_frames": int(result.processed_frames),
        "total_frames_in_range": int(result.total_frames_in_range),
        "exact": bool(result.exact),
        "preview": not result.exact,
        "approximate": bool(result.approximate),
        "sampling_policy": result.sampling_policy.value,
        "raw_density": {"normalization": "count", "description": "raw unnormalized hit counts"},
        "display": {
            "normalization": display_config.normalization.value,
            "palette": display_config.palette,
            "opacity": float(display_config.opacity),
            "color_scale_mode": display_config.color_scale_mode.value,
            "color_min": display_config.color_min,
            "color_max": display_config.color_max,
        },
        "persistence": persistence,
        "frequency_range_hz": (
            {"start": float(frequencies[0]), "stop": float(frequencies[-1])} if frequencies.size else None
        ),
        "frequency_bins": int(frequencies.size),
        "power_range_dbm": {"min": float(config.power_min_dbm), "max": float(config.power_max_dbm)},
        "power_bins": int(config.power_bins),
        "normalization": display_config.normalization.value,
        "frequency_grid_hash": result.frequency_grid_hash,
        "generation": int(result.generation),
        # Time of the actual computation (set by the worker; null when the
        # result was built without one), not to be confused with export time.
        "calculation_timestamp": result.computed_at,
        "export_timestamp": datetime.now(timezone.utc).isoformat(),
        "software_version": __version__,
    }
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
