"""Snapshot-only bridge for the native R10-D continuous sweep coordinator.

The coordinator owns retuning, acquisition and line assembly in C++.  This
module is called by a single-flight worker requested by a low-rate UI timer: it drains only bounded
terminal lines and progressive snapshots, never raw I/Q or individual FFT frames.
"""

from __future__ import annotations

import math
import threading
import time
from uuid import uuid4
from typing import Any, Protocol
from collections.abc import Callable

import numpy as np

from ..domain import SweepLineFrame, SweepLineGapReason, SweepLineState
from ..domain.analyzer_display import ContinuousSweepDisplayMetrics, ContinuousSweepDisplaySnapshot
from ..domain.sweep_progress import SweepProgressFrame
from ..domain.sweep_acquisition import SweepSegmentAcquisition, SweepSegmentPosition, SweepSegmentProcessing
from .native_spectrum_provenance import native_contributing_provenance
from ..domain.sweep_statistics import SweepStatisticsFrame
from ..domain.sweep_processing import AdSweepProcessingPlanV1, SweepProcessingContextV1
from .sweep_processing import (
    ad_sweep_processing_receipt, validate_ad_sweep_processing_config, ad_sweep_processing_reserved_bytes,
)
from ..domain.pluto_connection import PlutoUsbConnectionExpectation
from ..domain.layer_ready import LayerReadyReceipt
from .ad936x_identity_admission import create_identity_bound_owner
from .completed_line_rate import CompletedLineRateObservation
from ..domain.layer_journal import LayerJournalSnapshot, SweepLayerScope
from .native_layer_journal import NativeLayerJournal, LAYER_EVENT_CAPACITY
from .native_ready_bridge import NativeReadyBridge
from .native_sweep_layer_journal import (
    pluto_sweep_layer_capacity, preflight_sweep_layer_config, sweep_layer_receipt,
)


class ContinuousSweepDisplayPort(Protocol):
    """Minimal lifecycle surface consumed by the Qt continuous-sweep presenter.

    A direct native display service owns one already-created coordinator.  The
    Live composition service defers that ownership (and its exclusive receiver
    lease) until an explicit Start action.  The presenter needs neither
    implementation detail: it only starts a pre-built request/configuration,
    polls bounded snapshots, and performs orderly shutdown.

    Keeping this as a structural port prevents the UI composition root from
    widening a deferred Live service into a concrete coordinator service.
    """

    def start(self, native_config: Any) -> None:
        """Start from an already validated composition-owned value."""

    def poll_latest(self) -> ContinuousSweepDisplaySnapshot:
        """Return the newest bounded UI-safe snapshot."""

    def stop(self) -> None:
        """Stop a running operation without closing the service."""

    def close(self) -> None:
        """Release all service-owned resources."""


class NativeContinuousSweepDisplayService:
    """Own a native coordinator and expose a bounded polling surface to UI.

    It intentionally accepts the already validated native coordinator config:
    planning and hardware capability admission remain native/service concerns,
    rather than widget-owned mutable state.
    """

    def __init__(self, native_module: Any, context_uri: str, *, timeout_ms: int = 3000,
                 expected_serial: str | None = None,
                 expected_usb_connection: PlutoUsbConnectionExpectation | None = None,
                 processing_plan: AdSweepProcessingPlanV1 | None = None) -> None:
        if not context_uri.strip() or timeout_ms <= 0:
            raise ValueError("continuous sweep context URI and timeout must be positive")
        coordinator_type = getattr(native_module, "NativeContinuousSweepCoordinator", None)
        if coordinator_type is None:
            raise RuntimeError("native continuous sweep coordinator is unavailable")
        self._coordinator = create_identity_bound_owner(
            native_module, "NativeContinuousSweepCoordinator", context_uri, timeout_ms,
            expected_serial=expected_serial,
            expected_usb_connection=expected_usb_connection,
        )
        self._lock = threading.RLock()
        self._closed = False
        self._closing = False
        self._stop_required = False
        self._started = False
        self._has_started = False
        self._completed_rate = CompletedLineRateObservation()
        self._ui_superseded = 0
        self._terminal_watermark: tuple[str, int, int] | None = None
        self._progress_watermark: tuple[str, int, int, int] | None = None
        self._latest_progress: SweepProgressFrame | None = None
        self._active_identity: tuple[str, int] | None = None
        self._statistics_cache = _SweepStatisticsCache()
        self._native = native_module
        self._ready_bridge = NativeReadyBridge(native_module)
        self._layer_journal = NativeLayerJournal(native_module, density=False)
        self._layer_receiver: str | None = None
        self._processing_plan = processing_plan

    def start(self, native_config: Any) -> None:
        with self._lock:
            self._require_open()
            if self._started or self._stop_required:
                raise RuntimeError("continuous sweep display service is already running")
            if self._processing_plan is not None:
                validate_ad_sweep_processing_config(self._processing_plan, native_config)
                from .native_layer_journal import sweep_layer_host_reservation
                reserve = (ad_sweep_processing_reserved_bytes(self._processing_plan)
                           + sweep_layer_host_reservation(len(native_config.segments)))
                if native_config.product_publication_reserved_bytes < reserve:
                    raise ValueError("AD Sweep native config lacks SAME HOST processing reservation")
            elif any(getattr(segment.fixed_band, "dc_removal_block_mean", False) for segment in native_config.segments):
                raise ValueError("processed AD Sweep requires actual selected processing plan")
            epoch = native_config.epoch
            sources = tuple(segment.fixed_band.device.source_id for segment in native_config.segments)
            if (type(epoch) is not int or epoch < 0 or not sources
                    or any(not isinstance(source, str) or not source.strip() for source in sources)
                    or len(set(sources)) != 1):
                raise ValueError("continuous sweep requires one explicit source and epoch")
            capacity = getattr(native_config, "layer_event_capacity", 0)
            if (type(capacity) is not int or capacity not in (0, LAYER_EVENT_CAPACITY)
                    or (capacity and pluto_sweep_layer_capacity(self._native) != capacity)):
                raise ValueError("Sweep journal config requires admitted loaded API")
            count = preflight_sweep_layer_config(native_config) if capacity else 1
            receiver = None
            if capacity:
                selection = native_config.segments[0].fixed_band.receiver_selection
                rx = self._native.PlutoReceiverSelection
                if selection not in (rx.RX1, rx.RX2) or any(
                        segment.fixed_band.receiver_selection != selection for segment in native_config.segments):
                    raise ValueError("Sweep journal requires ONE exact receiver throughout plan")
                receiver = "RX1" if selection == rx.RX1 else "RX2"
            journal = NativeLayerJournal(self._native, capacity, density=False, sweep_segments=count)
            if capacity:
                self._ready_bridge.begin()
                run = uuid4().hex
                journal.begin(SweepLayerScope(self._ready_bridge.clock_scope_id,
                    self._ready_bridge.host_process_id, run, sources[0], receiver,
                    self._processing_plan.session_id if self._processing_plan is not None else run, epoch))
            self._layer_journal, self._layer_receiver = journal, receiver
            # A native configure/start may mutate before throwing. Retain the
            # cleanup obligation before entering either call, not on success.
            self._stop_required = True
            self._coordinator.configure(native_config)
            self._coordinator.start()
            self._active_identity = (sources[0], epoch)
            self._started = True
            self._has_started = True
            self._completed_rate.reset()
            self._ui_superseded = 0
            self._terminal_watermark = None
            self._progress_watermark = None
            self._latest_progress = None
            self._statistics_cache = _SweepStatisticsCache()

    def poll_latest(self, *, now_s: float | None = None) -> ContinuousSweepDisplaySnapshot:
        """Drain terminal output even after Stop; preview is running-only."""

        now = time.monotonic() if now_s is None else float(now_s)
        if not math.isfinite(now):
            raise ValueError("continuous sweep metric timestamp must be finite")
        with self._lock:
            self._require_open()
            lines = tuple(self._coordinator.poll_lines()) if self._has_started else ()
            if any((item.source_id, item.epoch) != self._active_identity for item in lines):
                raise RuntimeError("continuous sweep terminal source/epoch differs from active request")
            poll_progress = getattr(self._coordinator, "poll_progress", None)
            native_progress = poll_progress() if self._started and callable(poll_progress) else None
            if self._layer_journal.enabled:
                self._layer_journal.drain(self._read_layer_events)
                self._ready_bridge.sample()
            # Select by producer identity, not arrival position. Keep the
            # terminal watermark distinct from progressive revisions: a late
            # terminal still has consumers even after the next preview arrived.
            newest = max(lines, key=lambda item: item.line_sequence) if lines else None
            previous = self._terminal_watermark
            if newest is not None and previous is not None and newest.line_sequence <= previous[2]:
                newest = None
            line = (_to_domain_line(newest, statistics_cache=self._statistics_cache,
                receiver_id=self._layer_receiver, layer_ready=sweep_layer_receipt(newest,
                    self._layer_journal, self._ready_bridge, self._layer_receiver, progress=False),
                processing_join=self._processing_receipt)
                if newest is not None else None)
            self._ui_superseded += len(lines) - int(line is not None)
            terminal_watermark = self._terminal_watermark
            progress_watermark = self._progress_watermark
            latest_progress = self._latest_progress
            if line is not None:
                terminal_watermark = (line.source_id, line.epoch, line.sequence)
                if latest_progress is not None and latest_progress.sequence <= line.sequence:
                    latest_progress = None
            if (native_progress is not None
                    and (native_progress.source_id, native_progress.epoch) != self._active_identity):
                raise RuntimeError("continuous sweep progress source/epoch differs from active request")
            progress = None
            if native_progress is not None:
                sequence, revision = native_progress.line_sequence, native_progress.revision
                if any(type(value) is not int or value < 0 for value in (sequence, revision)):
                    raise ValueError("progress identity must use nonnegative integers")
                identity = (native_progress.source_id, native_progress.epoch, sequence, revision)
                if ((terminal_watermark is not None and terminal_watermark[:2] == identity[:2]
                     and terminal_watermark[2] >= identity[2])
                        or (progress_watermark is not None and progress_watermark[:2] == identity[:2]
                            and progress_watermark[2:] >= identity[2:])):
                    progress = None
                else:
                    # Native preview is latest-only, but can still precede a
                    # terminal drained in the same poll. Reject superseded
                    # identity before touching any full-grid array/statistics.
                    progress = _to_domain_progress(native_progress, statistics_cache=self._statistics_cache,
                        receiver_id=self._layer_receiver, layer_ready=sweep_layer_receipt(native_progress,
                            self._layer_journal, self._ready_bridge, self._layer_receiver, progress=True),
                        processing_join=self._processing_receipt)
                    progress_watermark = identity
                    latest_progress = progress
            if line is not None and progress is None and latest_progress is not None:
                # The chart must not roll back from preview N to late terminal
                # N-1. Retain just one immutable preview, never a history or an
                # accumulation buffer. The terminal stays available separately.
                progress = latest_progress
            metrics = self._metrics(now)
            snapshot = ContinuousSweepDisplaySnapshot(line=line, metrics=metrics, progress=progress)
            # A rejected conversion/coherence packet is not an acknowledgement.
            self._terminal_watermark = terminal_watermark
            self._progress_watermark = progress_watermark
            self._latest_progress = latest_progress
            return snapshot

    def stop(self) -> None:
        with self._lock:
            if self._closed or not self._stop_required:
                return
            native_state = getattr(self._coordinator, "state", None)
            state = native_state() if callable(native_state) else None
            # A rejected Configure/Start can leave an explicitly idle native
            # coordinator. Its Stop API rejects CREATED/CONFIGURED, and no
            # worker exists in those states. Unknown state never waives Stop.
            if getattr(state, "name", None) not in ("CREATED", "CONFIGURED"):
                self._coordinator.stop()
            if self._layer_journal.enabled:
                self._ready_bridge.sample()
            self._layer_journal.finish(self._read_layer_events)
            self._started = False
            self._stop_required = False

    def layer_journal_snapshot(self) -> LayerJournalSnapshot:
        """Cached ONLY; no clock/drain/SDK call or synthetic frame creation."""
        with self._lock:
            return self._layer_journal.current()

    def _processing_receipt(self, raw: Any, records: Any, ready: Any):
        return (ad_sweep_processing_receipt(raw, records, ready, self._layer_journal.current(), self._processing_plan)
                if self._processing_plan is not None else None)

    def _read_layer_events(self, maximum: int) -> object:
        rx = self._native.PlutoReceiverSelection
        receiver = rx.RX1 if self._layer_receiver == "RX1" else rx.RX2
        return self._coordinator.drain_sweep_layer_ready_events(receiver, maximum)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            self.stop()
            # Disconnect is forbidden until Stop succeeds. A failed disconnect
            # keeps this handle retryable without repeating a successful Stop.
            self._coordinator.disconnect()
            self._closed = True

    def _metrics(self, now_s: float) -> ContinuousSweepDisplayMetrics:
        native = self._coordinator.metrics()
        completed = int(getattr(native, "completed_lines", 0) or 0)
        rate = self._completed_rate.observe(completed, now_s)
        queue = getattr(native, "output_queue", None)
        return ContinuousSweepDisplayMetrics(
            completed_line_lps=rate,
            completed_lines=completed,
            gapped_lines=int(getattr(native, "gapped_lines", 0) or 0),
            native_line_relay_superseded=int(
                getattr(native, "line_relay_snapshots_superseded", 0) or 0
            ),
            native_line_relay_queue_capacity=int(
                getattr(native, "line_relay_queue_capacity", 0) or 0
            ),
            native_line_relay_queue_high_water=int(
                getattr(native, "line_relay_queue_high_water", 0) or 0
            ),
            terminal_control_gaps=int(getattr(native, "terminal_control_gaps", 0) or 0),
            native_output_superseded=int(getattr(native, "output_snapshots_superseded", 0) or 0),
            ui_snapshots_superseded=self._ui_superseded,
            native_queue_depth=int(getattr(queue, "depth", 0) or 0),
            native_queue_capacity=int(getattr(queue, "capacity", 0) or 0),
            has_error=bool(getattr(native, "has_error", False)),
        )

    def _require_open(self) -> None:
        if self._closed or self._closing:
            raise RuntimeError("continuous sweep display service is closed or awaiting cleanup")


def _to_domain_acquisition(native: Any) -> tuple[SweepSegmentAcquisition, ...] | None:
    records = getattr(native, "segment_acquisition", None)
    if records is None:
        return None
    return tuple(SweepSegmentAcquisition(
        segment_index=item.segment_index, config_generation=item.config_generation,
        frame_sequence=item.frame_sequence, first_sample_index=item.first_sample_index,
        timestamp_ns=item.timestamp_ns, sample_rate_hz=item.sample_rate_hz,
        fft_size=item.fft_size, quality_flags=item.quality_flags,
        processing_metadata=_to_domain_processing(item, native.unit),
    ) for item in records)


def _to_domain_processing(item: Any, unit: Any) -> SweepSegmentProcessing | None:
    try:
        metadata = getattr(item, "processing_metadata", None)
    except Exception:  # noqa: BLE001 - unsupported legacy metadata stays UNKNOWN.
        return None
    if metadata is None:
        return None
    return SweepSegmentProcessing(
        metadata.center_frequency_hz, metadata.sample_rate_hz, metadata.analog_bandwidth_hz,
        metadata.fft_size, metadata.hop_size,
        native_contributing_provenance(metadata, native_quality_flags=item.quality_flags, unit=unit),
    )


def _to_domain_position(native: Any) -> SweepSegmentPosition | None:
    value = getattr(native, "last_admitted_segment", None)
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 4:
        raise ValueError("native last admitted segment must be a four-field tuple")
    return SweepSegmentPosition(*value)


def _to_domain_progress(native: Any, *, statistics_cache: _SweepStatisticsCache | None = None,
                        receiver_id: str | None = None,
                        layer_ready: LayerReadyReceipt | None = None,
                        processing_join: Callable[..., SweepProcessingContextV1 | None] | None = None) -> SweepProgressFrame:
    acquisition = _to_domain_acquisition(native)
    context = processing_join(native, acquisition, layer_ready) if processing_join is not None else None
    return SweepProgressFrame(
        source_id=native.source_id, sequence=native.line_sequence,
        epoch=native.epoch, revision=native.revision, unit=native.unit,
        frequencies_hz=native.frequencies_hz, values_db=native.values,
        quality_flags=native.quality_flags_per_bin,
        source_segment_indices=native.source_segment_indices,
        acquired_segment_generations=tuple(native.acquired_segment_generations),
        pending_segment_indices=tuple(native.pending_segment_indices),
        segment_acquisition=acquisition,
        last_admitted_segment=_to_domain_position(native),
        statistics=_to_domain_statistics(native, cache=statistics_cache),
        receiver_id=receiver_id, layer_ready=layer_ready, processing_context=context,
    )


def _to_domain_line(native: Any, *, statistics_cache: _SweepStatisticsCache | None = None,
                    receiver_id: str | None = None,
                    layer_ready: LayerReadyReceipt | None = None,
                    processing_join: Callable[..., SweepProcessingContextV1 | None] | None = None) -> SweepLineFrame:
    from ..domain.sweep_lines import SweepQualitySchema

    try:
        acquisition = _to_domain_acquisition(native)
        context = processing_join(native, acquisition, layer_ready) if processing_join is not None else None
        state = SweepLineState(str(native.state))
        reasons = tuple(SweepLineGapReason(str(value)) for value in native.gap_reasons)
        quality = np.asarray(native.quality_flags_per_bin)
        if quality.size and int(np.max(quality)) > np.iinfo(np.uint16).max:
            raise ValueError("native continuous sweep quality flag exceeds domain width")
        return SweepLineFrame(
            sequence=int(native.line_sequence),
            epoch=int(native.epoch),
            completed_at_ns=int(native.completed_ns),
            source_id=str(native.source_id),
            state=state,
            frequencies_hz=native.frequencies_hz,
            values_db=native.values,
            quality_flags=quality,
            quality_schema=SweepQualitySchema.NATIVE_V5,
            segment_acquisition=acquisition,
            last_admitted_segment=_to_domain_position(native),
            statistics=_to_domain_statistics(native, cache=statistics_cache),
            source_segment_indices=native.source_segment_indices,
            missing_segment_indices=tuple(int(value) for value in native.missing_segment_indices),
            segment_config_generations=tuple(
                (int(index), int(generation))
                for index, generation in native.segment_config_generations
            ),
            gap_reasons=reasons,
            unit=str(native.unit),
            analysis_window_hz=float(getattr(native, "analysis_window_hz", 0.0)),
            analysis_bins_per_usable_window=int(
                getattr(native, "analysis_bins_per_usable_window", 0)
            ),
            physical_fft_bin_width_hz=float(
                getattr(native, "physical_fft_bin_width_hz", 0.0)
            ),
            physical_fft_size=int(getattr(native, "physical_fft_size", 0)),
            receiver_id=receiver_id, layer_ready=layer_ready, processing_context=context,
        )
    except (AttributeError, TypeError, ValueError) as error:
        raise RuntimeError(f"native continuous sweep line conversion failed: {error}") from error


class _SweepStatisticsCache:
    """One native owner, not an update-ID cache that could hide a changed payload.

    Only pybind's immutable shared snapshot type is reusable. Mutable fake
    wrappers are deliberately revalidated. A new run drops this sole owner.
    """
    def __init__(self) -> None:
        self.native: object | None = None
        self.frame: SweepStatisticsFrame | None = None


def _to_domain_statistics(native: Any, *, cache: _SweepStatisticsCache | None = None) -> SweepStatisticsFrame | None:

    statistics = getattr(native, "statistics", None)
    if statistics is None:
        return None
    immutable_native = (type(statistics).__module__ == "sdr_monitor._sdr_native"
                        and type(statistics).__name__ == "SweepStatisticsSnapshot")
    if cache is not None and immutable_native and statistics is cache.native:
        return cache.frame
    frame = SweepStatisticsFrame(**{
        name: getattr(statistics, name) for name in SweepStatisticsFrame.__dataclass_fields__
    })
    if cache is not None and immutable_native:
        cache.native, cache.frame = statistics, frame
    return frame


__all__ = [
    "ContinuousSweepDisplayPort",
    "ContinuousSweepDisplayMetrics",
    "ContinuousSweepDisplaySnapshot",
    "NativeContinuousSweepDisplayService",
]
