"""Bounded readout pacing; never used to schedule measurement rendering."""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import current_locale
from ..view_models.analyzer_view_model import AnalyzerViewState
from .analyzer_readouts import presentation_omission


def urgent_status_key(state: AnalyzerViewState) -> tuple[object, ...]:
    """Changes that bypass meter pacing, including loss and stale transitions."""
    bundle = state.bundle
    frame = getattr(bundle, "spectrum", None)
    age = state.live.data_age_ms
    performance = getattr(state.live.snapshot, "performance", None)
    omission = presentation_omission(state)
    # Historical quality.dropped_blocks also includes publication-queue loss.
    # Keep that aggregate visible, but do not treat each increment as RF loss.
    # Prefer the separately accounted producer counter for urgent updates.
    source_loss = getattr(performance, "source_blocks_dropped", None)
    if source_loss is None:
        source_loss = bool(state.live.loss.source_blocks)
    return (
        current_locale(), state.mode, state.configuration_pending, state.starting,
        state.stopping, state.running, state.error, state.stop_required,
        state.live.error_kind, source_loss, bool(state.live.loss.source_blocks),
        state.live.measurement_unavailable_reason,
        None if omission is None else tuple((item.source_id, item.epoch, item.state, item.gap_reasons)
                                            for item in omission.measurements),
        state.live.busy, state.live.discovery_pending, state.live.discovery_count,
        state.live.loss.acquisition_blocks, state.live.loss.fft_frames,
        None if age is None else age >= 1000,
        bundle is not None, getattr(frame, "source_id", None),
        getattr(bundle, "receiver_id", None), getattr(bundle, "acquisition_epoch", None),
        getattr(frame, "config_generation", None), getattr(bundle, "unit", None),
        getattr(frame, "native_quality_flags", None),
        getattr(frame, "dropped_samples_before", None),
        getattr(frame, "dropped_iq_blocks_before", None),
        getattr(frame, "dropped_fft_frames_before", None),
        getattr(frame, "state", None), getattr(frame, "gap_reasons", None),
        tuple(issue for issue in getattr(bundle, "coherence_issues", ())
              if issue != "persistence_pending"),
    )


@dataclass(slots=True)
class AnalyzerStatusCadence:
    """At most four numerical updates/s; urgent semantic changes are immediate."""

    interval_s: float = 0.25
    _last_at: float | None = None
    _key: tuple[object, ...] | None = None

    def admit(self, state: AnalyzerViewState, now: float) -> bool:
        key = urgent_status_key(state)
        if (self._last_at is None or self._key != key
                or now - self._last_at >= self.interval_s or now < self._last_at):
            self._key, self._last_at = key, now
            return True
        return False
