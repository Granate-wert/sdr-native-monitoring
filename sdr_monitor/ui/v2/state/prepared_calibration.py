"""Worker-only, budget-admitted CURRENT correction over the actual Live lane."""
from dataclasses import dataclass

import numpy as np

from sdr_monitor.services.captured_calibration import CapturedCalibrationLane, CapturedCalibratedPublication

from ..spectrum.allocation_budget import PresentationAllocationBudget
from ..spectrum.cancellation import CancelCheck, check_cancelled
from ..spectrum.contracts import PreparedSpectrumFrame
from ..spectrum.grid_baseline import MeasurementGridCache


@dataclass(frozen=True, slots=True)
class CalibratedCurrentFrame:
    """Separate values/axis; retains exact raw and immutable analytical authority."""

    publication: CapturedCalibratedPublication

    @property
    def frequencies_hz(self) -> np.ndarray:
        return self.publication.analytical.raw.frequencies_hz

    @property
    def values(self) -> np.ndarray:
        return self.publication.analytical.result.values

    @property
    def unit(self) -> str:
        return self.publication.analytical.result.unit

    @property
    def spectrum(self):
        return self.publication.analytical.raw


@dataclass(frozen=True, slots=True)
class PreparedCalibratedCurrent:
    frame: CalibratedCurrentFrame
    spectrum: PreparedSpectrumFrame


def prepare_calibrated_current(lane: CapturedCalibrationLane, budget: PresentationAllocationBudget,
                               *, cancelled: CancelCheck = None) -> PreparedCalibratedCurrent:
    check_cancelled(cancelled)
    handle = lane.capture(budget.admit_sources)
    check_cancelled(cancelled)
    with budget.reserve(handle.derived_output_bytes, handle) as allocation:
        check_cancelled(cancelled)
        publication = lane.correct(handle)
        check_cancelled(cancelled)
        allocation.commit(publication)
    frame = CalibratedCurrentFrame(publication)
    # The established renderer's comparison baseline is also admitted BEFORE
    # allocation, on this same worker/budget, never copied on Qt delivery.
    prepared = PreparedSpectrumFrame(frame, grid_cache=MeasurementGridCache(budget), cancelled=cancelled)
    check_cancelled(cancelled)
    return PreparedCalibratedCurrent(frame, prepared)
