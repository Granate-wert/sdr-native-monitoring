"""UI2-03 entry-point selection stays pure until the selected shell is built."""

from __future__ import annotations

import unittest

from sdr_monitor import main as main_module
from sdr_monitor.main import _requested_ui_mode, _resolve_ui_mode


class _FakeApplication:
    instance_value: "_FakeApplication | None" = None

    def __init__(self, _arguments: object) -> None:
        self.organization_name = ""
        self.organization_domain = ""
        self.application_name = ""
        self.exec_calls = 0

    @classmethod
    def instance(cls) -> "_FakeApplication | None":
        return cls.instance_value

    @staticmethod
    def primaryScreen():
        """No physical display in the entry-point double (APP-00 sizing seam)."""
        return None

    def setOrganizationName(self, value: str) -> None:  # noqa: N802 - Qt API spelling.
        self.organization_name = value

    def setOrganizationDomain(self, value: str) -> None:  # noqa: N802 - Qt API spelling.
        self.organization_domain = value

    def setApplicationName(self, value: str) -> None:  # noqa: N802 - Qt API spelling.
        self.application_name = value

    def exec(self) -> int:
        self.exec_calls += 1
        return 0


class _FakeShell:
    def __init__(self) -> None:
        self.show_calls = 0

    def show(self) -> None:
        self.show_calls += 1


class V2LaunchSelectionTests(unittest.TestCase):
    """V2 is the product default; standalone remains a deliberate rollback only."""

    def test_v2_is_selected_by_default_and_for_its_explicit_mode(self) -> None:
        self.assertEqual(_requested_ui_mode({}), "v2")
        self.assertEqual(_resolve_ui_mode(""), "v2")
        self.assertEqual(_resolve_ui_mode("v2"), "v2")
        self.assertEqual(_resolve_ui_mode(" V2 "), "v2")
        self.assertEqual(_resolve_ui_mode("unexpected"), "v2")

    def test_documented_flag_wins_and_compatibility_alias_remains_presentation_only(self) -> None:
        self.assertEqual(_requested_ui_mode({"SDR_UI_VERSION": "v2", "SDR_UI_MODE": "standalone"}), "v2")
        self.assertEqual(_requested_ui_mode({"SDR_UI_MODE": "standalone"}), "standalone")

    def test_standalone_and_legacy_are_explicit_rollbacks(self) -> None:
        self.assertEqual(_resolve_ui_mode("standalone"), "standalone")
        self.assertEqual(_resolve_ui_mode("legacy"), "standalone")

    def test_main_builds_v2_by_default_without_constructing_the_standalone_shell(self) -> None:
        from unittest.mock import patch

        app = _FakeApplication([])
        shell = _FakeShell()
        _FakeApplication.instance_value = app
        try:
            with patch.dict("os.environ", {"SDR_UI_VERSION": "", "SDR_UI_MODE": ""}), patch(
                "PySide6.QtWidgets.QApplication", _FakeApplication
            ), patch("sdr_monitor.main._build_v2_shell", return_value=shell) as build_v2:
                self.assertEqual(main_module.main([]), 0)
        finally:
            _FakeApplication.instance_value = None
        build_v2.assert_called_once_with()
        self.assertEqual(shell.show_calls, 1)
        self.assertEqual(app.exec_calls, 1)


if __name__ == "__main__":
    unittest.main()
