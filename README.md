# SDR Native Monitoring

Windows-first tools for read-only Rohde & Schwarz DFL analysis and a bounded C++20/Python SDR processing core.

The project provides spectrum and waterfall inspection, engineering measurements, native CPU/CUDA DSP boundaries, fixed-band SDR plumbing, calibration contracts, and cancellable background work. Live SpectrumFrame measurements are unit-aware and retain frame/config provenance plus quality warnings. Source DFL and RF recordings are never modified or committed. P12 adds bounded wide-span sweep planning and sequential execution with explicit segment timing, cancellation, and missing-segment results. P13 adds linear-power full-span stitching with explicit target grids, overlap correction, per-bin quality/uncertainty/source maps, seam evidence, and a GUI adapter that displays stitched sweeps without fabricating missing segments. The Wideband Sweep workspace turns this into a plan-before-run workflow: a range editor and expert controls, a segment diagram with coverage check, ETA and throttled progress, a seam/quality view, profile presets, and a result spectrum rendered directly from the stitched frame — with missing segments always explicit, and a deterministic fake service for offline runs. The Offline DFL workspace migrates the read-only DFL workflow into the new AppShell: session loading, frame navigation and playback, markers, heatmap/persistence controls, channel-power results and exports, with the presenter owning renderers, frame loading and measurement math while the widget stays a thin snapshot renderer.


P16UI-07 adds an offline-testable calibration workspace with profile browsing, applicability comparison, validated CSV preview, immutable finalization, correction/uncertainty plots, and an active-profile safety gate. Measurement cards and the bottom panel retain value, unit, quality, uncertainty, frame/config, timestamp, source, calibration status, and warnings; mixed-frame results are rejected and raw dBFS is never relabeled as dBm.

P16UI-08 adds recording, replay and diagnostics workspaces: bounded I/Q and spectrum capture with disk-forecast gating, a live health view, atomic `.part` recovery, replay of recorded streams with explicit pause/seek and CPU/CUDA reprocess, plus a diagnostics workspace that runs the safe native self-test, offline P15 validation in a cancellable worker, and exports an anonymized support bundle without exposing I/Q, calibration or private paths.

P16UI-09 adds keyboard navigation (Ctrl+1..7) for all workspaces, a bounded notification store with a tracked dropped count, settings schema versioning with deterministic recovery/reset, visible focus and the existing high-contrast theme, offscreen DPI-matrix tests, a 60 Hz workspace-switch budget assertion, and repeat lifecycle checks.

P16UI-10 adds package metadata (`[project]`, license file, pinned version), the Jetson `linux-aarch64-cuda` CMake preset scaffold, and release acceptance smoke tests. Packaging remains a bounded build out of the repository; field Jetson runtime is explicitly NOT_VERIFIED until executed on hardware.

## Engineering invariants

Input DFL and measurement recordings are always read-only. Every queue and cache has a finite bound. Python is not called for each sample or each FFT. GUI refresh rate is independent from analytical processing rate. dBm is not emitted for live SDR data without applicable calibration. CPU remains a supported reference backend.
P14 adds streaming SigMF-compatible IQ recording, versioned spectrum JSONL recording, explicit gap/drop sidecars, bounded backpressure, atomic recovery, disk forecasts, and replay into the native DSP boundary. Status: development. Real-device validation remains hardware-dependent. P15 adds a deterministic offline validation/benchmark CLI with CSV/JSON/log/plot evidence; CUDA, Pluto hardware, reference-equipment accuracy, and long soaks are reported as NOT_VERIFIED until run explicitly.

## Quick start

~~~powershell
python -m unittest discover -s tests -v
python main.py
~~~
