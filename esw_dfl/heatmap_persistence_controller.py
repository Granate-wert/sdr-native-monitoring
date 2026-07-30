"""Qt orchestration for the Heatmap persistence engine (review P2–P4).

Owns everything that used to live in ``MainWindow``'s heatmap job plumbing:
one-active-plus-one-latest routing, generation/source guard, cancellation,
fixed-result cache routing, render throttling and lifecycle. The controller
runs engine work through :class:`TaskWorker` on the shared thread pool; all
state transitions and snapshot emissions happen on the GUI thread via queued
signals.

Analytics and rendering are decoupled: sequential Rolling Exact advance reads
only the entered frames through the stateful engine (never compute_heatmap),
while ``snapshot_ready`` is emitted at most at ``render_fps`` — dropped
renders never drop analytical contributions. Rolling snapshots are NOT put
into the LRU cache; the cache serves fixed Selected/Full rebuilds only.

Exponential Decay runs through the wave-1 half-life data-time engine
(``rebuild_decay``/``advance_decay`` with an explicit effective timeline);
the legacy 0..1 coefficient path via compute_heatmap was removed in wave 3.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from threading import Condition, Event, Lock
from typing import Any

import numpy as np
from PySide6.QtCore import QObject, QRunnable, QTimer, Qt, Signal

from .frame_navigation import FrameSpanEvent, NavigationReason
from .heatmap import (
    HeatmapCache,
    HeatmapCacheKey,
    HeatmapConfig,
    HeatmapRangeMode,
    HeatmapRequest,
    HeatmapResult,
    frequency_grid_hash,
)
from .heatmap_persistence import (
    DecayState,
    PersistenceConfig,
    PersistenceEngine,
    PersistenceMode,
    PersistencePhase,
    PersistenceSnapshot,
    PersistenceSourceKey,
    PersistenceTarget,
    PersistenceWorkRequest,
    RebuildReason,
    RollingExactState,
    WindowUnit,
    decay_history_positions,
    resolve_persistence_bounds,
)
from .heatmap_worker import compute_heatmap, resolve_frame_range
from .models import SpectrogramInfo
from .spectrogram import SpectrogramFrameReader, SpectrogramIndex
from .workers import TaskWorker


AuditCallback = Callable[..., None]


@dataclass(slots=True)
class PersistenceSourceContext:
    """Everything the controller needs to read one source stream.

    ``frame_period_s`` is the documented fallback data-time step for decay
    when the source has no valid timestamps (acquisition timing or median
    positive timestamp delta); None means "no usable fallback period".
    """

    session_id: str
    waterfall_id: str
    source_id: str
    source_path: Path
    frequencies_hz: np.ndarray
    index: SpectrogramIndex
    info: SpectrogramInfo
    source_key: PersistenceSourceKey
    frame_period_s: float | None = None


@dataclass(frozen=True, slots=True)
class HeatmapPhaseEvent:
    """Phase transition for the UI: status text and stale-layer policy."""

    phase: PersistencePhase
    target_frame: int | None = None
    applied_frame: int | None = None
    rendered_frame: int | None = None
    render_lag_frames: int = 0
    frame_start: int | None = None
    frame_end: int | None = None
    lag_frames: int = 0
    hide_layer: bool = False
    reason: RebuildReason | None = None
    processed_frames: int = 0
    total_frames: int = 0
    message: str = ""


@dataclass(slots=True)
class _RebuildOutcome:
    state: RollingExactState | DecayState | None
    snapshot: PersistenceSnapshot | None


@dataclass(slots=True)
class _FixedOutcome:
    result: HeatmapResult
    snapshot: PersistenceSnapshot


@dataclass(slots=True)
class _WorkTicket:
    """One active/pending unit of work (test-facing via controller properties)."""

    kind: str  # "rebuild" | "advance" | "fixed"
    generation: int
    source_key: PersistenceSourceKey
    reason: RebuildReason
    target_frame: int
    target_timestamp: float | None
    navigation_generation: int
    worker: TaskWorker | None = None
    request: PersistenceWorkRequest | None = None
    target: PersistenceTarget | None = None
    heatmap_config: HeatmapConfig | None = None
    cache_key: HeatmapCacheKey | None = None
    current_frame: int = 0
    total: int = 0
    started_at: float = 0.0
    positions: list[int] | None = None  # SECONDS-window rebuilds resolve upstream


class _CountingReader:
    """Reader wrapper that counts completed reads for progress reporting."""

    def __init__(self, reader: SpectrogramFrameReader, reads_log: list[int] | None = None) -> None:
        self._reader = reader
        self.reads = 0
        self._reads_log = reads_log

    def read_frame(self, frame_index: int) -> Any:
        row = self._reader.read_frame(frame_index)
        self.reads += 1
        if self._reads_log is not None:
            self._reads_log.append(int(frame_index))
        return row

    def close(self) -> None:
        self._reader.close()


class _RollingTargetMailbox:
    """Thread-safe latest-target mailbox for one sequential Rolling Exact reader.

    It deliberately stores just the highest logical playhead. The worker owns
    an analytical cursor and derives every missing source frame from that
    cursor, so presentation coalescing cannot discard a spectrum frame.
    """

    def __init__(self, initial: PersistenceTarget) -> None:
        self._condition = Condition(Lock())
        self._target: PersistenceTarget | None = initial
        self._closed = False

    def publish(self, target: PersistenceTarget) -> None:
        with self._condition:
            if self._closed:
                return
            if self._target is None or target.frame_index >= self._target.frame_index:
                self._target = target
            self._condition.notify_all()

    def take_after(self, frame_index: int, cancel: Event) -> PersistenceTarget | None:
        with self._condition:
            while not self._closed and not cancel.is_set():
                if self._target is not None and self._target.frame_index > frame_index:
                    return self._target
                self._condition.wait(timeout=0.050)
        return None

    def latest_after(self, frame_index: int) -> PersistenceTarget | None:
        """Return the latest high-water target without waiting or consuming it."""
        with self._condition:
            target = self._target
            if target is not None and target.frame_index > frame_index:
                return target
        return None

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


@dataclass(slots=True)
class _StreamOutcome:
    snapshot: PersistenceSnapshot
    state: RollingExactState
    processed_frames: int
    elapsed_s: float
    published_at: float


class _RollingStreamSignals(QObject):
    snapshot = Signal(object)  # _StreamOutcome
    error = Signal(str, str)
    finished = Signal(object)  # _RollingExactStreamWorker


class _RollingExactStreamWorker(QRunnable):
    """One long-lived, read-only DFL reader for sequential Rolling Exact.

    The worker never touches Qt widgets. It waits without polling the DFL,
    consumes all source frames between its exact analytical cursor and the
    newest logical target, and publishes only one immutable snapshot per batch.
    """

    def __init__(
        self,
        *,
        engine: PersistenceEngine,
        state: RollingExactState,
        initial_target: PersistenceTarget,
        source_path: Path,
        index: SpectrogramIndex,
        reader_factory: Callable[[Path, SpectrogramIndex], Any],
        reads_log: list[int],
        publication_interval_s: float,
        max_frames_per_chunk: int = 128,
    ) -> None:
        super().__init__()
        self._engine = engine
        self._state = state
        self._source_path = source_path
        self._index = index
        self._reader_factory = reader_factory
        self._reads_log = reads_log
        self._mailbox = _RollingTargetMailbox(initial_target)
        self._publication_lock = Lock()
        self._publication_interval_s = max(0.001, float(publication_interval_s))
        self._max_frames_per_chunk = max(1, int(max_frames_per_chunk))
        self.cancel_event = Event()
        self.signals = _RollingStreamSignals()
        self.generation = initial_target.persistence_generation
        self.setAutoDelete(True)

    def publish(self, target: PersistenceTarget) -> None:
        self._mailbox.publish(target)

    def set_publication_interval(self, seconds: float) -> None:
        with self._publication_lock:
            self._publication_interval_s = max(0.001, float(seconds))

    def _publication_interval(self) -> float:
        with self._publication_lock:
            return self._publication_interval_s

    @staticmethod
    def _chunk_target(high_water: PersistenceTarget, frame_index: int) -> PersistenceTarget:
        """Keep generation/provenance while representing one exact analytical prefix."""
        return replace(
            high_water,
            frame_index=frame_index,
            timestamp=high_water.timestamp if frame_index == high_water.frame_index else None,
        )

    def cancel(self) -> None:
        self.cancel_event.set()
        self._mailbox.close()

    def run(self) -> None:
        reader = _CountingReader(self._reader_factory(self._source_path, self._index), self._reads_log)
        try:
            # The first prefix is submitted immediately. Thereafter a snapshot
            # is made at most once per presentation interval, while the reader
            # continues consuming every source frame in short cancellable chunks.
            next_publication_at = time.perf_counter()
            measurement_started = time.perf_counter()
            frames_since_publication = 0
            while not self.cancel_event.is_set():
                target = self._mailbox.take_after(self._state.target_frame, self.cancel_event)
                if target is None:
                    return
                while not self.cancel_event.is_set():
                    target = self._mailbox.latest_after(self._state.target_frame) or target
                    if target.frame_index <= self._state.target_frame:
                        break
                    start_frame = self._state.target_frame
                    chunk_end = min(
                        target.frame_index,
                        self._state.target_frame + self._max_frames_per_chunk,
                    )
                    chunk_target = self._chunk_target(target, chunk_end)
                    publish_snapshot = (
                        chunk_end >= target.frame_index
                        or time.perf_counter() >= next_publication_at
                    )
                    snapshot = self._engine.advance_exact_range(
                        self._state,
                        chunk_target,
                        reader,
                        self.cancel_event,
                        publish_snapshot=publish_snapshot,
                    )
                    frames_since_publication += max(0, self._state.target_frame - start_frame)
                    if self.cancel_event.is_set():
                        return
                    if snapshot is None:
                        continue
                    published_at = time.perf_counter()
                    measured_at = time.perf_counter()
                    self.signals.snapshot.emit(
                        _StreamOutcome(
                            snapshot=snapshot,
                            state=self._state,
                            processed_frames=frames_since_publication,
                            elapsed_s=max(0.0, measured_at - measurement_started),
                            published_at=published_at,
                        )
                    )
                    frames_since_publication = 0
                    measurement_started = measured_at
                    next_publication_at = published_at + self._publication_interval()
        except Exception as exc:
            if not self.cancel_event.is_set():
                import traceback

                self.signals.error.emit(str(exc), traceback.format_exc())
        finally:
            reader.close()
            self.signals.finished.emit(self)


def _run_rebuild_job(    engine: PersistenceEngine,
    request: PersistenceWorkRequest,
    kind: str,
    source_path: Path,
    index: SpectrogramIndex,
    ticket: _WorkTicket,
    reader_factory: Callable[[Path, SpectrogramIndex], Any] = SpectrogramFrameReader,
    reads_log: list[int] | None = None,
    progress: Callable[[float, str], None] | None = None,
    cancel: Event | None = None,
) -> _RebuildOutcome:
    """Worker: build a fresh engine state; returns state + snapshot.

    Progress is reported through the counting reader with a ~100 ms throttle,
    independent of the engine's own batching.
    """
    last_report = time.perf_counter()

    class _ProgressReader(_CountingReader):
        def read_frame(self, frame_index: int) -> Any:
            nonlocal last_report
            row = super().read_frame(frame_index)
            now = time.perf_counter()
            if progress is not None and now - last_report >= 0.1:
                last_report = now
                progress(self.reads / max(1, ticket.total), f"Heatmap: {self.reads:,}/{ticket.total:,} кадров")
            return row

    reader = _ProgressReader(reader_factory(source_path, index), reads_log)
    try:
        if kind == "decay":
            decay_state, decay_snapshot = engine.rebuild_decay(request, reader, cancel)
            return _RebuildOutcome(state=decay_state, snapshot=decay_snapshot)
        exact_state, exact_snapshot = engine.rebuild_exact(
            request, reader, cancel, positions=ticket.positions
        )
        return _RebuildOutcome(state=exact_state, snapshot=exact_snapshot)
    finally:
        reader.close()


def _run_advance_job(
    engine: PersistenceEngine,
    state: RollingExactState | DecayState,
    target: PersistenceTarget,
    source_path: Path,
    index: SpectrogramIndex,
    reader_factory: Callable[[Path, SpectrogramIndex], Any] = SpectrogramFrameReader,
    reads_log: list[int] | None = None,
    cancel: Event | None = None,
) -> PersistenceSnapshot | None:
    """Worker: incremental rolling advance; None means bounded rebuild required."""
    reader = _CountingReader(reader_factory(source_path, index), reads_log)
    try:
        if isinstance(state, RollingExactState):
            return engine.advance_exact(state, target, reader, cancel)
        return engine.advance_decay(state, target, reader, cancel)
    finally:
        reader.close()


def _run_fixed_job(
    source_path: Path,
    info: SpectrogramInfo,
    frequencies_hz: np.ndarray,
    config: HeatmapConfig,
    generation: int,
    session_id: str,
    waterfall_id: str,
    source_id: str,
    current_frame: int,
    index: SpectrogramIndex,
    ticket: _WorkTicket,
    progress: Callable[[float, str], None] | None = None,
    cancel: Event | None = None,
) -> HeatmapResult:
    """Worker: bounded fixed-range computation via compute_heatmap."""

    def frame_progress(processed: int, total: int) -> None:
        ticket.total = total
        if progress is not None:
            progress(processed / max(1, total), f"Heatmap: {processed:,}/{total:,} кадров")

    return compute_heatmap(
        source_path,
        info,
        frequencies_hz,
        config,
        generation,
        session_id,
        waterfall_id,
        source_id,
        current_frame=current_frame,
        index=index,
        progress=frame_progress,
        cancel=cancel,
    )


class HeatmapPersistenceController(QObject):
    """One-active-plus-one-latest routing and lifecycle for Heatmap persistence.

    Threading: public methods are GUI-thread only. Workers touch engine state
    in pool threads; results arrive via queued signals, so the current engine
    state is only ever handed to a new worker after the previous one
    finished. Test-facing read-only properties: ``active_ticket``,
    ``pending_ticket``, ``generation``, ``desired_target``,
    ``applied_snapshot``, ``phase``, ``engine_state``, ``cache``.
    """

    snapshot_ready = Signal(object)  # PersistenceSnapshot
    phase_changed = Signal(object)  # HeatmapPhaseEvent
    failed = Signal(str, str)

    def __init__(
        self,
        *,
        thread_pool: Any,
        reader_factory: Callable[[Path, SpectrogramIndex], Any] = SpectrogramFrameReader,
        audit: AuditCallback | None = None,
        render_fps: int = 60,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._pool = thread_pool
        self._reader_factory = reader_factory
        self._audit_cb: AuditCallback = audit if audit is not None else (lambda *args, **kwargs: None)
        self._engine = PersistenceEngine()
        self._cache = HeatmapCache()
        self._context: PersistenceSourceContext | None = None
        self._cached_frame_period_s: float | None = None
        self._frame_period_computed = False
        self._config: PersistenceConfig | None = None
        self._fixed_heatmap_config: HeatmapConfig | None = None
        self._decay_timeline_cache: np.ndarray | None = None
        self._state: RollingExactState | DecayState | None = None
        self._generation = 0
        self._active: _WorkTicket | None = None
        self._pending: _WorkTicket | None = None
        self._desired_target: PersistenceTarget | None = None
        self._applied_snapshot: PersistenceSnapshot | None = None
        self._phase = PersistencePhase.DISABLED
        self._enabled = False
        self._shutdown = False
        self._cancel_requested = False
        self._render_fps = max(1, int(render_fps))
        self._pending_render: PersistenceSnapshot | None = None
        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(max(1, round(1000.0 / self._render_fps)))
        self._render_timer.timeout.connect(self._render_timeout)
        self._advance_latencies_ms: deque[float] = deque(maxlen=200)
        self._reads_log: list[int] = []
        self._reads_at_last_apply = 0
        self._initial_rebuild_latency_ms = 0.0
        self._cancel_requested_at = 0.0
        self._reads_at_cancel = 0
        self._cancellation_latency_ms = 0.0
        self._reads_completed_after_cancel = 0
        self._first_render_at = 0.0
        self._last_render_emit_at = 0.0
        self._render_submitted_snapshot: PersistenceSnapshot | None = None
        self._render_submission_started: dict[tuple[int, int, int], float] = {}
        self._peak_structural_bytes = 0
        self._capacity_warning_active = False
        # Rolling Exact hands its mutable accumulator to exactly one long-lived
        # worker. The GUI owns only immutable snapshots and a safe state
        # reference for diagnostics after each applied batch.
        self._stream_worker: _RollingExactStreamWorker | None = None
        self._stream_analytical_target = -1
        self._diag: dict[str, float | int] = {
            "heatmap_requested_generation": 0,
            "heatmap_applied_generation": 0,
            "heatmap_processed_frames": 0,
            "heatmap_total_frames": 0,
            "heatmap_processing_fps": 0.0,
            "heatmap_batch_latency_ms": 0.0,
            "heatmap_stale_results_discarded": 0,
            "heatmap_cancel_count": 0,
            "heatmap_navigation_target": -1,
            "heatmap_analytical_target": -1,
            "heatmap_applied_snapshot_target": -1,
            "heatmap_lag_frames": 0,
            "heatmap_lag_data_seconds": 0.0,
            "heatmap_sequential_updates": 0,
            "heatmap_rebuild_count": 0,
            "heatmap_frames_decoded": 0,
            "heatmap_render_emitted": 0,
            "heatmap_render_dropped": 0,
            "heatmap_stream_batches": 0,
            "heatmap_stream_active": 0,
            "heatmap_render_submitted": 0,
            "heatmap_render_submit_latency_ms": 0.0,
            "heatmap_rendered_target": -1,
        }

    # --- test-facing read-only properties -----------------------------------
    @property
    def active_ticket(self) -> _WorkTicket | None:
        return self._active

    @property
    def pending_ticket(self) -> _WorkTicket | None:
        return self._pending

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def desired_target(self) -> PersistenceTarget | None:
        return self._desired_target

    @property
    def applied_snapshot(self) -> PersistenceSnapshot | None:
        return self._applied_snapshot

    @property
    def phase(self) -> PersistencePhase:
        return self._phase

    @property
    def engine_state(self) -> RollingExactState | DecayState | None:
        return self._state

    @property
    def cache(self) -> HeatmapCache:
        return self._cache

    @property
    def context_identity(self) -> tuple[str, str] | None:
        if self._context is None:
            return None
        return (self._context.session_id, self._context.waterfall_id)

    # --- audit/diagnostics ----------------------------------------------------
    def _audit(self, event: str, **details: Any) -> None:
        self._audit_cb(event, **details)

    def diagnostics(self) -> dict[str, float | int]:
        diag = dict(self._diag)
        diag["heatmap_active_request"] = 1 if self._active is not None else 0
        diag["heatmap_pending_request"] = 1 if self._pending is not None else 0
        diag["heatmap_cache_hit_ratio"] = self._cache.cache_hit_ratio
        diag["heatmap_frames_decoded"] = len(self._reads_log)
        if self._advance_latencies_ms:
            ordered = sorted(self._advance_latencies_ms)
            diag["heatmap_advance_latency_p50_ms"] = ordered[len(ordered) // 2]
            diag["heatmap_advance_latency_p95_ms"] = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            diag["heatmap_advance_latency_max_ms"] = ordered[-1]
        desired = self._desired_target.frame_index if self._desired_target is not None else -1
        applied = self._applied_snapshot.target_frame if self._applied_snapshot is not None else -1
        rendered = self._render_submitted_snapshot.target_frame if self._render_submitted_snapshot is not None else -1
        diag["heatmap_navigation_target"] = desired
        diag["heatmap_applied_snapshot_target"] = applied
        diag["heatmap_rendered_target"] = rendered
        diag["heatmap_visual_lag_frames"] = max(0, desired - rendered) if desired >= 0 and rendered >= 0 else 0
        diag["heatmap_lag_frames"] = max(0, desired - applied) if desired >= 0 and applied >= 0 else 0
        lag_seconds = 0.0
        if (
            self._context is not None
            and desired >= 0
            and applied >= 0
            and desired > applied
            and desired < self._context.index.timestamps.size
        ):
            timestamps = self._context.index.timestamps
            delta = timestamps[desired] - timestamps[min(applied, timestamps.size - 1)]
            if np.isfinite(delta):
                lag_seconds = float(max(0.0, delta))
        diag["heatmap_lag_data_seconds"] = lag_seconds
        if self._state is not None:
            diag["heatmap_analytical_target"] = self._state.target_frame
        # --- §10.2 performance contract metrics ------------------------------
        diag["initial_rebuild_latency_ms"] = self._initial_rebuild_latency_ms
        diag["sequential_update_latency_p50_ms"] = diag.get("heatmap_advance_latency_p50_ms", 0.0)
        diag["sequential_update_latency_p95_ms"] = diag.get("heatmap_advance_latency_p95_ms", 0.0)
        diag["sequential_update_latency_max_ms"] = diag.get("heatmap_advance_latency_max_ms", 0.0)
        diag["frames_decoded_per_update"] = len(self._reads_log) - self._reads_at_last_apply
        diag["cancellation_latency_ms"] = self._cancellation_latency_ms
        diag["reads_completed_after_cancel"] = self._reads_completed_after_cancel
        diag["analytical_frames_processed"] = len(self._reads_log)
        if self._first_render_at > 0.0:
            elapsed = time.perf_counter() - self._first_render_at
            diag["render_update_rate_hz"] = (
                self._diag["heatmap_render_emitted"] / elapsed if elapsed > 0.0 else 0.0
            )
        else:
            diag["render_update_rate_hz"] = 0.0
        diag["render_applied"] = diag["heatmap_render_emitted"]
        density_bytes = 0
        ring_bytes = 0
        if self._state is not None:
            density_bytes = int(
                self._state.accumulator.density.nbytes
                + self._state.accumulator.normalization_weights_by_frequency.nbytes
            )
            freq_bins = int(self._state.frequencies_hz.size)
            ring_bytes = len(self._state.contributions if isinstance(self._state, RollingExactState) else self._state.history) * (freq_bins * 2 + 64)
        diag["density_bytes"] = density_bytes
        diag["contribution_ring_bytes"] = ring_bytes
        diag["fixed_cache_bytes"] = self._cache.total_size_bytes
        structural = (
            density_bytes
            + ring_bytes
            + self._cache.total_size_bytes
            + (int(self._applied_snapshot.density.nbytes) if self._applied_snapshot is not None else 0)
        )
        self._peak_structural_bytes = max(self._peak_structural_bytes, structural)
        diag["peak_traced_memory_bytes"] = self._peak_structural_bytes
        frame_period = self._frame_period_s()
        diag["frame_period_s"] = frame_period if frame_period is not None else 0.0
        p95 = float(diag.get("heatmap_advance_latency_p95_ms", 0.0) or 0.0)
        if frame_period is not None and frame_period > 0.0 and p95 > 0.0:
            diag["processing_to_frame_period_ratio"] = p95 / (frame_period * 1000.0)
        else:
            diag["processing_to_frame_period_ratio"] = 0.0
        return diag

    def _frame_period_s(self) -> float | None:
        """Return the per-context data-time step, calculating it only once."""
        if self._context is None:
            return None
        if self._frame_period_computed:
            return self._cached_frame_period_s
        timestamps = self._context.index.timestamps
        period = self._context.frame_period_s
        if timestamps.size > 1:
            deltas = np.diff(timestamps)
            positive = deltas[np.isfinite(deltas) & (deltas > 0)]
            if positive.size:
                period = float(np.median(positive))
        self._cached_frame_period_s = period
        self._frame_period_computed = True
        return period

    # --- lifecycle ------------------------------------------------------------
    def set_context(self, context: PersistenceSourceContext | None) -> None:
        """§5.9: cancel old source work, clear rolling state, hand off context."""
        self._generation += 1
        self._stop_rolling_stream()
        self._drop_pending()
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()
        self._state = None
        self._context = context
        self._cached_frame_period_s = None
        self._frame_period_computed = False
        self._applied_snapshot = None
        self._render_submitted_snapshot = None
        self._render_submission_started.clear()
        self._desired_target = None
        self._audit(
            "HEATMAP_CONTEXT_CHANGED",
            session_id=context.session_id if context else None,
            waterfall_id=context.waterfall_id if context else None,
        )

    def set_render_fps(self, fps: int) -> None:
        self._render_fps = max(1, int(fps))
        self._render_timer.setInterval(max(1, round(1000.0 / self._render_fps)))
        if self._stream_worker is not None:
            self._stream_worker.set_publication_interval(1.0 / self._render_fps)

    @staticmethod
    def _snapshot_token(snapshot: PersistenceSnapshot) -> tuple[int, int, int]:
        return (snapshot.generation, snapshot.navigation_generation, snapshot.target_frame)

    def report_render_submitted(self, snapshot: PersistenceSnapshot) -> None:
        """Record that setImage accepted a specific immutable snapshot.

        This is a scene-submission acknowledgement, deliberately not a claim of
        GPU scan-out. It separates analytical/snapshot lag from Qt render lag.
        """
        if self._context is None or snapshot.source_key != self._context.source_key:
            return
        token = self._snapshot_token(snapshot)
        emitted_at = self._render_submission_started.pop(token, None)
        now = time.perf_counter()
        self._render_submitted_snapshot = snapshot
        self._diag["heatmap_render_submitted"] += 1
        self._diag["heatmap_rendered_target"] = snapshot.target_frame
        if emitted_at is not None:
            self._diag["heatmap_render_submit_latency_ms"] = (now - emitted_at) * 1000.0
        self._audit(
            "HEATMAP_RENDER_SUBMITTED",
            generation=snapshot.generation,
            target_frame=snapshot.target_frame,
            submission_latency_ms=self._diag["heatmap_render_submit_latency_ms"],
        )

    def enable(
        self,
        config: PersistenceConfig,
        target_frame: int,
        target_timestamp: float | None,
    ) -> None:
        """§5.1: initial build of the rolling/decay target."""
        if self._shutdown:
            return
        self._enabled = True
        self._stop_rolling_stream()
        self._config = config
        self._state = None
        self._generation += 1
        self._audit(
            "HEATMAP_PERSISTENCE_ENABLED",
            mode=config.mode.value,
            window_frames=config.window_frames,
            generation=self._generation,
        )
        if config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            timeline = self._decay_timeline()
            if timeline is None:
                # No silent 0.95 fallback, no wall clock: explicit error phase.
                self._phase = PersistencePhase.ERROR
                self._audit(
                    "HEATMAP_FAILED",
                    reason="decay_requires_timestamps",
                    message="Decay requires valid timestamps or acquisition period",
                )
                self._emit_phase(message="Decay requires valid timestamps or acquisition period")
                return
            self._decay_timeline_cache = timeline
            target_timestamp = float(timeline[min(target_frame, timeline.size - 1)])
        target = self._make_target(target_frame, target_timestamp, self._generation)
        self._desired_target = target
        if config.mode is PersistenceMode.ROLLING_EXACT:
            self._route_rebuild(target, RebuildReason.INITIAL)
        elif config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            self._route_rebuild(target, RebuildReason.INITIAL)
        else:
            self.recalculate()

    def _decay_timeline(self) -> np.ndarray | None:
        """Effective per-frame timeline for decay (documented fallback policy).

        Returns the index timestamps when every frame has a finite timestamp;
        otherwise a synthesized ``frame_index * frame_period_s`` when the
        context carries a valid fallback period; otherwise None (the caller
        reports the explicit ERROR phase — never a hidden coefficient).
        """
        if self._context is None:
            return None
        timestamps = np.asarray(self._context.index.timestamps, dtype=np.float64)
        if timestamps.size and bool(np.isfinite(timestamps).all()):
            return np.arange(timestamps.size, dtype=np.float64) * 0.0 + timestamps
        period = self._context.frame_period_s
        if period is not None and np.isfinite(period) and period > 0:
            return np.arange(timestamps.size, dtype=np.float64) * float(period)
        return None

    def disable(self) -> None:
        self._enabled = False
        self._generation += 1
        self._stop_rolling_stream()
        self._drop_pending()
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()
        self._state = None
        self._applied_snapshot = None
        self._render_submitted_snapshot = None
        self._render_submission_started.clear()
        self._phase = PersistencePhase.DISABLED
        self._emit_phase()

    def pause(self) -> None:
        """§5.6: no wall-clock effects; a pending latest-target job keeps running."""
        self._audit("HEATMAP_TARGET_RECEIVED", reason="pause", note="no wall-clock decay")

    def stop(self, target: int = 0) -> None:
        """§5.7: explicit initial window at ``target`` (playback Stop)."""
        if not self._enabled or self._context is None:
            return
        timestamp = self._timestamp_of(target)
        if self._config is not None and self._config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            if self._decay_timeline_cache is not None:
                timestamp = float(self._decay_timeline_cache[min(target, self._decay_timeline_cache.size - 1)])
        self._desired_target = self._make_target(target, timestamp, self._generation + 1)
        self._route_rebuild(self._desired_target, RebuildReason.STOP)

    def shutdown(self) -> None:
        """§5.10: cancel everything; late Qt callbacks become no-ops."""
        self._shutdown = True
        self._phase = PersistencePhase.SHUTTING_DOWN
        self._drop_pending()
        self._generation += 1
        self._stop_rolling_stream()
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()
        self._audit("HEATMAP_PERSISTENCE_SHUTDOWN", **self.diagnostics())

    def invalidate_session(self, session_id: str) -> None:
        """Session removal: drop its work and cached densities, guard late callbacks."""
        if self._active is not None and self._active.worker is not None and self._active.source_key.session_id == session_id:
            self._active.worker.cancel()
        if self._pending is not None and self._pending.source_key.session_id == session_id:
            self._pending = None
        if self._context is not None and self._context.session_id == session_id:
            self._stop_rolling_stream()
        dropped = self._cache.invalidate_session(session_id)
        self._generation += 1
        if self._applied_snapshot is not None and self._applied_snapshot.source_key.session_id == session_id:
            self._applied_snapshot = None
        if self._state is not None and self._state.source_key.session_id == session_id:
            self._state = None
        if dropped:
            self._audit("HEATMAP_CACHE_INVALIDATED", removed_session_id=session_id, entries=dropped)

    # --- requests ---------------------------------------------------------------
    def on_frame_span(self, event: FrameSpanEvent) -> None:
        """Route one logical FrameSpanEvent (§5.2-5.5); fixed modes ignore it."""
        if self._shutdown or not self._enabled or self._context is None or self._config is None:
            return
        if not self._config.follow_playhead:
            return  # analytics frozen by user policy; navigation keeps moving
        self._diag["heatmap_navigation_target"] = event.new_target
        timestamp = self._timestamp_of(event.new_target)
        if self._config.mode is PersistenceMode.EXPONENTIAL_DECAY and self._decay_timeline_cache is not None:
            timestamp = float(self._decay_timeline_cache[min(event.new_target, self._decay_timeline_cache.size - 1)])
        target = self._make_target(event.new_target, timestamp, self._generation, event)
        self._desired_target = target
        self._audit(
            "HEATMAP_TARGET_RECEIVED",
            frame=event.new_target,
            reason=event.reason.value,
            generation=event.generation,
        )
        if self._config.mode not in (PersistenceMode.ROLLING_EXACT, PersistenceMode.EXPONENTIAL_DECAY):
            return  # fixed Selected/Full ignore playback spans by design
        if (
            self._config.mode is PersistenceMode.ROLLING_EXACT
            and event.reason is NavigationReason.PLAYBACK
            and event.direction == 1
            and event.new_target > event.previous_target
        ):
            # The reader sees every source frame from its own analytical cursor
            # to this high-water target. UI events may be coalesced; source
            # spectra never are.
            if self._stream_worker is not None:
                self._publish_stream_target(target)
                return
            if self._active is not None:
                self._pending = self._make_stream_ticket(target)
                self._phase = PersistencePhase.UPDATING
                self._emit_phase(hide_layer=False)
                return
            if self._start_rolling_stream(target):
                self._phase = PersistencePhase.UPDATING
                self._emit_phase(hide_layer=False)
                return
        if self._is_sequential_forward(event):
            self._route_advance(target)
            return
        if (
            event.reason is NavigationReason.PLAYBACK
            and event.direction == 1
            and event.new_target > event.previous_target
        ):
            self._route_playback_gap(target)
            return
        reason = RebuildReason.SEEK
        if event.reason is NavigationReason.PLAYBACK and event.new_target < event.previous_target:
            reason = RebuildReason.LOOP
        elif event.direction == -1:
            reason = RebuildReason.REVERSE
        if reason is RebuildReason.LOOP:
            self._audit(
                "HEATMAP_LOOP_EPOCH_RESET",
                previous_target=event.previous_target,
                new_target=event.new_target,
            )
        self._route_rebuild(target, reason)

    def recalculate(self) -> None:
        """Manual rebuild of the current desired target (rolling/decay) or fixed job."""
        if self._shutdown or not self._enabled or self._context is None:
            return
        if self._config is not None and self._config.mode in (
            PersistenceMode.ROLLING_EXACT,
            PersistenceMode.EXPONENTIAL_DECAY,
        ):
            target = self._desired_target or self._make_target(
                self._context.index.frame_count - 1, None, self._generation + 1
            )
            self._desired_target = target
            self._route_rebuild(target, RebuildReason.CACHE_MISS)
            return
        if self._fixed_heatmap_config is not None:
            self._start_fixed_request(self._fixed_heatmap_config, self._current_frame())

    def request_fixed(self, config: HeatmapConfig, current_frame: int) -> None:
        """Bounded Selected/Full computation (cacheable, ignores playback spans)."""
        if self._shutdown or self._context is None:
            return
        # Fixed modes never go through enable(); accepting a fixed request is
        # what marks the controller enabled for this context.
        self._enabled = True
        self._fixed_heatmap_config = config
        self._start_fixed_request(config, current_frame)

    def try_show_cached(self, config: HeatmapConfig, current_frame: int) -> bool:
        """Apply a fixed cache hit synchronously; False when nothing is cached."""
        if self._context is None:
            return False
        ticket = self._make_fixed_ticket(config, current_frame, bump_generation=False)
        if ticket is None:
            return False
        assert ticket.cache_key is not None
        cached = self._cache.get(ticket.cache_key)
        if cached is None:
            return False
        snapshot = self._snapshot_from_fixed(cached, ticket)
        self._applied_snapshot = snapshot
        self._phase = PersistencePhase.CURRENT
        self._diag["heatmap_applied_generation"] = self._generation
        self._audit("HEATMAP_REBUILD_APPLIED", cache_hit=True, fixed=True)
        self.snapshot_ready.emit(snapshot)
        self._emit_phase()
        return True

    def cancel(self) -> None:
        """User cancel: invalidate in-flight work; report a settled CANCELLED phase."""
        if self._active is None and self._pending is None and self._stream_worker is None:
            return
        self._generation += 1
        self._stop_rolling_stream()
        self._cancel_requested = True
        self._cancel_requested_at = time.perf_counter()
        self._reads_at_cancel = len(self._reads_log)
        self._drop_pending()
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()

    def clear(self) -> None:
        self._generation += 1
        self._stop_rolling_stream()
        self._drop_pending()
        self._state = None
        self._applied_snapshot = None
        self._render_submitted_snapshot = None
        self._render_submission_started.clear()
        self._phase = PersistencePhase.EMPTY
        self._audit("HEATMAP_PERSISTENCE_CLEARED")
        self._emit_phase()

    def structural_config_changed(
        self,
        config: PersistenceConfig,
    ) -> None:
        """§5.8: structural change rebuilds the rolling/decay target."""
        if self._shutdown or not self._enabled:
            return
        self._config = config
        self._state = None
        if config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            timeline = self._decay_timeline()
            if timeline is None:
                self._phase = PersistencePhase.ERROR
                self._audit(
                    "HEATMAP_FAILED",
                    reason="decay_requires_timestamps",
                    message="Decay requires valid timestamps or acquisition period",
                )
                self._emit_phase(message="Decay requires valid timestamps or acquisition period")
                return
            self._decay_timeline_cache = timeline
        target = self._desired_target
        if target is None and self._context is not None:
            target = self._make_target(self._context.index.frame_count - 1, None, self._generation)
        if target is None:
            return
        self._desired_target = target
        self._route_rebuild(target, RebuildReason.CONFIG_CHANGE)

    # --- continuous Rolling Exact stream ---------------------------------------
    def _stop_rolling_stream(self) -> None:
        worker = self._stream_worker
        self._stream_worker = None
        self._diag["heatmap_stream_active"] = 0
        if worker is not None:
            worker.cancel()

    def _start_rolling_stream(self, target: PersistenceTarget) -> bool:
        """Transfer exact state to one reader which consumes every missing frame."""
        if (
            self._context is None
            or not isinstance(self._state, RollingExactState)
            or self._stream_worker is not None
        ):
            return False
        worker = _RollingExactStreamWorker(
            engine=self._engine,
            state=self._state,
            initial_target=target,
            source_path=self._context.source_path,
            index=self._context.index,
            reader_factory=self._reader_factory,
            reads_log=self._reads_log,
            publication_interval_s=1.0 / self._render_fps,
        )
        worker.signals.snapshot.connect(
            lambda outcome, worker=worker: self._on_stream_snapshot(worker, outcome)
        )
        worker.signals.error.connect(
            lambda message, details, worker=worker: self._on_stream_error(worker, message, details)
        )
        worker.signals.finished.connect(
            lambda worker: self._on_stream_finished(worker)
        )
        self._stream_worker = worker
        self._stream_analytical_target = self._state.target_frame
        self._diag["heatmap_stream_active"] = 1
        self._audit(
            "HEATMAP_STREAM_STARTED",
            generation=target.persistence_generation,
            source_frame=self._state.target_frame,
            target_frame=target.frame_index,
        )
        self._pool.start(worker)
        return True

    def _publish_stream_target(self, target: PersistenceTarget) -> None:
        worker = self._stream_worker
        if worker is None:
            return
        worker.publish(target)
        self._phase = PersistencePhase.UPDATING
        self._emit_phase(hide_layer=False)
        self._audit(
            "HEATMAP_STREAM_TARGET_PUBLISHED",
            generation=target.persistence_generation,
            target_frame=target.frame_index,
        )

    def _on_stream_snapshot(self, worker: _RollingExactStreamWorker, outcome: _StreamOutcome) -> None:
        if (
            self._shutdown
            or worker is not self._stream_worker
            or worker.generation != self._generation
            or self._context is None
            or outcome.snapshot.source_key != self._context.source_key
        ):
            self._diag["heatmap_stale_results_discarded"] += 1
            return
        snapshot = outcome.snapshot
        self._state = outcome.state
        self._stream_analytical_target = snapshot.target_frame
        processed = max(1, outcome.processed_frames)
        self._diag["heatmap_stream_batches"] += 1
        self._diag["heatmap_sequential_updates"] += processed
        self._diag["heatmap_processing_fps"] = processed / max(outcome.elapsed_s, 1e-9)
        self._diag["heatmap_batch_latency_ms"] = outcome.elapsed_s * 1000.0 / processed
        self._advance_latencies_ms.append(outcome.elapsed_s * 1000.0 / processed)
        self._reads_at_last_apply = len(self._reads_log)
        self._applied_snapshot = snapshot
        self._diag["heatmap_applied_generation"] = snapshot.generation
        self._diag["heatmap_processed_frames"] = snapshot.processed_frames
        self._diag["heatmap_total_frames"] = snapshot.processed_frames
        self._queue_render(snapshot)
        desired = self._desired_target.frame_index if self._desired_target is not None else snapshot.target_frame
        self._phase = PersistencePhase.CURRENT if snapshot.target_frame >= desired else PersistencePhase.UPDATING
        self._audit(
            "HEATMAP_STREAM_APPLIED",
            generation=snapshot.generation,
            target_frame=snapshot.target_frame,
            analytical_frames=processed,
        )
        self._check_capacity()
        self._emit_phase(hide_layer=False)

    def _on_stream_error(
        self, worker: _RollingExactStreamWorker, message: str, details: str
    ) -> None:
        if worker is not self._stream_worker or worker.generation != self._generation:
            return
        self._phase = PersistencePhase.ERROR
        self._emit_phase(message=message)
        self.failed.emit(message, details)

    def _on_stream_finished(self, worker: _RollingExactStreamWorker) -> None:
        if worker is not self._stream_worker:
            return
        self._stream_worker = None
        self._diag["heatmap_stream_active"] = 0
        if worker.cancel_event.is_set():
            self._diag["heatmap_cancel_count"] += 1
            self._audit("HEATMAP_STREAM_CANCELLED", generation=worker.generation)

    # --- routing internals ------------------------------------------------------
    def _timestamp_of(self, frame_index: int) -> float | None:
        if self._context is None:
            return None
        timestamps = self._context.index.timestamps
        if frame_index < 0 or frame_index >= timestamps.size:
            return None
        value = float(timestamps[frame_index])
        return value if np.isfinite(value) else None

    def _current_frame(self) -> int:
        if self._desired_target is not None:
            return self._desired_target.frame_index
        if self._context is not None:
            return self._context.index.frame_count - 1
        return 0

    def _make_target(
        self,
        frame_index: int,
        timestamp: float | None,
        generation: int,
        event: FrameSpanEvent | None = None,
    ) -> PersistenceTarget:
        assert self._context is not None
        return PersistenceTarget(
            source_key=self._context.source_key,
            frame_index=int(frame_index),
            timestamp=timestamp,
            navigation_generation=int(event.generation) if event is not None else 0,
            persistence_generation=generation,
            reason=event.reason if event is not None else NavigationReason.API,
        )

    def _is_sequential_forward(self, event: FrameSpanEvent) -> bool:
        """§5.2: PLAYBACK, direction +1, forward, same config, overlapping window."""
        if event.reason is not NavigationReason.PLAYBACK or event.direction != 1:
            return False
        if event.new_target <= event.previous_target:
            return False
        if self._config is None:
            return False
        if self._config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            # Decay advances forward through data time; backward/loop rebuilds.
            return isinstance(self._state, DecayState) and event.new_target > self._state.target_frame
        if self._config.mode is not PersistenceMode.ROLLING_EXACT:
            return False
        if not isinstance(self._state, RollingExactState) or not self._state.contributions:
            return False
        if event.new_target <= self._state.target_frame:
            return False
        config = self._config
        if config.window_unit is not WindowUnit.FRAMES:
            # A time-window is also an exact rolling window. Keep the
            # incremental path when the old and target windows overlap;
            # otherwise a bounded final-window rebuild avoids stale backlog.
            if config.window_unit is not WindowUnit.SECONDS or config.window_seconds is None:
                return False
            target_timestamp = self._timestamp_of(event.new_target)
            if target_timestamp is None:
                return False
            context = self._context
            if context is None:
                return False
            timestamps = np.asarray(context.index.timestamps, dtype=np.float64)
            cutoff = target_timestamp - config.window_seconds
            positions = np.flatnonzero(
                np.isfinite(timestamps)
                & (timestamps >= cutoff)
                & (timestamps <= target_timestamp)
                & (np.arange(timestamps.size) <= event.new_target)
            )
            return bool(positions.size) and int(positions[0]) <= self._state.current_end
        new_start, _new_end = resolve_persistence_bounds(
            config, event.new_target, self._state.frame_count
        )
        return new_start <= self._state.current_end

    def _route_advance(self, target: PersistenceTarget) -> None:
        ticket = _WorkTicket(
            kind="advance",
            generation=self._generation,
            source_key=target.source_key,
            reason=RebuildReason.INITIAL,
            target_frame=target.frame_index,
            target_timestamp=target.timestamp,
            navigation_generation=target.navigation_generation,
            target=target,
        )
        if self._active is not None:
            self._pending = ticket  # latest-target-wins: replace the pending slot
        else:
            self._start_ticket(ticket)
        self._phase = PersistencePhase.UPDATING
        self._emit_phase(hide_layer=False)

    def _route_playback_gap(self, target: PersistenceTarget) -> None:
        """Compatibility path for a forward target outside the old window.

        Rolling Exact normally enters the continuous stream before this method.
        If a legacy active task is still settling, its successor is a stream,
        not a final-window rebuild: every frame after the settled cursor is
        consumed exactly once.

        A forward timestamp-mode skip is not a manual seek. When an initial
        rebuild or an older advance is already running, let it finish, retain
        its snapshot as the visible stale basis, and replace only the one
        pending final-window rebuild. This avoids the cancel/restart storm
        that otherwise prevents a Heatmap from ever reaching the renderer.
        """

        if self._stream_worker is not None:
            self._publish_stream_target(target)
            return
        if self._active is not None and self._config is not None and self._config.mode is PersistenceMode.ROLLING_EXACT:
            self._desired_target = target
            self._pending = self._make_stream_ticket(target)
            self._phase = PersistencePhase.UPDATING
            self._audit(
                "HEATMAP_PLAYBACK_GAP_COALESCED",
                target_frame=target.frame_index,
                active_kind=self._active.kind,
                preserves_intermediate_frames=True,
            )
            self._emit_phase(hide_layer=False, reason=RebuildReason.PLAYBACK_GAP)
            return
        if self._active is not None:
            self._desired_target = target
            self._pending = self._make_rebuild_ticket(target, RebuildReason.PLAYBACK_GAP)
            self._phase = PersistencePhase.UPDATING
            self._audit(
                "HEATMAP_PLAYBACK_GAP_COALESCED",
                target_frame=target.frame_index,
                active_kind=self._active.kind,
            )
            self._emit_phase(hide_layer=False, reason=RebuildReason.PLAYBACK_GAP)
            return
        self._route_rebuild(target, RebuildReason.PLAYBACK_GAP, hide_layer=False)

    def _route_rebuild(
        self, target: PersistenceTarget, reason: RebuildReason, *, hide_layer: bool = True
    ) -> None:
        self._stop_rolling_stream()
        self._generation += 1
        assert self._context is not None
        target = PersistenceTarget(
            source_key=target.source_key,
            frame_index=target.frame_index,
            timestamp=target.timestamp,
            navigation_generation=target.navigation_generation,
            persistence_generation=self._generation,
            reason=target.reason,
        )
        self._desired_target = target
        ticket = self._make_rebuild_ticket(target, reason)
        self._pending = ticket
        self._diag["heatmap_requested_generation"] = self._generation
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()
        else:
            self._start_next_pending()
        self._phase = PersistencePhase.REBUILDING
        self._emit_phase(hide_layer=hide_layer, reason=reason)

    def _make_stream_ticket(self, target: PersistenceTarget) -> _WorkTicket:
        return _WorkTicket(
            kind="stream",
            generation=self._generation,
            source_key=target.source_key,
            reason=RebuildReason.PLAYBACK_GAP,
            target_frame=target.frame_index,
            target_timestamp=target.timestamp,
            navigation_generation=target.navigation_generation,
            target=target,
        )

    def _make_rebuild_ticket(self, target: PersistenceTarget, reason: RebuildReason) -> _WorkTicket:
        assert self._context is not None and self._config is not None
        config = self._config
        timeline: np.ndarray | None = None
        if config.mode is PersistenceMode.EXPONENTIAL_DECAY:
            timeline = self._decay_timeline_cache
        elif config.mode is PersistenceMode.ROLLING_EXACT and config.window_unit is WindowUnit.SECONDS:
            # Exact time-window membership uses the indexed acquisition
            # timeline when row XML does not carry its own timestamp.
            timeline = np.asarray(self._context.index.timestamps, dtype=np.float64)
        positions: list[int] | None = None
        if (
            config.window_unit is WindowUnit.SECONDS
            and config.mode is PersistenceMode.ROLLING_EXACT
            and config.window_seconds is not None
            and target.timestamp is not None
        ):
            timestamps = np.asarray(self._context.index.timestamps, dtype=np.float64)
            cutoff = target.timestamp - config.window_seconds
            selected = np.flatnonzero(
                np.isfinite(timestamps)
                & (timestamps >= cutoff)
                & (timestamps <= target.timestamp)
                & (np.arange(timestamps.size) <= target.frame_index)
            )
            positions = [int(i) for i in selected]
        request = PersistenceWorkRequest(
            source_key=target.source_key,
            config=config,
            generation=self._generation,
            navigation_generation=target.navigation_generation,
            target_frame=target.frame_index,
            target_timestamp=target.timestamp,
            frame_count=self._context.index.frame_count,
            frequencies_hz=self._context.frequencies_hz,
            reason=reason,
            timestamps=timeline,
        )
        if timeline is not None and target.timestamp is not None:
            try:
                total = int(decay_history_positions(config, target.timestamp, timeline).size)
            except ValueError:
                total = 1
        elif positions is not None:
            total = max(1, len(positions))
        else:
            start, end = resolve_persistence_bounds(
                config, target.frame_index, self._context.index.frame_count
            )
            total = end - start + 1
        return _WorkTicket(
            kind="decay" if config.mode is PersistenceMode.EXPONENTIAL_DECAY else "rebuild",
            generation=self._generation,
            source_key=target.source_key,
            reason=reason,
            target_frame=target.frame_index,
            target_timestamp=target.timestamp,
            navigation_generation=target.navigation_generation,
            request=request,
            target=target,
            total=total,
            positions=positions,
        )

    def _start_fixed_request(self, config: HeatmapConfig, current_frame: int) -> None:
        ticket = self._make_fixed_ticket(config, current_frame, bump_generation=True)
        if ticket is None:
            return
        self._diag["heatmap_requested_generation"] = self._generation
        assert ticket.cache_key is not None
        cached = self._cache.get(ticket.cache_key)
        if cached is not None:
            snapshot = self._snapshot_from_fixed(cached, ticket)
            self._applied_snapshot = snapshot
            self._phase = PersistencePhase.CURRENT
            self._diag["heatmap_applied_generation"] = self._generation
            self._audit(
                "HEATMAP_REQUESTED",
                generation=self._generation,
                cache_hit=True,
                fixed=True,
            )
            self.snapshot_ready.emit(snapshot)
            self._emit_phase()
            return
        self._audit(
            "HEATMAP_REQUESTED",
            generation=self._generation,
            cache_hit=False,
            fixed=True,
            reason=ticket.reason.value,
        )
        self._pending = ticket
        if self._active is not None and self._active.worker is not None:
            self._active.worker.cancel()
        else:
            self._start_next_pending()
        self._phase = PersistencePhase.REBUILDING
        self._emit_phase(hide_layer=True, reason=ticket.reason)

    def _make_fixed_request(
        self, config: HeatmapConfig, generation: int, current_frame: int
    ) -> HeatmapRequest:
        assert self._context is not None
        return HeatmapRequest(
            session_id=self._context.session_id,
            waterfall_id=self._context.waterfall_id,
            source_id=self._context.source_id,
            config=config,
            generation=generation,
            frequency_grid_hash=frequency_grid_hash(self._context.frequencies_hz),
        )

    def _fixed_cache_key(
        self, config: HeatmapConfig, request: HeatmapRequest, current_frame: int
    ) -> HeatmapCacheKey | None:
        assert self._context is not None
        try:
            start, end = resolve_frame_range(config, self._context.index.frame_count, current_frame)
        except ValueError:
            return None
        return HeatmapCache.make_key(request, frame_start=start, frame_end=end)

    def _make_fixed_ticket(self, config: HeatmapConfig, current_frame: int, *, bump_generation: bool) -> _WorkTicket | None:
        if self._context is None:
            return None
        if bump_generation:
            self._generation += 1
        generation = self._generation
        request = self._make_fixed_request(config, generation, current_frame)
        cache_key = self._fixed_cache_key(config, request, current_frame)
        if cache_key is None:
            return None
        return _WorkTicket(
            kind="fixed",
            generation=generation,
            source_key=self._context.source_key,
            reason=RebuildReason.CACHE_MISS,
            target_frame=current_frame,
            target_timestamp=self._timestamp_of(current_frame),
            navigation_generation=0,
            heatmap_config=config,
            cache_key=cache_key,
            current_frame=current_frame,
        )

    def _drop_pending(self) -> None:
        self._pending = None

    # --- worker management ------------------------------------------------------
    def _start_next_pending(self) -> None:
        ticket = self._pending
        self._pending = None
        if ticket is not None:
            # Synchronous start: a deferred (singleShot) start would open a
            # race window in which a new span could start a second worker on
            # the same engine state concurrently.
            self._start_ticket(ticket)

    def _start_ticket(self, ticket: _WorkTicket) -> None:
        if self._shutdown:
            return
        if ticket.generation != self._generation or self._context is None:
            return
        if ticket.source_key != self._context.source_key:
            return
        ticket.started_at = time.perf_counter()
        if ticket.kind == "stream":
            assert ticket.target is not None
            if self._start_rolling_stream(ticket.target):
                return
            # A source/state reset happened before this deferred successor.
            self._route_rebuild(ticket.target, RebuildReason.CACHE_MISS)
            return
        if ticket.kind == "fixed":
            assert ticket.heatmap_config is not None
            worker = TaskWorker(
                _run_fixed_job,
                self._context.source_path,
                self._context.info,
                self._context.frequencies_hz,
                ticket.heatmap_config,
                ticket.generation,
                self._context.session_id,
                self._context.waterfall_id,
                self._context.source_id,
                ticket.current_frame,
                self._context.index,
                ticket,
                pass_progress=True,
                pass_cancel=True,
            )
            self._audit(
                "HEATMAP_REBUILD_STARTED",
                fixed=True,
                generation=ticket.generation,
                target_frame=ticket.target_frame,
            )
        elif ticket.kind == "advance":
            assert ticket.target is not None and self._state is not None
            worker = TaskWorker(
                _run_advance_job,
                self._engine,
                self._state,
                ticket.target,
                self._context.source_path,
                self._context.index,
                self._reader_factory,
                self._reads_log,
                pass_cancel=True,
            )
            self._audit(
                "HEATMAP_INCREMENTAL_STARTED",
                generation=ticket.generation,
                target_frame=ticket.target_frame,
            )
        else:
            assert ticket.request is not None
            worker = TaskWorker(
                _run_rebuild_job,
                self._engine,
                ticket.request,
                ticket.kind,
                self._context.source_path,
                self._context.index,
                ticket,
                self._reader_factory,
                self._reads_log,
                pass_progress=True,
                pass_cancel=True,
            )
            self._diag["heatmap_rebuild_count"] += 1
            self._audit(
                "HEATMAP_REBUILD_STARTED",
                generation=ticket.generation,
                target_frame=ticket.target_frame,
                reason=ticket.reason.value,
            )
        ticket.worker = worker
        self._active = ticket
        worker.signals.result.connect(lambda value, ticket=ticket: self._on_result(ticket, value))
        worker.signals.error.connect(
            lambda message, details, ticket=ticket: self._on_error(ticket, message, details)
        )
        worker.signals.finished.connect(lambda worker=worker, ticket=ticket: self._on_finished(ticket, worker))
        if ticket.kind in ("fixed", "rebuild", "decay"):
            worker.signals.progress.connect(
                lambda fraction, text, ticket=ticket: self._on_progress(ticket, fraction)
            )
        self._pool.start(worker)

    def _on_progress(self, ticket: _WorkTicket, fraction: float) -> None:
        if ticket is not self._active:
            return
        total = ticket.total
        processed = int(round(fraction * max(1, total)))
        now = time.perf_counter()
        elapsed = now - ticket.started_at
        if elapsed > 0.0:
            self._diag["heatmap_processing_fps"] = processed / elapsed
        previous = int(self._diag["heatmap_processed_frames"])
        if previous:
            self._diag["heatmap_batch_latency_ms"] = (now - ticket.started_at) * 1000.0 / max(1, processed)
        self._diag["heatmap_processed_frames"] = processed
        self._diag["heatmap_total_frames"] = total
        self._emit_phase(processed=processed, total=total)

    def _on_result(self, ticket: _WorkTicket, value: Any) -> None:
        if self._shutdown:
            return
        if ticket.generation != self._generation or (
            self._context is not None and ticket.source_key != self._context.source_key
        ):
            self._diag["heatmap_stale_results_discarded"] += 1
            self._audit(
                "HEATMAP_LATE_RESULT_DISCARDED",
                generation=ticket.generation,
                current_generation=self._generation,
            )
            return
        snapshot: PersistenceSnapshot | None
        if ticket.kind == "fixed":
            outcome_result = value
            assert isinstance(outcome_result, HeatmapResult)
            assert ticket.cache_key is not None
            self._cache.put(ticket.cache_key, outcome_result)
            snapshot = self._snapshot_from_fixed(outcome_result, ticket)
        elif ticket.kind == "advance":
            snapshot = value
            if snapshot is None:
                # Incremental overlap lost: bounded rebuild of the final window.
                assert ticket.target is not None
                self._route_rebuild(ticket.target, RebuildReason.CACHE_MISS)
                return
            self._diag["heatmap_sequential_updates"] += 1
            self._advance_latencies_ms.append((time.perf_counter() - ticket.started_at) * 1000.0)
        else:
            outcome = value
            assert isinstance(outcome, _RebuildOutcome)
            if outcome.state is not None:
                self._state = outcome.state  # atomic ownership swap
            snapshot = outcome.snapshot
            if snapshot is not None:
                # §10.4 capacity signal: a bounded rebuild is what a sequential
                # advance degrades into once overlap is lost, so its per-frame
                # cost must feed the same latency series the capacity check
                # reads. Without this, processing_to_frame_period_ratio stays 0
                # whenever playback outruns the reader and every advance falls
                # back to CACHE_MISS rebuilds.
                frames = max(1, snapshot.processed_frames)
                elapsed_ms = (time.perf_counter() - ticket.started_at) * 1000.0
                self._advance_latencies_ms.append(elapsed_ms / frames)
        if snapshot is None:
            return
        if ticket.reason is RebuildReason.INITIAL and not self._initial_rebuild_latency_ms:
            self._initial_rebuild_latency_ms = (time.perf_counter() - ticket.started_at) * 1000.0
        self._reads_at_last_apply = len(self._reads_log)
        self._applied_snapshot = snapshot
        self._diag["heatmap_applied_generation"] = snapshot.generation
        self._diag["heatmap_processed_frames"] = snapshot.processed_frames
        self._diag["heatmap_total_frames"] = snapshot.processed_frames
        self._queue_render(snapshot)
        desired = self._desired_target.frame_index if self._desired_target is not None else snapshot.target_frame
        reached = snapshot.target_frame >= desired
        self._phase = PersistencePhase.CURRENT if reached else PersistencePhase.UPDATING
        if ticket.kind == "advance":
            self._audit(
                "HEATMAP_INCREMENTAL_APPLIED",
                generation=snapshot.generation,
                target_frame=snapshot.target_frame,
            )
        else:
            self._audit(
                "HEATMAP_REBUILD_APPLIED",
                generation=snapshot.generation,
                target_frame=snapshot.target_frame,
                cache_hit=False,
                fixed=ticket.kind == "fixed",
            )
        self._check_capacity()
        self._emit_phase(hide_layer=False)

    def _check_capacity(self) -> None:
        """Warn from cached hot-path metrics without rebuilding diagnostics."""
        frame_period = self._frame_period_s()
        ratio = 0.0
        if self._advance_latencies_ms and frame_period is not None and frame_period > 0.0:
            ordered = sorted(self._advance_latencies_ms)
            p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]
            ratio = p95 / (frame_period * 1000.0)
        if ratio > 1.0 and not self._capacity_warning_active:
            self._capacity_warning_active = True
            desired = self._desired_target.frame_index if self._desired_target is not None else -1
            applied = self._applied_snapshot.target_frame if self._applied_snapshot is not None else -1
            self._audit(
                "HEATMAP_CAPACITY_WARNING",
                ratio=ratio,
                lag_frames=max(0, desired - applied) if desired >= 0 and applied >= 0 else 0,
                reason="processing_p95_exceeds_frame_period",
                fallback="bounded_final_window_rebuild",
            )
        elif ratio <= 1.0:
            self._capacity_warning_active = False

    def _on_error(self, ticket: _WorkTicket, message: str, details: str) -> None:
        if ticket.generation != self._generation:
            return
        self._phase = PersistencePhase.ERROR
        self._emit_phase(message=message)
        self.failed.emit(message, details)

    def _on_finished(self, ticket: _WorkTicket, worker: TaskWorker) -> None:
        if self._active is not ticket or ticket.worker is not worker:
            return
        self._active = None
        if worker.cancel_event.is_set():
            self._diag["heatmap_cancel_count"] += 1
            if self._cancel_requested_at > 0.0:
                self._cancellation_latency_ms = (time.perf_counter() - self._cancel_requested_at) * 1000.0
                self._reads_completed_after_cancel = len(self._reads_log) - self._reads_at_cancel
            self._audit(
                "HEATMAP_REBUILD_CANCELLED",
                generation=ticket.generation,
                target_frame=ticket.target_frame,
            )
        if self._pending is not None:
            self._start_next_pending()
        elif self._cancel_requested:
            self._cancel_requested = False
            self._phase = PersistencePhase.CANCELLED
            self._emit_phase()

    # --- render throttling --------------------------------------------------------
    def _queue_render(self, snapshot: PersistenceSnapshot) -> None:
        if self._pending_render is not None:
            self._diag["heatmap_render_dropped"] += 1
        self._pending_render = snapshot
        now = time.perf_counter()
        interval_s = 1.0 / self._render_fps
        elapsed_s = now - self._last_render_emit_at if self._last_render_emit_at else interval_s
        if elapsed_s >= interval_s:
            self._render_timer.stop()
            self._flush_render()
            return
        # Do not restart a full timer interval for a newly arrived snapshot:
        # schedule only the remaining fraction. This removes one extra UI frame
        # of avoidable latency under continuous Rolling Exact playback.
        remaining_ms = max(1, round((interval_s - elapsed_s) * 1000.0))
        if not self._render_timer.isActive() or self._render_timer.remainingTime() > remaining_ms:
            self._render_timer.start(remaining_ms)

    def _render_timeout(self) -> None:
        if self._pending_render is not None:
            self._flush_render()

    def _flush_render(self) -> None:
        snapshot = self._pending_render
        self._pending_render = None
        if snapshot is None or self._shutdown:
            return
        emitted_at = time.perf_counter()
        if not self._first_render_at:
            self._first_render_at = emitted_at
        self._last_render_emit_at = emitted_at
        self._render_submission_started[self._snapshot_token(snapshot)] = emitted_at
        while len(self._render_submission_started) > 64:
            self._render_submission_started.pop(next(iter(self._render_submission_started)))
        self._diag["heatmap_render_emitted"] += 1
        self.snapshot_ready.emit(snapshot)

    # --- phase/snapshot helpers ---------------------------------------------------
    def _emit_phase(
        self,
        *,
        hide_layer: bool = False,
        reason: RebuildReason | None = None,
        processed: int = 0,
        total: int = 0,
        message: str = "",
    ) -> None:
        applied = self._applied_snapshot
        desired_frame = self._desired_target.frame_index if self._desired_target is not None else None
        lag = 0
        if desired_frame is not None and applied is not None:
            lag = max(0, desired_frame - applied.target_frame)
        if hide_layer:
            # §12: hiding the stale layer is an auditable decision, never silent.
            self._audit(
                "HEATMAP_STALE_HIDDEN",
                source_key=(
                    f"{self._context.session_id}/{self._context.waterfall_id}"
                    if self._context is not None
                    else None
                ),
                generation=self._generation,
                target_frame=desired_frame,
                reason=reason.value if reason is not None else None,
            )
        rendered = self._render_submitted_snapshot
        render_lag = 0
        if desired_frame is not None and rendered is not None:
            render_lag = max(0, desired_frame - rendered.target_frame)
        event = HeatmapPhaseEvent(
            phase=self._phase,
            target_frame=desired_frame,
            applied_frame=applied.target_frame if applied is not None else None,
            rendered_frame=rendered.target_frame if rendered is not None else None,
            render_lag_frames=render_lag,
            frame_start=applied.frame_start if applied is not None else None,
            frame_end=applied.frame_end if applied is not None else None,
            lag_frames=lag,
            hide_layer=hide_layer,
            reason=reason,
            processed_frames=processed,
            total_frames=total,
            message=message,
        )
        self.phase_changed.emit(event)

    def _snapshot_from_fixed(self, result: HeatmapResult, ticket: _WorkTicket) -> PersistenceSnapshot:
        config = result.config
        if config.range_mode is HeatmapRangeMode.FULL:
            mode = PersistenceMode.FULL_RECORDING
        elif config.range_mode is HeatmapRangeMode.SELECTED:
            mode = PersistenceMode.SELECTED_RANGE
        elif config.range_mode is HeatmapRangeMode.EXPONENTIAL_DECAY:
            mode = PersistenceMode.EXPONENTIAL_DECAY
        else:
            mode = PersistenceMode.ROLLING_EXACT
        persistence_config = PersistenceConfig(
            mode=mode,
            window_frames=config.window_frames,
            power_min_dbm=config.power_min_dbm,
            power_max_dbm=config.power_max_dbm,
            power_bins=config.power_bins,
            sampling_policy=config.sampling_policy,
        )
        weights = result.normalization_weights_by_frequency
        frame_start = config.frame_start if config.frame_start is not None else 0
        frame_end = (
            config.frame_end
            if config.frame_end is not None
            else max(0, ticket.current_frame)
        )
        if mode is PersistenceMode.FULL_RECORDING and self._context is not None:
            frame_end = self._context.index.frame_count - 1
        return PersistenceSnapshot(
            source_key=ticket.source_key,
            config=persistence_config,
            density=result.density.copy(),
            normalization_weights_by_frequency=weights.copy() if weights is not None else np.zeros(0),
            frequencies_hz=result.frequencies_hz.copy(),
            power_axis_dbm=result.power_axis_dbm.copy(),
            phase=self._phase,
            generation=ticket.generation,
            navigation_generation=ticket.navigation_generation,
            target_frame=ticket.current_frame,
            applied_frame=frame_end,
            frame_start=frame_start,
            frame_end=frame_end,
            timestamp_start=None,
            timestamp_end=None,
            history_start_frame=None,
            history_end_frame=None,
            half_life_seconds=None,
            decay_cutoff_epsilon=None,
            processed_frames=result.processed_frames,
            exact=result.exact,
            approximate=result.approximate,
            stale=False,
            computed_at=result.computed_at or "",
        )


