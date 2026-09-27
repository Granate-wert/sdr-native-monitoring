"""Bounded physical RX at 20 MS/s through the issued official-native permit.

No TX, amplifier/bias, raw-I/Q transfer into Python, UI, security or firmware
change. Reports requested/API-accepted Fs, NOT independent ADC rate readback.
This one-device staging test cannot claim common Analyzer or release support.
"""

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.preflight_sdr_native_build import (
    _load_native_module,
    _read_manifest,
    load_contract_expectations,
    validate_contract_surface,
    validate_hackrf_factory,
    validate_manifest,
)
from sdr_monitor.services.hackrf_activation_preflight import HackrfActivationPreflightService
from sdr_monitor.services.hackrf_capability_adapter import HackrfCapabilityAdapter
from sdr_monitor.services.hackrf_live_admission import HackrfLiveRequest, admit_hackrf_live
from sdr_monitor.services.hackrf_native_factory import (
    HackrfNativeFactoryError,
    HackrfNativeFactoryFailure,
    HackrfNativeRuntimeFactory,
)
from sdr_monitor.services.libhackrf_read_only import LibhackrfReadOnlyPort
from sdr_monitor.services.libhackrf_runtime_identity import LibhackrfRuntimeIdentityPort


def counter_delta(before, after, name):
    first, last = getattr(before, name), getattr(after, name)
    if type(first) is not int or type(last) is not int or last < first:
        raise ValueError("counter unavailable or regressed: " + name)
    return last - first


def identity_negative_gate(native, permit):
    """Wrong expected identity must fail at open, before native RF/start stages.

    Changes only the diagnostic native call, never the issued permit. Any
    unexpectedly returned owner is stopped before failing this gate.
    """
    observed = {"calls": 0, "rejected_at_open": False}

    def wrong_expectation(**values):
        words = values["expected_serial_words"]
        values["expected_serial_words"] = (*words[:3], words[3] ^ 1)
        observed["calls"] += 1
        try:
            return native.create_hackrf_runtime_dsp_control(**values)
        except native.DeviceError as error:
            observed["rejected_at_open"] = str(error) == "HackRF RX stage failed: open_exactly_one (status -30004)"
            raise

    diagnostic = SimpleNamespace(
        HACKRF_FACTORY_CONTRACT_VERSION=native.HACKRF_FACTORY_CONTRACT_VERSION,
        WindowType=native.WindowType,
        DetectorType=native.DetectorType,
        create_hackrf_runtime_dsp_control=wrong_expectation,
    )
    try:
        unexpected = HackrfNativeRuntimeFactory(lambda: diagnostic).create(permit)
    except HackrfNativeFactoryError as error:
        if error.failure is not HackrfNativeFactoryFailure.ACTIVATION_FAILED or observed != {
            "calls": 1,
            "rejected_at_open": True,
        }:
            raise RuntimeError("wrong-identity call did not fail at the identity-open gate") from None
    else:
        unexpected.stop(5000)
        raise RuntimeError("wrong-identity factory unexpectedly returned an RX owner")
    return {
        "passed": True,
        "rejected_at_open": True,
        "native_calls": 1,
        "scope": "Deliberately wrong native expectation rejected before RF configuration/start; not a real device-swap simulation.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--rx", action="store_true", help="Explicit physical RX, always required")
    parser.add_argument("--fft-size", type=int, choices=(4096, 16384), default=4096)
    parser.add_argument("--persistence-bins", type=int, choices=(0, 64, 256), default=0,
                        help="0 disables native persistence; 64/256 enable exponential density")
    parser.add_argument("--latest-bridge", action="store_true",
                        help="Poll density then one bounded latest spectrum every nominal 4 ms")
    parser.add_argument(
        "--identity-negative",
        action="store_true",
        help="Before RX, require a wrong handle identity to fail at native open",
    )
    args = parser.parse_args()
    if not args.rx or not math.isfinite(args.seconds) or not 5 <= args.seconds <= 120:
        parser.error("--rx and seconds5..120 required")
    output = args.output.resolve()
    if output.exists():
        parser.error("a new output file is required")
    module_path = args.module.resolve()
    manifest = _read_manifest(args.manifest.resolve())
    validate_manifest(module_path, manifest, expected_cuda=False)
    if manifest.get("hackrf_official_compiled") is not True:
        parser.error("only an official-HackRF staged module is admitted")
    if "sdr_monitor._sdr_native" in sys.modules or "_sdr_native" in sys.modules:
        raise RuntimeError("native module already loaded; fresh isolated process required")
    native = _load_native_module(module_path)
    sys.modules["sdr_monitor._sdr_native"] = native
    validate_hackrf_factory(native, manifest)
    validate_contract_surface(native, load_contract_expectations(ROOT / "esw_dfl/sdr/contracts.py"))
    runtime = module_path.parent
    dll = runtime / "hackrf.dll"
    observation = HackrfCapabilityAdapter(lambda: LibhackrfReadOnlyPort(dll, runtime)).observe()
    request = HackrfLiveRequest(
        center_frequency_hz=100e6,
        sample_rate_hz=20e6,
        baseband_filter_hz=15_000_000,
        lna_gain_db=16,
        vga_gain_db=20,
        fft_size=args.fft_size,
        hop_size=args.fft_size // 2,
        source_id="app06-hackrf-native-maxfs",
        persistence_enabled=args.persistence_bins != 0,
        persistence_mode="exponential-decay" if args.persistence_bins else "disabled",
        persistence_power_bins=args.persistence_bins or 256,
    )
    admission = admit_hackrf_live(observation.snapshot, observation.calibration_identity, request)
    if admission.plan is None:
        raise RuntimeError("capability admission rejected")
    preflight = HackrfActivationPreflightService(lambda: LibhackrfRuntimeIdentityPort(dll, runtime)).verify(
        admission.plan
    )
    if preflight.permit is None:
        raise RuntimeError("current physical identity admission rejected")
    report = {
        "schema": "app06-hackrf-native-maxfs-v3",
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "native_manifest": manifest,
        "native_module": str(module_path),
        "request": {
            "sample_rate_hz": 20e6,
            "center_hz": 100e6,
            "rf_filter_hz": 15e6,
            "fft": request.fft_size,
            "hop": request.hop_size,
            "persistence_power_bins": args.persistence_bins,
            "latest_bridge": args.latest_bridge,
            "lna_db": 16,
            "vga_db": 20,
            "amplifier": False,
            "bias": False,
        },
        "device_family": observation.snapshot.family.value,
        "identity_key": observation.snapshot.identity_key,
        "scope": "One physical native RX/DSP staging test. Passed means contracts/lifecycle, not loss-free I/Q. Requested/API-accepted Fs, not independent Fs readback; no common UI, RF calibration, DWM, duty or release acceptance.",
    }
    control = None
    try:
        if args.identity_negative:
            report["identity_negative"] = identity_negative_gate(native, preflight.permit)
            # The deliberate failure consumes its permit. Obtain a new current
            # observation; do not mutate or reuse the consumed one for RX.
            preflight = HackrfActivationPreflightService(lambda: LibhackrfRuntimeIdentityPort(dll, runtime)).verify(
                admission.plan
            )
            if preflight.permit is None:
                raise RuntimeError("identity re-observation after negative gate rejected")
        control = HackrfNativeRuntimeFactory(lambda: native).create(preflight.permit)
        warmup_end = time.monotonic() + 3
        while time.monotonic() < warmup_end:
            if args.latest_bridge:
                if request.persistence_enabled:
                    control.poll_persistence_snapshots(2)
                control.drain_latest_spectrum_frame()
            else:
                control.poll_spectrum_frames(4)
            time.sleep(0.004 if args.latest_bridge else 0.002)
        before = control.metrics()
        started = time.monotonic()
        previous = None
        frames_seen = invalid = flagged = coalesced = density_snapshots = 0
        while time.monotonic() - started < args.seconds:
            if args.latest_bridge:
                if request.persistence_enabled:
                    density_snapshots += len(control.poll_persistence_snapshots(2))
                drained = control.drain_latest_spectrum_frame()
                coalesced += drained.coalesced_frames
                frames = () if drained.frame is None else (drained.frame,)
            else:
                frames = control.poll_spectrum_frames(4)
            for frame in frames:
                frames_seen += 1
                valid = (
                    frame.fft_size == request.fft_size
                    and frame.hop_size == request.hop_size
                    and frame.unit == native.SpectrumUnit.DBFS_BIN
                    and frame.precision_mode == native.PrecisionMode.REFERENCE_F64
                    and frame.config_generation == request.configuration_generation
                    and frame.source.source_id == str(request.source_id)
                    and frame.sample_rate_hz == request.sample_rate_hz
                    and (previous is None or frame.frame_sequence > previous)
                )
                invalid += not valid
                previous = frame.frame_sequence
                flagged += bool(
                    frame.dropped_samples_before or frame.dropped_iq_blocks_before or frame.dropped_fft_frames_before
                )
            time.sleep(0.004 if args.latest_bridge else 0.002)
        elapsed = time.monotonic() - started
        after = control.metrics()
        samples = counter_delta(before.source, after.source, "samples_admitted")
        fft = counter_delta(before.processing.dsp.dsp, after.processing.dsp.dsp, "fft_frames_computed")
        report.update(
            elapsed_s=elapsed,
            samples_admitted=samples,
            ingress_msps=samples / elapsed / 1e6,
            analytical_fft_per_s=fft / elapsed,
            reduced_frames_polled=frames_seen,
            reduced_frames_coalesced=coalesced,
            persistence_snapshots_polled=density_snapshots,
            persistence_updates=counter_delta(before.processing.dsp, after.processing.dsp,
                                              "persistence_updates"),
            invalid_frame_contracts=invalid,
            frames_with_drop_flags=flagged,
            ingress_software_dropped_samples=counter_delta(before.source, after.source, "dropped_samples"),
            ingress_queue_full_drops=counter_delta(before.source, after.source, "queue_full_drops"),
            ingress_pool_exhaustion_drops=counter_delta(before.source, after.source,
                                                        "pool_exhaustion_drops"),
            ingress_lock_contention_drops=counter_delta(before.source, after.source,
                                                        "lock_contention_drops"),
            ingress_ready_high_water=after.source.ready_high_water,
            ingress_ready_capacity=after.source.ready_capacity,
            iq_blocks_processed=counter_delta(before.processing.dsp, after.processing.dsp,
                                               "iq_blocks_processed"),
            source_blocks_missing=counter_delta(before.processing.dsp, after.processing.dsp,
                                                "source_blocks_missing"),
            source_samples_missing=counter_delta(before.processing.dsp, after.processing.dsp,
                                                 "source_samples_missing"),
            device_overrun_counter_available=after.source.device_overrun_counter_available,
            worker_failures=after.processing.worker_failures,
        )
    finally:
        if control is not None:
            stop_started = time.monotonic()
            shutdown = control.stop(5000)
            final = control.metrics()
            report.update(
                stop_ms=(time.monotonic() - stop_started) * 1000,
                stop_complete=shutdown.complete(),
                callbacks_active=final.source.callbacks_active,
                slots_in_use=final.source.slots_in_use,
                ready_depth=final.source.ready_depth,
                worker_joined=final.processing.worker_joined,
                lifecycle_open=final.lifecycle_open,
            )
            report["passed"] = bool(
                report.get("samples_admitted", 0) > 0
                and report.get("reduced_frames_polled", 0) > 0
                and report.get("invalid_frame_contracts") == 0
                and report.get("worker_failures") == 0
                and report["stop_complete"]
                and report["worker_joined"]
                and not any(
                    (
                        report["callbacks_active"],
                        report["slots_in_use"],
                        report["ready_depth"],
                        report["lifecycle_open"],
                    )
                )
            )
        report.setdefault("passed", False)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                name: report.get(name)
                for name in ("passed", "ingress_msps", "analytical_fft_per_s", "reduced_frames_polled",
                             "ingress_software_dropped_samples", "stop_ms")
            }
        )
    )
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
