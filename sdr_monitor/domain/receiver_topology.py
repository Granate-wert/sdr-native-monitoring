"""Qt-free receiver, topology and multi-pane scheduling contracts for R10-E0.

The contracts deliberately describe *observed* IIO topology separately from
verified RF paths.  Four scan elements can make a dual-RX software layout
available, but cannot by themselves prove that RX2 is routed, independent,
calibrated, or sustainable at any transport rate.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math


_MAX_SCAN_ELEMENTS = 32
_MAX_GROUP_ENDPOINTS = 2
_MAX_SCHEDULING_WEIGHT = 100


def _required_text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _optional_text(value: object, label: str) -> str | None:
    return None if value is None else _required_text(value, label)


class ReceiverChain(StrEnum):
    """One physical receive chain; not a claim about an RF connector."""

    RX1 = "rx1"
    RX2 = "rx2"


class ReceiverChainSelection(StrEnum):
    """Explicit stream selection used by later native acquisition packages."""

    RX1 = "rx1"
    RX2 = "rx2"
    BOTH = "both"

    @property
    def chains(self) -> tuple[ReceiverChain, ...]:
        if self is ReceiverChainSelection.RX1:
            return (ReceiverChain.RX1,)
        if self is ReceiverChainSelection.RX2:
            return (ReceiverChain.RX2,)
        return (ReceiverChain.RX1, ReceiverChain.RX2)


class IqComponent(StrEnum):
    IN_PHASE = "i"
    QUADRATURE = "q"


class ReceiverPathVerification(StrEnum):
    """Evidence state of a physical RF path, intentionally not inferred."""

    NOT_VERIFIED = "not_verified"
    VERIFIED = "verified"


class ReceiverBindingMode(StrEnum):
    """Visible execution mode selected by a later supervisor."""

    DEDICATED_PARALLEL = "dedicated_parallel"
    SHARED_CAPTURE = "shared_capture"
    TIME_SLICED = "time_sliced"


class PaneDisplayPolicy(StrEnum):
    """A display preference, not permission to slow acquisition or DSP."""

    VISIBLE = "visible"
    BACKGROUND = "background"


class SchedulerPolicyKind(StrEnum):
    EQUAL = "equal"
    WEIGHTED = "weighted"
    MINIMUM_REVISIT = "minimum_revisit"


@dataclass(frozen=True, slots=True)
class StreamScanElement:
    """Read-only description of one enabled IIO input scan element.

    R10-E0 stores the layout necessary for later one-buffer admission.  It
    never enables a channel, opens a buffer, or assigns an RF connector.
    """

    element_id: str
    chain: ReceiverChain
    component: IqComponent
    device_channel_index: int
    storage_bits: int
    significant_bits: int
    shift: int
    is_signed: bool
    is_big_endian: bool
    # Buffer stride is not observable before E1 enables the selected scan
    # elements in one IIO buffer.  ``None`` preserves that distinction.
    stride_bytes: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "element_id", _required_text(self.element_id, "scan element id"))
        object.__setattr__(self, "chain", ReceiverChain(self.chain))
        object.__setattr__(self, "component", IqComponent(self.component))
        if self.device_channel_index < 0:
            raise ValueError("device channel index must be non-negative")
        if self.storage_bits <= 0 or self.significant_bits <= 0 or self.significant_bits > self.storage_bits:
            raise ValueError("scan element bit widths are invalid")
        if self.shift < 0 or self.shift >= self.storage_bits:
            raise ValueError("scan element shift is invalid")
        if self.stride_bytes is not None and self.stride_bytes <= 0:
            raise ValueError("scan element stride must be positive")

    @property
    def layout_signature(self) -> tuple[int, int, int, bool, bool]:
        return (
            self.storage_bits,
            self.significant_bits,
            self.shift,
            self.is_signed,
            self.is_big_endian,
        )


@dataclass(frozen=True, slots=True)
class ReceiverTopologySnapshot:
    """Observed topology for exactly one physical IIO stream resource.

    ``silicon_identity`` and ``board_identity`` are recorded independently:
    custom firmware may report a model string that is not the fitted RF part.
    ``verified_rf_paths`` is explicit evidence from a future physical test;
    absence of a chain from it means ``NOT_VERIFIED`` even if I/Q scan pairs
    are present.
    """

    physical_stream_resource_id: str
    silicon_identity: str | None
    board_identity: str | None
    phy_rx_channel_ids: tuple[str, ...]
    scan_elements: tuple[StreamScanElement, ...]
    verified_rf_paths: tuple[ReceiverChain, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "physical_stream_resource_id",
            _required_text(self.physical_stream_resource_id, "physical stream resource id"),
        )
        object.__setattr__(self, "silicon_identity", _optional_text(self.silicon_identity, "silicon identity"))
        object.__setattr__(self, "board_identity", _optional_text(self.board_identity, "board identity"))
        phy_channels = tuple(_required_text(value, "PHY RX channel id") for value in self.phy_rx_channel_ids)
        if len(set(phy_channels)) != len(phy_channels):
            raise ValueError("PHY RX channel ids must be unique")
        object.__setattr__(self, "phy_rx_channel_ids", phy_channels)
        elements = tuple(self.scan_elements)
        if not elements or len(elements) > _MAX_SCAN_ELEMENTS:
            raise ValueError(f"scan layout must contain 1..{_MAX_SCAN_ELEMENTS} elements")
        if len({element.element_id for element in elements}) != len(elements):
            raise ValueError("scan element ids must be unique")
        if len({element.device_channel_index for element in elements}) != len(elements):
            raise ValueError("device channel indices must be unique")
        if len({(element.chain, element.component) for element in elements}) != len(elements):
            raise ValueError("a receiver chain may expose each I/Q component once")
        # Device enumeration order is metadata only. Buffer order/stride must
        # remain unknown until E1 creates its single admitted IIO buffer.
        elements = tuple(sorted(elements, key=lambda element: element.device_channel_index))
        object.__setattr__(self, "scan_elements", elements)
        verified = tuple(ReceiverChain(value) for value in self.verified_rf_paths)
        if len(set(verified)) != len(verified):
            raise ValueError("verified RF paths must be unique")
        unsupported = set(verified) - set(self.observed_chains)
        if unsupported:
            raise ValueError("a verified RF path must have an observed complete I/Q layout")
        object.__setattr__(self, "verified_rf_paths", verified)

    @property
    def observed_chains(self) -> tuple[ReceiverChain, ...]:
        return tuple(
            chain
            for chain in ReceiverChain
            if {element.component for element in self._elements_for(chain)} == {IqComponent.IN_PHASE, IqComponent.QUADRATURE}
        )

    @property
    def available_selections(self) -> tuple[ReceiverChainSelection, ...]:
        selections: list[ReceiverChainSelection] = []
        for selection in ReceiverChainSelection:
            if self._selection_issues(selection) == ():
                selections.append(selection)
        return tuple(selections)

    @property
    def dual_rx_scan_layout_observed(self) -> bool:
        """True only for a compatible digital I/Q layout; never RF admission."""

        return ReceiverChainSelection.BOTH in self.available_selections

    def supports_selection(self, selection: ReceiverChainSelection) -> bool:
        return ReceiverChainSelection(selection) in self.available_selections

    def selection_issues(self, selection: ReceiverChainSelection) -> tuple[str, ...]:
        """Explain why an E1 one-buffer selection cannot be considered yet."""

        return self._selection_issues(ReceiverChainSelection(selection))

    def rf_path_verification(self, chain: ReceiverChain) -> ReceiverPathVerification:
        return ReceiverPathVerification.VERIFIED if ReceiverChain(chain) in self.verified_rf_paths else ReceiverPathVerification.NOT_VERIFIED

    def _selection_issues(self, selection: ReceiverChainSelection) -> tuple[str, ...]:
        selected_elements: list[StreamScanElement] = []
        issues: list[str] = []
        for chain in selection.chains:
            elements = self._elements_for(chain)
            missing = {IqComponent.IN_PHASE, IqComponent.QUADRATURE} - {element.component for element in elements}
            if missing:
                issues.append(f"{chain.value} is missing {'/'.join(component.value.upper() for component in sorted(missing))}")
            else:
                selected_elements.extend(elements)
        if len({element.layout_signature for element in selected_elements}) > 1:
            issues.append("selected I/Q scan elements have incompatible storage layout")
        return tuple(issues)

    def _elements_for(self, chain: ReceiverChain) -> tuple[StreamScanElement, ...]:
        return tuple(element for element in self.scan_elements if element.chain is chain)


@dataclass(frozen=True, slots=True)
class ReceiverEndpoint:
    """A logical selection on one physical stream; no stream is opened here."""

    endpoint_id: str
    source_id: str
    physical_stream_resource_id: str
    selection: ReceiverChainSelection

    def __post_init__(self) -> None:
        object.__setattr__(self, "endpoint_id", _required_text(self.endpoint_id, "receiver endpoint id"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "receiver source id"))
        object.__setattr__(self, "physical_stream_resource_id", _required_text(self.physical_stream_resource_id, "physical stream resource id"))
        object.__setattr__(self, "selection", ReceiverChainSelection(self.selection))


@dataclass(frozen=True, slots=True)
class AcquisitionGroup:
    """Compatible endpoints sharing exactly one physical stream resource."""

    group_id: str
    physical_stream_resource_id: str
    endpoints: tuple[ReceiverEndpoint, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_id", _required_text(self.group_id, "acquisition group id"))
        resource_id = _required_text(self.physical_stream_resource_id, "physical stream resource id")
        object.__setattr__(self, "physical_stream_resource_id", resource_id)
        endpoints = tuple(self.endpoints)
        if not endpoints or len(endpoints) > _MAX_GROUP_ENDPOINTS:
            raise ValueError(f"acquisition group must contain 1..{_MAX_GROUP_ENDPOINTS} endpoints")
        if len({endpoint.endpoint_id for endpoint in endpoints}) != len(endpoints):
            raise ValueError("acquisition group endpoint ids must be unique")
        if any(endpoint.physical_stream_resource_id != resource_id for endpoint in endpoints):
            raise ValueError("acquisition group endpoints must share one physical stream resource")
        occupied: set[ReceiverChain] = set()
        for endpoint in endpoints:
            overlap = occupied.intersection(endpoint.selection.chains)
            if overlap:
                raise ValueError("acquisition group receiver-chain selections overlap")
            occupied.update(endpoint.selection.chains)
        object.__setattr__(self, "endpoints", endpoints)


@dataclass(frozen=True, slots=True)
class PaneSchedulerPolicy:
    """Bounded R10-E4 scheduler intent; it never performs device I/O itself."""

    kind: SchedulerPolicyKind = SchedulerPolicyKind.EQUAL
    weight: int = 1
    minimum_revisit_s: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", SchedulerPolicyKind(self.kind))
        if isinstance(self.weight, bool) or not isinstance(self.weight, int) or not 1 <= self.weight <= _MAX_SCHEDULING_WEIGHT:
            raise ValueError(f"scheduler weight must be an integer in [1, {_MAX_SCHEDULING_WEIGHT}]")
        if self.minimum_revisit_s is not None and (not math.isfinite(self.minimum_revisit_s) or self.minimum_revisit_s <= 0.0):
            raise ValueError("minimum revisit seconds must be finite and positive")
        if self.kind is SchedulerPolicyKind.EQUAL and self.weight != 1:
            raise ValueError("equal scheduler policy requires weight=1")
        if self.kind is SchedulerPolicyKind.MINIMUM_REVISIT and self.minimum_revisit_s is None:
            raise ValueError("minimum-revisit scheduler policy requires minimum_revisit_s")
        if self.kind is not SchedulerPolicyKind.MINIMUM_REVISIT and self.minimum_revisit_s is not None:
            raise ValueError("minimum_revisit_s is only valid for minimum-revisit policy")


@dataclass(frozen=True, slots=True)
class SweepPaneRequest:
    """Immutable pane intent consumed by the Qt-free R10-E4 plan compiler."""

    pane_id: str
    receiver_endpoint_id: str
    start_hz: float
    stop_hz: float
    profile_id: str | None = None
    display_policy: PaneDisplayPolicy = PaneDisplayPolicy.VISIBLE
    requested_binding_mode: ReceiverBindingMode = ReceiverBindingMode.TIME_SLICED
    scheduler_policy: PaneSchedulerPolicy = PaneSchedulerPolicy()

    def __post_init__(self) -> None:
        object.__setattr__(self, "pane_id", _required_text(self.pane_id, "pane id"))
        object.__setattr__(self, "receiver_endpoint_id", _required_text(self.receiver_endpoint_id, "receiver endpoint id"))
        object.__setattr__(self, "profile_id", _optional_text(self.profile_id, "profile id"))
        object.__setattr__(self, "display_policy", PaneDisplayPolicy(self.display_policy))
        object.__setattr__(self, "requested_binding_mode", ReceiverBindingMode(self.requested_binding_mode))
        if not math.isfinite(self.start_hz) or not math.isfinite(self.stop_hz) or self.start_hz < 0.0 or self.stop_hz <= self.start_hz:
            raise ValueError("pane RF span must be finite, non-negative and increasing")


@dataclass(frozen=True, slots=True)
class ReceiverTopologyInventory:
    """Topology published for one logical device without probing or RX I/O."""

    device_id: str
    source_id: str
    topology: ReceiverTopologySnapshot | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "device_id", _required_text(self.device_id, "device id"))
        object.__setattr__(self, "source_id", _required_text(self.source_id, "source id"))

    @property
    def available_selections(self) -> tuple[ReceiverChainSelection, ...]:
        return () if self.topology is None else self.topology.available_selections

    def endpoint(self, endpoint_id: str, selection: ReceiverChainSelection) -> ReceiverEndpoint:
        """Create a logical endpoint only when the observed layout supports it."""

        normalized = ReceiverChainSelection(selection)
        if self.topology is None:
            raise ValueError("receiver topology has not been observed for this device")
        issues = self.topology.selection_issues(normalized)
        if issues:
            raise ValueError("receiver selection is not admitted by the observed scan layout: " + "; ".join(issues))
        return ReceiverEndpoint(endpoint_id, self.source_id, self.topology.physical_stream_resource_id, normalized)


__all__ = [
    "AcquisitionGroup",
    "IqComponent",
    "PaneDisplayPolicy",
    "PaneSchedulerPolicy",
    "ReceiverBindingMode",
    "ReceiverChain",
    "ReceiverChainSelection",
    "ReceiverEndpoint",
    "ReceiverPathVerification",
    "ReceiverTopologyInventory",
    "ReceiverTopologySnapshot",
    "SchedulerPolicyKind",
    "StreamScanElement",
    "SweepPaneRequest",
]
