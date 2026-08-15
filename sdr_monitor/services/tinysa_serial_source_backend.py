"""Explicit PySerial tinySA discovery/backend; construction performs no I/O."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from serial.tools import list_ports

from .tinysa_capability_adapter import TinySaModel
from .tinysa_serial_settings_port import TinySaSerialSettingsCommandPort
from .tinysa_serial_trace_collector import (
    TinySaScanRawRequest,
    TinySaTraceCollection,
    collect_tinysa_scanraw_trace,
)
from .tinysa_serial_version_probe import (
    TINYSA_USB_PID,
    TINYSA_USB_VID,
    TinySaVersionObservation,
    probe_tinysa_version,
)
from .tinysa_source_composition import (
    TinySaBoundSettingsExecutor,
    TinySaBoundTraceCollector,
    TinySaIdentityAssurance,
    TinySaTransportEndpoint,
    endpoint_identity_key,
)
from .tinysa_sweep_settings_controller import (
    TinySaSettingsApplyResult,
    TinySaSettingsCommandPort,
    TinySaSweepSettingsPlan,
    apply_tinysa_sweep_settings,
)

_PORT_PATTERN = re.compile(r"COM(?:[1-9]|[1-9][0-9]|[12][0-9]{2})", re.IGNORECASE)


class TinySaSerialSourceBackend:
    """Revalidate one USB identity before every effecting endpoint action."""

    def __init__(
        self,
        *,
        inventory_provider: Callable[[], Iterable[object]] = list_ports.comports,
        version_probe: Callable[[str], TinySaVersionObservation] = probe_tinysa_version,
        trace_collector: Callable[
            [str, TinySaScanRawRequest], TinySaTraceCollection
        ] = collect_tinysa_scanraw_trace,
        settings_port_factory: Callable[
            [str], TinySaSettingsCommandPort
        ] = TinySaSerialSettingsCommandPort,
    ) -> None:
        for value, label in (
            (inventory_provider, "inventory provider"),
            (version_probe, "version probe"),
            (trace_collector, "trace collector"),
            (settings_port_factory, "settings port factory"),
        ):
            if not callable(value):
                raise TypeError(f"tinySA serial {label} must be callable")
        self._inventory_provider = inventory_provider
        self._version_probe = version_probe
        self._trace_collector = trace_collector
        self._settings_port_factory = settings_port_factory

    def discover_endpoints(self) -> tuple[TinySaTransportEndpoint, ...]:
        """Enumerate matching PnP entries only; do not open a serial port."""

        endpoints: list[TinySaTransportEndpoint] = []
        for candidate in self._inventory_provider():
            if (
                getattr(candidate, "vid", None) != TINYSA_USB_VID
                or getattr(candidate, "pid", None) != TINYSA_USB_PID
            ):
                continue
            route = _normalized_route(getattr(candidate, "device", None))
            serial_number = _optional_identity_text(
                getattr(candidate, "serial_number", None)
            )
            location = _optional_identity_text(getattr(candidate, "location", None))
            if serial_number is not None:
                assurance = TinySaIdentityAssurance.USB_SERIAL
                material = (
                    f"vid={TINYSA_USB_VID:04x}|pid={TINYSA_USB_PID:04x}|"
                    f"serial={serial_number.casefold()}"
                )
            elif location is not None:
                assurance = TinySaIdentityAssurance.USB_LOCATION
                material = (
                    f"vid={TINYSA_USB_VID:04x}|pid={TINYSA_USB_PID:04x}|"
                    f"location={location.casefold()}"
                )
            else:
                assurance = TinySaIdentityAssurance.PNP_ENDPOINT_ONLY
                material = (
                    f"vid={TINYSA_USB_VID:04x}|pid={TINYSA_USB_PID:04x}|"
                    f"endpoint={route.casefold()}"
                )
            identity_key = endpoint_identity_key(material)
            endpoints.append(
                TinySaTransportEndpoint(
                    route=route,
                    identity_key=identity_key,
                    display_label=f"tinySA USB CDC {identity_key[7:15]}",
                    assurance=assurance,
                )
            )
        endpoints.sort(key=lambda item: item.identity_key)
        return tuple(endpoints)

    def probe_version(
        self,
        endpoint: TinySaTransportEndpoint,
    ) -> TinySaVersionObservation:
        current = self._resolve(endpoint)
        result = self._version_probe(current.route)
        if not isinstance(result, TinySaVersionObservation):
            raise TypeError("tinySA version probe returned an invalid result")
        return result

    def make_collector(
        self,
        endpoint: TinySaTransportEndpoint,
        model: TinySaModel,
    ) -> TinySaBoundTraceCollector:
        return _BoundTinySaTraceCollector(self, endpoint, TinySaModel(model))

    def make_settings_executor(
        self,
        endpoint: TinySaTransportEndpoint,
    ) -> TinySaBoundSettingsExecutor:
        return _BoundTinySaSettingsExecutor(self, endpoint)

    def _resolve(self, expected: TinySaTransportEndpoint) -> TinySaTransportEndpoint:
        if not isinstance(expected, TinySaTransportEndpoint):
            raise TypeError("tinySA endpoint binding is invalid")
        matches = tuple(
            current
            for current in self.discover_endpoints()
            if current.identity_key == expected.identity_key
        )
        if len(matches) != 1:
            raise RuntimeError("tinySA endpoint identity is absent or ambiguous")
        return matches[0]


class _BoundTinySaTraceCollector:
    def __init__(
        self,
        backend: TinySaSerialSourceBackend,
        endpoint: TinySaTransportEndpoint,
        model: TinySaModel,
    ) -> None:
        self._backend = backend
        self._endpoint = endpoint
        self._model = model

    def collect(self, request: TinySaScanRawRequest) -> TinySaTraceCollection:
        if not isinstance(request, TinySaScanRawRequest) or request.model is not self._model:
            raise ValueError("tinySA trace request does not match the verified source model")
        current = self._backend._resolve(self._endpoint)
        result = self._backend._trace_collector(current.route, request)
        if not isinstance(result, TinySaTraceCollection):
            raise TypeError("tinySA trace collector returned an invalid result")
        return result


class _BoundTinySaSettingsExecutor:
    def __init__(
        self,
        backend: TinySaSerialSourceBackend,
        endpoint: TinySaTransportEndpoint,
    ) -> None:
        self._backend = backend
        self._endpoint = endpoint

    def apply(
        self,
        plan: TinySaSweepSettingsPlan,
        *,
        confirmation: str,
    ) -> TinySaSettingsApplyResult:
        if not isinstance(plan, TinySaSweepSettingsPlan):
            raise TypeError("tinySA settings request is invalid")

        def port_factory() -> TinySaSettingsCommandPort:
            current = self._backend._resolve(self._endpoint)
            return self._backend._settings_port_factory(current.route)

        return apply_tinysa_sweep_settings(
            plan,
            confirmation=confirmation,
            port_factory=port_factory,
        )


def _normalized_route(value: object) -> str:
    if not isinstance(value, str) or not _PORT_PATTERN.fullmatch(value):
        raise ValueError("tinySA PnP route is invalid")
    return value.upper()


def _optional_identity_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("tinySA USB identity field must be text")
    normalized = value.strip()
    if not normalized or normalized.casefold() in {"none", "unknown", "n/a", "-"}:
        return None
    if len(normalized) > 128 or any(character in normalized for character in ("\r", "\n")):
        raise ValueError("tinySA USB identity field is invalid")
    return normalized


__all__ = ["TinySaSerialSourceBackend"]
