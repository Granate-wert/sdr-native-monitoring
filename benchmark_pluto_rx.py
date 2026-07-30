"""RX-only Pluto/libiio hardware benchmark for P06 acceptance evidence."""

from __future__ import annotations

import argparse
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from esw_dfl.sdr import DeviceConfig, GainMode
from esw_dfl.sdr.native_api import require_native
from esw_dfl.sdr.pluto import PlutoDeviceService, discover_pluto


@dataclass(frozen=True, slots=True)
class RateResult:
    requested_sample_rate_hz: float
    applied_sample_rate_hz: float
    duration_s: float
    process_cpu_percent_one_core: float
    blocks_received: int
    samples_received: int
    received_samples_per_s: float
    payload_mib_per_s: float
    short_reads: int
    refill_errors: int
    output_pool_exhaustions: int
    output_blocks_dropped: int
    estimated_dropped_samples: int


def _select_uri(explicit: str | None) -> str:
    if explicit:
        return explicit
    contexts = discover_pluto()
    if not contexts:
        raise RuntimeError("no Pluto context discovered")
    return next((item.uri for item in contexts if item.uri.startswith("ip:")), contexts[0].uri)


def _uri_scheme(uri: str) -> str:
    return uri.split(":", 1)[0].lower()


def _config(uri: str, sample_rate_hz: float, buffer_samples: int) -> DeviceConfig:
    return DeviceConfig(
        source_id="p06-hardware-benchmark",
        context_uri=uri,
        center_frequency_hz=2_450_000_000.0,
        sample_rate_hz=sample_rate_hz,
        analog_bandwidth_hz=min(sample_rate_hz * 0.5, 20_000_000.0),
        gain_mode=GainMode.MANUAL,
        manual_gain_db=20.0,
        buffer_samples=buffer_samples,
    )


def benchmark_rate(uri: str, sample_rate_hz: float, duration_s: float, buffer_samples: int) -> RateResult:
    with PlutoDeviceService(uri, timeout_ms=5000) as device:
        applied = device.configure(_config(uri, sample_rate_hz, buffer_samples))
        device.start()
        start_wall = time.perf_counter()
        start_cpu = time.process_time()
        while time.perf_counter() - start_wall < duration_s:
            device.read_block()
        elapsed = time.perf_counter() - start_wall
        cpu_elapsed = time.process_time() - start_cpu
        metrics = device.metrics()
        device.stop()
    samples_per_s = metrics.samples_received / elapsed
    return RateResult(
        requested_sample_rate_hz=sample_rate_hz,
        applied_sample_rate_hz=applied.sample_rate_hz,
        duration_s=elapsed,
        process_cpu_percent_one_core=100.0 * cpu_elapsed / elapsed,
        blocks_received=metrics.blocks_received,
        samples_received=metrics.samples_received,
        received_samples_per_s=samples_per_s,
        payload_mib_per_s=samples_per_s * 4.0 / (1024.0 * 1024.0),
        short_reads=metrics.short_reads,
        refill_errors=metrics.refill_errors,
        output_pool_exhaustions=metrics.output_pool_exhaustions,
        output_blocks_dropped=metrics.output_blocks_dropped,
        estimated_dropped_samples=metrics.estimated_dropped_samples,
    )


def benchmark_cancel(uri: str, sample_rate_hz: float, buffer_samples: int) -> dict[str, Any]:
    device = PlutoDeviceService(uri, timeout_ms=5000)
    device.configure(_config(uri, sample_rate_hz, buffer_samples))
    device.start()
    entered = threading.Event()
    result: dict[str, Any] = {}

    def refill_once() -> None:
        entered.set()
        try:
            device.read_block()
            result["result"] = "refill_completed_before_cancel"
        except Exception as error:  # exact translated type is recorded below
            result["result"] = type(error).__name__

    thread = threading.Thread(target=refill_once, name="p06-cancel-refill", daemon=True)
    thread.start()
    if not entered.wait(timeout=1.0):
        device.disconnect()
        raise RuntimeError("refill thread did not start")
    time.sleep(0.02)
    started = time.perf_counter()
    device.cancel()
    thread.join(timeout=2.0)
    result["cancel_latency_ms"] = (time.perf_counter() - started) * 1000.0
    result["thread_stopped"] = not thread.is_alive()
    device.disconnect()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uri", help="explicit libiio URI; defaults to discovered IP then USB")
    parser.add_argument("--duration", type=float, default=60.0, help="seconds per sample rate")
    parser.add_argument("--rates", type=float, nargs="+", default=[3_000_000.0])
    parser.add_argument("--buffer-samples", type=int, default=65_536)
    parser.add_argument("--cancel-buffer-samples", type=int, default=1_048_576)
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration <= 0.0 or args.buffer_samples <= 0 or args.cancel_buffer_samples <= 0:
        raise ValueError("duration and buffer sizes must be positive")
    native = require_native()
    runtime = native.pluto_runtime_info()
    uri = _select_uri(args.uri)
    payload = {
        "schema_version": 1,
        "backend": "pluto-libiio-rx-only",
        "uri_scheme": _uri_scheme(uri),
        "libiio_version": f"{runtime.major}.{runtime.minor}",
        "rates": [
            asdict(benchmark_rate(uri, rate, args.duration, args.buffer_samples))
            for rate in args.rates
        ],
        "cancel": benchmark_cancel(uri, args.rates[0], args.cancel_buffer_samples),
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True)
    print(encoded)
    if args.output is not None:
        target = args.output.resolve()
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_text(encoded + os.linesep, encoding="utf-8")
        temporary.replace(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())