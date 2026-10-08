"""Actual native AD config/CPU conversion plus separately labelled mock owner tests.

No SDK discovery, RF Start, recording, UI or hardware qualification.
"""

from dataclasses import replace
import importlib
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

import numpy as np

from sdr_monitor.domain.live import (
    AppliedLiveConfiguration, BackendKind, DeviceCapabilities, DeviceDescriptor,
    DeviceTransport, LiveAdmissionRejected, LiveConfiguration, LiveSessionState, LiveSnapshot,
)
from sdr_monitor.domain.analytical_ready import DetectorReadyReceipt, ReadyClockMapping
from sdr_monitor.domain.paired_live import PairedLiveRequest
from sdr_monitor.domain.processing_policy import (
    HostDcMode, HostSpurMode, SdrProcessingPolicyV1, SpurProfileReference, validate_processing_receipt,
)
from sdr_monitor.domain.receiver_topology import (
    IqComponent, ReceiverChain, ReceiverChainSelection, ReceiverEndpoint, ReceiverTopologySnapshot, StreamScanElement,
)
from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext, build_live_calibration_signature
from sdr_monitor.services.live_processing import LiveProcessingJoin, admit_ad_processing
from sdr_monitor.services.native_live import NativeLiveSessionService, build_native_fixed_band_config
from sdr_monitor.services.native_spectrum_provenance import native_spectrum_provenance
from tests.test_s15_live_rx_bridge import _FakeNative
from tests.native_test_dependencies import explicit_native_dependencies


def topology():
    return ReceiverTopologySnapshot("observed-test-resource", None, None, ("voltage0", "voltage1"),
        tuple(StreamScanElement(f"voltage{i}", chain, component, i, 16, 12, 0, True, False)
            for i, (chain, component) in enumerate((
                (ReceiverChain.RX1, IqComponent.IN_PHASE), (ReceiverChain.RX1, IqComponent.QUADRATURE),
                (ReceiverChain.RX2, IqComponent.IN_PHASE), (ReceiverChain.RX2, IqComponent.QUADRATURE)))))


def configuration(mode=HostDcMode.BLOCK_MEAN):
    return LiveConfiguration(center_hz=100e6, sample_rate_hz=61.44e6, analog_bandwidth_hz=56e6,
        fft_size=256, overlap_ratio=0, window="rectangular", backend=BackendKind.CPU,
        persistence_enabled=False, persistence_mode="disabled", processing_policy=SdrProcessingPolicyV1(mode))


def context(config, source="actual-test-source", receiver="RX1"):
    device = DeviceDescriptor(source, "test-only", "usb:test-only", DeviceTransport.USB,
        DeviceCapabilities((61.44e6,), (0., 73.), receiver_topology=topology()))
    return LiveSnapshot(7, 0, LiveSessionState.RUNNING, device=device,
        applied=AppliedLiveConfiguration(config, config,
            readback_fields=("center_hz", "sample_rate_hz", "analog_bandwidth_hz")),
        session_id="actual-test-session", active_source_id=source, receiver_id=receiver,
        active_config_generation=1, acquisition_epoch=9, clock_domain="host_steady_ns")


def mock_ready(current, receiver="RX1"):
    # Fabricated DOMAIN receipt for adapter negative/composition tests ONLY.
    # Actual owner authentication is qualified separately using mock-IIO owner.
    return DetectorReadyReceipt("mock-adapter", 1, 1, 1, current.active_config_generation,
        1, current.active_source_id, receiver, current.acquisition_epoch, current.session_id,
        ReadyClockMapping.OUTSIDE_SAMPLES, owner_run_id="MOCK-OWNER-TEST-ONLY")


class LiveProcessingAdmissionTests(unittest.TestCase):
    def test_default_off_and_legacy_config_builder_unchanged(self):
        default = LiveConfiguration()
        self.assertTrue(default.processing_policy.is_off)
        native = _FakeNative()
        self.assertFalse(admit_ad_processing(native, default))
        built = build_native_fixed_band_config(native, default, "usb:test-only")
        self.assertIs(built.args[10], False)
        with self.assertRaises(ValueError):
            replace(default, processing_policy="block_mean_v1")

    def test_non_off_gpu_auto_unknown_protocol_and_spur_refuse_before_constructor(self):
        native = _FakeNative()
        config = configuration()
        profile = SpurProfileReference("test", 1, "sha256:" + "a" * 64, "sha256:" + "b" * 64)
        requests = [config, replace(config, backend=BackendKind.AUTO),
            replace(config, backend=BackendKind.CUDA), replace(config, backend=BackendKind.HIP),
            replace(config, processing_policy=SdrProcessingPolicyV1(compare_raw=True)),
            replace(config, processing_policy=SdrProcessingPolicyV1(
                spur_mode=HostSpurMode.CANDIDATES, spur_profile=profile))]
        with patch.object(native, "DeviceConfig", side_effect=AssertionError("effect before refusal")):
            for request in requests:
                with self.subTest(policy=request.processing_policy, backend=request.backend), \
                        self.assertRaises(LiveAdmissionRejected):
                    build_native_fixed_band_config(native, request, "usb:test-only")
        native.dsp_processing_contract = lambda: {"schema_version": True}
        with self.assertRaises(LiveAdmissionRejected):
            admit_ad_processing(native, config)

    def test_running_policy_change_refuses_before_route_refresh_or_stop(self):
        service = NativeLiveSessionService(_FakeNative())
        service._snapshot = context(configuration(HostDcMode.OFF))
        before = service.latest_snapshot()
        with patch.object(service, "_refresh_operational_usb_alias", side_effect=AssertionError("RF")), \
                patch.object(service, "_stop_unlocked", side_effect=AssertionError("hidden Stop")), \
                self.assertRaises(LiveAdmissionRejected):
            service.apply_configuration(configuration())
        self.assertIs(service.latest_snapshot(), before)
        self.assertEqual(service._processing_revision, 1)

    def test_recording_now_and_arm_refuse_before_restart_or_writer_mutation(self):
        service = NativeLiveSessionService(_FakeNative())
        service._snapshot = context(configuration())
        epoch = service._native_recording_epoch
        with patch.object(service, "_stop_unlocked", side_effect=AssertionError("hidden Stop")):
            for action in (service.arm_native_recording, service.start_native_recording_now):
                with self.assertRaises(LiveAdmissionRejected):
                    action(None)
        self.assertEqual(service._native_recording_epoch, epoch)
        self.assertIsNone(service._native_recording_armed)

    def test_processed_sweep_refuses_before_refresh_or_lease(self):
        service = NativeLiveSessionService(_FakeNative())
        service._snapshot = replace(context(configuration()), state=LiveSessionState.CONNECTED)
        with patch.object(service, "_refresh_operational_usb_alias", side_effect=AssertionError("RF")), \
                self.assertRaises(LiveAdmissionRejected):
            service.acquire_native_sweep_lease()
        self.assertFalse(service._sweep_lease_active)


class NativeLiveProcessingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.native = importlib.import_module("sdr_monitor._sdr_native")
        except ImportError as error:
            raise unittest.SkipTest("optional native module unavailable") from error
        if not hasattr(cls.native, "dsp_processing_contract"):
            raise unittest.SkipTest("matching native recipe protocol required")

    def frame(self, mode=HostDcMode.BLOCK_MEAN):
        n = self.native
        backend = n.make_cpu_dsp_backend_for_policy_v1(SdrProcessingPolicyV1(mode).canonical_bytes())
        backend.configure(n.DspConfig(256, 256, n.WindowType.RECTANGULAR, n.DetectorType.SAMPLE,
            n.SpectrumUnit.DBFS_BIN, n.PrecisionMode.REFERENCE_F64, 1, 1, 8.6,
            n.CalibrationStatus.UNCALIBRATED, "", 5))
        backend.push_samples(np.full(256, .25 + .125j, dtype=np.complex64), 61.44e6, 100e6)
        return backend.poll_spectrum()[0]

    def test_actual_fixed_config_and_paired_builder_apply_same_native_boolean(self):
        n = self.native
        for mode in HostDcMode:
            config = configuration(mode)
            fixed = build_native_fixed_band_config(n, config, "usb:test-only", source_id="not-opened")
            self.assertEqual(fixed.dc_removal_block_mean, mode is HostDcMode.BLOCK_MEAN)
            self.assertEqual(fixed.dsp.fft_size, config.fft_size)
            self.assertEqual(fixed.device.sample_rate_hz, config.sample_rate_hz)
            service = NativeLiveSessionService(n)
            request = PairedLiveRequest("test-device", "test-session", topology(), config, "one", "two")
            paired = service._paired_native_config(request, "usb:test-only")
            for built in (paired.primary, paired.secondary):
                self.assertEqual(built.dc_removal_block_mean, mode is HostDcMode.BLOCK_MEAN)
            self.assertEqual(paired.primary.device.source_id, "one")
            self.assertEqual(paired.secondary.device.source_id, "two")

    def test_actual_cpu_same_converter_with_explicitly_mocked_owner_receipt(self):
        for mode in HostDcMode:
            frame = self.frame(mode)
            current = context(configuration(mode), frame.source.source_id)
            service = NativeLiveSessionService(self.native)
            service._processing_revision = 17
            with patch.object(service._ready_bridge, "convert", return_value=mock_ready(current)):
                converted = service._convert_spectrum(frame, current)
            receipt = converted.processing_context
            self.assertIsNotNone(receipt)
            self.assertEqual(receipt.frame_key.resource_id, "observed-test-resource")
            self.assertIs(receipt.frame_key.receiver, ReceiverChain.RX1)
            self.assertEqual(receipt.frame_key.acquisition_epoch, 9)
            self.assertEqual(receipt.frame_key.config_generation, 1)
            self.assertEqual(receipt.processing_revision, 17)
            validate_processing_receipt(configuration(mode).processing_policy,
                receipt.frame_key, 17, receipt)
            np.testing.assert_array_equal(converted.values, frame.values)
            np.testing.assert_array_equal(converted.frequencies_hz, frame.frequencies_hz)
            self.assertEqual(converted.native_quality_flags, frame.quality_flags)
            with self.assertRaises(ValueError):
                replace(converted, receiver_id="RX2")
            with self.assertRaises(ValueError):
                replace(converted, processing_context=replace(receipt,
                    frame_key=replace(receipt.frame_key, acquisition_epoch=10)))

    def test_incomplete_stale_foreign_policy_config_rf_and_recipe_refuse(self):
        frame = self.frame()
        current = context(configuration(), frame.source.source_id)
        bad = [replace(current, acquisition_epoch=None), replace(current, acquisition_epoch=10),
            replace(current, active_config_generation=2),
            replace(current, state=LiveSessionState.CONNECTED), replace(current, receiver_id=None),
            replace(current, active_source_id="foreign"),
            replace(current, applied=replace(current.applied, readback_fields=())),
            replace(current, applied=replace(current.applied,
                applied=replace(current.applied.applied, center_hz=101e6))),
            replace(current, applied=replace(current.applied,
                applied=configuration(HostDcMode.OFF)))]
        for value in bad:
            service = NativeLiveSessionService(self.native)
            with self.subTest(current=value), patch.object(service._ready_bridge, "convert", return_value=mock_ready(current)), \
                    self.assertRaises(ValueError):
                service._convert_spectrum(frame, value)
        with self.assertRaises(ValueError):
            NativeLiveSessionService(self.native)._convert_spectrum(self.frame(HostDcMode.OFF), current)

    def test_grid_cache_bounded_no_rehash_and_explicit_revision_not_generation_alias(self):
        frame = self.frame()
        current = context(configuration(), frame.source.source_id)
        provenance = native_spectrum_provenance(frame)
        join = LiveProcessingJoin()
        ready = mock_ready(current)
        first = join.receipt(frame, current, provenance, "dBFS/bin", frame.source.source_id, "RX1", 33,
            detector_ready=ready)
        with patch("sdr_monitor.services.live_processing.sha256", side_effect=AssertionError("rehash")):
            second = join.receipt(frame, current, provenance, "dBFS/bin", frame.source.source_id, "RX1", 34,
                detector_ready=ready)
        self.assertEqual(first.frame_key.grid_digest, second.frame_key.grid_digest)
        self.assertEqual((first.processing_revision, second.processing_revision), (33, 34))
        self.assertEqual(len(join._grids), 1)
        join.clear()
        self.assertEqual(len(join._grids), 0)

    def test_actual_cpu_recipe_alone_never_invents_authenticated_owner_context(self):
        frame = self.frame()
        current = context(configuration(), frame.source.source_id)
        service = NativeLiveSessionService(self.native)
        with self.assertRaisesRegex(ValueError, "complete SAME owner"):
            service._convert_spectrum(frame, current)
        for ready in (None, replace(mock_ready(current), owner_run_id=None)):
            with self.assertRaisesRegex(ValueError, "complete SAME owner"):
                service._processing_join.receipt(frame, current, native_spectrum_provenance(frame),
                    "dBFS/bin", frame.source.source_id, "RX1", 2, detector_ready=ready)

    def test_modified_recipe_cannot_borrow_raw_calibration_signature(self):
        frame = self.frame()
        current = context(configuration(), frame.source.source_id)
        endpoint = ReceiverEndpoint("test-endpoint", frame.source.source_id,
            topology().physical_stream_resource_id, ReceiverChainSelection.RX1)
        with self.assertRaisesRegex(CalibrationProfileError, "non-OFF host processing"):
            build_live_calibration_signature(current.device, endpoint, current.applied,
                native_spectrum_provenance(frame), CalibrationFrontendContext("mock-port", "mock-chain", "mock-ref"),
                unit="dBFS/bin")

    def test_stopped_apply_changes_revision_once_without_factory_start(self):
        fake = _FakeNative()
        fake.dsp_processing_contract = self.native.dsp_processing_contract
        fake.WindowType = self.native.WindowType
        fake.DetectorType = self.native.DetectorType
        service = NativeLiveSessionService(fake)
        current = replace(context(configuration(HostDcMode.OFF)), state=LiveSessionState.CONNECTED)
        service._snapshot = current
        service._selected = current.device
        with patch.object(service, "_refresh_operational_usb_alias"), \
                patch.object(fake, "PlutoFixedBandEngine", side_effect=AssertionError("implicit Start")):
            service.apply_configuration(configuration())
            self.assertEqual(service._processing_revision, 2)
            service.apply_configuration(configuration())
            self.assertEqual(service._processing_revision, 2)
            service.apply_configuration(configuration(HostDcMode.OFF))
            self.assertEqual(service._processing_revision, 3)
        self.assertFalse(service.is_running())
        self.assertIsNone(service.latest_snapshot().spectrum)

    def test_non_off_missing_topology_refuses_before_staging(self):
        fake = _FakeNative()
        fake.dsp_processing_contract = self.native.dsp_processing_contract
        fake.WindowType = self.native.WindowType
        fake.DetectorType = self.native.DetectorType
        service = NativeLiveSessionService(fake)
        current = context(configuration(HostDcMode.OFF))
        current = replace(current, state=LiveSessionState.CONNECTED, device=replace(current.device,
            capabilities=replace(current.device.capabilities, receiver_topology=None)))
        service._snapshot = current
        with patch.object(service, "_refresh_operational_usb_alias", side_effect=AssertionError("RF")), \
                self.assertRaises(LiveAdmissionRejected):
            service.apply_configuration(configuration())
        self.assertIs(service.latest_snapshot(), current)

    def test_aliases_match_actual_native_config_not_raw_requested_spelling(self):
        n = self.native
        for window, detector in (("hanning", "positive-peak"), ("blackman-harris", "average"),
                                 ("flat top", "negative_peak"), ("nuttall", "rms")):
            config = replace(configuration(), window=window, detector=detector)
            native_config = build_native_fixed_band_config(n, config, "usb:not-opened")
            backend = n.make_cpu_dsp_backend_for_policy_v1(config.processing_policy.canonical_bytes())
            backend.configure(native_config.dsp)
            backend.push_samples(np.full(256, .25 + .125j, dtype=np.complex64), 61.44e6, 100e6)
            frame = backend.poll_spectrum()[0]
            current = context(config, frame.source.source_id)
            service = NativeLiveSessionService(n)
            with patch.object(service._ready_bridge, "convert", return_value=mock_ready(current)):
                converted = service._convert_spectrum(frame, current)
            self.assertIsNotNone(converted.processing_context)
            self.assertEqual(converted.numerical_provenance.window, native_config.dsp.window.name.lower())
            self.assertEqual(converted.numerical_provenance.detector, native_config.dsp.detector.name.lower())
        for field in ("window", "detector"):
            with self.assertRaises(LiveAdmissionRejected):
                build_native_fixed_band_config(n, replace(configuration(), **{field: "unsupported"}), "usb:not-opened")

    def test_actual_mock_iio_owner_stage_start_both_modes_and_paired_context(self):
        module = os.environ.get("SDR_APP07_TEST_NATIVE_MODULE", "")
        mock = os.environ.get("SDR_APP07_TEST_MOCK_LIBIIO", "")
        if not module or not Path(mock).is_file():
            self.skipTest("explicit matching native + mock IIO required")
        env = dict(os.environ, LIBIIO_DLL_PATH=mock, SDR_MOCK_LIBIIO_CONTEXT_NAME="usb",
            SDR_MOCK_LIBIIO_BACKEND_URI="usb:2.42.5", SDR_MOCK_LIBIIO_TOPOLOGY_DUAL="1",
            SDR_MOCK_LIBIIO_REFILL_DELAY_MS="1")
        result = subprocess.run([sys.executable, "-m", "tests.test_live_processing", "--mock-owner", module],
            cwd=Path(__file__).resolve().parents[1], env=env, text=True, capture_output=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ACTUAL MOCK OWNER OFF/BLOCKMEAN/PAIRED PASS", result.stdout)


@explicit_native_dependencies
def run_mock_owner(module_path):
    # Child uses only the explicitly selected MOCK IIO DLL before native load.
    # No real SDK discovery/probe or RF Start is performed.
    spec = importlib.util.spec_from_file_location("_sdr_native", module_path)
    n = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(n)
    service = NativeLiveSessionService(n)
    captured = []
    original_convert = service._convert_spectrum
    def capture(frame, *args, **kwargs):
        if not captured:
            captured.append(frame)
        return original_convert(frame, *args, **kwargs)
    service._convert_spectrum = capture
    try:
        device = service.discover_devices()[0]
        service.select_device(device.device_id)
        first_epoch = None
        for mode in (HostDcMode.BLOCK_MEAN, HostDcMode.OFF):
            config = replace(configuration(mode), center_hz=2450e6,
                sample_rate_hz=20e6, analog_bandwidth_hz=20e6)
            staged = service.apply_configuration(config)
            assert staged.spectrum is None and not service.is_running(), staged
            started = service.start()
            assert started.state is LiveSessionState.RUNNING, started.error
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and service.latest_snapshot().spectrum is None:
                time.sleep(.005)
            snapshot = service.latest_snapshot()
            assert snapshot.spectrum is not None, snapshot.error
            receipt = snapshot.spectrum.processing_context
            assert receipt is not None and receipt.dc_mode is mode, snapshot.error
            assert snapshot.spectrum.detector_ready.owner_run_id is not None
            assert receipt.processing_revision == (2 if mode is HostDcMode.BLOCK_MEAN else 3)
            assert receipt.frame_key.resource_id == device.capabilities.receiver_topology.physical_stream_resource_id
            if first_epoch is None:
                first_epoch = receipt.frame_key.acquisition_epoch
            else:
                assert receipt.frame_key.acquisition_epoch > first_epoch
            assert service.stop().state is LiveSessionState.CONNECTED
            if mode is HostDcMode.BLOCK_MEAN:
                # SAME policy/config/source numbers after a fresh actual owner
                # are insufficient: old native producer instance must refuse.
                restarted = service.start()
                assert restarted.state is LiveSessionState.RUNNING, restarted.error
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and service.latest_snapshot().spectrum is None:
                    time.sleep(.005)
                fresh = service.latest_snapshot()
                assert fresh.spectrum is not None, fresh.error
                assert fresh.spectrum.processing_context.processing_revision == 2
                assert fresh.spectrum.detector_ready.owner_run_id != snapshot.spectrum.detector_ready.owner_run_id
                try:
                    original_convert(captured[0], fresh)
                except ValueError as error:
                    assert "SAME admitted owner journal" in str(error), error
                else:
                    raise AssertionError("old native producer adopted new host epoch")
                assert service.stop().state is LiveSessionState.CONNECTED
        config = replace(config, processing_policy=SdrProcessingPolicyV1(HostDcMode.BLOCK_MEAN))
        staged = service.apply_configuration(config)
        service.stage_paired_rtbw(PairedLiveRequest(device.device_id, str(staged.session_id),
            device.capabilities.receiver_topology, staged.applied.applied, "paired-one", "paired-two"))
        started = service.start()
        assert started.state is LiveSessionState.RUNNING, started.error
        deadline = time.monotonic() + 5
        pairs = ()
        while time.monotonic() < deadline and not pairs:
            pairs = service.poll_paired_frames()
            time.sleep(.005)
        assert pairs, service.latest_snapshot().error
        pair = pairs[0]
        receipts = (pair.primary.spectrum.processing_context, pair.secondary.spectrum.processing_context)
        assert all(r is not None and r.dc_mode is HostDcMode.BLOCK_MEAN for r in receipts)
        assert receipts[0].frame_key.receiver is ReceiverChain.RX1
        assert receipts[1].frame_key.receiver is ReceiverChain.RX2
        assert receipts[0].frame_key.source_id == "paired-one" and receipts[1].frame_key.source_id == "paired-two"
        assert receipts[0].frame_key.resource_id == receipts[1].frame_key.resource_id
        assert receipts[0].frame_key.acquisition_epoch == receipts[1].frame_key.acquisition_epoch
        assert receipts[0].processing_revision == receipts[1].processing_revision == 4
        print("ACTUAL MOCK OWNER OFF/BLOCKMEAN/PAIRED PASS", flush=True)
    finally:
        service.stop()
        service.close_capability_observation()
        assert not service.is_running() and service._engine is None and service._poller is None


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--mock-owner":
        run_mock_owner(sys.argv[2])
    else:
        unittest.main()
