"""Check the real installed PyInstaller spec generator before expensive builds.

No freeze, executable, device or security action. A temporary generated spec
must parse and preserve console stdout plus the GUI-owned hide-early policy.
PyInstaller 6.22.0 regressed this exact option (upstream issue #9503).
"""

import argparse
import ast
import json
from pathlib import Path
from tempfile import TemporaryDirectory


def verify_freezer(entry: Path) -> dict[str, object]:
    import PyInstaller
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
    return {"pyinstaller_version": PyInstaller.__version__, "spec_policy": "console+hide-early",
            "scope": "generated spec syntax/options only; not bootloader or frozen runtime acceptance"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entry", type=Path, required=True)
    args = parser.parse_args()
    if not args.entry.is_file():
        parser.error("entry script must exist")
    try:
        report = verify_freezer(args.entry)
    except (SyntaxError, ValueError, ImportError) as error:
        parser.exit(1, f"Freezer policy preflight failed: {error}\n"
                       "Install the project's pinned dev PyInstaller; do not remove console policy.\n")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
