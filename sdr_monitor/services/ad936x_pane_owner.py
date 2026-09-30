"""One selected AD936x RX owner for common RTBW or continuous Sweep panes.

The existing application, Sweep router and native lease retain acquisition
ownership. This adapter adds no IIO opener, DSP loop or alternative renderer.
"""

from __future__ import annotations

from dataclasses import replace
from math import isclose

import numpy as np

from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundle_from_sweep
from sdr_monitor.domain.analyzer_resources import AnalyzerGeometryPreflight
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import Ad936xSweepPaneProfile, CaptureJob, CaptureMeasurementMode
from sdr_monitor.domain.sweep_lines import SweepLineFrame, SweepQualitySchema
from sdr_monitor.domain.sweep_progress import SweepProgressFrame

from .ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from .pane_resource_session import PaneCaptureAdmission


class Ad936xPaneOwner(Ad936xRtbwPaneOwner):
    """Keep exact selection, monotonic Sweep epoch and bounded publications."""

    def __init__(self, live: LiveSessionApplicationService, sweep: AnalyzerSweepRouter, *,
                 physical_stream_resource_id: str, source_id: str,
                 receiver_endpoint_id: str) -> None:
        super().__init__(live, physical_stream_resource_id=physical_stream_resource_id,
                         source_id=source_id, receiver_endpoint_id=receiver_endpoint_id)
        if not isinstance(sweep, AnalyzerSweepRouter):
            raise TypeError("AD936x pane requires its common Sweep router")
        self._sweep_router = sweep
        self._active_mode: CaptureMeasurementMode | None = None
        self._active_sweep_profile: Ad936xSweepPaneProfile | None = None
        self._geometry: AnalyzerGeometryPreflight | None = None
        self._last_sweep_epoch = -1
        self._last_progress: tuple[int, int, int] | None = None
        self._last_line: tuple[int, int] | None = None
        self._validated_grid: np.ndarray | None = None

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if not isinstance(profile, Ad936xSweepPaneProfile):
            super().validate_job(job)
            return
        selection = self._live.current_source_selection()
        if (job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.source.device_id != self._source_id
                or selection is None or selection.selected is not profile.source
                or selection.revision != profile.selection_revision or selection.release_pending
                or job.start_hz != profile.pane_crop_start_hz
                or job.stop_hz != profile.pane_crop_stop_hz):
            raise ValueError("AD936x pane Sweep requires its exact selected source and plan")
        # Existing pure backend admission, BEFORE a lease, SDK open or RX.
        self._live.preflight_sweep(profile.configuration, profile.request_template)

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        profile = job.profile
        if not isinstance(profile, Ad936xSweepPaneProfile):
            self._active_mode = CaptureMeasurementMode.RTBW
            return super().start_capture(job)
        before = self._live.current_snapshot()
        state = self._live.analyzer_state
        if (state is None or state.phase is not AnalyzerPhase.IDLE
                or self._live.is_running() or before.state is not LiveSessionState.CONNECTED
                or before.stop_required or before.device is None
                or before.device.device_id != self._source_id or before.applied is None
                or self._last_sweep_epoch >= (1 << 64) - 1):
            raise RuntimeError("Selected AD936x Analyzer is not idle with the expected staged source")
        self._geometry = self._live.preflight_sweep(profile.configuration, profile.request_template)
        # Retain Stop duty even when Stage/Start has only partially succeeded.
        self._active_mode = CaptureMeasurementMode.SWEEP
        self._active_sweep_profile = profile
        staged = self._live.apply_configuration(profile.configuration)
        if (staged.error is not None or staged.applied is None
                or staged.applied.applied != profile.configuration):
            raise RuntimeError("AD936x pane Sweep profile was not staged exactly")
        started = self._live.start_sweep(replace(profile.request_template,
                                               epoch=self._last_sweep_epoch + 1))
        selection = self._live.current_source_selection()
        if (started.mode is not AnalyzerMode.SWEEP or started.phase is not AnalyzerPhase.RUNNING
                or type(started.sweep_epoch) is not int or started.sweep_epoch <= self._last_sweep_epoch
                or selection is None or selection.selected is not profile.source
                or selection.revision != profile.selection_revision or selection.release_pending):
            raise RuntimeError("AD936x pane Sweep Start lacks its exact source and common Analyzer epoch")
        self._last_sweep_epoch = started.sweep_epoch
        self._last_progress = None
        self._last_line = None
        self._validated_grid = None
        return PaneCaptureAdmission(
            job.capture_id, self._source_id, CaptureMeasurementMode.SWEEP,
            profile.unit, (self._endpoint_id,), started.sweep_epoch,
            sample_rate_hz=profile.sample_rate_hz, fft_size=profile.fft_size, hop_size=None,
        )

    def stop_capture_and_wait(self) -> None:
        # The same common Stop releases the native Sweep lease or RTBW owner.
        # Do not clear retained state on a failed release.
        super().stop_capture_and_wait()
        self._active_mode = None
        self._active_sweep_profile = None
        self._geometry = None
        self._last_progress = None
        self._last_line = None
        self._validated_grid = None

    def _validate_publication(self, frame: SweepLineFrame | SweepProgressFrame) -> None:
        profile, geometry = self._active_sweep_profile, self._geometry
        if (profile is None or geometry is None
                or frame.source_id != f"continuous-sweep:native-sweep:{self._source_id}"
                or frame.epoch != self._last_sweep_epoch or frame.unit != profile.unit):
            raise RuntimeError("AD936x pane Sweep publication source, epoch or unit changed")
        indices = (tuple(index for index, _generation in frame.segment_config_generations)
                   if isinstance(frame, SweepLineFrame) else
                   tuple(sorted(tuple(index for index, _generation in frame.acquired_segment_generations)
                                + frame.pending_segment_indices)))
        if indices != tuple(range(geometry.segment_count)):
            raise RuntimeError("AD936x pane Sweep segment geometry changed")
        if frame.statistics is not None and profile.request_template.statistics is None:
            raise RuntimeError("AD936x pane Sweep has unrequested statistics")
        if frame.segment_acquisition is not None and any(
                record.sample_rate_hz != geometry.sample_rate_hz
                or record.fft_size != geometry.physical_fft_size
                for record in frame.segment_acquisition):
            raise RuntimeError("AD936x pane Sweep segment acquisition profile changed")
        position = frame.last_admitted_segment
        if position is not None:
            start = profile.request_template.start_hz + position.segment_index * geometry.segment_stride_hz
            stop = min(profile.request_template.stop_hz, start + geometry.usable_window_hz)
            if position.usable_start_hz != start or position.usable_stop_hz != stop:
                raise RuntimeError("AD936x pane Sweep last admitted segment geometry changed")
        if isinstance(frame, SweepLineFrame) and (
                frame.quality_schema is not SweepQualitySchema.NATIVE_V5
                or frame.physical_fft_size != geometry.physical_fft_size
                or frame.analysis_window_hz != geometry.usable_window_hz
                or frame.analysis_bins_per_usable_window != geometry.analysis_bins_per_usable_window
                or not isclose(frame.physical_fft_bin_width_hz, geometry.physical_bin_spacing_hz,
                               rel_tol=1e-10, abs_tol=1e-7)):
            raise RuntimeError("AD936x pane Sweep terminal physical FFT contract changed")
        grid = frame.frequencies_hz
        if len(grid) != geometry.reduced.output_bins:
            raise RuntimeError("AD936x pane Sweep output grid size changed")
        if grid is self._validated_grid:
            return
        # Bounded scratch, no per-FFT Python work. Immutable native axis may be
        # shared by progressive/final snapshots; cache at most one exact view.
        spacing = geometry.output_spacing_hz
        tolerance = max(spacing * 1e-10, abs(float(np.spacing(grid[-1]))) * 8, 1e-7)
        for begin in range(0, len(grid), 65_536):
            end = min(len(grid), begin + 65_536)
            expected = profile.request_template.start_hz + np.arange(begin, end) * spacing
            if not np.allclose(grid[begin:end], expected, rtol=0, atol=tolerance):
                raise RuntimeError("AD936x pane Sweep output frequency grid changed")
        self._validated_grid = grid

    def _bundle(self, frame: SweepLineFrame | SweepProgressFrame) -> AnalyzerFrameBundle:
        self._validate_publication(frame)
        # Native Sweep has an established strategy-prefixed source ID. Map
        # ONLY that exact selected-source prefix at this owner boundary, also
        # preserving the statistics parent binding. No epoch/quality rewrite.
        statistics = (None if frame.statistics is None else
                      replace(frame.statistics, source_id=self._source_id))
        return bundle_from_sweep(replace(frame, source_id=self._source_id, statistics=statistics))

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        if self._active_mode is not CaptureMeasurementMode.SWEEP:
            return super().poll_bundles()
        snapshot = self._sweep_router.poll_latest()
        if snapshot.metrics.has_error:
            raise RuntimeError("AD936x pane Sweep worker failed; explicit Stop required")
        result: list[tuple[str, AnalyzerFrameBundle]] = []
        line, progress = snapshot.line, snapshot.progress
        if line is not None:
            if not isinstance(line, SweepLineFrame):
                raise RuntimeError("AD936x pane Sweep terminal contract changed")
            self._validate_publication(line)
            key = (line.epoch, line.sequence)
            if self._last_line is None or key > self._last_line:
                result.append((self._endpoint_id, self._bundle(line)))
                self._last_line = key
        if progress is not None:
            if not isinstance(progress, SweepProgressFrame):
                raise RuntimeError("AD936x pane Sweep progress contract changed")
            self._validate_publication(progress)
            progress_key = (progress.epoch, progress.sequence, progress.revision)
            if ((self._last_progress is None or progress_key > self._last_progress)
                    and (self._last_line is None or progress_key[:2] > self._last_line)):
                result.append((self._endpoint_id, self._bundle(progress)))
                self._last_progress = progress_key
        return tuple(result)


__all__ = ["Ad936xPaneOwner"]
