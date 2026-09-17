"""Source-only topology dependency: no services, Qt, SDK or receiver access."""

from dataclasses import FrozenInstanceError
import unittest

from sdr_monitor.domain.receiver_topology import (
    AcquisitionGroup,
    IqComponent,
    ReceiverChain,
    ReceiverChainSelection,
    ReceiverEndpoint,
    ReceiverPathVerification,
    ReceiverTopologyInventory,
    ReceiverTopologySnapshot,
    StreamScanElement,
)


def topology(*, dual: bool = True) -> ReceiverTopologySnapshot:
    chains = (ReceiverChain.RX1, ReceiverChain.RX2) if dual else (ReceiverChain.RX1,)
    elements = tuple(
        StreamScanElement(
            f"voltage{2 * chain_index + component_index}", chain, component,
            2 * chain_index + component_index, 16, 12, 0, True, False,
        )
        for chain_index, chain in enumerate(chains)
        for component_index, component in enumerate(IqComponent)
    )
    return ReceiverTopologySnapshot("synthetic-resource", None, None, (), elements)


class TopologySourceClosureTests(unittest.TestCase):
    def test_domain_import_keeps_topology_available_without_device_probe(self):
        from sdr_monitor.domain.live import DeviceCapabilities

        self.assertIn("receiver_topology", DeviceCapabilities.__dataclass_fields__)
        self.assertEqual(topology().observed_chains, (ReceiverChain.RX1, ReceiverChain.RX2))

    def test_dual_digital_layout_does_not_claim_rf_path_proof(self):
        value = topology()
        self.assertTrue(value.dual_rx_scan_layout_observed)
        self.assertTrue(value.supports_selection(ReceiverChainSelection.BOTH))
        for chain in ReceiverChain:
            self.assertIs(value.rf_path_verification(chain), ReceiverPathVerification.NOT_VERIFIED)

    def test_single_receiver_rejects_both(self):
        value = topology(dual=False)
        self.assertFalse(value.supports_selection(ReceiverChainSelection.BOTH))
        self.assertTrue(value.selection_issues(ReceiverChainSelection.BOTH))
        inventory = ReceiverTopologyInventory("synthetic-device", "synthetic-source", value)
        with self.assertRaises(ValueError):
            inventory.endpoint("both", ReceiverChainSelection.BOTH)

    def test_frozen_topology_and_unknown_stride(self):
        value = topology()
        self.assertTrue(all(element.stride_bytes is None for element in value.scan_elements))
        with self.assertRaises(FrozenInstanceError):
            value.board_identity = "changed"

    def test_acquisition_group_disallows_overlapping_receiver_ownership(self):
        first = ReceiverEndpoint("first", "source", "resource", ReceiverChainSelection.RX1)
        second = ReceiverEndpoint("second", "source", "resource", ReceiverChainSelection.BOTH)
        with self.assertRaises(ValueError):
            AcquisitionGroup("group", "resource", (first, second))

    def test_acquisition_group_disallows_mixing_physical_resources(self):
        first = ReceiverEndpoint("first", "source-a", "resource-a", ReceiverChainSelection.RX1)
        second = ReceiverEndpoint("second", "source-b", "resource-b", ReceiverChainSelection.RX2)
        with self.assertRaises(ValueError):
            AcquisitionGroup("group", "resource-a", (first, second))


if __name__ == "__main__":
    unittest.main()
