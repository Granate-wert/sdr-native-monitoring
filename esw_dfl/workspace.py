from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .domain import FrequencyRegion, Marker, MarkerType, MeasurementSession, TimeRegion


WORKSPACE_VERSION = 2


def session_to_workspace(session: MeasurementSession) -> dict[str, Any]:
    try:
        stat = session.source_path.stat()
        source_fingerprint: dict[str, int] = {
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        source_fingerprint = {}
    return {
        "session_id": session.session_id,
        "source_path": str(session.source_path),
        "name": session.name,
        "active_trace_id": session.active_trace_id,
        "active_waterfall_id": session.active_waterfall_id,
        "current_frame": session.current_frame,
        "comments": session.comments,
        "display_state": session.display_state,
        "source_fingerprint": source_fingerprint,
        "trace_display": {
            key: {"enabled": trace.enabled, "color": trace.color}
            for key, trace in session.traces.items()
        },
        "markers": [
            {**asdict(marker), "marker_type": marker.marker_type.value}
            for marker in session.markers
        ],
        "frequency_regions": [asdict(region) for region in session.frequency_regions],
        "time_regions": [asdict(region) for region in session.time_regions],
        "waterfalls": {
            key: {
                "min_level": waterfall.min_level,
                "max_level": waterfall.max_level,
                "colormap": waterfall.colormap,
                "time_direction": waterfall.time_direction,
            }
            for key, waterfall in session.waterfalls.items()
        },
    }


def apply_workspace_session(session: MeasurementSession, payload: dict[str, Any]) -> None:
    session.name = str(payload.get("name", session.name))
    session.comments = str(payload.get("comments", ""))
    session.active_trace_id = payload.get("active_trace_id") or session.active_trace_id
    session.active_waterfall_id = payload.get("active_waterfall_id") or session.active_waterfall_id
    session.current_frame = int(payload.get("current_frame", 0))
    session.display_state.update(payload.get("display_state", {}))
    fingerprint = payload.get("source_fingerprint", {})
    try:
        stat = session.source_path.stat()
        session.display_state["source_changed_since_workspace"] = bool(
            fingerprint
            and (
                int(fingerprint.get("size", -1)) != stat.st_size
                or int(fingerprint.get("mtime_ns", -1)) != stat.st_mtime_ns
            )
        )
    except OSError:
        session.display_state["source_changed_since_workspace"] = True
    for key, state in payload.get("trace_display", {}).items():
        if key in session.traces:
            session.traces[key].enabled = bool(state.get("enabled", True))
            session.traces[key].color = str(state.get("color", session.traces[key].color))
    session.markers = []
    for marker in payload.get("markers", []):
        values = dict(marker)
        raw_type = values.get("marker_type", MarkerType.MANUAL)
        if raw_type == "Normal":
            raw_type = MarkerType.MANUAL
        try:
            values["marker_type"] = MarkerType(raw_type)
            session.markers.append(Marker(**values))
        except (TypeError, ValueError):
            continue
    session.frequency_regions = []
    for region in payload.get("frequency_regions", []):
        try:
            session.frequency_regions.append(FrequencyRegion(**region))
        except TypeError:
            continue
    session.time_regions = []
    for region in payload.get("time_regions", []):
        try:
            session.time_regions.append(TimeRegion(**region))
        except TypeError:
            continue
    for key, state in payload.get("waterfalls", {}).items():
        if key in session.waterfalls:
            waterfall = session.waterfalls[key]
            waterfall.min_level = state.get("min_level")
            waterfall.max_level = state.get("max_level")
            waterfall.colormap = str(state.get("colormap", waterfall.colormap))
            waterfall.time_direction = str(state.get("time_direction", waterfall.time_direction))


def write_workspace(
    path: str | Path,
    sessions: list[MeasurementSession],
    active_session_id: str | None,
    ui_state: dict[str, Any] | None = None,
) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    payload = {
        "format": "R&S DFL parcer workspace",
        "version": WORKSPACE_VERSION,
        "active_session_id": active_session_id,
        "sessions": [session_to_workspace(session) for session in sessions],
        "ui_state": ui_state or {},
    }
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return target


def read_workspace(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if payload.get("format") != "R&S DFL parcer workspace":
        raise ValueError("Неподдерживаемый формат рабочего пространства")
    if int(payload.get("version", 0)) > WORKSPACE_VERSION:
        raise ValueError("Рабочее пространство создано более новой версией программы")
    if not isinstance(payload.get("sessions"), list):
        raise ValueError("В рабочем пространстве отсутствует список сессий")
    return payload
