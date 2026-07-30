"""P08 CUDA/cuFFT backend: availability, selection and golden parity tests.

The native module loads the CUDA runtime and cuFFT dynamically. On machines
where the CUDA toolkit is installed but its ``bin`` directory is not on
``PATH``, the tests prepend a discovered toolkit directory to the process
``PATH`` before probing availability. When CUDA remains unavailable, the
CUDA-executing tests skip with the native reason instead of faking a pass.
"""

from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from types import ModuleType

import numpy as np

from esw_dfl.sdr import native_api
from esw_dfl.sdr.contracts import CONTRACT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[1]
GOLDEN_DIR = Path(__file__).resolve().parent / "sdr_golden_vectors"

# Tolerances fixed by docs/architecture/sdr_golden_reference.md; they must
# not be widened to pass the backend.
PSD_RTOL = 2e-5
PSD_ATOL = 1e-12
PEAK_DB_TOLERANCE = 5e-5
INTEGRATED_RTOL = 2e-5

# Vectors additionally checked with the ACCURATE_F32_F64_ACCUM precision mode.
ACCURATE_PARITY_VECTORS = ("exact_bin_tone", "half_bin_tone", "broadband_noise")

_UNIQUE_ID_PATTERN = re.compile(
    r"serial|s/n|uuid|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    flags=re.IGNORECASE,
)


def _native_backend_module() -> ModuleType | None:
    if not native_api.native_availability().available:
        return None
    try:
        module = native_api.require_native()
    except native_api.NativeModuleUnavailableError:
        return None
    required = (
        "make_dsp_backend",
        "backend_availability",
        "run_backend_self_test",
        "DspBackendSelectionOptions",
        "BackendUnavailableError",
    )
    if not all(hasattr(module, name) for name in required):
        return None
    return module


def _prepend_cuda_runtime_path() -> None:
    """Make dynamically loaded cuFFT DLLs discoverable on Windows."""

    if os.name != "nt":
        return
    candidates = []
    cuda_path = os.environ.get("CUDA_PATH")
    if cuda_path:
        candidates.append(Path(cuda_path))
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidates.extend(
        Path(entry)
        for entry in glob.glob(
            str(Path(program_files) / "NVIDIA GPU Computing Toolkit" / "CUDA" / "v*")
        )
    )
    for toolkit in candidates:
        for bin_dir in (toolkit / "bin", toolkit / "bin" / "x64"):
            if (
                bin_dir.is_dir()
                and any(bin_dir.glob("cufft*.dll"))
                and str(bin_dir) not in os.environ.get("PATH", "")
            ):
                os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")


_NATIVE = _native_backend_module()
if _NATIVE is not None:
    _prepend_cuda_runtime_path()


def _cuda_skip_reason() -> str | None:
    if _NATIVE is None:
        return "compiled P08 _sdr_native module is unavailable"
    availability = _NATIVE.backend_availability(_NATIVE.ComputeBackendKind.CUDA)
    if not availability.compiled:
        return "native module was built without CUDA support"
    if not availability.device_supported:
        details = availability.details or str(availability.reason_code)
        return f"CUDA device is unavailable: {details}"
    return None


def _golden_files() -> list[Path]:
    manifest = json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="ascii"))
    return [GOLDEN_DIR / entry["file"] for entry in manifest["vectors"]]


@unittest.skipUnless(_NATIVE is not None, "compiled P08 _sdr_native module is unavailable")
class BackendSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = _NATIVE

    def test_availability_report(self) -> None:
        native = self.native
        availability = native.backend_availability(native.ComputeBackendKind.CUDA)
        if not native.build_info()["cuda_compiled"]:
            self.assertFalse(availability.compiled)
            return
        self.assertTrue(availability.compiled)
        self.assertTrue(availability.runtime_present, availability.details)
        if not availability.device_supported:
            self.skipTest(
                f"CUDA device is unavailable: {availability.details or availability.reason_code}"
            )
        self.assertTrue(availability.device_supported)
        # BackendAvailability.reason_code is a wire string ("" when NONE),
        # unlike DspBackendMetrics.last_backend_error, which is the enum.
        self.assertIn(availability.reason_code, ("", "none"))

    def test_cpu_only_preference(self) -> None:
        native = self.native
        backend = native.make_dsp_backend(
            native.DspBackendSelectionOptions(preference=native.ComputeBackendKind.CPU)
        )
        self.assertEqual(backend.info().kind, native.ComputeBackendKind.CPU)
        metrics = backend.metrics()
        self.assertEqual(metrics.requested_preference, native.ComputeBackendKind.CPU)
        self.assertEqual(metrics.active_backend, native.ComputeBackendKind.CPU)
        self.assertEqual(metrics.backend_fallback_count, 0)
        self.assertEqual(metrics.backend_switch_count, 0)
        self.assertEqual(metrics.last_backend_error, native.BackendErrorCode.NONE)

    def test_hip_rejected(self) -> None:
        native = self.native
        with self.assertRaises(native.BackendUnavailableError):
            native.make_dsp_backend(
                native.DspBackendSelectionOptions(preference=native.ComputeBackendKind.HIP)
            )

    def test_metrics_immutable(self) -> None:
        native = self.native
        backend = native.make_dsp_backend(
            native.DspBackendSelectionOptions(preference=native.ComputeBackendKind.CPU)
        )
        metrics = backend.metrics()
        with self.assertRaises(AttributeError):
            metrics.fft_frames_computed = 0
        with self.assertRaises(AttributeError):
            metrics.active_backend = native.ComputeBackendKind.CUDA
        info = backend.info()
        with self.assertRaises(AttributeError):
            info.kind = native.ComputeBackendKind.CUDA
        availability = native.backend_availability(native.ComputeBackendKind.CPU)
        with self.assertRaises(AttributeError):
            availability.device_supported = False


@unittest.skipUnless(_NATIVE is not None, "compiled P08 _sdr_native module is unavailable")
class CudaExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.native = _NATIVE
        cls.cuda_skip_reason = _cuda_skip_reason()

    def setUp(self) -> None:
        if self.cuda_skip_reason is not None:
            self.skipTest(self.cuda_skip_reason)

    def _dsp_config(
        self,
        fft_size: int,
        batch_size: int,
        unit: object | None = None,
        precision: object | None = None,
    ) -> object:
        native = self.native
        return native.DspConfig(
            fft_size,
            fft_size,
            native.WindowType.HANN,
            native.DetectorType.SAMPLE,
            unit or native.SpectrumUnit.DBFS_HZ,
            precision or native.PrecisionMode.REFERENCE_F64,
            batch_size,
            1,
            8.6,
            native.CalibrationStatus.UNCALIBRATED,
            "",
            CONTRACT_SCHEMA_VERSION,
        )

    def _make_backend(self, preference: object) -> object:
        return self.native.make_dsp_backend(
            self.native.DspBackendSelectionOptions(preference=preference)
        )

    def test_forced_cuda_and_self_test(self) -> None:
        native = self.native
        self_test = native.run_backend_self_test(native.ComputeBackendKind.CUDA)
        self.assertTrue(self_test.self_test_passed, self_test.details)
        backend = self._make_backend(native.ComputeBackendKind.CUDA)
        info = backend.info()
        self.assertEqual(info.kind, native.ComputeBackendKind.CUDA)
        self.assertEqual(info.vendor, "NVIDIA")
        self.assertIn("cuFFT", info.fft_library)

    def test_auto_selection(self) -> None:
        native = self.native
        small = self._make_backend(native.ComputeBackendKind.AUTO)
        small.configure(self._dsp_config(256, 1))
        self.assertEqual(small.info().kind, native.ComputeBackendKind.CPU)
        large = self._make_backend(native.ComputeBackendKind.AUTO)
        large.configure(self._dsp_config(4096, 16))
        self.assertEqual(large.info().kind, native.ComputeBackendKind.CPU)

    def _run_cuda_backend(
        self,
        samples: np.ndarray,
        rate: float,
        center: float,
        unit: object,
        precision: object | None = None,
    ) -> tuple[object, object]:
        backend = self._make_backend(self.native.ComputeBackendKind.CUDA)
        backend.configure(self._dsp_config(1024, 1, unit, precision))
        backend.push_samples(samples, rate, center)
        return backend.poll_spectrum(0), backend.metrics()

    def _assert_golden_parity(self, path: Path, precision: object | None = None) -> None:
        native = self.native
        with np.load(path) as vector:
            samples = np.ascontiguousarray(vector["input_iq"])
            rate = float(vector["sample_rate"])
            center = float(vector["center_frequency"])
            expected_psd = vector["expected_psd"]
            expected_peak = float(vector["expected_peak"])
            expected_frequency = float(vector["expected_frequency"])
            expected_axis = vector["expected_frequency_axis"]
            expected_integrated = float(vector["expected_integrated_power"])

        frames, metrics = self._run_cuda_backend(
            samples, rate, center, native.SpectrumUnit.DBFS_HZ, precision
        )
        self.assertEqual(metrics.active_backend, native.ComputeBackendKind.CUDA)
        self.assertTrue(metrics.backend_self_test_passed)
        self.assertEqual(len(frames), 1)
        self.assertEqual(metrics.fft_frames_computed, 1)
        frame = frames[0]
        np.testing.assert_array_equal(
            np.asarray(frame.frequencies_hz),
            expected_axis,
            err_msg="frequency axis must match exactly",
        )
        actual_psd = np.power(10.0, np.asarray(frame.values, dtype=np.float64) / 10.0)
        np.testing.assert_allclose(
            actual_psd,
            expected_psd,
            rtol=PSD_RTOL,
            atol=PSD_ATOL,
        )
        bin_width = rate / 1024.0
        integrated = float(actual_psd.sum() * bin_width)
        self.assertAlmostEqual(
            integrated,
            expected_integrated,
            delta=abs(expected_integrated) * INTEGRATED_RTOL,
        )

        frames_bin, _ = self._run_cuda_backend(
            samples, rate, center, native.SpectrumUnit.DBFS_BIN, precision
        )
        self.assertEqual(len(frames_bin), 1)
        values_bin = np.asarray(frames_bin[0].values)
        peak_bin = int(np.argmax(values_bin))
        self.assertAlmostEqual(
            float(values_bin[peak_bin]), expected_peak, delta=PEAK_DB_TOLERANCE
        )
        self.assertEqual(
            float(np.asarray(frames_bin[0].frequencies_hz)[peak_bin]),
            expected_frequency,
            "frequency-bin identity must be exact",
        )

    def test_golden_parity_cuda(self) -> None:
        files = _golden_files()
        self.assertEqual(len(files), 12)
        for path in files:
            with self.subTest(vector=path.name):
                self._assert_golden_parity(path)

    def test_golden_parity_cuda_accurate_f32(self) -> None:
        native = self.native
        files = {
            path.stem: path for path in _golden_files() if path.stem in ACCURATE_PARITY_VECTORS
        }
        self.assertEqual(sorted(files), sorted(ACCURATE_PARITY_VECTORS))
        for name, path in files.items():
            with self.subTest(vector=name):
                self._assert_golden_parity(path, native.PrecisionMode.ACCURATE_F32_F64_ACCUM)


class CliBackendReportingTests(unittest.TestCase):
    def _run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        return subprocess.run(
            [sys.executable, "-m", "esw_dfl.sdr.cli", *args],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def test_cli_backend_reporting(self) -> None:
        devices = self._run_cli("devices")
        self.assertEqual(devices.returncode, 0, devices.stdout + devices.stderr)
        payload = json.loads(devices.stdout)
        self.assertIn("devices", payload)
        self.assertIsNone(_UNIQUE_ID_PATTERN.search(devices.stdout))

        fixed_help = self._run_cli("fixed", "--help")
        self.assertEqual(fixed_help.returncode, 0, fixed_help.stdout + fixed_help.stderr)
        self.assertIn("--backend", fixed_help.stdout)
        self.assertIn("--no-runtime-fallback", fixed_help.stdout)

    def test_no_device_unique_data_in_cli(self) -> None:
        devices = self._run_cli("devices")
        self.assertEqual(devices.returncode, 0, devices.stdout + devices.stderr)
        for entry in json.loads(devices.stdout)["devices"]:
            self.assertIsNone(_UNIQUE_ID_PATTERN.search(entry["uri"]))
            self.assertIsNone(_UNIQUE_ID_PATTERN.search(entry["description"]))


if __name__ == "__main__":
    unittest.main()
