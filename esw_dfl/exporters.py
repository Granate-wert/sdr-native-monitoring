from __future__ import annotations

import csv
import json
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from .models import DflDocument, SpectrogramInfo, SpectrogramPreview, TraceData
from .plotting import save_spectrogram_png, save_trace_png
from .spectrogram import iter_spectrogram_rows


ProgressCallback = Callable[[float, str], None]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^\w. -]+", "_", value, flags=re.UNICODE).strip(" ._")
    return cleaned[:120] or "export"


def export_trace_csv(trace: TraceData, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    x_name = "frequency_hz" if trace.x_unit == "Hz" else f"x_{trace.x_unit or 'value'}"
    y_name = "level_dbm" if trace.y_unit == "dBm" else f"y_{trace.y_unit or 'value'}"
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([x_name, y_name])
        writer.writerows(zip(trace.x.tolist(), trace.y.tolist()))
    return target


def export_trace_npz(trace: TraceData, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target,
        x=trace.x,
        y=trace.y,
        x_unit=trace.x_unit,
        y_unit=trace.y_unit,
        title=trace.title,
        mode=trace.mode,
        detector=trace.detector,
        update_mode=trace.update_mode,
        source_stream=trace.source_stream,
    )
    return target


def export_preview_csv(preview: SpectrogramPreview, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    frequencies = preview.info.frequencies_hz[: preview.values.shape[1]]
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp_utc", "unix_seconds", "line_index", *frequencies.tolist()])
        for index, timestamp, values in zip(
            preview.line_indices, preview.timestamps, preview.values
        ):
            iso = datetime.fromtimestamp(float(timestamp), timezone.utc).isoformat()
            writer.writerow([iso, f"{timestamp:.9f}", int(index), *values.tolist()])
    return target


def export_preview_npz(preview: SpectrogramPreview, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        target,
        frequencies_hz=preview.info.frequencies_hz[: preview.values.shape[1]],
        timestamps=preview.timestamps,
        line_indices=preview.line_indices,
        values=preview.values,
        y_unit=preview.info.y_unit,
        mode=preview.info.mode,
        source_stream=preview.info.source_stream,
    )
    return target


def export_full_spectrogram_csv(
    source_path: str | Path,
    info: SpectrogramInfo,
    target_path: str | Path,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Stream all rows to a wide CSV; rows retain the instrument's saved order."""
    target = Path(target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "timestamp_utc",
                    "unix_seconds",
                    "line_index",
                    *info.frequencies_hz.tolist(),
                ]
            )
            for row in iter_spectrogram_rows(
                source_path, info, selected_lines=None, progress=progress, cancel=cancel
            ):
                iso = (
                    datetime.fromtimestamp(row.timestamp, timezone.utc).isoformat()
                    if np.isfinite(row.timestamp)
                    else ""
                )
                writer.writerow(
                    [iso, f"{row.timestamp:.9f}", row.line_index, *row.values.tolist()]
                )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def export_metadata_json(document: DflDocument, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = document.summary()
    payload["settings"] = document.settings
    payload["streams"] = document.streams
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def export_document_bundle(
    document: DflDocument,
    output_dir: str | Path,
    previews: dict[str, SpectrogramPreview] | None = None,
) -> list[Path]:
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for trace in document.traces:
        base = safe_name(trace.title)
        written.append(export_trace_csv(trace, folder / f"{base}.csv"))
        png = folder / f"{base}.png"
        save_trace_png(trace, png)
        written.append(png)
    for preview in (previews or {}).values():
        base = safe_name(preview.info.title)
        written.append(export_preview_csv(preview, folder / f"{base}_preview.csv"))
        written.append(export_preview_npz(preview, folder / f"{base}_preview.npz"))
        png = folder / f"{base}_preview.png"
        save_spectrogram_png(preview, png)
        written.append(png)
    written.append(export_metadata_json(document, folder / "metadata.json"))
    return written
