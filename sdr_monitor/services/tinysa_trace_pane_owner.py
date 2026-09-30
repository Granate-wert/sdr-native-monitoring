"""APP-07 tinySA trace pane adapter over the SAME common Analyzer owner.

The existing instrument service owns serial version/settings/scan/Stop. This
adapter neither opens a port nor invents I/Q geometry or a parallel SDK path.
It is called by the pane resource session on a control/poll worker, never Qt.
"""

from __future__ import annotations

from dataclasses import fields, replace
from math import isfinite
from typing import ContextManager

from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundle_from_sweep
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureJob, CaptureMeasurementMode, TinySaTracePaneProfile
from sdr_monitor.domain.receiver_topology import ReceiverEndpoint, SpectrumTraceEndpoint
from sdr_monitor.domain.sweep_lines import SweepLineFrame
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest

from .pane_resource_session import PaneCaptureAdmission
from .pane_resource_diagnostics import (
    PaneDiagnosticError, PaneFailureReason, PaneFailureStage, PaneResourceFailure,
)
from .tinysa_common_analyzer import TinySaCommonAnalyzerService


def _same_request_intent(actual: TinySaSweepRequest, expected: TinySaSweepRequest) -> bool:
    """The shared Analyzer assigns a fresh epoch by replacing the request."""
    for field in fields(TinySaSweepRequest):
        if field.name == "epoch":
            continue
        left, right = getattr(actual, field.name), getattr(expected, field.name)
        if field.name in {"source", "external_correction"}:
            if left is not right:
                return False
        elif left != right:
            return False
    return True


class TinySaTracePaneOwner:
    """One instrument output and exact selected source, with no synthetic RX."""

    def __init__(self, live: LiveSessionApplicationService, instrument: TinySaCommonAnalyzerService, *,
                 physical_stream_resource_id: str, source_id: str,
                 trace_endpoint_id: str) -> None:
        if any(not isinstance(value, str) or not value for value in (
                physical_stream_resource_id, source_id, trace_endpoint_id)):
            raise ValueError("tinySA pane requires exact resource, source and trace identities")
        self.physical_stream_resource_id = physical_stream_resource_id
        self._source_id = source_id
        self._endpoint_id = trace_endpoint_id
        self._live = live
        self._instrument = instrument
        self._control_claim = object()
        self._last_line: SweepLineFrame | None = None
        self._epoch = 0

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None:
        if (not isinstance(endpoint, SpectrumTraceEndpoint)
                or endpoint.endpoint_id != self._endpoint_id
                or endpoint.source_id != self._source_id
                or endpoint.physical_stream_resource_id != self.physical_stream_resource_id):
            raise ValueError("tinySA pane owner accepts only its exact instrument trace")

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if (not isinstance(profile, TinySaTracePaneProfile)
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.measurement_mode is not CaptureMeasurementMode.INSTRUMENT_TRACE
                or profile.unit != "dBm"
                or profile.request_template.source.device_id != self._source_id
                or any(not isfinite(value) or not float(value).is_integer()
                       for value in (job.start_hz, job.stop_hz))):
            raise ValueError("tinySA pane requires exact typed integer-Hz instrument intent")

    def control_transaction(self) -> ContextManager[None]:
        return self._live.pane_control_transaction(self._control_claim)

    def release_control_claim(self) -> None:
        self._live.release_pane_control(self._control_claim)

    def recording_active(self) -> bool:
        return self._live.pane_recording_conflict()

    def recording_conflict(self, job: CaptureJob) -> bool:
        self.validate_job(job)
        return self.recording_active()

    def receiver_identity(self, endpoint_id: str) -> None:
        if endpoint_id != self._endpoint_id:
            raise ValueError("foreign tinySA trace endpoint")
        return None

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        profile = job.profile
        assert isinstance(profile, TinySaTracePaneProfile)
        selection = self._live.current_source_selection()
        state = self._live.analyzer_state
        source = profile.request_template.source
        if (selection is None or selection.selected is not source
                or selection.revision != profile.request_template.selection_revision
                or selection.release_pending or source.family is not DeviceFamily.TINYSA
                or state is None or state.phase is not AnalyzerPhase.IDLE
                or self._live.is_running() or self._live.current_snapshot().stop_required
                or self._instrument.stop_required or self._epoch >= (1 << 64) - 1):
            raise RuntimeError("Selected tinySA common Analyzer is not idle with the expected source")
        request = replace(profile.request_template,
                          start_hz=int(job.start_hz), stop_hz=int(job.stop_hz),
                          points=profile.points, epoch=self._epoch + 1)
        started = self._live.start_sweep(request)
        run = self._instrument.instrument_run_identity
        identity = source.binding.calibration_identity
        snapshot = source.binding.snapshot
        if (started.phase is not AnalyzerPhase.RUNNING
                or not self._instrument.stop_required
                or run is None or not _same_request_intent(run.request, request)
                or run.request.epoch < request.epoch or run.request.epoch <= self._epoch
                or identity is None or snapshot is None
                or snapshot.model_id not in {"tinysa_basic", "tinysa_ultra"}):
            raise RuntimeError("tinySA pane Start lacks exact retained instrument run identity")
        self._epoch = run.request.epoch
        self._last_line = None
        return PaneCaptureAdmission(
            job.capture_id, self._source_id, CaptureMeasurementMode.INSTRUMENT_TRACE,
            "dBm", (self._endpoint_id,), run.request.epoch,
            config_generation=run.configuration_generation,
            trace_points=profile.points,
            instrument_model_id=snapshot.model_id,
            instrument_identity_key=identity.device_identity_key,
            firmware_fingerprint=identity.firmware_fingerprint,
        )

    def stop_capture_and_wait(self) -> None:
        stopped = self._live.stop()
        if (stopped.error is not None or stopped.stop_required
                or self._instrument.stop_required
                or self._live.analyzer_state is None
                or self._live.analyzer_state.phase is not AnalyzerPhase.IDLE):
            raise RuntimeError("tinySA pane Stop did not confirm serial owner release")
        self._last_line = None

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        snapshot = self._instrument.poll_latest()
        if snapshot.metrics.has_error:
            raise PaneDiagnosticError("tinySA instrument worker failed; explicit Stop required",
                failure=PaneResourceFailure(PaneFailureStage.OWNER_POLL,
                    PaneFailureReason.INSTRUMENT_FAILURE, self._instrument.acquisition_failure))
        line = snapshot.line
        if line is None or line is self._last_line:
            return ()
        if not isinstance(line, SweepLineFrame) or line.instrument is None:
            raise PaneDiagnosticError("tinySA pane received a non-instrument publication",
                failure=PaneResourceFailure(PaneFailureStage.PUBLICATION_VALIDATION,
                                            PaneFailureReason.INVALID_PUBLICATION))
        bundle = bundle_from_sweep(line)
        self._last_line = line
        return ((self._endpoint_id, bundle),)


__all__ = ["TinySaTracePaneOwner"]
