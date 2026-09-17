"""Bounded in-memory Live service used by S05 UI tests and safe startup."""

from __future__ import annotations

from dataclasses import replace
from math import floor, isfinite

from ..domain import (
    AppliedLiveConfiguration,
    BackendKind,
    CalibrationQuality,
    DeviceCapabilities,
    DeviceDescriptor,
    DeviceTransport,
    LiveConfiguration,
    LiveErrorKind,
    LiveQuality,
    LiveSessionState,
    LiveSnapshot,
    as_configuration_generation,
    as_frame_sequence,
    as_session_id,
)


def _nearest_sample_rate(capabilities: DeviceCapabilities, requested: float) -> float:
    """Stage within published ranges, never mistake UI presets for limits."""
    if not isfinite(requested) or requested <= 0:
        raise ValueError("sample rate must be finite and positive")
    if not capabilities.sample_rate_ranges_hz:
        return min(capabilities.sample_rates_hz, key=lambda rate: abs(rate - requested))
    candidates = []
    for minimum, maximum, step in capabilities.sample_rate_ranges_hz:
        if (not all(isfinite(value) for value in (minimum, maximum, step))
                or minimum <= 0 or maximum < minimum or step < 0):
            raise ValueError("invalid published sample-rate range")
        candidate = min(max(requested, minimum), maximum)
        if step > 0:
            index = min(max(round((candidate - minimum) / step), 0),
                        floor((maximum - minimum) / step))
            candidate = minimum + index * step
        candidates.append(candidate)
    return min(candidates, key=lambda rate: abs(rate - requested))


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
        self._session_id = as_session_id("live:0")
        self._snapshot = LiveSnapshot(
            generation=as_configuration_generation(0),
            sequence=as_frame_sequence(0),
            state=LiveSessionState.DISCONNECTED,
            session_id=self._session_id,
        )

    def discover_devices(self) -> tuple[DeviceDescriptor, ...]:
        return self._devices

    def discover_startup_devices(self) -> tuple[DeviceDescriptor, ...]:
        """Fast startup path; in-memory discovery needs no transport filter."""
        return self.discover_devices()

    def select_device(self, device_id: str) -> LiveSnapshot:
        selected = next((device for device in self._devices if device.device_id == device_id), None)
        if selected is None:
            return self._fail(f"Unknown device: {device_id}")
        self._selected = selected
        self._generation += 1
        self._sequence = 0
        self._session_id = as_session_id(f"live:{self._generation}")
        self._snapshot = LiveSnapshot(
            generation=as_configuration_generation(self._generation),
            sequence=as_frame_sequence(0),
            state=LiveSessionState.CONNECTED,
            device=selected,
            error=None,
            error_kind=None,
            session_id=self._session_id,
        )
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
        nearest_rate = _nearest_sample_rate(capabilities, requested.sample_rate_hz)
        gain = min(max(requested.gain_db, capabilities.gain_range_db[0]), capabilities.gain_range_db[1])
        backend = requested.backend if requested.backend in capabilities.supported_backends else BackendKind.CPU
        if requested.analog_bandwidth_hz is not None and capabilities.analog_bandwidths_hz:
            requested_bandwidth = requested.analog_bandwidth_hz
            nearest_bandwidth: float | None = min(
                capabilities.analog_bandwidths_hz,
                key=lambda value: abs(value - requested_bandwidth),
            )
        else:
            nearest_bandwidth = requested.analog_bandwidth_hz
        applied = replace(
            requested,
            sample_rate_hz=nearest_rate,
            gain_db=gain,
            backend=backend,
            analog_bandwidth_hz=nearest_bandwidth,
        )
        adjustments = tuple(
            message
            for message, changed in (
                ("sample rate adjusted by device", nearest_rate != requested.sample_rate_hz),
                ("gain limited by device", gain != requested.gain_db),
                ("requested backend unavailable; CPU selected", backend != requested.backend),
                ("bandwidth adjusted by device", nearest_bandwidth != requested.analog_bandwidth_hz),
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
            generation=as_configuration_generation(self._generation),
            sequence=as_frame_sequence(0),
            state=LiveSessionState.CONNECTED,
            device=self._selected,
            applied=AppliedLiveConfiguration(requested=requested, applied=applied, adjustments=adjustments),
            quality=quality,
            session_id=self._session_id,
        )
        return self._snapshot

    def start(self) -> LiveSnapshot:
        if self._snapshot.applied is None:
            return self._fail(
                "Apply a live configuration before starting",
                kind=LiveErrorKind.CONFIGURATION_REJECTED,
            )
        self._snapshot = replace(
            self._snapshot,
            state=LiveSessionState.RUNNING,
            error=None,
            error_kind=None,
        )
        return self._snapshot

    def stop(self) -> LiveSnapshot:
        target = LiveSessionState.CONNECTED if self._selected else LiveSessionState.DISCONNECTED
        self._snapshot = replace(
            self._snapshot,
            state=target,
            error=None,
            error_kind=None,
        )
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
        self._snapshot = replace(self._snapshot, sequence=as_frame_sequence(self._sequence))
        return self._snapshot

    def _fail(
        self,
        message: str,
        *,
        kind: LiveErrorKind = LiveErrorKind.INTERNAL,
    ) -> LiveSnapshot:
        self._snapshot = replace(
            self._snapshot,
            state=LiveSessionState.ERROR,
            error=message,
            error_kind=kind,
        )
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
