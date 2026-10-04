"""One selected RTL RTBW owner on the common Analyzer Live graph.

Only coarse native control and reduced Spectrum frames cross Python. The
unbundled SDK remains absent unless an exact external runtime is provisioned.
No recording, Sweep, bias control, raw IQ or absolute power is implemented.
"""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any, Callable, Protocol
from uuid import uuid4

import numpy as np

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import (
    AdapterRuntimeSnapshot, DeviceCapabilityBinding, DeviceCapabilityInventory, DeviceFamily,
)
from ..domain.identity import ConfigurationGeneration, FrameSequence, LossReason, SessionId, TimestampQuality
from ..domain.live import (
    BackendKind, LiveAdmissionRejected, LiveErrorKind, LivePerformance, LiveQuality, LiveSessionState,
    LiveSnapshot, LiveSpectrumFrame,
)
from ..domain.rtl_live import RtlConfigurationPatch, RtlLiveRequest
from .native_live import _native_quality_mask, _native_spectrum_unit
from .native_spectrum_provenance import native_spectrum_provenance, validate_absolute_unit
from .native_ready_bridge import NativeReadyBridge
from ..domain.analytical_journal import OwnerJournalScope, OwnerJournalSnapshot
from .native_owner_journal import NativeOwnerJournal, owner_journal_capacity
from .rtl_capability_provider import RtlControlPort, RtlRuntimeProvision
from .source_capability_admission import admit_source_request


class RtlExclusionPort(Protocol):
    def claim_external_analyzer_rx(self, owner: object) -> None: ...
    def release_external_analyzer_rx(self, owner: object) -> None: ...


class RtlAnalyzerService:
    def __init__(self, native: object, exclusion: RtlExclusionPort,
                 inventory: Callable[[], DeviceCapabilityInventory],
                 provision_for: Callable[[DeviceCapabilityBinding, AdapterRuntimeSnapshot],
                                         RtlRuntimeProvision]) -> None:
        self._native, self._exclusion = native, exclusion
        self._ready_bridge = NativeReadyBridge(native)
        self._journal = NativeOwnerJournal(native)
        self._inventory, self._provision_for = inventory, provision_for
        self._lock = threading.RLock()
        self._commands = threading.Lock()
        self._selection: AnalyzerSourceSelection | None = None
        self._snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0), LiveSessionState.DISCONNECTED)
        self._token = object()
        self._claimed = False
        self._control: RtlControlPort | None = None
        self._poller: threading.Thread | None = None
        self._cancel = threading.Event()
        self._generation = 0
        self._last_sequence = -1
        self._bridge_polled = 0
        self._bridge_coalesced = 0
        self._bridge_published = 0
        self._first_fault: tuple[str, Exception] | None = None

    def first_fault_diagnostic(self) -> tuple[str, Exception] | None:
        """Private first cause for diagnostics; never an operator-facing SDK path."""
        with self._lock:
            return self._first_fault

    def _retain_fault(self, phase: str, error: Exception) -> None:
        with self._lock:
            if self._first_fault is None:
                self._first_fault = (phase, error)

    def bind_selection(self, selection: AnalyzerSourceSelection) -> None:
        with self._commands, self._lock:
            if selection is self._selection:
                return
            if self._claimed or self._control is not None:
                raise LiveAdmissionRejected("Release RTL before changing source")
            self._selection = selection
            choice = selection.selected
            rtl = choice is not None and choice.family is DeviceFamily.RTL_SDR
            self._snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0),
                LiveSessionState.CONNECTED if rtl else LiveSessionState.DISCONNECTED,
                source_choice=choice if rtl else None,
                selection_revision=selection.revision if rtl else None,
                unit="unavailable")

    def current_snapshot(self) -> LiveSnapshot:
        with self._lock:
            return self._snapshot

    def analytical_journal_snapshot(self) -> OwnerJournalSnapshot:
        return self._journal.current()

    def analytical_journal_terminal_history(self) -> tuple[OwnerJournalSnapshot, ...]:
        return self._journal.terminal_history()

    def native_control_available(self) -> bool:
        quarantined = getattr(self._native, "rtl_process_is_quarantined", None)
        version = getattr(self._native, "RTLSDR_RX_CONTROL_CONTRACT_VERSION", None)
        return bool(getattr(self._native, "RTLSDR_OFFICIAL_COMPILED", None) is True
                    and type(version) is int and version == 1
                    and callable(quarantined) and quarantined() is False)

    def is_running(self) -> bool:
        with self._lock:
            return self._snapshot.state is LiveSessionState.RUNNING

    def poll_frames(self) -> list[LiveSnapshot]:
        snapshot = self.current_snapshot()
        return [snapshot] if snapshot.spectrum is not None or snapshot.error is not None else []

    def _admit(self, request: RtlLiveRequest) -> RtlRuntimeProvision:
        control_version = getattr(self._native, "RTLSDR_RX_CONTROL_CONTRACT_VERSION", None)
        if (getattr(self._native, "RTLSDR_OFFICIAL_COMPILED", None) is not True
                or type(control_version) is not int or control_version != 1):
            raise LiveAdmissionRejected("RTL native control protocol is unavailable")
        selection = self._selection
        if selection is None or selection.selected is None or selection.release_pending:
            raise LiveAdmissionRejected("RTL source is not selected")
        choice = selection.selected
        inventory = self._inventory()
        if (choice.family is not DeviceFamily.RTL_SDR
                or inventory.binding_for_source(choice.device_id) is not choice.binding
                or inventory.runtime_for_adapter(choice.binding.adapter_id) is not choice.runtime):
            raise LiveAdmissionRejected("RTL selected session changed")
        admitted = admit_source_request(inventory, choice.device_id, "rtbw", request)
        if not admitted.accepted:
            raise LiveAdmissionRejected(f"RTL RTBW refused: {admitted.reason.value if admitted.reason else 'unknown'}")
        assert choice.runtime is not None
        provision = self._provision_for(choice.binding, choice.runtime)
        if request.manual_tuner_gain_tenth_db is not None:
            version = getattr(provision.native, "RTLSDR_TUNER_GAIN_CONTRACT_VERSION", None)
            if (provision.manual_gain_contract_version != 1 or type(version) is not int or version != 1):
                raise LiveAdmissionRejected("RTL manual gain native bridge is unavailable")
        return provision

    def preflight(self, request: RtlLiveRequest) -> None:
        if not isinstance(request, RtlLiveRequest):
            raise LiveAdmissionRejected("RTL requires a typed RTBW request")
        request.__post_init__()
        with self._lock:
            self._admit(request)

    def stage(self, patch: RtlConfigurationPatch) -> LiveSnapshot:
        if not isinstance(patch, RtlConfigurationPatch):
            raise TypeError("RTL Stage requires a typed patch")
        with self._commands:
            current = self.current_snapshot()
            if self._claimed or self._control is not None:
                raise LiveAdmissionRejected("Stop RTL before staging settings")
            if (self._selection is None or patch.selection_revision != self._selection.revision
                    or patch.expected_generation != current.generation):
                raise LiveAdmissionRejected("RTL Stage revision/generation is stale")
            self._admit(patch.request)
            self._generation += 1
            request = replace(patch.request, configuration_generation=self._generation)
            with self._lock:
                self._snapshot = replace(current, generation=ConfigurationGeneration(self._generation),
                    rtl_request=request, state=LiveSessionState.CONNECTED, unit="dBFS/bin",
                    error=None, error_kind=None, spectrum=None, persistence=None,
                    performance=LivePerformance(), active_source_id=None,
                    active_config_generation=None, acquisition_epoch=None, clock_domain=None,
                    rtl_cached_tuner_gain_tenth_db=None)
                return self._snapshot

    def _error(self, message: str) -> LiveSnapshot:
        with self._lock:
            self._snapshot = replace(self._snapshot, state=LiveSessionState.ERROR,
                error=message, error_kind=LiveErrorKind.INTERNAL, stop_required=self._claimed)
            return self._snapshot

    def start(self) -> LiveSnapshot:
        with self._commands:
            current = self.current_snapshot()
            if self._claimed or self._control is not None:
                raise LiveAdmissionRejected("RTL owner needs explicit Stop")
            request = current.rtl_request
            if request is None:
                raise LiveAdmissionRejected("Stage RTL RTBW before Start")
            provision = self._admit(request)
            selection = self._selection
            assert selection is not None and selection.selected is not None
            route = selection.selected.binding.rtl_session_route
            assert route is not None
            self._generation += 1
            request = replace(request, configuration_generation=self._generation)
            # This claim refuses native recording, armed writer, another SDR,
            # or pending source cleanup before any RTL SDK open/configure.
            self._exclusion.claim_external_analyzer_rx(self._token)
            self._claimed = True
            with self._lock:
                self._snapshot = replace(current, generation=ConfigurationGeneration(self._generation),
                    rtl_request=request, state=LiveSessionState.STARTING, stop_required=True,
                    spectrum=None, persistence=None, performance=LivePerformance(),
                    rtl_cached_tuner_gain_tenth_db=None,
                    error=None, error_kind=None, active_source_id=request.source_id,
                    active_config_generation=request.configuration_generation)
            try:
                native = provision.native
                # SAME provision module as the actual native owner, not the
                # catalog/default module used for general capability gates.
                self._ready_bridge = NativeReadyBridge(native)
                self._ready_bridge.begin()
                self._journal.prepare(native=native, capacity=owner_journal_capacity(native))
                selected = native.RtlSessionRoute(route.manufacturer, route.product, route.serial,
                    route.tuner_type, route.observation_revision)
                gain_arguments: dict[str, object] = {}
                if owner_journal_capacity(native):
                    gain_arguments["analytical_event_capacity"] = owner_journal_capacity(native)
                if request.manual_tuner_gain_tenth_db is not None:
                    gain_arguments["manual_tuner_gain_tenth_db"] = request.manual_tuner_gain_tenth_db
                control = native.create_rtl_runtime_control(provision.runtime,
                    request.center_frequency_hz, request.sample_rate_hz,
                    request.fft_size, request.hop_size, request.slot_count,
                    request.ready_capacity, request.resolved_dsp_output_capacity,
                    request.presentation_capacity, request.configuration_generation,
                    str(request.source_id), native.DetectorType.PEAK if request.detector == "peak"
                    else native.DetectorType.SAMPLE, "", selected, **gain_arguments)
                self._control = control  # retain before any readback/poller effect
                readback = control.readback()
                if (type(readback.session_epoch) is not int or readback.session_epoch <= 0
                        or readback.actual_center_hz != request.center_frequency_hz
                        or readback.actual_sample_rate_hz != request.sample_rate_hz):
                    return self._error("RTL actual center/Fs readback differs; explicit Stop required")
                cached_gain = None
                if request.manual_tuner_gain_tenth_db is not None:
                    cached_gain = getattr(readback, "cached_tuner_gain_tenth_db", None)
                    if (getattr(readback, "tuner_gain_readback_known", None) is not True
                            or type(cached_gain) is not int or cached_gain != request.manual_tuner_gain_tenth_db):
                        return self._error("RTL SDK cached gain differs; explicit Stop required")
                self._last_sequence = -1
                self._bridge_polled = self._bridge_coalesced = self._bridge_published = 0
                self._first_fault = None
                self._cancel.clear()
                with self._lock:
                    self._snapshot = replace(self._snapshot, state=LiveSessionState.RUNNING,
                        acquisition_epoch=readback.session_epoch, clock_domain="host_steady_ns",
                        rtl_cached_tuner_gain_tenth_db=cached_gain,
                        session_id=SessionId(f"rtl-session-{readback.session_epoch}"))
                # Preserve the SAME provision module identity, never the default
                # catalog loader. Old terminal windows are retained on this consumer.
                self._journal.begin(OwnerJournalScope(self._ready_bridge.clock_scope_id,
                    self._ready_bridge.host_process_id, uuid4().hex, str(request.source_id), None,
                    str(self._snapshot.session_id), request.configuration_generation, readback.session_epoch),
                    capacity=owner_journal_capacity(native))
                self._poller = threading.Thread(target=self._poll, name="sdr-rtl-analyzer", daemon=False)
                self._poller.start()
                return self.current_snapshot()
            except Exception as error:  # noqa: BLE001 - exact native owner remains retained if returned.
                self._retain_fault("native_start", error)
                return self._error("RTL activation failed closed; explicit Stop required")

    def stop(self) -> LiveSnapshot:
        with self._commands:
            self._cancel.set()
            thread = self._poller
            if thread is not None and thread.ident is not None:
                thread.join(timeout=1.0)
            if thread is not None and thread.is_alive():
                return self._error("RTL publication worker did not join; owner retained")
            control = self._control
            if control is not None:
                try:
                    result = control.stop(5000)
                    if result.complete() is not True or control.cleanup_required() is True:
                        return self._error("RTL native Stop/close unconfirmed; owner retained")
                    self._journal.finish(lambda count: getattr(control, "drain_analytical_ready_events")(count))
                except Exception as error:  # noqa: BLE001 - never release an ambiguous owner.
                    self._retain_fault("native_stop", error)
                    return self._error("RTL native Stop failed; owner retained")
            if self._claimed or control is not None:
                # A factory may throw BEFORE returning control after an
                # ambiguous close. No returned handle is not a cleanup receipt.
                try:
                    quarantined = getattr(self._native, "rtl_process_is_quarantined", None)
                    if not callable(quarantined) or quarantined() is not False:
                        return self._error("RTL native cleanup quarantined/unconfirmed; owner retained")
                except Exception as error:  # noqa: BLE001 - unknown process state cannot release RX.
                    self._retain_fault("native_cleanup_status", error)
                    return self._error("RTL native cleanup status failed; owner retained")
            if control is None and not self._journal.current().native_stop_confirmed:
                # Failed factory after prepare: confirmed process cleanup above
                # permits a new attempt, but supplies no counters/epoch proof.
                self._journal.finish(lambda _count: None)
            if self._claimed:
                self._exclusion.release_external_analyzer_rx(self._token)
                self._claimed = False
            self._control = None
            self._poller = None
            with self._lock:
                self._snapshot = replace(self._snapshot, state=LiveSessionState.CONNECTED,
                    error=None, error_kind=None, stop_required=False)
                return self._snapshot

    def _convert(self, frame: Any, current: LiveSnapshot) -> LiveSpectrumFrame:
        request = current.rtl_request
        assert request is not None
        source = getattr(frame, "source", None)
        if (source is None or getattr(source, "source_id", None) != request.source_id
                or frame.config_generation != request.configuration_generation):
            raise ValueError("RTL native source/generation mismatch")
        # The native Start receipt already checked exact tuner center/Fs and
        # established this epoch. A later reduced frame must still prove its
        # own geometry and CPU producer contract before Live assigns that epoch.
        if (getattr(source, "backend_id", None) != "native.librtlsdr.unbundled.cpu.v1"
                or current.acquisition_epoch is None or current.acquisition_epoch <= 0
                or current.clock_domain != "host_steady_ns"):
            raise ValueError("RTL reduced frame has no admitted native CPU producer")
        if (type(frame.center_frequency_hz) not in (int, float)
                or frame.center_frequency_hz != request.center_frequency_hz
                or type(frame.sample_rate_hz) not in (int, float)
                or frame.sample_rate_hz != request.sample_rate_hz
                or type(frame.fft_size) is not int or frame.fft_size != request.fft_size
                or type(frame.hop_size) is not int or frame.hop_size != request.hop_size):
            raise ValueError("RTL reduced frame geometry differs from native Start readback")
        frequencies, values = frame.frequencies_hz, frame.values
        if (not isinstance(frequencies, np.ndarray) or frequencies.ndim != 1
                or frequencies.shape != (request.fft_size,)
                or not isinstance(values, np.ndarray) or values.ndim != 1
                or values.shape != (request.fft_size,)):
            raise ValueError("RTL reduced arrays differ from the admitted FFT geometry")
        unit = _native_spectrum_unit(frame.unit)
        provenance = native_spectrum_provenance(frame)
        validate_absolute_unit(unit, provenance)
        if (unit != "dBFS/bin" or provenance.calibration_status != "uncalibrated"
                or provenance.window != request.window or provenance.detector != request.detector
                or provenance.precision_mode != "reference_f64" or provenance.averaging_frames != 1):
            raise ValueError("RTL reduced frame numerical contract differs from native CPU profile")
        reasons: list[LossReason] = []
        if (frame.dropped_iq_blocks_before or frame.dropped_samples_before
                or _native_quality_mask(self._native, frame, "IQ_DROPPED")):
            reasons.append(LossReason.ACQUISITION_QUEUE)
        if frame.dropped_fft_frames_before or _native_quality_mask(self._native, frame, "FFT_DROPPED"):
            reasons.append(LossReason.DSP)
        return LiveSpectrumFrame(sequence=frame.frame_sequence, timestamp_ns=frame.timestamp_ns,
            center_frequency_hz=frame.center_frequency_hz, sample_rate_hz=frame.sample_rate_hz,
            fft_size=frame.fft_size, hop_size=frame.hop_size,
            frequencies_hz=frame.frequencies_hz, values=frame.values, unit=unit,
            source_id=request.source_id,
            config_generation=ConfigurationGeneration(request.configuration_generation),
            timestamp_quality=TimestampQuality.ESTIMATED if _native_quality_mask(self._native, frame, "TIMESTAMP_ESTIMATED")
                else TimestampQuality.UNKNOWN,
            loss_reasons=tuple(reasons), dropped_samples_before=frame.dropped_samples_before,
            dropped_iq_blocks_before=frame.dropped_iq_blocks_before,
            dropped_fft_frames_before=frame.dropped_fft_frames_before,
            native_quality_flags=int(frame.quality_flags), acquisition_epoch=current.acquisition_epoch,
            clock_domain=current.clock_domain, numerical_provenance=provenance,
            detector_ready=self._ready_bridge.convert(frame, source_id=request.source_id,
                config_generation=ConfigurationGeneration(request.configuration_generation),
                receiver_id=None, acquisition_epoch=current.acquisition_epoch,
                session_id=current.session_id, owner_journal=self._journal.current()))

    def _poll(self) -> None:
        last_metrics = time.monotonic()
        previous: tuple[int, int, int] | None = None
        try:
            while not self._cancel.wait(0.02):
                control = self._control
                if control is None:
                    break
                result = control.drain_latest_spectrum_frame()
                self._ready_bridge.sample()
                self._journal.drain(lambda count: getattr(control, "drain_analytical_ready_events")(count))
                frame = result.frame
                if frame is not None:
                    current = self.current_snapshot()
                    converted = self._convert(frame, current)
                    if int(converted.sequence) <= self._last_sequence:
                        raise ValueError("RTL frame sequence did not advance")
                    self._last_sequence = int(converted.sequence)
                    self._bridge_polled += int(result.coalesced_frames) + 1
                    self._bridge_coalesced += int(result.coalesced_frames)
                    self._bridge_published += 1
                    with self._lock:
                        if self._snapshot.state is LiveSessionState.RUNNING:
                            self._snapshot = replace(self._snapshot, sequence=converted.sequence,
                                spectrum=converted,
                                quality=LiveQuality(backend=BackendKind.CPU,
                                                    loss_reasons=converted.loss_reasons))
                now = time.monotonic()
                if now - last_metrics >= 0.25:
                    metrics = control.metrics()
                    if metrics.reader_returned_without_stop or metrics.worker_failures:
                        self._error("RTL native worker stopped/faulted; explicit Stop required")
                        break
                    counts = (int(metrics.samples_admitted), int(metrics.blocks_admitted),
                              int(metrics.dsp.fft_frames_computed))
                    interval = now - last_metrics
                    rates = tuple((value - old) / interval for value, old in zip(counts, previous, strict=True)) \
                        if previous is not None else None
                    performance = LivePerformance(
                        fft_frames_computed=counts[2],
                        fft_frames_dropped=int(metrics.dsp.fft_frames_dropped),
                        iq_samples_dropped=int(metrics.host_input_samples_dropped),
                        iq_blocks_dropped=int(metrics.host_input_blocks_dropped),
                        acquisition_queue_samples_dropped=int(metrics.host_input_samples_dropped),
                        acquisition_queue_blocks_dropped=int(metrics.host_input_blocks_dropped),
                        acquisition_queue_depth=int(metrics.ready_depth),
                        spectrum_queue_depth=int(metrics.dsp.output_pending),
                        snapshots_superseded=int(metrics.presentation_frames_superseded),
                        bridge_native_frames_polled=self._bridge_polled,
                        bridge_frames_coalesced=self._bridge_coalesced,
                        bridge_frames_published=self._bridge_published,
                        iq_blocks_received=counts[1],
                        iq_block_rate_hz=rates[1] if rates is not None else None,
                        iq_sample_rate_hz=rates[0] if rates is not None else 0.0,
                        analytical_fft_rate_hz=rates[2] if rates is not None else 0.0,
                        rate_observation_interval_s=interval if rates is not None else None)
                    with self._lock:
                        if self._snapshot.state is LiveSessionState.RUNNING:
                            self._snapshot = replace(self._snapshot, performance=performance)
                    previous, last_metrics = counts, now
        except Exception as error:  # noqa: BLE001 - retain owner; Stop is the only release path.
            self._retain_fault("reduced_publication", error)
            self._error("RTL reduced publication failed; explicit Stop required")


__all__ = ["RtlAnalyzerService"]
