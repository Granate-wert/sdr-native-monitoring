"""Immutable presentation state for the Recording & Replay workspace.

All types in this module are plain dataclasses: they carry no Qt objects,
no numpy buffers, no native handles and no ``Path`` objects for recorded
inputs, so the workspace can render them from a GUI timer without touching
the presenter thread.  Values are pre-formatted strings so the presenter
stays the single place where units and number formatting are decided.

Privacy invariant: snapshots never contain the recording output URI as a
full path; only the user-facing basename is exposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class RecordingRunState(StrEnum):
    """Lifecycle of the recording half of the workspace."""

    IDLE = "idle"
    CONFIGURED = "configured"
    RUNNING = "running"
    STOPPING = "stopping"
    COMPLETED = "completed"
    FAILED = "failed"


class ReplayRunState(StrEnum):
    """Lifecycle of the replay half of the workspace."""

    EMPTY = "empty"
    LOADED = "loaded"
    PLAYING = "playing"
    PAUSED = "paused"
    FINISHED = "finished"
    FAILED = "failed"


class ReplaySourceKind(StrEnum):
    """Which recorded stream is open for replay."""

    NONE = "none"
    IQ = "iq"
    SPECTRUM = "spectrum"


@dataclass(frozen=True, slots=True)
class RecordingSetupSnapshot:
    """User-chosen recording setup and the storage forecast verdict."""

    record_iq: bool
    record_spectrum: bool
    duration_s: str
    filename_template: str
    estimated_bytes: str | None = None
    free_bytes: str | None = None
    sufficient: str | None = None  # "yes" | "no" | "unknown"
    queue_capacity: int = 8
    error: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingHealthSnapshot:
    """Live counters while recording runs (from ``RecordingStats``)."""

    enqueued: str = "0"
    written_iq_samples: str = "0"
    written_spectrum_frames: str = "0"
    dropped_items: str = "0"
    gap_count: str = "0"
    queue_depth: str = "0"
    queue_high_water: str = "0"
    queue_capacity: str = "0"
    elapsed_s: str = "0.0"
    stopped_on_overflow: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ReplayStateSnapshot:
    """Open recording metadata and playback position."""

    kind: ReplaySourceKind
    name: str  # basename only, never a full path
    sample_count: str = "0"
    frame_count: str = "0"
    position_label: str = "0 / 0"
    duration_label: str = "00:00"
    gap_count: str = "0"
    reprocess_backend: str | None = None
    calibrated: bool = False
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RecordingWorkspaceSnapshot:
    """Everything the Recording & Replay workspace needs for one refresh."""

    generation: int
    recording_state: RecordingRunState
    replay_state: ReplayRunState
    setup: RecordingSetupSnapshot | None = None
    health: RecordingHealthSnapshot | None = None
    replay: ReplayStateSnapshot | None = None
    confirmation_required: bool = False
    error: str | None = None
    stale: bool = False


__all__ = [
    "RecordingHealthSnapshot",
    "RecordingRunState",
    "RecordingSetupSnapshot",
    "RecordingWorkspaceSnapshot",
    "ReplayRunState",
    "ReplaySourceKind",
    "ReplayStateSnapshot",
]
