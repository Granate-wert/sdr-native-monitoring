"""Recipe transport on existing converters; synthetic/mock only, never RF admission."""

from dataclasses import FrozenInstanceError
import importlib
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.live import LiveConfiguration, LiveSessionState, LiveSnapshot
from sdr_monitor.domain.paired_live import PairedLiveRequest
from sdr_monitor.domain.processing_policy import (
    DC_REMOVED_MASK, DspProcessingRecipeObservationV1, HostDcMode, SdrProcessingPolicyV1,
)
from sdr_monitor.domain.receiver_topology import (
    IqComponent, ReceiverChain, ReceiverTopologySnapshot, StreamScanElement,
)
from sdr_monitor.domain.rtl_live import RtlLiveRequest
from sdr_monitor.domain.spectrum_provenance import SpectrumProvenance
from sdr_monitor.services.hackrf_analyzer import HackrfAnalyzerService
from sdr_monitor.services.native_live import NativeLiveSessionService
from sdr_monitor.services.native_paired_live import pair_publication
from sdr_monitor.services.native_spectrum_provenance import native_spectrum_provenance
from sdr_monitor.services.rtl_analyzer import RtlAnalyzerService
from tests.test_native_live_discovery import _FakeNative
from tests.test_s15_live_rx_bridge import _make_frame


def recipe(mode=HostDcMode.OFF):
    observation = DspProcessingRecipeObservationV1(mode)
    return SimpleNamespace(schema_version=1, scope="dsp_recipe_only", dc_mode=mode.value,
        spur_mode="off", canonical_policy_json=observation.canonical_policy_json,
        policy_digest=observation.policy_digest, whole_frame_modified=observation.whole_frame_modified,
        hardware_dc_tracking=None, comparison_applied=False)


class ProcessingRecipeMapperTests(unittest.TestCase):
    def test_legacy_absent_or_none_stays_unknown_even_with_quality_bit(self):
        for flags in (None, 0, DC_REMOVED_MASK | (1 << 31)):
            for extra in ({}, {"dsp_processing_recipe": None}):
                with self.subTest(flags=flags, extra=extra):
                    value = native_spectrum_provenance(SimpleNamespace(quality_flags=flags, **extra))
                    self.assertIsNone(value.processing_recipe)

    def test_exact_recipes_immutable_and_not_owner_authority(self):
        for mode in HostDcMode:
            flags = (1 << 31) | DC_REMOVED_MASK | 1
            frame = SimpleNamespace(dsp_processing_recipe=recipe(mode), quality_flags=flags)
            observed = native_spectrum_provenance(frame).processing_recipe
            self.assertIsNotNone(observed)
            self.assertEqual(observed.policy, SdrProcessingPolicyV1(mode))
            self.assertEqual(observed.policy_digest, observed.policy.digest)
            self.assertEqual(observed.scope, "dsp_recipe_only")
            self.assertEqual(observed.whole_frame_modified, mode is HostDcMode.BLOCK_MEAN)
            self.assertIsNone(observed.hardware_dc_tracking)
            self.assertEqual(frame.quality_flags, flags)
            for field in ("frame_key", "processing_revision", "resource_id", "receiver", "acquisition_epoch"):
                self.assertFalse(hasattr(observed, field))
            with self.assertRaises(FrozenInstanceError):
                observed.dc_mode = HostDcMode.OFF
        with self.assertRaises(ValueError):
            DspProcessingRecipeObservationV1("off")
        with self.assertRaises(ValueError):
            SpectrumProvenance(processing_recipe=recipe())

    def test_no_json_or_hashing_on_publication_and_bounded_observation_reuse(self):
        frames = [SimpleNamespace(dsp_processing_recipe=recipe(mode), quality_flags=DC_REMOVED_MASK)
                  for mode in HostDcMode]
        with patch("sdr_monitor.domain.processing_policy.json.dumps", side_effect=AssertionError("per-frame JSON")), \
                patch("sdr_monitor.domain.processing_policy.hashlib.sha256", side_effect=AssertionError("per-frame hash")):
            observations = [native_spectrum_provenance(frame).processing_recipe for frame in frames]
            for _ in range(100):
                for frame, observed in zip(frames, observations, strict=True):
                    self.assertIs(native_spectrum_provenance(frame).processing_recipe, observed)

    def test_malformed_claimed_fields_refuse_not_unknown_or_off(self):
        changes = {"schema_version": (True, 2, 1.0, None), "scope": ("owner", None),
            "dc_mode": ("OFF", "mystery", 0, []), "spur_mode": ("candidates_v1", None),
            "canonical_policy_json": ("{}", recipe().canonical_policy_json + " ", b"{}"),
            "policy_digest": ("sha256:" + "0" * 64, None),
            "whole_frame_modified": (True, 0), "comparison_applied": (True, 0),
            "hardware_dc_tracking": (False, True, 0)}
        for name, values in changes.items():
            for invalid in values:
                frame = SimpleNamespace(dsp_processing_recipe=recipe(), quality_flags=DC_REMOVED_MASK)
                setattr(frame.dsp_processing_recipe, name, invalid)
                with self.subTest(name=name, invalid=invalid), self.assertRaises(ValueError):
                    native_spectrum_provenance(frame)
        for name in vars(recipe()):
            value = recipe()
            delattr(value, name)
            with self.subTest(missing=name), self.assertRaises(ValueError):
                native_spectrum_provenance(SimpleNamespace(dsp_processing_recipe=value, quality_flags=0))

    def test_flags_are_exact_and_block_mean_requires_but_off_may_inherit_dc(self):
        for mode in HostDcMode:
            for flags in (None, True, -1, 1.0, 1 << 32):
                with self.subTest(mode=mode, flags=flags), self.assertRaises(ValueError):
                    native_spectrum_provenance(SimpleNamespace(dsp_processing_recipe=recipe(mode), quality_flags=flags))
        with self.assertRaisesRegex(ValueError, "DC_REMOVED"):
            native_spectrum_provenance(SimpleNamespace(dsp_processing_recipe=recipe(HostDcMode.BLOCK_MEAN), quality_flags=1))
        for flags in (0, DC_REMOVED_MASK, (1 << 32) - 1):
            self.assertIs(native_spectrum_provenance(SimpleNamespace(dsp_processing_recipe=recipe(),
                          quality_flags=flags)).processing_recipe.dc_mode, HostDcMode.OFF)


class ProcessingRecipeAdapterTests(unittest.TestCase):
    def test_existing_ad_and_hackrf_converters_preserve_values_flags_and_recipe(self):
        ad = NativeLiveSessionService(_FakeNative())
        # Constructor only; conversion fixtures do not discover/stage/start SDR.
        hf = HackrfAnalyzerService(SimpleNamespace(), object(), lambda: None, object(), object())
        for mode in HostDcMode:
            frame = _make_frame(3, source_id="recipe-test", config_generation=4,
                                quality_flags=DC_REMOVED_MASK | (1 << 31))
            frame.dsp_processing_recipe = recipe(mode)
            ad_context = LiveSnapshot(4, 0, LiveSessionState.RUNNING,
                acquisition_epoch=8, clock_domain="host_steady_ns", receiver_id="RX2")
            hf_context = SimpleNamespace(hackrf_request=SimpleNamespace(source_id="recipe-test"),
                acquisition_epoch=8, clock_domain="host_steady_ns", session_id="conversion-only")
            for converted in (ad._convert_spectrum(frame, ad_context), hf._convert(frame, hf_context)):
                self.assertIs(converted.numerical_provenance.processing_recipe.dc_mode, mode)
                self.assertEqual(converted.native_quality_flags, frame.quality_flags)
                self.assertEqual(converted.acquisition_epoch, 8)
                np.testing.assert_array_equal(converted.values, frame.values)
                self.assertTrue(np.shares_memory(converted.values, frame.values))
                self.assertFalse(converted.values.flags.writeable)
            self.assertEqual(ad._convert_spectrum(frame, ad_context).receiver_id, "RX2")
        frame.source.source_id = "foreign"
        with self.assertRaisesRegex(ValueError, "source"):
            hf._convert(frame, hf_context)

    def test_rtl_exact_reduced_converter_carries_recipe_without_relaxing_guards(self):
        from tests.test_app07_rtl_product_route import _FrameControl

        rtl = RtlAnalyzerService(SimpleNamespace(), object(), lambda: None, lambda *_args: None)
        request = RtlLiveRequest(100_000_000, 2_400_000, detector="peak")
        context = SimpleNamespace(rtl_request=request, acquisition_epoch=17,
            clock_domain="host_steady_ns", session_id="conversion-only")
        for mode in HostDcMode:
            frame = _FrameControl(request.center_frequency_hz, request.sample_rate_hz,
                                  request.configuration_generation).make_frame(2, source_id=request.source_id)
            frame.dsp_processing_recipe = recipe(mode)
            frame.quality_flags = DC_REMOVED_MASK | 1
            converted = rtl._convert(frame, context)
            self.assertIs(converted.numerical_provenance.processing_recipe.dc_mode, mode)
            self.assertEqual(converted.native_quality_flags, frame.quality_flags)
            self.assertEqual(converted.acquisition_epoch, 17)
            np.testing.assert_array_equal(converted.values, frame.values)
            self.assertTrue(np.shares_memory(converted.values, frame.values))
            frame.source.backend_id = "foreign"
            with self.assertRaisesRegex(ValueError, "producer"):
                rtl._convert(frame, context)

    def test_same_pair_helper_keeps_per_rx_recipe_and_shared_epoch(self):
        topology = ReceiverTopologySnapshot("mock-stream", None, None, ("voltage0", "voltage1"),
            tuple(StreamScanElement(f"voltage{index}", chain, component, index, 16, 12, 0, True, False)
                  for index, (chain, component) in enumerate((
                      (ReceiverChain.RX1, IqComponent.IN_PHASE), (ReceiverChain.RX1, IqComponent.QUADRATURE),
                      (ReceiverChain.RX2, IqComponent.IN_PHASE), (ReceiverChain.RX2, IqComponent.QUADRATURE)))))
        request = PairedLiveRequest("mock-device", "mock-session", topology,
            LiveConfiguration(), "actual-rx1-test", "actual-rx2-test")
        context = LiveSnapshot(4, 0, LiveSessionState.RUNNING, session_id="mock-session",
            active_config_generation=4, acquisition_epoch=8, clock_domain="host_steady_ns")
        frames = []
        for source, receiver in ((request.primary_source_id, "RX1"), (request.secondary_source_id, "RX2")):
            frame = _make_frame(3, source_id=source, config_generation=4, quality_flags=DC_REMOVED_MASK)
            frame.source.metadata_json = {"receiver_selection": json.dumps(receiver)}
            frame.first_sample_index = 123
            frame.dsp_processing_recipe = recipe(HostDcMode.BLOCK_MEAN)
            frames.append(frame)
        value = SimpleNamespace(primary=frames[0], secondary=frames[1], config_generation=4,
            first_sample_index=123, timestamp_ns=frames[0].timestamp_ns,
            synchronization_epoch=12, shared_input_gaps_before=0)
        service = NativeLiveSessionService(_FakeNative())
        pair = pair_publication(value, context, request, service._convert_spectrum,
            (None, None), lambda _frame, quality: quality)
        for snapshot, receiver in ((pair.primary, "RX1"), (pair.secondary, "RX2")):
            self.assertEqual(snapshot.receiver_id, receiver)
            self.assertEqual(snapshot.acquisition_epoch, 8)
            self.assertIs(snapshot.spectrum.numerical_provenance.processing_recipe.dc_mode, HostDcMode.BLOCK_MEAN)
        self.assertEqual(pair.synchronization_epoch, 12)
        frames[1].source.metadata_json["receiver_selection"] = json.dumps("RX1")
        with self.assertRaisesRegex(ValueError, "foreign"):
            pair_publication(value, context, request, service._convert_spectrum,
                (None, None), lambda _frame, quality: quality)


class NativeProcessingRecipeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native module unavailable") from error
        if not hasattr(cls.native, "dsp_processing_contract"):
            raise unittest.SkipTest("legacy native has no DSP recipe")

    def test_actual_cpu_to_common_mapper_and_live_preserves_recipe_and_values(self):
        n = self.native
        for mode in HostDcMode:
            backend = n.make_cpu_dsp_backend_for_policy_v1(SdrProcessingPolicyV1(mode).canonical_bytes())
            backend.configure(n.DspConfig(256, 256, n.WindowType.RECTANGULAR, n.DetectorType.SAMPLE,
                n.SpectrumUnit.DBFS_BIN, n.PrecisionMode.REFERENCE_F64, 1, 1, 8.6,
                n.CalibrationStatus.UNCALIBRATED, "", 5))
            backend.push_samples(np.full(256, .25 + .125j, dtype=np.complex64), 61.44e6, 100e6)
            frames = backend.poll_spectrum()
            self.assertEqual(len(frames), 1)
            frame = frames[0]
            observed = native_spectrum_provenance(frame).processing_recipe
            self.assertIs(observed.dc_mode, mode)
            self.assertEqual(observed.policy_digest, frame.dsp_processing_recipe.policy_digest)
            # SAME actual clock/ready protocol as the compiled producer. No
            # selection/Start: constructing this service never opens hardware.
            service = NativeLiveSessionService(n)
            self.assertTrue(service._publish_frame(frame), service.latest_snapshot().error)
            converted = service.latest_snapshot().spectrum
            self.assertIs(converted.numerical_provenance.processing_recipe, observed)
            self.assertEqual(converted.native_quality_flags, frame.quality_flags)
            np.testing.assert_array_equal(converted.values, frame.values)
            np.testing.assert_array_equal(converted.frequencies_hz, frame.frequencies_hz)
            self.assertEqual(converted.center_frequency_hz, 100e6)
            self.assertEqual(converted.sample_rate_hz, 61.44e6)
            self.assertFalse(converted.values.flags.writeable)

    def test_actual_legacy_native_constructor_remains_unknown(self):
        frame = self.native._make_test_spectrum_frame(16)
        self.assertIsNone(frame.dsp_processing_recipe)
        self.assertIsNone(native_spectrum_provenance(frame).processing_recipe)
