"""Original density converters + bounded MOCK journal, never physical RF evidence."""

from dataclasses import replace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.analytical_journal import OwnerJournalScope
from sdr_monitor.domain.layer_journal import LayerJournalState
from sdr_monitor.domain.live import LiveAdmissionRejected, LiveSessionState
from sdr_monitor.domain.processing_policy import HostDcMode, SdrProcessingPolicyV1
from sdr_monitor.domain.source_processing import RfValueAuthority
from sdr_monitor.services.density_processing import DensityProcessingJoin, admit_density_processing
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_layer_journal import (
    DENSITY_CONTEXT_CACHE_RESERVATION, HOST_LAYER_RESERVATION, LAYER_EVENT_CAPACITY, NativeLayerJournal,
)
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_owner_journal import _scalar_bytes
from sdr_monitor.services.native_ready_bridge import NativeReadyBridge
from tests.test_app07_product_layer_bridge import batch, protocol, raw_ref
from tests.test_live_processing import configuration, context
from tests.test_persistence_processing_metadata import density
from tests.test_source_processing import composition
from tests.test_s15_live_rx_bridge import _FakeNative


def fixture(family="ad936x", mode=HostDcMode.BLOCK_MEAN, receiver="RX1"):
    policy = SdrProcessingPolicyV1(mode)
    if family == "ad936x":
        config = replace(configuration(mode), persistence_enabled=True, persistence_mode="exponential-decay",
            persistence_power_bins=16)
        current = context(config, receiver=receiver)
        center, rate, hop = config.center_hz, config.sample_rate_hz, config.fft_size
        source, size = current.active_source_id, config.fft_size
        native = protocol(_FakeNative())
        native.analytical_ready_clock_ns = protocol().analytical_ready_clock_ns
        service = NativeLiveSessionService(native)
    else:
        _, current, _, _, _ = composition(policy=policy)
        config = replace(current.hackrf_request, persistence_enabled=True, persistence_mode="exponential-decay",
            persistence_power_bins=16, window="rectangular")
        current = replace(current, hackrf_request=config, hackrf_persistence_available=True)
        center, rate, hop = config.center_frequency_hz, config.sample_rate_hz, config.hop_size
        source, size, receiver = config.source_id, config.fft_size, None
        native = protocol()
        service = object.__new__(HackrfAnalyzerService)
        service._native = native
        service._density_processing_join = DensityProcessingJoin()
    bridge = NativeReadyBridge(native)
    bridge.begin()
    journal = NativeLayerJournal(native, LAYER_EVENT_CAPACITY)
    scope = OwnerJournalScope(bridge.clock_scope_id, bridge.host_process_id, "MOCK-DENSITY-OWNER",
        str(source), receiver, str(current.session_id), current.active_config_generation, current.acquisition_epoch)
    journal.begin(scope)
    raw = density(mode, source=str(source), fft=size)
    raw.config_generation = current.active_config_generation
    raw.frequencies_hz = center - rate / 2 + np.arange(size, dtype=np.float64) * (rate / size)
    raw.processing_metadata.center_frequency_hz = float(center)
    raw.processing_metadata.sample_rate_hz = float(rate)
    raw.processing_metadata.analog_bandwidth_hz = config.analog_bandwidth_hz if family == "ad936x" else 0.
    raw.processing_metadata.fft_size = size
    raw.processing_metadata.hop_size = hop
    raw.processing_metadata.fft_bin_width_hz = rate / size
    raw.processing_metadata.enbw_hz = rate / size
    raw.processing_metadata.nominal_rbw_hz = rate / size
    raw.processing_metadata.averaging_frames = config.averaging_frames
    raw.processing_metadata.precision_mode = "accurate_f32_f64_accum" if family == "ad936x" else "reference_f64"
    ref = raw_ref(config_generation=current.active_config_generation, update_sequence=1,
        source_frame_sequence=0, accumulation_sequence=1)
    raw.layer_ready = ref
    journal.drain(lambda _: batch([ref]))
    service._ready_bridge = bridge
    if family == "ad936x":
        service._density_journals = (journal, journal)
    else:
        service._layer_journal = journal
    return service, raw, current, journal


class DensityOwnerTests(unittest.TestCase):
    def test_original_ad_hf_off_blockmean_density_context_without_spectrum(self):
        for family in ("ad936x", "hackrf"):
            for mode in HostDcMode:
                with self.subTest(family=family, mode=mode):
                    service, raw, current, journal = fixture(family, mode)
                    converted = service._convert_persistence(raw, current)
                    receipt = converted.processing_context
                    self.assertIsNotNone(receipt)
                    self.assertEqual(receipt.layer_ready.producer_instance_id, 41)
                    self.assertEqual(receipt.layer_ready.owner_run_id, "MOCK-DENSITY-OWNER")
                    self.assertIs(receipt.numerical_provenance.processing_recipe.dc_mode, mode)
                    self.assertIs(receipt.center.authority, RfValueAuthority.READBACK
                        if family == "ad936x" else RfValueAuthority.SDK_APPLIED)
                    self.assertEqual(receipt.native_quality_flags, raw.quality_flags)
                    self.assertIsNone(current.spectrum)
                    self.assertLessEqual(_scalar_bytes(receipt) + _scalar_bytes(converted.layer_ready), 8192)
                    self.assertFalse(converted.density.flags.writeable)
                    self.assertIs(journal.current().state, LayerJournalState.ACTIVE)
                    replace(current, persistence=converted)
                    with self.assertRaises(ValueError):
                        replace(converted, native_quality_flags=raw.quality_flags ^ 1)
                    with self.assertRaises(ValueError):
                        replace(converted, layer_ready=None)
                    if mode is HostDcMode.BLOCK_MEAN:
                        for changed in (replace(receipt, resource_id="other-device"),
                                replace(receipt, center=replace(receipt.center, value_hz=receipt.center.value_hz + 1.)),
                                replace(receipt, fft_size=receipt.fft_size * 2)):
                            with self.subTest(foreign_context=changed), self.assertRaises(ValueError):
                                replace(current, persistence=replace(converted, processing_context=changed))

    def test_final_diagnostic_receipt_never_grants_new_processed_publication(self):
        for family in ("ad936x", "hackrf"):
            service, raw, current, journal = fixture(family)
            valid = service._convert_persistence(raw, current)
            journal.finish(lambda _: batch([], created=1))
            self.assertIs(journal.current().state, LayerJournalState.FINAL)
            # Existing immutable history remains valid, but no new admission.
            replace(current, state=LiveSessionState.CONNECTED, persistence=valid)
            with self.assertRaisesRegex(ValueError, "ACTIVE SAME"):
                service._convert_persistence(raw, current)

    def test_foreign_and_stale_owner_source_rx_epoch_accumulation_refuse(self):
        changes = (dict(state=LiveSessionState.CONNECTED), dict(session_id="different"),
            dict(acquisition_epoch=10), dict(active_config_generation=2), dict(active_source_id="foreign"))
        for family in ("ad936x", "hackrf"):
            for change in changes:
                service, raw, current, _ = fixture(family)
                with self.subTest(family=family, change=change), self.assertRaises(ValueError):
                    service._convert_persistence(raw, replace(current, **change))
            for field, value in (("producer_instance_id", 42), ("accumulation_sequence", 2),
                                 ("ready_native_ns", 151), ("config_generation", 2)):
                service, raw, current, _ = fixture(family)
                raw.layer_ready = SimpleNamespace(**{**vars(raw.layer_ready), field: value})
                with self.subTest(family=family, field=field), self.assertRaises(ValueError):
                    service._convert_persistence(raw, current)
        service, raw, current, _ = fixture()
        with self.assertRaises(ValueError):
            service._convert_persistence(raw, current, receiver_id="RX2")

    def test_native_numerical_geometry_changes_refuse_without_policy_inference(self):
        for field, value in (("sample_rate_hz", 1.), ("center_frequency_hz", 1.), ("fft_size", 512),
                             ("hop_size", 1), ("averaging_frames", 2), ("precision_mode", "fast_f32")):
            for family in ("ad936x", "hackrf"):
                service, raw, current, _ = fixture(family)
                setattr(raw.processing_metadata, field, value)
                with self.subTest(field=field, family=family), self.assertRaises(ValueError):
                    service._convert_persistence(raw, current)
        service, raw, current, _ = fixture()
        raw.frequencies_hz[-1] += 1.
        with self.assertRaisesRegex(ValueError, "grid"):
            service._convert_persistence(raw, current)

    def test_no_hot_digest_or_json_and_bounded_scalar_cache(self):
        service, raw, current, _ = fixture()
        service._convert_persistence(raw, current)
        with patch("sdr_monitor.services.density_processing.sha256", side_effect=AssertionError("hot grid hash")), \
                patch("sdr_monitor.domain.processing_policy.json.dumps", side_effect=AssertionError("hot JSON")):
            service._convert_persistence(raw, current)
        cache = tuple(service._density_processing_join._grids.items())
        self.assertLessEqual(_scalar_bytes(cache), DENSITY_CONTEXT_CACHE_RESERVATION)
        self.assertEqual(service._density_journals[0]._host_budget, HOST_LAYER_RESERVATION)
        service._density_processing_join.clear()
        self.assertFalse(service._density_processing_join._grids)

    def test_fixed_scalar_cache_evicts_and_receipt_budget_refuses_before_delivery(self):
        join = DensityProcessingJoin()
        for index in range(6):
            service, raw, current, _ = fixture()
            service._density_processing_join = join
            config = replace(current.applied.applied, center_hz=current.applied.applied.center_hz + index * 1e6)
            current = replace(current, applied=replace(current.applied, applied=config))
            raw.processing_metadata.center_frequency_hz = config.center_hz
            raw.frequencies_hz = config.center_hz - config.sample_rate_hz / 2 + np.arange(config.fft_size) * config.sample_rate_hz / config.fft_size
            service._convert_persistence(raw, current)
            self.assertLessEqual(len(join._grids), 4)
            self.assertLessEqual(_scalar_bytes(tuple(join._grids.items())), DENSITY_CONTEXT_CACHE_RESERVATION)
        with patch("sdr_monitor.services.density_processing.DENSITY_RECEIPT_WINDOW_RESERVATION", 1), \
                self.assertRaisesRegex(ValueError, "host window"):
            service._convert_persistence(raw, current)

    def test_broken_optional_metadata_is_unknown_only_for_off(self):
        for mode in HostDcMode:
            service, raw, current, _ = fixture(mode=mode)
            class LegacyRaw:
                def __getattr__(self, name):
                    if name == "processing_metadata":
                        raise RuntimeError("legacy optional telemetry failure")
                    return getattr(raw, name)
            if mode is HostDcMode.OFF:
                converted = service._convert_persistence(LegacyRaw(), current)
                self.assertIsNone(converted.processing_context)
                self.assertIsNone(converted.numerical_provenance)
            else:
                with self.assertRaises(ValueError):
                    service._convert_persistence(LegacyRaw(), current)

    def test_capability_metadata_exact_types_before_effect_legacy_off_compatible(self):
        policy = SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN)
        native = protocol()
        expected = {"schema_version": 1, "scope": "native_contributing_density_metadata_only",
            "full_owner_context": False, "recipe_change_resets": True, "string_max_bytes": 256, "reserved_bytes": 8192}
        admit_density_processing(object(), SdrProcessingPolicyV1(), enabled=True)
        with self.assertRaises(LiveAdmissionRejected):
            admit_density_processing(native, policy, enabled=True)
        native.persistence_processing_contract = lambda: expected
        admit_density_processing(native, policy, enabled=True, hackrf=True)
        with self.assertRaisesRegex(LiveAdmissionRejected, "actual native owner drain"):
            admit_density_processing(native, policy, enabled=True)
        native.PlutoReceiverSelection = SimpleNamespace(RX1=1, RX2=2)
        native.PlutoFixedBandEngine = SimpleNamespace(drain_density_layer_ready_events=lambda: None)
        admit_density_processing(native, policy, enabled=True)
        for key, value in (("schema_version", True), ("full_owner_context", True), ("reserved_bytes", 8192.),
                           ("recipe_change_resets", 1), ("scope", "other")):
            native.persistence_processing_contract = lambda: {**expected, key: value}
            with self.subTest(key=key), self.assertRaises(LiveAdmissionRejected):
                admit_density_processing(native, policy, enabled=True)
