# SDR Native Monitoring

Windows-first tools for read-only Rohde & Schwarz DFL analysis and a bounded C++20/Python SDR processing core.

The project provides spectrum and waterfall inspection, engineering measurements, native CPU/CUDA DSP boundaries, fixed-band SDR plumbing, calibration contracts, and cancellable background work. Live SpectrumFrame measurements are unit-aware and retain frame/config provenance plus quality warnings. Source DFL and RF recordings are never modified or committed. P12 adds bounded wide-span sweep planning and sequential execution with explicit segment timing, cancellation, and missing-segment results. P13 adds linear-power full-span stitching with explicit target grids, overlap correction, per-bin quality/uncertainty/source maps, seam evidence, and a GUI adapter that displays stitched sweeps without fabricating missing segments.

Status: development. Real-device validation remains hardware-dependent.

## Quick start

~~~powershell
python -m unittest discover -s tests -v
python main.py
~~~
