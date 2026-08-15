"""Software/fake-only R11-W tinySA settings controller tests."""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from sdr_monitor.services.tinysa_sweep_policy import TinySaSweepAccuracy
from sdr_monitor.services.tinysa_sweep_settings_controller import (
    R11W_TINYSA_SETTINGS_CONFIRMATION,
    TinySaAttenuationMode,
    TinySaRbwMode,
    TinySaSettingsApplyError,
    TinySaSettingsApplyStatus,
    TinySaSettingsUnsupported,
    TinySaSpurPolicy,
    TinySaSweepPreset,
    TinySaSweepSettingsPlan,
    TinySaSwitchPolicy,
    apply_tinysa_sweep_settings,
    tinysa_sweep_preset_plan,
)


class _FakePort:
    def __init__(
        self,
        responses: list[bytes] | None = None,
        *,
        command_failure_at: int | None = None,
        close_failure: bool = False,
    ) -> None:
        self.responses = list(responses or [])
        self.command_failure_at = command_failure_at
        self.close_failure = close_failure
        self.calls: list[object] = []
        self.closed = False

    def command(self, command: bytes) -> bytes:
        self.calls.append(("command", command))
        if self.command_failure_at == len(self.calls):
            raise OSError("secret route failure")
        if self.responses:
            return self.responses.pop(0)
        return command + b"\nch> "

    def close(self) -> None:
        self.calls.append("close")
        if self.close_failure:
            raise OSError("secret close route")
        self.closed = True


class TinySaSweepSettingsControllerTests(unittest.TestCase):
    def test_preserve_preset_is_the_only_no_confirmation_no_transport_path(self) -> None:
        factory_calls = 0

        def factory() -> _FakePort:
            nonlocal factory_calls
            factory_calls += 1
            return _FakePort()

        result = apply_tinysa_sweep_settings(
            tinysa_sweep_preset_plan(TinySaSweepPreset.PRESERVE),
            port_factory=factory,
        )

        self.assertIs(result.status, TinySaSettingsApplyStatus.UNCHANGED)
        self.assertEqual(result.commands, ())
        self.assertEqual(factory_calls, 0)
        self.assertTrue(result.port_closed)

    def test_accuracy_presets_compile_to_one_explicit_runtime_command(self) -> None:
        expected = {
            TinySaSweepPreset.NORMAL: "sweep normal",
            TinySaSweepPreset.PRECISE: "sweep precise",
            TinySaSweepPreset.FAST: "sweep fast",
            TinySaSweepPreset.NOISE_SOURCE: "sweep noise",
        }
        for preset, command in expected.items():
            with self.subTest(preset=preset):
                plan = tinysa_sweep_preset_plan(preset)
                self.assertEqual(plan.commands, (command,))
                self.assertTrue(plan.warnings)

    def test_full_plan_has_deterministic_bounded_command_order(self) -> None:
        plan = TinySaSweepSettingsPlan(
            accuracy=TinySaSweepAccuracy.PRECISE,
            rbw_mode=TinySaRbwMode.MANUAL,
            rbw_hz=10_000,
            sweep_time_ms=1_250,
            spur_removal=TinySaSpurPolicy.AUTO,
            lna=TinySaSwitchPolicy.OFF,
            attenuation_mode=TinySaAttenuationMode.MANUAL,
            attenuation_db=12,
            repeat_count=4,
        )

        self.assertEqual(
            plan.commands,
            (
                "attenuate 12",
                "lna off",
                "spur auto",
                "rbw 10",
                "repeat 4",
                "sweeptime 1.25",
                "sweep precise",
            ),
        )
        self.assertNotIn("save", " ".join(plan.commands))
        self.assertNotIn("zero", " ".join(plan.commands))

    def test_manual_bounds_and_cross_field_invariants_fail_before_port(self) -> None:
        invalid = (
            {"rbw_mode": TinySaRbwMode.MANUAL, "rbw_hz": 199},
            {"rbw_mode": TinySaRbwMode.AUTO, "rbw_hz": 10_000},
            {"sweep_time_ms": 2},
            {"attenuation_mode": TinySaAttenuationMode.MANUAL, "attenuation_db": 32},
            {"attenuation_mode": TinySaAttenuationMode.AUTO, "attenuation_db": 0},
            {"repeat_count": 1_001},
            {"firmware_source_commit": "different"},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValueError):
                TinySaSweepSettingsPlan(**values)

    def test_nspeedup_and_wspeedup_are_visible_but_fail_closed(self) -> None:
        for values in ({"nspeedup": 4}, {"wspeedup": 4}):
            with self.subTest(values=values):
                plan = TinySaSweepSettingsPlan(**values)  # type: ignore[arg-type]
                with self.assertRaises(TinySaSettingsUnsupported):
                    _ = plan.commands

    def test_numeric_boundaries_compile_in_firmware_units(self) -> None:
        lower = TinySaSweepSettingsPlan(
            rbw_mode=TinySaRbwMode.MANUAL,
            rbw_hz=200,
            sweep_time_ms=3,
            attenuation_mode=TinySaAttenuationMode.MANUAL,
            attenuation_db=0,
            repeat_count=1,
        )
        upper = TinySaSweepSettingsPlan(
            rbw_mode=TinySaRbwMode.MANUAL,
            rbw_hz=850_000,
            sweep_time_ms=60_000,
            attenuation_mode=TinySaAttenuationMode.MANUAL,
            attenuation_db=31,
            repeat_count=1_000,
        )

        self.assertEqual(
            lower.commands,
            ("attenuate 0", "rbw 0.2", "repeat 1", "sweeptime 0.003"),
        )
        self.assertEqual(
            upper.commands,
            ("attenuate 31", "rbw 850", "repeat 1000", "sweeptime 60"),
        )

    def test_auto_and_switch_variants_are_explicit_not_implicit_defaults(self) -> None:
        self.assertEqual(
            TinySaSweepSettingsPlan(
                attenuation_mode=TinySaAttenuationMode.AUTO,
                lna=TinySaSwitchPolicy.ON,
                spur_removal=TinySaSpurPolicy.OFF,
                rbw_mode=TinySaRbwMode.AUTO,
            ).commands,
            ("attenuate auto", "lna on", "spur off", "rbw auto"),
        )
        self.assertEqual(TinySaSweepSettingsPlan().commands, ())

    def test_explicit_confirmation_applies_fake_commands_and_closes(self) -> None:
        plan = TinySaSweepSettingsPlan(
            accuracy=TinySaSweepAccuracy.FAST,
            rbw_mode=TinySaRbwMode.AUTO,
        )
        port = _FakePort()
        result = apply_tinysa_sweep_settings(
            plan,
            confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION,
            port_factory=lambda: port,
        )

        self.assertIs(result.status, TinySaSettingsApplyStatus.ACKNOWLEDGED_UNVERIFIED)
        self.assertEqual(result.commands, ("rbw auto", "sweep fast"))
        self.assertEqual(result.command_acknowledgements, 2)
        self.assertEqual(port.calls[-1], "close")
        self.assertTrue(port.closed)
        self.assertFalse(result.state_verified)
        self.assertFalse(result.previous_state_restored)
        self.assertEqual(result.readback_commands, 0)
        self.assertEqual(result.persistent_configuration_writes, 0)
        self.assertIn("fast_mode_may_raise_noise_about_10_db_and_reduce_level_accuracy", result.warnings)

    def test_missing_confirmation_does_not_invoke_factory(self) -> None:
        invoked = False

        def factory() -> _FakePort:
            nonlocal invoked
            invoked = True
            return _FakePort()

        with self.assertRaises(ValueError):
            apply_tinysa_sweep_settings(
                tinysa_sweep_preset_plan(TinySaSweepPreset.NORMAL),
                port_factory=factory,
            )
        self.assertFalse(invoked)

    def test_response_command_and_close_failures_are_redacted_and_close_attempted(self) -> None:
        plan = tinysa_sweep_preset_plan(TinySaSweepPreset.NORMAL)
        cases = (
            _FakePort([b"no prompt"]),
            _FakePort(command_failure_at=1),
            _FakePort(close_failure=True),
        )
        for port in cases:
            with self.subTest(port=port):
                def port_factory(result: _FakePort = port) -> _FakePort:
                    return result

                with self.assertRaisesRegex(TinySaSettingsApplyError, "failed closed") as captured:
                    apply_tinysa_sweep_settings(
                        plan,
                        confirmation=R11W_TINYSA_SETTINGS_CONFIRMATION,
                        port_factory=port_factory,
                    )
                self.assertNotIn("secret", str(captured.exception).casefold())
                self.assertEqual(port.calls[-1], "close")

    def test_module_has_no_serial_or_hardware_construction(self) -> None:
        tree = ast.parse(
            Path("sdr_monitor/services/tinysa_sweep_settings_controller.py").read_text(
                encoding="utf-8"
            )
        )
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        self.assertNotIn("serial", imports)
        source = ast.unparse(tree).casefold()
        for token in ("saveconfig", "firmware", "dfu", "reset"):
            self.assertNotIn(f'command("{token}', source)


if __name__ == "__main__":
    unittest.main()
