"""Compatibility and actual NativeLive admission stay before SDK allocation."""

from __future__ import annotations

import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest
from sdr_monitor.domain.device_capabilities import (
    AdapterRuntimeAvailability,
    AdapterRuntimeSnapshot,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityRange,
    DeviceCapabilityBinding,
    DeviceFamily,
    build_device_capability_inventory,
)
from sdr_monitor.domain.live import (
    AppliedLiveConfiguration,
    BackendKind,
    LiveAdmissionRejected,
    LiveConfiguration,
    LiveErrorKind,
    LiveSessionState,
)
from sdr_monitor.services.hackrf_capability_adapter import HackrfBoardKind, HackrfCapabilityAdapter, HackrfReadOnlyProbe
from sdr_monitor.services.hackrf_live_admission import HackrfLiveRequest
from sdr_monitor.services.native_continuous_sweep_factory import (
    NativeContinuousSweepPlanFactory,
    NativeLiveContinuousSweepDisplayService,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.source_capability_admission import (
    SourceRequestAdmissionReason as Reason,
)
from sdr_monitor.services.source_capability_admission import (
    admit_source_request,
    live_configuration_numbers_valid,
)
from sdr_monitor.services.tinysa_capability_adapter import TinySaCapabilityAdapter, TinySaModel, TinySaReadOnlyProbe
from sdr_monitor.services.tinysa_serial_trace_collector import TinySaScanRawRequest
from tests.test_app06_capability_inventory_join import _ProbePort
from tests.test_app06_pluto_observation_catalog import _Native


def _pluto():
    native = _Native()
    service = NativeLiveSessionService(native)
    source = service.discover_devices()[0].device_id
    return service.capability_inventory(), source, native


def _known(snapshot, identity, source="source-one", status=AdapterRuntimeAvailability.AVAILABLE):
    return build_device_capability_inventory(
        (snapshot,), bindings=(DeviceCapabilityBinding(source, snapshot.family, snapshot.adapter_id, snapshot, identity),),
        runtimes=(AdapterRuntimeSnapshot(snapshot.adapter_id, snapshot.family, status,
                                        "fixture-contract" if status is not AdapterRuntimeAvailability.UNKNOWN else "not-recorded"),),
    )


def _hackrf():
    port = _ProbePort(HackrfReadOnlyProbe(HackrfBoardKind.HACKRF_ONE, (0, 0, 1, 2), "fw", 0x0107))
    observed = HackrfCapabilityAdapter(lambda: port).observe()
    return observed, port


def _hackrf_request(**changes):
    return HackrfLiveRequest(center_frequency_hz=100e6, sample_rate_hz=20e6,
                             baseband_filter_hz=15_000_000, lna_gain_db=16, vga_gain_db=20,
                             source_id=changes.pop("source_id", "source-one"), **changes)


def _without(snapshot, field, **changes):
    return replace(snapshot, evidence=tuple(item for item in snapshot.evidence if item.field is not field), **changes)


class SourceCapabilityAdmissionTests(unittest.TestCase):
    def test_ad936x_high_fs_and_progressive_sweep_intent_use_same_facts_without_io(self):
        inventory, source, native = _pluto()
        opened = len(native.created)
        configuration = LiveConfiguration(center_hz=100e6, sample_rate_hz=61.44e6, analog_bandwidth_hz=40e6)
        self.assertTrue(admit_source_request(inventory, source, "rtbw", configuration).accepted)
        request = ContinuousSweepPlanRequest(100e6, 1000e6, usable_window_hz=36e6, overlap_hz=2e6)
        result = admit_source_request(inventory, source, "sweep", request, applied_live=configuration)
        self.assertTrue(result.accepted)
        self.assertFalse(hasattr(result, "plan"))  # not a native Start/activation permit
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])

    def test_ad936x_ranges_are_observed_not_inferred_from_chip_or_label(self):
        inventory, source, _ = _pluto()
        original = inventory.bindings[0]
        snapshot = replace(original.snapshot, label="Modified AD9363", sample_rate_ranges_hz=(CapabilityRange(1e6, 61.44e6, "Hz"),))
        identity = original.calibration_identity
        candidate = _known(snapshot, identity, source)
        request = LiveConfiguration(center_hz=100e6, sample_rate_hz=61.44e6)
        self.assertTrue(admit_source_request(candidate, source, "rtbw", request).accepted)
        lower = replace(snapshot, sample_rate_ranges_hz=(CapabilityRange(1e6, 20e6, "Hz"),))
        self.assertIs(admit_source_request(_known(lower, identity, source), source, "rtbw", request).reason, Reason.REQUEST_RANGE)

    def test_ad936x_unknown_range_is_not_a_claim_of_unsupported_hardware(self):
        inventory, source, _ = _pluto()
        binding = inventory.bindings[0]
        missing = _without(binding.snapshot, CapabilityField.SAMPLE_RATE_RANGE, sample_rate_ranges_hz=())
        result = admit_source_request(_known(missing, binding.calibration_identity, source), source, "rtbw", LiveConfiguration())
        self.assertIs(result.reason, Reason.CAPABILITY_UNVERIFIED)

    def test_invalid_live_numbers_including_nan_infinity_and_bool_are_rejected(self):
        inventory, source, _ = _pluto()
        for name, value in (("center_hz", math.nan), ("center_hz", math.inf), ("sample_rate_hz", math.inf),
                            ("gain_db", math.nan), ("gain_db", True), ("analog_bandwidth_hz", math.inf)):
            with self.subTest(name=name, value=value):
                request = replace(LiveConfiguration(), **{name: value})
                self.assertFalse(live_configuration_numbers_valid(request))
                self.assertIs(admit_source_request(inventory, source, "rtbw", request).reason, Reason.REQUEST_RANGE)

    def test_ad936x_sweep_configuration_and_window_range_are_explicit(self):
        inventory, source, _ = _pluto()
        request = ContinuousSweepPlanRequest(100e6, 1000e6)
        self.assertIs(admit_source_request(inventory, source, "sweep", request).reason, Reason.CONFIGURATION_REQUIRED)
        for candidate, profile in ((request, LiveConfiguration(sample_rate_hz=20e6)),
                                 (request, LiveConfiguration(sample_rate_hz=61.44e6, analog_bandwidth_hz=20e6)),
                                 (ContinuousSweepPlanRequest(100e6, 6.1e9), LiveConfiguration(sample_rate_hz=61.44e6))):
            with self.subTest(request=candidate, profile=profile):
                self.assertIs(admit_source_request(inventory, source, "sweep", candidate, applied_live=profile).reason, Reason.REQUEST_RANGE)

    def test_source_mode_type_and_unknown_identity_have_distinct_refusals(self):
        inventory, source, _ = _pluto()
        cases = (("absent", "rtbw", LiveConfiguration(), Reason.SOURCE_NOT_FOUND),
                 ("ip:private", "rtbw", LiveConfiguration(), Reason.SOURCE_NOT_FOUND),
                 (source, "unknown", LiveConfiguration(), Reason.REQUEST_MODE),
                 (source, "rtbw", object(), Reason.REQUEST_TYPE),
                 (source, "sweep", LiveConfiguration(), Reason.REQUEST_TYPE))
        for key, mode, request, reason in cases:
            with self.subTest(key=key, mode=mode):
                self.assertIs(admit_source_request(inventory, key, mode, request).reason, reason)
        unknown = build_device_capability_inventory((), bindings=(DeviceCapabilityBinding(source, DeviceFamily.AD936X, inventory.bindings[0].adapter_id),))
        self.assertIs(admit_source_request(unknown, source, "rtbw", LiveConfiguration()).reason, Reason.IDENTITY_UNVERIFIED)

    def test_runtime_unknown_missing_and_unavailable_do_not_alias_each_other(self):
        inventory, source, _ = _pluto()
        for runtimes, reason in (((), Reason.RUNTIME_NOT_OBSERVED),
                                 ((replace(inventory.runtimes[0], availability=AdapterRuntimeAvailability.UNKNOWN),), Reason.RUNTIME_NOT_OBSERVED),
                                 ((replace(inventory.runtimes[0], availability=AdapterRuntimeAvailability.UNAVAILABLE),), Reason.RUNTIME_UNAVAILABLE)):
            with self.subTest(reason=reason, runtimes=runtimes):
                candidate = replace(inventory, runtimes=runtimes)
                self.assertIs(admit_source_request(candidate, source, "rtbw", LiveConfiguration()).reason, reason)

    def test_hackrf_max20_uses_its_own_gain_request_and_does_not_reprobe_or_start(self):
        observed, port = _hackrf()
        inventory = _known(observed.snapshot, observed.calibration_identity)
        self.assertTrue(admit_source_request(inventory, "source-one", "rtbw", _hackrf_request()).accepted)
        self.assertEqual(port.calls, ["probe", "close"])
        self.assertIs(admit_source_request(inventory, "source-one", "rtbw", LiveConfiguration()).reason, Reason.REQUEST_TYPE)
        self.assertIs(admit_source_request(inventory, "source-one", "rtbw", _hackrf_request(source_id="other-source")).reason, Reason.REQUEST_SOURCE)
        self.assertIs(admit_source_request(inventory, "source-one", "sweep", object()).reason, Reason.MODE_RUNTIME_UNAVAILABLE)

    def test_hackrf_missing_transport_proof_is_not_misreported_as_frequency_error(self):
        observed, _ = _hackrf()
        snapshot = replace(observed.snapshot, evidence=tuple(
            replace(item, origin=CapabilityEvidenceOrigin.VENDOR_DECLARATION) if item.field is CapabilityField.TRANSPORT else item
            for item in observed.snapshot.evidence))
        result = admit_source_request(_known(snapshot, observed.calibration_identity), "source-one", "rtbw", _hackrf_request())
        self.assertIs(result.reason, Reason.CAPABILITY_UNVERIFIED)
        missing = _without(observed.snapshot, CapabilityField.SAMPLE_RATE_RANGE, sample_rate_ranges_hz=())
        result = admit_source_request(_known(missing, observed.calibration_identity), "source-one", "rtbw", _hackrf_request())
        self.assertIs(result.reason, Reason.CAPABILITY_UNVERIFIED)

    def test_tinysa_model_and_10001_point_trace_preserve_dbm_without_raw_iq(self):
        for model in TinySaModel:
            with self.subTest(model=model):
                port = _ProbePort(TinySaReadOnlyProbe(model, "unit", "fw"))
                observed = TinySaCapabilityAdapter(lambda port=port: port).observe()
                inventory = _known(observed.snapshot, observed.external_correction_identity)
                request = TinySaScanRawRequest(model, 100_000_000, 300_000_000, 10001)
                with patch("sdr_monitor.services.tinysa_serial_trace_collector.Serial", side_effect=AssertionError("No serial port permitted")):
                    self.assertTrue(admit_source_request(inventory, "source-one", "sweep", request).accepted)
                self.assertEqual(request.expected_frame_bytes, 30005)
                self.assertIs(admit_source_request(inventory, "source-one", "rtbw", LiveConfiguration()).reason, Reason.DEVICE_MODE_UNSUPPORTED)
                self.assertEqual(observed.analyzer_semantics.reported_unit, "dBm")
                self.assertFalse(observed.snapshot.raw_iq_available)
                self.assertEqual(port.calls, ["probe", "close"])

    def test_tinysa_model_is_not_parsed_from_display_label_and_input_range_is_not_joined(self):
        port = _ProbePort(TinySaReadOnlyProbe(TinySaModel.BASIC, "unit", "fw"))
        observed = TinySaCapabilityAdapter(lambda: port).observe()
        inventory = _known(replace(observed.snapshot, label="tinySA Ultra"), observed.external_correction_identity)
        request = TinySaScanRawRequest(TinySaModel.ULTRA, 100_000_000, 300_000_000, 10001)
        self.assertIs(admit_source_request(inventory, "source-one", "sweep", request).reason, Reason.REQUEST_MODEL)
        # Basic low/high input ranges overlap, but no one input covers this request.
        request = TinySaScanRawRequest(TinySaModel.BASIC, 100_000_000, 900_000_000, 8192)
        self.assertIs(admit_source_request(inventory, "source-one", "sweep", request).reason, Reason.REQUEST_RANGE)
        for points in (10002, 30000):
            with self.subTest(points=points), self.assertRaises(ValueError):
                TinySaScanRawRequest(TinySaModel.BASIC, 100_000_000, 300_000_000, points)


class NativeLiveCapabilityAdmissionTests(unittest.TestCase):
    def _selected(self, *, serial="fixture-a"):
        native = _Native(serial=serial)
        service = NativeLiveSessionService(native)
        descriptor = service.discover_devices()[0]
        service.select_device(descriptor.device_id)
        configuration = LiveConfiguration(center_hz=100e6, sample_rate_hz=61.44e6, analog_bandwidth_hz=40e6)
        service.apply_configuration(configuration)
        return service, native

    def _assert_no_owner_refusal(self, service, native, text):
        before = service.latest_snapshot()
        opened = len(native.created)
        with self.assertRaisesRegex(LiveAdmissionRejected, text):
            service.start_admitted()
        self.assertIs(service.latest_snapshot(), before)
        raw = service.start()
        self.assertIs(raw.error_kind, LiveErrorKind.CONFIGURATION_REJECTED)
        self.assertIn(text, raw.error)
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])
        service.close_live()

    def test_known_out_of_range_refusal_precedes_all_factory_calls_for_both_start_paths(self):
        service, native = self._selected()
        request = replace(service.latest_snapshot().applied.applied, center_hz=6.1e9)
        service._snapshot = replace(service.latest_snapshot(), applied=AppliedLiveConfiguration(request, request))
        self._assert_no_owner_refusal(service, native, "request_range")

    def test_nan_and_infinite_unknown_route_requests_do_not_bypass_sdk_guard(self):
        for value in (math.nan, math.inf):
            with self.subTest(value=value):
                service, native = self._selected(serial=None)
                request = replace(service.latest_snapshot().applied.applied, gain_db=value)
                service._snapshot = replace(service.latest_snapshot(), applied=AppliedLiveConfiguration(request, request))
                self._assert_no_owner_refusal(service, native, "numbers are invalid")

    def test_known_runtime_refusal_precedes_sdk_and_does_not_clear_observed_identity(self):
        service, native = self._selected()
        native.PlutoFixedBandEngine = None
        self.assertIsNotNone(service.latest_snapshot().device.calibration_identity)
        self._assert_no_owner_refusal(service, native, "runtime_unavailable")

    def test_maximum_profile_and_explicit_unknown_route_preserve_existing_start_boundary(self):
        for serial in ("fixture-a", None):
            with self.subTest(serial=serial):
                service, native = self._selected(serial=serial)
                before = service.latest_snapshot()
                with patch.object(service, "_start_unlocked", return_value=before) as start:
                    self.assertIs(service.start_admitted(), before)
                    start.assert_called_once_with()
                self.assertEqual(native.engines, [])
                service.close_live()

    def test_changed_profile_during_error_recovery_is_revalidated_before_open(self):
        service, native = self._selected()
        service._engine = object()
        service._snapshot = replace(service.latest_snapshot(), state=LiveSessionState.ERROR)
        invalid = replace(service.latest_snapshot().applied.applied, center_hz=6.1e9)
        opened = len(native.created)
        def recover(*, timeout_s):
            self.assertEqual(timeout_s, 5.0)
            service._engine = None
            service._snapshot = replace(service.latest_snapshot(), applied=AppliedLiveConfiguration(invalid, invalid))
        with patch.object(service, "_release_stream", side_effect=recover) as release:
            raw = service.start()
            release.assert_called_once()
        self.assertIs(raw.error_kind, LiveErrorKind.CONFIGURATION_REJECTED)
        self.assertIn("request_range", raw.error)
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])
        service.close_live()

    def test_continuous_sweep_range_guard_runs_before_native_config_and_releases_lease(self):
        service, native = self._selected()
        service.apply_configuration(replace(service.latest_snapshot().applied.applied, backend=BackendKind.CPU))
        sweep = NativeLiveContinuousSweepDisplayService(service)
        opened = len(native.created)
        with patch("sdr_monitor.services.native_continuous_sweep_factory.build_native_fixed_band_config") as build:
            with self.assertRaisesRegex(LiveAdmissionRejected, "request_range"):
                sweep.start(ContinuousSweepPlanRequest(5.9e9, 6.1e9))
            build.assert_not_called()
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])
        self.assertFalse(service._sweep_lease_active)
        self.assertIsNone(sweep._factory)
        self.assertIsNone(sweep._display)
        sweep.close()
        service.close_live()

    def test_sweep_lease_guard_uses_captured_profile_not_later_live_configuration(self):
        service, native = self._selected()
        service.apply_configuration(replace(service.latest_snapshot().applied.applied, backend=BackendKind.CPU))
        factory = NativeContinuousSweepPlanFactory.from_native_live(service)
        opened = len(native.created)
        try:
            changed = replace(service.latest_snapshot().applied.applied, sample_rate_hz=20e6)
            service._snapshot = replace(service.latest_snapshot(), applied=AppliedLiveConfiguration(changed, changed))
            geometry = factory.preflight(ContinuousSweepPlanRequest(100e6, 136e6))
            self.assertEqual(geometry.sample_rate_hz, 61.44e6)
        finally:
            factory.close()
            service.close_live()
        self.assertEqual(len(native.created), opened)
        self.assertEqual(native.engines, [])


if __name__ == "__main__":
    unittest.main()
