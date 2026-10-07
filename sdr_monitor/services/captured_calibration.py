"""Bounded display-analytics authority; raw history and strict pull stay separate."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import ContextManager

from ..domain.calibration import CalibrationApplicability, CalibrationProfile, CalibrationProfileError, CalibrationSignature
from ..domain.live import LiveSessionState, LiveSnapshot
from ..domain.receiver_topology import ReceiverEndpoint
from .calibration_service import (
    CalibratedLiveSpectrum, CalibratedSpectrumInput, CalibrationSelectionReceipt, CalibrationService,
)
from .live_calibration_signature import CalibrationFrontendContext, build_current_frame_calibration_signature
from .receiver_calibration import ReceiverCalibrationRegistry


def _context(snapshot: LiveSnapshot) -> tuple[object, ...]:
    frame = snapshot.spectrum
    # stop_required also denotes a healthy dispatched Start that must later
    # be joined. The owning observer validates that lifecycle distinction.
    if frame is None or snapshot.state is not LiveSessionState.RUNNING or snapshot.error is not None:
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
class CapturedCalibrationInput:
    """Admitted raw-only handle; no persistence/snapshot arrays are retained."""

    captured: CalibratedSpectrumInput
    ordinal: int
    control_revision: int
    context: tuple[object, ...]
    authority: object
    service: CalibrationService

    @property
    def derived_output_bytes(self) -> int:
        return self.captured.derived_output_bytes


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


@dataclass(frozen=True, slots=True)
class CalibrationCommandPreview:
    """Inert proposal tied to actual settings, binding and selection revision."""

    profile: CalibrationProfile | None
    signature: CalibrationSignature
    applicability: CalibrationApplicability | None
    control_revision: int
    context: tuple[object, ...]
    authority: object
    service: CalibrationService
    selection: CalibrationSelectionReceipt


class CapturedCalibrationLane:
    """One explicitly bound endpoint/frontend; replace/close on binding changes.

    Observe must be the owning application's atomic cached snapshot/revision
    boundary. It must not query hardware. Consumers charge retained arrays to
    their existing budget and schedule one active/one latest pending operation.
    This class does not schedule work. Selection changes require explicit
    preview/application under the owning application's control guard.
    """

    def __init__(self, observe: Callable[[], tuple[int, LiveSnapshot]],
                 registry: ReceiverCalibrationRegistry, endpoint: ReceiverEndpoint,
                 frontend: CalibrationFrontendContext, *,
                 selection_guard: Callable[[], ContextManager[None]] | None = None) -> None:
        if not isinstance(registry, ReceiverCalibrationRegistry):
            raise CalibrationProfileError("typed receiver registry required")
        if not isinstance(endpoint, ReceiverEndpoint) or not isinstance(frontend, CalibrationFrontendContext):
            raise CalibrationProfileError("typed endpoint/frontend binding required")
        self._observe = observe
        self._selection_guard = selection_guard
        self._registry = registry
        self._endpoint = endpoint
        self._frontend = frontend
        self._authority = object()
        self._lock = RLock()
        self._ordinal = 0
        self._delivered = 0
        self._closed = False

    def preview_selection(self, profile: CalibrationProfile | None) -> CalibrationCommandPreview:
        """Compare using actual current facts; never selects or changes settings."""
        if profile is not None and not isinstance(profile, CalibrationProfile):
            raise CalibrationProfileError("typed profile or explicit clear required")
        with self._lock:
            if self._closed:
                raise CalibrationProfileError("analytical binding is closed")
            revision, snapshot = self._observe()
            if snapshot.device is None or snapshot.spectrum is None:
                raise CalibrationProfileError("current admitted device/frame required")
            signature = build_current_frame_calibration_signature(
                snapshot, snapshot.spectrum, self._endpoint, self._frontend)
            context = _context(snapshot)
            service = self._registry.for_device(snapshot.device, self._endpoint)
            selection = service.selection_receipt()
            applicability = None if profile is None else service.applicability(profile, signature)
            return CalibrationCommandPreview(profile, signature, applicability, revision,
                                             context, self._authority, service, selection)

    def apply_selection(self, preview: CalibrationCommandPreview) -> CalibrationApplicability | None:
        """Explicit Select/Clear, no RF mutation or expert applicability bypass."""
        with self._lock:
            if (self._closed or not isinstance(preview, CalibrationCommandPreview)
                    or preview.authority is not self._authority or self._selection_guard is None):
                raise CalibrationProfileError("current owner-bound selection preview required")
            with self._selection_guard():
                revision, current = self._observe()
                if current.device is None or current.spectrum is None:
                    raise CalibrationProfileError("current admitted device/frame required")
                signature = build_current_frame_calibration_signature(
                    current, current.spectrum, self._endpoint, self._frontend)
                if (revision != preview.control_revision or _context(current) != preview.context
                        or signature != preview.signature):
                    raise CalibrationProfileError("acquisition changed since calibration preview")
                return self._registry.apply_selection(current.device, self._endpoint, preview.service,
                    preview.profile, signature, preview.selection)

    def capture(self, admit_source: Callable[[CapturedCalibrationInput], bool]) -> CapturedCalibrationInput:
        """Admit exact exposed backing before consumer custody/correction.

        Synchronous worker callback, outside application/RF and selection locks.
        It must be admission, not accounting only. No correction follows refusal.
        """
        if not callable(admit_source):
            raise CalibrationProfileError("explicit source admission callback required")
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
            captured = service.capture_spectrum_input(snapshot, self._endpoint, self._frontend)
            handle = CapturedCalibrationInput(captured, ordinal, revision, context, self._authority, service)
        # Snapshot is source-side transient custody; only raw-only handle is admitted.
        del snapshot
        if admit_source(handle) is not True:
            raise CalibrationProfileError("captured source memory admission refused")
        if not self.is_valid_input(handle):
            raise CalibrationProfileError("captured analytical input expired during admission")
        return handle

    def is_valid_input(self, handle: CapturedCalibrationInput) -> bool:
        with self._lock:
            if (self._closed or not isinstance(handle, CapturedCalibrationInput)
                    or handle.authority is not self._authority or handle.ordinal < self._delivered):
                return False
            try:
                revision, current = self._observe()
                if current.device is None or current.spectrum is None:
                    return False
                signature = build_current_frame_calibration_signature(
                    current, current.spectrum, self._endpoint, self._frontend)
                return (revision == handle.control_revision and _context(current) == handle.context
                        and signature == handle.captured.signature
                        and self._registry.is_current_service(current.device, self._endpoint, handle.service)
                        and handle.service.is_current_input(handle.captured))
            except (CalibrationProfileError, RuntimeError):
                return False

    def correct(self, handle: CapturedCalibrationInput) -> CapturedCalibratedPublication:
        """Caller reserves declared outputs first; this never recaptures raw."""
        if not self.is_valid_input(handle):
            raise CalibrationProfileError("captured analytical input expired before correction")
        analytical = handle.service.correct_captured_input(handle.captured)
        result = CapturedCalibratedPublication(analytical, handle.ordinal, handle.control_revision,
                                               handle.context, handle.authority, handle.service)
        if not self.is_valid(result):
            raise CalibrationProfileError("captured analytical context expired during preparation")
        return result

    def prepare(self) -> CapturedCalibratedPublication:
        """Legacy unbudgeted analytical convenience, NOT UI admission guarantee.

        Product UI must use capture(admit_sources), reserve, correct, commit.
        """
        return self.correct(self.capture(lambda _: True))

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
