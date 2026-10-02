"""Two RTBW endpoints over ONE existing paired Live/native acquisition owner.

No SDK calls, second opener, raw I/Q, DSP or inferred RF-path identity. The
common profile and native aggregate budget are admitted by the existing port.
Paired retuning Sweep and UI selection remain separate, unqualified work.
"""

from __future__ import annotations

from dataclasses import replace

from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, PairedCaptureMetadata
from sdr_monitor.domain.live import LiveConfiguration, LiveSessionState
from sdr_monitor.domain.paired_live import PairedLiveRequest
from sdr_monitor.domain.pane_scheduler import CaptureJob, CaptureMeasurementMode, PaneCaptureProfile
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint, SpectrumTraceEndpoint

from .ad936x_rtbw_pane_owner import (
    Ad936xRtbwPaneOwner, _KNOWN_DETECTORS, _KNOWN_WINDOWS, _REQUIRED_READBACK, _applied_covers_job,
)
from .pane_resource_session import PaneCaptureAdmission


class Ad936xPairedPaneOwner(Ad936xRtbwPaneOwner):
    """One lease/transaction, two typed native producers, one common RF plan."""

    def __init__(self, live: LiveSessionApplicationService, *,
                 physical_stream_resource_id: str, source_id: str,
                 endpoints: tuple[ReceiverEndpoint, ...]) -> None:
        endpoints = tuple(endpoints)
        if (len(endpoints) != 2 or any(not isinstance(item, ReceiverEndpoint) for item in endpoints)
                or {item.selection for item in endpoints} != {ReceiverChainSelection.RX1, ReceiverChainSelection.RX2}
                or len({item.endpoint_id for item in endpoints}) != 2
                or any(item.source_id != source_id
                       or item.physical_stream_resource_id != physical_stream_resource_id for item in endpoints)):
            raise ValueError("paired pane requires two exact distinct RX1/RX2 endpoints on one selected source")
        by_chain = {item.selection: item for item in endpoints}
        self._endpoints = (by_chain[ReceiverChainSelection.RX1], by_chain[ReceiverChainSelection.RX2])
        super().__init__(live, physical_stream_resource_id=physical_stream_resource_id,
                         source_id=source_id, receiver_endpoint_id=self._endpoints[0].endpoint_id)
        before = live.current_snapshot()
        device = before.device
        if device is None or device.capabilities.receiver_topology is None or before.applied is None:
            raise ValueError("paired pane requires the currently observed topology and staged profile")
        self._topology = device.capabilities.receiver_topology
        self._session_id = str(before.session_id)
        self._request(before.applied.applied).validate_snapshot(before)
        self._last_pair_key: tuple[int, int, int] | None = None

    def _request(self, configuration: LiveConfiguration) -> PairedLiveRequest:
        # Endpoint IDs are explicit caller-provided producer IDs, not inferred
        # serials. The native SourceDescriptor receives these very same values.
        return PairedLiveRequest(self._source_id, self._session_id, self._topology,
                                 configuration, *(item.endpoint_id for item in self._endpoints))

    def validate_endpoint(self, endpoint: ReceiverEndpoint | SpectrumTraceEndpoint) -> None:
        if endpoint not in self._endpoints:
            raise ValueError("foreign paired endpoint or chain")

    def receiver_identity(self, endpoint_id: str) -> str:
        for endpoint in self._endpoints:
            if endpoint.endpoint_id == endpoint_id:
                return endpoint.selection.name
        raise ValueError("foreign paired endpoint")

    def validate_job(self, job: CaptureJob) -> None:
        ids = tuple(sorted(item.endpoint_id for item in self._endpoints))
        profile = job.profile
        if (job.receiver_endpoint_ids != ids
                or job.physical_stream_resource_id != self.physical_stream_resource_id
                or not isinstance(profile, PaneCaptureProfile)
                or profile.measurement_mode is not CaptureMeasurementMode.RTBW
                or profile.unit != "dBFS/bin"
                or profile.gain_mode != "manual" or profile.manual_gain_db is None
                or profile.calibration_profile_id is not None
                or profile.window.strip().casefold().replace("_", "-").replace(" ", "-") not in _KNOWN_WINDOWS
                or profile.detector.strip().casefold().replace("_", "-").replace(" ", "-") not in _KNOWN_DETECTORS
                or 1.0 - profile.hop_size / profile.fft_size >= 0.95):
            raise ValueError("paired capture requires both endpoints under one common profile")

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        before = self._live.current_snapshot()
        state = self._live.analyzer_state
        if (state is None or state.phase is not AnalyzerPhase.IDLE or self._live.is_running()
                or before.state is not LiveSessionState.CONNECTED or before.stop_required
                or before.applied is None):
            raise RuntimeError("paired pane requires an idle selected Live graph")
        self._request(before.applied.applied).validate_snapshot(before)
        profile = job.profile
        assert isinstance(profile, PaneCaptureProfile)
        gain = profile.manual_gain_db
        assert gain is not None
        requested = replace(before.applied.applied,
            center_hz=(job.start_hz + job.stop_hz) / 2.0,
            sample_rate_hz=profile.sample_rate_hz, analog_bandwidth_hz=profile.analog_bandwidth_hz,
            gain_db=gain, fft_size=profile.fft_size,
            overlap_ratio=1.0 - profile.hop_size / profile.fft_size,
            window=profile.window, detector=profile.detector)
        staged = self._live.apply_configuration(requested)
        if staged.error is not None or staged.applied is None or staged.applied.applied != requested:
            raise RuntimeError("paired pane profile was not staged exactly")
        self._live.stage_paired_rtbw(self._request(requested))
        self._last_pair_key = None
        started = self._live.start()
        applied = started.applied
        if (started.error is not None or started.state is not LiveSessionState.RUNNING
                or started.device is None or started.device.device_id != self._source_id
                or str(started.session_id) != self._session_id or started.receiver_id is not None
                or type(started.acquisition_epoch) is not int or started.acquisition_epoch < 0
                or applied is None or not _REQUIRED_READBACK <= set(applied.readback_fields)
                or not _applied_covers_job(requested, applied.applied, job) or started.unit != profile.unit):
            raise RuntimeError("paired pane Start lacks actual common source/epoch/readback")
        return PaneCaptureAdmission(job.capture_id, self._source_id, profile.measurement_mode,
            started.unit, job.receiver_endpoint_ids, started.acquisition_epoch,
            session_id=self._session_id, config_generation=started.active_config_generation,
            sample_rate_hz=applied.applied.sample_rate_hz, fft_size=applied.applied.fft_size,
            hop_size=profile.hop_size,
            endpoint_source_ids=tuple((item, item) for item in job.receiver_endpoint_ids))

    def stop_capture_and_wait(self) -> None:
        super().stop_capture_and_wait()
        # Clear ONLY after confirmed Stop. Ordinary Live must not inherit an
        # invisible pair, nor may a subsequent paired epoch borrow old caches.
        self._live.clear_paired_rtbw()
        self._last_pair_key = None

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        state = self._live.current_snapshot()
        if state.error is not None or state.state is not LiveSessionState.RUNNING:
            raise RuntimeError("paired pane Live producer is no longer running")
        publications = self._live.poll_paired_publications()
        if len(publications) > 1:
            raise RuntimeError("paired pane latest publication exceeded its native bound")
        result: list[tuple[str, AnalyzerFrameBundle]] = []
        for pair in publications:
            spectrum = pair.primary.spectrum
            assert spectrum is not None  # PairedLivePublication validates BOTH.
            key = (pair.synchronization_epoch, pair.first_sample_index, int(spectrum.sequence))
            if self._last_pair_key == key:
                continue
            if self._last_pair_key is not None and (
                    key[0] < self._last_pair_key[0] or key[2] <= self._last_pair_key[2]
                    or (key[0] == self._last_pair_key[0] and key[1] <= self._last_pair_key[1])):
                raise RuntimeError("paired pane producer synchronization/index/sequence regressed")
            metadata = PairedCaptureMetadata(pair.synchronization_epoch, pair.first_sample_index,
                                             pair.shared_input_gaps_before)
            bundles: list[tuple[str, AnalyzerFrameBundle]] = []
            for endpoint, snapshot in zip(self._endpoints, (pair.primary, pair.secondary), strict=True):
                bundle = self._live.analyzer_bundle_for_snapshot(snapshot)
                if (bundle is None or bundle.identity is None
                        or bundle.identity.source_id != endpoint.endpoint_id
                        or bundle.receiver_id != endpoint.selection.name):
                    raise RuntimeError("paired pane refused a foreign or incomplete native producer")
                bundles.append((endpoint.endpoint_id, replace(bundle, paired_capture=metadata)))
            result.extend(bundles)  # Both validated BEFORE either can be delivered.
            self._last_pair_key = key
        return tuple(result)


__all__ = ["Ad936xPairedPaneOwner"]
