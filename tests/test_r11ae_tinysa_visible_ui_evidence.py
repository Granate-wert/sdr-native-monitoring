"""Qt-free R11-AE visible tinySA UI evidence-contract tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stderr
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.r11ae_tinysa_visible_ui import main as runner_main
from sdr_monitor.r11ae_tinysa_visible_ui_evidence import (
    R11AE_ALLOWED_SCALE_PERCENT,
    R11AE_VISIBLE_EXECUTION_ENABLE_ENV,
    R11AE_VISIBLE_EXECUTION_ENABLED,
    R11AEDisplayPreflight,
    R11AEVisibleUiEvidence,
    R11AEVisibleUiProfile,
    build_r11ae_preflight,
    build_r11ae_ready_preflight,
    claim_new_r11ae_evidence_output,
    publish_r11ae_evidence,
    r11ae_sha256,
    require_r11ae_display_preflight_freshness,
    validate_r11ae_display_preflight,
    validate_r11ae_evidence,
    validate_r11ae_preflight,
    validate_r11ae_ready_preflight,
    write_r11ae_json,
)

ROOT = Path(__file__).resolve().parents[1]


def _evidence(profile: R11AEVisibleUiProfile) -> R11AEVisibleUiEvidence:
    elapsed = 20.0
    return R11AEVisibleUiEvidence(
        profile=profile,
        draft_preflight_sha256="a" * 64,
        ready_preflight_sha256="b" * 64,
        display_preflight_sha256="c" * 64,
        source_id="tinysa-0123456789abcdef",
        identity_assurance="usb_serial",
        observed_at_utc="2026-08-15T12:00:00+00:00",
        platform_name="windows",
        window_visible=True,
        window_active=True,
        device_pixel_ratio=profile.expected_device_pixel_ratio,
        logical_dpi_x=96.0,
        logical_dpi_y=96.0,
        logical_width=profile.width,
        logical_height=profile.height,
        mouse_button_presses=3,
        keyboard_key_presses=4,
        maximum_resize_delta_logical_px=48,
        discover_actions=1,
        select_actions=1,
        version_verify_actions=1,
        compose_actions=1,
        collect_actions=1,
        point_count=10_001,
        finite_point_count=10_001,
        unit="dBm",
        value_provenance="device_reported_trace",
        calibration_provenance="device_reported_builtin",
        full_analytical_trace_retained=True,
        presentation_width=1_024,
        presentation_point_count=2_048,
        peak_preserving_presentation=True,
        trace_elapsed_seconds=elapsed,
        observed_points_per_second=10_001 / elapsed,
        heartbeat_samples_while_busy=200,
        maximum_heartbeat_stall_ms=40.0,
        worker_to_gui_latency_ms=5.0,
        canvas_trace_paint_events=2,
        maximum_canvas_paint_ms=8.0,
        version_commands=1,
        preflight_pnp_revalidations=1,
        measurement_commands=1,
        readback_commands=1,
        settings_writes=0,
        settings_apply_attempts=0,
        resets=0,
        firmware_writes=0,
        persistent_configuration_writes=0,
        retries=0,
        port_closed=True,
        shell_closed=True,
    )


class R11AEVisibleUiEvidenceTests(unittest.TestCase):
    def test_exact_four_scale_matrix_and_product_bounds(self) -> None:
        self.assertEqual(R11AE_ALLOWED_SCALE_PERCENT, {100, 150, 200, 300})
        for scale in sorted(R11AE_ALLOWED_SCALE_PERCENT):
            with self.subTest(scale=scale):
                profile = R11AEVisibleUiProfile(scale)
                self.assertEqual(profile.point_count, 10_001)
                self.assertEqual(profile.frame_bytes, 30_005)
                self.assertLess(profile.frame_bytes, 30 * 1_024)
                validate_r11ae_preflight(build_r11ae_preflight(profile), profile)
                validate_r11ae_preflight(
                    build_r11ae_preflight(profile, visible_execution_enabled=True), profile
                )
        with self.assertRaises(ValueError):
            R11AEVisibleUiProfile(125)
        with self.assertRaises(ValueError):
            R11AEVisibleUiProfile(100, point_count=10_002)

    def test_canonical_scalar_evidence_separates_ui_and_trace_metrics(self) -> None:
        profile = R11AEVisibleUiProfile(150)
        payload = _evidence(profile).to_json()

        validate_r11ae_evidence(payload, profile)

        rendered = json.dumps(payload, sort_keys=True).casefold()
        self.assertTrue(payload["metric_semantics"]["not_lps"])  # type: ignore[index]
        self.assertTrue(payload["metric_semantics"]["not_continuous_trace_rate"])  # type: ignore[index]
        for forbidden in ("values_dbm", "frequencies_hz", '"port"', '"route"', "com31"):
            self.assertNotIn(forbidden, rendered)

    def test_ready_preflight_binds_draft_to_one_opaque_source(self) -> None:
        profile = R11AEVisibleUiProfile(100)
        payload = build_r11ae_ready_preflight(
            profile,
            draft_preflight_sha256="a" * 64,
            source_id="tinysa-0123456789abcdef",
            identity_assurance="usb_location",
        )

        validate_r11ae_ready_preflight(payload, profile)
        rendered = json.dumps(payload, sort_keys=True).casefold()
        self.assertIn("per_process_opt_in_authorized", rendered)
        self.assertNotIn("com", payload["source_binding"]["source_id"])  # type: ignore[index]
        with self.assertRaises(ValueError):
            build_r11ae_ready_preflight(
                profile,
                draft_preflight_sha256="a" * 64,
                source_id="tinysa-0123456789abcdef",
                identity_assurance="unknown",
            )
        legacy = dict(payload)
        legacy["schema"] = "sdr-native-r11ae-tinysa-visible-ui-ready-v1"
        legacy["preparation"] = {
            "pnp_enumerations": 1,
            "serial_ports_opened": 0,
            "device_commands": 0,
            "executor_state": "disabled_software_review",
        }
        with self.assertRaisesRegex(ValueError, "non-canonical"):
            validate_r11ae_ready_preflight(legacy, profile)

    def test_display_preflight_proves_only_a_matching_painted_windows_surface(self) -> None:
        profile = R11AEVisibleUiProfile(150)
        display = R11AEDisplayPreflight(
            profile=profile,
            observed_at_utc="2026-08-15T12:00:00+00:00",
            platform_name="windows",
            window_visible=True,
            window_paint_events=1,
            device_pixel_ratio=profile.expected_device_pixel_ratio,
            logical_dpi_x=144.0,
            logical_dpi_y=144.0,
            logical_width=profile.width,
            logical_height=profile.height,
        )
        payload = display.to_json()
        validate_r11ae_display_preflight(payload, profile)
        self.assertEqual(payload["non_device_actions"], {
            "pnp_enumerations": 0,
            "serial_ports_opened": 0,
            "device_commands": 0,
            "final_evidence_claims": 0,
        })
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            R11AEDisplayPreflight(
                profile=profile,
                observed_at_utc="2026-08-15T12:00:00+00:00",
                platform_name="windows",
                window_visible=True,
                window_paint_events=1,
                device_pixel_ratio=profile.expected_device_pixel_ratio,
                logical_dpi_x=144.0,
                logical_dpi_y=144.0,
                logical_width=profile.width - 1,
                logical_height=profile.height,
            )
        require_r11ae_display_preflight_freshness(
            payload,
            profile,
            now_utc=datetime(2026, 8, 15, 12, 10, tzinfo=UTC),
        )
        with self.assertRaisesRegex(ValueError, "not fresh"):
            require_r11ae_display_preflight_freshness(
                payload,
                profile,
                now_utc=datetime(2026, 8, 15, 12, 16, tzinfo=UTC),
            )

    def test_threshold_settings_and_unexpected_fields_fail_closed(self) -> None:
        profile = R11AEVisibleUiProfile(200)
        with self.assertRaisesRegex(ValueError, "heartbeat"):
            replace(
                _evidence(profile),
                maximum_heartbeat_stall_ms=profile.maximum_heartbeat_stall_ms + 0.1,
            )
        with self.assertRaisesRegex(ValueError, "forbids settings"):
            replace(_evidence(profile), settings_writes=1)
        payload = _evidence(profile).to_json()
        payload["lps"] = 1.0
        with self.assertRaisesRegex(ValueError, "unexpected"):
            validate_r11ae_evidence(payload, profile)

    def test_evidence_rejects_each_visible_cell_admission_violation(self) -> None:
        profile = R11AEVisibleUiProfile(200)
        invalid_cases = (
            ("DPI", {"device_pixel_ratio": 1.0}, "pixel ratio"),
            ("geometry", {"logical_width": 1_023}, "matrix minimum"),
            ("interaction", {"keyboard_key_presses": 1}, "mouse and keyboard"),
            ("transition", {"collect_actions": 2}, "exactly one"),
            ("finite trace", {"finite_point_count": 10_000}, "complete finite"),
            ("trace deadline", {"trace_elapsed_seconds": 120.1}, "trace exceeded"),
            ("trace paint", {"canvas_trace_paint_events": 0}, "visibly paint"),
            ("settings attempt", {"settings_apply_attempts": 1}, "forbids settings"),
            ("clean close", {"shell_closed": False}, "clean serial and shell close"),
        )
        for label, updates, message in invalid_cases:
            with self.subTest(label=label), self.assertRaisesRegex(ValueError, message):
                replace(_evidence(profile), **updates)

    def test_preflight_is_write_once_and_execution_guard_precedes_work(self) -> None:
        self.assertFalse(R11AE_VISIBLE_EXECUTION_ENABLED)
        with tempfile.TemporaryDirectory() as raw:
            output = Path(raw) / "preflight.json"
            payload = build_r11ae_preflight(R11AEVisibleUiProfile(300))
            write_r11ae_json(output, payload)
            with self.assertRaises(FileExistsError):
                write_r11ae_json(output, payload)
        with patch(
            "scripts.r11ae_tinysa_visible_ui.build_r11ae_preflight"
        ) as build:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                runner_main(
                    [
                        "--output",
                        "must-not-exist.json",
                        "--scale-percent",
                        "100",
                        "--execute",
                    ]
                )
            build.assert_not_called()
        self.assertFalse((ROOT / "must-not-exist.json").exists())

        with patch(
            "scripts.r11ae_tinysa_visible_ui.build_r11ae_preflight"
        ) as build:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                runner_main(
                    [
                        "--output",
                        "must-not-exist-ready.json",
                        "--scale-percent",
                        "100",
                        "--prepare-ready",
                    ]
                )
            build.assert_not_called()
        self.assertFalse((ROOT / "must-not-exist-ready.json").exists())

        with (
            patch.dict("os.environ", {R11AE_VISIBLE_EXECUTION_ENABLE_ENV: "1"}),
            patch("scripts.r11ae_tinysa_visible_ui.build_r11ae_preflight") as build,
        ):
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                runner_main(
                    [
                        "--output",
                        "must-not-exist-confirmation.json",
                        "--scale-percent",
                        "100",
                        "--prepare-execution-draft",
                    ]
                )
            build.assert_not_called()
        self.assertFalse((ROOT / "must-not-exist-confirmation.json").exists())

        with patch("scripts.r11ae_tinysa_visible_ui._probe_display") as probe:
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                runner_main(
                    [
                        "--output",
                        "must-not-probe-display.json",
                        "--scale-percent",
                        "100",
                        "--probe-display",
                    ]
                )
            probe.assert_not_called()
        self.assertFalse((ROOT / "must-not-probe-display.json").exists())

        with (
            patch.dict("os.environ", {R11AE_VISIBLE_EXECUTION_ENABLE_ENV: "1"}),
            patch("scripts.r11ae_tinysa_visible_ui.claim_new_r11ae_evidence_output") as claim,
            self.assertRaisesRegex(ValueError, "draft, ready and fresh display"),
        ):
            runner_main(
                [
                    "--output",
                    "must-not-claim-without-display-preflight.json",
                    "--scale-percent",
                    "100",
                    "--execute",
                    "--confirm-visible",
                    "I CONFIRM R11-AE VISIBLE TINYSA UI",
                ]
            )
        claim.assert_not_called()
        self.assertFalse((ROOT / "must-not-claim-without-display-preflight.json").exists())

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            draft = root / "historical-false-draft.json"
            ready = root / "historical-false-ready.json"
            display = root / "fresh-display-preflight.json"
            output = root / "must-not-be-claimed.json"
            profile = R11AEVisibleUiProfile(100)
            write_r11ae_json(draft, build_r11ae_preflight(profile))
            write_r11ae_json(
                ready,
                build_r11ae_ready_preflight(
                    profile,
                    draft_preflight_sha256=r11ae_sha256(draft),
                    source_id="tinysa-0123456789abcdef",
                    identity_assurance="usb_serial",
                ),
            )
            write_r11ae_json(
                display,
                R11AEDisplayPreflight(
                    profile=profile,
                    observed_at_utc=datetime.now(UTC).isoformat(),
                    platform_name="windows",
                    window_visible=True,
                    window_paint_events=1,
                    device_pixel_ratio=profile.expected_device_pixel_ratio,
                    logical_dpi_x=96.0,
                    logical_dpi_y=96.0,
                    logical_width=profile.width,
                    logical_height=profile.height,
                ).to_json(),
            )
            with (
                patch.dict("os.environ", {R11AE_VISIBLE_EXECUTION_ENABLE_ENV: "1"}),
                patch("scripts.r11ae_tinysa_visible_ui.claim_new_r11ae_evidence_output") as claim,
                self.assertRaisesRegex(ValueError, "requires an enabled execution draft"),
            ):
                runner_main(
                    [
                        "--output",
                        str(output),
                        "--scale-percent",
                        "100",
                        "--execute",
                        "--draft-preflight",
                        str(draft),
                        "--ready-preflight",
                        str(ready),
                        "--display-preflight",
                        str(display),
                        "--confirm-visible",
                        "I CONFIRM R11-AE VISIBLE TINYSA UI",
                    ]
                )
            claim.assert_not_called()
            self.assertFalse(output.exists())

    def test_final_evidence_replaces_only_a_verified_exclusive_claim(self) -> None:
        profile = R11AEVisibleUiProfile(300)
        with tempfile.TemporaryDirectory() as raw:
            destination = Path(raw) / "visible-evidence.json"
            claim = claim_new_r11ae_evidence_output(destination)
            publish_r11ae_evidence(claim, _evidence(profile).to_json(), profile)
            payload = json.loads(destination.read_text(encoding="utf-8"))
            validate_r11ae_evidence(payload, profile)
            self.assertEqual(payload["status"], "OBSERVED_VISIBLE_WITH_LIMITATIONS")

    def test_contract_has_no_qt_serial_or_dfl_dependency_and_pyserial_is_declared(self) -> None:
        source = (
            ROOT / "sdr_monitor/r11ae_tinysa_visible_ui_evidence.py"
        ).read_text(encoding="utf-8").casefold()
        for forbidden in ("pyside", "serial(", "from serial", "esw_dfl"):
            self.assertNotIn(forbidden, source)
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8").casefold()
        self.assertIn('"pyserial>=3.5"', pyproject)
        display = (ROOT / "sdr_monitor/ui/r11ae_tinysa_display_preflight.py").read_text(
            encoding="utf-8"
        ).casefold()
        for forbidden in ("from serial", "discover(", "tinyserialsourcebackend", "claim_new"):
            self.assertNotIn(forbidden, display)

    def test_python313_software_gate_stays_offscreen_and_disables_discovery(self) -> None:
        gate = (ROOT / "scripts/run_r11ae_tinysa_software_gates.ps1").read_text(
            encoding="utf-8"
        )
        for required in (
            "declared Python 3.13 environment",
            "$env:SDR_AUTO_DISCOVER = '0'",
            "$env:QT_QPA_PLATFORM = 'offscreen'",
            "tests.test_r11ae_tinysa_offscreen_ui",
            "verify_sdr_frozen_tinysa_runtime.py",
            "visible execution remains disabled",
        ):
            self.assertIn(required, gate)


if __name__ == "__main__":
    unittest.main()
