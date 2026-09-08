"""Guard the V2 presentation surface against new hard-coded display strings."""

from __future__ import annotations

import ast
from pathlib import Path
import unittest


_DISPLAY_CONSTRUCTORS = frozenset(
    {
        "CommandField",
        "ErrorBanner",
        "PrimaryActionButton",
        "QCheckBox",
        "QLabel",
        "QPushButton",
        "SectionHeader",
        "StatusChipV2",
        "_secondary_label",
    }
)
_DISPLAY_METHODS = frozenset(
    {
        "addItem",
        "addRow",
        "drawText",
        "getOpenFileName",
        "question",
        "setAccessibleDescription",
        "setAccessibleName",
        "setFormat",
        "setPlaceholderText",
        "setPrefix",
        "setSuffix",
        "setText",
        "setToolTip",
        "set_content",
        "warning",
    }
)


class UiV2LocalizedSurfaceTests(unittest.TestCase):
    def test_display_strings_are_resolved_by_catalog_or_supplied_by_a_caller(self) -> None:
        root = Path(__file__).resolve().parents[2] / "sdr_monitor" / "ui" / "v2"
        violations: list[str] = []
        for path in sorted(root.rglob("*.py")):
            if path.name == "i18n.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                callee = _callee_name(node.func)
                if callee not in _DISPLAY_CONSTRUCTORS | _DISPLAY_METHODS:
                    continue
                arguments = node.args[:1] if callee == "addItem" else node.args
                for argument in arguments:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str) and argument.value.strip():
                        violations.append(f"{path.relative_to(root)}:{argument.lineno}: {callee}({argument.value!r})")
        self.assertEqual(violations, [], "hard-coded UI V2 display strings must use i18n.text():\n" + "\n".join(violations))


def _callee_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


if __name__ == "__main__":
    unittest.main()
