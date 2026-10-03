"""Stable refusal identities for V2 user plans, independent of Qt and SDK text.

Codes explain an admission failure, not hardware capabilities or RF evidence.
Translate them in presentation; never derive a code by parsing an exception.
Stage/Preview do not configure RF, stopped Apply arms only, Start is explicit.
"""

from enum import Enum


class PaneUserRefusal(str, Enum):
    INVALID_PLAN = "invalid_plan"
    INVALID_RECEIVER_SELECTION = "invalid_receiver_selection"
    SELECTION_CHANGED = "selection_changed"
    PAIRED_SELECTION_REQUIRED = "paired_selection_required"
    PAIRED_RECEIPTS_MISMATCH = "paired_receipts_mismatch"
    PAIRED_ASSIGNMENT_UNSUPPORTED = "paired_assignment_unsupported"
    PAIRED_TOPOLOGY_UNAVAILABLE = "paired_topology_unavailable"
    PAIRED_STABLE_IDENTITY_REQUIRED = "paired_stable_identity_required"
    PAIRED_MODE_UNSUPPORTED = "paired_mode_unsupported"
    PAIRED_PROFILE_CONFLICT = "paired_profile_conflict"
    PAIRED_WINDOW_CONFLICT = "paired_window_conflict"
    CAPTURE_SPAN_EXCEEDED = "capture_span_exceeded"
    OBSERVED_RANGE_EXCEEDED = "observed_range_exceeded"
    REVISIT_INFEASIBLE = "revisit_infeasible"
    NETWORK_INTENT_CONFLICT = "network_intent_conflict"
    STAGE_NOT_CONFIRMED = "stage_not_confirmed"
    CLEANUP_REQUIRED = "cleanup_required"
    PLAN_ALREADY_APPLIED_OR_CLOSED = "plan_already_applied_or_closed"
    RECORDING_CONFLICT = "recording_conflict"
    OWNER_NOT_STOPPED = "owner_not_stopped"
    CONFIGURATION_NOT_CONFIRMED = "configuration_not_confirmed"


__all__ = ["PaneUserRefusal"]
