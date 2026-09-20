"""Pure Live snapshot-to-presentation mapping for UI V2.

The functions here deliberately accept only immutable snapshot-shaped objects.
They do not import services, presenters, Qt, or renderer helpers and retain
renderer-ready spectrum frames by identity. Native auxiliary layers require
bounded presentation conversion, optionally reused by an owner-local cache.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sdr_monitor.domain import CalibrationQuality, LiveSessionState
from sdr_monitor.domain.live import LiveSnapshot, LiveSpectrumFrame
from sdr_monitor.domain.analyzer import AnalyzerFrameBundle, bundle_from_live

from ..i18n import text
from .analyzer_layers import persistence_density_from_native, waterfall_line_from_spectrum
from .analyzer_layer_cache import AnalyzerLayerCache
from ..spectrum.contracts import PreparedSpectrumFrame
from ..spectrum.allocation_budget import PresentationBudgetExceeded


class LiveAction(StrEnum):
    NONE = "none"
    DISCOVER = "discover"
    START = "start"
    STOP = "stop"
    RETRY = "retry"
    REVIEW_CONFIGURATION = "review_configuration"


class CalibrationPresentation(StrEnum):
    UNCALIBRATED = "uncalibrated"
    CALIBRATED = "calibrated"
    MISMATCH = "mismatch"
    PROFILE_SELECTED = "profile_selected"


class PersistencePresentation(StrEnum):
    NOT_CONFIGURED = "not_configured"
    DISABLED = "disabled"
    WAITING_FOR_FRAME = "waiting_for_frame"
    ACTIVE = "active"


@dataclass(frozen=True, slots=True)
class LiveLossSummary:
    """Named loss categories; presentation supersession is never FFT loss."""

    source_blocks: int = 0
    acquisition_blocks: int = 0
    fft_frames: int = 0
    publication_frames: int = 0
    bridge_frames: int = 0

    @property
    def analytical_loss(self) -> bool:
        return any((self.source_blocks, self.acquisition_blocks, self.fft_frames))

    @property
    def presentation_supersession(self) -> bool:
        return any((self.publication_frames, self.bridge_frames))


@dataclass(frozen=True, slots=True)
class LiveViewState:
    """Immutable, renderer-ready state derived without changing the snapshot."""

    snapshot: object | None
    connection_label: str
    acquisition_label: str
    primary_action: LiveAction
    primary_action_label: str
    primary_action_enabled: bool
    busy: bool
    device_label: str
    unit_label: str
    calibration: CalibrationPresentation
    calibration_label: str
    configuration_dirty: bool
    has_applied_configuration: bool
    has_spectrum: bool
    frozen_last_frame: bool
    data_age_ms: float | None
    data_age_label: str
    persistence: PersistencePresentation
    persistence_label: str
    loss: LiveLossSummary
    backend_label: str
    error_label: str | None
    error_kind: str | None
    spectrum: object | None
    persistence_frame: object | None
    waterfall_line: object | None
    analyzer_bundle: AnalyzerFrameBundle | None = None
    measurement_unavailable_reason: str | None = None
    # Control-plane observations, never measurement/session state. None means
    # no valid discovery result, not an empty result.
    discovery_pending: bool = False
    discovery_count: int | None = None
    prepared_spectrum: PreparedSpectrumFrame | None = None


def build_live_view_state(
    snapshot: object | None,
    *,
    busy: bool = False,
    now_ns: int | None = None,
    layer_cache: AnalyzerLayerCache | None = None,
    prepared_measurement: LiveViewState | None = None,
) -> LiveViewState:
    """Map a public immutable snapshot to labels and enabled controls.

    Optional fields are read through ``getattr``. This keeps the adapter
    compatible with the shipped minimal snapshot while using richer public
    fields when they are present. Missing data is unavailable, never inferred.
    """

    if prepared_measurement is not None and prepared_measurement.snapshot is not snapshot:
        raise ValueError("Prepared Live measurement must retain exact snapshot identity")
    if snapshot is None:
        if layer_cache is not None:
            layer_cache.clear()
        return _empty_state(busy=busy)

    state = _coerce_state(getattr(snapshot, "state", LiveSessionState.DISCONNECTED))
    spectrum = getattr(snapshot, "spectrum", None)
    invalid_measurement = False
    if prepared_measurement is not None:
        analyzer_bundle = prepared_measurement.analyzer_bundle
    elif isinstance(snapshot, LiveSnapshot):
        try:
            analyzer_bundle = bundle_from_live(snapshot)
        except (TypeError, ValueError, OverflowError):
            analyzer_bundle = None
            invalid_measurement = True
    else:
        analyzer_bundle = None
    if isinstance(snapshot, LiveSnapshot):
        spectrum = analyzer_bundle.spectrum if analyzer_bundle is not None else None
    # Product LiveSnapshot layers pass only through the domain coherence gate.
    # Snapshot-shaped test/public adapters retain their established behavior.
    persistence_frame = (
        analyzer_bundle.persistence if analyzer_bundle is not None
        else None if isinstance(snapshot, LiveSnapshot)
        else getattr(snapshot, "persistence", None)
    )
    waterfall_line = (
        analyzer_bundle.waterfall_line if analyzer_bundle is not None
        else None if isinstance(snapshot, LiveSnapshot)
        else getattr(snapshot, "waterfall_line", None)
    )
    coherence_issues = list(analyzer_bundle.coherence_issues) if analyzer_bundle is not None else []
    if analyzer_bundle is None and layer_cache is not None:
        layer_cache.clear()
    if prepared_measurement is not None:
        spectrum = prepared_measurement.spectrum
        persistence_frame = prepared_measurement.persistence_frame
        waterfall_line = prepared_measurement.waterfall_line
    elif analyzer_bundle is not None and isinstance(analyzer_bundle.spectrum, LiveSpectrumFrame):
        try:
            persistence_frame = (persistence_density_from_native(analyzer_bundle.persistence)
                if layer_cache is None else layer_cache.persistence(analyzer_bundle.persistence))
        except PresentationBudgetExceeded:
            persistence_frame = None
            coherence_issues.append("presentation_memory_budget")
        except (TypeError, ValueError, OverflowError):
            persistence_frame = None
            coherence_issues.append("persistence_geometry_invalid")
        if analyzer_bundle.persistence is not None and persistence_frame is None:
            coherence_issues.append("persistence_values_invalid")
        try:
            waterfall_line = (waterfall_line_from_spectrum(analyzer_bundle.spectrum)
                if layer_cache is None else layer_cache.waterfall(analyzer_bundle.spectrum))
        except PresentationBudgetExceeded:
            waterfall_line = None
            coherence_issues.append("presentation_memory_budget")
        except (TypeError, ValueError, OverflowError):
            waterfall_line = None
            coherence_issues.append("waterfall_geometry_invalid")
    has_spectrum = spectrum is not None
    frozen_last_frame = state is LiveSessionState.CONNECTED and has_spectrum
    connection_label, acquisition_label, action = _state_labels(state, frozen_last_frame)
    error_kind = _value_name(getattr(snapshot, "error_kind", None))
    if state is LiveSessionState.ERROR:
        action = _error_action(error_kind)
    if getattr(snapshot, "stop_required", False):
        action = LiveAction.STOP
    primary_action_label = _action_label(action)
    device = getattr(snapshot, "device", None)
    device_label = str(getattr(device, "label", text("live_state.device.unselected")))
    unit_label = str(getattr(snapshot, "unit", "dBFS/bin") or "dBFS/bin")
    if analyzer_bundle is not None:
        unit_label = analyzer_bundle.unit
    calibration, calibration_label = _calibration_presentation(snapshot)
    applied = getattr(snapshot, "applied", None)
    configuration_dirty = bool(
        applied is not None
        and getattr(applied, "requested", None) != getattr(applied, "applied", None)
    )
    has_applied_configuration = getattr(applied, "applied", None) is not None
    data_age_ms = _data_age_ms(spectrum, now_ns)
    persistence, persistence_label = _persistence_presentation(applied, persistence_frame)
    quality = getattr(snapshot, "quality", None)
    performance = getattr(snapshot, "performance", None)
    return LiveViewState(
        snapshot=snapshot,
        connection_label=connection_label,
        acquisition_label=acquisition_label,
        primary_action=action,
        primary_action_label=primary_action_label,
        primary_action_enabled=_action_is_available(action) and not busy,
        busy=busy,
        device_label=device_label,
        unit_label=unit_label,
        calibration=calibration,
        calibration_label=calibration_label,
        configuration_dirty=configuration_dirty,
        has_applied_configuration=has_applied_configuration,
        has_spectrum=has_spectrum,
        frozen_last_frame=frozen_last_frame,
        data_age_ms=data_age_ms,
        data_age_label=_format_data_age(data_age_ms, has_frame=has_spectrum),
        persistence=persistence,
        persistence_label=persistence_label,
        loss=_loss_summary(quality, performance, spectrum),
        backend_label=_backend_label(quality),
        error_label=_optional_text(getattr(snapshot, "error", None)),
        error_kind=error_kind,
        spectrum=spectrum,
        persistence_frame=persistence_frame,
        waterfall_line=waterfall_line,
        analyzer_bundle=analyzer_bundle,
        prepared_spectrum=(prepared_measurement.prepared_spectrum
                           if prepared_measurement is not None else None),
        measurement_unavailable_reason=(
            prepared_measurement.measurement_unavailable_reason if prepared_measurement is not None else
            "presentation_memory_budget" if getattr(snapshot, "presentation_omission", None) is not None else
            coherence_issues[0] if coherence_issues else
            "invalid_measurement" if invalid_measurement else
            "stale_measurement_identity" if isinstance(snapshot, LiveSnapshot)
            and getattr(snapshot, "spectrum", None) is not None and analyzer_bundle is None else None
        ),
    )


def _empty_state(*, busy: bool) -> LiveViewState:
    return LiveViewState(
        snapshot=None,
        connection_label=text("live_state.connection.none"),
        acquisition_label=text("live_state.acquisition.unstarted"),
        primary_action=LiveAction.DISCOVER,
        primary_action_label=_action_label(LiveAction.DISCOVER),
        primary_action_enabled=not busy,
        busy=busy,
        device_label=text("live_state.device.unselected"),
        unit_label="dBFS/bin",
        calibration=CalibrationPresentation.UNCALIBRATED,
        calibration_label=text("live_state.calibration.uncalibrated"),
        configuration_dirty=False,
        has_applied_configuration=False,
        has_spectrum=False,
        frozen_last_frame=False,
        data_age_ms=None,
        data_age_label=_format_data_age(None, has_frame=False),
        persistence=PersistencePresentation.NOT_CONFIGURED,
        persistence_label=text("live_state.persistence.not_configured"),
        loss=LiveLossSummary(),
        backend_label=text("live_state.backend.empty"),
        error_label=None,
        error_kind=None,
        spectrum=None,
        persistence_frame=None,
        waterfall_line=None,
        measurement_unavailable_reason=None,
    )


def _state_labels(state: LiveSessionState, frozen_last_frame: bool) -> tuple[str, str, LiveAction]:
    if state is LiveSessionState.CONNECTING:
        return text("live_state.connection.connecting"), text("live_state.acquisition.unstarted"), LiveAction.NONE
    if state is LiveSessionState.CONNECTED:
        if frozen_last_frame:
            return text("live_state.connection.ready"), text("live_state.acquisition.stopped_last_frame"), LiveAction.START
        return text("live_state.connection.ready"), text("live_state.acquisition.unstarted"), LiveAction.START
    if state is LiveSessionState.STARTING:
        return text("live_state.connection.ready"), text("live_state.acquisition.starting"), LiveAction.NONE
    if state is LiveSessionState.RUNNING:
        return text("live_state.connection.ready"), text("live_state.acquisition.running"), LiveAction.STOP
    if state is LiveSessionState.STOPPING:
        return text("live_state.connection.ready"), text("live_state.acquisition.stopping"), LiveAction.NONE
    if state is LiveSessionState.ERROR:
        return text("live_state.connection.error"), text("live_state.acquisition.unavailable"), LiveAction.RETRY
    return text("live_state.connection.none"), text("live_state.acquisition.unstarted"), LiveAction.DISCOVER


def _error_action(error_kind: str | None) -> LiveAction:
    if error_kind == "configuration_rejected":
        return LiveAction.REVIEW_CONFIGURATION
    if error_kind == "device_not_found":
        return LiveAction.DISCOVER
    return LiveAction.RETRY


def _action_label(action: LiveAction) -> str:
    return {
        LiveAction.NONE: text("live_state.action.none"),
        LiveAction.DISCOVER: text("live_state.action.discover"),
        LiveAction.START: text("live_state.action.start"),
        LiveAction.STOP: text("live_state.action.stop"),
        LiveAction.RETRY: text("live_state.action.retry"),
        LiveAction.REVIEW_CONFIGURATION: text("live_state.action.review_configuration"),
    }[action]


def _action_is_available(action: LiveAction) -> bool:
    """Keep controls disabled until UI2 has a documented presenter command."""

    return action in {LiveAction.DISCOVER, LiveAction.START, LiveAction.STOP}


def _calibration_presentation(snapshot: object) -> tuple[CalibrationPresentation, str]:
    quality = getattr(snapshot, "quality", None)
    calibration = getattr(quality, "calibration", CalibrationQuality.UNCALIBRATED)
    reports_dbm = bool(getattr(snapshot, "reports_dbm", False))
    if reports_dbm and calibration is CalibrationQuality.CALIBRATED:
        return CalibrationPresentation.CALIBRATED, text("live_state.calibration.calibrated")
    if calibration is CalibrationQuality.MISMATCH:
        return CalibrationPresentation.MISMATCH, text("live_state.calibration.mismatch")
    applied = getattr(snapshot, "applied", None)
    applied_config = getattr(applied, "applied", None)
    if getattr(applied_config, "profile_id", None):
        return CalibrationPresentation.PROFILE_SELECTED, text("live_state.calibration.profile_selected")
    return CalibrationPresentation.UNCALIBRATED, text("live_state.calibration.uncalibrated")


def _persistence_presentation(
    applied: object | None,
    persistence_frame: object | None,
) -> tuple[PersistencePresentation, str]:
    configuration = getattr(applied, "applied", None)
    if configuration is None or not hasattr(configuration, "persistence_enabled"):
        return PersistencePresentation.NOT_CONFIGURED, text("live_state.persistence.not_configured")
    if not bool(getattr(configuration, "persistence_enabled")):
        return PersistencePresentation.DISABLED, text("live_state.persistence.disabled")
    if persistence_frame is None:
        return PersistencePresentation.WAITING_FOR_FRAME, text("live_state.persistence.waiting")
    return PersistencePresentation.ACTIVE, text("live_state.persistence.active")


def _loss_summary(quality: object | None, performance: object | None, spectrum: object | None) -> LiveLossSummary:
    return LiveLossSummary(
        source_blocks=max(
            _counter(performance, "source_blocks_dropped"),
            _counter(quality, "dropped_blocks"),
        ),
        acquisition_blocks=_counter(performance, "acquisition_queue_blocks_dropped"),
        fft_frames=max(
            _counter(performance, "fft_frames_dropped"),
            _counter(spectrum, "dropped_fft_frames_before"),
        ),
        publication_frames=_counter(performance, "snapshots_superseded"),
        bridge_frames=_counter(performance, "bridge_frames_coalesced"),
    )


def _backend_label(quality: object | None) -> str:
    backend = _value_name(getattr(quality, "backend", None))
    fallback = _optional_text(getattr(quality, "fallback_reason", None))
    if fallback:
        return f"{(backend or 'CPU').upper()} fallback: {fallback}"
    return (backend or text("live_state.backend.unselected")).upper()


def _data_age_ms(spectrum: object | None, now_ns: int | None) -> float | None:
    if isinstance(spectrum, LiveSpectrumFrame):
        if str(getattr(spectrum, "clock_domain", "")).casefold() != "unix_ns":
            return None
        if _value_name(getattr(spectrum, "timestamp_quality", None)) in (None, "unknown"):
            return None
    timestamp = getattr(spectrum, "timestamp_ns", None)
    if timestamp is None or now_ns is None:
        return None
    return max(0.0, (int(now_ns) - int(timestamp)) / 1_000_000.0)


def _format_data_age(value: float | None, *, has_frame: bool) -> str:
    if value is None:
        return text("live_state.age.unknown" if has_frame else "live_state.age.empty")
    if value < 1_000.0:
        return text("live_state.age.milliseconds", value=value)
    return text("live_state.age.seconds", value=value / 1_000.0)


def _counter(source: object | None, name: str, fallback: int = 0) -> int:
    value = getattr(source, name, fallback)
    return max(0, int(value or 0))


def _coerce_state(value: object) -> LiveSessionState:
    if isinstance(value, LiveSessionState):
        return value
    try:
        return LiveSessionState(str(value))
    except ValueError:
        return LiveSessionState.DISCONNECTED


def _value_name(value: object | None) -> str | None:
    if value is None:
        return None
    raw = getattr(value, "value", value)
    return str(raw).strip().casefold() or None


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
