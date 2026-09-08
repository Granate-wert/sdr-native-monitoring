"""Public-signal adapter from the existing LivePresenter to UI V2 state."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Protocol

from ..state.live_view_state import LiveAction, LiveViewState, build_live_view_state


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
        self._last_snapshot: object | None = None
        self._listeners: list[Callable[[LiveViewState], None]] = []
        self._devices: tuple[object, ...] = ()
        self._device_listeners: list[Callable[[tuple[object, ...]], None]] = []
        self._state = build_live_view_state(None)
        presenter.devices_discovered.connect(self._on_devices_discovered)
        presenter.snapshot_changed.connect(self._on_snapshot)
        presenter.render_ready.connect(self._on_snapshot)
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
            self._presenter.discover_devices()
            return True
        if action is LiveAction.START:
            self._presenter.start()
            return True
        if action is LiveAction.STOP:
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
        self._presenter.discover_devices()
        return True

    def select_device(self, device_id: str) -> bool:
        """Delegate a chosen opaque identifier through the existing presenter only."""

        identifier = device_id.strip()
        if not identifier or self._busy:
            return False
        self._presenter.select_device(identifier)
        return True

    def select_manual_uri(self, uri: str) -> bool:
        """Delegate a non-empty explicit URI; transport validation remains upstream."""

        value = uri.strip()
        if not value or self._busy:
            return False
        self._presenter.select_manual_uri(value)
        return True

    def apply_configuration(self, configuration: object) -> bool:
        """Pass the frozen public configuration object without local device logic."""

        if self._busy:
            return False
        self._presenter.apply_configuration(configuration)
        return True

    def refresh_presentation(self) -> None:
        """Rebuild labels from the current immutable snapshot without a presenter call."""

        self._publish()

    def dispose(self) -> None:
        """Release signal subscriptions without shutting down the presenter."""

        for signal, callback in (
            (self._presenter.devices_discovered, self._on_devices_discovered),
            (self._presenter.snapshot_changed, self._on_snapshot),
            (self._presenter.render_ready, self._on_snapshot),
            (self._presenter.busy_changed, self._on_busy_changed),
            (self._presenter.task_failed, self._on_task_failed),
        ):
            try:
                signal.disconnect(callback)
            except (RuntimeError, TypeError):
                pass
        self._listeners.clear()
        self._device_listeners.clear()

    def _on_devices_discovered(self, devices: object) -> None:
        if isinstance(devices, Iterable) and not isinstance(devices, (str, bytes)):
            self._devices = tuple(devices)
        else:
            self._devices = ()
        for listener in tuple(self._device_listeners):
            listener(self._devices)

    def _on_snapshot(self, snapshot: object) -> None:
        self._last_snapshot = snapshot
        self._publish()

    def _on_busy_changed(self, busy: bool) -> None:
        self._busy = bool(busy)
        self._publish()

    def _on_task_failed(self, _error: str) -> None:
        # A presenter's transient failure must not replace immutable snapshot
        # truth. The existing UI notification layer remains responsible for a
        # transient toast until the V2 notification package is introduced.
        self._publish()

    def _publish(self) -> None:
        self._state = build_live_view_state(
            self._last_snapshot,
            busy=self._busy,
            now_ns=self._now_ns() if self._last_snapshot is not None else None,
        )
        for listener in tuple(self._listeners):
            listener(self._state)
