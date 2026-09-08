#!/usr/bin/env python3
"""Fail a UI V2 review when the Git diff changes protected paths.

Usage:
    python tests/ui_v2/check_ui2_scope.py <base-revision>
"""
from __future__ import annotations

import subprocess
import sys

try:  # Direct script execution keeps only tests/ui_v2 on sys.path.
    from tests.ui_v2.ui2_scope import classify_paths
except ModuleNotFoundError:  # pragma: no cover - exercised by the CLI itself.
    from ui2_scope import classify_paths


def _git_paths(arguments: list[str]) -> tuple[str, ...]:
    result = subprocess.run(
        ["git", *arguments], check=True, text=True, capture_output=True
    )
    return tuple(line for line in result.stdout.splitlines() if line.strip())


def changed_files(base: str) -> tuple[str, ...]:
    """Include committed, staged, unstaged and untracked review content."""
    paths = set(_git_paths(["diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base}...HEAD"]))
    paths.update(_git_paths(["diff", "--name-only", "--diff-filter=ACMRTUXB"]))
    paths.update(_git_paths(["diff", "--cached", "--name-only", "--diff-filter=ACMRTUXB"]))
    paths.update(_git_paths(["ls-files", "--others", "--exclude-standard"]))
    return tuple(sorted(paths))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check_ui2_scope.py <base-revision>", file=sys.stderr)
        return 2
    try:
        changed = changed_files(argv[1])
    except subprocess.CalledProcessError as error:
        print(error.stderr.strip() or str(error), file=sys.stderr)
        return 2

    violations = classify_paths(changed)
    if violations:
        print("UI V2 scope violation:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation.path}: {violation.reason}", file=sys.stderr)
        return 1

    print(f"UI V2 scope OK: {len(changed)} changed file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
