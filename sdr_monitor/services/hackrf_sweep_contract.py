"""Pure paired optional HackRF Sweep ABI check; never opens the SDK."""

from collections.abc import Mapping


def hackrf_sweep_contract_version(native: object, manifest: Mapping[str, object]) -> int | None:
    """Return protocol1 only when bridge, factory, manifest and callable agree."""
    missing = object()
    bridge = getattr(native, "HACKRF_SWEEP_BRIDGE_CONTRACT_VERSION", missing)
    factory = getattr(native, "HACKRF_SWEEP_FACTORY_CONTRACT_VERSION", missing)
    declared_bridge = manifest.get("hackrf_sweep_bridge_contract_version", missing)
    declared_factory = manifest.get("hackrf_sweep_factory_contract_version", missing)
    if all(value is missing for value in (bridge, factory, declared_bridge, declared_factory)):
        return None
    if (type(bridge) is not int or bridge != 1
            or type(factory) is not int or factory != 1
            or type(declared_bridge) is not int or declared_bridge != bridge
            or type(declared_factory) is not int or declared_factory != factory
            or not callable(getattr(native, "create_hackrf_sweep_runtime_control", None))):
        raise ValueError("HackRF optional Sweep bridge/factory contract does not match its native manifest")
    return 1


__all__ = ["hackrf_sweep_contract_version"]
