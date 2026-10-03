"""Bounded retired Sweep output, never an active Analyzer admission."""

from dataclasses import dataclass

import numpy as np

from .paired_sweep import PairedSweepRunIdentity
from .paired_sweep_publication import PairedSweepPublication
from .sweep_lines import SweepLineFrame, SweepLineState


@dataclass(frozen=True, slots=True)
class PairedSweepUnobservedTerminal:
    """Planned all-missing terminal; no actual RF/serial/step receipt claim."""

    run: PairedSweepRunIdentity
    primary: SweepLineFrame
    secondary: SweepLineFrame

    def __post_init__(self) -> None:
        if not isinstance(self.run, PairedSweepRunIdentity):
            raise TypeError("unobserved terminal requires its retired typed run")
        a, b = self.primary, self.secondary
        if (not isinstance(a, SweepLineFrame) or not isinstance(b, SweepLineFrame)
                or (a.source_id, b.source_id) != (self.run.request.pair.primary_source_id,
                                                 self.run.request.pair.secondary_source_id)
                or (a.epoch, a.sequence, a.unit, a.state, a.gap_reasons, a.missing_segment_indices)
                   != (b.epoch, b.sequence, b.unit, b.state, b.gap_reasons, b.missing_segment_indices)
                or a.epoch < self.run.request.sweep.epoch or a.state is not SweepLineState.GAP
                or not a.gap_reasons or not np.array_equal(a.frequencies_hz, b.frequencies_hz)):
            raise ValueError("unobserved paired terminal differs in planned source/epoch/grid/gap")
        for frame in (a, b):
            if frame.segment_acquisition or frame.last_admitted_segment is not None:
                raise ValueError("unobserved terminal must not claim an acquired step")
            for begin in range(0, frame.values_db.size, 65536):
                if (not bool(np.all(np.isnan(frame.values_db[begin:begin + 65536])))
                        or not bool(np.all(frame.source_segment_indices[begin:begin + 65536] == -1))):
                    raise ValueError("unobserved terminal must not claim measured bins")


@dataclass(frozen=True, slots=True)
class PairedSweepTerminalArchive:
    """One bounded drain after confirmed join; active polling stays retired.

    Caller retention is explicit reduced-output ownership, not another native
    queue, recording ledger, valid RF clock or complete scan guarantee.
    """

    run: PairedSweepRunIdentity
    terminals: tuple[PairedSweepPublication | PairedSweepUnobservedTerminal, ...]

    def __post_init__(self) -> None:
        if (not isinstance(self.run, PairedSweepRunIdentity) or type(self.terminals) is not tuple
                or len(self.terminals) > self.run.request.sweep.output_queue_capacity):
            raise ValueError("retired Sweep archive exceeds its admitted output capacity")
        previous = None
        for terminal in self.terminals:
            if (not isinstance(terminal, (PairedSweepPublication, PairedSweepUnobservedTerminal))
                    or terminal.run is not self.run or not isinstance(terminal.primary, SweepLineFrame)):
                raise ValueError("retired Sweep archive contains foreign run or nonterminal output")
            key = (terminal.primary.epoch, terminal.primary.sequence)
            if previous is not None and (key <= previous or key[0] != previous[0]):
                raise ValueError("retired Sweep archive epoch/sequence order differs")
            previous = key
