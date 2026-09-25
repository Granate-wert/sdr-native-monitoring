"""Repeated visible UI V2 Stop probe with bounded libiio C-stderr brackets.

This is RX-only diagnostic evidence, not a latency benchmark: each C stdio
flush changes scheduling. One child owns the Pluto at a time. No import opens
a device, and no firewall or SDR configuration is changed outside the child.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

STAGE_PREFIX = "APP05_STAGE "
FLUSH_BEGIN_PREFIX = "APP05_CFLUSH_BEGIN "
FLUSH_RETURN_PREFIX = "APP05_CFLUSH_RETURN "
EXPECTED_READ_ERRORS = ("READ LINE: -9", "READ INTEGER: -9")
_BEFORE_STOP = ("measurement_started", "measurement_ended_before_stop", "stop_click_before")


def classify_ui_stderr(stderr: str) -> dict[str, object]:
    """Classify observed C text relative to explicit pre-click flushes.

    The last pre-click flush brackets previously buffered output. It cannot
    timestamp the libiio instruction that wrote a later-flushed line: even a
    post-click flush may release bytes written in the pre-click/click gap.
    """
    stage: str | None = None
    flush_library: str | None = None
    stages: list[str] = []
    flushes: list[dict[str, object]] = []
    read_errors: list[dict[str, object]] = []
    unexpected_errors: list[dict[str, object]] = []
    warnings: list[str] = []
    unclassified: list[str] = []
    for line in stderr.splitlines():
        if line.startswith(STAGE_PREFIX):
            if flush_library is not None:
                unexpected_errors.append(dict(line=line, stage=stage, reason="flush_without_return"))
                flush_library = None
            parts = line[len(STAGE_PREFIX):].split(" ", 1)
            if len(parts) != 2 or not parts[0].isdigit():
                unexpected_errors.append(dict(line=line, stage=stage, reason="malformed_stage"))
                continue
            stage = parts[1]
            stages.append(stage)
        elif line.startswith(FLUSH_BEGIN_PREFIX):
            parts = line[len(FLUSH_BEGIN_PREFIX):].rsplit(" ", 1)
            if len(parts) != 2 or parts[1] not in ("msvcrt", "ucrtbase") or parts[0] != stage:
                unexpected_errors.append(dict(line=line, stage=stage, reason="malformed_flush_begin"))
            elif flush_library is not None:
                unexpected_errors.append(dict(line=line, stage=stage, reason="nested_flush_begin"))
            else:
                flush_library = parts[1]
        elif line.startswith(FLUSH_RETURN_PREFIX):
            parts = line[len(FLUSH_RETURN_PREFIX):].rsplit(" ", 2)
            if (len(parts) != 3 or parts[0] != stage or parts[1] != flush_library
                    or not parts[2].lstrip("-").isdigit()):
                unexpected_errors.append(dict(line=line, stage=stage, reason="malformed_flush_return"))
            else:
                flushes.append(dict(stage=stage, library=parts[1], result=int(parts[2])))
            flush_library = None
        elif any(message in line for message in EXPECTED_READ_ERRORS):
            pre_click_flush = (stage in _BEFORE_STOP and
                               (stage != "stop_click_before" or flush_library is not None))
            read_errors.append(dict(line=line, stage=stage, library=flush_library,
                                    observed_by_preclick_flush=pre_click_flush,
                                    origin_after_click_proven=False))
        elif "ERROR:" in line:
            unexpected_errors.append(dict(line=line, stage=stage, reason="other_native_error"))
        elif "already deleted" in line or "RuntimeError" in line:
            warnings.append(line)
        elif line.strip():
            unclassified.append(line)
    if flush_library is not None:
        unexpected_errors.append(dict(line=None, stage=stage, reason="flush_without_return"))
    return dict(stages=stages, flushes=flushes, read_errors=read_errors,
                unexpected_errors=unexpected_errors, warnings=warnings,
                unclassified_stderr=unclassified)


def ui_run_complete(returncode: int, child: dict | None, diagnostic: dict[str, object]) -> bool:
    """Require actual RX/UI completion, exact flush coverage and no pre-Stop -9."""
    if returncode != 0 or child is None or child.get("result") != "pass":
        return False
    stages = diagnostic["stages"]
    required = (*_BEFORE_STOP, "stop_click_after", "stop_acknowledged", "qt_event_loop_returned",
                "main_returning")
    if not all(stages.count(stage) == 1 for stage in required):
        return False
    if [stages.index(stage) for stage in required] != sorted(stages.index(stage) for stage in required):
        return False
    flushes = diagnostic["flushes"]
    if (len(flushes) < 2 * len(required)
            or any(item["result"] != 0 for item in flushes)
            or any(not {"msvcrt", "ucrtbase"} <= {item["library"] for item in flushes
                                                    if item["stage"] == stage}
                   for stage in required)):
        return False
    return (not diagnostic["unexpected_errors"] and not diagnostic["warnings"]
            and not diagnostic["unclassified_stderr"]
            and not any(error["observed_by_preclick_flush"] for error in diagnostic["read_errors"])
            and not any(error["stage"] == "stop_click_before" and error["library"] is None
                        for error in diagnostic["read_errors"])
            and child.get("workers_after_close") == []
            and child.get("allocation_budget", {}).get("reserved_bytes") == 0)


def child_provenance_ok(child: dict | None, parent: dict, *, uri: str,
                        render_mode: str, center_mhz: float, sample_rate_msps: float,
                        fft: int, warmup_s: float, measurement_s: float,
                        expected_native_sha256: str | None) -> bool:
    """Reject stale, dirty or mixed-configuration child evidence."""
    if not isinstance(child, dict) or parent["tracked_dirty_paths"]:
        return False
    requested = child.get("requested")
    runtime = child.get("runtime")
    native = runtime.get("native_binary") if isinstance(runtime, dict) else None
    if not isinstance(requested, dict) or not isinstance(native, dict):
        return False
    native_sha = native.get("sha256")
    return bool(child.get("source_commit") == parent["source_commit"]
                and child.get("script_sha256") == parent["benchmark_sha256"]
                and child.get("tracked_dirty_paths") == []
                and child.get("uri") == uri
                and requested.get("c_stdio_bracket") is True
                and requested.get("render_mode") == render_mode
                and requested.get("center_mhz") == center_mhz
                and requested.get("sample_rate_msps") == sample_rate_msps
                and requested.get("fft") == fft
                and requested.get("warmup_s") == warmup_s
                and requested.get("measurement_s") == measurement_s
                and isinstance(native_sha, str) and len(native_sha) == 64
                and native_sha == expected_native_sha256)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--uri", required=True, help="explicit USB/IP Pluto endpoint")
    result.add_argument("--output", required=True, type=Path, help="new aggregate JSON path")
    result.add_argument("--repetitions", type=int, default=3)
    result.add_argument("--duration", type=float, default=4.0)
    result.add_argument("--warmup", type=float, default=1.0)
    result.add_argument("--center-mhz", type=float, default=2450.0)
    result.add_argument("--sample-rate-msps", type=float, default=3.0)
    result.add_argument("--fft", type=int, default=16384)
    result.add_argument("--render-mode", choices=("direct", "visual"), default="visual")
    return result


def main() -> int:
    args = parser().parse_args()
    if not args.uri.startswith(("usb:", "ip:")) or not 1 <= args.repetitions <= 10:
        raise SystemExit("an explicit usb:/ip: endpoint and 1..10 repetitions are required")
    output = args.output.resolve()
    if output.exists():
        raise SystemExit("aggregate output already exists")
    child_paths = [output.with_name(f"{output.stem}_run_{index + 1:02}.json")
                   for index in range(args.repetitions)]
    existing = [path for path in (output, *child_paths) if path.exists()]
    if existing:
        raise SystemExit(f"evidence output already exists: {existing[0]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    script = Path(__file__).resolve()
    checkout = script.parents[1]
    benchmark = checkout / "scripts" / "benchmark_app05_physical_ui.py"
    source = subprocess.run(["git", "rev-parse", "HEAD"], cwd=checkout, capture_output=True,
                            text=True, check=True).stdout.strip()
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                           cwd=checkout, capture_output=True, text=True, check=True).stdout.splitlines()
    report = dict(schema="app05-ui-stop-cstdio-v1", scope=__doc__, uri=args.uri,
                  source_commit=source, tracked_dirty_paths=[line[3:] for line in dirty],
                  probe_sha256=_sha256(script), benchmark_sha256=_sha256(benchmark),
                  requested=dict(repetitions=args.repetitions, duration_s=args.duration,
                                 warmup_s=args.warmup, center_mhz=args.center_mhz,
                                 sample_rate_msps=args.sample_rate_msps, fft=args.fft,
                                 render_mode=args.render_mode), runs=[], release_acceptance=False)
    native_sha256: str | None = None
    for index, child_path in enumerate(child_paths):
        command = [sys.executable, "-I", str(benchmark), "--uri", args.uri,
                   "--output", str(child_path), "--duration", str(args.duration),
                   "--warmup", str(args.warmup), "--center-mhz", str(args.center_mhz),
                   "--sample-rate-msps", str(args.sample_rate_msps), "--fft", str(args.fft),
                   "--render-mode", args.render_mode, "--teardown-timing", "--c-stdio-bracket"]
        try:
            child = subprocess.run(command, cwd=checkout, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=75)
            returncode, stdout, stderr = child.returncode, child.stdout, child.stderr
        except subprocess.TimeoutExpired as error:
            returncode = -1
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            stdout = stdout if isinstance(stdout, str) else stdout.decode("utf-8", errors="replace")
            stderr = stderr if isinstance(stderr, str) else stderr.decode("utf-8", errors="replace")
        try:
            child_report = json.loads(child_path.read_text(encoding="utf-8")) if child_path.exists() else None
        except (OSError, ValueError):
            child_report = None
        if not isinstance(child_report, dict):
            child_report = None
        diagnostic = classify_ui_stderr(stderr)
        runtime = None if child_report is None else child_report.get("runtime")
        native_binary = runtime.get("native_binary") if isinstance(runtime, dict) else None
        current_native_sha = native_binary.get("sha256") if isinstance(native_binary, dict) else None
        if native_sha256 is None:
            native_sha256 = current_native_sha
        provenance_ok = child_provenance_ok(
            child_report, report, uri=args.uri, render_mode=args.render_mode,
            center_mhz=args.center_mhz, sample_rate_msps=args.sample_rate_msps,
            fft=args.fft, warmup_s=args.warmup, measurement_s=args.duration,
            expected_native_sha256=native_sha256)
        complete = provenance_ok and ui_run_complete(returncode, child_report, diagnostic)
        report["runs"].append(dict(index=index + 1, complete=complete, provenance_ok=provenance_ok,
                                   returncode=returncode,
                                   child_path=str(child_path), child_result=None if child_report is None
                                   else child_report.get("result"), child_failure=None if child_report is None
                                   else child_report.get("failure"), child_source_commit=None
                                   if child_report is None else child_report.get("source_commit"),
                                   child_script_sha256=None if child_report is None
                                   else child_report.get("script_sha256"),
                                   child_native_binary=native_binary,
                                   stderr=stderr, stdout=stdout, **diagnostic))
        if not complete:
            break  # A failed or timed-out child must not start another hardware owner.
    report["complete"] = (len(report["runs"]) == args.repetitions
                          and all(run["complete"] for run in report["runs"]))
    report["release_acceptance"] = False  # A short, instrumented USB run is never a release gate.
    with output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps(dict(complete=report["complete"], output=str(output),
                          runs=[dict(index=run["index"], complete=run["complete"],
                                     read_errors=run["read_errors"], warnings=run["warnings"])
                                for run in report["runs"]]), ensure_ascii=False))
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
