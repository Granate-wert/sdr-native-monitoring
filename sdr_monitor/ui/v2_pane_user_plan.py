"""Compile a user's four-slot source/range draft into the existing pane owners.

This module is pure: it does not discover devices, configure RF, acquire a
lease or start a receiver.  Source choices and selection revisions must come
from the explicit, freshly staged product graphs.  A repeated source ID maps
to one resource; it is never mistaken for a second receiver.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, isfinite
from typing import Mapping

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.analyzer_resources import AnalyzerGeometryPreflight
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.identity import SourceId
from sdr_monitor.domain.live import BackendKind, LiveConfiguration
from sdr_monitor.domain.pane_scheduler import (
    Ad936xSweepPaneProfile, CaptureEpochCost, CaptureMeasurementMode, HackrfRtbwPaneProfile,
    HackrfSweepPaneProfile, PaneCaptureProfile, PaneLayout,
    PaneLayoutSlot, PaneProfile, TinySaTracePaneProfile, compile_pane_layout,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection,
    ReceiverEndpoint, SpectrumTraceEndpoint, SweepPaneRequest,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory


class PaneUserPlanError(ValueError):
    """A bounded, user-facing draft cannot be admitted as an exact plan."""


@dataclass(frozen=True, slots=True)
class PaneSlotDraft:
    """One visible slot.  No source means genuinely Empty, not a cloned view."""

    number: int
    source_id: str | None = None
    start_hz: float | None = None
    stop_hz: float | None = None
    sample_rate_hz: float = 20_000_000.0
    fft_size: int = 4096
    points: int = 101
    network_discovery: bool = False
    measurement_mode: CaptureMeasurementMode | None = None

    def __post_init__(self) -> None:
        if type(self.number) is not int or not 1 <= self.number <= 4:
            raise PaneUserPlanError("pane number must be in [1, 4]")
        if type(self.network_discovery) is not bool:
            raise PaneUserPlanError("pane network discovery intent must be explicit")
        if self.measurement_mode is not None:
            try:
                object.__setattr__(self, "measurement_mode", CaptureMeasurementMode(self.measurement_mode))
            except ValueError:
                raise PaneUserPlanError("pane measurement mode is not supported") from None
        if self.source_id is None:
            if (self.start_hz is not None or self.stop_hz is not None
                    or self.measurement_mode is not None):
                raise PaneUserPlanError("an Empty pane cannot retain a frequency range")
            return
        if (not isinstance(self.source_id, str) or not self.source_id.strip()
                or isinstance(self.start_hz, bool) or isinstance(self.stop_hz, bool)
                or not isinstance(self.start_hz, (int, float))
                or not isinstance(self.stop_hz, (int, float))
                or not isfinite(self.start_hz) or not isfinite(self.stop_hz)
                or self.start_hz < 100_000 or self.stop_hz <= self.start_hz):
            raise PaneUserPlanError("occupied pane needs a source and an increasing RF range")
        if (self.sample_rate_hz not in {16_000_000.0, 20_000_000.0, 61_440_000.0}
                or type(self.fft_size) is not int or self.fft_size not in {1024, 4096, 16384}
                or type(self.points) is not int or not 2 <= self.points <= 10001):
            raise PaneUserPlanError("pane sample rate, FFT or trace points are outside the qualified draft choices")


@dataclass(frozen=True, slots=True)
class PaneUserPlan:
    layout: PaneLayout
    groups: tuple[AcquisitionGroup, ...]
    resource_sources: tuple[tuple[str, str], ...]
    initial_ad_configurations: tuple[tuple[str, LiveConfiguration], ...]
    ad_sweep_geometry: tuple[tuple[str, AnalyzerGeometryPreflight], ...] = ()


def compile_user_pane_plan(
    drafts: tuple[PaneSlotDraft, ...],
    selected: Mapping[str, AnalyzerSourceChoice],
    selection_revisions: Mapping[str, int],
) -> PaneUserPlan:
    """Compile explicit source assignments; reject unsupported family modes.

    AD936x RX1 can use RTBW or its existing native continuous Sweep. HackRF RX1 can use
    RTBW or its existing bounded host Sweep owner; tinySA panes are device
    dBm traces. A repeated source shares a capture only if *all* its panes
    have one compatible profile and fit its usable span; otherwise it receives
    bounded, visibly time-sliced jobs.  This does not claim continuous RF duty.
    """
    if (not 1 <= len(drafts) <= 4
            or tuple(draft.number for draft in drafts) != tuple(range(1, len(drafts) + 1))):
        raise PaneUserPlanError("pane slots must be contiguous from 1 to 4")
    source_order = tuple(dict.fromkeys(draft.source_id for draft in drafts
                                      if isinstance(draft.source_id, str)))
    if not source_order:
        raise PaneUserPlanError("at least one pane must have a source before Apply")
    if set(source_order) != set(selected) or set(source_order) != set(selection_revisions):
        raise PaneUserPlanError("draft sources differ from the exact staged selections")
    resource_for = {source_id: f"pane-resource-{index}" for index, source_id in enumerate(source_order, 1)}
    groups: list[AcquisitionGroup] = []
    endpoints: dict[str, str] = {}
    for source_id in source_order:
        choice = selected[source_id]
        if not isinstance(choice, AnalyzerSourceChoice) or choice.device_id != source_id:
            raise PaneUserPlanError("staged source identity changed before plan compilation")
        resource = resource_for[source_id]
        endpoint_id = f"{resource}:trace" if choice.family is DeviceFamily.TINYSA else f"{resource}:rx1"
        endpoint = (SpectrumTraceEndpoint(endpoint_id, source_id, resource)
                    if choice.family is DeviceFamily.TINYSA else
                    ReceiverEndpoint(endpoint_id, source_id, resource, ReceiverChainSelection.RX1))
        if choice.family not in {DeviceFamily.AD936X, DeviceFamily.HACKRF, DeviceFamily.TINYSA}:
            raise PaneUserPlanError("selected source has no qualified pane owner")
        groups.append(AcquisitionGroup(f"{resource}:group", resource, (endpoint,)))
        endpoints[source_id] = endpoint_id

    cost = CaptureEpochCost(0.01, 0.01, 0.05, 0.005, 0.005)
    trace_cost = CaptureEpochCost(0.01, 0.01, 8.0, 0.005, 0.005)
    profiles: dict[str, PaneProfile] = {}
    ad_geometry: list[tuple[str, AnalyzerGeometryPreflight]] = []
    by_source: dict[str, list[tuple[PaneSlotDraft, PaneProfile]]] = {source: [] for source in source_order}
    for draft in drafts:
        if draft.source_id is None:
            continue
        source_id = draft.source_id
        choice = selected[source_id]
        assert draft.start_hz is not None and draft.stop_hz is not None
        center = (draft.start_hz + draft.stop_hz) / 2.0
        if choice.family is DeviceFamily.AD936X:
            if draft.measurement_mode is CaptureMeasurementMode.SWEEP:
                if draft.sample_rate_hz != 61_440_000.0:
                    raise PaneUserPlanError("AD936x pane Sweep requires its explicit 61.44 MS/s profile")
                # The displayed selection is analysis N INSIDE W=36 MHz.
                # Choose and expose the minimum power-of-two physical F whose
                # Fs/F spacing can support W/N; never reinterpret N as F.
                minimum_f = ceil(draft.sample_rate_hz * draft.fft_size / 36_000_000.0)
                physical_f = 1 << (minimum_f - 1).bit_length()
                configuration = LiveConfiguration(
                    center_hz=center, sample_rate_hz=draft.sample_rate_hz,
                    analog_bandwidth_hz=40_000_000.0, gain_db=20.0,
                    fft_size=physical_f, overlap_ratio=0.5, detector="sample", window="hann",
                    snapshot_rate_hz=240.0, backend=BackendKind.CPU,
                    persistence_enabled=False, persistence_mode="disabled")
                request = ContinuousSweepPlanRequest(
                    draft.start_hz, draft.stop_hz, usable_window_hz=36_000_000.0,
                    overlap_hz=2_000_000.0, output_queue_capacity=2,
                    analysis_bins_per_usable_window=draft.fft_size)
                try:
                    geometry = NativeContinuousSweepPlanFactory.preflight_profile(configuration, request)
                except (TypeError, ValueError, RuntimeError):
                    raise PaneUserPlanError("AD936x Sweep exceeds native geometry or reduced-data memory bounds") from None
                profile: PaneProfile = Ad936xSweepPaneProfile(
                    choice, selection_revisions[source_id], configuration, request,
                    CaptureEpochCost(0.01, 0.01, 1.0, 0.005, 0.005))
                ad_geometry.append((f"pane-{draft.number}", geometry))
            else:
                if draft.measurement_mode not in (None, CaptureMeasurementMode.RTBW):
                    raise PaneUserPlanError("AD936x pane mode is not supported")
                if draft.sample_rate_hz not in {20_000_000.0, 61_440_000.0}:
                    raise PaneUserPlanError("AD936x pane supports the listed 20 or 61.44 MS/s profiles")
                usable = 10_000_000.0 if draft.sample_rate_hz == 20_000_000.0 else 36_000_000.0
                # RF filter and edge-trimmed usable analysis span are distinct.
                # Exact native capabilities and Start readback still decide.
                rf_bandwidth = 10_000_000.0 if draft.sample_rate_hz == 20_000_000.0 else 40_000_000.0
                profile = PaneCaptureProfile(
                    draft.sample_rate_hz, rf_bandwidth, "manual", 20.0,
                    draft.fft_size, draft.fft_size // 2, "hann", "sample", None, usable, cost)
        elif choice.family is DeviceFamily.HACKRF:
            if draft.measurement_mode is CaptureMeasurementMode.SWEEP:
                if draft.sample_rate_hz != 20_000_000.0:
                    raise PaneUserPlanError("HackRF host Sweep uses its fixed 20 MS/s profile")
                try:
                    sweep_request = HackrfSweepRequest(
                        choice, selection_revisions[source_id],
                        int(draft.start_hz), int(draft.stop_hz), draft.fft_size,
                        16, 20, 50)
                except (TypeError, ValueError):
                    raise PaneUserPlanError("HackRF Sweep requires whole MHz, a 20..320 MHz span and FFT <=4096") from None
                if sweep_request.start_hz != draft.start_hz or sweep_request.stop_hz != draft.stop_hz:
                    raise PaneUserPlanError("HackRF Sweep endpoints must be exact whole MHz")
                sweep_cost = CaptureEpochCost(0.01, 0.01, 1.0, 0.005, 0.005)
                profile = HackrfSweepPaneProfile(sweep_request, sweep_cost)
            else:
                if draft.measurement_mode not in (None, CaptureMeasurementMode.RTBW):
                    raise PaneUserPlanError("HackRF pane mode is not supported")
                if draft.sample_rate_hz not in {16_000_000.0, 20_000_000.0}:
                    raise PaneUserPlanError("HackRF pane supports the listed 16 or 20 MS/s profiles")
                hackrf_request = HackrfLiveRequest(
                    center, draft.sample_rate_hz,
                    14_000_000 if draft.sample_rate_hz == 16_000_000.0 else 15_000_000,
                    16, 20, fft_size=draft.fft_size, hop_size=draft.fft_size // 2,
                    detector="peak", source_id=SourceId(source_id))
                profile = HackrfRtbwPaneProfile(hackrf_request, 10_000_000.0, cost)
        else:
            if draft.measurement_mode not in (None, CaptureMeasurementMode.INSTRUMENT_TRACE):
                raise PaneUserPlanError("tinySA pane is a device trace, not an SDR receiver")
            if type(draft.start_hz) not in {int, float} or type(draft.stop_hz) not in {int, float}:
                raise PaneUserPlanError("tinySA range must be numeric")
            if not float(draft.start_hz).is_integer() or not float(draft.stop_hz).is_integer():
                raise PaneUserPlanError("tinySA endpoints must be exact whole hertz")
            revision = selection_revisions[source_id]
            tiny_request = TinySaSweepRequest(
                choice, revision, int(draft.start_hz), int(draft.stop_hz),
                draft.points, timeout_s=60.0, repeat_until_stop=True)
            profile = TinySaTracePaneProfile(
                draft.points, draft.stop_hz - draft.start_hz,
                "instrument-default", trace_cost, request_template=tiny_request)
        if draft.stop_hz - draft.start_hz > profile.usable_capture_span_hz:
            raise PaneUserPlanError("pane range exceeds the source's current usable capture span")
        snapshot = choice.binding.snapshot
        if (snapshot is not None and snapshot.tuning_ranges_hz
                and not any(item.minimum <= draft.start_hz < draft.stop_hz <= item.maximum
                            for item in snapshot.tuning_ranges_hz)):
            raise PaneUserPlanError("pane range is outside the selected device's observed tuning range")
        pane_id = f"pane-{draft.number}"
        profiles[pane_id] = profile
        by_source[source_id].append((draft, profile))

    modes: dict[str, ReceiverBindingMode] = {}
    for source_id, entries in by_source.items():
        if len(entries) == 1:
            mode = ReceiverBindingMode.DEDICATED_PARALLEL
        else:
            same_profile = len({profile.compatibility_key for _, profile in entries}) == 1
            envelope = max(float(draft.stop_hz) for draft, _ in entries if draft.stop_hz is not None) - min(
                float(draft.start_hz) for draft, _ in entries if draft.start_hz is not None)
            mode = (ReceiverBindingMode.SHARED_CAPTURE if same_profile
                    and envelope <= entries[0][1].usable_capture_span_hz
                    else ReceiverBindingMode.TIME_SLICED)
        modes[source_id] = mode
    slots_list: list[PaneLayoutSlot] = []
    for draft in drafts:
        if draft.source_id is None:
            slots_list.append(PaneLayoutSlot(draft.number))
            continue
        assert draft.start_hz is not None and draft.stop_hz is not None
        profile = profiles[f"pane-{draft.number}"]
        crop_start = (profile.pane_crop_start_hz if isinstance(profile, (Ad936xSweepPaneProfile, HackrfSweepPaneProfile))
                      else draft.start_hz)
        crop_stop = (profile.pane_crop_stop_hz if isinstance(profile, (Ad936xSweepPaneProfile, HackrfSweepPaneProfile))
                     else draft.stop_hz)
        slots_list.append(PaneLayoutSlot(draft.number, SweepPaneRequest(
            f"pane-{draft.number}", endpoints[draft.source_id],
            crop_start, crop_stop, profile_id=f"pane-{draft.number}",
            requested_binding_mode=modes[draft.source_id])))
    slots = tuple(slots_list)
    layout = compile_pane_layout(slots, tuple(groups), profiles)
    initial_ad: list[tuple[str, LiveConfiguration]] = []
    assert layout.schedule is not None
    for resource_schedule in layout.schedule.resources:
        source_id = source_order[int(resource_schedule.physical_stream_resource_id.rsplit("-", 1)[1]) - 1]
        if selected[source_id].family is not DeviceFamily.AD936X:
            continue
        job = resource_schedule.jobs[0]
        profile = job.profile
        if isinstance(profile, Ad936xSweepPaneProfile):
            initial_ad.append((resource_schedule.physical_stream_resource_id, profile.configuration))
            continue
        assert isinstance(profile, PaneCaptureProfile)
        initial_ad.append((resource_schedule.physical_stream_resource_id, LiveConfiguration(
            center_hz=(job.start_hz + job.stop_hz) / 2.0,
            sample_rate_hz=profile.sample_rate_hz,
            analog_bandwidth_hz=profile.analog_bandwidth_hz,
            gain_db=profile.manual_gain_db or 20.0,
            fft_size=profile.fft_size,
            overlap_ratio=1.0 - profile.hop_size / profile.fft_size,
            detector=profile.detector, window=profile.window,
            snapshot_rate_hz=240.0, backend=BackendKind.CPU,
            persistence_enabled=False, persistence_mode="disabled")))
    return PaneUserPlan(layout, tuple(groups),
                        tuple((resource_for[source], source) for source in source_order),
                        tuple(initial_ad), tuple(ad_geometry))


__all__ = ["PaneSlotDraft", "PaneUserPlan", "PaneUserPlanError", "compile_user_pane_plan"]
