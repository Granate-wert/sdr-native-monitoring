"""Snapshot-only bridge for the native R10-D continuous sweep coordinator.

The coordinator owns retuning, acquisition and line assembly in C++.  This
module is deliberately called by a low-rate UI timer: it drains only bounded
terminal lines and progressive snapshots, never raw I/Q or individual FFT frames.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Protocol

import numpy as np

from ..domain import SweepLineFrame, SweepLineGapReason, SweepLineState
from ..domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from ..domain.sweep_progress import SweepProgressFrame
from ..domain.sweep_acquisition import SweepSegmentAcquisition


class ContinuousSweepDisplayPort(Protocol):
    """Minimal lifecycle surface consumed by the Qt continuous-sweep presenter.

    A direct native display service owns one already-created coordinator.  The
    Live composition service defers that ownership (and its exclusive receiver
    lease) until an explicit Start action.  The presenter needs neither
    implementation detail: it only starts a pre-built request/configuration,
    polls bounded snapshots, and performs orderly shutdown.

    Keeping this as a structural port prevents the UI composition root from
    widening a deferred Live service into a concrete coordinator service.
    """

    def start(self, native_config: Any) -> None:
        """Start from an already validated composition-owned value."""

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        """Return the newest bounded UI-safe snapshot."""

    def stop(self) -> None:
        """Stop a running operation without closing the service."""

    def close(self) -> None:
        """Release all service-owned resources."""


class NativeContinuousSweepDisplayService:
    """Own a native coordinator and expose a bounded polling surface to UI.

    It intentionally accepts the already validated native coordinator config:
    planning and hardware capability admission remain native/service concerns,
    rather than widget-owned mutable state.
    """

    def __init__(self, native_module: Any, context_uri: str, *, timeout_ms: int = 3000) -> None:
        if not context_uri.strip() or timeout_ms <= 0:
            raise ValueError("continuous sweep context URI and timeout must be positive")
        coordinator_type = getattr(native_module, "NativeContinuousSweepCoordinator", None)
        if coordinator_type is None:
            raise RuntimeError("native continuous sweep coordinator is unavailable")
        self._coordinator = coordinator_type(context_uri, timeout_ms)
        self._lock = threading.RLock()
        self._closed = False
        self._started = False
        self._has_started = False
        self._last_completed = 0
        self._last_metrics_at_s: float | None = None
        self._ui_superseded = 0
        self._terminal_watermark: tuple[str, int, int] | None = None
        self._progress_watermark: tuple[str, int, int, int] | None = None
        self._active_identity: tuple[str, int] | None = None

    def start(self, native_config: Any) -> None:
        with self._lock:
            self._require_open()
            if self._started:
                raise RuntimeError("continuous sweep display service is already running")
            epoch = native_config.epoch
            sources = tuple(segment.fixed_band.device.source_id for segment in native_config.segments)
            if (type(epoch) is not int or epoch < 0 or not sources
                    or any(not isinstance(source, str) or not source.strip() for source in sources)
                    or len(set(sources)) != 1):
                raise ValueError("continuous sweep requires one explicit source and epoch")
            self._coordinator.configure(native_config)
            self._coordinator.start()
            self._active_identity = (sources[0], epoch)
            self._started = True
            self._has_started = True
            self._last_completed = 0
            self._last_metrics_at_s = None
            self._ui_superseded = 0
            self._terminal_watermark = None
            self._progress_watermark = None

    def poll_latest(self, *, now_s: float | None = None) -> ContinuousSweepDisplaySnapshot:
        """Drain terminal output even after Stop; preview is running-only."""

        now = time.monotonic() if now_s is None else float(now_s)
        if not math.isfinite(now):
            raise ValueError("continuous sweep metric timestamp must be finite")
        with self._lock:
            self._require_open()
            lines = tuple(self._coordinator.poll_lines()) if self._has_started else ()
            if any((item.source_id, item.epoch) != self._active_identity for item in lines):
                raise RuntimeError("continuous sweep terminal source/epoch differs from active request")
            if len(lines) > 1:
                self._ui_superseded += len(lines) - 1
            line = _to_domain_line(lines[-1]) if lines else None
            if line is not None:
                terminal = (line.source_id, line.epoch, line.sequence)
                previous = self._terminal_watermark
                if previous is None or previous[:2] != terminal[:2] or terminal[2] > previous[2]:
                    self._terminal_watermark = terminal
            poll_progress = getattr(self._coordinator, "poll_progress", None)
            native_progress = poll_progress() if self._started and callable(poll_progress) else None
            if (native_progress is not None
                    and (native_progress.source_id, native_progress.epoch) != self._active_identity):
                raise RuntimeError("continuous sweep progress source/epoch differs from active request")
            progress = _to_domain_progress(native_progress) if native_progress is not None else None
            if progress is not None:
                identity = (progress.source_id, progress.epoch, progress.sequence, progress.revision)
                terminal_watermark = self._terminal_watermark
                progress_watermark = self._progress_watermark
                if ((terminal_watermark is not None and terminal_watermark[:2] == identity[:2]
                     and terminal_watermark[2] >= identity[2])
                        or (progress_watermark is not None and progress_watermark[:2] == identity[:2]
                            and progress_watermark[2:] >= identity[2:])):
                    progress = None
                else:
                    self._progress_watermark = identity
            metrics = self._metrics(now)
            return ContinuousSweepDisplaySnapshot(line=line, metrics=metrics, progress=progress)

    def stop(self) -> None:
        with self._lock:
            if self._closed or not self._started:
                return
            self._coordinator.stop()
            self._started = False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                if self._started:
                    self._coordinator.stop()
            finally:
                self._started = False
                self._closed = True
                self._coordinator.disconnect()

    def _metrics(self, now_s: float) -> ContinuousSweepDisplayMetrics:
        native = self._coordinator.metrics()
        completed = int(getattr(native, "completed_lines", 0) or 0)
        rate = 0.0
        if self._last_metrics_at_s is not None:
            elapsed = now_s - self._last_metrics_at_s
            if elapsed > 0.0:
                rate = max(0.0, (completed - self._last_completed) / elapsed)
        self._last_completed = completed
        self._last_metrics_at_s = now_s
        queue = getattr(native, "output_queue", None)
        return ContinuousSweepDisplayMetrics(
            completed_line_lps=rate,
            completed_lines=completed,
            gapped_lines=int(getattr(native, "gapped_lines", 0) or 0),
            native_line_relay_superseded=int(
                getattr(native, "line_relay_snapshots_superseded", 0) or 0
            ),
            native_line_relay_queue_capacity=int(
                getattr(native, "line_relay_queue_capacity", 0) or 0
            ),
            native_line_relay_queue_high_water=int(
                getattr(native, "line_relay_queue_high_water", 0) or 0
            ),
            terminal_control_gaps=int(getattr(native, "terminal_control_gaps", 0) or 0),
            native_output_superseded=int(getattr(native, "output_snapshots_superseded", 0) or 0),
            ui_snapshots_superseded=self._ui_superseded,
            native_queue_depth=int(getattr(queue, "depth", 0) or 0),
            native_queue_capacity=int(getattr(queue, "capacity", 0) or 0),
            has_error=bool(getattr(native, "has_error", False)),
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("continuous sweep display service is closed")


def _to_domain_acquisition(native: Any) -> tuple[SweepSegmentAcquisition, ...] | None:
    records = getattr(native, "segment_acquisition", None)
    if records is None:
        return None
    return tuple(SweepSegmentAcquisition(
        segment_index=item.segment_index, config_generation=item.config_generation,
        frame_sequence=item.frame_sequence, first_sample_index=item.first_sample_index,
        timestamp_ns=item.timestamp_ns, sample_rate_hz=item.sample_rate_hz,
        fft_size=item.fft_size, quality_flags=item.quality_flags,
    ) for item in records)


def _to_domain_progress(native: Any) -> SweepProgressFrame:
    return SweepProgressFrame(
        source_id=native.source_id, sequence=native.line_sequence,
        epoch=native.epoch, revision=native.revision, unit=native.unit,
        frequencies_hz=native.frequencies_hz, values_db=native.values,
        quality_flags=native.quality_flags_per_bin,
        source_segment_indices=native.source_segment_indices,
        acquired_segment_generations=tuple(native.acquired_segment_generations),
        pending_segment_indices=tuple(native.pending_segment_indices),
        segment_acquisition=_to_domain_acquisition(native),
    )


def _to_domain_line(native: Any) -> SweepLineFrame:
    from ..domain.sweep_lines import SweepQualitySchema

    try:
        state = SweepLineState(str(native.state))
        reasons = tuple(SweepLineGapReason(str(value)) for value in native.gap_reasons)
        quality = np.asarray(native.quality_flags_per_bin)
        if quality.size and int(np.max(quality)) > np.iinfo(np.uint16).max:
            raise ValueError("native continuous sweep quality flag exceeds domain width")
        return SweepLineFrame(
            sequence=int(native.line_sequence),
            epoch=int(native.epoch),
            completed_at_ns=int(native.completed_ns),
            source_id=str(native.source_id),
            state=state,
            frequencies_hz=native.frequencies_hz,
            values_db=native.values,
            quality_flags=quality,
            quality_schema=SweepQualitySchema.NATIVE_V5,
            segment_acquisition=_to_domain_acquisition(native),
            source_segment_indices=native.source_segment_indices,
            missing_segment_indices=tuple(int(value) for value in native.missing_segment_indices),
            segment_config_generations=tuple(
                (int(index), int(generation))
                for index, generation in native.segment_config_generations
            ),
            gap_reasons=reasons,
            unit=str(native.unit),
            analysis_window_hz=float(getattr(native, "analysis_window_hz", 0.0)),
            analysis_bins_per_usable_window=int(
                getattr(native, "analysis_bins_per_usable_window", 0)
            ),
            physical_fft_bin_width_hz=float(
                getattr(native, "physical_fft_bin_width_hz", 0.0)
            ),
            physical_fft_size=int(getattr(native, "physical_fft_size", 0)),
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(f"native continuous sweep line conversion failed: {error}") from error


__all__ = [
    "ContinuousSweepDisplayPort",
    "ContinuousSweepDisplayMetrics",
    "ContinuousSweepDisplaySnapshot",
    "NativeContinuousSweepDisplayService",
]
