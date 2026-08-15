"""R11-D injected/mock-only HackRF capability adapter tests."""

from __future__ import annotations

import unittest

from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityTransport,
    DeviceFamily,
)
from sdr_monitor.services.hackrf_capability_adapter import (
    HACKRF_LIBHACKRF_ADAPTER_ID,
    HackrfBoardKind,
    HackrfCapabilityAdapter,
    HackrfCapabilityObservationError,
    HackrfReadOnlyProbe,
)


class _FakePort:
    def __init__(
        self,
        probe: object,
        *,
        probe_error: Exception | None = None,
        close_error: Exception | None = None,
    ) -> None:
        self.value = probe
        self.probe_error = probe_error
        self.close_error = close_error
        self.calls: list[str] = []

    def probe(self) -> object:
        self.calls.append("probe")
        if self.probe_error is not None:
            raise self.probe_error
        return self.value

    def close(self) -> None:
        self.calls.append("close")
        if self.close_error is not None:
            raise self.close_error

    def open(self) -> None:
        raise AssertionError("adapter must not open a device")

    def start_rx(self) -> None:
        raise AssertionError("adapter must not start RX")

    def start_tx(self) -> None:
        raise AssertionError("adapter must not start TX")

    def set_freq(self) -> None:
        raise AssertionError("adapter must not retune")

    def allocate_transfer(self) -> None:
        raise AssertionError("adapter must not allocate a transfer")


def _probe() -> HackrfReadOnlyProbe:
    return HackrfReadOnlyProbe(
        HackrfBoardKind.HACKRF_ONE,
        (0x00000000, 0x00000000, 0x010961DC, 0x2B78454F),
        "2024.02.1",
        0x0107,
    )


class R11DHackrfCapabilityAdapterTests(unittest.TestCase):
    def test_vendor_and_runtime_facts_map_without_stream_or_control(self) -> None:
        port = _FakePort(_probe())
        observation = HackrfCapabilityAdapter(lambda: port).observe()
        snapshot = observation.snapshot

        self.assertIs(snapshot.family, DeviceFamily.HACKRF)
        self.assertEqual(snapshot.adapter_id, HACKRF_LIBHACKRF_ADAPTER_ID)
        self.assertEqual(snapshot.transports, (CapabilityTransport.USB,))
        self.assertEqual(snapshot.acquisition_kinds, (AcquisitionKind.COMPLEX_IQ,))
        self.assertEqual(snapshot.tuning_ranges_hz[0].minimum, 1e6)
        self.assertEqual(snapshot.tuning_ranges_hz[0].maximum, 6e9)
        self.assertEqual(snapshot.sample_rate_ranges_hz[0].minimum, 2e6)
        self.assertEqual(snapshot.sample_rate_ranges_hz[0].maximum, 20e6)
        self.assertEqual(snapshot.rx_channel_count, 1)
        self.assertIs(snapshot.raw_iq_available, True)
        self.assertEqual(port.calls, ["probe", "close"])
        self.assertTrue(snapshot.identity_key.startswith("sha256:"))
        self.assertTrue(observation.calibration_identity.firmware_fingerprint.startswith("sha256:"))
        self.assertIs(
            snapshot.evidence_for(CapabilityField.TRANSPORT).origin,
            CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
        )
        self.assertIs(
            snapshot.evidence_for(CapabilityField.TUNING_RANGE).origin,
            CapabilityEvidenceOrigin.VENDOR_DECLARATION,
        )

    def test_ambiguous_or_unproven_capabilities_stay_unknown(self) -> None:
        snapshot = HackrfCapabilityAdapter(lambda: _FakePort(_probe())).observe().snapshot
        self.assertEqual(snapshot.analog_bandwidth_ranges_hz, ())
        self.assertEqual(snapshot.gain_ranges_db, ())
        self.assertIsNone(snapshot.shared_rx_lo)
        self.assertIsNone(snapshot.hardware_timestamp_available)
        self.assertIsNone(snapshot.hardware_overflow_counter_available)
        for field in (
            CapabilityField.ANALOG_BANDWIDTH_RANGE,
            CapabilityField.GAIN_RANGE,
            CapabilityField.SHARED_RX_LO,
            CapabilityField.HARDWARE_TIMESTAMP,
            CapabilityField.HARDWARE_OVERFLOW_COUNTER,
        ):
            self.assertIs(snapshot.evidence_for(field).origin, CapabilityEvidenceOrigin.UNKNOWN)

    def test_failure_and_close_failure_are_generic_and_close_is_attempted(self) -> None:
        cases = (
            _FakePort(_probe(), probe_error=RuntimeError("USB secret path")),
            _FakePort(_probe(), close_error=RuntimeError("close secret path")),
            _FakePort(object()),
        )
        for port in cases:
            with self.subTest(port=port):
                with self.assertRaisesRegex(
                    HackrfCapabilityObservationError, "failed closed"
                ) as captured:
                    HackrfCapabilityAdapter(lambda port=port: port).observe()
                self.assertNotIn("secret", str(captured.exception).casefold())
                self.assertEqual(port.calls[-1], "close")

    def test_probe_contract_rejects_unsafe_identity_and_versions(self) -> None:
        invalid = (
            ((1, 2, 3), "2024.02.1", 0x0107),
            ((1, 2, 3, -1), "2024.02.1", 0x0107),
            ((1, 2, 3, 4), "usb:secret", 0x0107),
            ((1, 2, 3, 4), "2024.02.1", True),
            ((1, 2, 3, 4), "2024.02.1", 0x10000),
        )
        for serial, firmware, api in invalid:
            with self.subTest(serial=serial, firmware=firmware, api=api):
                with self.assertRaises(ValueError):
                    HackrfReadOnlyProbe(  # type: ignore[arg-type]
                        HackrfBoardKind.HACKRF_ONE, serial, firmware, api
                    )

    def test_output_is_capability_identity_only_not_calibration_result(self) -> None:
        observation = HackrfCapabilityAdapter(lambda: _FakePort(_probe())).observe()
        identity = observation.calibration_identity
        self.assertIs(identity.family, DeviceFamily.HACKRF)
        self.assertEqual(identity.adapter_id, HACKRF_LIBHACKRF_ADAPTER_ID)
        self.assertTrue(identity.device_identity_key.startswith("sha256:"))
        self.assertTrue(identity.firmware_fingerprint.startswith("sha256:"))
        self.assertEqual(
            tuple(observation.__dataclass_fields__),
            ("snapshot", "calibration_identity"),
        )


if __name__ == "__main__":
    unittest.main()
