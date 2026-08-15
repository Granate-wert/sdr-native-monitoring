"""Software-only acceptance tests for the tinySA read-only capability adapter."""

from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import cast

from sdr_monitor.domain.device_capabilities import (
    AcquisitionKind,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityTransport,
    DeviceFamily,
)
from sdr_monitor.services.tinysa_capability_adapter import (
    TINYSA_READ_ONLY_ADAPTER_ID,
    TinySaCapabilityAdapter,
    TinySaCapabilityObservationError,
    TinySaModel,
    TinySaReadOnlyProbe,
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

    def probe(self) -> TinySaReadOnlyProbe:
        self.calls.append("probe")
        if self.probe_error is not None:
            raise self.probe_error
        return cast(TinySaReadOnlyProbe, self.value)

    def close(self) -> None:
        self.calls.append("close")
        if self.close_error is not None:
            raise self.close_error

    def open_serial(self) -> None:
        raise AssertionError("adapter must not open a serial/USB transport")

    def start_sweep(self) -> None:
        raise AssertionError("adapter must not start a sweep")

    def command(self, _command: str) -> None:
        raise AssertionError("adapter must not issue an instrument command")

    def write_firmware(self) -> None:
        raise AssertionError("adapter must not write firmware")

    def reset(self) -> None:
        raise AssertionError("adapter must not reset an instrument")


def _probe(model: TinySaModel = TinySaModel.BASIC) -> TinySaReadOnlyProbe:
    return TinySaReadOnlyProbe(model, "UNIT-SECRET-01", "FW-SECRET-1.4.0")


def _factory(port: _FakePort) -> Callable[[], _FakePort]:
    return lambda: port


class TinySaCapabilityAdapterTests(unittest.TestCase):
    def test_basic_mapping_is_trace_only_and_never_issues_a_sweep_or_write(self) -> None:
        port = _FakePort(_probe())
        observation = TinySaCapabilityAdapter(_factory(port)).observe()
        snapshot = observation.snapshot

        self.assertIs(snapshot.family, DeviceFamily.TINYSA)
        self.assertEqual(snapshot.adapter_id, TINYSA_READ_ONLY_ADAPTER_ID)
        self.assertEqual(snapshot.transports, (CapabilityTransport.USB,))
        self.assertEqual(snapshot.acquisition_kinds, (AcquisitionKind.SPECTRUM_TRACE,))
        self.assertEqual(
            tuple((item.minimum, item.maximum) for item in snapshot.tuning_ranges_hz),
            ((100e3, 350e6), (240e6, 960e6)),
        )
        self.assertIs(snapshot.raw_iq_available, False)
        self.assertEqual(port.calls, ["probe", "close"])
        self.assertTrue(snapshot.identity_key.startswith("sha256:"))
        self.assertTrue(observation.external_correction_identity.firmware_fingerprint.startswith("sha256:"))
        for field in (
            CapabilityField.TRANSPORT,
            CapabilityField.ACQUISITION_KIND,
            CapabilityField.TUNING_RANGE,
            CapabilityField.RAW_IQ,
        ):
            self.assertIsNot(snapshot.evidence_for(field).origin, CapabilityEvidenceOrigin.UNKNOWN)

        rendered = repr(observation)
        for secret in ("UNIT-SECRET", "FW-SECRET"):
            self.assertNotIn(secret.casefold(), rendered.casefold())

    def test_ultra_mapping_retains_its_distinct_declared_upper_range(self) -> None:
        observation = TinySaCapabilityAdapter(_factory(_FakePort(_probe(TinySaModel.ULTRA)))).observe()
        snapshot = observation.snapshot

        self.assertEqual(snapshot.label, "tinySA Ultra spectrum analyzer")
        self.assertEqual(
            tuple((item.minimum, item.maximum) for item in snapshot.tuning_ranges_hz),
            ((100e3, 5.3e9),),
        )

    def test_device_reported_dbm_and_external_correction_are_explicit_not_sdr_dbfs_calibration(self) -> None:
        semantics = TinySaCapabilityAdapter(_factory(_FakePort(_probe()))).observe().analyzer_semantics

        self.assertEqual(semantics.reported_unit, "dBm")
        self.assertEqual(semantics.value_provenance, "device_reported_trace")
        self.assertEqual(semantics.device_calibration_provenance, "device_reported_builtin")
        self.assertTrue(semantics.optional_external_correction_supported)
        self.assertTrue(semantics.external_correction_requires_separate_layer)
        self.assertFalse(semantics.raw_iq_available)
        self.assertFalse(semantics.dbfs_conversion_available)
        self.assertFalse(semantics.metrological_accuracy_verified)

    def test_unknown_analyzer_properties_remain_unknown(self) -> None:
        snapshot = TinySaCapabilityAdapter(_factory(_FakePort(_probe()))).observe().snapshot

        self.assertEqual(snapshot.sample_rate_ranges_hz, ())
        self.assertEqual(snapshot.analog_bandwidth_ranges_hz, ())
        self.assertEqual(snapshot.gain_ranges_db, ())
        self.assertIsNone(snapshot.rx_channel_count)
        self.assertIsNone(snapshot.hardware_timestamp_available)
        self.assertIsNone(snapshot.hardware_overflow_counter_available)
        for field in (
            CapabilityField.SAMPLE_RATE_RANGE,
            CapabilityField.ANALOG_BANDWIDTH_RANGE,
            CapabilityField.GAIN_RANGE,
            CapabilityField.RX_CHANNEL_COUNT,
            CapabilityField.HARDWARE_TIMESTAMP,
            CapabilityField.HARDWARE_OVERFLOW_COUNTER,
        ):
            self.assertIs(snapshot.evidence_for(field).origin, CapabilityEvidenceOrigin.UNKNOWN)

    def test_probe_and_close_failures_are_redacted_and_close_is_attempted(self) -> None:
        cases = (
            _FakePort(_probe(), probe_error=RuntimeError("USB secret route")),
            _FakePort(_probe(), close_error=RuntimeError("close secret route")),
            _FakePort(object()),
        )
        for port in cases:
            with self.subTest(port=port):
                with self.assertRaisesRegex(TinySaCapabilityObservationError, "failed closed") as captured:
                    TinySaCapabilityAdapter(_factory(port)).observe()
                self.assertNotIn("secret", str(captured.exception).casefold())
                self.assertEqual(port.calls[-1], "close")

    def test_probe_contract_rejects_unknown_or_route_shaped_identity(self) -> None:
        invalid = (
            ("unknown", "1.4.0"),
            ("serial:secret", "1.4.0"),
            ("unit-1", "usb:secret"),
            (" unit-1", "1.4.0"),
        )
        for identity, firmware in invalid:
            with self.subTest(identity=identity, firmware=firmware), self.assertRaises(ValueError):
                TinySaReadOnlyProbe(TinySaModel.BASIC, identity, firmware)


if __name__ == "__main__":
    unittest.main()
