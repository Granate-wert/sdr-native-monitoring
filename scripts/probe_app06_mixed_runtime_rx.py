"""Bounded HackRF -> Pluto UI V2 -> HackRF in one diagnostic Windows process.

Explicit RX required. Tests a staged shared-DLL candidate and sequential
cleanup, NOT common Analyzer selection, packaged runtime or release support.
No TX, amp/bias, system-DLL, firmware or firewall changes.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.preflight_sdr_native_build import _load_native_module, _read_manifest, validate_hackrf_factory
from scripts.preflight_sdr_shared_runtime import validate_shared_runtime
from scripts.probe_app06_hackrf_native_maxfs import counter_delta
from sdr_monitor.services.hackrf_activation_preflight import HackrfActivationPreflightService
from sdr_monitor.services.hackrf_capability_adapter import HackrfCapabilityAdapter
from sdr_monitor.services.hackrf_live_admission import HackrfLiveRequest, admit_hackrf_live
from sdr_monitor.services.hackrf_native_factory import HackrfNativeRuntimeFactory
from sdr_monitor.services.libhackrf_read_only import LibhackrfReadOnlyPort
from sdr_monitor.services.libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort


def loaded_libusb_witness() -> dict:
    """First-name Windows handle witness, not exclusion of duplicate basenames."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
    kernel.GetModuleFileNameW.restype = ctypes.c_uint32
    handle = kernel.GetModuleHandleW("libusb-1.0.dll")
    if not handle:
        raise RuntimeError("selected libusb is not loaded")
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel.GetModuleFileNameW(handle, buffer, len(buffer))
    if not length or length >= len(buffer):
        raise RuntimeError("loaded libusb path is unavailable or truncated")
    path = Path(buffer.value).resolve()
    return {
        "path": str(path),
        "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "scope": "First-name handle; file hash, not in-memory binary attestation or duplicate exclusion.",
    }


def exercise_hackrf(native, directory: Path, seconds: float, phase: int) -> dict:
    dll = directory / "hackrf.dll"
    observation = HackrfCapabilityAdapter(lambda: LibhackrfReadOnlyPort(dll, directory)).observe()
    request = HackrfLiveRequest(
        center_frequency_hz=100e6,
        sample_rate_hz=20e6,
        baseband_filter_hz=15_000_000,
        lna_gain_db=16,
        vga_gain_db=20,
        fft_size=4096,
        hop_size=2048,
        configuration_generation=phase,
        source_id=f"app06-mixed-hackrf-phase-{phase}",
    )
    admission = admit_hackrf_live(observation.snapshot, observation.calibration_identity, request)
    if admission.plan is None:
        raise RuntimeError("HackRF capability admission failed")
    preflight = HackrfActivationPreflightService(lambda: LibhackrfRuntimeIdentityPort(dll, directory)).verify(
        admission.plan
    )
    if preflight.permit is None:
        raise RuntimeError("HackRF current identity admission failed")
    control = HackrfNativeRuntimeFactory(lambda: native).create(preflight.permit)
    report = {
        "source_id": str(request.source_id),
        "configuration_generation": phase,
        "requested_api_accepted_fs_hz": 20e6,
        "identity_key": observation.snapshot.identity_key,
        "amp": False,
        "bias": False,
    }
    try:
        before = control.metrics()
        started = time.monotonic()
        frames = invalid = flagged = 0
        previous = None
        while time.monotonic() - started < seconds:
            for frame in control.poll_spectrum_frames(4):
                frames += 1
                valid = (
                    frame.source.source_id == str(request.source_id)
                    and frame.config_generation == phase
                    and frame.sample_rate_hz == 20e6
                    and frame.fft_size == 4096
                    and frame.hop_size == 2048
                    and frame.unit == native.SpectrumUnit.DBFS_BIN
                    and frame.precision_mode == native.PrecisionMode.REFERENCE_F64
                    and (previous is None or frame.frame_sequence > previous)
                )
                invalid += not valid
                previous = frame.frame_sequence
                flagged += bool(
                    frame.dropped_samples_before or frame.dropped_iq_blocks_before or frame.dropped_fft_frames_before
                )
            time.sleep(0.002)
        elapsed = time.monotonic() - started
        after = control.metrics()
        samples = counter_delta(before.source, after.source, "samples_admitted")
        report.update(
            elapsed_s=elapsed,
            ingress_msps=samples / elapsed / 1e6,
            analytical_fft_per_s=counter_delta(
                before.processing.dsp.dsp, after.processing.dsp.dsp, "fft_frames_computed"
            )
            / elapsed,
            reduced_frames_polled=frames,
            invalid_frame_contracts=invalid,
            frames_with_drop_flags=flagged,
            software_dropped_samples=counter_delta(before.source, after.source, "dropped_samples"),
            hardware_overrun_counter_available=after.source.device_overrun_counter_available,
            worker_failures=after.processing.worker_failures,
        )
    finally:
        shutdown = control.stop(5000)
        final = control.metrics()
        report.update(
            stop_complete=shutdown.complete(),
            worker_joined=final.processing.worker_joined,
            callbacks_active=final.source.callbacks_active,
            slots_in_use=final.source.slots_in_use,
            ready_depth=final.source.ready_depth,
            lifecycle_open=final.lifecycle_open,
        )
        report["passed"] = bool(
            report.get("reduced_frames_polled", 0) > 0
            and report.get("invalid_frame_contracts") == 0
            and report.get("worker_failures") == 0
            and report["stop_complete"]
            and report["worker_joined"]
            and not any(report[name] for name in ("callbacks_active", "slots_in_use", "ready_depth", "lifecycle_open"))
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--shared-directory", type=Path, required=True)
    parser.add_argument("--pluto-uri", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=15)
    parser.add_argument("--rx", action="store_true")
    args = parser.parse_args()
    if not args.rx or not math.isfinite(args.duration) or not 5 <= args.duration <= 30:
        parser.error("--rx and duration5..30 required")
    if os.name != "nt" or not args.pluto_uri.startswith(("usb:", "ip:")):
        parser.error("Windows and an explicit Pluto usb:/ip: URI required")
    output = args.output.resolve()
    pluto_output = output.with_name(output.stem + "-pluto.json")
    if output.exists() or pluto_output.exists():
        parser.error("new main and Pluto output paths required")
    if "sdr_monitor._sdr_native" in sys.modules or "_sdr_native" in sys.modules:
        raise RuntimeError("fresh process required")
    module, shared = args.module.resolve(), args.shared_directory.resolve()
    manifest = _read_manifest(args.manifest.resolve())
    static = validate_shared_runtime(module, manifest, shared, expected_cuda=False)
    if manifest.get("hackrf_official_compiled") is not True:
        raise RuntimeError("official HackRF staged module required")
    report = {
        "schema": "app06-mixed-runtime-rx-v1",
        "passed": False,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "tracked_dirty_paths": subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
        ).splitlines(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "native_manifest": manifest,
        "static_shared_runtime": static,
        "scope": "Short one-process sequential RX/DLL compatibility. Not common Analyzer routing, packaged closure, long stability, DWM, RF continuity/Pd or release acceptance.",
    }
    previous_libiio = os.environ.get("LIBIIO_DLL_PATH")
    try:
        os.environ["LIBIIO_DLL_PATH"] = str(shared / "libiio.dll")
        with os.add_dll_directory(str(shared)):
            preload = ctypes.CDLL(str(shared / "libusb-1.0.dll"))
            native = _load_native_module(module)
            sys.modules["sdr_monitor._sdr_native"] = native
            validate_hackrf_factory(native, manifest)
            report["loaded_before"] = loaded_libusb_witness()
            if Path(report["loaded_before"]["path"]) != shared / "libusb-1.0.dll":
                raise RuntimeError("first loaded libusb is not the explicit shared candidate")
            report["hackrf_before"] = exercise_hackrf(native, module.parent, args.duration, 1)
            if not report["hackrf_before"]["passed"]:
                raise RuntimeError("HackRF first phase did not close successfully")
            from scripts.benchmark_app05_physical_ui import main as pluto_main

            sys.argv = [
                "physical-v2-pluto",
                "--uri",
                args.pluto_uri,
                "--output",
                str(pluto_output),
                "--duration",
                str(args.duration),
                "--warmup",
                "3",
                "--sample-rate-msps",
                "61.44",
                "--fft",
                "4096",
                "--backend",
                "cpu",
                "--render-mode",
                "visual",
                "--hide-show",
                "--lock-vertical-range",
            ]
            with open(os.devnull, "w", encoding="utf-8") as sink:
                original_stdout = sys.stdout
                try:
                    sys.stdout = sink
                    code = pluto_main()
                finally:
                    sys.stdout = original_stdout
            pluto = json.loads(pluto_output.read_text(encoding="utf-8"))
            report["pluto"] = {
                "output": str(pluto_output),
                "result": pluto["result"],
                "applied": pluto["applied"],
                "hide_show": pluto["hide_show"]["passed"],
                "workers_after_close": pluto["workers_after_close"],
                "reserved_after_close": pluto["allocation_budget"]["reserved_bytes"],
            }
            if code != 0 or pluto["result"] != "pass":
                raise RuntimeError("Pluto physical V2 phase did not pass")
            report["hackrf_after"] = exercise_hackrf(native, module.parent, args.duration, 3)
            report["loaded_after"] = loaded_libusb_witness()
            report["passed"] = bool(
                report["hackrf_after"]["passed"]
                and report["loaded_before"] == report["loaded_after"]
                and preload is not None
            )
    except Exception as error:  # noqa: BLE001 - preserve failed diagnostic, never continue RX or report PASS.
        report["failure"] = str(error)
        report["failure_type"] = type(error).__name__
    finally:
        if previous_libiio is None:
            os.environ.pop("LIBIIO_DLL_PATH", None)
        else:
            os.environ["LIBIIO_DLL_PATH"] = previous_libiio
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps({name: report.get(name) for name in ("passed", "failure", "hackrf_before", "pluto", "hackrf_after")})
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
