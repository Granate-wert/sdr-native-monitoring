"""Generate byte-deterministic P03 golden NPZ vectors and their manifest."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Mapping

import numpy as np

from esw_dfl.sdr.contracts import WindowType
from esw_dfl.sdr.reference_dsp import integrate_psd, reference_spectrum
from esw_dfl.sdr.synthetic import (
    SyntheticConfig,
    SyntheticScenario,
    generate_scenario,
    signal_sha256,
)


GOLDEN_SCHEMA_NAME = "sdr-golden-vector"
GOLDEN_SCHEMA_VERSION = 1
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _npy_bytes(value: object) -> bytes:
    stream = io.BytesIO()
    np.lib.format.write_array(stream, np.asanyarray(value), allow_pickle=False)
    return stream.getvalue()


def deterministic_npz_bytes(arrays: Mapping[str, object]) -> bytes:
    """Return an uncompressed, fixed-metadata NPZ archive.

    NumPy values are serialized in sorted entry order. ZIP timestamps,
    permissions and host-system fields are fixed, so equal arrays produce
    identical bytes on Windows and Linux.
    """

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED, strict_timestamps=True) as archive:
        for name in sorted(arrays):
            if not name or "/" in name or "\\" in name:
                raise ValueError(f"invalid golden-vector field name: {name!r}")
            info = zipfile.ZipInfo(f"{name}.npy", date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, _npy_bytes(arrays[name]))
    return output.getvalue()


def vector_arrays(scenario: SyntheticScenario, config: SyntheticConfig | None = None) -> dict[str, object]:
    selected = SyntheticConfig() if config is None else config
    signal = generate_scenario(scenario, selected)
    spectrum = reference_spectrum(
        signal.samples,
        selected.sample_rate_hz,
        center_frequency_hz=selected.center_frequency_hz,
        window=WindowType.HANN,
    )
    peak_index = int(np.argmax(spectrum.dbfs_per_bin))
    configuration = {
        "schema": GOLDEN_SCHEMA_NAME,
        "schema_version": GOLDEN_SCHEMA_VERSION,
        "scenario": scenario.value,
        "sample_count": selected.sample_count,
        "sample_rate_hz": selected.sample_rate_hz,
        "center_frequency_hz": selected.center_frequency_hz,
        "seed": selected.seed,
        "quantization_bits": selected.quantization_bits,
        "window": WindowType.HANN.value,
        "quality_flags": int(signal.quality_flags),
        "scenario_metadata": dict(signal.metadata),
        "input_sha256": signal_sha256(signal),
    }
    return {
        "center_frequency": np.asarray(selected.center_frequency_hz, dtype="<f8"),
        "config": np.asarray(_json(configuration)),
        "expected_frequency": np.asarray(spectrum.frequencies_hz[peak_index], dtype="<f8"),
        "expected_frequency_axis": np.asarray(spectrum.frequencies_hz, dtype="<f8"),
        "expected_integrated_power": np.asarray(
            integrate_psd(spectrum.psd_dbfs_per_hz_linear, spectrum.bin_width_hz),
            dtype="<f8",
        ),
        "expected_peak": np.asarray(spectrum.dbfs_per_bin[peak_index], dtype="<f8"),
        "expected_psd": np.asarray(spectrum.psd_dbfs_per_hz_linear, dtype="<f8"),
        "input_iq": np.asarray(signal.samples, dtype="<c16"),
        "sample_rate": np.asarray(selected.sample_rate_hz, dtype="<f8"),
    }


def write_golden_vectors(output_directory: Path) -> dict[str, object]:
    output_directory.mkdir(parents=True, exist_ok=True)
    vectors: list[dict[str, object]] = []
    for scenario in SyntheticScenario:
        name = f"{scenario.value}.npz"
        payload = deterministic_npz_bytes(vector_arrays(scenario))
        destination = output_directory / name
        destination.write_bytes(payload)
        vectors.append(
            {
                "file": name,
                "scenario": scenario.value,
                "sha256": hashlib.sha256(payload).hexdigest().upper(),
                "size_bytes": len(payload),
            }
        )
    manifest: dict[str, object] = {
        "schema": "sdr-golden-manifest",
        "schema_version": 1,
        "generator": "tests.sdr_golden_vectors.generate_vectors",
        "vector_schema": GOLDEN_SCHEMA_NAME,
        "vector_schema_version": GOLDEN_SCHEMA_VERSION,
        "vectors": vectors,
    }
    manifest_bytes = (_json(manifest) + "\n").encode("utf-8")
    (output_directory / "manifest.json").write_bytes(manifest_bytes)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory receiving deterministic *.npz and manifest.json",
    )
    arguments = parser.parse_args()
    manifest = write_golden_vectors(arguments.output)
    print(_json(manifest))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
