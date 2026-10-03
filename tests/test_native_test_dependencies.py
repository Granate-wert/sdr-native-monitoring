"""Dependency scope mechanics only; fake file, no native import or physical RX."""

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from tests.native_test_dependencies import explicit_native_dependencies


class NativeTestDependencyScopeTests(unittest.TestCase):
    def test_exact_parent_remains_registered_through_case_and_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "explicit.pyd"
            module.touch()
            handle = MagicMock()
            events = []
            handle.__enter__.side_effect = lambda: events.append("open")
            handle.__exit__.side_effect = lambda *args: events.append("close")

            @explicit_native_dependencies
            def case(path, value):
                self.assertEqual(events, ["open"])
                self.assertEqual(path, str(module))
                events.extend(("case", "cleanup"))
                return value

            register = MagicMock(return_value=handle)
            with patch("tests.native_test_dependencies.os", SimpleNamespace(
                name="nt", add_dll_directory=register,
            )):
                self.assertEqual(case(str(module), 7), 7)
            register.assert_called_once_with(str(module.parent.resolve()))
            self.assertEqual(events, ["open", "case", "cleanup", "close"])

    def test_failure_closes_scope_and_preserves_original_error(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "explicit.pyd"
            module.touch()
            handle = MagicMock()
            original = RuntimeError("original native failure")

            @explicit_native_dependencies
            def case(path):
                raise original

            with patch("tests.native_test_dependencies.os", SimpleNamespace(
                name="nt", add_dll_directory=MagicMock(return_value=handle),
            )), self.assertRaises(RuntimeError) as failure:
                case(str(module))
            self.assertIs(failure.exception, original)
            handle.__exit__.assert_called_once()

    def test_missing_or_directory_module_refuses_before_case_or_search(self):
        with tempfile.TemporaryDirectory() as directory:
            body = MagicMock()
            case = explicit_native_dependencies(body)
            with patch("tests.native_test_dependencies.os.add_dll_directory", create=True) as register:
                with self.assertRaises(FileNotFoundError):
                    case(str(Path(directory) / "absent.pyd"))
                with self.assertRaises(ValueError):
                    case(directory)
            register.assert_not_called()
            body.assert_not_called()

    def test_non_windows_does_not_mutate_path_or_register_dll_search(self):
        with tempfile.TemporaryDirectory() as directory:
            module = Path(directory) / "explicit.so"
            module.touch()
            before = dict(os.environ)
            body = MagicMock(return_value=9)
            register = MagicMock()
            with patch("tests.native_test_dependencies.os", SimpleNamespace(
                name="posix", add_dll_directory=register,
            )):
                self.assertEqual(explicit_native_dependencies(body)(str(module)), 9)
            register.assert_not_called()
            self.assertEqual(dict(os.environ), before)
