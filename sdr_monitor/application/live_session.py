"""Application boundary for one bounded Live SDR session.

The port deliberately carries immutable domain values only.  It is implemented
by infrastructure services such as the in-memory and native Pluto adapters,
while presenters depend on the use-case interface rather than service modules.
"""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from functools import wraps
from threading import RLock, local
from typing import Concatenate, Iterator, ParamSpec, Protocol, TypeVar
from collections.abc import Callable
from dataclasses import replace

from .analyzer_session import (
    AnalyzerSessionApplicationService, AnalyzerSessionState, AnalyzerMode,
    AnalyzerPhase, AnalyzerLiveRejected,
)
from .analyzer_sources import AnalyzerSourceSelectionApplicationService
from .analyzer_rtbw_router import AnalyzerRtbwRouter
from .analyzer_rf_change import (
    AnalyzerRfApplyReceipt, AnalyzerRfChangeRejected, AnalyzerRfContext, AnalyzerRfShiftProposal,
    compile_analyzer_rf_shift, source_inventory,
)
from ..domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from ..domain.rtl_live import RtlConfigurationPatch
from ..domain.paired_live import PairedLiveRequest, PairedLivePublication, PairedLivePerformance
from ..domain.paired_sweep import PairedSweepRequest
from ..domain.analyzer_sources import AnalyzerSourceChoice, AnalyzerSourceSelection

from ..domain import DeviceDescriptor, LiveConfiguration, LiveSnapshot, ConfigurationGeneration, FrameSequence
from ..domain.live_configuration_patch import LiveConfigurationPatch
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET, LiveSessionState, LiveErrorKind
from ..domain.analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.hackrf_sweep import HackrfSweepRequest
from ..domain.analyzer import AnalyzerFrameBundle, bundle_from_live
from ..domain.recording import RecordingState
from ..domain.tinysa_analyzer import TinySaSweepRequest
from ..domain.device_capabilities import AdapterRuntimeAvailability, DeviceFamily
from ..services.source_capability_admission import admit_source_request
from ..domain.analytical_journal import OwnerJournalSnapshot
from ..services.owner_journal_scope import cached_owner_journals
from ..domain.layer_journal import LayerJournalSnapshot
from ..services.pane_layer_admission import cached_layer_journals
from ..services.calibration_service import CalibrationService, CalibratedLiveSpectrum
from ..services.receiver_calibration import ReceiverCalibrationRegistry
from ..services.captured_calibration import CapturedCalibrationLane
from ..services.current_calibration_binding import CurrentCalibrationBinding, current_calibration_binding
from ..services.live_calibration_signature import (
    CalibrationFrontendContext, build_current_frame_calibration_signature,
)
from ..domain.calibration import CalibrationProfileError
from ..domain.receiver_topology import ReceiverEndpoint


_Args = ParamSpec("_Args")
_Result = TypeVar("_Result")


def _pane_exclusive_command(method: Callable[Concatenate[LiveSessionApplicationService, _Args], _Result]
                            ) -> Callable[Concatenate[LiveSessionApplicationService, _Args], _Result]:
    """Keep ordinary V2 controls outside an APP-07 pane-owned Live graph."""
    @wraps(method)
    def guarded(self: LiveSessionApplicationService, *args: _Args.args,
                **kwargs: _Args.kwargs) -> _Result:
        if not self._pane_application_lock.acquire(blocking=False):
            raise RuntimeError("Another receiver control operation is pending")
        try:
            claim = self._pane_control_claim
            if claim is not None and getattr(self._pane_control_thread, "claim", None) is not claim:
                raise RuntimeError("Receiver is reserved by a pane capture; use its explicit Stop")
            self._analytical_control_revision += 1
            return method(self, *args, **kwargs)
        finally:
            self._pane_application_lock.release()

    return guarded


class LiveSessionPort(Protocol):
    """Infrastructure operations required by the bounded Live use case."""

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]: ...
    def discover_startup_devices(self) -> tuple[DeviceDescriptor, ...]: ...
    def select_device(self, device_id: str) -> LiveSnapshot: ...
    def select_manual_uri(self, uri: str) -> LiveSnapshot: ...
    def apply_configuration(self, requested: LiveConfiguration) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def latest_snapshot(self) -> LiveSnapshot: ...
    def poll_frames(self) -> list[LiveSnapshot]: ...
    def is_running(self) -> bool: ...
    def stop_and_wait(self, timeout_s: float) -> None: ...


class LiveSessionUseCases(Protocol):
    """Application operations consumed by the Qt presenter."""

    def discover(self, *, startup: bool = False, local_only: bool = False) -> tuple[DeviceDescriptor | AnalyzerSourceChoice, ...]: ...
    def select_device(self, device_id: str) -> LiveSnapshot: ...
    def select_manual_uri(self, uri: str) -> LiveSnapshot: ...
    def current_snapshot(self) -> LiveSnapshot: ...
    def analyzer_bundle_for_snapshot(self, snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None: ...
    def preflight_configuration(self, configuration: LiveConfiguration) -> AnalyzerGeometryPreflight: ...
    def preflight_sweep(self, configuration: LiveConfiguration, request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight: ...
    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> LiveSnapshot: ...
    def reconfigure(self, configuration: LiveConfiguration, *, restart: bool = True) -> LiveSnapshot: ...
    def start_with_configuration(self, configuration: LiveConfiguration) -> LiveSnapshot: ...
    def start(self) -> LiveSnapshot: ...
    def stop(self) -> LiveSnapshot: ...
    def poll_published_snapshots(self) -> list[LiveSnapshot]: ...
    def is_running(self) -> bool: ...
    def shutdown(self, timeout_s: float = 5.0) -> None: ...


class LiveSessionApplicationService:
    """Coordinates one Live session without Qt, native bindings or widgets."""

    def __init__(self, port: LiveSessionPort, *, sweep_preflight: Callable[
        [LiveConfiguration, ContinuousSweepPlanRequest], AnalyzerGeometryPreflight,
    ] | None = None, analyzer: AnalyzerSessionApplicationService | None = None,
                 catalog_close: Callable[[], None] | None = None,
                 sources: AnalyzerSourceSelectionApplicationService | None = None,
                 rtbw: AnalyzerRtbwRouter | None = None,
                 family_sweep_preflight: Callable[[TinySaSweepRequest | HackrfSweepRequest], None] | None = None) -> None:
        self._port = port
        self._sweep_preflight = sweep_preflight
        self._analyzer = analyzer
        self._catalog_close = catalog_close
        self._sources = sources
        self._rtbw = rtbw
        self._family_sweep_preflight = family_sweep_preflight
        self._empty_source_snapshot: tuple[AnalyzerSourceSelection, LiveSnapshot] | None = None
        self._control_error: tuple[str, LiveErrorKind | None] | None = None
        self._pane_application_lock = RLock()
        self._pane_control_thread = local()
        self._pane_control_claim: object | None = None
        self._paired_sweep_acquisition_epoch = 0
        self._rf_receipt: AnalyzerRfApplyReceipt | None = None
        self._rf_receipt_acknowledged = False
        self._rf_receipt_failed = False
        self._analytical_control_revision = 0
        self._calibration_binding_authority = object()

    @property
    def analyzer_state(self) -> AnalyzerSessionState | None:
        return self._analyzer.state if self._analyzer is not None else None

    def _configuration_admission(self, *, idle_only: bool = False) -> None:
        if self._analyzer is None:
            return
        state = self._analyzer.state
        if state.phase is AnalyzerPhase.IDLE and not self._analyzer.control_busy:
            return
        if not idle_only and state.mode is AnalyzerMode.RTBW and state.phase is AnalyzerPhase.RUNNING:
            return
        raise RuntimeError("Stop the active analyzer operation before changing configuration")

    def _lifecycle_snapshot(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        if self._analyzer is None:
            return snapshot
        required = (self._analyzer.stop_required or self._rf_receipt_failed
                    or bool(self._sources and self._sources.current().release_pending))
        if self._analyzer.state.phase is AnalyzerPhase.ERROR and self._control_error is not None:
            message, kind = self._control_error
            snapshot = replace(snapshot, state=LiveSessionState.ERROR, error=message, error_kind=kind)
        return replace(snapshot, stop_required=required) if snapshot.stop_required != required else snapshot

    def _failed_lifecycle_snapshot(self, error: Exception) -> LiveSnapshot:
        snapshot = error.snapshot if isinstance(error, AnalyzerLiveRejected) else replace(
            self.current_snapshot(), state=LiveSessionState.ERROR,
            error=str(error), error_kind=LiveErrorKind.INTERNAL,
        )
        if snapshot.error is None:
            snapshot = replace(snapshot, error=str(error), error_kind=LiveErrorKind.INTERNAL)
        self._control_error = (snapshot.error or str(error), snapshot.error_kind)
        return self._lifecycle_snapshot(snapshot)

    def current_source_selection(self) -> AnalyzerSourceSelection | None:
        """Immutable low-rate control metadata; no SDK/catalog rebuild."""
        return self._sources.current() if self._sources is not None else None

    def analytical_journal_snapshots(self) -> tuple[OwnerJournalSnapshot, ...]:
        """Immutable cached scalar scope; no device discovery, poll or drain."""
        return (self._rtbw.analytical_journal_snapshots() if self._rtbw is not None
                else cached_owner_journals(self._port))

    def density_layer_journal_snapshots(self) -> tuple[LayerJournalSnapshot, ...]:
        """Immutable cached layers from the SAME RTBW owner; no native I/O."""
        return (self._rtbw.density_layer_journal_snapshots() if self._rtbw is not None
                else cached_layer_journals(self._port))

    def rtl_controls_available(self, source_id: str, selection_revision: int) -> bool:
        """No-I/O exact graph/selection readiness, never hardware or Start proof."""
        return bool(self._rtbw is not None
                    and self._rtbw.rtl_controls_available(source_id, selection_revision))

    def rtl_candidate_stage_available(self, source_id: str, selection_revision: int) -> bool:
        """No-I/O cached candidate for independent pane Stage, never Start."""
        return bool(self._rtbw is not None
                    and self._rtbw.rtl_candidate_stage_available(source_id, selection_revision))

    @_pane_exclusive_command
    def capture_rf_context(self) -> AnalyzerRfContext:
        """Cached full-profile identity for the default Analyzer, no SDK/IQ.

        The presenter must call this in its existing worker lane. A tentative
        editor request is not a successfully dispatched Sweep profile.
        """
        selection, state = self.current_source_selection(), self.analyzer_state
        if (selection is None or selection.selected is None or selection.release_pending
                or selection.refusal is not None or state is None or self._analyzer is None
                or self._analyzer.control_busy):
            raise AnalyzerRfChangeRejected("RF change requires an available shared source/operation")
        source = selection.selected
        if state.mode is AnalyzerMode.SWEEP:
            request = self._analyzer.accepted_sweep_request
            if request is None:
                raise AnalyzerRfChangeRejected("RF change requires the actually accepted Sweep request")
            if isinstance(request, ContinuousSweepPlanRequest):
                snapshot = self._port.latest_snapshot()
                if (snapshot.applied is None or snapshot.device is None
                        or snapshot.device.device_id != source.device_id or snapshot.error is not None):
                    raise AnalyzerRfChangeRejected("RF change requires the same applied AD Sweep profile")
                return AnalyzerRfContext(source, selection.revision, state, request,
                    applied_live=snapshot.applied.applied, session_id=snapshot.session_id,
                    configuration_generation=int(snapshot.generation), acquisition_epoch=state.sweep_epoch,
                    route_rf_capabilities=snapshot.device.route_rf_capabilities)
            return AnalyzerRfContext(source, selection.revision, state, request, acquisition_epoch=state.sweep_epoch)
        snapshot = self.current_snapshot()
        if snapshot.error is not None:
            raise AnalyzerRfChangeRejected("RF change cannot reuse a failed receiver profile")
        if source.family is DeviceFamily.AD936X:
            if (snapshot.applied is None or snapshot.device is None
                    or snapshot.device.device_id != source.device_id):
                raise AnalyzerRfChangeRejected("RF change requires the same applied AD RTBW profile")
            profile: LiveConfiguration | HackrfLiveRequest = snapshot.applied.applied
        elif source.family is DeviceFamily.HACKRF:
            if (snapshot.hackrf_request is None or snapshot.source_choice is not source
                    or snapshot.selection_revision != selection.revision):
                raise AnalyzerRfChangeRejected("RF change requires the same full HackRF RTBW profile")
            profile = snapshot.hackrf_request
        else:
            raise AnalyzerRfChangeRejected("The selected instrument has no RTBW RF profile")
        return AnalyzerRfContext(source, selection.revision, state, profile,
            session_id=snapshot.session_id, configuration_generation=int(snapshot.generation),
            acquisition_epoch=snapshot.acquisition_epoch, receiver_id=snapshot.receiver_id,
            clock_domain=snapshot.clock_domain, unit=snapshot.unit,
            route_rf_capabilities=(snapshot.device.route_rf_capabilities
                                   if source.family is DeviceFamily.AD936X and snapshot.device is not None else None))

    def _preflight_rf_proposal(self, proposal: AnalyzerRfShiftProposal) -> None:
        if not isinstance(proposal, AnalyzerRfShiftProposal):
            raise AnalyzerRfChangeRejected("RF control requires a typed full-profile proposal")
        proposal.__post_init__()
        request, context = proposal.request, proposal.expected
        if isinstance(request, (LiveConfiguration, ContinuousSweepPlanRequest)):
            if context.route_rf_capabilities is not None:
                runtime = context.source.runtime
                gate = getattr(self._port, "preflight_rf_route", None)
                if (runtime is None or runtime.family is not DeviceFamily.AD936X
                        or runtime.adapter_id != context.source.binding.adapter_id
                        or runtime.availability is not AdapterRuntimeAvailability.AVAILABLE or not callable(gate)):
                    raise AnalyzerRfChangeRejected("RF route runtime admission is unavailable")
                gate(context.source.device_id, context.route_rf_capabilities, request, applied_live=context.applied_live)
            else:
                admitted = admit_source_request(source_inventory(context), context.source.device_id,
                    context.state.mode.value, request, applied_live=context.applied_live)
                if not admitted.accepted:
                    raise AnalyzerRfChangeRejected("RF range/profile is not admitted by the selected capability")
            if isinstance(request, LiveConfiguration):
                self.preflight_configuration(request)
            else:
                assert context.applied_live is not None
                self.preflight_sweep(context.applied_live, request)
        elif isinstance(request, HackrfLiveRequest):
            if self._rtbw is None:
                raise AnalyzerRfChangeRejected("Shared HackRF RTBW admission is not composed")
            self._rtbw.preflight_hackrf(request)
        elif self._family_sweep_preflight is not None:
            self._family_sweep_preflight(request)
        else:
            raise AnalyzerRfChangeRejected("Shared family Sweep admission is not composed")

    @_pane_exclusive_command
    def preview_rf_shift(self, shift_hz: float) -> AnalyzerRfShiftProposal:
        """Inert full-profile preview with the existing native/family preflight."""
        proposal = compile_analyzer_rf_shift(self.capture_rf_context(), shift_hz)
        self._preflight_rf_proposal(proposal)
        return proposal

    @contextmanager
    def rf_change_transaction(self, proposal: AnalyzerRfShiftProposal, *, stopped: bool = False) -> Iterator[None]:
        """Revalidate before a caller's control effect under SAME RX/IQ owner.

        This scope performs no Stop, Apply or Start itself. Sweep callers MUST
        still use the owned Sweep facade/terminal publication, not generic Stop
        followed by a new SDK owner. Unknown/active recording refuses RF control;
        ordinary Stop remains independent and available for cleanup.
        """
        if type(stopped) is not bool or not isinstance(proposal, AnalyzerRfShiftProposal):
            raise AnalyzerRfChangeRejected("RF control requires an explicit typed proposal/phase")
        with self.pane_control_transaction():
            current = self.capture_rf_context()
            if not proposal.expected.matches(current, stopped=stopped):
                raise AnalyzerRfChangeRejected("RF source, operation or full profile changed")
            self._preflight_rf_proposal(proposal)
            if self.pane_recording_conflict():
                raise AnalyzerRfChangeRejected("RF change conflicts with the native recording owner")
            yield

    def stop_rf_rtbw(self, proposal: AnalyzerRfShiftProposal, *, cancelled: Callable[[], bool]) -> LiveSnapshot:
        """Same guarded owner Stop; Sweep MUST use its terminal-owning facade."""
        with self.rf_change_transaction(proposal):
            if proposal.expected.state.mode is not AnalyzerMode.RTBW or cancelled():
                raise AnalyzerRfChangeRejected("RF Stop was cancelled or belongs to Sweep")
            snapshot = self.stop()
            if snapshot.error is not None or snapshot.stop_required or self.analyzer_state is None or (
                    self.analyzer_state.phase is not AnalyzerPhase.IDLE):
                raise RuntimeError(snapshot.error or "RF Stop was not confirmed")
            return snapshot

    def apply_rf_shift(self, proposal: AnalyzerRfShiftProposal, *, cancelled: Callable[[], bool]) -> AnalyzerRfApplyReceipt:
        """Stopped frequency-only Stage; host-only Sweep intent, NEVER Start.

Failure or missing GUI acknowledgement bars ANY Start until explicit Stop.
The exact full HackRF request is staged through its existing optimistic patch;
AD Sweep does not reapply unrelated RF/DSP fields or fabricate a Live owner.
"""
        with self.rf_change_transaction(proposal, stopped=True):
            if cancelled():
                raise AnalyzerRfChangeRejected("RF Apply was cancelled")
            self._rf_receipt = None
            self._rf_receipt_acknowledged = False
            self._rf_receipt_failed = True
            request = proposal.request
            if isinstance(request, LiveConfiguration):
                snapshot = self.apply_configuration(request)
            elif isinstance(request, HackrfLiveRequest):
                generation = proposal.expected.configuration_generation
                if generation is None:
                    raise AnalyzerRfChangeRejected("RF Apply requires the observed HackRF generation")
                snapshot = self.stage_hackrf_configuration(HackrfConfigurationPatch(
                    request, proposal.expected.selection_revision, generation))
            else:
                snapshot = self.current_snapshot()
            # The pending failure latch is application admission, not an SDK
            # snapshot/quality mutation. Do not interpret it as failed Stop.
            snapshot = replace(snapshot, stop_required=self._analyzer.stop_required if self._analyzer else True)
            receipt = AnalyzerRfApplyReceipt(proposal, self.capture_rf_context(), snapshot)
            self._rf_receipt = receipt
            return receipt

    @_pane_exclusive_command
    def acknowledge_rf_apply(self, receipt: AnalyzerRfApplyReceipt) -> None:
        """Called on the control worker AFTER Qt delivered every view receipt."""
        if (receipt is not self._rf_receipt or not isinstance(receipt, AnalyzerRfApplyReceipt)
                or not receipt.armed_context.matches(self.capture_rf_context())):
            raise AnalyzerRfChangeRejected("RF UI receipt is missing or has been superseded")
        receipt.__post_init__()
        self._rf_receipt_acknowledged = True
        self._rf_receipt_failed = False

    @contextmanager
    def rf_start_transaction(self, receipt: AnalyzerRfApplyReceipt, *, cancelled: Callable[[], bool]) -> Iterator[None]:
        """One-use actual Start admission; no stale/cancelled/recording restart."""
        with self.pane_control_transaction():
            if (not isinstance(receipt, AnalyzerRfApplyReceipt) or receipt is not self._rf_receipt
                    or not self._rf_receipt_acknowledged or self._rf_receipt_failed
                    or not receipt.armed_context.matches(self.capture_rf_context())):
                raise AnalyzerRfChangeRejected("RF Start requires the current acknowledged stopped receipt")
            receipt.__post_init__()
            self._preflight_rf_proposal(receipt.proposal)
            if self.pane_recording_conflict() or cancelled():
                raise AnalyzerRfChangeRejected("RF Start was cancelled or conflicts with recording")
            # Consume BEFORE dispatch, including a rejected/partially owned
            # Start. There is no product retry of a failed permit/SDK call.
            self._rf_receipt = None
            self._rf_receipt_acknowledged = False
            try:
                yield
                state = self.analyzer_state
                if state is None or state.phase is not AnalyzerPhase.RUNNING:
                    raise AnalyzerRfChangeRejected("RF Start did not confirm RUNNING")
            except Exception:
                self._rf_receipt_failed = True
                raise

    def start_rf_rtbw(self, receipt: AnalyzerRfApplyReceipt, *, cancelled: Callable[[], bool]) -> LiveSnapshot:
        if receipt.proposal.expected.state.mode is not AnalyzerMode.RTBW:
            raise AnalyzerRfChangeRejected("Sweep RF Start requires its existing facade")
        with self.rf_start_transaction(receipt, cancelled=cancelled):
            return self.start()

    def _rf_start_admission(self) -> None:
        if self._rf_receipt is not None or self._rf_receipt_failed:
            raise AnalyzerRfChangeRejected("A pending or failed RF receipt bars Start; acknowledge it or explicitly Stop")

    @contextmanager
    def pane_control_transaction(self, claim: object | None = None, *,
                                 require_existing_claim: bool = False) -> Iterator[None]:
        """Use this graph's native recorder/receiver exclusion for APP-07.

        Inert/non-native graphs do not silently provide a no-op transaction.
        The transaction is acquired by the caller around the entire staged
        configuration and Start or Stop, not around the FFT hot path.
        """
        transaction = getattr(self._port, "pane_capture_control_transaction", None)
        if not callable(transaction):
            raise RuntimeError("Pane capture requires the native recording/control owner")
        if not self._pane_application_lock.acquire(blocking=False):
            raise RuntimeError("Another receiver control operation is pending")
        try:
            # Existing lease operations MUST NOT recreate a reservation after
            # concurrent release. Test under this lock BEFORE entering native
            # control; a released closure's earlier bool check is not authority.
            if require_existing_claim and (claim is None or self._pane_control_claim is not claim):
                raise RuntimeError("Receiver reservation was released or belongs to another owner")
            with transaction():
                if claim is None:
                    if self._pane_control_claim is not None:
                        raise RuntimeError("Receiver is reserved by another pane capture")
                elif self._pane_control_claim is None:
                    self._pane_control_claim = claim
                elif self._pane_control_claim is not claim:
                    raise RuntimeError("Receiver is reserved by another pane capture")
                prior = getattr(self._pane_control_thread, "claim", None)
                self._pane_control_thread.claim = claim
                try:
                    self._analytical_control_revision += 1
                    yield
                finally:
                    self._pane_control_thread.claim = prior
        finally:
            self._pane_application_lock.release()

    def release_pane_control(self, claim: object) -> None:
        """Release only this owner after its explicit Stop confirmed release."""
        with self._pane_application_lock:
            if self._pane_control_claim is None:
                return
            if (self._pane_control_claim is not claim
                    or getattr(self._pane_control_thread, "claim", None) is not claim):
                raise RuntimeError("Foreign pane cannot release receiver control")
            self._pane_control_claim = None

    def pane_recording_conflict(self) -> bool:
        """Unknown native recorder state is a refusal, never assumed idle."""
        getter = getattr(self._port, "native_recording_health", None)
        if not callable(getter):
            raise RuntimeError("Pane capture requires native recording state")
        state = getattr(getter(), "state", None)
        if not isinstance(state, RecordingState):
            raise RuntimeError("Pane capture could not verify native recording state")
        return state not in (RecordingState.IDLE, RecordingState.COMPLETED)

    def acquire_paired_sweep_lease(self, request: PairedSweepRequest, *, control_claim: object | None = None):
        """Reserve actual selection AND the SAME native owner, without RF/Start.

        This backend seam does not lift the product pane Sweep refusal. Keep
        the app claim until native join/disconnect is confirmed, including a
        failed cleanup retry. Never accept a caller's revision as current truth.
        """
        from ..services.native_sweep import NativeSweepLease

        if not isinstance(request, PairedSweepRequest):
            raise TypeError("paired Sweep requires a typed request")
        borrowed = control_claim is not None
        claim = object() if control_claim is None else control_claim
        if borrowed and getattr(self._pane_control_thread, "claim", None) is not claim:
            raise RuntimeError("paired Sweep requires the owning pane control transaction")
        with self.pane_control_transaction(claim, require_existing_claim=borrowed):
            # Borrow only INSIDE this exact pane owner's existing transaction.
            # A token alone outside the owning thread is not acquisition authority.
            try:
                self._configuration_admission(idle_only=True)
                self._require_native_family()
                selected = self.current_source_selection()
                if (selected is None or selected.selected is None or selected.release_pending
                        or selected.refusal is not None or selected.selected_id != request.pair.device_id):
                    raise RuntimeError("paired Sweep requires an actual current source selection")
                request.validate_applied(self.current_snapshot(), selected.revision)
                if self.pane_recording_conflict():
                    raise RuntimeError("stop/unarm recording before paired Sweep")
                acquire = getattr(self._port, "acquire_native_sweep_lease", None)
                if not callable(acquire):
                    raise RuntimeError("paired Sweep requires the SAME native lease owner")
                lease = acquire(paired_request=request)
            except BaseException:
                if not borrowed:
                    self.release_pane_control(claim)
                raise

        released = False

        def assert_active():
            if released:
                raise RuntimeError("paired Sweep application lease was released")
            current = self.current_source_selection()
            if (current is None or current.selected is not selected.selected
                    or current.revision != selected.revision or current.release_pending
                    or current.refusal is not None):
                raise RuntimeError("paired Sweep application selection changed after admission")
            request.validate_applied(self.current_snapshot(), current.revision)
            lease.assert_active()

        @contextmanager
        def control_transaction():
            if released:
                raise RuntimeError("paired Sweep application lease was released")
            with self.pane_control_transaction(claim, require_existing_claim=True):
                assert_active()
                if lease.control_transaction is None:
                    raise RuntimeError("paired Sweep lacks native control authority")
                with lease.control_transaction():
                    yield

        def release():
            nonlocal released
            if released:
                return
            # Cleanup must remain possible after stale selection; do not require
            # Start's admission fence here. Native failure retains BOTH claims.
            with self.pane_control_transaction(claim, require_existing_claim=True):
                lease.release()
                if not borrowed:
                    self.release_pane_control(claim)
                released = True

        def construct_owner(construct, cleanup):
            with control_transaction():
                if lease.construct_owner is None:
                    raise RuntimeError("paired Sweep lacks native constructor authority")
                return lease.construct_owner(construct, cleanup)

        @contextmanager
        def cleanup_transaction():
            if released:
                raise RuntimeError("paired Sweep application lease was released")
            with self.pane_control_transaction(claim, require_existing_claim=True):
                if lease.cleanup_transaction is None:
                    raise RuntimeError("paired Sweep lacks native cleanup authority")
                with lease.cleanup_transaction():
                    yield

        def allocate_paired_run():
            from ..domain.paired_sweep import PairedSweepRunIdentity

            with control_transaction():
                if self._paired_sweep_acquisition_epoch >= (1 << 64) - 1:
                    raise RuntimeError("paired Sweep application acquisition epoch exhausted")
                self._paired_sweep_acquisition_epoch += 1
                return PairedSweepRunIdentity(request, self._paired_sweep_acquisition_epoch,
                                              self.current_snapshot())

        return NativeSweepLease(lease.native_module, lease.source, assert_active, release,
            lease.validate_continuous_request, control_transaction, construct_owner,
            lease.paired_request, lease.paired_configuration, cleanup_transaction, allocate_paired_run)

    def _require_native_family(self) -> None:
        if self._sources is not None:
            self._sources.require_ad936x_controls()

    @_pane_exclusive_command
    def discover(self, *, startup: bool = False, local_only: bool = False) -> tuple[DeviceDescriptor | AnalyzerSourceChoice, ...]:
        if type(local_only) is not bool or (startup and local_only):
            raise ValueError("explicit local discovery is distinct from startup discovery")
        if self._sources is not None:
            try:
                return (self._sources.discover(local_only=True) if local_only else
                        self._sources.discover(startup=startup))
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
        return self._port.discover_startup_devices() if startup or local_only else self._port.discover_devices()

    @_pane_exclusive_command
    def select_device(self, device_id: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._sources is not None:
            try:
                self._sources.select(device_id)
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
            return self.current_snapshot()
        return self._port.select_device(device_id)

    @_pane_exclusive_command
    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._sources is not None:
            try:
                return self._sources.select_manual_uri(uri)
            finally:
                if self._rtbw is not None:
                    self._rtbw.refresh_selection()
        return self._port.select_manual_uri(uri)

    def current_snapshot(self) -> LiveSnapshot:
        if self._rtbw is not None and (self._rtbw.hackrf_selected or self._rtbw.rtl_selected):
            return self._lifecycle_snapshot(self._rtbw.current_snapshot())
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            # This is an EMPTY AD936x publication, not a fabricated HackRF/
            # instrument LiveSnapshot. Typed family control state is separate.
            if self._empty_source_snapshot is None or self._empty_source_snapshot[0] is not selection:
                snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0),
                    LiveSessionState.ERROR if selection.refusal else LiveSessionState.DISCONNECTED,
                    unit="unavailable", error="Source observation failed closed" if selection.refusal else None,
                    error_kind=LiveErrorKind.CONNECTION_FAILED if selection.refusal else None,
                    stop_required=selection.release_pending)
                self._empty_source_snapshot = selection, snapshot
            return self._lifecycle_snapshot(self._empty_source_snapshot[1])
        self._empty_source_snapshot = None
        return self._lifecycle_snapshot(self._port.latest_snapshot())

    def _observe_analytical_context(self) -> tuple[int, LiveSnapshot]:
        """Cached SAME owner observation; never wait on a running control job."""
        if not self._pane_application_lock.acquire(blocking=False):
            raise CalibrationProfileError("receiver control is pending")
        try:
            if self._pane_control_claim is not None:
                raise CalibrationProfileError("pane-owned analytics requires its own graph authority")
            if self._analyzer is not None and self._analyzer.state.mode is not AnalyzerMode.RTBW:
                raise CalibrationProfileError("Sweep analytics requires its own publication authority")
            return self._analytical_control_revision, self.current_snapshot()
        finally:
            self._pane_application_lock.release()

    @contextmanager
    def _analytical_selection_transaction(self) -> Iterator[None]:
        """Short cached selection transaction; no SDK/RF work in this scope."""
        if not self._pane_application_lock.acquire(blocking=False):
            raise CalibrationProfileError("receiver control is pending")
        try:
            self._observe_analytical_context()
            yield
        finally:
            self._pane_application_lock.release()

    def current_calibration_binding(self, frontend: CalibrationFrontendContext) -> CurrentCalibrationBinding:
        """Array-free receipt from SAME cached ordinary RTBW owner; no RF operation."""
        revision, current = self._observe_analytical_context()
        return current_calibration_binding(current, frontend, revision, self._calibration_binding_authority)

    def captured_bound_calibration_lane(
        self, registry: ReceiverCalibrationRegistry, binding: CurrentCalibrationBinding,
    ) -> CapturedCalibrationLane:
        """Pin the explicit current binding; never silently rebind an old lane."""
        if (not isinstance(binding, CurrentCalibrationBinding)
                or binding.authority is not self._calibration_binding_authority):
            raise CalibrationProfileError("binding from this exact Live owner required")

        def observe() -> tuple[int, LiveSnapshot]:
            revision, current = self._observe_analytical_context()
            actual = current_calibration_binding(current, binding.frontend, revision,
                                                 self._calibration_binding_authority)
            if actual != binding:
                raise CalibrationProfileError("current receiver calibration binding changed")
            return revision, current

        observe()  # Refuse stale bindings before returning a consumer lane.
        return CapturedCalibrationLane(observe, registry, binding.endpoint, binding.frontend,
                                       selection_guard=self._analytical_selection_transaction)

    def captured_calibration_lane(
        self, registry: ReceiverCalibrationRegistry, endpoint: ReceiverEndpoint,
        frontend: CalibrationFrontendContext,
    ) -> CapturedCalibrationLane:
        """Inert explicit binding to this owner; no profile activation or RX.

        Single Live owner boundary only. Paired graph/Sweep authority and V2
        preparation/render integration remain separate required work.
        """
        return CapturedCalibrationLane(self._observe_analytical_context, registry, endpoint, frontend,
                                       selection_guard=self._analytical_selection_transaction)

    def calibrated_current_spectrum(
        self, calibration: CalibrationService, endpoint: ReceiverEndpoint,
        frontend: CalibrationFrontendContext,
    ) -> CalibratedLiveSpectrum:
        """Read the SAME Live owner before/after off-acquisition correction.

        Explicit analytical pull only; no hardware open, profile activation or
        native frame relabel. Call from the preparation worker, not acquisition.
        Rejection is point-in-time; consumers must discard after a later control
        or selection change. Each endpoint must receive its own selection service.
        """
        if not isinstance(calibration, CalibrationService):
            raise CalibrationProfileError("typed calibration service required")
        result = calibration.correct_current_spectrum(self.current_snapshot(), endpoint, frontend)
        current = self.current_snapshot()
        signature = build_current_frame_calibration_signature(current, result.raw, endpoint, frontend)
        if (current.session_id != result.session_id or signature != result.signature
                or not calibration.is_current_selection(result)):
            raise CalibrationProfileError("calibration owner or selected profile changed during preparation")
        return result

    def calibrated_receiver_spectrum(
        self, registry: ReceiverCalibrationRegistry, endpoint: ReceiverEndpoint,
        frontend: CalibrationFrontendContext,
    ) -> CalibratedLiveSpectrum:
        """Registry-bound pull; retired or rebound RX selection scopes refuse."""
        if not isinstance(registry, ReceiverCalibrationRegistry):
            raise CalibrationProfileError("typed receiver calibration registry required")
        initial = self.current_snapshot()
        if initial.device is None:
            raise CalibrationProfileError("current admitted device required")
        service = registry.for_device(initial.device, endpoint)
        result = self.calibrated_current_spectrum(service, endpoint, frontend)
        current = self.current_snapshot()
        signature = build_current_frame_calibration_signature(current, result.raw, endpoint, frontend)
        if (current.device is None or current.session_id != result.session_id or signature != result.signature
                or not registry.is_current_service(current.device, endpoint, service)
                or not service.is_current_selection(result)):
            raise CalibrationProfileError("receiver calibration scope changed during preparation")
        return result

    def current_analyzer_bundle(self) -> AnalyzerFrameBundle | None:
        """Validated shared reduced contract, never a UI-specific frame format."""
        return self.analyzer_bundle_for_snapshot(self.current_snapshot())

    def analyzer_bundle_for_snapshot(self, snapshot: LiveSnapshot) -> AnalyzerFrameBundle | None:
        """Validate the exact delivered snapshot, never sample another revision."""
        return bundle_from_live(snapshot)

    def preflight_configuration(self, configuration: LiveConfiguration) -> AnalyzerGeometryPreflight:
        """Pure geometry/resource admission, never applied hardware readback."""
        self._require_native_family()
        resources = DEFAULT_LIVE_RESOURCE_BUDGET.validate(configuration)
        retained = resources.spectrum_backlog_bytes // (configuration.fft_size * 16)
        reduced = estimate_analyzer_reduced("rtbw", configuration.fft_size, retained)
        return AnalyzerGeometryPreflight(
            "rtbw", configuration.sample_rate_hz, configuration.fft_size,
            configuration.sample_rate_hz / configuration.fft_size, 1, 0.0, reduced,
            usable_window_hz=configuration.sample_rate_hz,
            physical_bin_spacing_hz=configuration.sample_rate_hz / configuration.fft_size,
            fft_averaging_frames=configuration.averaging_frames,
            minimum_samples_per_spectrum=configuration.fft_size + (configuration.averaging_frames - 1) *
                max(1, int(round(configuration.fft_size * (1.0 - configuration.overlap_ratio)))),
        )

    def preflight_sweep(
        self, configuration: LiveConfiguration, request: ContinuousSweepPlanRequest,
    ) -> AnalyzerGeometryPreflight:
        self._require_native_family()
        if self._sweep_preflight is None:
            raise RuntimeError("Sweep preflight is unavailable in this composition")
        return self._sweep_preflight(configuration, request)

    @_pane_exclusive_command
    def apply_configuration(self, configuration: LiveConfiguration | LiveConfigurationPatch) -> LiveSnapshot:
        self._require_native_family()
        self._configuration_admission()
        # The presenter invokes this inside its single control worker, not
        # while the UI is assembling a draft. A conflict has no port mutation.
        if isinstance(configuration, LiveConfigurationPatch):
            configuration = configuration.resolve(self._port.latest_snapshot())
        self.preflight_configuration(configuration)
        return self._lifecycle_snapshot(self._port.apply_configuration(configuration))

    @_pane_exclusive_command
    def reconfigure(self, configuration: LiveConfiguration, *, restart: bool = True) -> LiveSnapshot:
        """Preserve the stop → apply → optional-resume session transaction."""

        # Reject an inadmissible draft before disturbing an existing stream.
        self._configuration_admission()
        self.preflight_configuration(configuration)
        was_running = self._port.is_running()
        if was_running:
            stopped = self.stop()
            if stopped.error is not None or self._port.is_running():
                return stopped
        snapshot = self.apply_configuration(configuration)
        if restart and was_running and snapshot.error is None:
            return self.start()
        return snapshot

    @_pane_exclusive_command
    def start_with_configuration(self, configuration: LiveConfiguration) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        snapshot = self.apply_configuration(configuration)
        return self.start() if snapshot.error is None else snapshot

    @_pane_exclusive_command
    def start(self) -> LiveSnapshot:
        self._rf_start_admission()
        if self._rtbw is None or not (self._rtbw.hackrf_selected or self._rtbw.rtl_selected):
            self._require_native_family()
        if self._analyzer is not None:
            self._analyzer.select_mode(AnalyzerMode.RTBW)
            self._control_error = None
            try:
                self._analyzer.start()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            return self.current_snapshot()
        return self._port.start()

    @_pane_exclusive_command
    def stage_paired_rtbw(self, request: PairedLiveRequest) -> LiveSnapshot:
        """Arm the SAME selected Live owner; explicit Start remains separate.

        This internal typed boundary does not enable the UI/pane selector.
        A pair cannot be consumed through the single-producer Analyzer port.
        """
        self._configuration_admission(idle_only=True)
        self._require_native_family()
        stage = getattr(self._port, "stage_paired_rtbw", None)
        if not callable(stage):
            raise RuntimeError("paired RTBW is unavailable in this composition")
        reservation = self._analyzer.idle_control_operation() if self._analyzer is not None else nullcontext()
        with reservation:
            request.validate_snapshot(self._port.latest_snapshot())
            return self._lifecycle_snapshot(stage(request))

    @_pane_exclusive_command
    def clear_paired_rtbw(self) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        self._require_native_family()
        clear = getattr(self._port, "clear_paired_rtbw", None)
        if not callable(clear):
            raise RuntimeError("paired RTBW is unavailable in this composition")
        reservation = self._analyzer.idle_control_operation() if self._analyzer is not None else nullcontext()
        with reservation:
            return self._lifecycle_snapshot(clear())

    def poll_paired_publications(self) -> tuple[PairedLivePublication, ...]:
        self._require_native_family()
        state = self.analyzer_state
        if state is not None and (state.mode is not AnalyzerMode.RTBW or state.phase is not AnalyzerPhase.RUNNING):
            return ()
        poll = getattr(self._port, "poll_paired_frames", None)
        if not callable(poll):
            raise RuntimeError("paired RTBW is unavailable in this composition")
        return poll()

    def paired_performance(self) -> PairedLivePerformance | None:
        self._require_native_family()
        getter = getattr(self._port, "paired_performance", None)
        if not callable(getter):
            raise RuntimeError("paired RTBW is unavailable in this composition")
        return getter()

    @_pane_exclusive_command
    def stage_hackrf_configuration(self, patch: HackrfConfigurationPatch) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._rtbw is None or self._analyzer is None:
            raise RuntimeError("Common HackRF RTBW is unavailable")
        with self._analyzer.idle_control_operation():
            return self._lifecycle_snapshot(self._rtbw.stage_hackrf(patch))

    @_pane_exclusive_command
    def stage_rtl_configuration(self, patch: RtlConfigurationPatch) -> LiveSnapshot:
        self._configuration_admission(idle_only=True)
        if self._rtbw is None or self._analyzer is None:
            raise RuntimeError("Common RTL RTBW is unavailable")
        with self._analyzer.idle_control_operation():
            return self._lifecycle_snapshot(self._rtbw.stage_rtl(patch))

    @_pane_exclusive_command
    def start_sweep(self, request: ContinuousSweepPlanRequest | TinySaSweepRequest | HackrfSweepRequest) -> AnalyzerSessionState:
        self._rf_start_admission()
        if self._analyzer is None:
            raise RuntimeError("Shared analyzer is unavailable in this composition")
        self._configuration_admission(idle_only=True)
        if isinstance(request, (TinySaSweepRequest, HackrfSweepRequest)):
            selection = self.current_source_selection()
            if (selection is None or selection.selected is not request.source
                    or selection.revision != request.selection_revision or selection.release_pending):
                raise RuntimeError("Sweep source selection changed")
        else:
            self._require_native_family()
            snapshot = self._port.latest_snapshot()
            if snapshot.applied is None:
                raise RuntimeError("Sweep requires an applied Live profile")
            self.preflight_sweep(snapshot.applied.applied, request)
        self._analyzer.select_mode(AnalyzerMode.SWEEP)
        self._control_error = None
        try:
            return self._analyzer.start(request)
        except Exception as error:
            self._control_error = (str(error), LiveErrorKind.INTERNAL)
            raise

    @_pane_exclusive_command
    def stop(self) -> LiveSnapshot:
        if self._analyzer is not None:
            before = self._analyzer.state
            try:
                self._analyzer.stop()
            except Exception as error:
                return self._failed_lifecycle_snapshot(error)
            if self._sources is not None:
                self._sources.release_pending()
            # An unsuccessful idle RF Stage may have returned an SDK error
            # without a dispatched Analyzer attempt. Explicit Stop still owns
            # cleanup of THIS selected RTBW port, never a foreign pane resource.
            state = self._analyzer.state
            if self._rf_receipt_failed and before.phase is AnalyzerPhase.IDLE and state.phase is AnalyzerPhase.IDLE:
                selection = self.current_source_selection()
                if selection is not None and selection.selected is not None and selection.selected.family in (
                        DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.RTL_SDR):
                    cleaned = self._rtbw.stop() if self._rtbw is not None else self._port.stop()
                    if cleaned.error is not None or cleaned.stop_required:
                        return self._lifecycle_snapshot(cleaned)
            snapshot = self.current_snapshot()
            if snapshot.error is None and state.phase is AnalyzerPhase.IDLE:
                self._rf_receipt = None
                self._rf_receipt_acknowledged = self._rf_receipt_failed = False
                snapshot = self._lifecycle_snapshot(snapshot)
            return snapshot
        return self._port.stop()

    def poll_published_snapshots(self) -> list[LiveSnapshot]:
        if self._rtbw is not None:
            return [self._lifecycle_snapshot(value) for value in self._rtbw.poll_frames()]
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            return []
        return [self._lifecycle_snapshot(snapshot) for snapshot in self._port.poll_frames()]

    def is_running(self) -> bool:
        if self._rtbw is not None:
            return self._rtbw.is_running()
        selection = self.current_source_selection()
        if selection is not None and not selection.ad936x_controls_available:
            return False
        return self._port.is_running()

    @_pane_exclusive_command
    def shutdown(self, timeout_s: float = 5.0) -> None:
        errors: list[Exception] = []
        try:
            if self._analyzer is not None:
                self._analyzer.stop()
        except Exception as error:  # noqa: BLE001 - independent owner cleanup must still run.
            errors.append(error)
        try:
            self._port.stop_and_wait(timeout_s)
        except Exception as error:  # noqa: BLE001 - preserve failure and attempt independent catalog release.
            errors.append(error)
        try:
            # Runs on the presenter's lifecycle worker, after RX cancellation.
            # Its shared control gate refuses unresolved stream ownership; it
            # never steals that owner merely to close a read-only provider.
            if self._catalog_close is not None:
                self._catalog_close()
        except Exception as error:  # noqa: BLE001 - preserve the same retained provider for explicit retry.
            errors.append(error)
        if errors:
            raise errors[0]  # Explicit subsequent shutdown can resume cleanup.


__all__ = ["LiveSessionApplicationService", "LiveSessionPort", "LiveSessionUseCases"]
