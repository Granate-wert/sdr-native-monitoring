"""Explicit AD936x pane profiles, admitted by capabilities rather than chip labels.

These are receive/filter and digital edge-trim intents, not measured flat RF
passbands. Rate availability is not sustained host transport throughput. The
existing native Apply/Start readback and selected-session guards remain final.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .device_capabilities import (
    CapabilityEvidenceOrigin, CapabilityField, DeviceCapabilitySnapshot, DeviceFamily,
)


@dataclass(frozen=True, slots=True)
class Ad936xPaneRateProfile:
    sample_rate_hz: float
    trimmed_filter_hz: float
    trimmed_window_hz: float
    full_receive_filter_hz: float
    sweep_overlap_hz: float | None


AD936X_PANE_RATE_PROFILES = (
    Ad936xPaneRateProfile(20_000_000., 10_000_000., 10_000_000., 20_000_000., None),
    Ad936xPaneRateProfile(30_720_000., 30_000_000., 30_000_000., 30_000_000., 1_000_000.),
    Ad936xPaneRateProfile(61_440_000., 40_000_000., 36_000_000., 56_000_000., 2_000_000.),
)


def ad936x_pane_rate_profile(sample_rate_hz: float) -> Ad936xPaneRateProfile:
    for profile in AD936X_PANE_RATE_PROFILES:
        if sample_rate_hz == profile.sample_rate_hz:
            return profile
    raise ValueError("AD936x pane supports explicit 20, 30.72 or 61.44 MS/s profiles")


def ad936x_pane_profile_supported(
    snapshot: DeviceCapabilitySnapshot | None,
    profile: Ad936xPaneRateProfile,
    *, full_receive: bool = False,
) -> bool:
    """Pure preflight, not discovery or a receipt authorizing RF changes.

    The new 30.72 profile requires observed Fs AND filter ranges. Legacy
    profiles retain deferred native admission when those facts are unknown;
    any known contradictory range still refuses, never silently clips/falls back.
    """
    if profile not in AD936X_PANE_RATE_PROFILES:
        return False
    strict = profile.sample_rate_hz == 30_720_000.
    if snapshot is None:
        return not strict
    if snapshot.family is not DeviceFamily.AD936X:
        return False
    bandwidth = profile.full_receive_filter_hz if full_receive else profile.trimmed_filter_hz
    for ranges, field, value in (
        (snapshot.sample_rate_ranges_hz, CapabilityField.SAMPLE_RATE_RANGE, profile.sample_rate_hz),
        (snapshot.analog_bandwidth_ranges_hz, CapabilityField.ANALOG_BANDWIDTH_RANGE, bandwidth),
    ):
        if strict and (not ranges or snapshot.evidence_for(field).origin is not CapabilityEvidenceOrigin.RUNTIME_READBACK):
            return False
        if ranges and not any(bounds.minimum <= value <= bounds.maximum for bounds in ranges):
            return False
    return True


def ad936x_pane_rate_choices(
    snapshot: DeviceCapabilitySnapshot | None, *, sweep: bool = False, full_receive: bool = False,
) -> tuple[float, ...]:
    """Ascending explicit choices; UI may choose the highest admitted Sweep Fs."""
    return tuple(profile.sample_rate_hz for profile in AD936X_PANE_RATE_PROFILES
                 if (not sweep or profile.sweep_overlap_hz is not None)
                 and ad936x_pane_profile_supported(snapshot, profile, full_receive=full_receive))


def ad936x_pane_sweep_window(profile: Ad936xPaneRateProfile, requested_hz: float | None = None) -> float:
    """Explicit Sweep window, capped at 36 MHz; never hardcode 18 MHz at low Fs.

    36 MHz is the established high-rate Sweep crop due to observed edge
    distortion. It is NOT an RTBW limit or a calibrated flatness guarantee for
    every device. Native geometry additionally validates overlap and spacing.
    """
    if profile not in AD936X_PANE_RATE_PROFILES or profile.sweep_overlap_hz is None:
        raise ValueError("AD936x pane Sweep requires an explicit 30.72 or 61.44 MS/s profile")
    window = profile.trimmed_window_hz if requested_hz is None else requested_hz
    maximum = min(profile.sample_rate_hz, profile.trimmed_filter_hz, 36_000_000.)
    if (type(window) not in {int, float} or not isfinite(window)
            or not profile.sweep_overlap_hz < window <= maximum):
        raise ValueError("Sweep analysis window must exceed overlap and fit Fs, RF filter and the 36 MHz Sweep limit")
    return float(window)
