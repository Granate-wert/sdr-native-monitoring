"""Validation/import utility for P09 calibration profiles."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from esw_dfl.sdr.calibration_store import (
    CalibrationProfileError,
    CalibrationProfileStore,
    CalibrationSignature,
    profile_from_csv,
    validate_profile_file,
)


def _signature(args: argparse.Namespace) -> CalibrationSignature:
    return CalibrationSignature(
        device_serial=args.serial,
        backend=args.backend,
        rf_port_path=args.rf_path,
        sample_rate_hz=args.sample_rate,
        analog_bandwidth_hz=args.bandwidth,
        gain_mode=args.gain_mode,
        manual_gain_db=args.gain,
        window_normalization_version=args.window_normalization,
        fft_unit_convention=args.fft_unit,
        frontend_chain=args.frontend_chain,
        reference_plane=args.reference_plane,
    )


def _validate(args: argparse.Namespace) -> int:
    profile = validate_profile_file(args.profile)
    print(json.dumps({
        "valid": True,
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "point_count": len(profile.points),
        "frequency_start_hz": profile.points[0].frequency_hz,
        "frequency_stop_hz": profile.points[-1].frequency_hz,
        "fingerprint": profile.fingerprint,
    }, ensure_ascii=False))
    return 0


def _import_csv(args: argparse.Namespace) -> int:
    profile = profile_from_csv(
        args.csv,
        profile_id=args.profile_id,
        profile_version=args.profile_version,
        signature=_signature(args),
        reference_equipment=args.reference_equipment,
        notes=args.notes,
    )
    path = CalibrationProfileStore(args.output_dir).save(profile)
    print(json.dumps({
        "saved": str(path),
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "fingerprint": profile.fingerprint,
    }, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m esw_dfl.sdr.calibration_cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate one profile JSON")
    validate.add_argument("profile")
    validate.set_defaults(func=_validate)

    import_csv = subparsers.add_parser("import-csv", help="import the P09 five-column CSV format")
    import_csv.add_argument("csv")
    import_csv.add_argument("output_dir")
    import_csv.add_argument("--profile-id", required=True)
    import_csv.add_argument("--profile-version", type=int, default=1)
    import_csv.add_argument("--serial", required=True)
    import_csv.add_argument("--backend", default="cpu")
    import_csv.add_argument("--rf-path", default="rx")
    import_csv.add_argument("--sample-rate", type=float, required=True)
    import_csv.add_argument("--bandwidth", type=float, required=True)
    import_csv.add_argument("--gain-mode", default="manual")
    import_csv.add_argument("--gain", type=float, default=0.0)
    import_csv.add_argument("--window-normalization", default="p05-v1")
    import_csv.add_argument("--fft-unit", default="dBFS/bin")
    import_csv.add_argument("--frontend-chain", default="pluto-rx")
    import_csv.add_argument("--reference-plane", default="rf_input")
    import_csv.add_argument("--reference-equipment", default="")
    import_csv.add_argument("--notes", default="")
    import_csv.set_defaults(func=_import_csv)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CalibrationProfileError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"valid": False, "error_type": type(exc).__name__, "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
