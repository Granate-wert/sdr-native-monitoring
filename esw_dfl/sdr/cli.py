"""Headless diagnostics for SDR discovery and P07 fixed-band acquisition."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Sequence

import numpy as np

from .contracts import (
    DetectorType,
    DeviceConfig,
    DspConfig,
    GainMode,
    PrecisionMode,
    SpectrumUnit,
    WindowType,
)
from .fixed_band import FixedBandEngineService, FixedBandOptions
from .pluto import discover_pluto


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

    bandwidth = (
        args.bandwidth
        if args.bandwidth is not None
        else min(args.sample_rate, args.sample_rate * 0.8)
    )
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
        snapshot_rate_hz=args.snapshot_rate,
        discard_blocks_after_start=args.discard_blocks,
        dc_removal_block_mean=args.dc_remove,
    )

    with FixedBandEngineService(uri, timeout_ms=args.timeout_ms) as engine:
        applied = engine.configure(options)
        print(
            json.dumps(
                {
                    "event": "configured",
                    "backend": "pluto-libiio/cpu-pocketfft",
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
    fixed.add_argument("--snapshot-rate", type=_positive_float, default=60.0)
    fixed.add_argument("--discard-blocks", type=int, default=2)
    fixed.add_argument("--dc-remove", action="store_true")
    fixed.add_argument("--timeout-ms", type=_positive_int, default=3000)
    fixed.add_argument("--duration", type=float, default=10.0, help="seconds; 0 runs until Ctrl+C")
    fixed.add_argument("--report-interval", type=_positive_float, default=1.0)
    fixed.set_defaults(func=_fixed)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "fixed":
        if args.duration < 0.0 or not math.isfinite(args.duration):
            raise SystemExit("--duration must be finite and non-negative")
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
