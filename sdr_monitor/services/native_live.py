"""Optional native/libiio live-device discovery for the standalone product."""

from __future__ import annotations

from typing import Any

from ..domain import (
    BackendKind,
    DeviceCapabilities,
    DeviceDescriptor,
    DeviceTransport,
    LiveSnapshot,
)
from .live_session import InMemoryLiveSessionService


class NativeLiveSessionService(InMemoryLiveSessionService):
    """Expose real Pluto contexts while preserving the bounded S05 service port.

    Discovery and connection probing are deliberately performed only when the
    presenter requests them. The current standalone live frame pipeline is
    still not a native RX stream, so ``start`` refuses to claim a running
    hardware session until that pipeline is wired end-to-end.
    """

    def __init__(self, native_module: Any, *, timeout_ms: int = 3000) -> None:
        super().__init__()
        self._native = native_module
        self._timeout_ms = timeout_ms
        self._native_device: Any | None = None
        self._native_uri: str | None = None

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]:
        try:
            contexts = tuple(self._native.scan_pluto_contexts("usb,ip"))
        except Exception as error:
            raise RuntimeError(f"Pluto/libiio discovery failed: {error}") from error

        devices = tuple(self._descriptor_for_context(context) for context in contexts)
        self._devices = devices
        return devices

    def select_device(self, device_id: str) -> LiveSnapshot:
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        if selected is None:
            return self._fail(f"Unknown device: {device_id}")
        try:
            self._disconnect_native()
            self._native_device = self._native.PlutoDevice(selected.uri, self._timeout_ms)
            self._native_device.probe()
            self._native_uri = selected.uri
        except Exception as error:
            self._disconnect_native()
            return self._fail(f"Pluto connection failed for {selected.uri}: {error}")
        return super().select_device(device_id)

    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        value = uri.strip()
        if not value:
            return self._fail("Enter a USB or IP URI")
        try:
            descriptor = self._descriptor_for_uri(value, None)
        except Exception as error:
            return self._fail(f"Pluto connection failed for {value}: {error}")
        self._devices = tuple(item for item in self._devices if item.device_id != descriptor.device_id) + (descriptor,)
        return self.select_device(descriptor.device_id)

    def start(self) -> LiveSnapshot:
        return self._fail("Pluto RX streaming is not connected to the Live Monitor pipeline yet")

    def stop(self) -> LiveSnapshot:
        snapshot = super().stop()
        self._disconnect_native()
        return snapshot

    def close_live(self) -> None:
        self.stop()

    def stop_and_wait(self, timeout_s: float) -> None:
        del timeout_s
        self.stop()

    def _descriptor_for_context(self, context: Any) -> DeviceDescriptor:
        uri = str(context.uri)
        return self._descriptor_for_uri(uri, str(getattr(context, "description", "")))

    def _descriptor_for_uri(self, uri: str, description: str | None) -> DeviceDescriptor:
        probe = None
        try:
            probe = self._native.probe_pluto_context(uri, self._timeout_ms)
        except Exception:
            # A context returned by libiio is still useful to show in the
            # dialog; selection performs the authoritative connection probe.
            pass

        native_capabilities = None
        temporary_device = None
        try:
            temporary_device = self._native.PlutoDevice(uri, self._timeout_ms)
            native_capabilities = temporary_device.capabilities()
        except Exception:
            native_capabilities = None
        finally:
            if temporary_device is not None:
                try:
                    temporary_device.disconnect()
                except Exception:
                    pass

        model = str(getattr(probe, "model", "") or getattr(native_capabilities, "model", "") or "PlutoSDR")
        description_text = (description or "").strip()
        model_prefix = model.split("(", 1)[0].strip().casefold()
        label = model if not description_text or model_prefix in description_text.casefold() else f"{model} — {description_text}"
        return DeviceDescriptor(
            device_id=f"pluto:{uri}",
            label=label,
            uri=uri,
            transport=_transport_for_uri(uri),
            capabilities=_domain_capabilities(self._native, native_capabilities),
        )

    def _disconnect_native(self) -> None:
        device = self._native_device
        self._native_device = None
        self._native_uri = None
        if device is not None:
            try:
                device.disconnect()
            except Exception:
                pass


def build_optional_native_live_service() -> NativeLiveSessionService | None:
    """Return the native service when the packaged extension is available."""

    try:
        from .. import _sdr_native

        info = _sdr_native.build_info()
        if not info.get("pluto_compiled", False):
            return None
    except (ImportError, ModuleNotFoundError, OSError, AttributeError):
        return None
    return NativeLiveSessionService(_sdr_native)


def _transport_for_uri(uri: str) -> DeviceTransport:
    value = uri.casefold()
    if value.startswith("usb:"):
        return DeviceTransport.USB
    if value.startswith("ip:"):
        return DeviceTransport.IP
    return DeviceTransport.MANUAL


def _domain_capabilities(native_module: Any, native_capabilities: Any | None) -> DeviceCapabilities:
    if native_capabilities is None:
        rates = (2e6, 10e6, 20e6)
        gain_range = (0.0, 73.0)
    else:
        rates = _representative_rates(native_capabilities.sample_rate_ranges_hz)
        gain = native_capabilities.gain_range_db
        gain_range = (float(gain.minimum), float(gain.maximum))

    build_info = native_module.build_info()
    backends = [BackendKind.AUTO, BackendKind.CPU]
    if build_info.get("cuda_compiled", False):
        backends.append(BackendKind.CUDA)
    return DeviceCapabilities(sample_rates_hz=rates, gain_range_db=gain_range, supported_backends=tuple(backends))


def _representative_rates(ranges: Any) -> tuple[float, ...]:
    values: set[float] = set()
    preferred = (2e6, 2.4e6, 5e6, 10e6, 20e6, 30.72e6, 40e6, 50e6)
    for rate_range in ranges:
        minimum = float(rate_range.minimum)
        maximum = float(rate_range.maximum)
        values.update((minimum, maximum))
        values.update(value for value in preferred if minimum <= value <= maximum)
    return tuple(sorted(values)) or (2e6, 10e6, 20e6)


__all__ = ["NativeLiveSessionService", "build_optional_native_live_service"]