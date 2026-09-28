"""Bounded process-exit probe for the Pluto/libiio native ownership boundary.

This is diagnostic hardware evidence, not a release gate or an RF benchmark.
Each child owns one device/engine and exits before the next child starts.
Markers and libiio stderr share the same captured pipe; their ordering is
observable, but marker flushing and process startup perturb all timings.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import importlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path


STAGE_PREFIX = "APP05_NATIVE_STAGE "
RESULT_PREFIX = "APP05_NATIVE_RESULT "
READ_ERRORS = ("READ LINE: -9", "READ INTEGER: -9")
MODES = ("probe", "configured", "stream")
_DLL_DIRECTORY_HANDLES: list[object] = []


def _load_native(checkout: Path, native_module: Path | None,
                 libiio_dll: Path | None) -> object:
    """Use one exact frozen native/DLL sibling pair when explicitly requested."""
    sys.path.insert(0, str(checkout))
    if (native_module is None) != (libiio_dll is None):
        raise ValueError("--native-module and --libiio-dll must be supplied together")
    if native_module is not None and libiio_dll is not None:
        module = native_module.resolve(strict=True)
        library = libiio_dll.resolve(strict=True)
        if (checkout not in module.parents or checkout not in library.parents
                or module.parent != library.parent
                or not module.name.startswith("_sdr_native.") or module.suffix.lower() != ".pyd"
                or library.name.lower() != "libiio.dll"):
            raise ValueError("frozen native module and libiio must be exact checkout-local siblings")
        os.environ["LIBIIO_DLL_PATH"] = str(library)
        _DLL_DIRECTORY_HANDLES.append(os.add_dll_directory(str(module.parent)))
        import sdr_monitor
        sdr_monitor.__path__.insert(0, str(module.parent))
    native = importlib.import_module("sdr_monitor._sdr_native")
    if native_module is not None and Path(native.__file__).resolve(strict=True) != module:
        raise RuntimeError("loaded native module is not the explicitly selected frozen sibling")
    if libiio_dll is not None:
        runtime = native.pluto_runtime_info()
        if (not runtime.available or
                Path(runtime.library_path).resolve(strict=True) != library):
            raise RuntimeError("loaded libiio is not the explicitly selected frozen sibling")
    return native


def classify_stderr(stderr: str) -> dict:
    """Retain the last flushed child stage before each diagnostic line."""
    stage = None
    stages = []
    errors = []
    for line in stderr.splitlines():
        if line.startswith(STAGE_PREFIX):
            parts = line[len(STAGE_PREFIX):].split(" ", 1)
            if len(parts) == 2 and parts[0].isdigit():
                stage = parts[1]
                stages.append(dict(at_ns=int(parts[0]), stage=stage))
            else:
                errors.append(dict(line=line, after_stage=stage, malformed_marker=True))
        elif any(message in line for message in READ_ERRORS):
            errors.append(dict(line=line, after_stage=stage))
    return dict(stages=stages, read_errors=errors)


def child_complete(mode: str, returncode: int, result: dict | None,
                   stages: list[str]) -> bool:
    """Require an ordered lifecycle and clean native state, not just exit 0."""
    if returncode != 0 or result is None or result.get("error") is not None:
        return False
    if "cleanup_error" in result or result.get("connected_after_disconnect") is not False:
        return False
    required = {
        "probe": ("device_created", "device_disconnect_return", "child_return"),
        "configured": ("engine_configured", "engine_disconnect_return", "child_return"),
        "stream": ("engine_started", "request_stop_return", "join_return"),
    }[mode]
    cycles = result.get("stream_cycles", 1) if mode == "stream" else 1
    if type(cycles) is not int or not 1 <= cycles <= 2:
        return False
    if mode == "stream":
        ordered = list(required)
        for _ in range(1, cycles):
            ordered.extend(("engine_reconfigured", *required))
        ordered.extend(("engine_disconnect_return", "child_return"))
    else:
        ordered = required
    cursor = 0
    for stage in ordered:
        try:
            cursor = stages.index(stage, cursor) + 1
        except ValueError:
            return False
    if mode != "stream":
        return True
    cycle_results = result.get("cycles")
    if not isinstance(cycle_results, list) or len(cycle_results) != cycles:
        return False
    if any(
        not isinstance(row, dict)
        or row.get("state_after_join") != "EngineState.STOPPED"
        or row.get("streaming_after_join") is not False
        or row.get("native_has_error") is not False
        or row.get("diagnostic_events_lost") != 0
        or type(row.get("rx_blocks")) is not int or row["rx_blocks"] <= 0
        or type(row.get("rx_samples")) is not int or row["rx_samples"] <= 0
        or "acquisition_failure" in row.get("native_event_codes", [])
        for row in cycle_results
    ):
        return False
    return (result.get("state_after_join") == "EngineState.STOPPED"
            and result.get("streaming_after_join") is False
            and result.get("native_has_error") is False
            and result.get("diagnostic_events_lost") == 0
            and isinstance(result.get("rx_blocks"), int)
            and result["rx_blocks"] > 0
            and isinstance(result.get("rx_samples"), int)
            and result["rx_samples"] > 0
            and "acquisition_failure" not in result.get("native_event_codes", []))


def _mark(stage: str) -> None:
    print(f"{STAGE_PREFIX}{time.perf_counter_ns()} {stage}", file=sys.stderr, flush=True)


def _flush_c_stdio(stage: str) -> dict[str, int]:
    """Flush both common Windows C runtimes, bracketing each call in stderr."""
    results = {}
    for name in ("msvcrt", "ucrtbase"):
        _mark(f"{stage}_flush_{name}_begin")
        library = ctypes.CDLL(name)
        flush = library.fflush
        flush.argtypes = (ctypes.c_void_p,)
        flush.restype = ctypes.c_int
        results[name] = int(flush(None))
        _mark(f"{stage}_flush_{name}_return")
    return results


def _child(args: argparse.Namespace, checkout: Path) -> int:
    native = _load_native(checkout, args.native_module, args.libiio_dll)

    engine = device = None
    disconnected = False
    result: dict[str, object] = dict(mode=args.mode, uri=args.uri, error=None)
    _mark("child_begin")

    def checked_applied(applied: object) -> dict[str, int | float]:
        sample_rate = float(applied.sample_rate_hz)
        bandwidth = float(applied.analog_bandwidth_hz)
        center = float(applied.center_frequency_hz)
        if (not math.isclose(sample_rate, args.sample_rate_msps * 1e6,
                             rel_tol=0.0, abs_tol=1.0)
                or not math.isclose(bandwidth, args.rf_bandwidth_mhz * 1e6,
                                    rel_tol=0.0, abs_tol=1.0)
                or not math.isclose(center, 2.45e9, rel_tol=0.0, abs_tol=1.0)):
            raise RuntimeError("Pluto applied center/Fs/RF bandwidth differs from requested values")
        return dict(sample_rate_hz=sample_rate, analog_bandwidth_hz=bandwidth,
                    center_frequency_hz=center,
                    config_generation=int(applied.config_generation))

    def flush_at(stage: str) -> None:
        if args.flush_c_stdio:
            _mark(stage + "_flush_begin")
            result.setdefault("c_stdio_flushes", []).append(
                dict(stage=stage, results=_flush_c_stdio(stage)))
            _mark(stage + "_flush_return")

    try:
        if args.mode == "probe":
            device = native.PlutoDevice(args.uri, 3000)
            _mark("device_created")
            probe = device.probe()
            result["context_name"] = probe.context_name
            device.disconnect()
            disconnected = True
            _mark("device_disconnect_return")
            result["connected_after_disconnect"] = bool(device.connected)
        else:
            from sdr_monitor.domain import BackendKind, LiveConfiguration
            from sdr_monitor.services.native_live import build_native_fixed_band_config

            live = LiveConfiguration(
                center_hz=2.45e9,
                sample_rate_hz=args.sample_rate_msps * 1e6,
                analog_bandwidth_hz=args.rf_bandwidth_mhz * 1e6,
                fft_size=args.fft, backend=BackendKind.CPU,
                persistence_enabled=False, persistence_mode="disabled",
                snapshot_rate_hz=30.0,
            )
            config = build_native_fixed_band_config(
                native, live, args.uri, source_id="app05-native-teardown")
            engine = native.PlutoFixedBandEngine(args.uri, 3000)
            _mark("engine_created")
            applied = checked_applied(engine.configure(config))
            _mark("engine_configured")
            result["applied"] = applied
            if args.mode == "stream":
                result["stream_cycles"] = args.stream_cycles
                result["cycles"] = []
                for cycle_index in range(args.stream_cycles):
                    if cycle_index:
                        applied = checked_applied(engine.configure(config))
                        _mark("engine_reconfigured")
                    engine.start()
                    _mark("engine_started")
                    flush_at("start")
                    time.sleep(args.run_seconds)
                    _mark("request_stop_begin")
                    engine.request_stop()
                    _mark("request_stop_return")
                    flush_at("request_stop")
                    _mark("join_begin")
                    engine.join()
                    _mark("join_return")
                    flush_at("join")
                    metrics = engine.metrics()
                    cycle_result = dict(
                        cycle=cycle_index + 1,
                        applied=applied,
                        state_after_join=str(engine.state()),
                        streaming_after_join=bool(engine.streaming),
                        expected_cancellations=int(metrics.expected_cancellations),
                        native_has_error=bool(metrics.has_error),
                        diagnostic_events_lost=int(metrics.diagnostic_events_lost),
                        rx_blocks=int(metrics.engine.iq_blocks_received),
                        rx_samples=int(metrics.engine.iq_samples_received),
                        native_event_codes=[event.code for event in engine.poll_events()],
                    )
                    result["cycles"].append(cycle_result)
                    result.update(cycle_result)
            engine.disconnect()
            disconnected = True
            _mark("engine_disconnect_return")
            flush_at("disconnect")
            result["connected_after_disconnect"] = bool(engine.connected)
    except BaseException as error:
        result["error"] = f"{type(error).__name__}: {error}"
        _mark("child_error")
    finally:
        if not disconnected:
            try:
                if engine is not None:
                    engine.disconnect()
                if device is not None:
                    device.disconnect()
            except BaseException as error:
                result["cleanup_error"] = f"{type(error).__name__}: {error}"
            _mark("fallback_disconnect_return")
        engine = device = None
        gc.collect()
        _mark("gc_return")
        flush_at("gc")
        time.sleep(args.hold_seconds)
        _mark("hold_return")
        flush_at("hold")
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True), flush=True)
    _mark("child_return")
    return 0 if result["error"] is None and "cleanup_error" not in result else 1


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(checkout: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=checkout, check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout.strip()


def _parent(args: argparse.Namespace, checkout: Path, script: Path) -> int:
    if args.output is None or args.output.exists():
        raise ValueError("--output must be a new file")
    native = _load_native(checkout, args.native_module, args.libiio_dll)

    runtime = native.pluto_runtime_info()
    if not runtime.available:
        raise RuntimeError(f"libiio runtime unavailable: {runtime.error}")
    library = Path(runtime.library_path).resolve(strict=True)
    if args.libiio_dll is not None and library != args.libiio_dll:
        raise RuntimeError("loaded libiio is not the explicitly selected frozen sibling")
    report = dict(
        scope=__doc__, source_commit=_git(checkout, "rev-parse", "HEAD"),
        tracked_dirty_paths=_git(checkout, "diff", "--name-only", "HEAD").splitlines(),
        script_sha256=_sha256(script), native_module=str(Path(native.__file__).resolve()),
        native_sha256=_sha256(Path(native.__file__)), libiio_library=str(library),
        libiio_sha256=_sha256(library),
        libiio_version=f"{runtime.major}.{runtime.minor} {runtime.git_tag}",
        uri=args.uri, modes=list(args.modes), repetitions=args.repetitions,
        run_seconds=args.run_seconds, hold_seconds=args.hold_seconds,
        stream_cycles=args.stream_cycles,
        sample_rate_msps=args.sample_rate_msps,
        rf_bandwidth_mhz=args.rf_bandwidth_mhz,
        fft=args.fft,
        flush_c_stdio=args.flush_c_stdio,
        results=[], release_acceptance=False,
    )
    for mode in args.modes:
        for repetition in range(args.repetitions):
            command = [
                sys.executable, "-I", str(script), "--child", "--checkout",
                str(checkout), "--uri", args.uri, "--mode", mode,
                "--run-seconds", str(args.run_seconds),
                "--hold-seconds", str(args.hold_seconds),
                "--stream-cycles", str(args.stream_cycles),
                "--sample-rate-msps", str(args.sample_rate_msps),
                "--rf-bandwidth-mhz", str(args.rf_bandwidth_mhz),
                "--fft", str(args.fft),
            ]
            if args.native_module is not None:
                command.extend(("--native-module", str(args.native_module),
                                "--libiio-dll", str(args.libiio_dll)))
            if args.flush_c_stdio:
                command.append("--flush-c-stdio")
            started = time.perf_counter_ns()
            child = subprocess.run(
                command, cwd=checkout, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=35,
            )
            diagnostic = classify_stderr(child.stderr)
            result_lines = [line[len(RESULT_PREFIX):] for line in child.stdout.splitlines()
                            if line.startswith(RESULT_PREFIX)]
            child_result = json.loads(result_lines[-1]) if len(result_lines) == 1 else None
            stages = [row["stage"] for row in diagnostic["stages"]]
            complete = child_complete(mode, child.returncode, child_result, stages)
            report["results"].append(dict(
                mode=mode, repetition=repetition + 1, returncode=child.returncode,
                elapsed_ms=(time.perf_counter_ns() - started) / 1e6,
                complete=complete, child=child_result, **diagnostic,
                stdout_without_result=[line for line in child.stdout.splitlines()
                                       if not line.startswith(RESULT_PREFIX)],
                stderr=child.stderr,
            ))
    report["all_children_complete"] = all(row["complete"] for row in report["results"])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2, ensure_ascii=False)
    print(json.dumps(dict(
        output=str(args.output), source_commit=report["source_commit"],
        all_children_complete=report["all_children_complete"],
        outcomes=[dict(mode=row["mode"], repetition=row["repetition"],
                       complete=row["complete"], read_errors=row["read_errors"])
                  for row in report["results"]],
    ), ensure_ascii=False))
    return 0 if report["all_children_complete"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    parser.add_argument("--uri", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--modes", nargs="+", choices=MODES, default=list(MODES))
    parser.add_argument("--mode", choices=MODES)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--run-seconds", type=float, default=1.0)
    parser.add_argument("--hold-seconds", type=float, default=1.0)
    parser.add_argument("--sample-rate-msps", type=float, default=3.0)
    parser.add_argument("--rf-bandwidth-mhz", type=float, default=3.0)
    parser.add_argument("--fft", type=int, default=16384)
    parser.add_argument("--stream-cycles", type=int, default=1,
                        help="one or two explicit Start/Stop cycles on the same native engine")
    parser.add_argument("--native-module", type=Path,
                        help="exact frozen _sdr_native sibling inside this checkout")
    parser.add_argument("--libiio-dll", type=Path,
                        help="exact sibling libiio.dll from the same frozen package")
    parser.add_argument("--flush-c-stdio", action="store_true",
                        help="diagnostic fflush(NULL) via msvcrt and ucrtbase at lifecycle boundaries")
    parser.add_argument("--child", action="store_true")
    args = parser.parse_args(argv)
    if not sys.flags.isolated:
        parser.error("Python -I required")
    if not args.uri.startswith(("usb:", "ip:")):
        parser.error("an explicit usb: or ip: URI is required")
    if not 1 <= args.repetitions <= 6 or not 0.25 <= args.run_seconds <= 5:
        parser.error("repetitions must be 1..6 and RX duration 0.25..5 seconds")
    if not 0 <= args.hold_seconds <= 5:
        parser.error("post-disconnect hold must be 0..5 seconds")
    if (not 1 <= args.sample_rate_msps <= 61.44
            or not 0.2 <= args.rf_bandwidth_mhz <= 56
            or args.fft < 256 or args.fft > 262144
            or args.fft & (args.fft - 1)):
        parser.error("invalid bounded Pluto sample rate, RF bandwidth or FFT")
    if not 1 <= args.stream_cycles <= 2:
        parser.error("stream cycles must be 1..2")
    if args.stream_cycles > 1 and (args.mode not in (None, "stream")
                                    or (args.mode is None and args.modes != ["stream"])):
        parser.error("multiple stream cycles require stream-only mode")
    if (args.native_module is None) != (args.libiio_dll is None):
        parser.error("--native-module and --libiio-dll must be supplied together")
    checkout = args.checkout.resolve(strict=True)
    if args.native_module is not None:
        args.native_module = args.native_module.resolve(strict=True)
        args.libiio_dll = args.libiio_dll.resolve(strict=True)
    script = Path(__file__).resolve(strict=True)
    if checkout not in script.parents:
        parser.error("script must be inside the selected checkout")
    if args.child:
        if args.mode is None or args.output is not None:
            parser.error("child requires --mode and cannot write --output")
        return _child(args, checkout)
    if args.output is None or args.mode is not None:
        parser.error("parent requires --output and cannot select a child --mode")
    return _parent(args, checkout, script)


if __name__ == "__main__":
    raise SystemExit(main())
