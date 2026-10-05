"""One HackRF RX owner for explicit RTBW or host Sweep pane activations.

The selected V2 graph and its common Analyzer own both strategies. A mode
change is Stop -> confirmed release -> next Start under the *same* pane claim;
this adapter neither loads another SDK nor opens another receiver.
"""

from __future__ import annotations

from dataclasses import replace
from math import isclose

from sdr_monitor.application.analyzer_session import AnalyzerMode, AnalyzerPhase
from sdr_monitor.application.analyzer_sweep_router import AnalyzerSweepRouter
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundle_from_sweep
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import (
    CaptureJob, CaptureMeasurementMode, HackrfSweepPaneProfile,
)
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.sweep_progress import SweepProgressFrame

from .hackrf_rtbw_pane_owner import HackrfRtbwPaneOwner
from .pane_resource_session import PaneCaptureAdmission
from ..domain.layer_journal import LayerJournalSnapshot


class HackrfPaneOwner(HackrfRtbwPaneOwner):
    """Mode-specific publications, one exact RX endpoint and control claim."""

    def __init__(self, live: LiveSessionApplicationService, sweep: AnalyzerSweepRouter, *,
                 physical_stream_resource_id: str, source_id: str,
                 receiver_endpoint_id: str, sweep_available: bool) -> None:
        super().__init__(live, physical_stream_resource_id=physical_stream_resource_id,
                         source_id=source_id, receiver_endpoint_id=receiver_endpoint_id)
        if not isinstance(sweep, AnalyzerSweepRouter) or type(sweep_available) is not bool:
            raise TypeError("HackRF pane needs the selected common Sweep router and capability")
        self._sweep_router = sweep
        self._sweep_available = sweep_available
        self._active_mode: CaptureMeasurementMode | None = None
        self._last_sweep_epoch = -1
        self._last_progress: tuple[int, int, int] | None = None
        self._last_line: tuple[int, int] | None = None
        self._active_sweep_profile: HackrfSweepPaneProfile | None = None
        self._terminal_layers: tuple[LayerJournalSnapshot, ...] = ()

    def layer_journal_snapshots(self) -> tuple[LayerJournalSnapshot, ...]:
        if self._active_mode is None:
            return self._terminal_layers
        return (self._sweep_router.layer_journal_snapshots() if self._active_mode is CaptureMeasurementMode.SWEEP
                else super().layer_journal_snapshots())

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if not isinstance(profile, HackrfSweepPaneProfile):
            super().validate_job(job)
            return
        request = profile.request_template
        if (not self._sweep_available
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.measurement_mode is not CaptureMeasurementMode.SWEEP
                or profile.unit != "dBFS/bin"
                or request.source.family is not DeviceFamily.HACKRF
                or request.source.device_id != self._source_id
                or job.start_hz != profile.pane_crop_start_hz
                or job.stop_hz != profile.pane_crop_stop_hz):
            raise ValueError("HackRF pane Sweep requires its exact selected source and bounded plan")

    def recording_conflict(self, job: CaptureJob) -> bool:
        self.validate_job(job)
        return self.recording_active()

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        self._terminal_layers = ()
        profile = job.profile
        if not isinstance(profile, HackrfSweepPaneProfile):
            self._active_mode = CaptureMeasurementMode.RTBW  # retain Stop duty on a partial Start
            return super().start_capture(job)
        selection = self._live.current_source_selection()
        state = self._live.analyzer_state
        request = profile.request_template
        if (selection is None or selection.selected is not request.source
                or selection.revision != request.selection_revision
                or selection.release_pending or state is None
                or state.phase is not AnalyzerPhase.IDLE or self._live.is_running()
                or self._live.current_snapshot().stop_required
                or self._last_sweep_epoch >= (1 << 64) - 1):
            raise RuntimeError("Selected HackRF Analyzer is not idle with the exact Sweep source")
        self._active_mode = CaptureMeasurementMode.SWEEP  # before the possibly effectful Start
        self._active_sweep_profile = profile
        started = self._live.start_sweep(replace(request, epoch=self._last_sweep_epoch + 1))
        confirmed_selection = self._live.current_source_selection()
        if (started.mode is not AnalyzerMode.SWEEP or started.phase is not AnalyzerPhase.RUNNING
                or type(started.sweep_epoch) is not int
                or started.sweep_epoch <= self._last_sweep_epoch
                or confirmed_selection is None
                or selection.selected is not confirmed_selection.selected):
            raise RuntimeError("HackRF pane Sweep Start lacks its common Analyzer epoch")
        self._last_sweep_epoch = started.sweep_epoch
        self._last_progress = None
        self._last_line = None
        return PaneCaptureAdmission(
            job.capture_id, self._source_id, CaptureMeasurementMode.SWEEP,
            profile.unit, (self._endpoint_id,), started.sweep_epoch,
            sample_rate_hz=profile.sample_rate_hz, fft_size=profile.fft_size,
            hop_size=None,
        )

    def stop_capture_and_wait(self) -> None:
        if self._active_mode is not CaptureMeasurementMode.SWEEP:
            super().stop_capture_and_wait()
        else:
            stopped = self._live.stop()
            state = self._live.analyzer_state
            if (stopped.error is not None or stopped.stop_required or state is None
                    or state.phase is not AnalyzerPhase.IDLE):
                raise RuntimeError("HackRF pane Sweep Stop did not confirm common owner release")
        try:
            self._terminal_layers = self.layer_journal_snapshots()
        except Exception:  # noqa: BLE001 - cached optional telemetry, not hardware cleanup.
            self._terminal_layers = ()
        self._active_mode = None
        self._last_progress = None
        self._last_line = None
        self._active_sweep_profile = None

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        if self._active_mode is not CaptureMeasurementMode.SWEEP:
            return super().poll_bundles()
        snapshot = self._sweep_router.poll_latest()
        if snapshot.metrics.has_error:
            raise RuntimeError("HackRF pane Sweep worker failed; explicit Stop required")
        result: list[tuple[str, AnalyzerFrameBundle]] = []
        line = snapshot.line
        progress = snapshot.progress
        profile = self._active_sweep_profile
        if profile is None:
            raise RuntimeError("HackRF pane Sweep has no retained physical FFT contract")
        if line is not None:
            if not isinstance(line, SweepLineFrame) or line.source_id != self._source_id:
                raise RuntimeError("HackRF pane Sweep terminal source changed")
            if (line.physical_fft_size != profile.fft_size
                    or line.analysis_window_hz != 5_000_000.0
                    or line.analysis_bins_per_usable_window != profile.fft_size // 4
                    or not isclose(line.physical_fft_bin_width_hz,
                                   profile.sample_rate_hz / profile.fft_size,
                                   rel_tol=1e-10, abs_tol=1e-7)):
                raise RuntimeError("HackRF pane Sweep terminal FFT geometry changed")
            line_key = (line.epoch, line.sequence)
            if self._last_line is None or line_key > self._last_line:
                result.append((self._endpoint_id, bundle_from_sweep(line)))
                self._last_line = line_key
        if progress is not None:
            if not isinstance(progress, SweepProgressFrame) or progress.source_id != self._source_id:
                raise RuntimeError("HackRF pane Sweep progress source changed")
            progress_key = (progress.epoch, progress.sequence, progress.revision)
            if ((self._last_progress is None or progress_key > self._last_progress)
                    and (self._last_line is None or progress_key[:2] > self._last_line)):
                result.append((self._endpoint_id, bundle_from_sweep(progress)))
                self._last_progress = progress_key
        return tuple(result)


__all__ = ["HackrfPaneOwner"]
