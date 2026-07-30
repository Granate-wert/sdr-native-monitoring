from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from threading import Event
from typing import Any, Callable

import numpy as np

from .domain import AnalysisResult, Marker, MeasurementSession, SpectrumTrace, WaterfallData
from .spectrogram import OperationCancelled
from .time_gated_power import ManualOverride, TimeGatedChannelPowerResult


Progress = Callable[[float, str], None]


def _atomic_path(target: Path) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    return target.with_suffix(target.suffix + ".part")


def export_trace_csv(trace: SpectrumTrace, path: str | Path) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([f"x_{trace.axis_unit or 'value'}", f"level_{trace.unit or 'value'}"])
            writer.writerows(zip(trace.x_values.tolist(), trace.power_values.tolist()))
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_traces_csv(
    traces: list[SpectrumTrace],
    path: str | Path,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["trace_id", "trace_name", "x", "x_unit", "level", "level_unit"])
            total = sum(trace.point_count for trace in traces)
            written = 0
            for trace in traces:
                for x, y in zip(trace.x_values, trace.power_values):
                    if cancel is not None and cancel.is_set():
                        raise OperationCancelled("Экспорт отменён")
                    writer.writerow([trace.trace_id, trace.name, float(x), trace.axis_unit, float(y), trace.unit])
                    written += 1
                    if progress and written % 10_000 == 0:
                        progress(written / max(1, total), f"Экспорт: {written:,} / {total:,}")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_session_npz(session: MeasurementSession, path: str | Path) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    arrays: dict[str, Any] = {}
    for index, trace in enumerate(session.traces.values()):
        arrays[f"trace_{index}_x"] = trace.x_values
        arrays[f"trace_{index}_y"] = trace.power_values
        arrays[f"trace_{index}_name"] = trace.name
    for index, waterfall in enumerate(session.waterfalls.values()):
        if waterfall.values is not None:
            arrays[f"waterfall_{index}_values"] = waterfall.values
            arrays[f"waterfall_{index}_timestamps"] = (
                waterfall.timestamps if waterfall.timestamps is not None else np.empty(0)
            )
    try:
        with temporary.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_markers_csv(markers: list[Marker], path: str | Path) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(asdict(Marker()).keys()))
            writer.writeheader()
            for marker in markers:
                row = asdict(marker)
                row["marker_type"] = marker.marker_type.value
                writer.writerow(row)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_results_csv(results: list[AnalysisResult], path: str | Path) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["created_at", "kind", "name", "trace_id", "region_id", "approximate", "metric", "value"])
            for result in results:
                for metric, value in result.values.items():
                    writer.writerow([
                        result.created_at.isoformat(), result.kind, result.name,
                        result.trace_id or "", result.region_id or "", result.approximate,
                        metric, value,
                    ])
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_waterfall_region_csv(
    waterfall: WaterfallData,
    path: str | Path,
    row_range: tuple[int, int] | None = None,
    frequency_range: tuple[float, float] | None = None,
) -> Path:
    if waterfall.values is None:
        raise ValueError("Предпросмотр waterfall ещё не загружен")
    row_start, row_stop = row_range or (0, waterfall.values.shape[0])
    frequencies = waterfall.frequencies_hz[: waterfall.values.shape[1]]
    mask: np.ndarray = np.ones(frequencies.size, dtype=bool)
    if frequency_range is not None:
        low, high = sorted(frequency_range)
        mask = (frequencies >= low) & (frequencies <= high)
    values = waterfall.values[row_start:row_stop, : frequencies.size][:, mask]
    timestamps = waterfall.timestamps[row_start:row_stop] if waterfall.timestamps is not None else np.arange(row_start, row_stop)
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(["timestamp", *frequencies[mask].tolist()])
            for timestamp, row in zip(timestamps, values):
                writer.writerow([float(timestamp), *row.tolist()])
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_session_json(session: MeasurementSession, path: str | Path) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    payload = {
        "session_id": session.session_id,
        "source_path": str(session.source_path),
        "name": session.name,
        "instrument": asdict(session.metadata),
        "traces": [
            {
                "trace_id": trace.trace_id,
                "name": trace.name,
                "points": trace.point_count,
                "start_frequency_hz": trace.start_frequency_hz,
                "stop_frequency_hz": trace.stop_frequency_hz,
                "frequency_step_hz": trace.frequency_step_hz,
                "unit": trace.unit,
                "detector": trace.detector,
                "trace_mode": trace.trace_mode,
                "source_stream": trace.source_stream,
            }
            for trace in session.traces.values()
        ],
        "waterfalls": [
            {
                "waterfall_id": item.waterfall_id,
                "name": item.name,
                "lines": item.line_count,
                "points": item.point_count,
                "source_stream": item.source_stream,
            }
            for item in session.waterfalls.values()
        ],
    }
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def _time_gated_summary(
    result: TimeGatedChannelPowerResult,
    source_path: str | Path | None = None,
    trace_name: str | None = None,
) -> dict[str, Any]:
    request = result.request
    idle = result.activity.idle_estimate
    config = request.activity_config
    return {
        "File": str(source_path or ""),
        "Trace": trace_name or request.trace_id,
        "Frequency Start": request.frequency_start_hz,
        "Frequency Stop": request.frequency_stop_hz,
        "Bandwidth": abs(request.frequency_stop_hz - request.frequency_start_hz),
        "Time Start": request.time_start_s,
        "Time Stop": request.time_stop_s,
        "Mode": request.mode.value,
        "Frame Inclusion": request.frame_inclusion.value,
        "Active Mean Power": result.active_mean_power_dbm,
        "Long-Term Mean Power": result.long_term_mean_power_dbm,
        "Idle Power": result.idle_mean_power_dbm,
        "Noise-Corrected Power": result.noise_corrected_active_power_dbm,
        "Maximum Frame Power": result.maximum_frame_power_dbm,
        "Minimum Active Power": result.minimum_active_power_dbm,
        "Duty Cycle": result.duty_cycle_percent,
        "Active Duration": result.active_duration_s,
        "Selected Duration": result.selected_duration_s,
        "Event Count": len(result.events),
        "Valid Frame Count": result.frame_count_valid,
        "Threshold Mode": config.threshold_mode.value if config else "disabled",
        "Threshold ON": result.activity.threshold_on_dbm,
        "Threshold OFF": result.activity.threshold_off_dbm,
        "Idle Level": idle.median_idle_dbm if idle else None,
        "RBW": request.rbw_hz,
        "ENBW": request.enbw_hz,
        "Power Semantics": request.power_semantics.value,
        "Calculation Quality": result.calculation_quality.value,
        "Warnings": " | ".join(result.warnings),
    }


def export_time_gated_summary_csv(
    result: TimeGatedChannelPowerResult,
    path: str | Path,
    source_path: str | Path | None = None,
    trace_name: str | None = None,
) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        summary = _time_gated_summary(result, source_path, trace_name)
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(summary))
            writer.writeheader()
            writer.writerow(summary)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_time_gated_frames_csv(
    result: TimeGatedChannelPowerResult,
    path: str | Path,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    series = result.series
    activity = result.activity
    event_ids = np.full(series.frame_count, "", dtype=object)
    for event in result.events:
        event_ids[event.start_frame_index : event.stop_frame_index + 1] = event.event_id
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Frame Index", "Timestamp", "Channel Power dBm", "Channel Power mW",
                "Smoothed Power dBm", "Automatic State", "Manual Override",
                "Effective State", "Event ID", "Valid",
            ])
            for index in range(series.frame_count):
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Экспорт отменён")
                writer.writerow([
                    int(series.frame_indices[index]), float(series.timestamps_s[index]),
                    float(series.power_dbm[index]), float(series.power_mw[index]),
                    float(activity.smoothed_power_dbm[index]),
                    bool(activity.automatic_activity_mask[index]),
                    ManualOverride(int(activity.manual_override_mask[index])).name,
                    bool(activity.effective_activity_mask[index]), event_ids[index],
                    bool(series.valid_mask[index]),
                ])
                if progress and index % 10_000 == 0:
                    progress(index / max(1, series.frame_count), "Экспорт кадров Channel Power")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_time_gated_events_csv(
    result: TimeGatedChannelPowerResult, path: str | Path
) -> Path:
    target = Path(path)
    temporary = _atomic_path(target)
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow([
                "Event ID", "Start Time", "Stop Time", "Duration", "Mean Power",
                "Maximum Power", "Minimum Power", "Frame Count", "Manual Edit",
            ])
            for event in result.events:
                writer.writerow([
                    event.event_id, event.start_time_s, event.stop_time_s, event.duration_s,
                    event.mean_power_dbm, event.max_power_dbm, event.min_power_dbm,
                    event.active_frame_count, event.manually_edited,
                ])
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_time_gated_json(
    result: TimeGatedChannelPowerResult,
    path: str | Path,
    source_path: str | Path | None = None,
    trace_name: str | None = None,
    progress: Progress | None = None,
    cancel: Event | None = None,
) -> Path:
    """Stream JSON frames so a long recording does not create a second large object graph."""
    target = Path(path)
    temporary = _atomic_path(target)
    summary = _time_gated_summary(result, source_path, trace_name)
    series = result.series
    activity = result.activity
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write('{"summary":')
            json.dump(summary, handle, ensure_ascii=False)
            handle.write(',"frames":[')
            for index in range(series.frame_count):
                if cancel is not None and cancel.is_set():
                    raise OperationCancelled("Экспорт отменён")
                if index:
                    handle.write(",")
                json.dump({
                    "frame_index": int(series.frame_indices[index]),
                    "timestamp": float(series.timestamps_s[index]),
                    "power_dbm": float(series.power_dbm[index]),
                    "power_mw": float(series.power_mw[index]),
                    "smoothed_power_dbm": float(activity.smoothed_power_dbm[index]),
                    "automatic_state": bool(activity.automatic_activity_mask[index]),
                    "manual_override": int(activity.manual_override_mask[index]),
                    "effective_state": bool(activity.effective_activity_mask[index]),
                    "valid": bool(series.valid_mask[index]),
                }, handle, ensure_ascii=False, allow_nan=True)
                if progress and index % 10_000 == 0:
                    progress(index / max(1, series.frame_count), "Экспорт Channel Power JSON")
            handle.write('],"events":')
            json.dump([asdict(event) for event in result.events], handle, ensure_ascii=False)
            handle.write("}")
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target
