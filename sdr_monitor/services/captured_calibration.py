"""Bounded display-analytics authority; raw history and strict pull stay separate."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock

from ..domain.calibration import CalibrationProfileError
from ..domain.live import LiveSnapshot
from ..domain.receiver_topology import ReceiverEndpoint
from .calibration_service import CalibratedLiveSpectrum, CalibrationService
from .live_calibration_signature import CalibrationFrontendContext, build_current_frame_calibration_signature
from .receiver_calibration import ReceiverCalibrationRegistry


def _context(snapshot: LiveSnapshot) -> tuple[object, ...]:
    frame = snapshot.spectrum
    if frame is None or snapshot.stop_required:
        raise CalibrationProfileError("running analytical context required")
    return (snapshot.device, snapshot.applied, snapshot.session_id,
            snapshot.active_source_id, snapshot.receiver_id, snapshot.acquisition_epoch,
            snapshot.active_config_generation, snapshot.clock_domain, snapshot.unit,
            snapshot.quality.backend, snapshot.quality.backend_discontinuity,
            snapshot.performance.source_sequence_discontinuities,
            snapshot.performance.source_sample_index_discontinuities,
            snapshot.performance.source_timestamp_regressions,
            frame.accumulation_id, frame.dropped_samples_before, frame.dropped_iq_blocks_before,
            frame.numerical_provenance,
            frame.center_frequency_hz, frame.sample_rate_hz, frame.fft_size, frame.hop_size)


@dataclass(frozen=True, slots=True)
class CapturedCalibratedPublication:
    """Exact captured frame, not necessarily producer-latest at delivery.

    The retained analytical result contains immutable raw/values/uncertainty.
    No queue or additional frame cache is owned by this contract.
    """

    analytical: CalibratedLiveSpectrum
    ordinal: int
    control_revision: int
    context: tuple[object, ...]
    authority: object
    service: CalibrationService


class CapturedCalibrationLane:
    """One explicitly bound endpoint/frontend; replace/close on binding changes.

    Observe must be the owning application's atomic cached snapshot/revision
    boundary. It must not query hardware. Consumers charge retained arrays to
    their existing budget and schedule one active/one latest pending operation.
    This class neither schedules work nor changes profile selection.
    """

    def __init__(self, observe: Callable[[], tuple[int, LiveSnapshot]],
                 registry: ReceiverCalibrationRegistry, endpoint: ReceiverEndpoint,
                 frontend: CalibrationFrontendContext) -> None:
        if not isinstance(registry, ReceiverCalibrationRegistry):
            raise CalibrationProfileError("typed receiver registry required")
        if not isinstance(endpoint, ReceiverEndpoint) or not isinstance(frontend, CalibrationFrontendContext):
            raise CalibrationProfileError("typed endpoint/frontend binding required")
        self._observe = observe
        self._registry = registry
        self._endpoint = endpoint
        self._frontend = frontend
        self._authority = object()
        self._lock = RLock()
        self._ordinal = 0
        self._delivered = 0
        self._closed = False

    def prepare(self) -> CapturedCalibratedPublication:
        with self._lock:
            if self._closed:
                raise CalibrationProfileError("analytical binding is closed")
            revision, snapshot = self._observe()
            if snapshot.device is None or snapshot.spectrum is None:
                raise CalibrationProfileError("current admitted device/frame required")
            build_current_frame_calibration_signature(snapshot, snapshot.spectrum, self._endpoint, self._frontend)
            context = _context(snapshot)
            service = self._registry.for_device(snapshot.device, self._endpoint)
            self._ordinal += 1
            ordinal = self._ordinal
        # Exact immutable captured publication; no acquisition/control lock during math.
        analytical = service.correct_current_spectrum(snapshot, self._endpoint, self._frontend)
        result = CapturedCalibratedPublication(analytical, ordinal, revision, context, self._authority, service)
        if not self.is_valid(result):
            raise CalibrationProfileError("captured analytical context expired during preparation")
        return result

    def is_valid(self, result: CapturedCalibratedPublication) -> bool:
        """Same-context cadence advancement is allowed; controls/selection are not."""
        with self._lock:
            if (self._closed or not isinstance(result, CapturedCalibratedPublication)
                    or result.authority is not self._authority or result.ordinal < self._delivered):
                return False
            try:
                revision, current = self._observe()
                if current.device is None or current.spectrum is None:
                    return False
                signature = build_current_frame_calibration_signature(
                    current, current.spectrum, self._endpoint, self._frontend)
                return (revision == result.control_revision and _context(current) == result.context
                        and signature == result.analytical.signature
                        and self._registry.is_current_service(current.device, self._endpoint, result.service)
                        and result.service.is_current_selection(result.analytical))
            except (CalibrationProfileError, RuntimeError):
                return False

    def admit_delivery(self, result: CapturedCalibratedPublication) -> bool:
        """Reject delayed older completion after a successor reached this lane."""
        with self._lock:
            if not self.is_valid(result) or result.ordinal <= self._delivered:
                return False
            self._delivered = result.ordinal
            return True

    def close(self) -> None:
        with self._lock:
            self._closed = True

