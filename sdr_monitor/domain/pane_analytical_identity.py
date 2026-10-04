"""Scalar analytical delivery identity, not RF time or a paint/offer ledger."""
from __future__ import annotations

from dataclasses import dataclass

from .analytical_journal import OwnerJournalScope
from .analytical_ready import DetectorReadyReceipt, _integer


@dataclass(frozen=True, slots=True)
class PaneAnalyticalIdentity:
    """Exact owner offer plus explicit host resource/capture/RX/pane binding.

    Distinct pane obligations may reference the SAME native offer. Do not sum
    those references as multiple acquired streams or multiple computed FFTs.
    Native receiver tag may be unknown even though endpoint identity is typed.
    """

    owner_scope: OwnerJournalScope
    ready: DetectorReadyReceipt
    physical_stream_resource_id: str
    capture_id: str
    receiver_endpoint_id: str
    pane_id: str
    host_run_serial: int
    host_activation_serial: int

    def __post_init__(self) -> None:
        if not isinstance(self.owner_scope, OwnerJournalScope) or not isinstance(self.ready, DetectorReadyReceipt):
            raise ValueError("analytical delivery requires immutable owner and ready receipts")
        for name in ("physical_stream_resource_id", "capture_id", "receiver_endpoint_id", "pane_id"):
            value = getattr(self, name)
            if (type(value) is not str or not value or value != value.strip()
                    or len(value) > 4096 or "\x00" in value):
                raise ValueError("analytical delivery requires exact host identities")
        for name in ("host_run_serial", "host_activation_serial"):
            _integer(getattr(self, name), name, 1, (1 << 64) - 1)
        scope, ready = self.owner_scope, self.ready
        if (ready.owner_run_id != scope.owner_run_id
                or ready.adapter_clock_scope_id != scope.clock_scope_id
                or ready.host_process_id != scope.host_process_id
                or ready.source_id != scope.source_id or ready.session_id != scope.session_id
                or ready.config_generation != scope.configuration_generation
                or ready.acquisition_epoch != scope.acquisition_epoch
                or ready.receiver_id is not None and ready.receiver_id != scope.receiver_id):
            raise ValueError("analytical delivery has a stale or foreign owner receipt")
