"""Pure optional native metadata agreement, not a device/SDK observation."""

from collections.abc import Mapping


def hackrf_dsp_profile_contract_version(native: object, manifest: Mapping[str, object]) -> int | None:
    """Legacy factory2 has no extension; a new extension must agree exactly."""
    missing = object()
    observed = getattr(native, "HACKRF_DSP_PROFILE_CONTRACT_VERSION", missing)
    declared = manifest.get("hackrf_dsp_profile_contract_version", missing)
    if observed is missing and declared is missing:
        return None
    if type(observed) is not int or observed != 1 or type(declared) is not int or declared != observed:
        raise ValueError("HackRF optional DSP profile contract does not match its native manifest")
    return observed


def hackrf_persistence_contract_version(native: object, manifest: Mapping[str, object]) -> int | None:
    """Optional bounded native density path; never assume support from factory2."""
    missing = object()
    observed = getattr(native, "HACKRF_PERSISTENCE_CONTRACT_VERSION", missing)
    declared = manifest.get("hackrf_persistence_contract_version", missing)
    if observed is missing and declared is missing:
        return None
    if type(observed) is not int or observed != 1 or type(declared) is not int or declared != observed:
        raise ValueError("HackRF optional persistence contract does not match its native manifest")
    return observed


__all__ = ["hackrf_dsp_profile_contract_version", "hackrf_persistence_contract_version"]
