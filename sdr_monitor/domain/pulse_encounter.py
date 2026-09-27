"""Conditional temporal opportunity for ONE uniformly timed pulse, not RF Pd.

Only externally qualified capture windows for the selected frequency/RX may
be supplied. Host ingress rates, FFT publication times and visual FPS cannot
construct those windows. The UI must keep the result unknown until a producer
can qualify them; this module performs no acquisition or detector inference.
"""

from dataclasses import dataclass

from .identity import TimestampQuality

_MAX_WINDOWS = 4096


@dataclass(frozen=True, slots=True)
class PulseEncounterEstimate:
    temporal_fraction: float
    pulse_duration_ns: int
    minimum_overlap_ns: int
    pulse_start_horizon_ns: tuple[int, int]
    capture_timing_quality: TimestampQuality
    scope: str = "conditional temporal encounter; not amplitude/detector probability"


def pulse_encounter_estimate(
    capture_windows_ns: tuple[tuple[int, int], ...], *,
    pulse_start_horizon_ns: tuple[int, int], pulse_duration_ns: int,
    capture_timing_quality: TimestampQuality, minimum_overlap_ns: int = 0,
    coverage_complete: bool = False,
) -> PulseEncounterEstimate | None:
    """Measure the union of valid pulse START intervals on a finite horizon.

    For an observed interval [a,b], overlap >= delta is possible for pulse
    starts [a+delta-tau,b-delta], provided both pulse and interval are long
    enough. Adjacent/overlapping capture intervals are merged first. tau=0 is
    invalid; delta=0 means any positive overlap (endpoints have zero measure).
    Start times are assumed uniform on H. This makes no promise about pulse
    repetition/phase, SNR, calibration, detector accuracy or future intervals.
    Unknown/estimated/replay bounds or an incomplete/unknown window ledger
    have no hardware-qualified result. The producer must also qualify the
    clock/RX/frequency scope of the supplied complete ledger.
    """
    if not isinstance(capture_windows_ns, tuple) or len(capture_windows_ns) > _MAX_WINDOWS:
        raise ValueError("capture window tuple exceeds the bounded analysis contract")

    def interval(value, label):
        if (not isinstance(value, tuple) or len(value) != 2
                or any(type(number) is not int or number < 0 for number in value)
                or value[1] <= value[0]):
            raise ValueError(f"invalid {label} interval")
        return value

    horizon_start, horizon_stop = interval(pulse_start_horizon_ns, "pulse-start horizon")
    if (type(pulse_duration_ns) is not int or pulse_duration_ns <= 0
            or type(minimum_overlap_ns) is not int or minimum_overlap_ns < 0):
        raise ValueError("pulse duration/overlap must be integral nonnegative nanoseconds")
    windows = sorted(interval(value, "capture") for value in capture_windows_ns)
    if not isinstance(capture_timing_quality, TimestampQuality) or type(coverage_complete) is not bool:
        raise ValueError("capture timing/ledger provenance must be explicitly qualified")
    if not coverage_complete or capture_timing_quality not in (TimestampQuality.HARDWARE, TimestampQuality.SYNTHETIC):
        return None
    merged: list[tuple[int, int]] = []
    for start, stop in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(stop, merged[-1][1]))
        else:
            merged.append((start, stop))
    opportunities: list[tuple[int, int]] = []
    for start, stop in merged:
        if min(pulse_duration_ns, stop - start) < minimum_overlap_ns:
            continue
        begin = max(horizon_start, start + minimum_overlap_ns - pulse_duration_ns)
        end = min(horizon_stop, stop - minimum_overlap_ns)
        if end <= begin:
            continue
        if opportunities and begin <= opportunities[-1][1]:
            opportunities[-1] = (opportunities[-1][0], max(end, opportunities[-1][1]))
        else:
            opportunities.append((begin, end))
    fraction = sum(end - begin for begin, end in opportunities) / (horizon_stop - horizon_start)
    return PulseEncounterEstimate(fraction, pulse_duration_ns, minimum_overlap_ns,
                                  pulse_start_horizon_ns, capture_timing_quality)
