"""Fail-closed scalar contract for a future visible tinySA product UI witness.

The module deliberately imports neither Qt nor serial.  R11-AE software review
can therefore validate the exact visible-cell semantics without opening a
window, enumerating PnP or touching the analyzer.
"""

from __future__ import annotations

import json
import math
import os
import secrets
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

R11AE_PREFLIGHT_SCHEMA = "sdr-native-r11ae-tinysa-visible-ui-preflight-v1"
R11AE_READY_PREFLIGHT_SCHEMA = "sdr-native-r11ae-tinysa-visible-ui-ready-v2"
R11AE_DISPLAY_PREFLIGHT_SCHEMA = "sdr-native-r11ae-tinysa-visible-ui-display-preflight-v1"
R11AE_EVIDENCE_SCHEMA = "sdr-native-r11ae-tinysa-visible-ui-evidence-v3"
R11AE_EVIDENCE_OUTPUT_CLAIM_SCHEMA = "sdr-native-r11ae-tinysa-visible-ui-claim-v1"
R11AE_PROFILE_ID = "tinysa-ultra-product-ui-10001-v1"
R11AE_CONFIRMATION = "I CONFIRM R11-AE VISIBLE TINYSA UI"
# This is deliberately a default, not a standing hardware permission.  A
# physical run additionally needs the exact per-process environment gate and
# the exact CLI confirmation below.
R11AE_VISIBLE_EXECUTION_ENABLED = False
R11AE_VISIBLE_EXECUTION_ENABLE_ENV = "R11AE_VISIBLE_EXECUTION_ENABLE_PROCESS"
R11AE_POINT_COUNT = 10_001
R11AE_FRAME_BYTES = 30_005
R11AE_FRAME_BYTES_MAX = 30 * 1024
R11AE_PRESENTATION_WIDTH = 1_024
R11AE_PRESENTATION_POINTS_MAX = 2 * R11AE_PRESENTATION_WIDTH
R11AE_MIN_LOGICAL_WIDTH = 1_024
R11AE_MIN_LOGICAL_HEIGHT = 640
R11AE_ALLOWED_SCALE_PERCENT = frozenset((100, 150, 200, 300))
R11AE_DISPLAY_PREFLIGHT_MAX_AGE_SECONDS = 900.0
R11AE_ALLOWED_IDENTITY_ASSURANCE = frozenset(
    ("usb_serial", "usb_location", "pnp_endpoint_only_continuity_unverified")
)


@dataclass(frozen=True, slots=True)
class R11AEVisibleUiProfile:
    """One exact Windows scale cell at the normal product maximum."""

    scale_percent: int
    width: int = 1_280
    height: int = 900
    point_count: int = R11AE_POINT_COUNT
    frame_bytes: int = R11AE_FRAME_BYTES
    presentation_width: int = R11AE_PRESENTATION_WIDTH
    expected_platform: str = "windows"
    trace_deadline_seconds: float = 120.0
    interaction_timeout_seconds: float = 180.0
    minimum_heartbeat_samples: int = 10
    maximum_heartbeat_stall_ms: float = 250.0
    maximum_worker_to_gui_latency_ms: float = 100.0
    maximum_canvas_paint_ms: float = 50.0
    minimum_resize_delta_logical_px: int = 24

    def __post_init__(self) -> None:
        if self.scale_percent not in R11AE_ALLOWED_SCALE_PERCENT:
            raise ValueError("R11-AE scale must be 100, 150, 200 or 300 percent")
        if self.width < R11AE_MIN_LOGICAL_WIDTH or self.height < R11AE_MIN_LOGICAL_HEIGHT:
            raise ValueError("R11-AE logical window is below the visible matrix minimum")
        if self.point_count != R11AE_POINT_COUNT or self.frame_bytes != R11AE_FRAME_BYTES:
            raise ValueError("R11-AE visible maximum cell must use exactly 10001 points/30005 bytes")
        if self.frame_bytes > R11AE_FRAME_BYTES_MAX:
            raise ValueError("R11-AE frame exceeds the independent 30-KiB product bound")
        if not 1 <= self.presentation_width <= 4_096:
            raise ValueError("R11-AE presentation width is outside the product bound")
        if self.expected_platform.casefold() != "windows":
            raise ValueError("R11-AE visible evidence requires the Windows Qt platform")
        for value in (
            self.trace_deadline_seconds,
            self.interaction_timeout_seconds,
            self.maximum_heartbeat_stall_ms,
            self.maximum_worker_to_gui_latency_ms,
            self.maximum_canvas_paint_ms,
        ):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("R11-AE timing thresholds must be finite and positive")
        if not 1 <= self.minimum_heartbeat_samples <= 10_000:
            raise ValueError("R11-AE heartbeat sample minimum is invalid")
        if not 8 <= self.minimum_resize_delta_logical_px <= 512:
            raise ValueError("R11-AE resize threshold is invalid")

    @property
    def expected_device_pixel_ratio(self) -> float:
        return self.scale_percent / 100.0

    def to_json(self) -> dict[str, object]:
        return {
            "profile_id": R11AE_PROFILE_ID,
            "scale_percent": self.scale_percent,
            "window": {"width": self.width, "height": self.height},
            "point_count": self.point_count,
            "frame_bytes": self.frame_bytes,
            "frame_bytes_max": R11AE_FRAME_BYTES_MAX,
            "presentation_width": self.presentation_width,
            "presentation_points_max": 2 * self.presentation_width,
            "expected_platform": self.expected_platform,
            "trace_deadline_seconds": self.trace_deadline_seconds,
            "interaction_timeout_seconds": self.interaction_timeout_seconds,
            "thresholds": {
                "minimum_heartbeat_samples": self.minimum_heartbeat_samples,
                "maximum_heartbeat_stall_ms": self.maximum_heartbeat_stall_ms,
                "maximum_worker_to_gui_latency_ms": self.maximum_worker_to_gui_latency_ms,
                "maximum_canvas_paint_ms": self.maximum_canvas_paint_ms,
                "minimum_resize_delta_logical_px": self.minimum_resize_delta_logical_px,
            },
        }


@dataclass(frozen=True, slots=True)
class R11AEEvidenceOutputClaim:
    """Exclusive final-output reservation without importing benchmark/runtime code."""

    destination: Path
    token: str

    def payload(self) -> dict[str, str]:
        return {
            "schema": R11AE_EVIDENCE_OUTPUT_CLAIM_SCHEMA,
            "state": "reserved_before_visible_qt_or_serial",
            "token": self.token,
        }


@dataclass(frozen=True, slots=True)
class R11AEDisplayPreflight:
    """Disposable visible-display observation that cannot reserve a final cell."""

    profile: R11AEVisibleUiProfile
    observed_at_utc: str
    platform_name: str
    window_visible: bool
    window_paint_events: int
    device_pixel_ratio: float
    logical_dpi_x: float
    logical_dpi_y: float
    logical_width: int
    logical_height: int
    pnp_enumerations: int = 0
    serial_ports_opened: int = 0
    device_commands: int = 0
    final_evidence_claims: int = 0

    def __post_init__(self) -> None:
        _parse_utc(self.observed_at_utc)
        if self.platform_name.casefold() != self.profile.expected_platform.casefold():
            raise ValueError("R11-AE display preflight requires the visible Windows Qt platform")
        if not self.window_visible or self.window_paint_events < 1:
            raise ValueError("R11-AE display preflight requires one visible painted window")
        for value in (self.device_pixel_ratio, self.logical_dpi_x, self.logical_dpi_y):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("R11-AE display preflight screen metrics are invalid")
        if abs(self.device_pixel_ratio - self.profile.expected_device_pixel_ratio) > 0.08:
            raise ValueError("R11-AE display preflight device pixel ratio differs from the selected scale")
        if self.logical_width < self.profile.width or self.logical_height < self.profile.height:
            raise ValueError("R11-AE display preflight cannot fit the selected logical window")
        if (
            self.pnp_enumerations,
            self.serial_ports_opened,
            self.device_commands,
            self.final_evidence_claims,
        ) != (0, 0, 0, 0):
            raise ValueError("R11-AE display preflight must not enumerate or touch a device or claim evidence")

    def to_json(self) -> dict[str, object]:
        return {
            "schema": R11AE_DISPLAY_PREFLIGHT_SCHEMA,
            "status": "OBSERVED_DISPLAY_ONLY_NO_FINAL_CLAIM",
            "profile": self.profile.to_json(),
            "observed_at_utc": self.observed_at_utc,
            "display": {
                "platform_name": self.platform_name,
                "window_visible": self.window_visible,
                "window_paint_events": self.window_paint_events,
                "device_pixel_ratio": self.device_pixel_ratio,
                "logical_dpi_x": self.logical_dpi_x,
                "logical_dpi_y": self.logical_dpi_y,
                "logical_width": self.logical_width,
                "logical_height": self.logical_height,
            },
            "non_device_actions": {
                "pnp_enumerations": self.pnp_enumerations,
                "serial_ports_opened": self.serial_ports_opened,
                "device_commands": self.device_commands,
                "final_evidence_claims": self.final_evidence_claims,
            },
            "claim_limits": [
                "display preflight is not a final visible R11-AE cell",
                "display preflight is not human interaction, trace, device or continuity evidence",
                "display preflight alone does not admit a serial, PnP or final evidence action",
            ],
        }


@dataclass(frozen=True, slots=True)
class R11AEVisibleUiEvidence:
    """Scalar result for one human-visible, one-trace normal-product cell."""

    profile: R11AEVisibleUiProfile
    draft_preflight_sha256: str
    ready_preflight_sha256: str
    display_preflight_sha256: str
    source_id: str
    identity_assurance: str
    observed_at_utc: str
    platform_name: str
    window_visible: bool
    window_active: bool
    device_pixel_ratio: float
    logical_dpi_x: float
    logical_dpi_y: float
    logical_width: int
    logical_height: int
    mouse_button_presses: int
    keyboard_key_presses: int
    maximum_resize_delta_logical_px: int
    discover_actions: int
    select_actions: int
    version_verify_actions: int
    compose_actions: int
    collect_actions: int
    point_count: int
    finite_point_count: int
    unit: str
    value_provenance: str
    calibration_provenance: str
    full_analytical_trace_retained: bool
    presentation_width: int
    presentation_point_count: int
    peak_preserving_presentation: bool
    trace_elapsed_seconds: float
    observed_points_per_second: float
    heartbeat_samples_while_busy: int
    maximum_heartbeat_stall_ms: float
    worker_to_gui_latency_ms: float
    canvas_trace_paint_events: int
    maximum_canvas_paint_ms: float
    version_commands: int
    preflight_pnp_revalidations: int
    measurement_commands: int
    readback_commands: int
    settings_writes: int
    settings_apply_attempts: int
    resets: int
    firmware_writes: int
    persistent_configuration_writes: int
    retries: int
    port_closed: bool
    shell_closed: bool

    def __post_init__(self) -> None:
        if not all(
            _is_digest(value)
            for value in (
                self.draft_preflight_sha256,
                self.ready_preflight_sha256,
                self.display_preflight_sha256,
            )
        ):
            raise ValueError("R11-AE evidence requires all retained preflight digests")
        if not _is_source_id(self.source_id):
            raise ValueError("R11-AE evidence source id is invalid")
        if self.identity_assurance not in R11AE_ALLOWED_IDENTITY_ASSURANCE:
            raise ValueError("R11-AE evidence identity assurance is invalid")
        _parse_utc(self.observed_at_utc)
        if self.platform_name.casefold() != self.profile.expected_platform.casefold():
            raise ValueError("R11-AE visible Qt platform differs from the profile")
        if not self.window_visible or not self.window_active:
            raise ValueError("R11-AE requires a visible active product window")
        for value in (self.device_pixel_ratio, self.logical_dpi_x, self.logical_dpi_y):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("R11-AE screen metrics are invalid")
        if abs(self.device_pixel_ratio - self.profile.expected_device_pixel_ratio) > 0.08:
            raise ValueError("R11-AE device pixel ratio differs from the selected Windows scale")
        if (
            self.logical_width < R11AE_MIN_LOGICAL_WIDTH
            or self.logical_height < R11AE_MIN_LOGICAL_HEIGHT
        ):
            raise ValueError("R11-AE visible window is below the matrix minimum")
        if self.mouse_button_presses < 1 or self.keyboard_key_presses < 2:
            raise ValueError("R11-AE requires both mouse and keyboard interaction")
        if self.maximum_resize_delta_logical_px < self.profile.minimum_resize_delta_logical_px:
            raise ValueError("R11-AE requires a visible manual resize")
        transitions = (
            self.discover_actions,
            self.select_actions,
            self.version_verify_actions,
            self.compose_actions,
            self.collect_actions,
        )
        if transitions != (1, 1, 1, 1, 1):
            raise ValueError("R11-AE requires exactly one explicit normal-product transition")
        if self.point_count != self.profile.point_count or self.finite_point_count != self.point_count:
            raise ValueError("R11-AE did not retain one complete finite maximum-size trace")
        if self.unit != "dBm":
            raise ValueError("R11-AE values must remain device-reported dBm")
        if self.value_provenance != "device_reported_trace":
            raise ValueError("R11-AE value provenance is invalid")
        if self.calibration_provenance != "device_reported_builtin":
            raise ValueError("R11-AE calibration provenance is invalid")
        if not self.full_analytical_trace_retained or not self.peak_preserving_presentation:
            raise ValueError("R11-AE analytical retention/presentation contract failed")
        if self.presentation_width != self.profile.presentation_width:
            raise ValueError("R11-AE presentation width differs from the profile")
        if not 1 <= self.presentation_point_count <= 2 * self.presentation_width:
            raise ValueError("R11-AE Qt presentation exceeds two extrema per pixel bucket")
        for value in (
            self.trace_elapsed_seconds,
            self.observed_points_per_second,
            self.maximum_heartbeat_stall_ms,
            self.worker_to_gui_latency_ms,
            self.maximum_canvas_paint_ms,
        ):
            if not math.isfinite(value) or value < 0.0:
                raise ValueError("R11-AE timing scalar is invalid")
        if not 0.0 < self.trace_elapsed_seconds <= self.profile.trace_deadline_seconds:
            raise ValueError("R11-AE trace exceeded its fixed deadline")
        expected_rate = self.point_count / self.trace_elapsed_seconds
        if not math.isclose(self.observed_points_per_second, expected_rate, rel_tol=1e-9):
            raise ValueError("R11-AE points/s scalar is inconsistent")
        if self.heartbeat_samples_while_busy < self.profile.minimum_heartbeat_samples:
            raise ValueError("R11-AE did not observe enough event-loop heartbeats while busy")
        if self.maximum_heartbeat_stall_ms > self.profile.maximum_heartbeat_stall_ms:
            raise ValueError("R11-AE event-loop heartbeat stall exceeded the threshold")
        if self.worker_to_gui_latency_ms > self.profile.maximum_worker_to_gui_latency_ms:
            raise ValueError("R11-AE worker-to-GUI delivery exceeded the threshold")
        if self.canvas_trace_paint_events < 1:
            raise ValueError("R11-AE did not visibly paint the reduced trace")
        if self.maximum_canvas_paint_ms > self.profile.maximum_canvas_paint_ms:
            raise ValueError("R11-AE canvas paint exceeded the threshold")
        if (
            self.version_commands,
            self.preflight_pnp_revalidations,
            self.measurement_commands,
            self.readback_commands,
        ) != (1, 1, 1, 1):
            raise ValueError("R11-AE read-only command counts differ from the fixed cell")
        if any(
            value != 0
            for value in (
                self.settings_writes,
                self.settings_apply_attempts,
                self.resets,
                self.firmware_writes,
                self.persistent_configuration_writes,
                self.retries,
            )
        ):
            raise ValueError("R11-AE forbids settings, firmware, persistence and retry actions")
        if not self.port_closed or not self.shell_closed:
            raise ValueError("R11-AE requires clean serial and shell close")

    def to_json(self) -> dict[str, object]:
        return {
            "schema": R11AE_EVIDENCE_SCHEMA,
            "status": "OBSERVED_VISIBLE_WITH_LIMITATIONS",
            "preflight": {
                "draft_sha256": self.draft_preflight_sha256,
                "ready_sha256": self.ready_preflight_sha256,
                "display_sha256": self.display_preflight_sha256,
            },
            "profile": self.profile.to_json(),
            "source": {
                "source_id": self.source_id,
                "identity_assurance": self.identity_assurance,
                "continuity_verified": False,
                "route_serialized": False,
            },
            "observed_at_utc": self.observed_at_utc,
            "platform": {
                "name": self.platform_name,
                "window_visible": self.window_visible,
                "window_active": self.window_active,
                "device_pixel_ratio": self.device_pixel_ratio,
                "logical_dpi_x": self.logical_dpi_x,
                "logical_dpi_y": self.logical_dpi_y,
                "logical_width": self.logical_width,
                "logical_height": self.logical_height,
            },
            "human_interaction": {
                "mouse_button_presses": self.mouse_button_presses,
                "keyboard_key_presses": self.keyboard_key_presses,
                "maximum_resize_delta_logical_px": self.maximum_resize_delta_logical_px,
            },
            "product_transitions": {
                "discover": self.discover_actions,
                "select": self.select_actions,
                "version_verify": self.version_verify_actions,
                "compose": self.compose_actions,
                "collect_one_trace": self.collect_actions,
            },
            "trace": {
                "point_count": self.point_count,
                "finite_point_count": self.finite_point_count,
                "unit": self.unit,
                "value_provenance": self.value_provenance,
                "calibration_provenance": self.calibration_provenance,
                "full_analytical_trace_retained": self.full_analytical_trace_retained,
                "presentation_width": self.presentation_width,
                "presentation_point_count": self.presentation_point_count,
                "peak_preserving_presentation": self.peak_preserving_presentation,
                "trace_elapsed_seconds": self.trace_elapsed_seconds,
                "observed_points_per_second": self.observed_points_per_second,
            },
            "ui_performance": {
                "heartbeat_samples_while_busy": self.heartbeat_samples_while_busy,
                "maximum_heartbeat_stall_ms": self.maximum_heartbeat_stall_ms,
                "worker_to_gui_latency_ms": self.worker_to_gui_latency_ms,
                "canvas_trace_paint_events": self.canvas_trace_paint_events,
                "maximum_canvas_paint_ms": self.maximum_canvas_paint_ms,
            },
            "hardware_actions": {
                "version_commands": self.version_commands,
                "preflight_pnp_revalidations": self.preflight_pnp_revalidations,
                "measurement_commands": self.measurement_commands,
                "readback_commands": self.readback_commands,
                "settings_writes": self.settings_writes,
                "settings_apply_attempts": self.settings_apply_attempts,
                "resets": self.resets,
                "firmware_writes": self.firmware_writes,
                "persistent_configuration_writes": self.persistent_configuration_writes,
                "retries": self.retries,
            },
            "shutdown": {"port_closed": self.port_closed, "shell_closed": self.shell_closed},
            "metric_semantics": {
                "observed_points_per_second": "host-total points divided by one trace elapsed time",
                "not_lps": True,
                "not_fft_frames_per_second": True,
                "not_continuous_trace_rate": True,
            },
            "not_verified": [
                "metrological_accuracy",
                "device_identity_continuity",
                "continuous_trace_rate",
                "settings_effects_or_readback",
                "spectrozir_parity",
                "counts_above_10001_points",
            ],
        }


def r11ae_visible_execution_is_enabled() -> bool:
    """Return the explicit process-local opt-in; default remains fail-closed."""

    return os.environ.get(R11AE_VISIBLE_EXECUTION_ENABLE_ENV) == "1"


def build_r11ae_preflight(
    profile: R11AEVisibleUiProfile,
    *,
    visible_execution_enabled: bool = R11AE_VISIBLE_EXECUTION_ENABLED,
) -> dict[str, object]:
    """Build an exact no-Qt/no-PnP/no-serial software-review record."""

    if not isinstance(visible_execution_enabled, bool):
        raise TypeError("R11-AE visible execution flag must be boolean")
    return {
        "schema": R11AE_PREFLIGHT_SCHEMA,
        "execution": "dry_run_no_qt_no_pnp_no_serial",
        "visible_execution_enabled": visible_execution_enabled,
        "confirmation": R11AE_CONFIRMATION,
        "profile": profile.to_json(),
        "required_visible_flow": [
            "human opens the normal tinySA product entry",
            "human performs Discover, Select, Verify identity and Use verified source once",
            "human requests exactly one unchanged-settings 10001-point trace",
            "human uses mouse and keyboard and resizes the active window while the worker is busy",
            "full analytical dBm trace stays outside Qt and the reduced trace visibly paints",
            "serial port and AppShell close cleanly",
        ],
        "allowed_device_commands": {"version": 1, "zero_readback": 1, "scanraw": 1},
        "forbidden_actions": [
            "settings_write",
            "reset",
            "firmware_write",
            "persistent_configuration_write",
            "retry",
            "point_count_above_10001",
        ],
        "metric_separation": [
            "trace elapsed time and host-total points/s",
            "event-loop heartbeat stall while the serial worker is busy",
            "worker-completion to GUI dispatch latency",
            "bounded reduced-trace canvas paint time",
        ],
        "claim_limits": [
            "points/s is not LPS or FFT/s",
            "offscreen tests are not visible Windows DPI evidence",
            "one trace is not continuous-rate, accuracy or Spectrozir parity evidence",
        ],
    }


def validate_r11ae_preflight(payload: object, profile: R11AEVisibleUiProfile) -> None:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("visible_execution_enabled"), bool
    ):
        raise TypeError("R11-AE preflight execution flag is invalid")
    if payload != build_r11ae_preflight(
        profile,
        visible_execution_enabled=payload["visible_execution_enabled"],
    ):
        raise ValueError("R11-AE preflight does not exactly match the selected scale cell")


def build_r11ae_ready_preflight(
    profile: R11AEVisibleUiProfile,
    *,
    draft_preflight_sha256: str,
    source_id: str,
    identity_assurance: str,
) -> dict[str, object]:
    """Bind a reviewed draft to one current route-free PnP identity."""

    if not _is_digest(draft_preflight_sha256):
        raise ValueError("R11-AE draft preflight digest is invalid")
    if not _is_source_id(source_id):
        raise ValueError("R11-AE ready source id is invalid")
    if identity_assurance not in R11AE_ALLOWED_IDENTITY_ASSURANCE:
        raise ValueError("R11-AE ready identity assurance is invalid")
    return {
        "schema": R11AE_READY_PREFLIGHT_SCHEMA,
        "profile": profile.to_json(),
        "draft_preflight_sha256": draft_preflight_sha256,
        "source_binding": {
            "source_id": source_id,
            "identity_assurance": identity_assurance,
            "continuity_verified": False,
            "route_serialized": False,
        },
        "preparation": {
            "pnp_enumerations": 1,
            "serial_ports_opened": 0,
            "device_commands": 0,
            "executor_state": "per_process_opt_in_authorized",
        },
        "execution_requirements": [
            "current PnP revalidation resolves exactly this opaque source id",
            "exclusive final evidence output is claimed before Qt or serial work",
            "visible executor and exact operator confirmation are both enabled",
            "one human-driven normal-product flow satisfies the draft profile",
        ],
    }


def validate_r11ae_ready_preflight(
    payload: object,
    profile: R11AEVisibleUiProfile,
) -> None:
    if not isinstance(payload, dict):
        raise TypeError("R11-AE ready preflight must be an object")
    binding = payload.get("source_binding")
    if not isinstance(binding, dict):
        raise TypeError("R11-AE ready preflight source binding is invalid")
    rebuilt = build_r11ae_ready_preflight(
        profile,
        draft_preflight_sha256=str(payload.get("draft_preflight_sha256", "")),
        source_id=str(binding.get("source_id", "")),
        identity_assurance=str(binding.get("identity_assurance", "")),
    )
    if payload != rebuilt:
        raise ValueError("R11-AE ready preflight is non-canonical")
    _validate_route_free(payload)


def validate_r11ae_display_preflight(
    payload: object,
    profile: R11AEVisibleUiProfile,
) -> None:
    """Validate an exact non-device DPI observation, never final evidence."""

    if not isinstance(payload, dict) or payload.get("schema") != R11AE_DISPLAY_PREFLIGHT_SCHEMA:
        raise ValueError("R11-AE display preflight schema is invalid")
    if payload.get("profile") != profile.to_json():
        raise ValueError("R11-AE display preflight profile differs from the selected scale")
    display = _dict(payload, "display")
    actions = _dict(payload, "non_device_actions")
    try:
        preflight = R11AEDisplayPreflight(
            profile=profile,
            observed_at_utc=str(payload["observed_at_utc"]),
            platform_name=str(display["platform_name"]),
            window_visible=display["window_visible"] is True,
            window_paint_events=_int(display["window_paint_events"]),
            device_pixel_ratio=_float(display["device_pixel_ratio"]),
            logical_dpi_x=_float(display["logical_dpi_x"]),
            logical_dpi_y=_float(display["logical_dpi_y"]),
            logical_width=_int(display["logical_width"]),
            logical_height=_int(display["logical_height"]),
            pnp_enumerations=_int(actions["pnp_enumerations"]),
            serial_ports_opened=_int(actions["serial_ports_opened"]),
            device_commands=_int(actions["device_commands"]),
            final_evidence_claims=_int(actions["final_evidence_claims"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("R11-AE display preflight fields are invalid") from error
    if payload != preflight.to_json():
        raise ValueError("R11-AE display preflight has unexpected or inconsistent fields")
    _validate_route_free(payload)


def require_r11ae_display_preflight_freshness(
    payload: object,
    profile: R11AEVisibleUiProfile,
    *,
    now_utc: datetime,
) -> None:
    """Require a matching display observation from the current UI session."""

    validate_r11ae_display_preflight(payload, profile)
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("R11-AE display preflight freshness time must include an offset")
    if not isinstance(payload, dict):  # Kept explicit for type narrowing after validation.
        raise TypeError("R11-AE display preflight must be an object")
    observed_at_utc = _parse_utc(str(payload["observed_at_utc"]))
    age_seconds = (now_utc - observed_at_utc).total_seconds()
    if not 0.0 <= age_seconds <= R11AE_DISPLAY_PREFLIGHT_MAX_AGE_SECONDS:
        raise ValueError("R11-AE display preflight is not fresh for final visible execution")


def validate_r11ae_evidence(payload: object, profile: R11AEVisibleUiProfile) -> None:
    """Reject non-canonical, route-bearing or semantically ambiguous evidence."""

    if not isinstance(payload, dict) or payload.get("schema") != R11AE_EVIDENCE_SCHEMA:
        raise ValueError("R11-AE evidence schema is invalid")
    if payload.get("profile") != profile.to_json():
        raise ValueError("R11-AE evidence profile differs from the selected scale cell")
    preflight = _dict(payload, "preflight")
    source = _dict(payload, "source")
    platform = _dict(payload, "platform")
    interaction = _dict(payload, "human_interaction")
    transitions = _dict(payload, "product_transitions")
    trace = _dict(payload, "trace")
    ui = _dict(payload, "ui_performance")
    actions = _dict(payload, "hardware_actions")
    shutdown = _dict(payload, "shutdown")
    try:
        evidence = R11AEVisibleUiEvidence(
            profile=profile,
            draft_preflight_sha256=str(preflight["draft_sha256"]),
            ready_preflight_sha256=str(preflight["ready_sha256"]),
            display_preflight_sha256=str(preflight["display_sha256"]),
            source_id=str(source["source_id"]),
            identity_assurance=str(source["identity_assurance"]),
            observed_at_utc=str(payload["observed_at_utc"]),
            platform_name=str(platform["name"]),
            window_visible=platform["window_visible"] is True,
            window_active=platform["window_active"] is True,
            device_pixel_ratio=_float(platform["device_pixel_ratio"]),
            logical_dpi_x=_float(platform["logical_dpi_x"]),
            logical_dpi_y=_float(platform["logical_dpi_y"]),
            logical_width=_int(platform["logical_width"]),
            logical_height=_int(platform["logical_height"]),
            mouse_button_presses=_int(interaction["mouse_button_presses"]),
            keyboard_key_presses=_int(interaction["keyboard_key_presses"]),
            maximum_resize_delta_logical_px=_int(interaction["maximum_resize_delta_logical_px"]),
            discover_actions=_int(transitions["discover"]),
            select_actions=_int(transitions["select"]),
            version_verify_actions=_int(transitions["version_verify"]),
            compose_actions=_int(transitions["compose"]),
            collect_actions=_int(transitions["collect_one_trace"]),
            point_count=_int(trace["point_count"]),
            finite_point_count=_int(trace["finite_point_count"]),
            unit=str(trace["unit"]),
            value_provenance=str(trace["value_provenance"]),
            calibration_provenance=str(trace["calibration_provenance"]),
            full_analytical_trace_retained=trace["full_analytical_trace_retained"] is True,
            presentation_width=_int(trace["presentation_width"]),
            presentation_point_count=_int(trace["presentation_point_count"]),
            peak_preserving_presentation=trace["peak_preserving_presentation"] is True,
            trace_elapsed_seconds=_float(trace["trace_elapsed_seconds"]),
            observed_points_per_second=_float(trace["observed_points_per_second"]),
            heartbeat_samples_while_busy=_int(ui["heartbeat_samples_while_busy"]),
            maximum_heartbeat_stall_ms=_float(ui["maximum_heartbeat_stall_ms"]),
            worker_to_gui_latency_ms=_float(ui["worker_to_gui_latency_ms"]),
            canvas_trace_paint_events=_int(ui["canvas_trace_paint_events"]),
            maximum_canvas_paint_ms=_float(ui["maximum_canvas_paint_ms"]),
            version_commands=_int(actions["version_commands"]),
            preflight_pnp_revalidations=_int(actions["preflight_pnp_revalidations"]),
            measurement_commands=_int(actions["measurement_commands"]),
            readback_commands=_int(actions["readback_commands"]),
            settings_writes=_int(actions["settings_writes"]),
            settings_apply_attempts=_int(actions["settings_apply_attempts"]),
            resets=_int(actions["resets"]),
            firmware_writes=_int(actions["firmware_writes"]),
            persistent_configuration_writes=_int(actions["persistent_configuration_writes"]),
            retries=_int(actions["retries"]),
            port_closed=shutdown["port_closed"] is True,
            shell_closed=shutdown["shell_closed"] is True,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("R11-AE evidence fields are invalid") from error
    if payload != evidence.to_json():
        raise ValueError("R11-AE evidence has unexpected or inconsistent fields")
    _validate_route_free(payload)


def write_r11ae_json(path: Path, payload: dict[str, object]) -> Path:
    """Write one new JSON artifact atomically; existing evidence is immutable."""

    destination = Path(path)
    if destination.suffix.casefold() != ".json":
        raise ValueError("R11-AE evidence path must use .json")
    part = destination.with_suffix(destination.suffix + ".part")
    if destination.exists() or part.exists():
        raise FileExistsError("R11-AE evidence output must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, destination)
    return destination


def claim_new_r11ae_evidence_output(path: Path) -> R11AEEvidenceOutputClaim:
    """Reserve one immutable final evidence destination before visible work."""

    destination = Path(path)
    if destination.suffix.casefold() != ".json":
        raise ValueError("R11-AE final evidence path must use .json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_suffix(destination.suffix + ".part")
    if part.exists():
        raise FileExistsError("R11-AE final evidence partial output already exists")
    claim = R11AEEvidenceOutputClaim(destination, secrets.token_hex(16))
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(claim.payload(), sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return claim


def verify_r11ae_evidence_output_claim(claim: R11AEEvidenceOutputClaim) -> None:
    """Refuse final publication if the pre-Qt/serial reservation changed."""

    if not isinstance(claim, R11AEEvidenceOutputClaim):
        raise TypeError("R11-AE final evidence claim is invalid")
    try:
        payload = json.loads(claim.destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("R11-AE final evidence claim is missing or invalid") from error
    if payload != claim.payload():
        raise RuntimeError("R11-AE final evidence claim changed before publication")


def publish_r11ae_evidence(
    claim: R11AEEvidenceOutputClaim,
    payload: dict[str, object],
    profile: R11AEVisibleUiProfile,
) -> None:
    """Atomically replace a verified final-output claim with canonical evidence."""

    validate_r11ae_evidence(payload, profile)
    verify_r11ae_evidence_output_claim(claim)
    destination = claim.destination
    part = destination.with_suffix(destination.suffix + ".part")
    if part.exists():
        raise FileExistsError("R11-AE final evidence partial output already exists")
    descriptor = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    verify_r11ae_evidence_output_claim(claim)
    os.replace(part, destination)


def _dict(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise TypeError(f"R11-AE evidence section {key} is invalid")
    return value


def _float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("R11-AE evidence numeric value is invalid")
    return float(value)


def _int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("R11-AE evidence integer value is invalid")
    return value


def _parse_utc(value: str) -> datetime:
    try:
        observed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("R11-AE observation timestamp is invalid") from error
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("R11-AE observation timestamp must include an offset")
    return observed


def r11ae_sha256(path: Path) -> str:
    digest = __import__("hashlib").sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.casefold())
    )


def _is_source_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("tinysa-")
        and len(value) == 23
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _validate_route_free(payload: object) -> None:
    rendered = json.dumps(payload, sort_keys=True).casefold()
    for token in (
        '"port"',
        '"route"',
        '"usb_serial_number"',
        '"values_dbm"',
        '"frequencies_hz"',
        '"raw_frame"',
        '"com1',
    ):
        if token in rendered:
            raise ValueError("R11-AE evidence contains a forbidden route or raw array")


__all__ = [
    "R11AE_ALLOWED_IDENTITY_ASSURANCE",
    "R11AE_ALLOWED_SCALE_PERCENT",
    "R11AE_CONFIRMATION",
    "R11AE_DISPLAY_PREFLIGHT_MAX_AGE_SECONDS",
    "R11AE_DISPLAY_PREFLIGHT_SCHEMA",
    "R11AE_EVIDENCE_OUTPUT_CLAIM_SCHEMA",
    "R11AE_EVIDENCE_SCHEMA",
    "R11AE_MIN_LOGICAL_HEIGHT",
    "R11AE_MIN_LOGICAL_WIDTH",
    "R11AE_PREFLIGHT_SCHEMA",
    "R11AE_READY_PREFLIGHT_SCHEMA",
    "R11AE_VISIBLE_EXECUTION_ENABLED",
    "R11AE_VISIBLE_EXECUTION_ENABLE_ENV",
    "R11AEDisplayPreflight",
    "R11AEVisibleUiEvidence",
    "R11AEVisibleUiProfile",
    "build_r11ae_preflight",
    "build_r11ae_ready_preflight",
    "claim_new_r11ae_evidence_output",
    "publish_r11ae_evidence",
    "r11ae_sha256",
    "r11ae_visible_execution_is_enabled",
    "require_r11ae_display_preflight_freshness",
    "validate_r11ae_display_preflight",
    "validate_r11ae_evidence",
    "validate_r11ae_preflight",
    "validate_r11ae_ready_preflight",
    "verify_r11ae_evidence_output_claim",
    "write_r11ae_json",
]
