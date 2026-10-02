"""RTL pane capture through its selected common Analyzer RTBW owner only."""

from __future__ import annotations

from dataclasses import replace
from typing import ContextManager

from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import CaptureJob, CaptureMeasurementMode, RtlRtbwPaneProfile
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint, SpectrumTraceEndpoint

from .pane_resource_session import PaneCaptureAdmission


class RtlRtbwPaneOwner:
    def __init__(self, live: LiveSessionApplicationService, *,
                 physical_stream_resource_id: str, source_id: str,
                 receiver_endpoint_id: str) -> None:
        if any(not isinstance(value, str) or not value for value in (
                physical_stream_resource_id, source_id, receiver_endpoint_id)):
            raise ValueError("RTL pane needs exact resource, source and RX endpoint")
        self.physical_stream_resource_id = physical_stream_resource_id
        self._source_id = source_id
        self._endpoint_id = receiver_endpoint_id
        self._live = live
        self._claim = object()
        self._last_frame: object | None = None

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None:
        if (not isinstance(endpoint, ReceiverEndpoint)
                or endpoint.endpoint_id != self._endpoint_id
                or endpoint.source_id != self._source_id
                or endpoint.physical_stream_resource_id != self.physical_stream_resource_id
                or endpoint.selection is not ReceiverChainSelection.RX1):
            raise ValueError("RTL pane requires its one selected RX1 producer")

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if (not isinstance(profile, RtlRtbwPaneProfile)
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.measurement_mode is not CaptureMeasurementMode.RTBW
                or profile.unit != "dBFS/bin" or profile.request_template.source_id != self._source_id):
            raise ValueError("RTL pane requires its exact RTBW source/profile")

    def control_transaction(self) -> ContextManager[None]:
        return self._live.pane_control_transaction(self._claim)

    def release_control_claim(self) -> None:
        self._live.release_pane_control(self._claim)

    def recording_active(self) -> bool:
        return self._live.pane_recording_conflict()

    def recording_conflict(self, job: CaptureJob) -> bool:
        self.validate_job(job)
        return self.recording_active()

    def receiver_identity(self, endpoint_id: str) -> None:
        if endpoint_id != self._endpoint_id:
            raise ValueError("foreign RTL RX endpoint")
        return None  # One digital stream, no stable physical RX-chain identity.

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        profile = job.profile
        assert isinstance(profile, RtlRtbwPaneProfile)
        before = self._live.current_snapshot()
        selection = self._live.current_source_selection()
        state = self._live.analyzer_state
        if (selection is None or selection.selected is None or selection.release_pending
                or selection.selected.device_id != self._source_id
                or selection.selected.family is not DeviceFamily.RTL_SDR
                or selection.selected.binding.rtl_session_route is None
                or state is None or state.phase is not AnalyzerPhase.IDLE
                or self._live.is_running() or before.state is not LiveSessionState.CONNECTED
                or before.stop_required or before.source_choice is not selection.selected
                or self.recording_active()):
            raise RuntimeError("Selected RTL session is not idle and recording-free")
        center = (job.start_hz + job.stop_hz) / 2.0
        if not center.is_integer():
            raise ValueError("RTL pane center needs exact whole hertz")
        requested = replace(profile.request_template, center_frequency_hz=int(center))
        staged = self._live.stage_rtl_configuration(RtlConfigurationPatch(
            requested, selection.revision, int(before.generation)))
        if (staged.error is not None or staged.state is not LiveSessionState.CONNECTED
                or not isinstance(staged.rtl_request, RtlLiveRequest)
                or replace(staged.rtl_request, configuration_generation=1)
                   != replace(requested, configuration_generation=1)):
            raise RuntimeError("RTL pane request was not staged exactly")
        started = self._live.start()
        applied = started.rtl_request
        if (started.error is not None or started.state is not LiveSessionState.RUNNING
                or started.active_source_id != self._source_id
                or started.source_choice is not selection.selected
                or type(started.acquisition_epoch) is not int or started.acquisition_epoch <= 0
                or applied is None or applied.center_frequency_hz != requested.center_frequency_hz
                or applied.sample_rate_hz != requested.sample_rate_hz
                or started.active_config_generation != applied.configuration_generation
                or started.unit != profile.unit):
            raise RuntimeError("RTL pane Start lacks exact native readback/epoch receipt")
        self._last_frame = None
        return PaneCaptureAdmission(job.capture_id, self._source_id, CaptureMeasurementMode.RTBW,
            started.unit, (self._endpoint_id,), started.acquisition_epoch,
            session_id=str(started.session_id), config_generation=started.active_config_generation,
            sample_rate_hz=applied.sample_rate_hz, fft_size=applied.fft_size,
            hop_size=applied.hop_size)

    def stop_capture_and_wait(self) -> None:
        stopped = self._live.stop()
        state = self._live.analyzer_state
        if (stopped.error is not None or stopped.stop_required or self._live.is_running()
                or state is None or state.phase is not AnalyzerPhase.IDLE):
            raise RuntimeError("RTL pane Stop did not confirm owner release")
        self._last_frame = None

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        snapshots = self._live.poll_published_snapshots()
        if len(snapshots) > 64:
            raise RuntimeError("RTL pane publication batch exceeded its bound")
        delivered: list[tuple[str, AnalyzerFrameBundle]] = []
        for snapshot in snapshots:
            if snapshot.error is not None or snapshot.state is not LiveSessionState.RUNNING:
                raise RuntimeError("RTL pane owner failed; explicit Stop required")
            if snapshot.spectrum is None or snapshot.spectrum is self._last_frame:
                continue
            bundle = self._live.analyzer_bundle_for_snapshot(snapshot)
            if bundle is not None:
                delivered.append((self._endpoint_id, bundle))
                self._last_frame = snapshot.spectrum
        return tuple(delivered)


__all__ = ["RtlRtbwPaneOwner"]
