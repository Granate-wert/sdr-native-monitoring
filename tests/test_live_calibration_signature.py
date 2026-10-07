"""Exact signature joins with synthetic admitted facts; no hardware proof."""

from dataclasses import replace
from types import SimpleNamespace
import unittest

from sdr_monitor.domain.calibration import CalibrationProfileError
from sdr_monitor.domain.device_capabilities import DeviceCalibrationIdentity, DeviceFamily
from sdr_monitor.domain.live import AppliedLiveConfiguration, BackendKind, ObservedGainMode
from sdr_monitor.domain.receiver_topology import ReceiverChain, ReceiverChainSelection, ReceiverEndpoint
from sdr_monitor.domain.spectrum_provenance import SpectrumProvenance
from sdr_monitor.services.live_calibration_signature import CalibrationFrontendContext, build_live_calibration_signature
from sdr_monitor.services.native_spectrum_provenance import native_spectrum_provenance
from tests.test_app07_paired_sweep_contract import request_fixture


class LiveCalibrationSignatureTests(unittest.TestCase):
    def setUp(self):
        request = request_fixture()
        device = request.selected_snapshot.device
        self.device = replace(device, calibration_identity=DeviceCalibrationIdentity(
            DeviceFamily.AD936X, device.capability_snapshot.adapter_id,
            device.capability_snapshot.identity_key, "sha256:" + "2" * 64,
        ))
        self.endpoint = ReceiverEndpoint("endpoint", self.device.device_id, "mock-device", ReceiverChainSelection.RX1)
        config = request.pair.configuration
        self.applied = AppliedLiveConfiguration(
            config, config, readback_fields=("center_hz", "sample_rate_hz", "analog_bandwidth_hz", "gain_db", "gain_mode"),
            observed_gain_mode=ObservedGainMode.MANUAL,
        )
        self.provenance = SpectrumProvenance(window="hann", window_normalization_version="synthetic-test-v1")
        self.frontend = CalibrationFrontendContext("declared-input", "reference-cable", "rf_input")

    def build(self, **changes):
        arguments = dict(device=self.device, endpoint=self.endpoint, applied=self.applied,
                         provenance=self.provenance, frontend=self.frontend, unit="dBFS/bin")
        arguments.update(changes)
        return build_live_calibration_signature(**arguments)

    def test_exact_readbacks_and_both_individual_rx_signatures(self):
        for selection, chain in ((ReceiverChainSelection.RX1, ReceiverChain.RX1),
                                 (ReceiverChainSelection.RX2, ReceiverChain.RX2)):
            signature = self.build(endpoint=replace(self.endpoint, selection=selection))
            self.assertIs(signature.receiver_chain, chain)
            self.assertEqual(signature.sample_rate_hz, 61.44e6)
            self.assertEqual(signature.analog_bandwidth_hz, 56e6)
            self.assertEqual(signature.device_identity_key, self.device.identity_key)
            self.assertEqual(signature.window_normalization_version, "synthetic-test-v1")
            self.assertEqual(signature.rf_port_path, "declared-input")
        self.assertEqual(self.build(unit="dBFS/Hz").fft_unit_convention, "dBFS/Hz")

    def test_missing_each_readback_refuses_instead_of_requested_fallback(self):
        for missing in self.applied.readback_fields:
            with self.subTest(missing=missing), self.assertRaises(CalibrationProfileError):
                self.build(applied=replace(self.applied, readback_fields=tuple(
                    item for item in self.applied.readback_fields if item != missing)))

    def test_agc_unknown_backend_and_absolute_input_refuse(self):
        for mode in (None, ObservedGainMode.SLOW_ATTACK, ObservedGainMode.FAST_ATTACK, ObservedGainMode.HYBRID):
            with self.subTest(mode=mode), self.assertRaises(CalibrationProfileError):
                self.build(applied=replace(self.applied, observed_gain_mode=mode))
        with self.assertRaises(CalibrationProfileError):
            self.build(applied=replace(self.applied, applied=replace(self.applied.applied, backend=BackendKind.AUTO)))
        for unit in ("dBm", "dBm/Hz", "unknown"):
            with self.subTest(unit=unit), self.assertRaises(CalibrationProfileError):
                self.build(unit=unit)

    def test_identity_source_resource_and_both_refuse(self):
        with self.assertRaises(CalibrationProfileError):
            self.build(device=replace(self.device, calibration_identity=None))
        with self.assertRaises(CalibrationProfileError):
            self.build(device=replace(self.device, capability_snapshot=replace(
                self.device.capability_snapshot, device_id="other")))
        for changes in (dict(source_id="other"), dict(physical_stream_resource_id="other"),
                        dict(selection=ReceiverChainSelection.BOTH)):
            with self.subTest(changes=changes), self.assertRaises(CalibrationProfileError):
                self.build(endpoint=replace(self.endpoint, **changes))
        with self.assertRaises(CalibrationProfileError):
            self.build(device=replace(self.device, capabilities=replace(self.device.capabilities, receiver_topology=None)))

    def test_old_native_producer_normalization_is_unknown_not_invented(self):
        old = native_spectrum_provenance(SimpleNamespace(window=SimpleNamespace(name="HANN")))
        self.assertIsNone(old.window_normalization_version)
        with self.assertRaises(CalibrationProfileError):
            self.build(provenance=old)
        with self.assertRaises(CalibrationProfileError):
            self.build(provenance=replace(self.provenance, window_normalization_version="unknown"))
        observed = native_spectrum_provenance(SimpleNamespace(
            window=SimpleNamespace(name="HANN"), window_normalization_version="synthetic-test-v1"))
        self.assertEqual(self.build(provenance=observed).window_normalization_version, "synthetic-test-v1")

    def test_frontend_and_normalization_are_explicit_bounded_text(self):
        for value in ("", "unknown", "x" * 129, None):
            with self.subTest(value=value), self.assertRaises(CalibrationProfileError):
                CalibrationFrontendContext(value, "reference-cable", "rf_input")
        for value in ("", "x" * 129, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SpectrumProvenance(window_normalization_version=value)
