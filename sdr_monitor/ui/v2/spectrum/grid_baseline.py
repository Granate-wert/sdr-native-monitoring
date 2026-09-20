"""Worker-owned single comparison grid, admitted before allocating its copy."""
from typing import Callable
import numpy as np

from .allocation_budget import PresentationAllocationBudget


class MeasurementGridCache:
    def __init__(self, budget: PresentationAllocationBudget) -> None:
        self.budget = budget
        self.baseline: np.ndarray | None = None
        self._regular_spacing: float | None = None

    def clear(self) -> None:
        self.baseline = None
        self._regular_spacing = None

    def regular_spacing(self, frequencies: np.ndarray,
                        validate: Callable[[np.ndarray], float]) -> float:
        """Reuse one scalar only after exact content match to our owned grid.

        Mismatched terminal/preview grids take the original validation path;
        they neither replace the spectrum baseline nor allocate another one.
        Do not trust a source pointer/read-only flag: its external backing may
        have changed. No source reference, exception or derived array is kept.
        """
        if self.baseline is None or not np.array_equal(self.baseline, frequencies):
            return validate(frequencies)
        if self._regular_spacing is None:
            self._regular_spacing = validate(frequencies)
        return self._regular_spacing

    def prepare(self, frequencies: np.ndarray) -> np.ndarray:
        previous = self.baseline
        if previous is not None and np.array_equal(previous, frequencies):
            return previous
        # The displayed old baseline remains charged while this replacement
        # is prepared. No input reference or deferred allocation is retained.
        with self.budget.reserve(int(frequencies.nbytes)) as allocation:
            result = frequencies.copy()
            result.setflags(write=False)
            allocation.commit(result)
        self.baseline = result
        self._regular_spacing = None
        return result
