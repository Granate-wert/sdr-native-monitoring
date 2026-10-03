"""Paired Sweep CaptureJob adapter over ONE admitted Live/native coordinator.

No second SDK opener, raw I/Q, UI policy or implicit mode fallback. A failed
Stop/close retains factory and claim for an explicit cleanup retry.
"""

from __future__ import annotations

from typing import Any

from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundles_from_paired_sweep
from sdr_monitor.domain.paired_sweep_archive import PairedSweepTerminalArchive
from sdr_monitor.domain.paired_sweep import PairedSweepRunIdentity
from sdr_monitor.domain.pane_scheduler import Ad936xPairedSweepPaneProfile, CaptureJob
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint, SpectrumTraceEndpoint

from .ad936x_rtbw_pane_owner import Ad936xRtbwPaneOwner
from .native_continuous_sweep_factory import NativeContinuousSweepPlanFactory
from .pane_resource_session import PaneCaptureAdmission


class Ad936xPairedSweepPaneOwner(Ad936xRtbwPaneOwner):
    """Inert adapter; Stage cannot open RF and Start must acquire exact run."""

    def __init__(self, live: LiveSessionApplicationService, *,
                 physical_stream_resource_id: str, source_id: str,
                 endpoints: tuple[ReceiverEndpoint, ...]) -> None:
        if (len(endpoints) != 2 or any(not isinstance(item, ReceiverEndpoint) for item in endpoints)
                or {item.selection for item in endpoints} != {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
                or len({item.endpoint_id for item in endpoints}) != 2
                or any(item.source_id != source_id
                       or item.physical_stream_resource_id != physical_stream_resource_id for item in endpoints)):
            raise ValueError("paired Sweep requires two exact endpoints on one resource")
        self._endpoints = tuple(sorted(endpoints, key=lambda item: item.selection.name))
        super().__init__(live, physical_stream_resource_id=physical_stream_resource_id,
                         source_id=source_id, receiver_endpoint_id=self._endpoints[0].endpoint_id)
        self._factory: NativeContinuousSweepPlanFactory | None = None
        self._coordinator: Any = None
        self._last_line: tuple[int, int] | None = None
        self._last_progress: tuple[int, int, int] | None = None
        self._terminal_archive: PairedSweepTerminalArchive | None = None
        self._terminal_archive_error: str | None = None
        self._started_run: PairedSweepRunIdentity | None = None

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None:
        if endpoint not in self._endpoints:
            raise ValueError("foreign paired Sweep endpoint")

    def receiver_identity(self, endpoint_id: str) -> str:
        for endpoint in self._endpoints:
            if endpoint.endpoint_id == endpoint_id:
                return endpoint.selection.name
        raise ValueError("foreign paired Sweep endpoint")

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if (not isinstance(profile, Ad936xPairedSweepPaneProfile)
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != tuple(sorted(item.endpoint_id for item in self._endpoints))
                or profile.paired_request.pair.device_id != self._source_id
                or (profile.paired_request.pair.primary_source_id,
                    profile.paired_request.pair.secondary_source_id)
                   != tuple(item.endpoint_id for item in self._endpoints)):
            raise ValueError("paired Sweep requires the exact typed common intent and BOTH endpoints")
        selected = self._live.current_source_selection()
        if selected is None or selected.selected is not profile.source or selected.release_pending:
            raise ValueError("paired Sweep staged source is no longer selected")
        profile.paired_request.validate_applied(self._live.current_snapshot(), selected.revision)

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        if self._factory is not None:
            raise RuntimeError("paired Sweep still owns a prior capture; explicit Stop required")
        # One bounded retained drain, not another active queue. Release the
        # previous archive BEFORE a new native/publication reservation begins.
        self._terminal_archive = None
        self._terminal_archive_error = None
        self._started_run = None
        profile = job.profile
        assert isinstance(profile, Ad936xPairedSweepPaneProfile)
        # PaneResourceSession already owns this SAME application transaction.
        # Retain factory immediately so partial construction/Start can be closed.
        self._factory = NativeContinuousSweepPlanFactory.from_paired_application(
            self._live, profile.paired_request, control_claim=self._control_claim)
        self._coordinator = self._factory.create_coordinator()
        self._coordinator.configure_paired(self._factory.build_paired())
        run = self._coordinator.start()
        self._started_run = run
        self._last_line = self._last_progress = None
        return PaneCaptureAdmission(job.capture_id, self._source_id, profile.measurement_mode,
            profile.unit, job.receiver_endpoint_ids, run.acquisition_epoch,
            session_id=run.request.pair.session_id, sample_rate_hz=profile.sample_rate_hz,
            fft_size=profile.fft_size, hop_size=None,
            endpoint_source_ids=tuple((item, item) for item in job.receiver_endpoint_ids),
            paired_sweep_run=run)

    def stop_capture_and_wait(self) -> None:
        archive_error = None
        if self._factory is not None:
            if self._coordinator is not None:
                self._coordinator.stop()  # retires active run BEFORE native Stop
                if self._started_run is not None and self._terminal_archive is None:
                    try:
                        self._terminal_archive = self._coordinator.poll_retired_archive()
                    except Exception as error:
                        archive_error = error
                        self._terminal_archive_error = f"{type(error).__name__}: {error}"
            try:
                self._factory.close()  # confirmed join/context release, borrowed claim retained
            except Exception as cleanup_error:
                if archive_error is not None:
                    raise ExceptionGroup("paired Sweep archival and cleanup failed", [archive_error, cleanup_error])
                raise
        self._factory = None
        self._coordinator = None
        self._started_run = None
        self._last_line = self._last_progress = None
        if archive_error is not None:
            raise RuntimeError("paired Sweep terminal archive failed; hardware cleanup completed") from archive_error

    @property
    def terminal_archive(self) -> PairedSweepTerminalArchive | None:
        """Stopped retained output only; not polled into current pane histories."""
        return self._terminal_archive

    @property
    def terminal_archive_error(self) -> str | None:
        return self._terminal_archive_error

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        if self._coordinator is None or self._coordinator.active_run is None:
            raise RuntimeError("paired Sweep capture has no active admitted run")
        if self._coordinator.last_error():
            raise RuntimeError("paired Sweep native worker failed; explicit Stop required")
        lines = self._coordinator.poll_observed_lines()
        progress = self._coordinator.poll_observed_progress()
        result: list[tuple[str, AnalyzerFrameBundle]] = []
        # Native analytical/statistics consumers precede this presentation
        # coalescing. No promise that each computed FFT is painted.
        if lines:
            pair = lines[-1]
            key = (pair.primary.epoch, pair.primary.sequence)
            if self._last_line is not None and key < self._last_line:
                raise RuntimeError("paired Sweep terminal sequence regressed")
            if self._last_line != key:
                result.extend(bundles_from_paired_sweep(pair))
                self._last_line = key
        if progress is not None:
            frame = progress.primary
            key3 = (frame.epoch, frame.sequence, frame.revision)
            if self._last_progress is not None and key3 < self._last_progress:
                raise RuntimeError("paired Sweep progress sequence regressed")
            if (self._last_progress != key3
                    and (self._last_line is None or key3[:2] > self._last_line)):
                result.extend(bundles_from_paired_sweep(progress))
                self._last_progress = key3
        return tuple(result)
