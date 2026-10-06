"""30.72 pane compiler and observed MOCK capabilities; no physical RX."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.ad936x_pane_profiles import (
    ad936x_pane_profile_supported, ad936x_pane_rate_choices, ad936x_pane_rate_profile,
)
from sdr_monitor.domain.device_capabilities import CapabilityEvidenceOrigin, CapabilityField, CapabilityRange, DeviceFamily
from sdr_monitor.domain.pane_scheduler import CaptureMeasurementMode
from sdr_monitor.domain.live import LiveConfiguration
from sdr_monitor.domain.receiver_topology import (
    IqComponent, ReceiverBindingMode, ReceiverChain, ReceiverChainSelection,
    ReceiverTopologySnapshot, StreamScanElement,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_catalog import SourceCapabilityCatalog
from sdr_monitor.services.source_capability_providers import NativeLiveCapabilityProvider
from sdr_monitor.ui.v2_application_graph import build_v2_analyzer_application_graph
from sdr_monitor.ui.v2_pane_user_plan import (
    PanePairedSelectionReceipt, PaneSlotDraft, PaneUserPlanError, RtbwBandPolicy, compile_user_pane_plan,
)
from sdr_monitor.ui.v2_pane_graph_pool import PaneProductGraphPool
from sdr_monitor.ui.v2_pane_user_stage import prepare_user_pane_session, discard_user_pane_session
from tests.test_app07_ad936x_rtbw_pane_owner import _UnusedSweep
from tests.ui_v2.test_app07_three_concrete_owners import _ObservedReadbackNative


class _ThirtyNative(_ObservedReadbackNative):
    def PlutoDevice(self, uri, timeout_ms, *, expected_serial=None):
        device = super().PlutoDevice(uri, timeout_ms, expected_serial=expected_serial)
        original = device.capabilities

        def capabilities():
            result = original()
            result.sample_rate_ranges_hz = (SimpleNamespace(minimum=2.083333e6, maximum=30.72e6, step=0.),)
            return result

        device.capabilities = capabilities
        return device


def thirty_graph():
    """Owned MOCK discovery/readback produces the SAME capability snapshot."""
    native = _ThirtyNative(serial="mock-thirty-pluto", uri="ip:mock-thirty.local")
    live = NativeLiveSessionService(native)
    catalog = SourceCapabilityCatalog((NativeLiveCapabilityProvider(live),),
                                      control_transaction=live.capability_control_transaction)
    graph = build_v2_analyzer_application_graph(SimpleNamespace(
        live_sdr=live, analyzer_display=_UnusedSweep(), device_catalog=catalog))
    return native, graph


class Ad936xRateProfileTests(unittest.TestCase):
    def test_same_live_staging_preserves_admitted_30mhz_filter_without_start(self):
        requested = LiveConfiguration(center_hz=115e6, sample_rate_hz=30.72e6,
                                      analog_bandwidth_hz=30e6, gain_db=20., fft_size=4096)
        snapshot = self.graph.live.apply_configuration(requested)
        self.assertIsNone(snapshot.error)
        self.assertIsNotNone(snapshot.applied)
        self.assertEqual(snapshot.applied.applied, requested)
        self.assertEqual(snapshot.applied.adjustments, ())
        self.assertFalse(self.graph.live.is_running())
        self.assertEqual(self.native.engines, [])

    def setUp(self):
        self.native, self.graph = thirty_graph()
        source = self.graph.live.discover(startup=True)[0]
        self.graph.live.select_device(source.device_id)
        selected = self.graph.live.current_source_selection()
        self.source, self.revision = selected.selected, selected.revision
        self.capability = self.source.binding.snapshot

    def tearDown(self):
        self.graph.live.shutdown()

    def compile(self, drafts, *, source=None, pair=None):
        source = self.source if source is None else source
        return compile_user_pane_plan(tuple(drafts), {source.device_id: source},
            {source.device_id: self.revision},
            paired_selections={} if pair is None else {source.device_id: pair})

    def draft(self, **changes):
        return replace(PaneSlotDraft(1, self.source.device_id, 100e6, 130e6,
                                    sample_rate_hz=30.72e6), **changes)

    def test_rate_choices_are_observed_not_chip_label_or_transport(self):
        self.assertEqual(ad936x_pane_rate_choices(self.capability), (20e6, 30.72e6))
        self.assertEqual(ad936x_pane_rate_choices(self.capability, sweep=True), (30.72e6,))
        self.assertEqual(ad936x_pane_rate_choices(replace(self.capability, label="Not a Pluto AD9363")),
                         (20e6, 30.72e6))
        self.assertEqual(ad936x_pane_rate_choices(None), (20e6, 61.44e6))

    def test_new_profile_requires_runtime_readback_for_both_ranges(self):
        profile = ad936x_pane_rate_profile(30.72e6)
        for field in (CapabilityField.SAMPLE_RATE_RANGE, CapabilityField.ANALOG_BANDWIDTH_RANGE):
            for origin in (CapabilityEvidenceOrigin.VENDOR_DECLARATION, CapabilityEvidenceOrigin.USER_DECLARATION):
                capability = replace(self.capability, evidence=tuple(
                    replace(item, origin=origin) if item.field is field else item
                    for item in self.capability.evidence))
                with self.subTest(field=field, origin=origin):
                    self.assertFalse(ad936x_pane_profile_supported(capability, profile))
        self.assertFalse(ad936x_pane_profile_supported(None, profile))

    def test_missing_ranges_and_noncontiguous_rate_ranges_refuse(self):
        for field, attribute in ((CapabilityField.SAMPLE_RATE_RANGE, "sample_rate_ranges_hz"),
                                 (CapabilityField.ANALOG_BANDWIDTH_RANGE, "analog_bandwidth_ranges_hz")):
            missing = replace(self.capability, **{attribute: ()}, evidence=tuple(
                item for item in self.capability.evidence if item.field is not field))
            with self.subTest(field=field):
                self.assertFalse(ad936x_pane_profile_supported(missing, ad936x_pane_rate_profile(30.72e6)))
        gap = replace(self.capability, sample_rate_ranges_hz=(CapabilityRange(2e6, 20e6, "Hz"),
                                                            CapabilityRange(40e6, 61.44e6, "Hz")))
        self.assertFalse(ad936x_pane_profile_supported(gap, ad936x_pane_rate_profile(30.72e6)))

    def test_rtbw_exact_trimmed_and_full_receive_no_hidden_downgrade(self):
        for band, stop, bandwidth in ((RtbwBandPolicy.EDGE_TRIMMED, 130e6, 30e6),
                                      (RtbwBandPolicy.FULL_RECEIVE, 130e6, 30e6)):
            with self.subTest(band=band):
                plan = self.compile((self.draft(stop_hz=stop, rtbw_band=band),))
                profile = plan.layout.schedule.resources[0].jobs[0].profile
                self.assertEqual((profile.sample_rate_hz, profile.analog_bandwidth_hz,
                                  profile.usable_capture_span_hz, profile.fft_size, profile.hop_size),
                                 (30.72e6, bandwidth, stop - 100e6, 4096, 2048))
                self.assertEqual(plan.initial_ad_configurations[0][1].sample_rate_hz, 30.72e6)
                with self.assertRaises(PaneUserPlanError):
                    self.compile((self.draft(stop_hz=stop + 1, rtbw_band=band),))
        self.assertEqual(self.native.engines, [])

    def test_filter_limits_refuse_profile_without_silent_narrowing(self):
        capability = replace(self.capability, analog_bandwidth_ranges_hz=(CapabilityRange(.2e6, 20e6, "Hz"),))
        source = replace(self.source, binding=replace(self.source.binding, snapshot=capability))
        for band in (RtbwBandPolicy.EDGE_TRIMMED, RtbwBandPolicy.FULL_RECEIVE):
            with self.subTest(band=band), self.assertRaises(PaneUserPlanError):
                self.compile((self.draft(rtbw_band=band),), source=source)

    def test_exact_capability_boundaries_and_legacy_6144_geometry(self):
        profile = ad936x_pane_rate_profile(30.72e6)
        self.assertTrue(ad936x_pane_profile_supported(self.capability, profile))
        for changes in ({"sample_rate_ranges_hz": (CapabilityRange(2e6, 30.72e6 - 1, "Hz"),)},
                        {"analog_bandwidth_ranges_hz": (CapabilityRange(.2e6, 30e6 - 1, "Hz"),)}):
            self.assertFalse(ad936x_pane_profile_supported(replace(self.capability, **changes), profile))
        capability = replace(self.capability, sample_rate_ranges_hz=(CapabilityRange(2e6, 61.44e6, "Hz"),))
        source = replace(self.source, binding=replace(self.source.binding, snapshot=capability))
        sweep = self.compile((self.draft(sample_rate_hz=61.44e6, stop_hz=220e6,
                                         measurement_mode=CaptureMeasurementMode.SWEEP),), source=source)
        sweep_profile = sweep.layout.schedule.resources[0].jobs[0].profile
        self.assertEqual((sweep_profile.configuration.sample_rate_hz, sweep_profile.configuration.analog_bandwidth_hz,
                          sweep_profile.configuration.fft_size), (61.44e6, 40e6, 8192))
        self.assertEqual((sweep_profile.request_template.usable_window_hz, sweep_profile.request_template.overlap_hz),
                         (36e6, 2e6))
        full = self.compile((self.draft(sample_rate_hz=61.44e6, stop_hz=156e6,
                                        rtbw_band=RtbwBandPolicy.FULL_RECEIVE),), source=source)
        self.assertEqual(full.initial_ad_configurations[0][1].analog_bandwidth_hz, 56e6)

    def test_unsupported_6144_does_not_fall_back_and_all_staging_is_inert(self):
        for mode in (CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP):
            with self.subTest(mode=mode), self.assertRaisesRegex(PaneUserPlanError, "observed capabilities"):
                self.compile((self.draft(sample_rate_hz=61.44e6, measurement_mode=mode),))
        self.assertEqual(self.native.engines, [])
        self.assertIsNone(self.graph.live.current_snapshot().applied)

    def test_sweep_analysis_n_inside_default_30mhz_uses_explicit_physical_fft(self):
        for bins, physical in ((1024, 2048), (4096, 8192), (16384, 32768)):
            with self.subTest(bins=bins):
                plan = self.compile((self.draft(stop_hz=220e6, measurement_mode=CaptureMeasurementMode.SWEEP,
                                                fft_size=bins),))
                profile = plan.layout.schedule.resources[0].jobs[0].profile
                self.assertEqual((profile.configuration.sample_rate_hz, profile.configuration.analog_bandwidth_hz,
                                  profile.configuration.fft_size), (30.72e6, 30e6, physical))
                self.assertEqual((profile.request_template.usable_window_hz, profile.request_template.overlap_hz,
                                  profile.request_template.analysis_bins_per_usable_window), (30e6, 1e6, bins))
                self.assertEqual(plan.initial_ad_configurations[0][1], profile.configuration)
        self.assertEqual(self.native.engines, [])

    def test_actual_stage_composition_uses_fresh_30_capabilities_without_configure_or_rx(self):
        for mode in (CaptureMeasurementMode.RTBW, CaptureMeasurementMode.SWEEP):
            native, graph = thirty_graph()
            prepared = None
            try:
                draft = self.draft(measurement_mode=mode, network_discovery=True)
                pool = PaneProductGraphPool(lambda _resource: graph)
                prepared = prepare_user_pane_session((draft,), pool_factory=lambda: pool)
                self.assertEqual(prepared.plan.initial_ad_configurations[0][1].sample_rate_hz, 30.72e6)
                self.assertEqual(len(prepared.preview), 1)
                self.assertEqual(native.engines, [])
                self.assertIsNone(graph.live.current_snapshot().applied)
            finally:
                if prepared is not None:
                    discard_user_pane_session(prepared)
                else:
                    graph.live.shutdown()

    def pair(self):
        before = self.graph.live.current_snapshot()
        topology = ReceiverTopologySnapshot("mock:physical", "ad9361", "mock-board", ("voltage0", "voltage1"),
            tuple(StreamScanElement(f"voltage{index}", chain, component, index, 16, 12, 0, True, False)
                  for index, (chain, component) in enumerate((
                      (ReceiverChain.RX1, IqComponent.IN_PHASE), (ReceiverChain.RX1, IqComponent.QUADRATURE),
                      (ReceiverChain.RX2, IqComponent.IN_PHASE), (ReceiverChain.RX2, IqComponent.QUADRATURE)))))
        device = replace(before.device, capabilities=replace(before.device.capabilities, receiver_topology=topology))
        return PanePairedSelectionReceipt(self.source, self.revision, replace(before, device=device))

    def test_paired_rtbw_one_shared_job_and_exact_30mhz_window(self):
        drafts = (self.draft(stop_hz=110e6, rtbw_band=RtbwBandPolicy.FULL_RECEIVE),
                  self.draft(number=2, start_hz=120e6, stop_hz=130e6, rtbw_band=RtbwBandPolicy.FULL_RECEIVE,
                             receiver_selection=ReceiverChainSelection.RX2))
        plan = self.compile(drafts, pair=self.pair())
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        self.assertIs(resource.jobs[0].mode, ReceiverBindingMode.SHARED_CAPTURE)
        self.assertEqual(len(resource.jobs[0].receiver_endpoint_ids), 2)
        self.assertEqual(plan.initial_ad_configurations[0][1].sample_rate_hz, 30.72e6)

    def test_paired_rtbw_incompatible_rate_refuses_without_owner_creation(self):
        drafts = (self.draft(stop_hz=108e6), self.draft(number=2, start_hz=108e6, stop_hz=116e6,
                   receiver_selection=ReceiverChainSelection.RX2))
        for changes in ({"sample_rate_hz": 20e6}, {"sample_rate_hz": 61.44e6}):
            with self.subTest(changes=changes), self.assertRaises(PaneUserPlanError):
                self.compile((drafts[0], replace(drafts[1], **changes)), pair=self.pair())
        self.assertEqual(self.native.engines, [])

    def test_paired_sweep_common_profile_and_incompatible_rates_refuse(self):
        drafts = (self.draft(stop_hz=160e6, measurement_mode=CaptureMeasurementMode.SWEEP),
                  self.draft(number=2, start_hz=140e6, stop_hz=220e6, measurement_mode=CaptureMeasurementMode.SWEEP,
                             receiver_selection=ReceiverChainSelection.RX2))
        plan = self.compile(drafts, pair=self.pair())
        resource = plan.layout.schedule.resources[0]
        self.assertEqual(len(resource.jobs), 1)
        self.assertEqual(len(resource.jobs[0].receiver_endpoint_ids), 2)
        profile = resource.jobs[0].profile
        self.assertEqual((profile.configuration.sample_rate_hz, profile.configuration.fft_size), (30.72e6, 8192))
        self.assertEqual((profile.request_template.start_hz, profile.request_template.stop_hz), (100e6, 220e6))
        self.assertEqual(profile.request_template.usable_window_hz, 30e6)
        self.assertEqual(self.native.engines, [])
        with self.assertRaises(PaneUserPlanError):
            self.compile((drafts[0], replace(drafts[1], sample_rate_hz=61.44e6)), pair=self.pair())
        for rate in (16e6, 20e6, 61.44e6):
            with self.subTest(rate=rate), self.assertRaises(PaneUserPlanError):
                self.compile(tuple(replace(draft, sample_rate_hz=rate) for draft in drafts), pair=self.pair())

    def test_explicit_sweep_window_not_hardcoded18_and_never_changes_fs_or_filter(self):
        for window, physical in ((30e6, 8192), (24e6, 8192), (18e6, 8192), (8e6, 16384)):
            with self.subTest(window=window):
                plan = self.compile((self.draft(stop_hz=220e6, measurement_mode=CaptureMeasurementMode.SWEEP,
                                                sweep_window_hz=window),))
                profile = plan.layout.schedule.resources[0].jobs[0].profile
                self.assertEqual(profile.configuration.sample_rate_hz, 30.72e6)
                self.assertEqual(profile.configuration.analog_bandwidth_hz, 30e6)
                self.assertEqual(profile.configuration.fft_size, physical)
                self.assertEqual(profile.request_template.usable_window_hz, window)
                self.assertEqual(profile.request_template.analysis_bins_per_usable_window, 4096)
        for invalid in (True, "18", 0, -1, float("nan"), 36e6 + 1):
            with self.subTest(invalid=invalid), self.assertRaises(PaneUserPlanError):
                self.draft(measurement_mode=CaptureMeasurementMode.SWEEP, sweep_window_hz=invalid)
        for window in (1e6, 30e6 + 1, 36e6):
            with self.subTest(window=window), self.assertRaises(PaneUserPlanError):
                self.compile((self.draft(measurement_mode=CaptureMeasurementMode.SWEEP, sweep_window_hz=window),))
        with self.assertRaises(PaneUserPlanError):
            self.draft(sweep_window_hz=18e6)

    def test_paired_sweep_common_window_is_resolved_not_inferred_from_rate(self):
        first = self.draft(stop_hz=160e6, measurement_mode=CaptureMeasurementMode.SWEEP)
        second = self.draft(number=2, start_hz=140e6, stop_hz=220e6,
                            measurement_mode=CaptureMeasurementMode.SWEEP,
                            receiver_selection=ReceiverChainSelection.RX2, sweep_window_hz=30e6)
        plan = self.compile((first, second), pair=self.pair())
        self.assertEqual(len(plan.layout.schedule.resources[0].jobs), 1)
        with self.assertRaisesRegex(PaneUserPlanError, "common analysis window"):
            self.compile((first, replace(second, sweep_window_hz=18e6)), pair=self.pair())
        custom = self.compile((replace(first, sweep_window_hz=18e6), replace(second, sweep_window_hz=18e6)),
                              pair=self.pair())
        self.assertEqual(custom.layout.schedule.resources[0].jobs[0].profile.request_template.usable_window_hz, 18e6)
        self.assertEqual(self.native.engines, [])

    def test_explicit_window_is_not_silently_ignored_by_other_families(self):
        for family in (DeviceFamily.HACKRF, DeviceFamily.TINYSA):
            source = replace(self.source, operational_routes=(), runtime=None,
                             binding=replace(self.source.binding,
                                             family=family, snapshot=None, calibration_identity=None))
            with self.subTest(family=family), self.assertRaisesRegex(PaneUserPlanError, "only to AD936x"):
                self.compile((self.draft(measurement_mode=CaptureMeasurementMode.SWEEP,
                                         sweep_window_hz=18e6),), source=source)
