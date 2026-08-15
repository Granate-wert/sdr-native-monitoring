"""R11-M no-device HackRF Live admission tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import unittest

from sdr_monitor.domain import (
    BackendKind,
    CapabilityEvidenceOrigin,
    CapabilityField,
    DeviceCalibrationIdentity,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    stable_identity_key,
)
from sdr_monitor.services import (
    HACKRF_LIBHACKRF_ADAPTER_ID,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfLiveAdmissionReason,
    HackrfLiveRequest,
    HackrfReadOnlyProbe,
    admit_hackrf_live,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "sdr_monitor/services/hackrf_live_admission.py"


class _FakeReadOnlyPort:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def probe(self) -> HackrfReadOnlyProbe:
        self.calls.append("probe")
        return HackrfReadOnlyProbe(
            HackrfBoardKind.HACKRF_ONE,
            (0, 0, 0x010961DC, 0x2B78454F),
            "2024.02.1",
            0x0107,
        )

    def close(self) -> None:
        self.calls.append("close")


def _observed() -> tuple[DeviceCapabilitySnapshot, DeviceCalibrationIdentity, _FakeReadOnlyPort]:
    port = _FakeReadOnlyPort()
    observation = HackrfCapabilityAdapter(lambda: port).observe()
    return observation.snapshot, observation.calibration_identity, port


def _request(**changes: object) -> HackrfLiveRequest:
    values: dict[str, object] = {
        "center_frequency_hz": 100e6,
        "sample_rate_hz": 10e6,
        "baseband_filter_hz": 8_000_000,
        "lna_gain_db": 16,
        "vga_gain_db": 20,
        "rf_amplifier_enabled": False,
        "bias_tee_enabled": False,
        "fft_size": 4096,
        "hop_size": 2048,
        "window": "hann",
        "detector": "sample",
        "backend": BackendKind.CPU,
        "persistence_enabled": False,
        "slot_count": 32,
        "ready_capacity": 24,
        "dsp_output_capacity": 8,
        "presentation_capacity": 4,
        "configuration_generation": 7,
        "source_id": "native.hackrf.live",
    }
    values.update(changes)
    return HackrfLiveRequest(**values)  # type: ignore[arg-type]


class R11MHackrfLiveAdmissionTests(unittest.TestCase):
    def test_matching_observation_and_request_produce_complete_plan_without_io(self) -> None:
        snapshot, identity, port = _observed()
        request = _request()

        result = admit_hackrf_live(snapshot, identity, request)

        self.assertTrue(result.accepted)
        self.assertIsNone(result.reason)
        assert result.plan is not None
        self.assertEqual(result.plan.device_id, snapshot.device_id)
        self.assertEqual(result.plan.identity_key, snapshot.identity_key)
        self.assertEqual(result.plan.adapter_id, HACKRF_LIBHACKRF_ADAPTER_ID)
        self.assertIs(result.plan.request, request)
        # Only the explicitly prior capability observation touched its fake port.
        self.assertEqual(port.calls, ["probe", "close"])

    def test_snapshot_and_identity_gates_fail_closed_with_a_typed_reason(self) -> None:
        snapshot, identity, _port = _observed()
        transport_vendor_only = tuple(
            replace(item, origin=CapabilityEvidenceOrigin.VENDOR_DECLARATION)
            if item.field is CapabilityField.TRANSPORT
            else item
            for item in snapshot.evidence
        )
        cases = (
            (replace(snapshot, family=DeviceFamily.AD936X), identity, HackrfLiveAdmissionReason.DEVICE_FAMILY),
            (replace(snapshot, adapter_id="native.other.v1"), identity, HackrfLiveAdmissionReason.ADAPTER),
            (replace(snapshot, rx_channel_count=2), identity, HackrfLiveAdmissionReason.ACQUISITION_KIND),
            (replace(snapshot, raw_iq_available=False), identity, HackrfLiveAdmissionReason.RAW_IQ),
            (replace(snapshot, evidence=transport_vendor_only), identity, HackrfLiveAdmissionReason.TRANSPORT_PROVENANCE),
            (
                snapshot,
                replace(identity, device_identity_key=stable_identity_key("different-hackrf")),
                HackrfLiveAdmissionReason.IDENTITY,
            ),
        )
        for candidate, candidate_identity, reason in cases:
            with self.subTest(reason=reason):
                result = admit_hackrf_live(candidate, candidate_identity, _request())
                self.assertFalse(result.accepted)
                self.assertIsNone(result.plan)
                self.assertIs(result.reason, reason)

    def test_capability_ranges_reject_before_any_factory_exists(self) -> None:
        snapshot, identity, _port = _observed()
        cases = (
            (_request(center_frequency_hz=6.1e9), HackrfLiveAdmissionReason.CENTER_FREQUENCY),
            (_request(sample_rate_hz=21e6), HackrfLiveAdmissionReason.SAMPLE_RATE),
        )
        for request, reason in cases:
            with self.subTest(reason=reason):
                result = admit_hackrf_live(snapshot, identity, request)
                self.assertFalse(result.accepted)
                self.assertIs(result.reason, reason)

    def test_request_rejects_noncanonical_dsp_or_unbounded_profile_before_admission(self) -> None:
        invalid = (
            {"baseband_filter_hz": 1_000_000},
            {"lna_gain_db": 13},
            {"vga_gain_db": 13},
            {"rf_amplifier_enabled": True},
            {"bias_tee_enabled": True},
            {"fft_size": 3000},
            {"hop_size": 4097},
            {"window": "not-a-window"},
            {"detector": "not-a-detector"},
            {"backend": BackendKind.CUDA},
            {"persistence_enabled": True},
            {"slot_count": 65},
            {"ready_capacity": 33, "slot_count": 32},
            {"source_id": "usb:secret-route"},
        )
        for changes in invalid:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    _request(**changes)

    def test_module_is_admission_only_and_does_not_import_runtime_or_product_paths(self) -> None:
        text = SOURCE.read_text(encoding="utf-8").lower()
        for forbidden in (
            "hackrf.h",
            "hackrf.dll",
            "ctypes",
            "importlib",
            "make_official",
            "_sdr_native",
            "start_rx",
            "start_tx",
            "native_live_session",
            "recording",
            "sweep",
            "pyside",
            "qwidget",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)
        self.assertIn("def admit_hackrf_live", text)


if __name__ == "__main__":
    unittest.main()
