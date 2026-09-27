"""One retained HackRF RTBW owner publishing the EXISTING common Live contract.

No raw IQ, second renderer, fake Pluto profile, alternate SDK loader or implicit
retry. SDK effects/polling run off Qt; current_snapshot is a cached scalar read.
The staged request and setter success are not hardware sample-rate readback.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from typing import Any, Protocol, cast

from ..domain.analyzer_sources import AnalyzerSourceSelection
from ..domain.device_capabilities import DeviceCapabilityInventory, DeviceFamily
from ..domain.hackrf_live import HackrfConfigurationPatch, HackrfLiveRequest
from ..domain.identity import ConfigurationGeneration, FrameSequence, SessionId
from ..domain.live import (
    BackendKind,
    LiveAdmissionRejected,
    LiveErrorKind,
    LivePerformance,
    LiveQuality,
    LiveSessionState,
    LiveSnapshot,
    LiveSpectrumFrame,
)
from .hackrf_activation_preflight import HackrfActivationPreflightService, HackrfRuntimeIdentityPort
from .hackrf_live_admission import admit_hackrf_live
from .hackrf_product_live import HackrfNativeFactoryPort, HackrfProductLiveCoordinator, HackrfProductLiveState
from .native_live import _native_frame_metadata, _native_spectrum_unit
from .native_spectrum_provenance import native_spectrum_provenance, validate_absolute_unit
from .source_capability_admission import admit_source_request


class AnalyzerExclusionPort(Protocol):
    def claim_external_analyzer_rx(self, owner: object) -> None: ...
    def release_external_analyzer_rx(self, owner: object) -> None: ...


class HackrfAnalyzerService:
    """Same-owner admission, bounded reduced-frame polling and explicit Stop."""

    def __init__(self, native: object, exclusion: AnalyzerExclusionPort,
                 inventory: Callable[[], DeviceCapabilityInventory],
                 preflight: HackrfActivationPreflightService,
                 coordinator: HackrfProductLiveCoordinator) -> None:
        self._native, self._exclusion, self._inventory = native, exclusion, inventory
        self._preflight, self._coordinator = preflight, coordinator
        self._lock = threading.RLock()
        self._commands = threading.Lock()
        self._selection: AnalyzerSourceSelection | None = None
        self._snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0), LiveSessionState.DISCONNECTED)
        self._token = object()
        self._claimed = False
        self._poller: threading.Thread | None = None
        self._cancel = threading.Event()
        self._generation = 0
        self._epoch = 0
        self._last_native_sequence = -1
        self._polled = self._coalesced = self._published = 0

    def bind_selection(self, selection: AnalyzerSourceSelection) -> None:
        """Worker-side low-rate reset; no stale draft/frame follows a new choice."""
        with self._commands, self._lock:
            if selection is self._selection:
                return
            if self._claimed or self._poller is not None:
                raise LiveAdmissionRejected("Release HackRF before changing source")
            self._selection = selection
            choice = selection.selected
            self._snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0),
                LiveSessionState.CONNECTED if choice and choice.family is DeviceFamily.HACKRF else LiveSessionState.DISCONNECTED,
                unit="unavailable", source_choice=choice if choice and choice.family is DeviceFamily.HACKRF else None,
                selection_revision=selection.revision if choice and choice.family is DeviceFamily.HACKRF else None,
                hackrf_detector_groups_available=bool(choice and choice.family is DeviceFamily.HACKRF
                    and self._detector_groups_available()))

    def _detector_groups_available(self) -> bool:
        version = getattr(self._native, "HACKRF_DSP_PROFILE_CONTRACT_VERSION", None)
        return type(version) is int and version == 1

    def current_snapshot(self) -> LiveSnapshot:
        with self._lock:
            return self._snapshot

    def is_running(self) -> bool:
        with self._lock:
            return self._snapshot.state is LiveSessionState.RUNNING

    def poll_frames(self) -> list[LiveSnapshot]:
        snapshot = self.current_snapshot()
        return [snapshot] if snapshot.spectrum is not None or snapshot.error is not None else []

    def _next_generation(self) -> int:
        if self._generation >= (1 << 63) - 1:
            raise LiveAdmissionRejected("HackRF configuration generation exhausted")
        self._generation += 1
        return self._generation

    def _admit(self, request: HackrfLiveRequest) -> None:
        if request.averaging_frames != 1 and not self._detector_groups_available():
            raise LiveAdmissionRejected("HackRF detector groups require native DSP profile protocol1")
        selection = self._selection
        if selection is None or selection.selected is None or selection.release_pending:
            raise LiveAdmissionRejected("HackRF selection is unavailable")
        choice = selection.selected
        inventory = self._inventory()  # Pure retained metadata; never discover/probe.
        if (inventory.binding_for_source(choice.device_id) is not choice.binding
                or inventory.runtime_for_adapter(choice.binding.adapter_id) is not choice.runtime):
            raise LiveAdmissionRejected("HackRF catalog selection changed")
        result = admit_source_request(inventory, choice.device_id, "rtbw", request)
        if not result.accepted:
            raise LiveAdmissionRejected(f"HackRF request refused: {result.reason.value if result.reason else 'unknown'}")

    def stage(self, patch: HackrfConfigurationPatch) -> LiveSnapshot:
        if not isinstance(patch, HackrfConfigurationPatch):
            raise TypeError("HackRF settings require an immutable patch")
        with self._commands:
            snapshot = self.current_snapshot()
            if self._claimed or self._poller is not None:
                raise LiveAdmissionRejected("Stop and release HackRF before staging settings")
            if (self._selection is None or patch.selection_revision != self._selection.revision
                    or patch.expected_generation != snapshot.generation):
                raise LiveAdmissionRejected("HackRF draft conflicts with current selection/profile")
            self._admit(patch.request)
            request = replace(patch.request, configuration_generation=self._next_generation())
            with self._lock:
                self._snapshot = replace(snapshot, generation=ConfigurationGeneration(request.configuration_generation),
                    hackrf_request=request, unit="dBFS/bin", error=None, error_kind=None,
                    state=LiveSessionState.CONNECTED, spectrum=None, performance=LivePerformance(),
                    active_source_id=None, active_config_generation=None, acquisition_epoch=None, clock_domain=None)
                return self._snapshot

    def _error(self, message: str) -> LiveSnapshot:
        with self._lock:
            self._snapshot = replace(self._snapshot, state=LiveSessionState.ERROR,
                error=message, error_kind=LiveErrorKind.INTERNAL, stop_required=self._claimed)
            return self._snapshot

    def start(self) -> LiveSnapshot:
        with self._commands:
            snapshot = self.current_snapshot()
            if self._claimed or self._poller is not None:
                raise LiveAdmissionRejected("HackRF owner requires explicit Stop")
            request = snapshot.hackrf_request
            if request is None:
                raise LiveAdmissionRejected("Stage HackRF settings before Start")
            self._admit(request)
            selection = self._selection
            assert selection is not None and selection.selected is not None
            choice = selection.selected
            assert choice.binding.snapshot is not None and choice.binding.calibration_identity is not None
            request = replace(request, configuration_generation=self._next_generation())
            admission = admit_hackrf_live(choice.binding.snapshot, choice.binding.calibration_identity, request)
            if admission.plan is None:
                raise LiveAdmissionRejected("HackRF plan was not admitted")
            if self._epoch >= (1 << 64) - 1:
                raise LiveAdmissionRejected("HackRF acquisition epoch exhausted")
            self._exclusion.claim_external_analyzer_rx(self._token)
            self._claimed = True  # Retained on ANY subsequent failure until explicit Stop.
            self._epoch += 1
            with self._lock:
                self._snapshot = replace(snapshot, generation=ConfigurationGeneration(request.configuration_generation),
                    hackrf_request=request, spectrum=None, performance=LivePerformance(),
                    state=LiveSessionState.STARTING, stop_required=True, error=None, error_kind=None,
                    session_id=SessionId(f"hackrf-analyzer-{self._epoch}"), active_source_id=request.source_id,
                    active_config_generation=request.configuration_generation, acquisition_epoch=self._epoch,
                    clock_domain="host_steady_ns")
            try:
                verified = self._preflight.verify(admission.plan)
                if verified.permit is None:
                    return self._error("HackRF identity preflight refused; explicit Stop required")
                started = self._coordinator.start_after_confirmation(verified.permit, user_confirmed=True)
                if not started.started:
                    return self._error("HackRF native activation failed; explicit Stop required")
                self._cancel.clear()
                self._last_native_sequence = -1
                self._polled = self._coalesced = self._published = 0
                with self._lock:
                    self._snapshot = replace(self._snapshot, state=LiveSessionState.RUNNING)
                    self._poller = threading.Thread(target=self._poll, name="sdr-hackrf-analyzer", daemon=False)
                    self._poller.start()
                    return self._snapshot
            except Exception:  # noqa: BLE001 - retain partial owner and redact SDK details.
                return self._error("HackRF activation failed closed; explicit Stop required")

    def stop(self) -> LiveSnapshot:
        with self._commands:
            self._cancel.set()
            thread = self._poller
            if thread is not None and thread.ident is not None:
                thread.join(timeout=1.0)
            stopped = self._coordinator.snapshot().state is HackrfProductLiveState.IDLE
            if not stopped:
                stopped = self._coordinator.stop(5000).stopped
            try:
                self._preflight.close()  # Explicit same-observer cleanup only.
            except Exception:  # noqa: BLE001 - preserve the same pending identity observer.
                return self._error("HackRF identity release failed; explicit Stop required")
            if not stopped or (thread is not None and thread.is_alive()):
                return self._error("HackRF Stop/join not confirmed; explicit Stop required")
            if self._claimed:
                self._exclusion.release_external_analyzer_rx(self._token)
                self._claimed = False
            with self._lock:
                self._poller = None
                self._snapshot = replace(self._snapshot, state=LiveSessionState.CONNECTED,
                                         error=None, error_kind=None, stop_required=False)
                return self._snapshot

    def _convert(self, frame: Any, context: LiveSnapshot) -> LiveSpectrumFrame:
        request = context.hackrf_request
        assert request is not None
        # Do NOT use the native mapper's fallback identity for an untagged
        # foreign frame. This owner requires exact declared source/generation.
        if getattr(getattr(frame, "source", None), "source_id", None) != request.source_id:
            raise ValueError("missing/foreign HackRF frame source")
        source, generation, timestamp_quality, reasons = _native_frame_metadata(self._native, frame, "invalid")
        provenance = native_spectrum_provenance(frame)
        unit = _native_spectrum_unit(frame.unit)
        validate_absolute_unit(unit, provenance)
        return LiveSpectrumFrame(sequence=frame.frame_sequence, timestamp_ns=frame.timestamp_ns,
            center_frequency_hz=frame.center_frequency_hz, sample_rate_hz=frame.sample_rate_hz,
            fft_size=frame.fft_size, hop_size=frame.hop_size,
            frequencies_hz=frame.frequencies_hz, values=frame.values, unit=unit,
            source_id=source, config_generation=generation, timestamp_quality=timestamp_quality,
            loss_reasons=reasons, dropped_samples_before=frame.dropped_samples_before,
            dropped_iq_blocks_before=frame.dropped_iq_blocks_before,
            dropped_fft_frames_before=frame.dropped_fft_frames_before,
            native_quality_flags=int(frame.quality_flags), acquisition_epoch=context.acquisition_epoch,
            clock_domain=context.clock_domain, numerical_provenance=provenance)

    def _poll(self) -> None:
        last_frame = time.monotonic()
        last_metrics = last_frame
        previous: tuple[int, int, int, int] | None = None
        try:
            while not self._cancel.wait(0.001):
                frames = self._coordinator.poll_spectrum_frames(32)
                if frames:
                    context = self.current_snapshot()
                    spectrum = self._convert(frames[-1], context)
                    self._polled += len(frames)
                    self._coalesced += len(frames) - 1
                    if spectrum.sequence > self._last_native_sequence:
                        with self._lock:
                            if self._cancel.is_set():
                                return
                            candidate = replace(self._snapshot, spectrum=spectrum, unit=spectrum.unit,
                                sequence=FrameSequence(self._snapshot.sequence + 1),
                                quality=LiveQuality(backend=BackendKind.CPU, loss_reasons=spectrum.loss_reasons))
                            self._snapshot = candidate  # Domain validates exact profile/epoch/unit.
                        self._last_native_sequence = int(spectrum.sequence)
                        self._published += 1
                        last_frame = time.monotonic()
                now = time.monotonic()
                request = self.current_snapshot().hackrf_request
                assert request is not None
                if now - last_frame > request.spectrum_stall_timeout_s:
                    self._error("HackRF reduced spectrum stalled; explicit Stop required")
                    return
                if now - last_metrics >= 0.25:
                    metrics: Any = self._coordinator.metrics()
                    if metrics is None or int(metrics.processing.worker_failures) > 0:
                        self._error("HackRF worker metrics failed; explicit Stop required")
                        return
                    ingress, dsp = metrics.processing.ingress, metrics.processing.dsp
                    computed = int(dsp.dsp.fft_frames_computed)
                    counts = (int(ingress.samples_admitted), int(ingress.blocks_admitted), computed,
                              int(dsp.presentation.pushed))
                    interval = now - last_metrics
                    rates = tuple((counts[i] - previous[i]) / interval for i in range(4)) if previous else (0.0, 0.0, 0.0, 0.0)
                    performance = LivePerformance(fft_frames_computed=computed,
                        fft_frames_dropped=int(dsp.dsp.fft_frames_dropped),
                        snapshots_superseded=int(dsp.presentation.dropped),
                        snapshots_emitted=int(dsp.presentation.pushed),
                        spectrum_queue_depth=int(dsp.presentation.depth),
                        acquisition_queue_samples_dropped=int(ingress.dropped_samples),
                        acquisition_queue_blocks_dropped=int(ingress.loss_events),
                        acquisition_queue_depth=int(ingress.ready_depth),
                        bridge_native_frames_polled=self._polled, bridge_frames_coalesced=self._coalesced,
                        bridge_frames_published=self._published, iq_samples_dropped=int(ingress.dropped_samples),
                        iq_blocks_received=counts[1], iq_block_rate_hz=rates[1] if previous else None,
                        iq_sample_rate_hz=rates[0], analytical_fft_rate_hz=rates[2],
                        # Native publication BEFORE queue supersession/bridge/Qt.
                        # This is not unique display paints, FPS or RF duty.
                        spectrum_snapshot_rate_hz=rates[3],
                        source_sequence_discontinuities=int(dsp.source_sequence_discontinuities),
                        source_sample_index_discontinuities=int(dsp.source_sample_index_discontinuities),
                        source_timestamp_regressions=int(dsp.source_timestamp_regressions),
                        source_estimated_timestamp_blocks=int(dsp.source_estimated_timestamp_blocks),
                        rate_observation_interval_s=interval if previous else None)
                    with self._lock:
                        if not self._cancel.is_set():
                            self._snapshot = replace(self._snapshot, performance=performance)
                    previous, last_metrics = counts, now
        except Exception:  # noqa: BLE001 - quarantine malformed native publications, never retry RX.
            if not self._cancel.is_set():
                self._error("HackRF frame/metrics contract failed closed; explicit Stop required")


def build_hackrf_analyzer_service(native_live: Any, catalog: Any) -> HackrfAnalyzerService | None:
    """Same qualified loaded module/DLL folder as catalog; no SDK/device access."""
    from .hackrf_native_factory import HackrfNativeRuntimeFactory
    from .libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort
    from .source_capability_providers import _qualified_hackrf_sdk_directory

    native = native_live._native
    directory = _qualified_hackrf_sdk_directory(native)
    if directory is None:
        return None
    preflight = HackrfActivationPreflightService(lambda: cast(HackrfRuntimeIdentityPort,
        LibhackrfRuntimeIdentityPort(directory / "hackrf.dll", directory)))
    return HackrfAnalyzerService(native, native_live, catalog.snapshot, preflight,
        HackrfProductLiveCoordinator(cast(HackrfNativeFactoryPort, HackrfNativeRuntimeFactory(lambda: native))))


__all__ = ["HackrfAnalyzerService", "build_hackrf_analyzer_service"]
