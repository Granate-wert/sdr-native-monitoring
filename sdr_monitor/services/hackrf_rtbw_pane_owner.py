"""APP-07 HackRF pane adapter over the SAME selected common Live owner.

No second SDK loader, independent device selector, raw I/Q or guessed sample
rate readback is introduced. A caller supplies a separately composed graph
for each concurrently active physical resource; this adapter cannot make the
current product's single global Analyzer graph parallel by itself.
"""

from __future__ import annotations

from dataclasses import replace
from typing import ContextManager

from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from sdr_monitor.domain.live import LiveSessionState
from sdr_monitor.domain.pane_scheduler import CaptureJob, CaptureMeasurementMode, HackrfRtbwPaneProfile
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint, SpectrumTraceEndpoint

from .pane_resource_session import PaneCaptureAdmission


def _same_request_intent(actual: HackrfLiveRequest | None, expected: HackrfLiveRequest) -> bool:
    return (isinstance(actual, HackrfLiveRequest)
            and replace(actual, configuration_generation=1)
            == replace(expected, configuration_generation=1))


class HackrfRtbwPaneOwner:
    """One RX1 HackRF resource; Stage/Start/Stop remain on its Live graph."""

    def __init__(self, live: LiveSessionApplicationService, *,
                 physical_stream_resource_id: str, source_id: str,
                 receiver_endpoint_id: str) -> None:
        if any(not isinstance(value, str) or not value for value in (
                physical_stream_resource_id, source_id, receiver_endpoint_id)):
            raise ValueError("HackRF pane requires exact resource, source and RX endpoint identities")
        self.physical_stream_resource_id = physical_stream_resource_id
        self._source_id = source_id
        self._endpoint_id = receiver_endpoint_id
        self._live = live
        self._control_claim = object()
        self._last_publication: tuple[object, object, object] | None = None

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None:
        if (not isinstance(endpoint, ReceiverEndpoint)
                or endpoint.endpoint_id != self._endpoint_id
                or endpoint.source_id != self._source_id
                or endpoint.physical_stream_resource_id != self.physical_stream_resource_id
                or endpoint.selection is not ReceiverChainSelection.RX1):
            raise ValueError("HackRF pane owner has one proven RX1 producer")

    def validate_job(self, job: CaptureJob) -> None:
        profile = job.profile
        if (not isinstance(profile, HackrfRtbwPaneProfile)
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.measurement_mode is not CaptureMeasurementMode.RTBW
                or profile.unit != "dBFS/bin"
                or profile.request_template.source_id != self._source_id):
            raise ValueError("HackRF pane requires its exact uncalibrated RX/DSP source profile")

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
            raise ValueError("foreign HackRF RX endpoint")
        # The current reduced HackRF producer has no separate RX tag.
        return None

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        profile = job.profile
        assert isinstance(profile, HackrfRtbwPaneProfile)
        before = self._live.current_snapshot()
        selection = self._live.current_source_selection()
        analyzer_state = self._live.analyzer_state
        if (selection is None or selection.selected is None or selection.release_pending
                or selection.selected.device_id != self._source_id
                or selection.selected.family is not DeviceFamily.HACKRF
                or analyzer_state is None or analyzer_state.phase is not AnalyzerPhase.IDLE
                or self._live.is_running() or before.state is not LiveSessionState.CONNECTED
                or before.stop_required or before.source_choice is not selection.selected):
            raise RuntimeError("Selected HackRF Live graph is not idle with the expected source")
        requested = replace(profile.request_template,
                            center_frequency_hz=(job.start_hz + job.stop_hz) / 2.0)
        patch = HackrfConfigurationPatch(requested, selection.revision, int(before.generation))
        staged = self._live.stage_hackrf_configuration(patch)
        if (staged.error is not None or staged.state is not LiveSessionState.CONNECTED
                or not _same_request_intent(staged.hackrf_request, requested)):
            raise RuntimeError("HackRF pane request was not staged exactly")
        started = self._live.start()
        applied = started.hackrf_request
        if (started.error is not None or started.state is not LiveSessionState.RUNNING
                or started.active_source_id != self._source_id
                or started.source_choice is not selection.selected
                or type(started.acquisition_epoch) is not int or started.acquisition_epoch < 0
                or started.session_id is None
                or applied is None or not _same_request_intent(applied, requested)
                or started.active_config_generation != applied.configuration_generation
                or started.unit != profile.unit):
            raise RuntimeError("HackRF pane Start lacks exact source, epoch or request acknowledgement")
        self._last_publication = None
        return PaneCaptureAdmission(
            job.capture_id, self._source_id, CaptureMeasurementMode.RTBW,
            started.unit, (self._endpoint_id,), started.acquisition_epoch,
            session_id=str(started.session_id),
            config_generation=started.active_config_generation,
            sample_rate_hz=applied.sample_rate_hz,
            fft_size=applied.fft_size,
            hop_size=applied.hop_size,
        )

    def stop_capture_and_wait(self) -> None:
        stopped = self._live.stop()
        if (stopped.error is not None or stopped.stop_required or self._live.is_running()
                or self._live.analyzer_state is None
                or self._live.analyzer_state.phase is not AnalyzerPhase.IDLE):
            raise RuntimeError("HackRF pane Stop did not confirm owner release")
        self._last_publication = None

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        snapshots = self._live.poll_published_snapshots()
        if len(snapshots) > 64:
            raise RuntimeError("HackRF pane publication batch exceeded its bound")
        delivered: list[tuple[str, AnalyzerFrameBundle]] = []
        for snapshot in snapshots:
            if snapshot.error is not None or snapshot.state is not LiveSessionState.RUNNING:
                raise RuntimeError("HackRF Live producer is no longer running")
            if snapshot.spectrum is None:
                continue
            publication = (snapshot.spectrum, snapshot.persistence,
                           getattr(snapshot, "waterfall_line", None))
            prior = self._last_publication
            if prior is not None and all(current is previous for current, previous in zip(publication, prior, strict=True)):
                continue
            bundle = self._live.analyzer_bundle_for_snapshot(snapshot)
            if bundle is not None:
                delivered.append((self._endpoint_id, bundle))
                self._last_publication = publication
        return tuple(delivered)


__all__ = ["HackrfRtbwPaneOwner"]
