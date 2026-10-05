"""Pure Sweep layer admission; scalar readiness is not RF or paint timing."""
from __future__ import annotations

import math
from typing import Any

from ..domain.analyzer_resources import estimate_analyzer_reduced
from ..domain.live import DEFAULT_LIVE_RESOURCE_BUDGET
from ..domain.layer_ready import SweepLayerIdentity, LayerReadyReceipt
from .native_layer_journal import (
    LAYER_EVENT_CAPACITY, NativeLayerJournal, layer_journal_capacity, sweep_layer_host_reservation,
)
from .native_ready_bridge import NativeReadyBridge


def pluto_sweep_layer_capacity(native: object) -> int:
    owner = getattr(native, "NativeContinuousSweepCoordinator", None)
    config = getattr(native, "ContinuousSweepCoordinatorConfig", None)
    receivers = getattr(native, "PlutoReceiverSelection", None)
    if (not callable(getattr(owner, "drain_sweep_layer_ready_events", None))
            or getattr(config, "layer_event_capacity", None) is None
            or any(getattr(receivers, name, None) is None for name in ("RX1", "RX2"))):
        return 0
    return layer_journal_capacity(native)


def sweep_layer_reserved_bytes(segment_count: int) -> int:
    # Host scalar window + conservative native ring/serialized-drain margin.
    # Included in the SAME existing 128MiB reduced component, not another pool.
    return sweep_layer_host_reservation(segment_count) + 2 * LAYER_EVENT_CAPACITY * 1024 + 4096


def preflight_sweep_layer_config(config: Any) -> int:
    """Recheck before Configure, including manually supplied native plans."""
    count = len(config.segments)
    reserve = sweep_layer_reserved_bytes(count)
    first = config.segments[0].fixed_band
    size, rate = first.dsp.fft_size, first.device.sample_rate_hz
    spacing = (config.usable_window_hz / config.analysis_bins_per_usable_window
               if config.analysis_bins_per_usable_window else rate / size)
    ratio = (config.display_stop_hz - config.display_start_hz) / spacing
    bins = math.ceil(ratio - 1e-12) if config.analysis_bins_per_usable_window else math.floor(ratio) + 1
    reduced = estimate_analyzer_reduced("sweep", bins, config.output_queue_capacity + 3,
        physical_fft_size=size, segment_count=count)
    if reduced.total_bytes + reserve > DEFAULT_LIVE_RESOURCE_BUDGET.max_spectrum_backlog_bytes:
        raise ValueError("Sweep layer retention exceeds existing reduced memory budget")
    return count


def sweep_layer_receipt(raw: Any, journal: NativeLayerJournal, bridge: NativeReadyBridge,
                        receiver_id: str | None, *, progress: bool) -> LayerReadyReceipt | None:
    """Original scalar identity before the ONE actual domain array conversion."""
    if not journal.enabled:
        return None
    key = None
    original = None
    try:
        original = getattr(raw, "layer_ready", None)
        if progress:
            acquired = tuple(raw.acquired_segment_generations)
            pending = tuple(raw.pending_segment_indices)
            revision = raw.revision
        else:
            pending = tuple(raw.missing_segment_indices)
            missing = frozenset(pending)
            acquired = tuple(pair for pair in raw.segment_config_generations if pair[0] not in missing)
            revision = None
        key = SweepLayerIdentity(raw.source_id, raw.epoch, raw.line_sequence, revision,
            acquired, pending, receiver_id)
    except Exception:  # noqa: BLE001 - optional evidence cannot invalidate measurement.
        pass
    return journal.receipt(original, key, bridge)
