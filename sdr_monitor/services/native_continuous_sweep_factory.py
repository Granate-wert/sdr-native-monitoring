"""Explicit stopped-Live lease to R10-D native coordinator plan conversion."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager, nullcontext
import math
import threading
from typing import Any, Callable
from uuid import uuid4

from ..domain import BackendKind, LiveConfiguration
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.paired_sweep import PairedSweepRequest, PairedSweepRunIdentity
from ..domain.paired_sweep_publication import PairedSweepPublication
from ..domain.paired_sweep_archive import PairedSweepTerminalArchive, PairedSweepUnobservedTerminal
from .native_paired_sweep_receipts import observed_paired_publication
from ..domain.continuous_sweep_geometry import sweep_segment_count, sweep_step_geometry
from ..domain.sweep_speed import SweepSpeedProfile
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET
from ..domain.analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from .native_live import build_native_fixed_band_config, _SPECTRUM_QUEUE_CAPACITY
from .native_continuous_sweep import (
    ContinuousSweepDisplaySnapshot,
    NativeContinuousSweepDisplayService,
    _to_domain_line,
)
from .native_sweep import NativeSweepLease, NativeSweepSource
from .ad936x_identity_admission import create_identity_bound_owner
from ..domain.sweep_capacity import LEGACY_SWEEP_MAX_SEGMENTS
from .sweep_geometry_contract import require_extended_sweep_geometry
from .native_sweep_layer_journal import pluto_sweep_layer_capacity, sweep_layer_reserved_bytes, sweep_layer_receipt
from .native_layer_journal import NativeLayerJournal, sweep_layer_host_reservation
from .native_ready_bridge import NativeReadyBridge
from ..domain.layer_journal import LayerJournalSnapshot, SweepLayerScope


class NativeContinuousSweepPlanFactory:
    """Holds one explicit stopped-Live lease; building performs no SDR I/O."""

    def __init__(
        self,
        lease: NativeSweepLease,
        *,
        capability_evidence_sha256: str | None = None,
    ) -> None:
        self._lease = lease
        self._closed = False
        self._capability_evidence_sha256 = capability_evidence_sha256

    @classmethod
    def from_native_live(
        cls,
        live_service: Any,
        *,
        capability_evidence_sha256: str | None = None,
    ) -> "NativeContinuousSweepPlanFactory":
        return cls(
            live_service.acquire_native_sweep_lease(),
            capability_evidence_sha256=capability_evidence_sha256,
        )

    @classmethod
    def from_paired_application(cls, application: Any, request: PairedSweepRequest, *,
                                control_claim: object | None = None):
        """Actual selected application/native authority, not a standalone lease."""
        lease = (application.acquire_paired_sweep_lease(request) if control_claim is None else
                 application.acquire_paired_sweep_lease(request, control_claim=control_claim))
        if (lease.paired_request is not request or lease.paired_configuration is None
                or lease.control_transaction is None or lease.construct_owner is None
                or lease.cleanup_transaction is None or lease.allocate_paired_run is None):
            lease.release()
            raise RuntimeError("paired Sweep requires admitted application/native authority")
        return cls(lease)

    def preflight(self, request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight:
        """Read the held applied profile; construct no native config or device."""
        self._require_open()
        self._lease.assert_active()
        if self._lease.validate_continuous_request is not None:
            self._lease.validate_continuous_request(request)
        result = self.preflight_native_profile(
            self._lease.native_module, self._lease.source.live_configuration, request)
        if self._lease.paired_request is None and pluto_sweep_layer_capacity(self._lease.native_module):
            reduced = replace(result.reduced, output_bytes=result.reduced.output_bytes
                              + sweep_layer_reserved_bytes(result.segment_count))
            if reduced.total_bytes > DEFAULT_LIVE_RESOURCE_BUDGET.max_spectrum_backlog_bytes:
                raise ValueError("Sweep layer retention exceeds existing reduced memory budget")
            result = replace(result, reduced=reduced)
        return result

    @staticmethod
    def preflight_native_profile(
        native: object, live: LiveConfiguration, request: ContinuousSweepPlanRequest,
    ) -> AnalyzerGeometryPreflight:
        geometry = NativeContinuousSweepPlanFactory.preflight_profile(live, request)
        if geometry.segment_count > LEGACY_SWEEP_MAX_SEGMENTS:
            require_extended_sweep_geometry(native)
        return geometry

    @staticmethod
    def preflight_profile(
        live: LiveConfiguration, request: ContinuousSweepPlanRequest,
    ) -> AnalyzerGeometryPreflight:
        """Pure draft calculation; requires no factory, lease or native module.

        Success does not authorize RX or prove this profile is still applied.
        build() always repeats the same calculation on the held lease profile.
        """
        # Revalidate scalar geometry here: frozen dataclasses can originate
        # outside its normal constructor, and preflight must refuse it before
        # native config construction.  Keep native's physical geometry; do not
        # compensate by silently selecting another FFT size.
        scalar_request_values = (
            ("start", request.start_hz),
            ("stop", request.stop_hz),
            ("usable window", request.usable_window_hz),
            ("overlap", request.overlap_hz),
        )
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for _name, value in scalar_request_values
        ):
            raise ValueError("continuous sweep request contains non-finite geometry")
        if (
            request.start_hz <= 0.0 or request.stop_hz <= request.start_hz
            or request.usable_window_hz <= 0.0 or request.overlap_hz < 0.0
            or request.overlap_hz >= request.usable_window_hz
            or type(request.output_queue_capacity) is not int
            or not 1 <= request.output_queue_capacity <= 64
            or type(request.analysis_bins_per_usable_window) is not int
            or request.analysis_bins_per_usable_window < 0
        ):
            raise ValueError("continuous sweep request has invalid bounded geometry")
        if request.analysis_bins_per_usable_window and (
            request.analysis_bins_per_usable_window < 256
            or request.analysis_bins_per_usable_window > 262_144
            or request.analysis_bins_per_usable_window
            & (request.analysis_bins_per_usable_window - 1)
        ):
            raise ValueError("continuous sweep analysis bins must be a power of two in [256, 262144]")
        sample_rate = live.sample_rate_hz
        fft_size = live.fft_size
        averaging_frames = SweepSpeedProfile(request.speed_profile).averaging_frames(live.averaging_frames)
        bandwidth = live.analog_bandwidth_hz if live.analog_bandwidth_hz is not None else sample_rate
        if (
            isinstance(sample_rate, bool) or not isinstance(sample_rate, (int, float))
            or not math.isfinite(sample_rate) or sample_rate <= 0.0
            or isinstance(bandwidth, bool) or not isinstance(bandwidth, (int, float))
            or not math.isfinite(bandwidth) or bandwidth <= 0.0
            or type(fft_size) is not int or not 256 <= fft_size <= 262_144
            or fft_size & (fft_size - 1)
        ):
            raise ValueError("applied profile has invalid physical Fs/FFT/RF-bandwidth geometry")
        admitted_window = min(float(sample_rate), float(bandwidth))
        if live.backend is not BackendKind.CPU:
            raise RuntimeError("continuous sweep requires an explicitly applied CPU Live profile")
        if request.usable_window_hz > admitted_window:
            raise ValueError("requested usable window exceeds the applied sample-rate/RF-bandwidth profile")
        stride = request.usable_window_hz - request.overlap_hz
        span = request.stop_hz - request.start_hz
        count = sweep_segment_count(request)
        spacing = (request.usable_window_hz / request.analysis_bins_per_usable_window
                   if request.analysis_bins_per_usable_window else sample_rate / fft_size)
        physical_spacing = sample_rate / fft_size
        if request.analysis_bins_per_usable_window and spacing + 1e-9 < physical_spacing:
            raise ValueError("continuous sweep analysis grid requires a denser physical FFT transform")
        ratio = span / spacing
        if not math.isfinite(ratio):
            raise ValueError("continuous sweep output geometry is not finite")
        bins = (math.ceil(ratio - 1e-12) if request.analysis_bins_per_usable_window
                else math.floor(ratio) + 1)
        if not 2 <= bins <= 2_000_000:
            raise ValueError("continuous sweep output grid exceeds native bounded bin limit")
        # Axis f64 + value f32 + quality u32 + segment i32. Include output
        # queue, constructing snapshot, progressive mailbox and UI-held frame.
        # Conservative for shared axes; not an estimate of total engine memory.
        reduced = estimate_analyzer_reduced(
            "sweep", bins, request.output_queue_capacity + 3,
            physical_fft_size=fft_size, segment_count=count,
        )
        if reduced.total_bytes > DEFAULT_LIVE_RESOURCE_BUDGET.max_spectrum_backlog_bytes:
            raise ValueError("continuous sweep reduced spectrum backlog exceeds memory budget")
        statistics_bytes = 0
        hop = max(1, int(round(fft_size * (1.0 - live.overlap_ratio))))
        if request.statistics is not None:
            # Same producer retention as native configure, plus downstream
            # owners included by payload_upper_bound. No config/device call.
            slots = request.output_queue_capacity * 2 + 4
            if count == 1:
                burst = request.acquisition_buffer_samples // hop + 1 + 2
                slots += 2 * max(burst, _SPECTRUM_QUEUE_CAPACITY, request.output_queue_capacity)
            statistics_bytes = request.statistics.payload_upper_bound(bins, slots)
        return AnalyzerGeometryPreflight(
            "sweep", sample_rate, fft_size, spacing, count, stride, reduced,
            usable_window_hz=request.usable_window_hz,
            analysis_bins_per_usable_window=request.analysis_bins_per_usable_window,
            physical_bin_spacing_hz=physical_spacing,
            statistics_payload_bytes=statistics_bytes,
            fft_averaging_frames=averaging_frames,
            minimum_samples_per_spectrum=fft_size + (averaging_frames - 1) * hop,
        )

    def build(self, request: ContinuousSweepPlanRequest) -> Any:
        if self._lease.paired_request is not None:
            raise RuntimeError("paired Sweep cannot use a single-producer plan")
        preflight = self.preflight(request)
        source = self._lease.source
        return self._build_native_plan(self._lease.native_module, source, request, preflight,
                                       source_id=f"continuous-sweep:{source.source_id}",
                                       layer_event_capacity=pluto_sweep_layer_capacity(self._lease.native_module))

    @staticmethod
    def _build_native_plan(native: Any, source: NativeSweepSource,
                           request: ContinuousSweepPlanRequest, preflight: AnalyzerGeometryPreflight,
                           *, source_id: str, receiver_selection: Any = None,
                           layer_event_capacity: int = 0) -> Any:
        if (type(layer_event_capacity) is not int or layer_event_capacity not in (0, 64)
                or (layer_event_capacity and pluto_sweep_layer_capacity(native) != layer_event_capacity)):
            raise ValueError("Sweep layer creation API/capacity was not admitted")
        live = source.live_configuration
        segments = []
        for index in range(preflight.segment_count):
            geometry = sweep_step_geometry(request, index)
            configuration = replace(live, center_hz=geometry.center_hz,
                                    averaging_frames=preflight.fft_averaging_frames)
            fixed = build_native_fixed_band_config(
                native,
                configuration,
                source.context_uri,
                source_id=source_id,
                receiver_selection=receiver_selection,
                device_buffer_samples=request.acquisition_buffer_samples,
                # This queue is private to the native coordinator. The
                # completed SweepLine output remains separately bounded and
                # the Qt render cadence is never configured here.
                snapshot_rate_hz=request.line_snapshot_rate_hz,
                allow_r10d5_evidence_buffer_geometry=request.allow_r10d5_evidence_buffer_geometry,
            )
            segments.append(
                native.ContinuousSweepSegmentConfig(
                    fixed, geometry.usable_start_hz, geometry.usable_stop_hz
                )
            )
        statistics_arguments: dict[str, Any] = {}
        if layer_event_capacity:
            statistics_arguments["layer_event_capacity"] = layer_event_capacity
        if request.statistics is not None:
            settings = request.statistics
            statistics_arguments.update({
                "statistics": native.SweepStatisticsConfig(
                    settings.window_passes, settings.power_bins, settings.power_min_db,
                    settings.power_max_db, settings.max_payload_bytes, settings.density_columns,
                ),
                "statistics_snapshot_rate_hz": settings.snapshot_rate_hz,
            })
        return native.ContinuousSweepCoordinatorConfig(
            request.epoch,
            request.start_hz,
            request.stop_hz,
            segments,
            output_queue_capacity=request.output_queue_capacity,
            segment_frame_timeout_ms=request.segment_frame_timeout_ms,
            usable_window_hz=request.usable_window_hz,
            analysis_bins_per_usable_window=request.analysis_bins_per_usable_window,
            line_snapshot_rate_hz=(
                request.line_snapshot_rate_hz
                if request.line_snapshot_rate_hz is not None
                else 0.0
            ),
            **statistics_arguments,
        )

    @staticmethod
    def build_paired_native_config(native: Any, source: NativeSweepSource,
                                   request: PairedSweepRequest) -> Any:
        """Pure combined native budget/geometry admission BEFORE context open."""
        for name in ("PLUTO_PAIRED_SWEEP_REDUCED_PROTOCOL_VERSION",
                     "PLUTO_PAIRED_SWEEP_STATISTICS_PROTOCOL_VERSION",
                     "PLUTO_PAIRED_SWEEP_PRODUCT_RESERVATION_PROTOCOL_VERSION",
                     "PLUTO_PAIRED_SWEEP_GAIN_RECEIPT_PROTOCOL_VERSION"):
            value = getattr(native, name, None)
            if type(value) is not int or value != 1:
                raise RuntimeError("paired Sweep native reduced/statistics/product reservation/gain receipt protocol v1 required")
        if request.pair.configuration != source.live_configuration:
            raise ValueError("paired Sweep profile differs from admitted Live profile")
        preflight = NativeContinuousSweepPlanFactory.preflight_native_profile(
            native, source.live_configuration, request.sweep)
        rx = native.PlutoReceiverSelection
        layer_capacity = pluto_sweep_layer_capacity(native)
        plans = [NativeContinuousSweepPlanFactory._build_native_plan(native, source, request.sweep,
                    preflight, source_id=producer, receiver_selection=selection,
                    layer_event_capacity=layer_capacity)
                 for producer, selection in ((request.pair.primary_source_id, rx.RX1),
                                             (request.pair.secondary_source_id, rx.RX2))]
        # Native validates both reduced/statistics/whole-owner budgets together.
        # No second full memory allowance; no RF in config constructors.
        # Native queued+drained+preview/current pair slots. 4KiB/step bounds
        # Python identities, BOTH observations/acquisitions/generation tuples,
        # wrappers and scalar object overhead; shared request/profile not copied.
        # Each terminal domain chain OWNS f64 frequency + f32 value + i32
        # source + u16 quality (18B/bin): BOTH copies need 36B/bin per slot.
        # Progress arrays are immutable native views already counted upstream;
        # reserve the larger terminal ownership even for progress slots. Another
        # 64B/bin bounds sequential conversion/validation scratch (quality cast,
        # chunk-local masks/np.isin and full-grid pair comparisons), not a second
        # retained batch. This deliberate upper bound is not measured RSS.
        # Conservative payload reservation, not RSS measurement: SAME native
        # Sweep sink component128MiB/whole512MiB preflight counts it once.
        slots = 2 * request.sweep.output_queue_capacity + 5
        reserved = preflight.segment_count * (slots + 1) * 4096
        reserved += preflight.reduced.output_bins * (36 * slots + 64) + (slots + 1) * 4096
        if layer_capacity:
            # BOTH host journals share the SAME aggregate component ceiling.
            # Native already counts both rings/drain storage; do not count the
            # single-plan native margin again or allocate two full budgets.
            reserved += 2 * sweep_layer_host_reservation(preflight.segment_count)
        if request.sweep.statistics is not None:
            # Density validation takes boolean/fancy-index copies and float
            # products/allclose scratch over CELLS, not measurement bins.
            # Native immutable statistics views stay budgeted upstream. Reserve
            # 64B/cell for EACH chain conservatively even though validation is
            # sequential; neither a cache nor native retained slots pays for it.
            cells = min(preflight.reduced.output_bins,
                        request.sweep.statistics.density_columns) * request.sweep.statistics.power_bins
            reserved += 2 * 64 * cells
        return native.PairedContinuousSweepCoordinatorConfig(
            request.resource_id, *plans, product_publication_reserved_bytes=reserved)

    def build_paired(self) -> Any:
        with self.control_transaction():
            if self._lease.paired_request is None or self._lease.paired_configuration is None:
                raise RuntimeError("factory does not hold a paired Sweep plan")
            return self._lease.paired_configuration

    def close(self) -> None:
        if not self._closed:
            self._lease.release()
            self._closed = True

    def create_display_service(self) -> NativeContinuousSweepDisplayService:
        if self._lease.paired_request is not None:
            raise RuntimeError("paired Sweep requires the paired reduced display bridge")
        return self._construct_owner(
            lambda: NativeContinuousSweepDisplayService(
                self._lease.native_module, self._lease.source.context_uri,
                expected_serial=self._lease.source.expected_serial,
                expected_usb_connection=self._lease.source.expected_usb_connection,
            ),
            lambda owner: owner.close(),
        )

    def create_coordinator(self, *, timeout_ms: int = 3000) -> Any:
        """Create one native-only coordinator while this explicit lease lives.

        This is for non-UI evidence runners. It exposes the same bounded
        completed-line native boundary as the display service and deliberately
        does not create a Qt timer, a renderer or a raw-I/Q bridge.
        """

        self._require_open()
        if timeout_ms <= 0:
            raise ValueError("continuous sweep coordinator timeout must be positive")
        paired_owner: list[_AdmittedPairedSweepCoordinator] = []

        def cleanup(owner: Any) -> None:
            if paired_owner:
                paired_owner[0]._close_registered_owner(owner)
            else:
                owner.disconnect()

        owner = self._construct_owner(
            lambda: create_identity_bound_owner(
                self._lease.native_module, "NativeContinuousSweepCoordinator",
                self._lease.source.context_uri, timeout_ms,
                expected_serial=self._lease.source.expected_serial,
                expected_usb_connection=self._lease.source.expected_usb_connection,
            ),
            cleanup,
        )
        if self._lease.paired_request is not None:
            wrapper = _AdmittedPairedSweepCoordinator(self, owner, self._lease.paired_configuration)
            paired_owner.append(wrapper)
            return wrapper
        return owner

    @contextmanager
    def control_transaction(self):
        self._require_open()  # A closed application factory must not reacquire a claim.
        control = self._lease.control_transaction
        with control() if control is not None else nullcontext():
            self._require_open()
            self._lease.assert_active()
            yield

    def _construct_owner(self, construct: Callable[[], Any], cleanup: Callable[[Any], None]) -> Any:
        with self.control_transaction():
            owned_construct = self._lease.construct_owner
            if owned_construct is not None:
                return owned_construct(construct, cleanup)
            return construct()  # Older explicit adapter; not SAME Live admission.

    def evidence_build_info(self) -> dict[str, str]:
        """Return a small, route-free native-build identity for evidence."""

        self._require_open()
        build_info = getattr(self._lease.native_module, "build_info", None)
        if not callable(build_info):
            raise RuntimeError("native continuous sweep module does not expose build_info")
        raw = build_info()
        if not isinstance(raw, dict):
            raise RuntimeError("native continuous sweep build_info has an invalid shape")
        accepted = (
            "version",
            "platform",
            "architecture",
            "compiler",
            "build_type",
            "pluto_compiled",
            "cuda_compiled",
        )
        result = {
            f"native_{key}": str(raw[key])
            for key in accepted
            if key in raw and str(raw[key]).strip()
        }
        if "native_version" not in result:
            raise RuntimeError("native continuous sweep build_info omits version")
        return {"native_module": "sdr_monitor._sdr_native"} | result

    def capability_evidence_sha256(self) -> str:
        """Return the physical capability binding required by evidence runners."""

        self._require_open()
        digest = self._capability_evidence_sha256 or ""
        if len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise RuntimeError(
                "continuous sweep evidence factory lacks a capability snapshot digest"
            )
        return digest

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeError("continuous sweep plan factory is closed")


class _AdmittedPairedSweepCoordinator:
    """Bound internal owner, NOT unrestricted native configuration authority.

    Raw native coordinator remains registered with NativeLive for cleanup.
    Reduced polling is bounded; no IQ/callback/native configure escape hatch.
    Application run identity and checked per-step reduced publications are
    owner-issued here; actual pane/CaptureJob delivery remains separate.
    """

    def __init__(self, factory: NativeContinuousSweepPlanFactory, owner: Any, configuration: Any):
        self._factory, self._owner, self._configuration = factory, owner, configuration
        self._operation_lock = threading.RLock()
        self._run: PairedSweepRunIdentity | None = None
        self._retired_run: PairedSweepRunIdentity | None = None
        self._retired_joined = False
        self._observed_sweep_epoch: int | None = None
        self._last_sweep_epoch: int | None = None
        self._ready_bridge = NativeReadyBridge(factory._lease.native_module)
        self._layer_journals = tuple(NativeLayerJournal(factory._lease.native_module, density=False)
                                     for _ in range(2))
        self._layer_capacity = 0

    def configure(self, configuration: Any) -> None:
        raise RuntimeError("paired Sweep owner cannot configure a single-producer plan")

    def configure_paired(self, configuration: Any) -> None:
        with self._factory.control_transaction(), self._operation_lock:
            if configuration is not self._configuration:
                raise RuntimeError("paired Sweep owner requires its exact admitted configuration")
            if any(j.current().scope is not None and not j.current().native_stop_confirmed
                   for j in self._layer_journals):
                raise RuntimeError("paired Sweep previous layer owner requires confirmed Stop")
            self._owner.configure_paired(configuration)
            self._run = None
            self._retired_run = None
            self._retired_joined = False

    def start(self) -> PairedSweepRunIdentity:
        with self._factory.control_transaction(), self._operation_lock:
            if self._owner.state() != self._factory._lease.native_module.EngineState.CONFIGURED:
                raise RuntimeError("paired Sweep Start requires an explicitly configured stopped plan")
            allocate = self._factory._lease.allocate_paired_run
            if allocate is None:
                raise RuntimeError("paired Sweep Start lacks application run authority")
            self._run = None
            self._retired_run = None
            self._retired_joined = False
            run = allocate()  # Attempt consumed even if native Start partially fails.
            native = self._factory._lease.native_module
            capacities = tuple(getattr(plan, "layer_event_capacity", 0) for plan in
                               (self._configuration.primary, self._configuration.secondary))
            if (capacities[0] != capacities[1] or type(capacities[0]) is not int
                    or capacities[0] not in (0, 64) or
                    (capacities[0] and pluto_sweep_layer_capacity(native) != capacities[0])):
                raise ValueError("paired Sweep layer journals require admitted common capacity")
            capacity = capacities[0]
            count = len(self._configuration.primary.segments)
            journals = tuple(NativeLayerJournal(native, capacity, density=False, sweep_segments=count)
                             for _ in range(2))
            if capacity:
                owner_run = uuid4().hex
                floor = max(run.request.sweep.epoch, (self._last_sweep_epoch + 1
                            if self._last_sweep_epoch is not None else 0))
                for journal, producer, receiver in zip(journals,
                        (run.request.pair.primary_source_id, run.request.pair.secondary_source_id), ("RX1", "RX2")):
                    journal.begin(SweepLayerScope(self._ready_bridge.clock_scope_id,
                        self._ready_bridge.host_process_id, owner_run, producer, receiver,
                        run.request.pair.session_id, None, minimum_epoch=floor))
                self._ready_bridge.begin()
            self._layer_journals, self._layer_capacity = journals, capacity
            try:
                self._owner.start()
            except Exception:
                # No worker exists only for exact native idle. Unknown/partial
                # activation retains cleanup obligation and must not auto-rearm.
                if self._owner.state() in (native.EngineState.CREATED, native.EngineState.CONFIGURED):
                    self._finish_layers()
                raise
            self._observed_sweep_epoch = None
            self._run = run
            return run

    @contextmanager
    def _cleanup(self):
        self._factory._require_open()
        transaction = self._factory._lease.cleanup_transaction
        if transaction is None:
            raise RuntimeError("paired Sweep owner lacks cleanup authority")
        with transaction(), self._operation_lock:
            yield

    def request_stop(self) -> None:
        with self._cleanup():
            self._retire_run()  # Invalidate BEFORE native Stop, including failed cleanup.
            state = self._owner.state()
            native_state = self._factory._lease.native_module.EngineState
            if state not in (native_state.CREATED, native_state.CONFIGURED):
                self._owner.request_stop()

    def join(self) -> None:
        with self._cleanup():
            if self._owner.state() == self._factory._lease.native_module.EngineState.RUNNING:
                raise RuntimeError("paired Sweep join requires request_stop or terminal worker failure")
            self._retire_run()
            self._owner.join()
            self._retired_joined = True
            self._finish_layers()

    def stop(self) -> None:
        with self._cleanup():
            self._retire_run()
            state = self._owner.state()
            native_state = self._factory._lease.native_module.EngineState
            if state not in (native_state.CREATED, native_state.CONFIGURED):
                self._owner.stop()
            self._retired_joined = True
            self._finish_layers()

    def layer_journal_snapshots(self) -> tuple[LayerJournalSnapshot, ...]:
        """Cached only, including after factory release; no owner/clock I/O."""
        with self._operation_lock:
            return tuple(j.current() for j in self._layer_journals)

    def _drain_layers(self) -> None:
        if self._layer_capacity:
            rx = self._factory._lease.native_module.PlutoReceiverSelection
            for journal, receiver in zip(self._layer_journals, (rx.RX1, rx.RX2)):
                journal.drain(self._layer_reader(self._owner, receiver))
            self._ready_bridge.sample()

    @staticmethod
    def _layer_reader(owner: Any, receiver: Any) -> Callable[[int], object]:
        def read(maximum: int) -> object:
            return owner.drain_sweep_layer_ready_events(receiver, maximum)
        return read

    def _finish_layers(self, owner: Any = None) -> None:
        owner = self._owner if owner is None else owner
        if self._layer_capacity:
            self._ready_bridge.sample()
            rx = self._factory._lease.native_module.PlutoReceiverSelection
            for journal, receiver in zip(self._layer_journals, (rx.RX1, rx.RX2)):
                journal.finish(self._layer_reader(owner, receiver))

    def _close_registered_owner(self, owner: Any) -> None:
        # NativeLive invokes this under its release transaction. Do not acquire
        # a new claim/cleanup transaction here. Keep actual raw owner registered.
        with self._operation_lock:
            self._retire_run()
            state = owner.state()
            enum = self._factory._lease.native_module.EngineState
            if state not in (enum.CREATED, enum.CONFIGURED):
                owner.stop()
            self._retired_joined = True
            self._finish_layers(owner)
            owner.disconnect()

    def _layer_receipt(self, frame: Any, receiver: str, progress: bool):
        return sweep_layer_receipt(frame, self._layer_journals[0 if receiver == "RX1" else 1],
                                   self._ready_bridge, receiver, progress=progress)

    def _retire_run(self) -> None:
        if self._run is not None:
            self._retired_run = self._run
            self._retired_joined = False
        self._run = None

    def poll_retired_archive(self) -> PairedSweepTerminalArchive:
        """Drain retained output only after join, under SAME cleanup authority.

        No active run is restored. A zero-prefix gap is explicitly UNOBSERVED,
        not an identity-qualified measurement. Each drain is bounded by the
        existing output reservation and never starts or opens hardware.
        """
        with self._cleanup():
            run = self._retired_run
            if self._run is not None or run is None or not self._retired_joined:
                raise RuntimeError("paired Sweep archive requires a retired confirmed-joined run")
            native = self._factory._lease.native_module
            terminals: list[PairedSweepPublication | PairedSweepUnobservedTerminal] = []
            for value in self._owner.poll_paired_lines(run.request.sweep.output_queue_capacity):
                if type(value) is not native.PairedSweepLineFrame:
                    raise TypeError("retired Sweep requires immutable native terminal output")
                self._check_epoch(value.primary.epoch)
                if value.steps:
                    terminals.append(observed_paired_publication(value, run,
                        layer_receipt=self._layer_receipt if self._layer_capacity else None))
                else:
                    if value.resource_id != run.request.resource_id:
                        raise ValueError("unobserved terminal differs from retired resource")
                    terminals.append(PairedSweepUnobservedTerminal(run,
                        _to_domain_line(value.primary, receiver_id="RX1" if self._layer_capacity else None,
                            layer_ready=self._layer_receipt(value.primary, "RX1", False) if self._layer_capacity else None),
                        _to_domain_line(value.secondary, receiver_id="RX2" if self._layer_capacity else None,
                            layer_ready=self._layer_receipt(value.secondary, "RX2", False) if self._layer_capacity else None)))
            archive = PairedSweepTerminalArchive(run, tuple(terminals))
            if terminals:
                self._observed_sweep_epoch = self._last_sweep_epoch = terminals[0].primary.epoch
            return archive

    def disconnect(self) -> None:
        self._factory.close()  # Registered cleanup retires before releasing both claims.

    @property
    def active_run(self) -> PairedSweepRunIdentity | None:
        if self._factory._closed:
            return None
        with self._factory.control_transaction(), self._operation_lock:
            return self._run

    def _observe(self, value: Any, *, progress: bool = False) -> PairedSweepPublication:
        run = self._run
        if run is None:
            raise RuntimeError("paired Sweep publication has no active admitted run")
        native = self._factory._lease.native_module
        frame_type = native.PairedSweepProgressFrame if progress else native.PairedSweepLineFrame
        if type(value) is not frame_type:
            raise TypeError("paired Sweep requires the immutable native publication type")
        epoch = value.primary.epoch
        self._check_epoch(epoch)
        publication = observed_paired_publication(value, run, progress=progress,
            layer_receipt=self._layer_receipt if self._layer_capacity else None)
        self._observed_sweep_epoch = self._last_sweep_epoch = epoch
        return publication

    def _check_epoch(self, epoch: int) -> None:
        if (self._observed_sweep_epoch is not None and epoch != self._observed_sweep_epoch
                or self._observed_sweep_epoch is None and self._last_sweep_epoch is not None
                and epoch <= self._last_sweep_epoch):
            raise ValueError("paired Sweep native epoch is stale or changed within the active run")

    def poll_observed_lines(self, max_items: int | None = None) -> tuple[PairedSweepPublication, ...]:
        with self._factory.control_transaction(), self._operation_lock:
            if self._run is None:
                raise RuntimeError("paired Sweep has no active admitted run")
            capacity = self._run.request.sweep.output_queue_capacity
            max_items = capacity if max_items is None else max_items
            if type(max_items) is not int or not 1 <= max_items <= capacity:
                raise ValueError("observed paired Sweep batch exceeds its reserved output capacity")
            return tuple(self._observe(value) for value in self.poll_paired_lines(max_items))

    def poll_observed_progress(self) -> PairedSweepPublication | None:
        with self._factory.control_transaction(), self._operation_lock:
            if self._run is None:
                raise RuntimeError("paired Sweep has no active admitted run")
            value = self.poll_paired_progress()
            return self._observe(value, progress=True) if value is not None else None

    def poll_paired_lines(self, max_items: int = 8) -> list[Any]:
        if type(max_items) is not int or not 1 <= max_items <= 64:
            raise ValueError("paired Sweep polling batch must be in [1,64]")
        with self._factory.control_transaction(), self._operation_lock:
            values = self._owner.poll_paired_lines(max_items)
            self._drain_layers()
            return values

    def poll_paired_progress(self) -> Any:
        with self._factory.control_transaction(), self._operation_lock:
            value = self._owner.poll_paired_progress()
            self._drain_layers()
            return value

    def metrics(self) -> Any:
        self._factory._require_open()
        return self._owner.metrics()

    def state(self) -> Any:
        self._factory._require_open()
        return self._owner.state()

    def last_error(self) -> Any:
        self._factory._require_open()
        return self._owner.last_error()


class NativeLiveContinuousSweepDisplayService:
    """Deferred Live composition: acquire/release the device lease per Start.

    It implements the display service's small start/poll/stop/close surface,
    but no lease is acquired when the workspace is opened.
    """

    def __init__(self, live_service: Any) -> None:
        self._live_service = live_service
        self._factory: NativeContinuousSweepPlanFactory | None = None
        self._display: NativeContinuousSweepDisplayService | None = None
        self._final_snapshot: ContinuousSweepDisplaySnapshot | None = None
        self._terminal_poll_attempted = False
        self._terminal_error: Exception | None = None
        self._display_closed = False
        self._operation_lock = threading.Lock()

    @contextmanager
    def _operation(self):
        # Reject rather than queue another lifecycle command behind a slow
        # device operation. In particular Stop must not become a later Start.
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("continuous sweep lifecycle operation is pending")
        try:
            yield
        finally:
            self._operation_lock.release()

    def start(self, request: ContinuousSweepPlanRequest) -> None:
        with self._operation():
            self._start(request)

    def _start(self, request: ContinuousSweepPlanRequest) -> None:
        if self._display is not None or self._factory is not None:
            raise RuntimeError("continuous sweep is already running")
        factory = NativeContinuousSweepPlanFactory.from_native_live(self._live_service)
        self._factory = factory
        self._terminal_poll_attempted = False
        self._terminal_error = None
        self._display_closed = False
        self._final_snapshot = None
        display = None
        try:
            config = factory.build(request)
            display = factory.create_display_service()
            self._display = display
            with factory.control_transaction():
                display.start(config)
        except Exception:
            # No successfully started publication stream was exposed. Cleanup
            # retries must not poll a failed or already closing display.
            self._terminal_poll_attempted = True
            if display is not None:
                display.close()
                self._display_closed = True
            factory.close()
            self._display = None
            self._factory = None
            raise

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        with self._operation():
            return self._poll_latest()

    def _poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        if self._display is None:
            if self._final_snapshot is not None:
                snapshot = self._final_snapshot
                self._final_snapshot = None
                return snapshot
            raise RuntimeError("continuous sweep is not running")
        return self._display.poll_latest()

    def stop(self) -> None:
        with self._operation():
            self._stop()

    def _stop(self) -> None:
        display, factory = self._display, self._factory
        if display is not None and not self._display_closed:
            # One explicit attempt per Stop. Do not retry a failing Stop in a
            # finally/Close path or disconnect an uncertain running receiver.
            display.stop()
            if not self._terminal_poll_attempted:
                self._terminal_poll_attempted = True
                try:
                    self._final_snapshot = display.poll_latest()
                except Exception as error:
                    self._terminal_error = error
            display.close()
            self._display_closed = True
        # Keep the occupied handle throughout Stop and on cleanup failure.
        # A concurrent Start must not acquire another owner before this point.
        if factory is not None:
            factory.close()
        self._display = None
        self._factory = None
        if self._terminal_error is not None:
            terminal_error, self._terminal_error = self._terminal_error, None
            raise terminal_error

    def close(self) -> None:
        self.stop()


__all__ = [
    "ContinuousSweepPlanRequest",
    "NativeContinuousSweepPlanFactory",
    "NativeLiveContinuousSweepDisplayService",
]
