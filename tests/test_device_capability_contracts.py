"""Pure domain tests for capability evidence and opaque identity contracts."""

from __future__ import annotations

from dataclasses import replace
import unittest

from sdr_monitor.domain import (
    AcquisitionKind,
    CapabilityEvidence,
    CapabilityEvidenceOrigin,
    CapabilityField,
    CapabilityRange,
    CapabilityTransport,
    DeviceCapabilitySnapshot,
    DeviceFamily,
    as_frame_sequence,
    as_source_id,
    build_device_capability_inventory,
    stable_identity_key,
)


def _snapshot(identifier: str = "fixture-hackrf") -> DeviceCapabilitySnapshot:
    return DeviceCapabilitySnapshot(
        device_id=identifier,
        identity_key=stable_identity_key(identifier),
        label="HackRF fixture",
        family=DeviceFamily.HACKRF,
        adapter_id="native.fixture.v1",
        transports=(CapabilityTransport.USB,),
        acquisition_kinds=(AcquisitionKind.COMPLEX_IQ,),
        tuning_ranges_hz=(CapabilityRange(1e6, 6e9, "Hz"),),
        raw_iq_available=True,
        evidence=(
            CapabilityEvidence(
                CapabilityField.TRANSPORT,
                CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
                "fixture-transport",
            ),
            CapabilityEvidence(
                CapabilityField.ACQUISITION_KIND,
                CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                "fixture-acquisition",
            ),
            CapabilityEvidence(
                CapabilityField.TUNING_RANGE,
                CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                "fixture-tuning",
            ),
            CapabilityEvidence(
                CapabilityField.RAW_IQ,
                CapabilityEvidenceOrigin.VENDOR_DECLARATION,
                "fixture-raw-iq",
            ),
        ),
    )


class DeviceCapabilityContractTests(unittest.TestCase):
    def test_snapshot_requires_provenance_for_every_declared_capability(self) -> None:
        snapshot = _snapshot()
        self.assertEqual(
            snapshot.evidence_for(CapabilityField.TRANSPORT).origin,
            CapabilityEvidenceOrigin.RUNTIME_TOPOLOGY,
        )
        without_transport = tuple(
            item for item in snapshot.evidence if item.field is not CapabilityField.TRANSPORT
        )
        with self.assertRaisesRegex(ValueError, "transport"):
            replace(snapshot, evidence=without_transport)

    def test_identity_is_hashed_and_routes_cannot_cross_the_contract(self) -> None:
        snapshot = _snapshot()
        self.assertTrue(snapshot.identity_key.startswith("sha256:"))
        with self.assertRaisesRegex(ValueError, "opaque"):
            replace(snapshot, device_id="usb:1.2.3")
        with self.assertRaisesRegex(ValueError, "URI"):
            CapabilityEvidence(
                CapabilityField.RAW_IQ,
                CapabilityEvidenceOrigin.RUNTIME_READBACK,
                "ip:192.0.2.1",
            )

    def test_inventory_is_finite_and_prevents_duplicate_physical_identity(self) -> None:
        first = _snapshot("first")
        with self.assertRaisesRegex(ValueError, "unique identity"):
            build_device_capability_inventory((first, replace(first, device_id="second")))
        with self.assertRaisesRegex(ValueError, "maximum_devices"):
            build_device_capability_inventory((first,), maximum_devices=0)

    def test_identity_and_sequence_helpers_fail_closed(self) -> None:
        self.assertEqual(as_source_id("native.hackrf.live"), "native.hackrf.live")
        self.assertEqual(as_frame_sequence(0), 0)
        with self.assertRaises(ValueError):
            as_source_id(" ")
        with self.assertRaises(ValueError):
            as_frame_sequence(-1)


if __name__ == "__main__":
    unittest.main()
