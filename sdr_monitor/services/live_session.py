"""Bounded in-memory Live service used by S05 UI tests and safe startup."""

from __future__ import annotations

from dataclasses import replace

from ..domain import (
    AppliedLiveConfiguration,
    BackendKind,
    CalibrationQuality,
    DeviceCapabilities,
    DeviceDescriptor,
    DeviceTransport,
    LiveConfiguration,
    LiveQuality,
    LiveSessionState,
    LiveSnapshot,
)


class InMemoryLiveSessionService:
    """Models device/session truth without accessing hardware from the UI.

    A real adapter is injected in S05 after its worker boundary exists.  This
    implementation intentionally exposes only the latest immutable snapshot.
    """

    def __init__(self, devices: tuple[DeviceDescriptor, ...] = ()) -> None:
        self._devices = devices
        self._selected: DeviceDescriptor | None = None
        self._generation = 0
        self._sequence = 0
        self._snapshot = LiveSnapshot(generation=0, sequence=0, state=LiveSessionState.DISCONNECTED)

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]:
        return self._devices

    def select_device(self, device_id: str) -> LiveSnapshot:
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        if selected is None:
            return self._fail(f"Unknown device: {device_id}")
        self._selected = selected
        self._generation += 1
        self._sequence = 0
        self._snapshot = LiveSnapshot(generation=self._generation, sequence=0, state=LiveSessionState.CONNECTED, device=selected)
        return self._snapshot

    def select_manual_uri(self, uri: str) -> LiveSnapshot:
        value = uri.strip()
        if not value:
            return self._fail("Enter a USB or IP URI")
        transport = DeviceTransport.USB if value.casefold().startswith("usb:") else DeviceTransport.IP
        device_id = f"manual:{value}"
        descriptor = DeviceDescriptor(
            device_id=device_id,
            label="Manual SDR URI",
            uri=value,
            transport=transport,
            capabilities=DeviceCapabilities(
                sample_rates_hz=(2e6, 10e6, 20e6),
                gain_range_db=(0.0, 73.0),
                supported_backends=(BackendKind.AUTO, BackendKind.CPU),
            ),
        )
        self._devices = tuple(item for item in self._devices if item.device_id != device_id) + (descriptor,)
        return self.select_device(device_id)
    def apply_configuration(self, requested: LiveConfiguration) -> LiveSnapshot:
        if self._selected is None:
            return self._fail("Select a device before applying a live configuration")
        capabilities = self._selected.capabilities
        nearest_rate = min(capabilities.sample_rates_hz, key=lambda rate: abs(rate - requested.sample_rate_hz))
        gain = min(max(requested.gain_db, capabilities.gain_range_db[0]), capabilities.gain_range_db[1])
        backend = requested.backend if requested.backend in capabilities.supported_backends else BackendKind.CPU
        applied = replace(requested, sample_rate_hz=nearest_rate, gain_db=gain, backend=backend)
        adjustments = tuple(
            message
            for message, changed in (
                ("sample rate adjusted by device", nearest_rate != requested.sample_rate_hz),
                ("gain limited by device", gain != requested.gain_db),
                ("requested backend unavailable; CPU selected", backend != requested.backend),
            )
            if changed
        )
        self._generation += 1
        self._sequence = 0
        quality = LiveQuality(
            calibration=CalibrationQuality.UNCALIBRATED,
            backend=backend,
            fallback_reason="backend unavailable" if backend != requested.backend else None,
        )
        self._snapshot = LiveSnapshot(
            generation=self._generation,
            sequence=0,
            state=LiveSessionState.CONNECTED,
            device=self._selected,
            applied=AppliedLiveConfiguration(requested=requested, applied=applied, adjustments=adjustments),
            quality=quality,
        )
        return self._snapshot

    def start(self) -> LiveSnapshot:
        if self._snapshot.applied is None:
            return self._fail("Apply a live configuration before starting")
        self._snapshot = replace(self._snapshot, state=LiveSessionState.RUNNING, error=None)
        return self._snapshot

    def stop(self) -> LiveSnapshot:
        target = LiveSessionState.CONNECTED if self._selected else LiveSessionState.DISCONNECTED
        self._snapshot = replace(self._snapshot, state=target)
        return self._snapshot

    def open_live(self, config: LiveConfiguration) -> None:
        self.apply_configuration(config)

    def close_live(self) -> None:
        self.stop()

    def poll_frames(self) -> list[LiveSnapshot]:
        return [self._snapshot] if self._snapshot.state is LiveSessionState.RUNNING else []

    def poll_live_metrics(self, timeout_s: float) -> LiveSnapshot:
        return self._snapshot

    def is_running(self) -> bool:
        return self._snapshot.state is LiveSessionState.RUNNING

    def stop_and_wait(self, timeout_s: float) -> None:
        self.stop()
    def latest_snapshot(self) -> LiveSnapshot:
        return self._snapshot

    def publish_fake_snapshot(self, generation: int) -> LiveSnapshot:
        """Test-only latest-wins publication; stale generations are ignored."""
        if generation != self._generation or self._snapshot.state is not LiveSessionState.RUNNING:
            return self._snapshot
        self._sequence += 1
        self._snapshot = replace(self._snapshot, sequence=self._sequence)
        return self._snapshot

    def _fail(self, message: str) -> LiveSnapshot:
        self._snapshot = replace(self._snapshot, state=LiveSessionState.ERROR, error=message)
        return self._snapshot


def fake_pluto_device() -> DeviceDescriptor:
    return DeviceDescriptor(
        device_id="fake-pluto-usb",
        label="PlutoSDR USB (test)",
        uri="usb:fake",
        transport=DeviceTransport.USB,
        capabilities=DeviceCapabilities(
            sample_rates_hz=(2e6, 10e6, 19.999e6),
            gain_range_db=(0.0, 73.0),
            supported_backends=(BackendKind.AUTO, BackendKind.CPU),
        ),
    )
