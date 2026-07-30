# SDR Native Monitoring

Windows-first tools for read-only Rohde & Schwarz DFL analysis and a bounded C++20/Python SDR processing core.

The project provides spectrum and waterfall inspection, engineering measurements, native CPU/CUDA DSP boundaries, fixed-band SDR plumbing, calibration contracts, and cancellable background work. Live SpectrumFrame measurements are unit-aware and retain frame/config provenance plus quality warnings. Source DFL and RF recordings are never modified or committed.

Status: development. Real-device validation remains hardware-dependent.

## Quick start

~~~powershell
python -m unittest discover -s tests -v
python main.py
~~~
