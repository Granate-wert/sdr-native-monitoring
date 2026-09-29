"""APP-07 AD936x RTBW pane adapter over the existing Live application owner.

This is one selected source and one RX endpoint per application graph. It does
not open a second IIO context, invent dual-RX RF independence, or choose a
device on behalf of the user. Other families and Sweep need their own proven
adapters before a multi-resource UI may advertise them.
"""

from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import ContextManager

from sdr_monitor.application.analyzer_session import AnalyzerPhase
from sdr_monitor.application.live_session import LiveSessionApplicationService
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle
from sdr_monitor.domain.live import LiveConfiguration, LiveSessionState
from sdr_monitor.domain.pane_scheduler import CaptureJob, CaptureMeasurementMode
from sdr_monitor.domain.receiver_topology import ReceiverChainSelection, ReceiverEndpoint

from .pane_resource_session import PaneCaptureAdmission


_REQUIRED_READBACK = frozenset({"center_hz", "sample_rate_hz", "analog_bandwidth_hz", "gain_db"})
_KNOWN_WINDOWS = frozenset({"rectangular", "hann", "hanning", "blackman-harris", "blackmanharris",
                            "flattop", "flat-top", "nuttall", "kaiser"})
_KNOWN_DETECTORS = frozenset({"sample", "peak", "positive-peak", "negative-peak",
                              "rms", "average", "average-power"})


def _applied_covers_job(requested: LiveConfiguration, applied: LiveConfiguration,
                        job: CaptureJob) -> bool:
    """Allow only sub-bin LO quantization while preserving planned coverage."""
    if (not isfinite(applied.center_hz)
            or replace(applied, center_hz=requested.center_hz,
                       backend=requested.backend) != requested):
        return False
    half_bin_hz = applied.sample_rate_hz / applied.fft_size / 2.0
    if abs(applied.center_hz - requested.center_hz) > half_bin_hz:
        return False
    usable_half_hz = job.profile.usable_capture_span_hz / 2.0
    return (job.start_hz >= applied.center_hz - usable_half_hz - half_bin_hz
            and job.stop_hz <= applied.center_hz + usable_half_hz + half_bin_hz)


class Ad936xRtbwPaneOwner:
    """Bind a pane to the *same* Live and native recording control graph.

    ``PaneResourceSession`` holds the physical-resource lease and invokes
    ``control_transaction`` around this adapter's Stage/Start or Stop. The
    transaction is supplied by the Live application's own native port, so
    concurrent native recording Start cannot cross a retune boundary.
    """

    def __init__(self, live: LiveSessionApplicationService, *,
                 physical_stream_resource_id: str, source_id: str,
                 receiver_endpoint_id: str) -> None:
        if any(not isinstance(value, str) or not value for value in (
                physical_stream_resource_id, source_id, receiver_endpoint_id)):
            raise ValueError("pane owner requires exact resource, source and RX endpoint identities")
        self.physical_stream_resource_id = physical_stream_resource_id
        self._source_id = source_id
        self._endpoint_id = receiver_endpoint_id
        self._live = live

    def validate_endpoint(self, endpoint: ReceiverEndpoint) -> None:
        if (endpoint.endpoint_id != self._endpoint_id
                or endpoint.source_id != self._source_id
                or endpoint.physical_stream_resource_id != self.physical_stream_resource_id
                or endpoint.selection is not ReceiverChainSelection.RX1):
            raise ValueError("AD936x pane owner has only a proven RX1 producer")

    def validate_job(self, job: CaptureJob) -> None:
        """Pure fixed refusal before the session reserves a receiver lease."""
        profile = job.profile
        if (job.physical_stream_resource_id != self.physical_stream_resource_id
                or job.receiver_endpoint_ids != (self._endpoint_id,)
                or profile.measurement_mode is not CaptureMeasurementMode.RTBW
                or profile.unit != "dBFS/bin"
                or profile.gain_mode != "manual" or profile.manual_gain_db is None
                or profile.calibration_profile_id is not None
                or profile.window.strip().casefold().replace("_", "-").replace(" ", "-") not in _KNOWN_WINDOWS
                or profile.detector.strip().casefold().replace("_", "-") not in _KNOWN_DETECTORS
                or 1.0 - profile.hop_size / profile.fft_size >= 0.95):
            raise ValueError("AD936x pane owner supports only one uncalibrated manual-gain RTBW RX")

    def control_transaction(self) -> ContextManager[None]:
        return self._live.pane_control_transaction()

    def recording_active(self) -> bool:
        return self._live.pane_recording_conflict()

    def recording_conflict(self, job: CaptureJob) -> bool:
        self.validate_job(job)
        return self.recording_active()

    def receiver_identity(self, endpoint_id: str) -> None:
        if endpoint_id != self._endpoint_id:
            raise ValueError("foreign RX endpoint")
        # The current AD936x Live bundle has no proven producer RX ID.
        return None

    def start_capture(self, job: CaptureJob) -> PaneCaptureAdmission:
        self.validate_job(job)
        before = self._live.current_snapshot()
        analyzer_state = self._live.analyzer_state
        if (analyzer_state is None or analyzer_state.phase is not AnalyzerPhase.IDLE
                or self._live.is_running() or before.state is not LiveSessionState.CONNECTED
                or before.stop_required or before.device is None
                or before.device.device_id != self._source_id or before.applied is None):
            raise RuntimeError("Selected AD936x Live graph is not idle with the expected staged source")
        profile = job.profile
        gain_db = profile.manual_gain_db
        if gain_db is None:
            raise RuntimeError("AD936x pane requires an explicit manual gain")
        center_hz = (job.start_hz + job.stop_hz) / 2.0
        requested = replace(
            before.applied.applied,
            center_hz=center_hz,
            sample_rate_hz=profile.sample_rate_hz,
            analog_bandwidth_hz=profile.analog_bandwidth_hz,
            gain_db=gain_db,
            fft_size=profile.fft_size,
            overlap_ratio=1.0 - profile.hop_size / profile.fft_size,
            window=profile.window,
            detector=profile.detector,
        )
        staged = self._live.apply_configuration(requested)
        if staged.error is not None or staged.applied is None or staged.applied.applied != requested:
            raise RuntimeError("AD936x pane configuration was not staged exactly")
        started = self._live.start()
        applied = started.applied
        if (started.error is not None or started.state is not LiveSessionState.RUNNING
                or started.device is None or started.device.device_id != self._source_id
                or started.active_source_id != self._source_id
                or type(started.acquisition_epoch) is not int or started.acquisition_epoch < 0
                or applied is None or not _REQUIRED_READBACK <= set(applied.readback_fields)
                or not _applied_covers_job(requested, applied.applied, job)
                or started.unit != profile.unit):
            raise RuntimeError("AD936x pane Start lacks exact source, epoch or applied readback")
        return PaneCaptureAdmission(
            job.capture_id, self._source_id, CaptureMeasurementMode.RTBW,
            started.unit, (self._endpoint_id,), started.acquisition_epoch,
            session_id=str(started.session_id),
            config_generation=started.active_config_generation,
            sample_rate_hz=applied.applied.sample_rate_hz,
            fft_size=applied.applied.fft_size,
            hop_size=profile.hop_size,
        )

    def stop_capture_and_wait(self) -> None:
        stopped = self._live.stop()
        if (stopped.error is not None or stopped.stop_required or self._live.is_running()
                or self._live.analyzer_state is None
                or self._live.analyzer_state.phase is not AnalyzerPhase.IDLE):
            raise RuntimeError("AD936x pane Stop did not confirm application/native release")

    def poll_bundles(self) -> tuple[tuple[str, AnalyzerFrameBundle], ...]:
        snapshots = self._live.poll_published_snapshots()
        if len(snapshots) > 64:
            raise RuntimeError("AD936x pane publication batch exceeded its bound")
        delivered: list[tuple[str, AnalyzerFrameBundle]] = []
        for snapshot in snapshots:
            if snapshot.error is not None or snapshot.state is not LiveSessionState.RUNNING:
                raise RuntimeError("AD936x Live producer is no longer running")
            bundle = self._live.analyzer_bundle_for_snapshot(snapshot)
            if bundle is not None:
                delivered.append((self._endpoint_id, bundle))
        return tuple(delivered)


__all__ = ["Ad936xRtbwPaneOwner"]
