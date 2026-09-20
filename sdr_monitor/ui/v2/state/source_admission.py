"""Pre-holder admission of immutable Live/Sweep presentation source arrays."""
from dataclasses import replace

from sdr_monitor.domain.analyzer_display import ContinuousSweepDisplaySnapshot
from sdr_monitor.domain.live import LiveSnapshot
from sdr_monitor.domain.presentation_omission import OmittedMeasurement, PresentationOmission

from ..spectrum.allocation_budget import PresentationAllocationBudget
from ..spectrum.retained_bytes import retained_arrays


class PresentationSourceAdmission:
    def __init__(self, budget: PresentationAllocationBudget) -> None:
        self.budget = budget

    def _omission(self, snapshot: object, measurements: tuple[OmittedMeasurement, ...]) -> PresentationOmission:
        return PresentationOmission(sum(retained_arrays(snapshot).values()), self.budget.limit_bytes,
                                    tuple(measurements))

    def live(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        if snapshot.presentation_omission is not None or self.budget.admit_sources(snapshot):
            return snapshot
        return self.omit_live(snapshot)

    def omit_live(self, snapshot: LiveSnapshot) -> LiveSnapshot:
        frame = snapshot.spectrum
        records = () if frame is None else (OmittedMeasurement(
            source_id=str(frame.source_id), sequence=int(frame.sequence), epoch=frame.acquisition_epoch,
            unit=frame.unit, state="frame", raw_timestamp_ns=frame.timestamp_ns,
            clock_domain=frame.clock_domain, receiver_id=frame.receiver_id,
            configuration_generation=int(frame.config_generation)),)
        if frame is None and snapshot.persistence is not None:
            density = snapshot.persistence
            records = (OmittedMeasurement(
                source_id=str(density.source_id), sequence=int(density.source_frame_sequence),
                epoch=density.acquisition_epoch, unit=density.unit or snapshot.unit,
                state="persistence", revision=int(density.update_sequence),
                raw_timestamp_ns=int(density.timestamp_ns), clock_domain=density.clock_domain,
                receiver_id=density.receiver_id, configuration_generation=int(density.config_generation)),)
        return replace(snapshot, spectrum=None, persistence=None,
                       presentation_omission=self._omission(snapshot, records))

    def sweep(self, snapshot: ContinuousSweepDisplaySnapshot) -> ContinuousSweepDisplaySnapshot:
        if snapshot.presentation_omission is not None or self.budget.admit_sources(snapshot):
            return snapshot
        return self.omit_sweep(snapshot)

    def omit_sweep(self, snapshot: ContinuousSweepDisplaySnapshot) -> ContinuousSweepDisplaySnapshot:
        records: list[OmittedMeasurement] = []
        for frame in (snapshot.line, snapshot.progress):
            if frame is None:
                continue
            reasons = getattr(frame, "gap_reasons", ())
            records.append(OmittedMeasurement(
                source_id=frame.source_id, sequence=frame.sequence, epoch=frame.epoch, unit=frame.unit,
                state=getattr(getattr(frame, "state", None), "value", "partial"),
                revision=getattr(frame, "revision", None),
                raw_timestamp_ns=getattr(frame, "completed_at_ns", None),
                missing_segments=len(getattr(frame, "missing_segment_indices", ())),
                pending_segments=len(getattr(frame, "pending_segment_indices", ())),
                gap_reasons=tuple(str(getattr(reason, "value", reason)) for reason in reasons[:8]),
                gap_reason_count=len(reasons)))
        return replace(snapshot, line=None, progress=None,
                       presentation_omission=self._omission(snapshot, tuple(records)))
