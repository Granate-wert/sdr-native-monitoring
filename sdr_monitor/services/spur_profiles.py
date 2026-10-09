"""Read-only bounded artifact resolution for DCSP-03 diagnostic planning.

Not native support/owner admission, and never enables an unsupported spur mode.
Artifact hashes bind existing request references to actual decoded content.
"""

from pathlib import Path

from ..domain.processing_policy import HostSpurMode, SdrProcessingPolicyV1
from ..domain.spur_profiles import MAX_SPUR_PROFILE_BYTES, SpurProfileV1


def _require_request(policy: SdrProcessingPolicyV1) -> None:
    if type(policy) is not SdrProcessingPolicyV1 or policy.spur_mode is HostSpurMode.OFF:
        raise ValueError("explicit typed non-OFF diagnostic profile request required")


def resolve_spur_profile(policy: SdrProcessingPolicyV1, payload: bytes) -> SpurProfileV1:
    _require_request(policy)
    profile = SpurProfileV1.from_json(payload)
    if profile.reference != policy.spur_profile:
        raise ValueError("profile identity/content/applicability differs from request")
    return profile


def read_spur_profile(path: Path, policy: SdrProcessingPolicyV1) -> SpurProfileV1:
    """Bound the read BEFORE JSON allocation; no writes or directory scanning."""
    _require_request(policy)
    if not isinstance(path, Path):
        raise TypeError("explicit profile Path required")
    with path.open("rb") as source:
        payload = source.read(MAX_SPUR_PROFILE_BYTES + 1)
    return resolve_spur_profile(policy, payload)
