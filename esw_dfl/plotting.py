from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from .analysis import power_average_db
from .models import SpectrogramPreview, TraceData


@dataclass(slots=True)
class WaterfallArtists:
    waterfall_axis: object
    spectrum_axis: object
    image: object
    current_spectrum: object
    average_spectrum: object
    max_spectrum: object
    stored_spectrum: object
    time_marker: object
    frequency_marker: object
    marker_a_waterfall: object
    marker_b_waterfall: object
    marker_a_spectrum: object
    marker_b_spectrum: object
    selection_patch: object
    frequency_scale: float


def scaled_frequency(values_hz: np.ndarray) -> tuple[np.ndarray, str]:
    maximum = float(np.nanmax(np.abs(values_hz))) if values_hz.size else 0.0
    if maximum >= 1e9:
        return values_hz / 1e9, "ГГц"
    if maximum >= 1e6:
        return values_hz / 1e6, "МГц"
    if maximum >= 1e3:
        return values_hz / 1e3, "кГц"
    return values_hz, "Гц"


def plot_trace(figure: Figure, trace: TraceData) -> None:
    figure.clear()
    axis = figure.add_subplot(111)
    x = trace.x
    x_label = trace.x_unit or "X"
    if trace.x_unit == "Hz":
        x, x_label = scaled_frequency(trace.x)
    axis.plot(x, trace.y, color="#1464a5", linewidth=1.1)
    axis.set_title(trace.title)
    axis.set_xlabel(x_label)
    axis.set_ylabel(trace.y_unit or "Y")
    axis.grid(True, alpha=0.28)
    axis.margins(x=0.01)
    figure.tight_layout()


def plot_spectrogram(figure: Figure, preview: SpectrogramPreview) -> None:
    figure.clear()
    axis = figure.add_subplot(111)
    if preview.values.size == 0:
        axis.text(0.5, 0.5, "Нет данных спектрограммы", ha="center", va="center")
        return
    frequencies, unit = scaled_frequency(preview.info.frequencies_hz[: preview.values.shape[1]])
    elapsed = preview.elapsed_seconds
    extent = [
        float(frequencies[0]),
        float(frequencies[-1]),
        float(elapsed[0]),
        float(elapsed[-1]) if elapsed.size > 1 else 1.0,
    ]
    finite = preview.values[np.isfinite(preview.values)]
    if finite.size:
        vmin, vmax = np.percentile(finite, [2.0, 99.5])
        if vmin == vmax:
            vmin, vmax = float(vmin - 1), float(vmax + 1)
    else:
        vmin, vmax = None, None
    image = axis.imshow(
        preview.values,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=extent,
        cmap="turbo",
        vmin=vmin,
        vmax=vmax,
    )
    axis.set_title(preview.info.title)
    axis.set_xlabel(f"Частота, {unit}")
    axis.set_ylabel("Время от начала, с")
    colorbar = figure.colorbar(image, ax=axis, pad=0.02)
    colorbar.set_label(preview.info.y_unit)
    if preview.timestamps.size:
        start = datetime.fromtimestamp(float(np.nanmin(preview.timestamps)))
        stop = datetime.fromtimestamp(float(np.nanmax(preview.timestamps)))
        axis.text(
            0.01,
            0.99,
            f"{start:%Y-%m-%d %H:%M:%S.%f} - {stop:%H:%M:%S.%f}",
            transform=axis.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            color="white",
            bbox={"facecolor": "black", "alpha": 0.4, "pad": 2},
        )
    figure.tight_layout()


def plot_waterfall_analysis(
    figure: Figure,
    preview: SpectrogramPreview,
    selected_index: int,
    selected_frequency_index: int,
    cmap: str = "turbo",
    vmin: float | None = None,
    vmax: float | None = None,
) -> WaterfallArtists:
    """Build linked waterfall and spectrum axes and return mutable artists."""
    figure.clear()
    grid = figure.add_gridspec(2, 1, height_ratios=(2.2, 1.0), hspace=0.30)
    waterfall_axis = figure.add_subplot(grid[0, 0])
    spectrum_axis = figure.add_subplot(grid[1, 0], sharex=waterfall_axis)
    frequencies_hz = preview.info.frequencies_hz[: preview.values.shape[1]]
    frequencies, unit = scaled_frequency(frequencies_hz)
    frequency_scale = (
        1e9 if unit == "ГГц" else 1e6 if unit == "МГц" else 1e3 if unit == "кГц" else 1.0
    )
    elapsed = preview.elapsed_seconds
    selected_index = int(np.clip(selected_index, 0, preview.values.shape[0] - 1))
    selected_frequency_index = int(
        np.clip(selected_frequency_index, 0, preview.values.shape[1] - 1)
    )
    extent = [
        float(frequencies[0]),
        float(frequencies[-1]),
        float(elapsed[0]),
        float(elapsed[-1]) if elapsed.size > 1 else 1.0,
    ]
    image = waterfall_axis.imshow(
        preview.values,
        origin="lower",
        aspect="auto",
        interpolation="nearest",
        extent=extent,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
    )
    waterfall_axis.set_title(preview.info.title)
    waterfall_axis.set_ylabel("Время от начала, с")
    waterfall_axis.tick_params(labelbottom=False)
    colorbar = figure.colorbar(image, ax=waterfall_axis, pad=0.012)
    colorbar.set_label(preview.info.y_unit)

    selected_time = float(elapsed[selected_index])
    selected_frequency = float(frequencies[selected_frequency_index])
    selected_level = float(preview.values[selected_index, selected_frequency_index])
    time_marker = waterfall_axis.axhline(selected_time, color="white", linewidth=1.0)
    frequency_marker = waterfall_axis.axvline(
        selected_frequency, color="white", linewidth=0.8, alpha=0.75
    )
    marker_a_waterfall = waterfall_axis.plot(
        [selected_frequency], [selected_time], marker="+", color="yellow", markersize=11,
        markeredgewidth=2, linestyle="none", label="A"
    )[0]
    marker_b_waterfall = waterfall_axis.plot(
        [], [], marker="x", color="#ff4b4b", markersize=9, markeredgewidth=2,
        linestyle="none", label="B"
    )[0]
    selection_patch = Rectangle(
        (float(frequencies[0]), selected_time),
        float(frequencies[-1] - frequencies[0]),
        0.0,
        facecolor="white",
        edgecolor="white",
        linewidth=0.8,
        alpha=0.10,
        visible=False,
    )
    waterfall_axis.add_patch(selection_patch)

    current_spectrum = spectrum_axis.plot(
        frequencies,
        preview.values[selected_index],
        color="#1464a5",
        linewidth=1.15,
        label="Текущий",
    )[0]
    average_spectrum = spectrum_axis.plot(
        frequencies,
        power_average_db(preview.values, axis=0),
        color="#25a244",
        linewidth=1.0,
        alpha=0.9,
        label="Average",
    )[0]
    max_spectrum = spectrum_axis.plot(
        frequencies,
        np.nanmax(preview.values, axis=0),
        color="#ef7d00",
        linewidth=1.0,
        alpha=0.9,
        label="Max Hold",
    )[0]
    stored_spectrum = spectrum_axis.plot(
        [], [], color="#777777", linewidth=1.0, linestyle="--", label="Маркер B"
    )[0]
    marker_a_spectrum = spectrum_axis.plot(
        [selected_frequency], [selected_level], marker="o", color="yellow",
        markeredgecolor="#333333", markersize=6, linestyle="none", label="A"
    )[0]
    marker_b_spectrum = spectrum_axis.plot(
        [], [], marker="x", color="#d62728", markersize=7, markeredgewidth=2,
        linestyle="none", label="B"
    )[0]
    spectrum_axis.set_xlabel(f"Частота, {unit}")
    spectrum_axis.set_ylabel(preview.info.y_unit)
    spectrum_axis.grid(True, alpha=0.25)
    spectrum_axis.margins(x=0.01)
    spectrum_axis.legend(loc="best", fontsize=8, ncols=3)
    figure.subplots_adjust(left=0.08, right=0.94, top=0.94, bottom=0.09)
    return WaterfallArtists(
        waterfall_axis=waterfall_axis,
        spectrum_axis=spectrum_axis,
        image=image,
        current_spectrum=current_spectrum,
        average_spectrum=average_spectrum,
        max_spectrum=max_spectrum,
        stored_spectrum=stored_spectrum,
        time_marker=time_marker,
        frequency_marker=frequency_marker,
        marker_a_waterfall=marker_a_waterfall,
        marker_b_waterfall=marker_b_waterfall,
        marker_a_spectrum=marker_a_spectrum,
        marker_b_spectrum=marker_b_spectrum,
        selection_patch=selection_patch,
        frequency_scale=frequency_scale,
    )


def save_trace_png(trace: TraceData, path: str | Path, dpi: int = 180) -> None:
    figure = Figure(figsize=(11, 6.5), dpi=100)
    FigureCanvasAgg(figure)
    plot_trace(figure, trace)
    figure.savefig(path, dpi=dpi, bbox_inches="tight")
    figure.clear()


def save_spectrogram_png(
    preview: SpectrogramPreview, path: str | Path, dpi: int = 180
) -> None:
    figure = Figure(figsize=(12, 7), dpi=100)
    FigureCanvasAgg(figure)
    plot_spectrogram(figure, preview)
    figure.savefig(path, dpi=dpi, bbox_inches="tight")
    figure.clear()
