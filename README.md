# SDR Native Monitoring

Windows desktop foundation for standalone software-defined-radio monitoring.

- `sdr-native-monitoring` starts the independent SDR application.
- `dfl-analyzer` remains the separately maintained legacy measurement-container tool.

The SDR launch path contains no DFL parser, spectrogram decoder, or DFL GUI dependency.

## S05 Home/Live UI

The standalone UI provides a short Home-to-Live workflow with device discovery
(USB, IP, or a manual URI), capability-aware controls, requested/applied values,
profile storage, bounded 60 Hz presentation, and explicit dBFS quality status.
Native/libiio Pluto discovery and connection probing are enabled in the standalone
service; native RX frame streaming and numeric spectrum rendering remain follow-up
work, while the safe in-memory path remains available for UI validation.

## S06 Wideband Sweep UI

The standalone Sweep workspace now provides Fast/Balanced/Precise planning,
plan preview, stale-plan protection, cancellable background execution, explicit
unknown seam/calibration quality, and atomic JSON summary export. The bundled
in-memory service is safe for UI validation; hardware sweep acquisition and
numeric full-span rendering remain follow-up work.

## S07 Calibration and Measurements

The standalone Calibration workspace provides immutable profile versions, CSV
preview/finalize, applicability rows, correction/uncertainty visualization,
explicit expert override for incompatible settings, and measurement cards that
always show unit, quality, and uncertainty. Uncalibrated or incompatible data
remains in `dBFS/bin`; absolute `dBm` is blocked until a valid profile applies.

## S08 Live Recording

The standalone recording path accepts live IQ and Spectrum publications through
a bounded non-blocking tee. IQ-only, spectrum-only, or combined recordings
persist source/config metadata, queue drops and gaps, finalize through a `.part`
file, and retain failed partials for recovery. No synthetic producer is started
by the production composition root.

## S09 Replay and Reprocess

Recordings have an indexed reader with physical byte-offset seek, play/pause,
0.25x–8x ReplayClock, shared frame publication, and asynchronous IQ reprocess.
A requested CUDA reprocess reports a visible CPU fallback when CUDA is
unavailable; cancellation and replay position remain explicit.

## S10 Diagnostics and Support

Diagnostics runs in bounded worker tasks, exposes CPU/CUDA/Pluto environment
cards, explicit RX-only confirmation, a bounded error center, cancellation,
and a support bundle redacted by default. Raw IQ/calibration data and private
paths are not included.

## S11 Accessibility, DPI and Performance Validation

The standalone UI includes separate-process DPI probes for 100%, 200% and
300%, accessible-name/focus auditing, keyboard shortcut collision detection,
and bounded 60 Hz p50/p95 plus memory-plateau instrumentation. Validation is
measurement-only and does not reduce analytical publication rate.

## S12 Windows Release Packaging

`build_sdr_release.ps1` freezes a standalone `SDRNativeMonitoring.exe` with
Python 3.13 ABI policy, CPU/CUDA lanes, native artifact preflight, forbidden
DFL/spectrogram-module checks, and SHA-256 release manifest generation.
The local CPU and CUDA lanes both build with Python 3.13, include the ABI-matched
`_sdr_native` extension in the PyInstaller package, and pass release preflight.
Real Pluto hardware, Jetson/aarch64 runtime, clean-machine startup, and long soak
remain explicit NOT_VERIFIED acceptance gates.

## S13 Jetson Orin NX build path

The native CMake layer now selects POSIX Pluto/libiio and cuFFT loaders on Linux,
keeps CUDA architecture `87` explicit for Jetson Orin NX, and provides native
AArch64 CPU/CUDA presets that can build the `_sdr_native` extension with Python
3.13. Windows CPU/CUDA native builds and CTest pass locally; Jetson/aarch64 build/import
and Pluto RX hardware acceptance remain `NOT_VERIFIED` until executed on target
hardware.

## S14 P17 final acceptance

The standalone S00–S14 acceptance matrix is reproducible and currently reports
71/71 targeted tests passing. Windows CPU and CUDA native/release lanes are verified
locally; the release verdict remains `ACCEPT WITH GAPS` because Jetson/aarch64,
real Pluto hardware, clean-machine startup, screenshots and long soak are
environment-dependent acceptance gates.
