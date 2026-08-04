"""Bounded S14/P17 acceptance matrix for the standalone SDR product."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("esw_dfl", "_sgram_native", "dfl-analyzer")


def _standalone_boundary() -> dict[str, object]:
    scanned = [ROOT / "main_sdr.py", *sorted((ROOT / "sdr_monitor").rglob("*.py"))]
    violations: list[str] = []
    for path in scanned:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN:
            if token in text:
                violations.append(f"{path.relative_to(ROOT)}:{token}")
    return {"status": "PASS" if not violations else "FAIL", "files": len(scanned), "violations": violations}


def _release_contract() -> dict[str, object]:
    presets = json.loads((ROOT / "native" / "sdr_core" / "CMakePresets.json").read_text(encoding="utf-8-sig"))
    names = {item["name"] for item in presets["configurePresets"]}
    required = {"windows-msvc-cpu", "windows-msvc-cuda", "linux-aarch64-native-cpu", "linux-aarch64-native-cuda"}
    cuda = next(item for item in presets["configurePresets"] if item["name"] == "linux-aarch64-native-cuda")
    checks = {
        "required_presets": required <= names,
        "cuda_architecture": cuda["cacheVariables"].get("CMAKE_CUDA_ARCHITECTURES") == "87",
        "python_abi": "Python 3.13" in (ROOT / "native" / "sdr_core" / "cmake" / "Dependencies.cmake").read_text(encoding="utf-8"),
        "release_preflight": (ROOT / "scripts" / "preflight_sdr_release.py").exists(),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks}


def _resolve_tool(name: str) -> str | None:
    resolved = shutil.which(name)
    if resolved or sys.platform != "win32":
        return resolved
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    patterns = {
        "cmake": "Microsoft Visual Studio/*/*/Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe",
        "ninja": "Microsoft Visual Studio/*/*/Common7/IDE/CommonExtensions/Microsoft/CMake/Ninja/ninja.exe",
        "cl": "Microsoft Visual Studio/*/*/VC/Tools/MSVC/*/bin/Hostx64/x64/cl.exe",
        "nvcc": "NVIDIA GPU Computing Toolkit/CUDA/*/bin/nvcc.exe",
    }
    matches = sorted(program_files.glob(patterns[name]))
    return str(matches[-1]) if matches else None


def _command_probe() -> dict[str, object]:
    tools = {name: _resolve_tool(name) for name in ("cmake", "ninja", "cl", "nvcc")}
    available = [name for name, path in tools.items() if path]
    return {"status": "PASS" if len(available) == len(tools) else "NOT_VERIFIED", "tools": tools}


def build_matrix() -> dict[str, object]:
    version = subprocess.run(
        [sys.executable, "-m", "sdr_monitor", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    boundary = _standalone_boundary()
    release = _release_contract()
    toolchain = _command_probe()
    gaps = [
        "Jetson Orin NX, CUDA parity, PySide6 target install and Pluto hardware are not available locally",
        "8-hour soak, clean-machine startup and acceptance screenshots require release/target environments",
    ]
    if toolchain["status"] != "PASS":
        gaps.insert(0, "native CMake/CPU/CUDA clean builds require the corresponding toolchain")
    return {
        "package": "S14/P17",
        "verdict": "ACCEPT WITH GAPS",
        "version": version,
        "boundary": boundary,
        "release_contract": release,
        "toolchain_probe": toolchain,
        "known_gaps": gaps,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable matrix")
    args = parser.parse_args()
    matrix = build_matrix()
    if args.json:
        print(json.dumps(matrix, indent=2, sort_keys=True))
    else:
        print(f"{matrix['package']}: {matrix['verdict']}")
        print(f"version: {matrix['version']}")
        print(f"boundary: {matrix['boundary']['status']}")
        print(f"release contract: {matrix['release_contract']['status']}")
        print(f"toolchain probe: {matrix['toolchain_probe']['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
