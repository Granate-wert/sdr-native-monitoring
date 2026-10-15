"""Worker-owned single comparison grid, admitted before allocating its copy."""
from typing import Callable
import numpy as np

from .allocation_budget import PresentationAllocationBudget


def _same_grid(left: np.ndarray, right: np.ndarray) -> bool:
    """Exact array_equal semantics, with <=64K comparison scratch for 1D grids.

    Recheck content even for identical pointers/read-only source views. The
    owned baseline is not a licence to trust externally mutable backing data.
    Non-vector callers retain the original comparison semantics.
    """
    if left.shape != right.shape:
        return False
    if left.ndim != 1:
        return bool(np.array_equal(left, right))
    return all(np.array_equal(left[start:start + 65536], right[start:start + 65536])
               for start in range(0, left.size, 65536))


class MeasurementGridCache:
    def __init__(self, budget: PresentationAllocationBudget) -> None:
        self.budget = budget
        self.baseline: np.ndarray | None = None
        self._regular_spacing: float | None = None
        self._regular_spacing_fp_clean = False

    def clear(self) -> None:
        self.baseline = None
        self._regular_spacing = None
        self._regular_spacing_fp_clean = False

    def regular_spacing(self, frequencies: np.ndarray,
                        validate: Callable[[np.ndarray], float], *,
                        require_fp_clean: bool = False) -> float:
        """Reuse one scalar only after exact content match to our owned grid.

        Mismatched terminal/preview grids take the original validation path;
        they neither replace the spectrum baseline nor allocate another one.
        Do not trust a source pointer/read-only flag: its external backing may
        have changed. Density callers may additionally require a successful
        validation under a private raising FP policy; ordinary callers retain
        their original cached-spacing semantics. No source reference, exception
        or derived array is kept.
        """
        if self.baseline is None or not _same_grid(self.baseline, frequencies):
            return validate(frequencies)
        if require_fp_clean:
            if self._regular_spacing is not None and self._regular_spacing_fp_clean:
                return self._regular_spacing
            probe_failed = False
            try:
                with np.errstate(all="raise"):
                    spacing = validate(frequencies)
            except FloatingPointError:
                probe_failed = True
            if probe_failed:
                # Replay only after the exception handler has exited. The
                # original caller policy, callback and exception context
                # apply to this one required validation attempt.
                return validate(frequencies)
            self._regular_spacing = spacing
            self._regular_spacing_fp_clean = True
            return spacing
        if self._regular_spacing is None:
            self._regular_spacing = validate(frequencies)
        return self._regular_spacing

    def prepare(self, frequencies: np.ndarray) -> np.ndarray:
        previous = self.baseline
        if previous is not None and _same_grid(previous, frequencies):
            return previous
        # The displayed old baseline remains charged while this replacement
        # is prepared. No input reference or deferred allocation is retained.
        with self.budget.reserve(int(frequencies.nbytes)) as allocation:
            result = frequencies.copy()
            result.setflags(write=False)
            allocation.commit(result)
        self.baseline = result
        self._regular_spacing = None
        self._regular_spacing_fp_clean = False
        return result
