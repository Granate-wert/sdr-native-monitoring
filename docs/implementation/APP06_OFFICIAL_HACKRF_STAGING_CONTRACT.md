# APP-06: official HackRF staging boundary

2026-09-27 source checkpoint. APP-06 is PARTIAL; this is not common UI,
packaged-runtime or release acceptance. Product target remains UI V2.

## Explicit isolated build

`build_native_sdr.ps1 -StageOnly -Lane CPU -PythonExecutable <Python3.13>`
with both `-HackrfIncludeDirectory <directory-containing-hackrf.h>` and
`-HackrfLibrary <full-import-library-path>` selects the separate
`windows-msvc-cpu-hackrf` CMake build directory. CPU Release and StageOnly
are mandatory for this initial integration. Incomplete paths/runtime bundles,
CUDA/Debug/activation requests or StageOnly+Clean fail before compilation.

Default CPU/CUDA presets explicitly disable the official factory. The staged
candidate never replaces the active application extension or its manifest.
Normal native tests, dependency/header freshness and contract preflight are
still mandatory. Staged app-local `hackrf.dll`, `libusb-1.0.dll` and
`pthreadVC3.dll` must match the chosen SDK bundle by SHA-256. Manifest records
their hashes plus header/import-library identity. Preflight checks callable
factory presence and private factory protocol version2 against the manifest
without constructing an RX owner. Main Spectrum wire schema remains5.

Build/manifest presence is runtime availability, not hardware capability or
physical support. Package closure, activation and a common Analyzer device
selection remain separate APP-06B/C gates.

## Bounded physical probe

`scripts/probe_app06_hackrf_native_maxfs.py` requires explicit `--rx`, one
official staged module/manifest and a new output path. Measurement5..120s
follows3s warmup. Existing capability observation, current enumeration
identity preflight and issued permit precede the native factory. The profile
requests20MS/s,100MHz center,15MHz baseband filter,LNA16/VGA20,4096FFT/
2048hop, CPU reference-f64, no persistence, amplifier and bias OFF.

Only reduced spectrum frames and scalar counters cross to Python. Native
ingress and analytical FFT counters use deltas over the same measured time;
resets/invalid types reject rather than become zero. Frame contract, sequence,
source/unit/generation and observed software drop flags are checked. Finally
always requests native Stop and records callbacks, queues, slots, join and
lifecycle closure. A successful short test cannot prove long-term memory,
RF continuity, device overrun absence or calibrated amplitude. Requested/
API-accepted sample rate is not labelled independent hardware Fs readback.

The private issued permit carries four serial words without exposing them in
UI diagnostics. The Python factory requires protocol2 and passes that
expectation to native code. After opening exactly one HackRF One, native
reads its serial on that SAME handle before any RF configuration or RX start.
Mismatch is fail-closed; cleanup preserves a failed-close handle for the
destructor's retry. Manual C++ tools may omit this optional expectation, but
the product Python factory cannot. Old protocol1 extensions are unavailable,
not silently adapted; their rejection does not consume the permit.

`--identity-negative` first substitutes an intentionally wrong expectation
only in a diagnostic native call. It requires the exact native identity-open
failure, then obtains a fresh permit and performs normal bounded RX. This
tests the physical comparison/cleanup path, not an actual USB device swap.
Any unexpectedly returned owner is stopped and the negative gate fails.

Native artifact and Python runner source commits/hashes are independent.
Do not claim that a script-only change rebuilt the staged extension. Do not
confuse frames polled or FFT/s with Qt/DWM FPS or Waterfall LPS.

## Common integration still required

- Preserve physical opaque identity and aliases in the common catalog;
  declared, runtime-present and hardware-verified support are distinct.
- The same-handle serial guard does not implement selection among several
  HackRF devices. Exactly-one restriction remains; common/multi-device
  Supported claims need explicit catalog, selection and owner-routing tests.
- HackRF requests retain separate LNA/VGA/amp/bias and 8-bit I/Q semantics;
  never reinterpret Pluto's aggregate gain as these controls.
- One owner must confirm Stop/release before another source starts; late
  frames cannot cross source/session/epoch/unit boundaries.
- Shared Spectrum/Waterfall UI V2 composition is not implemented by a
  standalone factory smoke. tinySA retains device-reported calibrated dBm,
  instrument sweep/points/RBW and the10001-point UI cap, without fabricated
  I/Q, RTBW, host FFT metadata or progressive observations from serial chunks.

Detailed local ignored measurements/JSONs are separate from this published
source contract and are not automatically included in Git pushes.
