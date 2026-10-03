"""Two reduced views and scalar receipts from one admitted paired Sweep run."""

from dataclasses import dataclass

import numpy as np

from .paired_sweep import PairedSweepRunIdentity, PairedSweepStepPair
from .sweep_lines import SweepLineFrame
from .sweep_progress import SweepProgressFrame


@dataclass(frozen=True, slots=True)
class PairedSweepPublication:
    run: PairedSweepRunIdentity
    steps: tuple[PairedSweepStepPair, ...]
    primary: SweepLineFrame | SweepProgressFrame
    secondary: SweepLineFrame | SweepProgressFrame

    def __post_init__(self) -> None:
        if not isinstance(self.run, PairedSweepRunIdentity) or type(self.steps) is not tuple:
            raise TypeError("paired Sweep publication requires typed immutable provenance")
        a, b = self.primary, self.secondary
        if type(a) is not type(b) or not isinstance(a, (SweepLineFrame, SweepProgressFrame)):
            raise TypeError("paired Sweep publication requires two matching reduced views")
        request = self.run.request
        if (a.source_id != request.pair.primary_source_id
                or b.source_id != request.pair.secondary_source_id
                or (a.epoch, a.sequence, a.unit) != (b.epoch, b.sequence, b.unit)
                or a.epoch < request.sweep.epoch
                or not np.array_equal(a.frequencies_hz, b.frequencies_hz)
                or not np.array_equal(a.source_segment_indices, b.source_segment_indices)):
            raise ValueError("paired Sweep reduced views differ in source/epoch/grid")
        if isinstance(a, SweepProgressFrame) and isinstance(b, SweepProgressFrame):
            if (a.revision, a.pending_segment_indices) != (b.revision, b.pending_segment_indices):
                raise ValueError("paired Sweep progress partitions differ")
        elif isinstance(a, SweepLineFrame) and isinstance(b, SweepLineFrame):
            if (a.state, a.missing_segment_indices, a.gap_reasons) != (
                    b.state, b.missing_segment_indices, b.gap_reasons):
                raise ValueError("paired Sweep terminal states differ")
        acquired = (a.segment_acquisition or (), b.segment_acquisition or ())
        if any(len(chain) != len(self.steps) for chain in acquired):
            raise ValueError("paired Sweep views lack matching acquisition receipts")
        for index, pair in enumerate(self.steps):
            if not isinstance(pair, PairedSweepStepPair):
                raise TypeError("paired Sweep publication requires typed step pairs")
            identity = pair.primary.identity
            if (identity.acquisition_epoch != self.run.acquisition_epoch
                    or identity.sweep_epoch != a.epoch or identity.line_sequence != a.sequence
                    or identity.segment_index != index):
                raise ValueError("paired Sweep publication steps differ from the admitted run/prefix")
            pair.validate_active(request, identity)
            for observation, chain in ((pair.primary, acquired[0]), (pair.secondary, acquired[1])):
                item = chain[index]
                if ((item.segment_index, item.config_generation, item.frame_sequence,
                     item.first_sample_index, item.timestamp_ns, item.sample_rate_hz,
                     item.fft_size, item.quality_flags)
                        != (index, identity.config_generation, observation.frame_sequence,
                            observation.first_sample_index, observation.timestamp_ns,
                            observation.sample_rate_hz, observation.fft_size, observation.quality_flags)):
                    raise ValueError("paired Sweep reduced acquisition differs from observed step")
