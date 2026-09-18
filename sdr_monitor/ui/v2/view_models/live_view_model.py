"""Public-signal adapter from the existing LivePresenter to UI V2 state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import replace
from typing import Protocol
from sdr_monitor.domain import LiveConfiguration
from sdr_monitor.domain.analyzer_resources import AnalyzerGeometryPreflight
from sdr_monitor.domain.continuous_sweep_request import ContinuousSweepPlanRequest

from ..state.live_view_state import LiveAction, LiveViewState, build_live_view_state
from ..state.analyzer_layer_cache import AnalyzerLayerCache


class _SignalPort(Protocol):
    def connect(self, callback: Callable[..., None]) -> object: ...

    def disconnect(self, callback: Callable[..., None]) -> object: ...


class LivePresenterPort(Protocol):
    """The only Live dependency UI V2 is allowed to consume in this package."""

    devices_discovered: _SignalPort
    snapshot_changed: _SignalPort
    task_failed: _SignalPort
    busy_changed: _SignalPort
    render_ready: _SignalPort

    def discover_devices(self) -> None: ...

    def select_device(self, device_id: str) -> None: ...

    def select_manual_uri(self, uri: str) -> None: ...

    def apply_configuration(self, configuration: object) -> None: ...

    def start(self) -> None: ...

    def stop(self) -> None: ...


class LiveViewModel:
    """Owns presentation subscriptions, never a device, service, or worker."""

    def __init__(
        self,
        presenter: LivePresenterPort,
        *,
        now_ns: Callable[[], int],
    ) -> None:
        self._presenter = presenter
        self._now_ns = now_ns
        self._busy = False
        self._command_error: str | None = None
        self._discovery_pending = False
        self._discovery_count: int | None = None
        self._last_snapshot: object | None = None
        self._prepared_measurement: LiveViewState | None = None
        self._expects_prepared = getattr(presenter, "prepares_snapshots", False) is True
        self._layer_cache = AnalyzerLayerCache()
        self._listeners: list[Callable[[LiveViewState], None]] = []
        self._devices: tuple[object, ...] = ()
        self._device_listeners: list[Callable[[tuple[object, ...]], None]] = []
        self._state = build_live_view_state(None)
        presenter.devices_discovered.connect(self._on_devices_discovered)
        self._snapshot_connections = (
            ((getattr(presenter, "prepared_snapshot_ready"), self._on_prepared_snapshot),)
            if self._expects_prepared else
            ((presenter.snapshot_changed, self._on_snapshot), (presenter.render_ready, self._on_snapshot))
        )
        for signal, callback in self._snapshot_connections:
            signal.connect(callback)
        presenter.busy_changed.connect(self._on_busy_changed)
        presenter.task_failed.connect(self._on_task_failed)

    @property
    def state(self) -> LiveViewState:
        return self._state

    @property
    def devices(self) -> tuple[object, ...]:
        """Return the last public discovery result; no route is inferred or stored."""

        return self._devices

    def subscribe(self, listener: Callable[[LiveViewState], None]) -> Callable[[], None]:
        """Subscribe to immutable presentation updates and receive current state."""

        self._listeners.append(listener)
        listener(self._state)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def subscribe_devices(
        self,
        listener: Callable[[tuple[object, ...]], None],
    ) -> Callable[[], None]:
        """Subscribe to public discovery results without initiating discovery."""

        self._device_listeners.append(listener)
        listener(self._devices)

        def unsubscribe() -> None:
            if listener in self._device_listeners:
                self._device_listeners.remove(listener)

        return unsubscribe

    def execute_primary_action(self) -> bool:
        """Delegate only already-public and unambiguous presenter commands."""

        action = self._state.primary_action
        if not self._state.primary_action_enabled:
            return False
        if action is LiveAction.DISCOVER:
            return self.discover_devices()
        if action is LiveAction.START:
            self._begin_explicit_command()
            self._presenter.start()
            return True
        if action is LiveAction.STOP:
            self._begin_explicit_command()
            self._presenter.stop()
            return True
        # Retry and configuration review require package-specific controls;
        # UI2-01 intentionally leaves them disabled rather than guessing a
        # service operation from an error string.
        return False

    def discover_devices(self) -> bool:
        """Request the existing presenter's public discovery command explicitly."""

        if self._busy:
            return False
        self._begin_explicit_command()
        self._discovery_pending = True
        try:
            self._presenter.discover_devices()
        except Exception as error:
            self._on_task_failed(str(error))
            return False
        return True

    def select_device(self, device_id: str) -> bool:
        """Delegate a chosen opaque identifier through the existing presenter only."""

        identifier = device_id.strip()
        if not identifier or self._busy:
            return False
        self._begin_explicit_command()
        self._presenter.select_device(identifier)
        return True

    def select_manual_uri(self, uri: str) -> bool:
        """Delegate a non-empty explicit URI; transport validation remains upstream."""

        value = uri.strip()
        if not value or self._busy:
            return False
        self._begin_explicit_command()
        self._presenter.select_manual_uri(value)
        return True

    def apply_configuration(self, configuration: object) -> bool:
        """Pass the frozen public configuration object without local device logic."""

        if self._busy:
            return False
        self._begin_explicit_command()
        self._presenter.apply_configuration(configuration)
        return True

    def refresh_presentation(self) -> None:
        """Rebuild labels from the current immutable snapshot without a presenter call."""

        self._publish()

    def preview_sweep(self, configuration: LiveConfiguration,
                      request: ContinuousSweepPlanRequest) -> AnalyzerGeometryPreflight:
        """Read-only public presenter port; never infer geometry in Qt."""
        preview = getattr(self._presenter, "preview_sweep", None)
        if not callable(preview):
            raise RuntimeError("Sweep preview is unavailable in this composition")
        result = preview(configuration, request)
        if not isinstance(result, AnalyzerGeometryPreflight) or result.mode != "sweep":
            raise ValueError("Invalid Sweep preview result")
        return result

    def dispose(self) -> None:
        """Release signal subscriptions without shutting down the presenter."""

        for signal, callback in (
            (self._presenter.devices_discovered, self._on_devices_discovered),
            *self._snapshot_connections,
            (self._presenter.busy_changed, self._on_busy_changed),
            (self._presenter.task_failed, self._on_task_failed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._listeners.clear()
        self._device_listeners.clear()
        self._layer_cache.clear()
        self._prepared_measurement = None

    def _on_devices_discovered(self, devices: object) -> None:
        valid_sequence = isinstance(devices, Iterable) and not isinstance(devices, (str, bytes))
        if isinstance(devices, Iterable) and not isinstance(devices, (str, bytes)):
            self._devices = tuple(devices)
        else:
            self._devices = ()
        # Success and busy=False are separate public signals. Keep the search
        # label until the command settles, without a one-frame generic-busy flash.
        self._discovery_pending = self._discovery_pending and self._busy
        # A malformed payload is not evidence that no receiver was found.
        identifiers = tuple(getattr(device, "device_id", None) for device in self._devices)
        valid_devices = all(isinstance(identifier, str) and bool(identifier.strip())
                            for identifier in identifiers)
        self._discovery_count = len(self._devices) if valid_sequence and valid_devices else None
        for listener in tuple(self._device_listeners):
            listener(self._devices)
        self._publish()

    def _on_snapshot(self, snapshot: object) -> None:
        self._prepared_measurement = None
        self._last_snapshot = snapshot
        self._publish()

    def _on_prepared_snapshot(self, value: object) -> None:
        if not isinstance(value, LiveViewState):
            self._on_task_failed("Invalid prepared Live publication")
            return
        self._prepared_measurement = value
        self._last_snapshot = value.snapshot
        self._publish()

    def _on_busy_changed(self, busy: bool) -> None:
        self._busy = bool(busy)
        if not self._busy:
            self._discovery_pending = False
        self._publish()

    def _on_task_failed(self, error: str) -> None:
        # Command failure is presentation control-plane state. It must remain
        # visible without rewriting immutable measurement/session truth.
        self._command_error = str(error)
        self._discovery_pending = False
        self._discovery_count = None
        self._publish()

    def _begin_explicit_command(self) -> None:
        if (self._command_error is not None or self._discovery_pending
                or self._discovery_count is not None):
            self._command_error = None
            self._discovery_pending = False
            self._discovery_count = None
            self._publish()

    def _publish(self) -> None:
        state = build_live_view_state(
            self._last_snapshot,
            busy=self._busy,
            now_ns=self._now_ns() if self._last_snapshot is not None else None,
            layer_cache=self._layer_cache,
            prepared_measurement=self._prepared_measurement,
        )
        self._state = (
            state if self._command_error is None else
            replace(state, error_label=self._command_error, error_kind="command-not-measurement")
        )
        if self._discovery_pending or self._discovery_count is not None:
            self._state = replace(self._state, discovery_pending=self._discovery_pending,
                                  discovery_count=self._discovery_count)
        for listener in tuple(self._listeners):
            listener(self._state)
