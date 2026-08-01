"""Qt-widget-independent presenter for the Wideband Sweep workspace.

``SweepPresenter`` owns one background worker thread for sequential sweep
execution and stitching. The GUI thread only calls :meth:`plan`, :meth:`run`,
:meth:`cancel`, :meth:`poll`, and :meth:`close`. A lock protects the latest
immutable state, while :meth:`poll` is a cheap cached-snapshot read when idle.

The worker never calls Python per-frame callbacks. ``SweepExecutor`` reports
only per-segment progress, and each report replaces the immutable snapshot
under the lock. ``generation`` changes for plan, run, cancellation, and final
state transitions; progress publications keep it stable because workspace
render keys include the run fields themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import math
import threading
import time
from typing import Any

import numpy as np

from ..sdr.contracts import (
    DeviceConfig,
    DspConfig,
    QualityFlag,
    SpectrumUnit,
    SweepConfig,
    SweepSpectrumFrame,
)
from ..sdr.fixed_band import FixedBandEngineService, FixedBandOptions
from ..sdr.stitching import SweepStitchError, SweepStitchOptions, stitch_sweep
from ..sdr.sweep import (
    SweepExecutionStatus,
    SweepExecutor,
    SweepPlan,
    SweepPlannerOptions,
    SweepPlanningError,
    SweepProgress,
    plan_sweep,
)
from .sweep_state import (
    SweepPlanSegmentSnapshot,
    SweepPlanSnapshot,
    SweepQualitySnapshot,
    SweepResultSnapshot,
    SweepRunSnapshot,
    SweepRunStatus,
    SweepSeamSnapshot,
    SweepWorkspaceSnapshot,
)


SweepServiceFactory = Callable[[str], Any]


class SweepPresenter:
    """Own plan preview, execution, stitching, and immutable snapshots."""

    def __init__(
        self,
        *,
        service_factory: SweepServiceFactory = FixedBandEngineService,
        planner_options: SweepPlannerOptions | None = None,
        stitch_options: SweepStitchOptions | None = None,
        poll_batch_size: int = 8,
        idle_timeout_s: float = 2.0,
        sweep_id: int | None = None,
    ) -> None:
        if poll_batch_size <= 0:
            raise ValueError("poll_batch_size must be positive")
        if not math.isfinite(idle_timeout_s) or idle_timeout_s <= 0.0:
            raise ValueError("idle_timeout_s must be finite and positive")
        self._service_factory = service_factory
        self._planner_options = planner_options or SweepPlannerOptions()
        self._stitch_options = stitch_options or SweepStitchOptions()
        self._poll_batch_size = int(poll_batch_size)
        self._idle_timeout_s = float(idle_timeout_s)
        self._sweep_id = sweep_id

        self._lock = threading.Lock()
        self._generation = 0
        self._plan: SweepPlan | None = None
        self._plan_snapshot: SweepPlanSnapshot | None = None
        self._run = SweepRunSnapshot()
        self._result = SweepResultSnapshot()
        self._snapshot = SweepWorkspaceSnapshot()
        self._thread: threading.Thread | None = None
        self._cancel_event: threading.Event | None = None
        self._last_frame: SweepSpectrumFrame | None = None
        self._base_options: FixedBandOptions | None = None
        self._service: Any | None = None
        self._last_error: str | None = None

    @property
    def snapshot(self) -> SweepWorkspaceSnapshot:
        """The most recent immutable workspace snapshot."""

        return self._snapshot

    @property
    def plan_snapshot(self) -> SweepPlanSnapshot | None:
        """The latest device-independent plan preview, if available."""

        return self._plan_snapshot

    @property
    def last_frame(self) -> SweepSpectrumFrame | None:
        """The full stitched frame retained outside the presentation snapshot."""

        with self._lock:
            return self._last_frame

    @property
    def running(self) -> bool:
        """Whether a sweep worker is active or publication is in-flight."""

        with self._lock:
            thread = self._thread
            return bool(
                (thread is not None and thread.is_alive())
                or self._run.status
                in (SweepRunStatus.RUNNING, SweepRunStatus.CANCELLING, SweepRunStatus.PLANNING)
            )

    @property
    def last_error(self) -> str | None:
        """Most recent planning, execution, or stitching error."""

        return self._last_error

    def plan(
        self,
        config: SweepConfig,
        planner_options: SweepPlannerOptions | None = None,
    ) -> list[str]:
        """Build a plan preview without touching a device."""

        if not isinstance(config, SweepConfig):
            raise TypeError("config must be SweepConfig")
        options = planner_options or self._planner_options
        with self._lock:
            if (
                (self._thread is not None and self._thread.is_alive())
                or self._run.status in (SweepRunStatus.PLANNING, SweepRunStatus.RUNNING, SweepRunStatus.CANCELLING)
            ):
                return ["sweep is already running"]
        try:
            plan = plan_sweep(config, options)
        except (SweepPlanningError, TypeError, ValueError) as exc:
            error = str(exc)
            with self._lock:
                self._plan = None
                self._plan_snapshot = SweepPlanSnapshot(error=error)
                self._last_error = error
                self._run = replace(self._run, error=error)
                self._generation += 1
                self._rebuild_snapshot_locked()
            return [error]

        snapshot = self._build_plan_snapshot(plan)
        with self._lock:
            self._plan = plan
            self._plan_snapshot = snapshot
            self._last_error = None
            self._run = replace(
                self._run,
                status=SweepRunStatus.PLANNED,
                current_segment_index=None,
                completed_segments=0,
                total_segments=len(plan.segments),
                stage=None,
                elapsed_s=0.0,
                eta_s=None,
                error=None,
            )
            self._generation += 1
            self._rebuild_snapshot_locked()
        return []

    def run(self, uri: str) -> list[str]:
        """Start one background execution of the current plan."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return ["sweep is already running"]
            if self._plan is None:
                return ["no plan is built"]
            cancel = threading.Event()
            self._cancel_event = cancel
            self._run = SweepRunSnapshot(
                status=SweepRunStatus.RUNNING,
                total_segments=len(self._plan.segments),
            )
            self._last_error = None
            self._generation += 1
            self._rebuild_snapshot_locked()
            thread = threading.Thread(
                target=self._execute,
                args=(uri,),
                name="sweep-run",
                daemon=True,
            )
            self._thread = thread
        thread.start()
        return []

    def cancel(self) -> None:
        """Request cancellation without blocking the GUI thread."""

        with self._lock:
            cancel = self._cancel_event
            if cancel is None:
                return
            cancel.set()
            self._run = replace(
                self._run,
                status=SweepRunStatus.CANCELLING,
                error=None,
            )
            self._generation += 1
            self._rebuild_snapshot_locked()

    def poll(self) -> SweepWorkspaceSnapshot:
        """Return the cached snapshot without draining or rebuilding state."""

        return self._snapshot

    def close(self) -> None:
        """Cancel the active worker and release presenter-owned references."""

        self.cancel()
        with self._lock:
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._last_error = "shutdown timed out; sweep worker is still draining"
                self._run = replace(self._run, status=SweepRunStatus.FAILED, error=self._last_error)
                self._generation += 1
                self._rebuild_snapshot_locked()
                return
            self._thread = None
            self._cancel_event = None
            self._plan = None
            self._plan_snapshot = None
            self._base_options = None
            self._service = None

    def _execute(self, uri: str) -> None:
        """Run execution and stitching on the presenter-owned worker."""

        started = time.monotonic()
        with self._lock:
            plan = self._plan
            cancel = self._cancel_event
        if plan is None or cancel is None:
            return

        service: Any | None = None
        result_errors: tuple[str, ...] = ()
        final_status = SweepRunStatus.FAILED
        final_error: str | None = None
        result_snapshot = SweepResultSnapshot()
        frame: SweepSpectrumFrame | None = None
        try:
            service = self._service_factory(uri)
            base_options = self._build_base_options(uri, plan.config)
            with self._lock:
                self._service = service
                self._base_options = base_options

            def report(progress: SweepProgress) -> None:
                elapsed = time.monotonic() - started
                eta: float | None = None
                if progress.fraction > 0.0:
                    candidate = elapsed / progress.fraction - elapsed
                    if math.isfinite(candidate) and candidate >= 0.0:
                        eta = candidate
                with self._lock:
                    self._run = SweepRunSnapshot(
                        status=SweepRunStatus.RUNNING,
                        current_segment_index=progress.segment_index,
                        completed_segments=progress.completed_segments,
                        total_segments=progress.total_segments,
                        stage=progress.stage,
                        elapsed_s=elapsed,
                        eta_s=eta,
                    )
                    self._rebuild_snapshot_locked()

            executor = SweepExecutor(
                service,
                base_options,
                poll_batch_size=self._poll_batch_size,
                idle_timeout_s=self._idle_timeout_s,
                sweep_id=self._sweep_id,
            )
            execution = executor.execute(plan, cancel=cancel, progress=report)
            result_errors = execution.errors
            final_status = {
                SweepExecutionStatus.COMPLETED: SweepRunStatus.COMPLETED,
                SweepExecutionStatus.CANCELLED: SweepRunStatus.CANCELLED,
                SweepExecutionStatus.FAILED: SweepRunStatus.FAILED,
            }[execution.status]
            if execution.status is SweepExecutionStatus.FAILED:
                final_error = execution.errors[0] if execution.errors else "sweep execution failed"

            try:
                frame = stitch_sweep(execution, self._stitch_options)
            except SweepStitchError as exc:
                final_status = SweepRunStatus.FAILED
                final_error = f"{type(exc).__name__}: {exc}"
            except Exception as exc:
                final_status = SweepRunStatus.FAILED
                final_error = f"{type(exc).__name__}: {exc}"
            else:
                result_snapshot = self._build_result_snapshot(frame, execution.errors)
        except Exception as exc:
            final_status = SweepRunStatus.FAILED
            final_error = f"{type(exc).__name__}: {exc}"
            result_errors = (final_error,)
            result_snapshot = SweepResultSnapshot(errors=result_errors)
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                if frame is not None:
                    self._last_frame = frame
                self._result = result_snapshot
                self._last_error = final_error
                self._run = replace(
                    self._run,
                    status=final_status,
                    error=final_error,
                    elapsed_s=elapsed,
                    eta_s=None,
                    stage="finished" if final_status is SweepRunStatus.COMPLETED else None,
                )
                self._generation += 1
                self._rebuild_snapshot_locked()
                self._thread = None
                self._cancel_event = None
            if service is not None:
                disconnect = getattr(service, "disconnect", None)
                if callable(disconnect):
                    disconnect()
            with self._lock:
                self._service = None

    @staticmethod
    def _build_base_options(uri: str, config: SweepConfig) -> FixedBandOptions:
        device = DeviceConfig(
            source_id="sweep-ui",
            context_uri=uri,
            center_frequency_hz=(config.start_frequency_hz + config.stop_frequency_hz) / 2.0,
            sample_rate_hz=config.sample_rate_hz,
            analog_bandwidth_hz=config.analog_bandwidth_hz,
            buffer_samples=262_144,
        )
        dsp = DspConfig(
            fft_size=config.fft_size,
            hop_size=config.hop_size,
            unit=SpectrumUnit.DBFS_BIN,
        )
        return FixedBandOptions(device=device, dsp=dsp, allow_runtime_fallback=True)

    @staticmethod
    def _build_plan_snapshot(plan: SweepPlan) -> SweepPlanSnapshot:
        return SweepPlanSnapshot(
            requested_start_hz=plan.requested_start_hz,
            requested_stop_hz=plan.requested_stop_hz,
            usable_bandwidth_hz=plan.usable_bandwidth_hz,
            expected_duration_s=plan.expected_duration_s,
            segment_count=len(plan.segments),
            segments=tuple(
                SweepPlanSegmentSnapshot(
                    segment_index=segment.segment_index,
                    center_frequency_hz=segment.center_frequency_hz,
                    requested_start_hz=segment.requested_start_hz,
                    requested_stop_hz=segment.requested_stop_hz,
                    overlap_hz=segment.overlap_hz,
                    expected_total_duration_s=segment.expected_total_duration_s,
                )
                for segment in plan.segments
            ),
            coverage_gaps_hz=plan.coverage_gaps_hz,
        )

    @staticmethod
    def _build_result_snapshot(
        frame: SweepSpectrumFrame,
        errors: tuple[str, ...],
    ) -> SweepResultSnapshot:
        flags = np.asarray(frame.quality_flags_per_bin, dtype=np.uint16)
        quality = SweepQualitySnapshot(
            missing_bins=int(np.count_nonzero(flags & np.uint16(QualityFlag.MISSING_SEGMENT))),
            overlap_bins=int(np.count_nonzero(flags & np.uint16(QualityFlag.STITCH_OVERLAP))),
            seams=tuple(
                SweepSeamSnapshot(
                    left_segment_index=seam.left_segment_index,
                    right_segment_index=seam.right_segment_index,
                    correction_db=seam.correction_db,
                    before_p95_db=seam.before_p95_db,
                    after_p95_db=seam.after_p95_db,
                    sample_count=seam.sample_count,
                )
                for seam in frame.seam_metrics
            ),
            unit=str(frame.unit.value),
            calibration_status=str(frame.calibration_status.value),
            calibration_profile_id=frame.calibration_profile_id,
        )
        return SweepResultSnapshot(
            present=True,
            sweep_id=frame.sweep_id,
            bin_count=int(frame.frequencies_hz.size),
            requested_start_hz=frame.requested_start_hz,
            requested_stop_hz=frame.requested_stop_hz,
            nominal_rbw_hz=frame.nominal_rbw_hz,
            quality=quality,
            errors=errors,
        )

    def _rebuild_snapshot_locked(self) -> None:
        self._snapshot = SweepWorkspaceSnapshot(
            generation=self._generation,
            run=self._run,
            plan=self._plan_snapshot,
            result=self._result,
            error=self._last_error,
            stale=False,
        )


__all__ = ["SweepPresenter", "SweepServiceFactory"]
