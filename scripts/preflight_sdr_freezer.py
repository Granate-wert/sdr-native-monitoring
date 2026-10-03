"""Check the real installed PyInstaller spec generator before expensive builds.

No freeze, executable, device or security action. A temporary generated spec
must parse and preserve console stdout plus the GUI-owned hide-early policy.
PyInstaller 6.22.0 regressed this exact option (upstream issue #9503).
"""

import argparse
import ast
import json
import re
import sys
import tomllib
from pathlib import Path
from tempfile import TemporaryDirectory


def required_freezer_version() -> str:
    """Read the single canonical exact release pin, never a fallback version."""
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))
    section = project.get("project", {})
    if not isinstance(section, dict):
        raise ValueError("project metadata must be a table")
    optional = section.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        raise ValueError("project optional dependencies must be a table")
    dependencies = optional.get("dev", [])
    if not isinstance(dependencies, list) or not all(isinstance(item, str) for item in dependencies):
        raise ValueError("project dev dependencies must be a list of requirement strings")
    requirements = [item.strip() for item in dependencies if re.match(r"(?i)^pyinstaller\b", item.strip())]
    if len(requirements) != 1:
        raise ValueError("project dev dependencies must contain exactly one PyInstaller release pin")
    match = re.fullmatch(r"(?i)pyinstaller==([0-9]+\.[0-9]+\.[0-9]+)", requirements[0])
    if match is None:
        raise ValueError("project PyInstaller dependency must be one exact release pin")
    return match.group(1)


def verify_freezer_version() -> dict[str, object]:
    """Refuse toolchain drift before generating files or starting native builds."""
    required = required_freezer_version()
    import PyInstaller

    actual = PyInstaller.__version__
    if actual != required:
        raise ValueError(f"PyInstaller version mismatch: required {required}, observed {actual}")
    return {"pyinstaller_version": actual, "required_pyinstaller_version": required,
            "python_executable": sys.executable, "python_version": sys.version.split()[0],
            "pyinstaller_module": str(Path(PyInstaller.__file__).resolve(strict=True)),
            "pin_source": "pyproject.toml:project.optional-dependencies.dev"}


def verify_freezer(entry: Path) -> dict[str, object]:
    report = verify_freezer_version()
    from PyInstaller.building import makespec
    from PyInstaller.utils.cliutils.makespec import generate_parser

    with TemporaryDirectory(prefix="sdr-freezer-preflight-") as directory:
        # Use the generator's CLI defaults, including version-specific options.
        options = generate_parser().parse_args([
            "--onedir", "--name", "SDRFreezerPreflight", "--specpath", directory,
            "--hide-console", "hide-early", str(entry.resolve()),
        ])
        spec = makespec.main(options.scriptname, **vars(options))
        tree = ast.parse(Path(spec).read_text(encoding="utf-8"), filename="generated-freezer-spec")
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Name) and node.func.id == "EXE"]
    if len(calls) != 1:
        raise ValueError("expected exactly one generated EXE policy")
    options = {item.arg: item.value for item in calls[0].keywords}
    for name, expected in (("console", True), ("hide_console", "hide-early")):
        node = options.get(name)
        if not isinstance(node, ast.Constant) or type(node.value) is not type(expected) or node.value != expected:
            raise ValueError(f"generated {name} policy does not preserve {expected!r}")
    return {**report, "spec_policy": "console+hide-early",
            "scope": "exact tool version and generated spec syntax/options only; not bootloader or frozen runtime acceptance"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    args = parser.parse_args()
    if not args.entry.is_file():
        parser.error("entry script must exist")
    try:
        report = verify_freezer(args.entry)
    except (OSError, SyntaxError, ValueError, ImportError) as error:
        parser.exit(1, f"Freezer policy preflight failed: {error}\n"
                       "Install the project's pinned dev PyInstaller; do not remove console policy.\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
