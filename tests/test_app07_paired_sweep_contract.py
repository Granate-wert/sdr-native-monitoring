"""Pure paired retuning admission protocol, NOT native or physical Sweep proof."""

from dataclasses import replace
import math
import unittest
from unittest.mock import patch

from sdr_monitor.domain.continuous_sweep_geometry import sweep_segment_count, sweep_step_geometry
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind, CapabilityEvidence, CapabilityEvidenceOrigin, CapabilityField,
    CapabilityTransport, DeviceCapabilitySnapshot, DeviceFamily,
)
from sdr_monitor.domain.identity import ConfigurationGeneration, FrameSequence, SessionId, TimestampQuality
from sdr_monitor.domain.live import (
    AppliedLiveConfiguration, BackendKind, DeviceCapabilities, DeviceDescriptor,
    DeviceTransport, LiveConfiguration, LiveSessionState, LiveSnapshot,
)
from sdr_monitor.domain.paired_live import PairedLiveRequest
from sdr_monitor.domain.paired_sweep import (
    PairedSweepRequest, PairedSweepStepIdentity, PairedSweepStepObservation, PairedSweepStepPair,
    PairedSweepGainMode, PairedSweepGainReadback,
)
from sdr_monitor.domain.receiver_topology import (
    IqComponent, ReceiverChain, ReceiverChainSelection, ReceiverTopologySnapshot, StreamScanElement,
)
from sdr_monitor.domain.sweep_speed import SweepSpeedProfile


def request_fixture():
    """Build immutable observed-facts fixtures without importing Qt or an SDK."""
    topology = ReceiverTopologySnapshot("mock-device", "ad9361", "mock-board", ("voltage0", "voltage1"),
        tuple(StreamScanElement(f"voltage{index}", chain, component, index, 16, 12, 0, True, False)
              for index, (chain, component) in enumerate((
                  (ReceiverChain.RX1, IqComponent.IN_PHASE), (ReceiverChain.RX1, IqComponent.QUADRATURE),
                  (ReceiverChain.RX2, IqComponent.IN_PHASE), (ReceiverChain.RX2, IqComponent.QUADRATURE)))))
    capabilities = DeviceCapabilitySnapshot("mock-device", "sha256:" + "1" * 64, "Mock AD", DeviceFamily.AD936X,
        "mock-ad", (CapabilityTransport.USB,), (AcquisitionKind.COMPLEX_IQ,), rx_channel_count=2, shared_rx_lo=True,
        evidence=tuple(CapabilityEvidence(field, CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY, "mock-fixture")
                       for field in (CapabilityField.TRANSPORT, CapabilityField.ACQUISITION_KIND,
                                     CapabilityField.RX_CHANNEL_COUNT, CapabilityField.SHARED_RX_LO)))
    device = DeviceDescriptor("mock-device", "Mock AD", "usb:mock", DeviceTransport.USB,
        DeviceCapabilities((61.44e6,), (0., 60.), (56e6,), receiver_topology=topology),
        serial="mock-serial", identity_key=capabilities.identity_key, capability_snapshot=capabilities)
    snapshot = LiveSnapshot(ConfigurationGeneration(0), FrameSequence(0), LiveSessionState.CONNECTED,
                            device=device, session_id=SessionId("mock-session"))
    profile = LiveConfiguration(center_hz=144e6, sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
                                fft_size=4096, backend=BackendKind.CPU)
    pair = PairedLiveRequest(device.device_id, str(snapshot.session_id), topology, profile, "producer-one", "producer-two")
    return PairedSweepRequest("physical-resource", pair, ContinuousSweepPlanRequest(100e6, 220e6, epoch=4), 3, snapshot)


def pair_fixture(request, *, segment=0, epoch=5):
    geometry = sweep_step_geometry(request.sweep, segment)
    identity = PairedSweepStepIdentity(request.resource_id, request.pair.session_id, epoch, 7, segment,
        19, 8, 3, request.sweep, request.pair.configuration, request.selection_revision,
        geometry.usable_start_hz, geometry.usable_stop_hz,
        geometry.center_hz, request.pair.configuration.sample_rate_hz, 56e6,
        request.pair.configuration.fft_size)
    first = PairedSweepStepObservation(identity, ReceiverChainSelection.RX1, request.pair.primary_source_id,
        identity.center_hz, identity.sample_rate_hz, identity.fft_size, 29, 57344, 0,
        TimestampQuality.UNKNOWN, None, 8192, 2,
        PairedSweepGainReadback(ReceiverChainSelection.RX1, PairedSweepGainMode.MANUAL, 20.))
    second = replace(first, receiver_selection=ReceiverChainSelection.RX2,
                     producer_source_id=request.pair.secondary_source_id, quality_flags=8193,
                     gain=replace(first.gain, receiver_selection=ReceiverChainSelection.RX2))
    return PairedSweepStepPair(first, second)


class CommonSweepGeometryTests(unittest.TestCase):
    def test_existing_formula_and_clipped_last_step_for_many_common_plans(self):
        for window in (20e6, 36e6, 56e6):
            for overlap in (0., 1e6, 2e6):
                for span in (10e6, window, window + 1., 120e6, 5.93e9):
                    with self.subTest(window=window, overlap=overlap, span=span):
                        request = ContinuousSweepPlanRequest(70e6, 70e6 + span, window, overlap)
                        expected = max(1, math.ceil(max(0., span - window) / (window - overlap)) + 1)
                        self.assertEqual(sweep_segment_count(request), expected)
                        last = sweep_step_geometry(request, expected - 1)
                        self.assertEqual(last.usable_stop_hz, request.stop_hz)
                        self.assertEqual(last.usable_start_hz, request.start_hz + (expected - 1) * (window - overlap))
                        self.assertLessEqual(last.usable_stop_hz - last.usable_start_hz, window)
                        self.assertGreaterEqual(last.center_hz, last.usable_start_hz)
                        self.assertLessEqual(last.center_hz, last.usable_stop_hz)

    def test_exact_2048_step_bound_and_no_bool_or_out_of_range_index(self):
        request = ContinuousSweepPlanRequest(1e6, 1e6 + 2048 * 20e6, 20e6, 0.)
        self.assertEqual(sweep_segment_count(request), 2048)
        self.assertEqual(sweep_step_geometry(request, 2047).usable_stop_hz, request.stop_hz)
        with self.assertRaises(ValueError):
            sweep_segment_count(replace(request, stop_hz=request.stop_hz + 1.))
        for value in (-1, True, 1., 2048):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sweep_step_geometry(request, value)
        with self.assertRaises(ValueError):
            sweep_segment_count(ContinuousSweepPlanRequest(True, 2., 1., 0.))

    def test_finite_huge_draft_midpoint_does_not_overflow(self):
        request = ContinuousSweepPlanRequest(1e308, 1.6e308, 8e307, 0.)
        self.assertTrue(math.isfinite(sweep_step_geometry(request, 0).center_hz))
        with self.assertRaises(TypeError):
            sweep_segment_count(None)
        for value in (float("nan"), float("inf"), -1.):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ContinuousSweepPlanRequest(1e6, 2e6, value, 0.)


class PairedSweepRequestTests(unittest.TestCase):
    def setUp(self):
        self.request = request_fixture()

    def test_stage_selection_and_prepared_apply_do_not_construct_hardware(self):
        request = self.request
        self.assertIsNone(request.selected_snapshot.applied)
        with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
            request.validate_selected(request.selected_snapshot, 3)
            with self.assertRaises(ValueError):
                request.validate_applied(request.selected_snapshot, 3)
            prepared = replace(request.selected_snapshot, applied=AppliedLiveConfiguration(
                request.pair.configuration, request.pair.configuration))
            request.validate_applied(prepared, 3)
            self.assertEqual(prepared.applied.readback_fields, ())
            build.assert_not_called()

    def test_changed_session_serial_identity_capabilities_or_revision_refuses(self):
        request = self.request
        original = request.selected_snapshot
        device = original.device
        changed_devices = (
            replace(device, device_id="different"), replace(device, serial="different"),
            replace(device, identity_key="different"), replace(device, capability_snapshot=None),
            replace(device, capability_snapshot=replace(device.capability_snapshot, label="Changed observation")),
            replace(device, capabilities=replace(device.capabilities, receiver_topology=None)),
        )
        for changed in changed_devices:
            with self.subTest(device=changed), self.assertRaises(ValueError):
                request.validate_selected(replace(original, device=changed), 3)
        for revision in (True, 3., 4, -1):
            with self.subTest(revision=revision), self.assertRaises(ValueError):
                request.validate_selected(original, revision)
        with self.assertRaises(ValueError):
            request.validate_selected(replace(original, session_id=SessionId("different")), 3)

    def test_running_stage_allowed_but_apply_never_silently_stops(self):
        request = self.request
        running = replace(request.selected_snapshot, state=LiveSessionState.RUNNING, stop_required=True,
                          applied=AppliedLiveConfiguration(request.pair.configuration, request.pair.configuration))
        request.validate_selected(running, 3)
        with self.assertRaisesRegex(ValueError, "no hidden restart"):
            request.validate_applied(running, 3)
        for change in ({"state": LiveSessionState.STOPPING}, {"state": LiveSessionState.ERROR},
                       {"stop_required": True}, {"error": "worker failed"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                request.validate_selected(replace(request.selected_snapshot, **change), 3)

    def test_initial_missing_stable_identity_or_single_rx_refuses(self):
        request = self.request
        device = request.selected_snapshot.device
        topology = request.pair.topology
        single = replace(topology, phy_rx_channel_ids=("voltage0",), scan_elements=topology.scan_elements[:2])
        with self.assertRaises(ValueError):
            replace(request.pair, topology=single)
        for field in ("serial", "identity_key", "capability_snapshot"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(request, selected_snapshot=replace(request.selected_snapshot, device=replace(device, **{field: None})))

    def test_unsupported_backend_duplicate_producer_and_untyped_fields_refuse(self):
        request = self.request
        for backend in (BackendKind.AUTO, BackendKind.CUDA, BackendKind.HIP):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                replace(request, pair=replace(request.pair, configuration=replace(request.pair.configuration, backend=backend)))
        with self.assertRaises(ValueError):
            replace(request.pair, secondary_source_id=request.pair.primary_source_id)
        for field, value in (("resource_id", " "), ("selection_revision", True), ("selection_revision", 1 << 64)):
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(request, **{field: value})
        for field in ("pair", "sweep", "selected_snapshot"):
            with self.subTest(field=field), self.assertRaises(TypeError):
                replace(request, **{field: None})


class PairedSweepStepTests(unittest.TestCase):
    def setUp(self):
        self.request = request_fixture()
        self.pair = pair_fixture(self.request)

    def test_aligned_actual_pair_unknown_clock_and_distinct_quality_masks(self):
        self.pair.validate_active(self.request, self.pair.primary.identity)
        self.assertIsNone(self.pair.primary.clock_domain)
        self.assertIs(self.pair.primary.timestamp_quality, TimestampQuality.UNKNOWN)
        self.assertNotEqual(self.pair.primary.quality_flags, self.pair.secondary.quality_flags)
        for step in range(sweep_segment_count(self.request.sweep)):
            pair = pair_fixture(self.request, segment=step)
            pair.validate_active(self.request, pair.primary.identity)

    def test_actual_gain_retained_without_requested_value_alias_or_tolerance(self):
        a, b = self.pair.primary, self.pair.secondary
        actual = PairedSweepStepPair(replace(a, gain=replace(a.gain, gain_db=19.)),
                                    replace(b, gain=replace(b.gain, gain_db=19.)))
        actual.validate_active(self.request, actual.primary.identity)
        self.assertEqual(actual.primary.gain.gain_db, 19.)
        self.assertNotEqual(actual.primary.gain.gain_db, self.request.pair.configuration.gain_db)
        with self.assertRaisesRegex(ValueError, "do not agree"):
            PairedSweepStepPair(a, replace(b, gain=replace(b.gain, gain_db=20.000000001)))

    def test_missing_untyped_nonfinite_wrong_chain_gain_refuses(self):
        a = self.pair.primary
        with self.assertRaises(TypeError):
            replace(a, gain=None)
        for value in (True, "20", float("nan"), float("inf"), -float("inf")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(a.gain, gain_db=value)
        with self.assertRaises(TypeError):
            replace(a.gain, mode="manual")
        for selection in ("RX1", ReceiverChainSelection.BOTH):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                replace(a.gain, receiver_selection=selection)
        with self.assertRaises(ValueError):
            replace(a, gain=replace(a.gain, receiver_selection=ReceiverChainSelection.RX2))

    def test_stopped_or_changed_namespace_step_or_epoch_is_stale(self):
        identity = self.pair.primary.identity
        for active in (None, replace(identity, resource_id="foreign"), replace(identity, session_id="foreign"),
                       replace(identity, sweep_epoch=6), replace(identity, line_sequence=8),
                       replace(identity, segment_index=1), replace(identity, config_generation=20),
                       replace(identity, acquisition_epoch=9), replace(identity, synchronization_epoch=4),
                       replace(identity, center_hz=identity.center_hz + 1.)):
            with self.subTest(active=active), self.assertRaises(ValueError):
                self.pair.validate_active(self.request, active)

    def test_epoch_lower_bound_is_not_permission_to_reuse_an_old_epoch(self):
        pair = pair_fixture(self.request, epoch=4)
        pair.validate_active(self.request, pair.primary.identity)
        newer = pair_fixture(self.request, epoch=5)
        with self.assertRaises(ValueError):
            pair.validate_active(self.request, newer.primary.identity)
        with self.assertRaises(ValueError):
            self.pair.validate_active(replace(self.request, sweep=replace(self.request.sweep, epoch=6)),
                                      self.pair.primary.identity)

    def test_modified_common_range_cannot_relabel_old_step_even_if_rf_window_covers_it(self):
        changed = replace(self.request, sweep=replace(self.request.sweep, start_hz=101e6, stop_hz=221e6))
        with self.assertRaisesRegex(ValueError, "current exact group"):
            self.pair.validate_active(changed, self.pair.primary.identity)

    def test_same_first_window_cannot_alias_changed_full_plan_profile_or_revision(self):
        request = self.request
        pair = self.pair
        identity = pair.primary.identity
        plans = (replace(request.sweep, stop_hz=300e6), replace(request.sweep, overlap_hz=1e6),
                 replace(request.sweep, speed_profile=SweepSpeedProfile.QUICK), replace(request.sweep, output_queue_capacity=8),
                 replace(request.sweep, segment_frame_timeout_ms=2000))
        profiles = (replace(request.pair.configuration, gain_db=20.),
                    replace(request.pair.configuration, fft_size=8192),
                    replace(request.pair.configuration, averaging_frames=2),
                    replace(request.pair.configuration, window="blackman"))
        for plan in plans:
            self.assertEqual(sweep_step_geometry(plan, 0), sweep_step_geometry(request.sweep, 0))
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                pair.validate_active(replace(request, sweep=plan), identity)
        for profile in profiles:
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                pair.validate_active(replace(request, pair=replace(request.pair, configuration=profile)), identity)
        with self.assertRaises(ValueError):
            pair.validate_active(replace(request, selection_revision=4), identity)

    def test_fresh_owner_receipt_binds_updated_intent_with_new_epoch(self):
        changed = replace(self.request, sweep=replace(self.request.sweep, stop_hz=300e6))
        new_pair = pair_fixture(changed, epoch=6)
        new_pair.validate_active(changed, new_pair.primary.identity)
        with self.assertRaises(ValueError):
            self.pair.validate_active(changed, new_pair.primary.identity)

    def test_one_sided_geometry_time_order_or_shared_gap_change_cannot_form_pair(self):
        second = self.pair.secondary
        for changes in ({"identity": replace(second.identity, config_generation=20)}, {"frame_sequence": 30},
                        {"first_sample_index": 57345}, {"timestamp_ns": 1}, {"clock_domain": "other-clock"},
                        {"timestamp_quality": TimestampQuality.HARDWARE}, {"shared_input_gaps_before": 3},
                        {"receiver_selection": ReceiverChainSelection.RX1},
                        {"producer_source_id": self.pair.primary.producer_source_id}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                PairedSweepStepPair(self.pair.primary, replace(second, **changes))
        with self.assertRaises(ValueError):
            replace(second, center_hz=second.center_hz + 1.)

    def test_foreign_sources_profile_and_uncovered_actual_readback_refuse(self):
        pair = self.pair
        changed = PairedSweepStepPair(replace(pair.primary, producer_source_id="foreign"), pair.secondary)
        with self.assertRaises(ValueError):
            changed.validate_active(self.request, changed.primary.identity)
        for field, value in (("sample_rate_hz", 20e6), ("fft_size", 8192)):
            request = replace(self.request, pair=replace(self.request.pair,
                              configuration=replace(self.request.pair.configuration, **{field: value})))
            with self.subTest(field=field), self.assertRaises(ValueError):
                pair.validate_active(request, pair.primary.identity)
        for change in ({"center_hz": 200e6}, {"analog_bandwidth_hz": 20e6}):
            identity = replace(pair.primary.identity, **change)
            first = replace(pair.primary, identity=identity, center_hz=identity.center_hz)
            second = replace(pair.secondary, identity=identity, center_hz=identity.center_hz)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "cover"):
                PairedSweepStepPair(first, second).validate_active(self.request, identity)

    def test_invalid_counter_geometry_receiver_and_time_provenance_refuse(self):
        for changes in ({"sweep_epoch": True}, {"config_generation": 0}, {"synchronization_epoch": 0},
                        {"acquisition_epoch": 0}, {"segment_index": 2048}, {"fft_size": 300},
                        {"center_hz": float("nan")}, {"analog_bandwidth_hz": float("inf")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.pair.primary.identity, **changes)
        for changes in ({"receiver_selection": "rx1"}, {"receiver_selection": ReceiverChainSelection.BOTH},
                        {"timestamp_ns": -1}, {"frame_sequence": True}, {"quality_flags": 1 << 32},
                        {"shared_input_gaps_before": -1}, {"clock_domain": " "}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.pair.primary, **changes)
        with self.assertRaises(TypeError):
            replace(self.pair.primary, timestamp_quality="unknown")
