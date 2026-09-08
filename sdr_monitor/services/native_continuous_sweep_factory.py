"""Explicit stopped-Live lease to R10-D native coordinator plan conversion."""

from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
import math
import threading
from typing import Any

from ..domain import BackendKind, LiveConfiguration
from ..domain.continuous_sweep_request import ContinuousSweepPlanRequest
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET
from ..domain.analyzer_resources import AnalyzerGeometryPreflight, estimate_analyzer_reduced
from .native_live import build_native_fixed_band_config
from .native_continuous_sweep import (
    ContinuousSweepDisplaySnapshot,
    NativeContinuousSweepDisplayService,
)
from .native_sweep import NativeSweepLease


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

    def preflight(self, request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight:
        """Read the held applied profile; construct no native config or device."""
        self._require_open()
        self._lease.assert_active()
        return self.preflight_profile(self._lease.source.live_configuration, request)

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
        count = max(1, math.ceil(max(0.0, span - request.usable_window_hz) / stride) + 1)
        if count > 64:
            raise ValueError("continuous sweep plan exceeds native 64-segment bound")
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
        return AnalyzerGeometryPreflight(
            "sweep", sample_rate, fft_size, spacing, count, stride, reduced,
            usable_window_hz=request.usable_window_hz,
            analysis_bins_per_usable_window=request.analysis_bins_per_usable_window,
            physical_bin_spacing_hz=physical_spacing,
        )

    def build(self, request: ContinuousSweepPlanRequest) -> Any:
        preflight = self.preflight(request)
        source = self._lease.source
        live = source.live_configuration
        segments = []
        for index in range(preflight.segment_count):
            usable_start = request.start_hz + index * preflight.segment_stride_hz
            usable_stop = min(request.stop_hz, usable_start + request.usable_window_hz)
            center_hz = (usable_start + usable_stop) / 2.0
            configuration = replace(live, center_hz=center_hz)
            fixed = build_native_fixed_band_config(
                self._lease.native_module,
                configuration,
                source.context_uri,
                source_id=f"continuous-sweep:{source.source_id}",
                device_buffer_samples=request.acquisition_buffer_samples,
                # This queue is private to the native coordinator. The
                # completed SweepLine output remains separately bounded and
                # the Qt render cadence is never configured here.
                snapshot_rate_hz=request.line_snapshot_rate_hz,
                allow_r10d5_evidence_buffer_geometry=request.allow_r10d5_evidence_buffer_geometry,
            )
            segments.append(
                self._lease.native_module.ContinuousSweepSegmentConfig(
                    fixed, usable_start, usable_stop
                )
            )
        return self._lease.native_module.ContinuousSweepCoordinatorConfig(
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
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._lease.release()

    def create_display_service(self) -> NativeContinuousSweepDisplayService:
        self._require_open()
        return NativeContinuousSweepDisplayService(
            self._lease.native_module, self._lease.source.context_uri
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
        return self._lease.native_module.NativeContinuousSweepCoordinator(
            self._lease.source.context_uri, timeout_ms
        )

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
        if self._display is not None:
            raise RuntimeError("continuous sweep is already running")
        factory = NativeContinuousSweepPlanFactory.from_native_live(self._live_service)
        display = None
        try:
            config = factory.build(request)
            display = factory.create_display_service()
            display.start(config)
        except Exception:
            if display is not None:
                try:
                    display.close()
                except Exception:
                    # Cleanup failed: keep ownership and the handle available
                    # for explicit shutdown, never hand an uncertain RX to Live.
                    self._factory = factory
                    self._display = display
                    raise
            factory.close()
            raise
        self._factory = factory
        self._display = display
        self._final_snapshot = None

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
        if display is not None:
            try:
                display.stop()
                self._final_snapshot = display.poll_latest()
            finally:
                display.close()
        # Keep the occupied handle throughout Stop and on cleanup failure.
        # A concurrent Start must not acquire another owner before this point.
        if factory is not None:
            factory.close()
        self._display = None
        self._factory = None

    def close(self) -> None:
        self.stop()


__all__ = [
    "ContinuousSweepPlanRequest",
    "NativeContinuousSweepPlanFactory",
    "NativeLiveContinuousSweepDisplayService",
]
