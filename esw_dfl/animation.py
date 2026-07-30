from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Callable, Generator

import imageio_ffmpeg
import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

from .models import SpectrogramPreview
from .plotting import scaled_frequency
from .spectrogram import OperationCancelled


def export_waterfall_animation(
    preview: SpectrogramPreview,
    path: str | Path,
    start_index: int,
    stop_index: int,
    fps: float = 12.0,
    max_frames: int = 300,
    cmap: str = "turbo",
    vmin: float | None = None,
    vmax: float | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: Event | None = None,
) -> Path:
    """Render a selected waterfall interval as GIF or MP4."""
    destination = Path(path)
    suffix = destination.suffix.lower()
    if suffix not in {".gif", ".mp4"}:
        raise ValueError("Поддерживаются форматы GIF и MP4")
    if preview.values.size == 0:
        raise ValueError("В спектрограмме нет данных")
    row_count = preview.values.shape[0]
    start = int(np.clip(min(start_index, stop_index), 0, row_count - 1))
    stop = int(np.clip(max(start_index, stop_index), 0, row_count - 1))
    source_indices = np.arange(start, stop + 1, dtype=np.int64)
    if source_indices.size > max_frames:
        source_indices = np.unique(
            np.linspace(start, stop, max_frames, dtype=np.int64)
        )
    fps = float(np.clip(fps, 0.5, 60.0))
    temp = destination.with_name(destination.stem + ".part" + suffix)

    frequencies, unit = scaled_frequency(
        preview.info.frequencies_hz[: preview.values.shape[1]]
    )
    elapsed = preview.elapsed_seconds
    segment = preview.values[start : stop + 1]
    segment_elapsed = elapsed[start : stop + 1]
    if vmin is None or vmax is None:
        finite = segment[np.isfinite(segment)]
        if finite.size:
            auto_min, auto_max = np.percentile(finite, [2.0, 99.5])
            vmin = float(auto_min) if vmin is None else vmin
            vmax = float(auto_max) if vmax is None else vmax

    figure = Figure(figsize=(9.6, 7.2), dpi=100)
    canvas = FigureCanvasAgg(figure)
    grid = figure.add_gridspec(2, 1, height_ratios=(2.2, 1.0), hspace=0.30)
    waterfall_axis = figure.add_subplot(grid[0, 0])
    spectrum_axis = figure.add_subplot(grid[1, 0], sharex=waterfall_axis)
    y0 = float(segment_elapsed[0])
    y1 = float(segment_elapsed[-1]) if segment_elapsed.size > 1 else y0 + 1.0
    image = waterfall_axis.imshow(
        segment,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=[float(frequencies[0]), float(frequencies[-1]), y0, y1],
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
    )
    cursor = waterfall_axis.axhline(float(elapsed[source_indices[0]]), color="white")
    waterfall_axis.set_title(preview.info.title)
    waterfall_axis.set_ylabel("Время от начала, с")
    waterfall_axis.tick_params(labelbottom=False)
    figure.colorbar(image, ax=waterfall_axis, pad=0.012).set_label(preview.info.y_unit)
    spectrum_line = spectrum_axis.plot(
        frequencies, preview.values[source_indices[0]], color="#1464a5", linewidth=1.2
    )[0]
    spectrum_axis.set_xlabel(f"Частота, {unit}")
    spectrum_axis.set_ylabel(preview.info.y_unit)
    spectrum_axis.grid(True, alpha=0.25)
    finite_values = segment[np.isfinite(segment)]
    if finite_values.size:
        margin = max(1.0, float(np.ptp(finite_values)) * 0.05)
        spectrum_axis.set_ylim(float(np.min(finite_values) - margin), float(np.max(finite_values) + margin))
    timestamp_label = spectrum_axis.text(
        0.01, 0.96, "", transform=spectrum_axis.transAxes, va="top", fontsize=9
    )
    figure.subplots_adjust(left=0.09, right=0.93, top=0.94, bottom=0.09)

    writer: Generator[None, bytes, None] | None = None
    if suffix == ".gif":
        writer = imageio_ffmpeg.write_frames(
            temp,
            (960, 720),
            fps=fps,
            codec="gif",
            pix_fmt_out="rgb8",
            macro_block_size=1,
            output_params=["-loop", "0"],
        )
    else:
        writer = imageio_ffmpeg.write_frames(
            temp,
            (960, 720),
            fps=fps,
            codec="libx264",
            pix_fmt_out="yuv420p",
            quality=8,
            macro_block_size=2,
        )
    try:
        writer.send(None)
        total = source_indices.size
        for frame_number, row_index in enumerate(source_indices, start=1):
            if cancel is not None and cancel.is_set():
                raise OperationCancelled()
            cursor.set_ydata([float(elapsed[row_index]), float(elapsed[row_index])])
            spectrum_line.set_ydata(preview.values[row_index])
            timestamp = float(preview.timestamps[row_index])
            timestamp_label.set_text(
                f"Кадр {row_index + 1}/{row_count} · "
                f"{np.datetime64(int(timestamp * 1000), 'ms')}"
            )
            canvas.draw()
            frame = np.asarray(canvas.buffer_rgba())[:, :, :3]
            writer.send(frame.tobytes())
            if progress is not None:
                progress(frame_number / total, f"Анимация: кадр {frame_number}/{total}")
        writer.close()
        writer = None
        temp.replace(destination)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()
        figure.clear()
    return destination
