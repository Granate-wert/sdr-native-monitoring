"""P15 validation evidence runner.

Offline validation is safe by default. Add ``--hardware`` only for an explicit
RX-only Pluto run; no device identifiers are printed or written to evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path

from esw_dfl.sdr.contracts import ComputeBackendKind
from esw_dfl.sdr.validation import run_hardware_fixed_band, run_offline_validation


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0.0:
        raise argparse.ArgumentTypeError("value must be positive")
    return parsed


def _backends(value: str) -> tuple[ComputeBackendKind, ...]:
    result: list[ComputeBackendKind] = []
    for item in value.split(","):
        try:
            backend = ComputeBackendKind(item.strip())
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        if backend not in result:
            result.append(backend)
    if not result:
        raise argparse.ArgumentTypeError("at least one backend is required")
    return tuple(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="benchmark_p15_validation.py")
    parser.add_argument("--benchmark-repeats", type=_positive_int, default=3)
    parser.add_argument("--recording-blocks", type=_positive_int, default=256)
    parser.add_argument("--output-dir", type=Path, help="private evidence directory; never commit its contents")
    parser.add_argument("--hardware", action="store_true", help="run opt-in RX-only Pluto fixed-band matrix")
    parser.add_argument(
        "--hardware-duration", type=_positive_float, default=600.0, help="seconds for each requested hardware backend"
    )
    parser.add_argument(
        "--backends", type=_backends, default=(ComputeBackendKind.CPU, ComputeBackendKind.AUTO, ComputeBackendKind.CUDA)
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report = run_offline_validation(benchmark_repeats=args.benchmark_repeats, recording_blocks=args.recording_blocks)
    if args.hardware:
        report = replace(
            report,
            completed_utc=datetime.now(timezone.utc).isoformat(),
            results=report.results
            + run_hardware_fixed_band(duration_seconds=args.hardware_duration, backends=args.backends),
        )
    evidence: dict[str, str] = {}
    if args.output_dir is not None:
        evidence = {key: str(value) for key, value in report.write_evidence(args.output_dir).items()}
    payload = report.to_dict()
    if evidence:
        payload["evidence_paths"] = evidence
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
