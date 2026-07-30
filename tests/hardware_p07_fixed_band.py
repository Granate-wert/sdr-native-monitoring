"""Long-running real-Pluto acceptance harness for P07.

This is deliberately not named ``test_*.py``: hardware access is opt-in.
It performs RX only and prints JSONL evidence without exposing device serials.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from esw_dfl.sdr import (  # noqa: E402
    DeviceConfig,
    DspConfig,
    EngineState,
    FixedBandEngineService,
    FixedBandOptions,
    GainMode,
    SpectrumUnit,
    discover_pluto,
)


class ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def private_bytes() -> int:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(ProcessMemoryCountersEx),
        wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    process = kernel32.GetCurrentProcess()
    ok = psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.PrivateUsage)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--uri", default="")
    result.add_argument("--duration", type=float, default=600.0)
    result.add_argument("--center", type=float, default=2_450_000_000.0)
    result.add_argument("--sample-rate", type=float, default=3_000_000.0)
    result.add_argument("--bandwidth", type=float, default=1_500_000.0)
    result.add_argument("--buffer-samples", type=int, default=16_384)
    result.add_argument("--fft", type=int, default=4096)
    result.add_argument("--hop", type=int, default=2048)
    result.add_argument("--snapshot-rate", type=float, default=60.0)
    result.add_argument("--poll-interval", type=float, default=0.05)
    result.add_argument("--report-interval", type=float, default=10.0)
    result.add_argument("--warmup", type=float, default=30.0)
    result.add_argument(
        "--max-private-growth-mib",
        type=float,
        default=64.0,
        help="post-warmup allocator-retention tolerance",
    )
    return result


def main() -> int:
    args = parser().parse_args()
    if os.name != "nt":
        raise SystemExit("P07 Pluto acceptance is currently Windows-only")
    if args.duration <= 0.0 or args.poll_interval <= 0.0:
        raise SystemExit("duration and poll interval must be positive")
    uri = args.uri
    if not uri:
        devices = discover_pluto()
        usb = [item.uri for item in devices if item.uri.startswith("usb:")]
        if not usb:
            raise SystemExit("no USB PlutoSDR discovered")
        uri = usb[0]

    options = FixedBandOptions(
        device=DeviceConfig(
            source_id="p07-hardware-acceptance",
            context_uri=uri,
            center_frequency_hz=args.center,
            sample_rate_hz=args.sample_rate,
            analog_bandwidth_hz=args.bandwidth,
            gain_mode=GainMode.MANUAL,
            manual_gain_db=20.0,
            buffer_samples=args.buffer_samples,
        ),
        dsp=DspConfig(
            fft_size=args.fft,
            hop_size=args.hop,
            unit=SpectrumUnit.DBFS_BIN,
            batch_size=4,
        ),
        snapshot_rate_hz=args.snapshot_rate,
        spectrum_queue_capacity=4,
    )

    memory_samples: list[tuple[float, int]] = []
    frames_seen = 0
    first_frame_sequence: int | None = None
    last_frame_sequence: int | None = None
    with FixedBandEngineService(uri) as engine:
        applied = engine.configure(options)
        print(
            json.dumps(
                {
                    "event": "configured",
                    "backend": "pluto-libiio/cpu-pocketfft",
                    "applied_center_hz": applied.center_frequency_hz,
                    "applied_sample_rate_hz": applied.sample_rate_hz,
                    "applied_bandwidth_hz": applied.analog_bandwidth_hz,
                    "generation": applied.config_generation,
                }
            ),
            flush=True,
        )
        engine.start()
        started = time.monotonic()
        next_report = started
        try:
            while time.monotonic() - started < args.duration:
                now = time.monotonic()
                frames = engine.poll_spectrum(4)
                if frames:
                    frames_seen += len(frames)
                    if first_frame_sequence is None:
                        first_frame_sequence = frames[0].frame_sequence
                    last_frame_sequence = frames[-1].frame_sequence
                    if any(
                        frame.config_generation != applied.config_generation
                        for frame in frames
                    ):
                        raise RuntimeError("stale config generation crossed snapshot boundary")
                if engine.state is EngineState.ERROR:
                    raise RuntimeError("native fixed-band engine entered ERROR")
                if now - started >= args.warmup:
                    memory_samples.append((now - started, private_bytes()))
                if now >= next_report:
                    metrics = engine.metrics()
                    print(
                        json.dumps(
                            {
                                "event": "status",
                                "elapsed_s": now - started,
                                "fft_rate": metrics.engine.analytical_fft_rate,
                                "fft_frames": metrics.engine.fft_frames_computed,
                                "fft_dropped": metrics.engine.fft_frames_dropped,
                                "iq_dropped": metrics.engine.iq_blocks_dropped,
                                "snapshot_dropped": metrics.spectrum_queue.dropped,
                                "latency_ms": metrics.engine.end_to_end_latency_ms,
                                "private_bytes": private_bytes(),
                                "healthy": metrics.healthy,
                            }
                        ),
                        flush=True,
                    )
                    next_report = now + args.report_interval
                time.sleep(args.poll_interval)
        finally:
            engine.stop()
        metrics = engine.metrics()

    growth = 0
    span = 0
    if memory_samples:
        values = [value for _, value in memory_samples]
        growth = values[-1] - values[0]
        span = max(values) - min(values)
    limit = int(args.max_private_growth_mib * 1024 * 1024)
    summary = {
        "event": "completed",
        "duration_s": args.duration,
        "frames_polled": frames_seen,
        "first_frame_sequence": first_frame_sequence,
        "last_frame_sequence": last_frame_sequence,
        "fft_frames": metrics.engine.fft_frames_computed,
        "fft_rate": metrics.engine.analytical_fft_rate,
        "fft_dropped": metrics.engine.fft_frames_dropped,
        "iq_dropped": metrics.engine.iq_blocks_dropped,
        "shutdown_blocks_discarded": metrics.shutdown_blocks_discarded,
        "snapshot_dropped": metrics.spectrum_queue.dropped,
        "queue_high_water": metrics.acquisition_queue.high_water,
        "queue_capacity": metrics.acquisition_queue.capacity,
        "private_growth_bytes": growth,
        "private_span_bytes": span,
        "healthy": metrics.healthy,
    }
    print(json.dumps(summary), flush=True)

    if frames_seen == 0:
        raise RuntimeError("no spectrum snapshots were polled")
    if metrics.engine.fft_frames_computed == 0:
        raise RuntimeError("no analytical FFT frames were computed")
    if not metrics.healthy:
        raise RuntimeError("fixed-band health is degraded")
    if metrics.acquisition_queue.high_water > metrics.acquisition_queue.capacity:
        raise RuntimeError("acquisition queue exceeded its configured bound")
    if len(memory_samples) >= 2 and growth > limit:
        raise RuntimeError(
            f"post-warmup private bytes grew by {growth} (limit {limit})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
