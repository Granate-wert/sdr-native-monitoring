"""Immutable presentation snapshots for the Offline DFL workspace.

All types are plain frozen dataclasses without Qt objects or numpy buffers,
so the workspace can render them from GUI signals without touching the
presenter's worker state.  Pre-formatted strings are allowed where the
presenter is the appropriate formatting boundary.  The conventions match
:mod:`esw_dfl.ui.sweep_state` and :mod:`esw_dfl.ui.live_state`.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OfflineTraceSnapshot:
    """One spectrum trace as shown in the session tree."""

    trace_id: str
    name: str
    trace_mode: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class OfflineWaterfallSnapshot:
    """One waterfall as shown in the session tree."""

    waterfall_id: str
    name: str
    line_count: int
    point_count: int


@dataclass(frozen=True, slots=True)
class OfflineSessionSnapshot:
    """One open DFL session rendered in the tree and status areas."""

    session_id: str
    name: str
    source_path: str
    visible: bool
    source_type: str = "dfl_file"
    active_trace_id: str | None = None
    active_waterfall_id: str | None = None
    current_frame: int = 0
    frame_count: int = 0
    traces: tuple[OfflineTraceSnapshot, ...] = ()
    waterfalls: tuple[OfflineWaterfallSnapshot, ...] = ()


@dataclass(frozen=True, slots=True)
class OfflineMarkerSnapshot:
    """One marker row for the context inspector table."""

    marker_id: str
    name: str
    marker_type: str
    frequency_mhz: str
    power_dbm: str
    delta_f_mhz: str
    delta_l_db: str
    timestamp: str
    trace_id: str | None
    enabled: bool
    locked: bool


@dataclass(frozen=True, slots=True)
class OfflineResultSnapshot:
    """One analysis result row for the measurements table."""

    result_id: str
    name: str
    key: str
    value: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class OfflinePlaybackSnapshot:
    """Timeline/playback state for the bottom bar."""

    playing: bool = False
    frame: int = 0
    frame_count: int = 0
    speed: str = "1×"
    fps: int = 60
    loop: bool = False
    no_skip: bool = False
    cursor_text: str = ""


@dataclass(frozen=True, slots=True)
class OfflineHeatmapSnapshot:
    """Heatmap/persistence controls state for the workspace."""

    enabled: bool = False
    mode: str = ""
    phase: str = "disabled"
    status: str = ""
    error: bool = False
    applied: bool = False
    stale: bool = False
    can_cancel: bool = False


@dataclass(frozen=True, slots=True)
class OfflineStatusSnapshot:
    """Status-line summary."""

    source_path: str = ""
    trace_summary: str = ""
    metadata_summary: str = ""


@dataclass(frozen=True, slots=True)
class OfflineWorkspaceSnapshot:
    """Everything the Offline DFL workspace needs for one UI refresh."""

    generation: int = 0
    active_session_id: str | None = None
    sessions: tuple[OfflineSessionSnapshot, ...] = ()
    playback: OfflinePlaybackSnapshot = field(default_factory=OfflinePlaybackSnapshot)
    heatmap: OfflineHeatmapSnapshot = field(default_factory=OfflineHeatmapSnapshot)
    status: OfflineStatusSnapshot = field(default_factory=OfflineStatusSnapshot)
    busy: bool = False
    busy_text: str = ""
    error: str | None = None
    workspace_path: str | None = None


__all__ = [
    "OfflineHeatmapSnapshot",
    "OfflineMarkerSnapshot",
    "OfflinePlaybackSnapshot",
    "OfflineResultSnapshot",
    "OfflineSessionSnapshot",
    "OfflineStatusSnapshot",
    "OfflineTraceSnapshot",
    "OfflineWaterfallSnapshot",
    "OfflineWorkspaceSnapshot",
]
