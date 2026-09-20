"""Worker-owned single comparison grid, admitted before allocating its copy."""
import numpy as np

from .allocation_budget import PresentationAllocationBudget


class MeasurementGridCache:
    def __init__(self, budget: PresentationAllocationBudget) -> None:
        self.budget = budget
        self.baseline: np.ndarray | None = None

    def clear(self) -> None:
        self.baseline = None

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
        return result
