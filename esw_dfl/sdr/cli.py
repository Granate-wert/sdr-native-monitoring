"""Headless diagnostics for SDR discovery and P07 fixed-band acquisition."""

from __future__ import annotations

import argparse
import json
import math
import re
import signal
import sys
import time
from threading import Event
from collections.abc import Sequence

import numpy as np

from .contracts import (
    ComputeBackendKind,
    DetectorType,
    DeviceConfig,
    DspConfig,
    GainMode,
    PrecisionMode,
    QualityFlag,
    SampleFormat,
    SpectrumUnit,
    SweepConfig,
    WindowType,
)
from .fixed_band import FixedBandEngineService, FixedBandOptions
from .pluto import discover_pluto
from .sweep import SweepExecutionStatus, SweepExecutor, SweepPlannerOptions, plan_sweep
from .stitching import SweepStitchError, SweepStitchOptions, stitch_sweep
from .recording import (
    SpectrumReplay,
    estimate_storage,
    recover_iq_recording,
    recover_spectrum_recording,
)

_SERIAL_FIELD = re.compile(
    r"(?:\s*[,;]\s*|\s+)?(?:serial(?:\s+number)?|s/n|sn)\s*[:=]\s*[^,;)\]]*",
    flags=re.IGNORECASE,
)


def _sanitize_device_description(description: str) -> str:
    """Remove device-unique serial fields from public CLI output."""

    cleaned = _SERIAL_FIELD.sub("", description)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")


def _devices(_args: argparse.Namespace) -> int:
    rows = [
        {
            "uri": item.uri,
            "description": _sanitize_device_description(item.description),
        }
        for item in discover_pluto()
    ]
    print(json.dumps({"devices": rows}, ensure_ascii=False))
    return 0


def _gain_mode(value: str) -> GainMode:
    try:
        return GainMode(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _window(value: str) -> WindowType:
    try:
        return WindowType(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _compute_backend(value: str) -> ComputeBackendKind:
    try:
        return ComputeBackendKind(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _sample_format(value: str) -> SampleFormat:
    try:
        return SampleFormat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _positive_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be finite and positive")
    return parsed


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _fixed(args: argparse.Namespace) -> int:
    uri = args.uri
    if not uri:
        devices = discover_pluto()
        if not devices:
            print("No PlutoSDR contexts discovered", file=sys.stderr)
            return 2
        uri = devices[0].uri

    bandwidth = args.bandwidth if args.bandwidth is not None else min(args.sample_rate, args.sample_rate * 0.8)
    device = DeviceConfig(
        source_id="p07-cli",
        context_uri=uri,
        center_frequency_hz=args.center,
        sample_rate_hz=args.sample_rate,
        analog_bandwidth_hz=bandwidth,
        gain_mode=args.gain_mode,
        manual_gain_db=args.gain,
        buffer_samples=args.buffer_samples,
    )
    dsp = DspConfig(
        fft_size=args.fft,
        hop_size=args.hop,
        window=args.window,
        detector=DetectorType.SAMPLE,
        unit=SpectrumUnit.DBFS_BIN,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
        batch_size=args.batch,
        averaging_frames=args.average,
    )
    options = FixedBandOptions(
        device=device,
        dsp=dsp,
        backend=args.backend,
        allow_runtime_fallback=not args.no_runtime_fallback,
        snapshot_rate_hz=args.snapshot_rate,
        discard_blocks_after_start=args.discard_blocks,
        dc_removal_block_mean=args.dc_remove,
    )

    with FixedBandEngineService(uri, timeout_ms=args.timeout_ms) as engine:
        applied = engine.configure(options)
        configured = engine.metrics()
        print(
            json.dumps(
                {
                    "event": "configured",
                    "backend": f"pluto-libiio/{configured.active_backend.value}",
                    "requested_backend": configured.requested_backend.value,
                    "active_backend": configured.active_backend.value,
                    "backend_self_test_passed": configured.backend_self_test_passed,
                    "backend_fallback_count": configured.backend_fallback_count,
                    "requested": {
                        "center_frequency_hz": args.center,
                        "sample_rate_hz": args.sample_rate,
                        "analog_bandwidth_hz": bandwidth,
                    },
                    "applied": {
                        "center_frequency_hz": applied.center_frequency_hz,
                        "sample_rate_hz": applied.sample_rate_hz,
                        "analog_bandwidth_hz": applied.analog_bandwidth_hz,
                        "gain_mode": applied.gain_mode.value,
                        "manual_gain_db": applied.manual_gain_db,
                        "config_generation": applied.config_generation,
                    },
                    "dsp": {
                        "fft_size": args.fft,
                        "hop_size": args.hop,
                        "window": args.window.value,
                        "unit": SpectrumUnit.DBFS_BIN.value,
                        "snapshot_rate_limit_hz": args.snapshot_rate,
                    },
                },
                ensure_ascii=False,
            )
        )
        engine.start()
        started = time.monotonic()
        next_report = started
        latest = None
        try:
            while args.duration == 0.0 or time.monotonic() - started < args.duration:
                frames = engine.poll_spectrum(4)
                if frames:
                    latest = frames[-1]
                now = time.monotonic()
                if now >= next_report:
                    metrics = engine.metrics()
                    peak_frequency = None
                    peak_level = None
                    if latest is not None:
                        finite = np.isfinite(latest.values)
                        if np.any(finite):
                            masked = np.where(finite, latest.values, -np.inf)
                            peak_index = int(np.argmax(masked))
                            peak_frequency = float(latest.frequencies_hz[peak_index])
                            peak_level = float(latest.values[peak_index])
                    print(
                        json.dumps(
                            {
                                "event": "status",
                                "health": "OK" if metrics.healthy else "DEGRADED",
                                "state": engine.state.value,
                                "analytical_fft_rate": metrics.engine.analytical_fft_rate,
                                "fft_frames_computed": metrics.engine.fft_frames_computed,
                                "fft_frames_dropped": metrics.engine.fft_frames_dropped,
                                "iq_blocks_received": metrics.engine.iq_blocks_received,
                                "iq_blocks_dropped": metrics.engine.iq_blocks_dropped,
                                "snapshot_queue_dropped": metrics.spectrum_queue.dropped,
                                "snapshot_queue_depth": metrics.spectrum_queue.depth,
                                "end_to_end_latency_ms": metrics.engine.end_to_end_latency_ms,
                                "peak_frequency_hz": peak_frequency,
                                "peak_level_dbfs_bin": peak_level,
                            },
                            ensure_ascii=False,
                        )
                    )
                    next_report = now + args.report_interval
                time.sleep(min(0.01, args.report_interval))
        except KeyboardInterrupt:
            pass
        finally:
            engine.stop()
        final = engine.metrics()
        print(
            json.dumps(
                {
                    "event": "stopped",
                    "health": "OK" if final.healthy else "DEGRADED",
                    "fft_frames_computed": final.engine.fft_frames_computed,
                    "fft_frames_dropped": final.engine.fft_frames_dropped,
                    "iq_blocks_dropped": final.engine.iq_blocks_dropped,
                    "snapshots_emitted": final.engine.spectrum_snapshots_emitted,
                    "snapshots_superseded": final.spectrum_snapshots_superseded,
                },
                ensure_ascii=False,
            )
        )
    return 0


def _sweep_config(args: argparse.Namespace) -> SweepConfig:
    bandwidth = args.bandwidth if args.bandwidth is not None else min(args.sample_rate, args.sample_rate * 0.8)
    return SweepConfig(
        start_frequency_hz=args.start,
        stop_frequency_hz=args.stop,
        sample_rate_hz=args.sample_rate,
        analog_bandwidth_hz=bandwidth,
        overlap_hz=args.overlap,
        fft_size=args.fft,
        hop_size=args.hop,
        dwell_frames=args.dwell_frames,
        settling_time_seconds=args.settling_time,
        discard_blocks=args.discard_blocks,
    )


def _sweep_options(args: argparse.Namespace) -> SweepPlannerOptions:
    return SweepPlannerOptions(
        edge_margin_hz=args.edge_margin,
        dc_exclusion_hz=args.dc_exclusion,
    )


def _plan_payload(plan: object) -> dict[str, object]:
    return {
        "requested_start_hz": plan.requested_start_hz,
        "requested_stop_hz": plan.requested_stop_hz,
        "usable_bandwidth_hz": plan.usable_bandwidth_hz,
        "segment_count": len(plan.segments),
        "expected_duration_s": plan.expected_duration_s,
        "coverage_gaps_hz": list(plan.coverage_gaps_hz),
        "segments": [
            {
                "segment_index": item.segment_index,
                "center_frequency_hz": item.center_frequency_hz,
                "requested_start_hz": item.requested_start_hz,
                "requested_stop_hz": item.requested_stop_hz,
                "actual_start_hz": item.actual_start_hz,
                "actual_stop_hz": item.actual_stop_hz,
                "capture_samples": item.capture_samples,
                "expected_total_duration_s": item.expected_total_duration_s,
                "crop_ranges": [
                    {
                        "start_frequency_hz": crop.start_frequency_hz,
                        "stop_frequency_hz": crop.stop_frequency_hz,
                        "start_bin": crop.start_bin,
                        "stop_bin": crop.stop_bin,
                    }
                    for crop in item.crop_ranges
                ],
            }
            for item in plan.segments
        ],
    }


def _stitched_payload(frame: object) -> dict[str, object]:
    flags = np.asarray(frame.quality_flags_per_bin, dtype=np.uint16)
    return {
        "sweep_id": frame.sweep_id,
        "config_generation": frame.config_generation,
        "unit": frame.unit.value,
        "calibration_status": frame.calibration_status.value,
        "bin_count": int(frame.frequencies_hz.size),
        "missing_bins": int(np.count_nonzero(flags & np.uint16(QualityFlag.MISSING_SEGMENT))),
        "overlap_bins": int(np.count_nonzero(flags & np.uint16(QualityFlag.STITCH_OVERLAP))),
        "seams": [
            {
                "left_segment_index": item.left_segment_index,
                "right_segment_index": item.right_segment_index,
                "correction_db": item.correction_db,
                "before_p95_db": item.before_p95_db,
                "after_p95_db": item.after_p95_db,
            }
            for item in frame.seam_metrics
        ],
    }


def _sweep_plan(args: argparse.Namespace) -> int:
    plan = plan_sweep(_sweep_config(args), _sweep_options(args))
    print(json.dumps({"event": "sweep_plan", **_plan_payload(plan)}, ensure_ascii=False))
    return 0


def _sweep(args: argparse.Namespace) -> int:
    uri = args.uri
    if not uri:
        devices = discover_pluto()
        if not devices:
            print("No PlutoSDR contexts discovered", file=sys.stderr)
            return 2
        uri = devices[0].uri
    config = _sweep_config(args)
    plan = plan_sweep(config, _sweep_options(args))
    device = DeviceConfig(
        source_id="p12-cli",
        context_uri=uri,
        center_frequency_hz=(config.start_frequency_hz + config.stop_frequency_hz) / 2.0,
        sample_rate_hz=config.sample_rate_hz,
        analog_bandwidth_hz=config.analog_bandwidth_hz,
        gain_mode=args.gain_mode,
        manual_gain_db=args.gain,
        buffer_samples=args.buffer_samples,
    )
    dsp = DspConfig(
        fft_size=config.fft_size,
        hop_size=config.hop_size,
        window=args.window,
        detector=DetectorType.SAMPLE,
        unit=SpectrumUnit.DBFS_BIN,
        precision_mode=PrecisionMode.ACCURATE_F32_F64_ACCUM,
    )
    base_options = FixedBandOptions(
        device=device,
        dsp=dsp,
        backend=args.backend,
        allow_runtime_fallback=not args.no_runtime_fallback,
        snapshot_rate_hz=args.snapshot_rate,
        discard_blocks_after_start=config.discard_blocks,
    )
    print(json.dumps({"event": "sweep_planned", **_plan_payload(plan)}, ensure_ascii=False))
    cancel = Event()
    previous_handler = signal.signal(signal.SIGINT, lambda _signum, _frame: cancel.set())
    try:
        with FixedBandEngineService(uri, timeout_ms=args.timeout_ms) as engine:

            def report(progress: object) -> None:
                if getattr(progress, "stage", "") in {"segment_start", "segment_complete", "finished"}:
                    print(
                        json.dumps(
                            {
                                "event": "sweep_progress",
                                "fraction": progress.fraction,
                                "stage": progress.stage,
                                "segment_index": progress.segment_index,
                                "completed_segments": progress.completed_segments,
                                "total_segments": progress.total_segments,
                            },
                            ensure_ascii=False,
                        )
                    )

            result = SweepExecutor(engine, base_options).execute(
                plan,
                cancel=cancel,
                progress=report,
            )
            try:
                stitched_frame = stitch_sweep(
                    result,
                    SweepStitchOptions(target_spacing_hz=args.stitch_spacing),
                )
            except SweepStitchError as exc:
                print(
                    json.dumps(
                        {
                            "event": "sweep_stitch_error",
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )
                return 1
            print(
                json.dumps(
                    {
                        "event": "sweep_finished",
                        "status": result.status.value,
                        "restored": result.restored,
                        "restore_error": result.restore_error,
                        "errors": list(result.errors),
                        "stitched": _stitched_payload(stitched_frame),
                        "segments": [
                            {
                                "segment_index": item.plan.segment_index,
                                "status": item.status.value,
                                "frame_count": len(item.frames),
                                "error": item.error,
                                "timing": {
                                    "retune_s": item.timing.retune_s,
                                    "readback_s": item.timing.readback_s,
                                    "settling_s": item.timing.settling_s,
                                    "capture_s": item.timing.capture_s,
                                    "process_s": item.timing.process_s,
                                    "total_s": item.timing.total_s,
                                },
                                "applied_config_generation": getattr(item.applied_config, "config_generation", None),
                            }
                            for item in result.segments
                        ],
                    },
                    ensure_ascii=False,
                )
            )
            return (
                0
                if result.status is SweepExecutionStatus.COMPLETED
                else 130
                if result.status is SweepExecutionStatus.CANCELLED
                else 1
            )
    finally:
        signal.signal(signal.SIGINT, previous_handler)


def _recording_forecast(args: argparse.Namespace) -> int:
    forecast = estimate_storage(
        sample_rate_hz=args.sample_rate,
        duration_seconds=args.duration,
        sample_format=args.sample_format,
        spectrum_frames_per_second=args.spectrum_fps,
        spectrum_bins=args.spectrum_bins,
        record_iq=args.record_iq,
        record_spectrum=args.record_spectrum,
        output_uri=args.output,
        reserve_bytes=args.reserve_bytes,
    )
    print(
        json.dumps(
            {
                "event": "recording_forecast",
                "sample_format": args.sample_format.value,
                "record_iq": args.record_iq,
                "record_spectrum": args.record_spectrum,
                "iq_bytes_per_second": forecast.iq_bytes_per_second,
                "spectrum_bytes_per_frame": forecast.spectrum_bytes_per_frame,
                "estimated_bytes": forecast.estimated_bytes,
                "free_bytes": forecast.free_bytes,
                "reserve_bytes": forecast.reserve_bytes,
                "sufficient": forecast.sufficient,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _replay_spectrum(args: argparse.Namespace) -> int:
    replay = SpectrumReplay(args.path, allow_partial=args.allow_partial)
    count = 0
    for frame in replay.iter_frames():
        print(
            json.dumps(
                {
                    "event": "spectrum_frame",
                    "frame_sequence": frame.frame_sequence,
                    "timestamp_ns": frame.timestamp_ns,
                    "frequency_start_hz": float(frame.frequencies_hz[0]),
                    "frequency_stop_hz": float(frame.frequencies_hz[-1]),
                    "bin_count": int(frame.frequencies_hz.size),
                    "unit": frame.unit.value,
                    "calibration_status": frame.calibration_status.value,
                    "calibration_profile_id": frame.calibration_profile_id,
                    "quality_flags": int(frame.quality_flags),
                    "source_type": frame.source.source_type.value,
                },
                ensure_ascii=False,
            )
        )
        count += 1
        if args.limit and count >= args.limit:
            break
    return 0


def _recording_recover(args: argparse.Namespace) -> int:
    if args.kind == "iq":
        result = recover_iq_recording(args.path, finalize=args.finalize)
        payload = {
            "base_path": str(result.base_path),
            "truncated_bytes": result.truncated_bytes,
            "retained_iq_blocks": result.retained_iq_blocks,
            "finalized": result.finalized,
        }
    else:
        truncated = recover_spectrum_recording(args.path, finalize=args.finalize)
        payload = {"base_path": str(args.path), "truncated_lines": truncated, "finalized": args.finalize}
    print(json.dumps({"event": "recording_recovered", "kind": args.kind, **payload}, ensure_ascii=False))
    return 0


def _add_sweep_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--uri", default="", help="libiio URI; first discovered for execution")
    parser.add_argument("--start", type=_positive_float, default=2_300_000_000.0)
    parser.add_argument("--stop", type=_positive_float, default=2_500_000_000.0)
    parser.add_argument("--sample-rate", type=_positive_float, default=3_000_000.0)
    parser.add_argument("--bandwidth", type=_positive_float)
    parser.add_argument("--overlap", type=float, default=200_000.0)
    parser.add_argument("--edge-margin", type=float, default=0.0)
    parser.add_argument("--dc-exclusion", type=float, default=0.0)
    parser.add_argument("--fft", type=_positive_int, default=4096)
    parser.add_argument("--hop", type=_positive_int, default=2048)
    parser.add_argument("--dwell-frames", type=_positive_int, default=1)
    parser.add_argument("--settling-time", type=float, default=0.0)
    parser.add_argument("--discard-blocks", type=int, default=2)
    parser.add_argument("--gain-mode", type=_gain_mode, default=GainMode.MANUAL)
    parser.add_argument("--gain", type=float, default=20.0)
    parser.add_argument("--buffer-samples", type=_positive_int, default=16_384)
    parser.add_argument("--window", type=_window, default=WindowType.HANN)
    parser.add_argument(
        "--backend",
        type=_compute_backend,
        choices=(ComputeBackendKind.AUTO, ComputeBackendKind.CPU, ComputeBackendKind.CUDA),
        default=ComputeBackendKind.AUTO,
    )
    parser.add_argument("--no-runtime-fallback", action="store_true")
    parser.add_argument("--snapshot-rate", type=_positive_float, default=60.0)
    parser.add_argument("--stitch-spacing", type=_positive_float, help="explicit P13 target spacing in Hz")
    parser.add_argument("--timeout-ms", type=_positive_int, default=3000)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m esw_dfl.sdr.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    devices = subparsers.add_parser("devices", help="list Pluto USB/IP contexts")
    devices.set_defaults(func=_devices)

    fixed = subparsers.add_parser("fixed", help="run the P07 fixed-band pipeline")
    fixed.add_argument("--uri", default="", help="libiio URI; first discovered when omitted")
    fixed.add_argument("--center", type=_positive_float, default=2_450_000_000.0)
    fixed.add_argument("--sample-rate", type=_positive_float, default=3_000_000.0)
    fixed.add_argument("--bandwidth", type=_positive_float)
    fixed.add_argument("--gain-mode", type=_gain_mode, default=GainMode.MANUAL)
    fixed.add_argument("--gain", type=float, default=20.0)
    fixed.add_argument("--buffer-samples", type=_positive_int, default=16_384)
    fixed.add_argument("--fft", type=_positive_int, default=4096)
    fixed.add_argument("--hop", type=_positive_int, default=2048)
    fixed.add_argument("--batch", type=_positive_int, default=4)
    fixed.add_argument("--average", type=_positive_int, default=1)
    fixed.add_argument("--window", type=_window, default=WindowType.HANN)
    fixed.add_argument(
        "--backend",
        type=_compute_backend,
        choices=(ComputeBackendKind.AUTO, ComputeBackendKind.CPU, ComputeBackendKind.CUDA),
        default=ComputeBackendKind.AUTO,
        help="DSP compute backend preference",
    )
    fixed.add_argument(
        "--no-runtime-fallback",
        action="store_true",
        help="fail instead of falling back when the requested backend is unavailable",
    )
    fixed.add_argument("--snapshot-rate", type=_positive_float, default=60.0)
    fixed.add_argument("--discard-blocks", type=int, default=2)
    fixed.add_argument("--dc-remove", action="store_true")
    fixed.add_argument("--timeout-ms", type=_positive_int, default=3000)
    fixed.add_argument("--duration", type=float, default=10.0, help="seconds; 0 runs until Ctrl+C")
    fixed.add_argument("--report-interval", type=_positive_float, default=1.0)
    fixed.set_defaults(func=_fixed)

    forecast = subparsers.add_parser("recording-forecast", help="estimate P14 recording storage")
    forecast.add_argument("--sample-rate", type=_positive_float, required=True)
    forecast.add_argument("--duration", type=_positive_float, required=True)
    forecast.add_argument("--sample-format", type=_sample_format, default=SampleFormat.COMPLEX_FLOAT32_LE)
    forecast.add_argument("--spectrum-fps", type=_positive_float, default=0.0)
    forecast.add_argument("--spectrum-bins", type=int, default=0)
    forecast.add_argument("--record-iq", action=argparse.BooleanOptionalAction, default=True)
    forecast.add_argument("--record-spectrum", action=argparse.BooleanOptionalAction, default=False)
    forecast.add_argument("--output", default="")
    forecast.add_argument("--reserve-bytes", type=int, default=0)
    forecast.set_defaults(func=_recording_forecast)

    replay_spectrum = subparsers.add_parser("replay-spectrum", help="replay a P14 spectrum JSONL recording")
    replay_spectrum.add_argument("path")
    replay_spectrum.add_argument("--limit", type=_positive_int, default=0)
    replay_spectrum.add_argument("--allow-partial", action="store_true")
    replay_spectrum.set_defaults(func=_replay_spectrum)

    recover = subparsers.add_parser("recording-recover", help="recover a P14 interrupted recording")
    recover.add_argument("kind", choices=("iq", "spectrum"))
    recover.add_argument("path")
    recover.add_argument("--finalize", action="store_true")
    recover.set_defaults(func=_recording_recover)
    sweep_plan = subparsers.add_parser("sweep-plan", help="plan a P12 wide-span sweep without hardware")
    _add_sweep_arguments(sweep_plan)
    sweep_plan.set_defaults(func=_sweep_plan)

    sweep = subparsers.add_parser("sweep", help="execute a P12 wide-span sweep")
    _add_sweep_arguments(sweep)
    sweep.set_defaults(func=_sweep)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fixed":
        if args.duration < 0.0 or not math.isfinite(args.duration):
            raise SystemExit("--duration must be finite and non-negative")
        if args.discard_blocks < 0:
            raise SystemExit("--discard-blocks must be non-negative")
    if args.command in {"sweep-plan", "sweep"}:
        if args.stop <= args.start:
            raise SystemExit("--stop must exceed --start")
        if args.overlap < 0.0 or not math.isfinite(args.overlap):
            raise SystemExit("--overlap must be finite and non-negative")
        if args.edge_margin < 0.0 or not math.isfinite(args.edge_margin):
            raise SystemExit("--edge-margin must be finite and non-negative")
        if args.dc_exclusion < 0.0 or not math.isfinite(args.dc_exclusion):
            raise SystemExit("--dc-exclusion must be finite and non-negative")
        if args.settling_time < 0.0 or not math.isfinite(args.settling_time):
            raise SystemExit("--settling-time must be finite and non-negative")
        if args.discard_blocks < 0:
            raise SystemExit("--discard-blocks must be non-negative")
    try:
        return int(args.func(args))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "event": "error",
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
