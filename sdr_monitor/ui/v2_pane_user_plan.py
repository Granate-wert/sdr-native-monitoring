"""Compile a user's four-slot source/range draft into the existing pane owners.

This module is pure: it does not discover devices, configure RF, acquire a
lease or start a receiver.  Source choices and selection revisions must come
from the explicit, freshly staged product graphs.  A repeated source ID maps
to one resource; it is never mistaken for a second receiver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from math import ceil, isfinite
from typing import Mapping

from sdr_monitor.domain.analyzer_sources import AnalyzerSourceChoice
from sdr_monitor.domain.analyzer_resources import AnalyzerGeometryPreflight
from sdr_monitor.domain.calibration import CalibrationProfile
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import DeviceFamily
from sdr_monitor.domain.hackrf_live import HackrfLiveRequest
from sdr_monitor.domain.hackrf_sweep import HackrfSweepRequest
from sdr_monitor.domain.identity import SourceId
from sdr_monitor.domain.live import BackendKind, LiveConfiguration
from sdr_monitor.domain.pane_scheduler import (
    Ad936xSweepPaneProfile, CaptureEpochCost, CaptureMeasurementMode, HackrfRtbwPaneProfile,
    HackrfSweepPaneProfile, PaneCaptureProfile, PaneLayout,
    PaneLayoutSlot, PaneProfile, PaneRevisitEstimate, PaneScheduleDeadlineError,
    TinySaTracePaneProfile, compile_pane_layout,
)
from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup, ReceiverBindingMode, ReceiverChainSelection,
    PaneSchedulerPolicy, ReceiverEndpoint, SchedulerPolicyKind,
    SpectrumTraceEndpoint, SweepPaneRequest,
)
from sdr_monitor.domain.tinysa_analyzer import TinySaSweepRequest
from sdr_monitor.domain.tinysa_settings import TinySaInputMode, TinySaSweepSettingsPlan
from sdr_monitor.services.native_continuous_sweep_factory import NativeContinuousSweepPlanFactory


class PaneUserPlanError(ValueError):
    """A bounded, user-facing draft cannot be admitted as an exact plan."""

    def __init__(self, message: str, *,
                 revisit_violations: tuple[PaneRevisitEstimate, ...] = ()) -> None:
        super().__init__(message)
        self.revisit_violations = revisit_violations


class RtbwBandPolicy(str, Enum):
    """Explicit receive/filter intent, not a calibrated passband claim."""

    EDGE_TRIMMED = "edge_trimmed"
    FULL_RECEIVE = "full_receive"


@dataclass(frozen=True, slots=True)
class TinySaPaneIntent:
    """Immutable UI intent, validated against the FRESH source during Stage.

    This is not an observed firmware/input state. The same TinySaSweepRequest
    and serial owner perform model/contract admission and post-pass readback.
    """

    settings: TinySaSweepSettingsPlan = field(default_factory=TinySaSweepSettingsPlan)
    input_mode: TinySaInputMode = TinySaInputMode.PRESERVE
    readback: bool = False
    external_correction: CalibrationProfile | None = None
    frontend_chain: str = "unknown"
    allow_correction_extrapolation: bool = False

    def __post_init__(self) -> None:
        if (not isinstance(self.settings, TinySaSweepSettingsPlan)
                or not isinstance(self.input_mode, TinySaInputMode)
                or type(self.readback) is not bool
                or type(self.allow_correction_extrapolation) is not bool
                or self.external_correction is not None
                and not isinstance(self.external_correction, CalibrationProfile)):
            raise PaneUserPlanError("tinySA pane settings require typed immutable intent")

    @property
    def readback_settings(self) -> bool:
        # Match the single-source UI: changed settings/correction always query
        # actual values, even if the preserve-only checkbox is not selected.
        return bool(self.readback or self.settings.commands
                    or self.input_mode is not TinySaInputMode.PRESERVE
                    or self.external_correction is not None)


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
    priority: int = field(default=1, kw_only=True)
    maximum_revisit_s: float | None = field(default=None, kw_only=True)
    tinysa: TinySaPaneIntent | None = field(default=None, kw_only=True)
    rtbw_band: RtbwBandPolicy = field(default=RtbwBandPolicy.EDGE_TRIMMED, kw_only=True)

    def __post_init__(self) -> None:
        if type(self.number) is not int or not 1 <= self.number <= 4:
            raise PaneUserPlanError("pane number must be in [1, 4]")
        if type(self.network_discovery) is not bool:
            raise PaneUserPlanError("pane network discovery intent must be explicit")
        if self.tinysa is not None and not isinstance(self.tinysa, TinySaPaneIntent):
            raise PaneUserPlanError("pane tinySA settings require typed intent")
        if not isinstance(self.rtbw_band, RtbwBandPolicy):
            raise PaneUserPlanError("RTBW receive band requires typed explicit intent")
        if type(self.priority) is not int or not 1 <= self.priority <= 100:
            raise PaneUserPlanError("pane scheduling priority must be an integer in [1, 100]")
        if self.maximum_revisit_s is not None and (
                type(self.maximum_revisit_s) not in {int, float}
                or not 0 < self.maximum_revisit_s <= 3600
                or not isfinite(self.maximum_revisit_s)):
            raise PaneUserPlanError("pane maximum revisit must be finite in (0, 3600] seconds")
        if self.measurement_mode is not None:
            try:
                object.__setattr__(self, "measurement_mode", CaptureMeasurementMode(self.measurement_mode))
            except ValueError:
                raise PaneUserPlanError("pane measurement mode is not supported") from None
        if self.source_id is None:
            if (self.start_hz is not None or self.stop_hz is not None
                    or self.measurement_mode is not None or self.priority != 1
                    or self.maximum_revisit_s is not None or self.tinysa is not None
                    or self.rtbw_band is not RtbwBandPolicy.EDGE_TRIMMED):
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
                or type(self.fft_size) is not int or self.fft_size not in {1024, 2048, 4096, 16384}
                or type(self.points) is not int or not 2 <= self.points <= 10001):
            raise PaneUserPlanError("pane sample rate, FFT or trace points are outside the qualified draft choices")

    @property
    def scheduler_policy(self) -> PaneSchedulerPolicy:
        """Legacy minimum_revisit_s means a MAXIMUM permitted visit interval."""
        kind = (SchedulerPolicyKind.MINIMUM_REVISIT if self.maximum_revisit_s is not None
                else SchedulerPolicyKind.WEIGHTED if self.priority != 1
                else SchedulerPolicyKind.EQUAL)
        return PaneSchedulerPolicy(kind, self.priority, self.maximum_revisit_s)


@dataclass(frozen=True, slots=True)
class PaneSchedulingIntent:
    pane_id: str
    requested: PaneSchedulerPolicy
    effective: PaneSchedulerPolicy


def _shared_scheduler_policy(entries: list[tuple[PaneSlotDraft, PaneProfile]]) -> PaneSchedulerPolicy:
    # A common capture has one schedule, not a second RX for a view with a
    # different priority. Preserve individual requests beside this explicitly
    # previewed max-weight/strictest-deadline merge; never relax a deadline.
    weight = max(draft.priority for draft, _profile in entries)
    deadlines = [draft.maximum_revisit_s for draft, _profile in entries
                 if draft.maximum_revisit_s is not None]
    deadline = min(deadlines) if deadlines else None
    kind = (SchedulerPolicyKind.MINIMUM_REVISIT if deadline is not None
            else SchedulerPolicyKind.WEIGHTED if weight != 1 else SchedulerPolicyKind.EQUAL)
    return PaneSchedulerPolicy(kind, weight, deadline)


@dataclass(frozen=True, slots=True)
class PaneUserPlan:
    layout: PaneLayout
    groups: tuple[AcquisitionGroup, ...]
    resource_sources: tuple[tuple[str, str], ...]
    initial_ad_configurations: tuple[tuple[str, LiveConfiguration], ...]
    ad_sweep_geometry: tuple[tuple[str, AnalyzerGeometryPreflight], ...] = ()
    hackrf_sweep_geometry: tuple[tuple[str, AnalyzerGeometryPreflight], ...] = ()
    hackrf_hardware_ranges: tuple[tuple[str, int, int], ...] = ()
    scheduler_intents: tuple[PaneSchedulingIntent, ...] = ()


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
    hf_geometry: list[tuple[str, AnalyzerGeometryPreflight]] = []
    hf_ranges: list[tuple[str, int, int]] = []
    by_source: dict[str, list[tuple[PaneSlotDraft, PaneProfile]]] = {source: [] for source in source_order}
    for draft in drafts:
        if draft.source_id is None:
            continue
        source_id = draft.source_id
        choice = selected[source_id]
        if draft.tinysa is not None and choice.family is not DeviceFamily.TINYSA:
            raise PaneUserPlanError("tinySA settings cannot be attached to an SDR pane")
        if draft.rtbw_band is not RtbwBandPolicy.EDGE_TRIMMED and (
                choice.family not in {DeviceFamily.AD936X, DeviceFamily.HACKRF}
                or draft.measurement_mode not in (None, CaptureMeasurementMode.RTBW)):
            raise PaneUserPlanError("wide receive band is only an explicit SDR RTBW intent")
        assert draft.start_hz is not None and draft.stop_hz is not None
        if draft.fft_size == 2048 and not (
                choice.family is DeviceFamily.HACKRF
                and draft.measurement_mode is CaptureMeasurementMode.SWEEP):
            raise PaneUserPlanError("2048 is a physical FFT choice for the qualified HackRF Sweep pane")
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
                if draft.rtbw_band is RtbwBandPolicy.FULL_RECEIVE:
                    rf_bandwidth = min(draft.sample_rate_hz, 56_000_000.0)
                    usable = rf_bandwidth
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
                    raise PaneUserPlanError("HackRF Sweep requires whole MHz, admitted FFT and reduced-data memory budget") from None
                if sweep_request.start_hz != draft.start_hz or sweep_request.stop_hz != draft.stop_hz:
                    raise PaneUserPlanError("HackRF Sweep endpoints must be exact whole MHz")
                sweep_cost = CaptureEpochCost(0.01, 0.01, 1.0, 0.005, 0.005)
                profile = HackrfSweepPaneProfile(sweep_request, sweep_cost)
                hf_geometry.append((f"pane-{draft.number}", sweep_request.geometry))
                hf_ranges.append((f"pane-{draft.number}", sweep_request.start_hz, sweep_request.hardware_stop_hz))
            else:
                if draft.measurement_mode not in (None, CaptureMeasurementMode.RTBW):
                    raise PaneUserPlanError("HackRF pane mode is not supported")
                if draft.sample_rate_hz not in {16_000_000.0, 20_000_000.0}:
                    raise PaneUserPlanError("HackRF pane supports the listed 16 or 20 MS/s profiles")
                filter_hz = 14_000_000 if draft.sample_rate_hz == 16_000_000.0 else 15_000_000
                usable = 10_000_000.0
                if draft.rtbw_band is RtbwBandPolicy.FULL_RECEIVE:
                    filter_hz = 14_000_000 if draft.sample_rate_hz == 16_000_000.0 else 20_000_000
                    usable = float(filter_hz)
                hackrf_request = HackrfLiveRequest(
                    center, draft.sample_rate_hz,
                    filter_hz,
                    16, 20, fft_size=draft.fft_size, hop_size=draft.fft_size // 2,
                    detector="peak", source_id=SourceId(source_id))
                profile = HackrfRtbwPaneProfile(hackrf_request, usable, cost)
        else:
            if draft.measurement_mode not in (None, CaptureMeasurementMode.INSTRUMENT_TRACE):
                raise PaneUserPlanError("tinySA pane is a device trace, not an SDR receiver")
            if type(draft.start_hz) not in {int, float} or type(draft.stop_hz) not in {int, float}:
                raise PaneUserPlanError("tinySA range must be numeric")
            if not float(draft.start_hz).is_integer() or not float(draft.stop_hz).is_integer():
                raise PaneUserPlanError("tinySA endpoints must be exact whole hertz")
            revision = selection_revisions[source_id]
            intent = draft.tinysa or TinySaPaneIntent()
            tiny_request = TinySaSweepRequest(
                choice, revision, int(draft.start_hz), int(draft.stop_hz),
                draft.points, timeout_s=60.0, repeat_until_stop=True,
                settings=intent.settings, input_mode=intent.input_mode,
                readback_settings=intent.readback_settings,
                external_correction=intent.external_correction,
                frontend_chain=intent.frontend_chain,
                allow_correction_extrapolation=intent.allow_correction_extrapolation)
            profile = TinySaTracePaneProfile(
                draft.points, draft.stop_hz - draft.start_hz,
                "tinysa-request-v1", trace_cost, request_template=tiny_request)
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
    shared_policies: dict[str, PaneSchedulerPolicy] = {}
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
        if mode is ReceiverBindingMode.SHARED_CAPTURE:
            shared_policies[source_id] = _shared_scheduler_policy(entries)
    slots_list: list[PaneLayoutSlot] = []
    scheduler_intents: list[PaneSchedulingIntent] = []
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
        requested_policy = draft.scheduler_policy
        effective_policy = shared_policies.get(draft.source_id, requested_policy)
        scheduler_intents.append(PaneSchedulingIntent(
            f"pane-{draft.number}", requested_policy, effective_policy))
        slots_list.append(PaneLayoutSlot(draft.number, SweepPaneRequest(
            f"pane-{draft.number}", endpoints[draft.source_id],
            crop_start, crop_stop, profile_id=f"pane-{draft.number}",
            requested_binding_mode=modes[draft.source_id], scheduler_policy=effective_policy)))
    slots = tuple(slots_list)
    try:
        layout = compile_pane_layout(slots, tuple(groups), profiles)
    except PaneScheduleDeadlineError as error:
        raise PaneUserPlanError("pane revisit targets are infeasible in the declared cost model",
                                revisit_violations=error.violations) from None
    initial_ad: list[tuple[str, LiveConfiguration]] = []
    assert layout.schedule is not None
    for resource_schedule in layout.schedule.resources:
        source_id = source_order[int(resource_schedule.physical_stream_resource_id.rsplit("-", 1)[1]) - 1]
        if selected[source_id].family is not DeviceFamily.AD936X:
            continue
        first_capture = resource_schedule.slots[0].capture_id
        job = next(item for item in resource_schedule.jobs if item.capture_id == first_capture)
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
                        tuple(initial_ad), tuple(ad_geometry), tuple(hf_geometry), tuple(hf_ranges),
                        tuple(scheduler_intents))


__all__ = ["RtbwBandPolicy", "TinySaPaneIntent", "PaneSlotDraft", "PaneSchedulingIntent", "PaneUserPlan", "PaneUserPlanError", "compile_user_pane_plan"]
