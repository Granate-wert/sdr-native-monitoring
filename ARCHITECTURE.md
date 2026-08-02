# SDR Native Monitoring Architecture

This document describes the accepted P07 headless fixed-band pipeline and the
implemented, locally verified P08/P08H-00 CUDA portability worktree. The live
Pluto acceptance gate remains hardware- and review-dependent. It covers the
offline DFL analyzer, portable SDR contracts and their boundaries.

## 1. Goals

The architecture is designed for:

- safe analysis of large Rohde & Schwarz DFL measurement containers;
- high-rate live or recorded I/Q ingestion through replaceable backends;
- deterministic CPU reference processing;
- bounded memory and observable loss under overload;
- independent analytical and GUI update rates;
- future Windows and Linux/AArch64 deployment.

It is not designed to treat Python or Qt as the high-rate sample-processing
loop.

## 2. Current implementation boundary

### Implemented

- read-only DFL parsing and streamed waterfall access;
- normalized offline measurement domain;
- spectrum measurements, heatmap persistence and export;
- PySide6/PyQtGraph desktop application;
- C++20 SDR contract core and pybind11 boundary;
- Python fallback and native compatibility checks;
- deterministic synthetic I/Q sources;
- NumPy reference FFT/power calculation and golden vectors;
- bounded native queues, buffer pool, engine lifecycle, cancellation and
  metrics transport;
- portable CPU DSP backend (unpack, DC removal, overlap, windows, FFT,
  power/PSD, detectors) with golden-verified numerics;
- Windows Pluto/AD936x RX discovery, configuration/readback and bounded libiio
  refill, exposed through a Python control-plane service.
- headless Pluto acquisition → bounded native queue → CPU DSP orchestration,
  with rate-limited latest-wins Python snapshots, metrics, reconfigure and CLI;
- optional CUDA/cuFFT target, generic backend selection, availability/error
  contracts, canonical Pluto Int12 decoding, epoch/timestamp isolation,
  bounded transactional fallback and CPU/CUDA parity/benchmark gates;
  production live acceptance remains hardware- and review-gated.

### Planned, not yet production implementations

- receiver backends other than Pluto/libiio;
- live acquisition/DSP integration in the desktop application;
- live sweep scheduling;
- raw I/Q recording and replay;
- GUI calibration wizard and live calibrated measurement integration;
- production live CUDA sign-off after connected-Pluto acceptance and independent
  review;
- live SDR integration in the desktop GUI.

## 3. System context

```text
                    +--------------------------+
recorded DFL ------>| read-only DFL subsystem  |
                    +------------+-------------+
                                 |
                                 v
                    +--------------------------+
                    | normalized spectrum data |
                    +------------+-------------+
                                 |
Pluto or future SDR -> bounded I/Q -> native DSP |
                                 |
                                 v
                  analysis / heatmap / recording
                                 |
                                 v
                      rate-limited GUI snapshots
```

DFL and SDR acquisition share downstream spectral concepts, not storage
parsers. A DFL spectrum frame is never misrepresented as raw I/Q.

## 4. Layer boundaries

### 4.1 Container and codec

`esw_dfl/parser.py` opens the compound file read-only, inventories streams and
dispatches structural handlers. `esw_dfl/codec.py` owns Base64, endian-aware
numeric and timestamp decoding. Neither module imports GUI code.

### 4.2 Large spectrogram access

`esw_dfl/spectrogram.py` provides:

- bounded preview sampling;
- frame metadata and timestamp indexing;
- sector-oriented random access;
- exact single-frame decoding;
- cancellation and progress hooks.

The entire time-frequency matrix is not materialized for normal viewing.

### 4.3 Domain and repository

Parser models in `esw_dfl/models.py` preserve source facts. The adapter maps
them into GUI-independent entities in `esw_dfl/domain.py`, stored by
`esw_dfl/repository.py`.

The domain carries frequency axes, timestamps, units, detector/update modes,
source identity and quality metadata. Widgets do not own the authoritative
numeric state.

### 4.4 Analysis

`esw_dfl/processing.py`, `esw_dfl/power_measurements.py` and
`esw_dfl/time_gated_power.py` contain unit-aware calculations. Power-domain
aggregation converts logarithmic values to linear power before integration or
averaging.

These modules consume normalized arrays rather than XML nodes or Qt objects.

### 4.5 Heatmap persistence

`esw_dfl/heatmap.py` defines density accumulation and quantization.
`esw_dfl/heatmap_persistence.py` and
`esw_dfl/heatmap_persistence_controller.py` own stateful rolling behavior,
generation control and applied snapshots. `esw_dfl/heatmap_worker.py` performs
bounded background rebuilds; `esw_dfl/heatmap_export.py` writes reusable
artifacts.

Sequential playback updates the rolling state incrementally. Initial build,
seek rebuild and fixed ranges use bounded worker jobs. Results from stale
generations are discarded.

### 4.6 Presentation and orchestration

`esw_dfl/renderers.py` owns reusable PyQtGraph items. `esw_dfl/gui.py`
coordinates user commands, session selection, playback, workers and atomic UI
commits. `esw_dfl/workers.py` supplies cancellable background-task plumbing.

The GUI renders snapshots at its configured rate. It is not the analytical
clock and must not silently determine which source frames are processed.

### 4.7 Export and logging

Export modules write large results through a temporary `.part` file and rename
only after successful completion. `esw_dfl/activity_log.py` uses a bounded
asynchronous JSONL writer and avoids copying bulk measurement arrays into log
records.

## 5. Native SDR core

`native/sdr_core/` is a portable C++20 library. Its common target must not
depend on:

- Python or pybind11;
- Qt;
- WinAPI;
- libiio or a specific receiver SDK;
- CUDA.

Those dependencies belong in explicit boundary targets or future adapters.

### 5.1 Python boundary

The `_sdr_native` module exposes coarse-grained operations and immutable
contracts. Long native work releases the GIL. Arrays exported to Python are
read-only views whose lifetime is tied to a native ownership capsule.

`esw_dfl/sdr/native_api.py` validates native API/schema compatibility and
provides a deterministic fallback when the module is absent.

### 5.2 Canonical contracts

The current contracts cover:

- source identity and source type;
- device state and capabilities;
- immutable acquisition configuration;
- timestamped `IqBlock`;
- `SpectrumFrame` and sweep spectrum frames;
- spectrum unit;
- quality flags;
- queue and processing metrics;
- schema/API version.

Wire enum values are tested for exact C++/Python agreement.

### 5.3 Synthetic/reference path

`esw_dfl/sdr/synthetic.py` generates deterministic complex float64 inputs.
`esw_dfl/sdr/reference_dsp.py` is a transparent numerical oracle rather than a
throughput implementation. Golden NPZ fixtures validate signal generation,
frequency axes, window normalization and spectrum semantics.

The native synthetic source skeleton tests ownership and boundary behavior but
does not claim to be a production receiver or DSP pipeline.

### 5.4 Bounded transport and engine lifecycle (P04)

The common core provides the production transport primitives for the live
pipeline:

- `BoundedQueue<T>` with fixed capacity and explicit overflow policies
  (`block`, `drop_newest`, `drop_oldest`, `latest_wins`); every drop and every
  abandoned item is counted exactly and never hidden;
- `BufferPool`, a preallocated pool of fixed-size blocks handed out through
  reference-counted handles that return their slot when the last owner
  releases them; pool blocks convert to the P02 `IqBlock` storage contract
  without copying;
- `SyntheticEngine`, a state machine (created → configured → running →
  stopping → stopped/error) driving a deterministic synthetic producer, an
  acquisition ring, a DSP input queue, an optional recorder tee and bounded
  diagnostic events;
- cooperative cancellation: a stop request wakes every blocked wait, worker
  threads are always joined, and the engine destructor never leaves a thread
  behind;
- lock-free atomic metrics counters sampled into the P02 `EngineMetrics`
  snapshot; Python polls coarse-grained snapshots, events and metrics — never
  per-block callbacks.

P04 itself introduced no FFT: at that package boundary the consumer performed
transport accounting only. The P05 layer below now supplies the production CPU
FFT path without changing the bounded transport contract.

### 5.5 CPU DSP backend (P05)

`CpuDspBackend` implements the live pipeline behind a replaceable
`DspBackend` contract (`configure / push_iq / poll_spectrum / reset /
metrics`):

- I/Q unpack for AD936x signed int12-in-int16, `ci16_le` and `cf32_le` with
  strict normalization and non-finite rejection;
- optional block-mean DC removal (flagged in frame metadata);
- overlap assembly, six symmetric windows with coherent gain and ENBW;
- a replaceable `FftProvider` (vendored pocketfft, BSD-3-Clause);
- linear-domain power/PSD and detectors, dB conversion only on output;
- `SpectrumFrame` emission with fftshift frequency axis, quality flags and
  loss counters;
- gap handling: a stream discontinuity rebases the assembler so frames never
  stitch stale data across a gap.

The P04 engine consumer runs this stage on every analytical block,
independent of the GUI snapshot rate, and publishes bounded latest-wins
spectrum frames plus FFT metrics. Numerics are verified against the P03
golden vectors inside documented tolerances (PSD rtol 2e-5, peak ±5e-5 dB,
exact bin identity).

### 5.6 Pluto/libiio RX backend (P06)

The Windows-only `sdr_pluto` target is intentionally separate from the
portable `sdr_core` target. It loads `libiio.dll` at runtime, first from an
explicit `LIBIIO_DLL_PATH`, then from the standard IIO Oscilloscope runtime
location, then from the loader search path. A missing or incompatible runtime
therefore produces a controlled backend-unavailable result without making the
common CPU library non-portable.

The backend supports explicit `usb:` and `ip:` URIs plus scan-based discovery.
It finds the AD936x PHY, RX streaming device, I/Q input channels and RX LO by
known names with structural fallback. No TX streaming channel is enabled and
all buffers are created non-cyclic.

Configuration is a logical transaction:

1. validate the canonical immutable `DeviceConfig` and buffer bounds;
2. capture the current hardware attributes;
3. write sample rate, analog bandwidth, RX LO, gain mode and manual gain;
4. read back the applied values and validate the sample layout;
5. allocate the bounded output pool (eight blocks for standalone use; P07 derives
   a larger fixed bound from its acquisition queue capacity);
6. publish the new applied configuration and generation only after every prior
   operation succeeds;
7. on failure, best-effort restore the captured hardware attributes while the
   prior published configuration remains authoritative.

Each successful refill emits one ordered `IqBlock`. Driver-format samples are
normalized into signed 12-bit values stored as little-endian int16 I/Q pairs;
metadata retains storage bits, significant bits, driver shift and endianness.
The block carries source sequence, first sample index, an explicitly estimated
host timestamp, applied configuration generation and quality flags. Returned
sample storage belongs to a bounded native pool and remains immutable in
Python. Stream metrics use a separate short-held mutex, so low-rate metrics
polling never waits behind a blocking libiio refill.

`iio_buffer_cancel()` is callable without taking the mutex held by a blocking
refill. Stop and disconnect cancel first, then destroy the non-reusable
cancelled buffer, disable only RX I/Q channels and invalidate context handles.
Python calls release the GIL. The application must run refill in a worker
thread; GUI-thread device I/O remains forbidden.

The backend reports blocks/samples, short reads, refill errors, output-pool
exhaustions, output blocks dropped and estimated dropped samples. Once libiio
has returned a block, sequence/sample indices advance even if all eight output
slots are retained; that consumed block is counted as dropped so the next
delivered block exposes a gap. The current driver path has no hardware overflow
counter, so zero estimated drops is not proof of zero device/USB loss.
Capability `sampling_frequency_available` is never reported as measured
transport throughput. `benchmark_pluto_rx.py` supplies reproducible RX-only
rate and cancellation evidence without exposing device serials.

### 5.7 Headless fixed-band engine (P07)

`FixedBandEngine` belongs to the Windows-only `sdr_pluto` target, not the
portable common library. One instance owns exactly one `PlutoDevice`, one
bounded acquisition queue, one selected `DspBackend`, one bounded latest-wins
spectrum queue and one bounded diagnostic-event queue. The accepted P07
configuration is CPU; the P08 CUDA selection path is experimental.

The high-rate path is entirely native:

```text
PlutoDevice::refill (acquisition thread)
  -> bounded IqBlock queue (explicit overflow policy)
  -> selected DspBackend::push_iq / poll_spectrum (DSP thread)
  -> every admitted analytical FFT is accounted
  -> wall-clock rate limiter selects the latest immutable SpectrumFrame
  -> bounded latest-wins Python polling boundary
```

`FixedBandConfig` combines canonical `DeviceConfig` and `DspConfig` with hard
queue capacities, a publication limit (60 Hz by default), transient-block count
and optional block-mean DC removal. The Pluto output pool is derived from the
acquisition queue bound plus in-flight ownership, preventing the engine itself
from exhausting the standalone eight-block pool.

Snapshot replacement is not analytical FFT loss. Native metrics therefore keep
I/Q loss, DSP FFT loss, expected shutdown discard and superseded Python
snapshots separate. Exact queue-eviction callbacks account the sample count of
the item that actually left the queue, including DropOldest and LatestWins.
Device metrics are sampled without contending with blocking refill. Python
exposes only lifecycle, reconfigure, metrics/events and batched snapshot
polling; there is no per-block or per-FFT callback.

The diagnostic queue has a fixed main capacity plus one bounded priority slot
used only when an Error/Critical event cannot enter the main queue. Replacing
that pending priority event is counted as a real diagnostic loss. Error and
Critical severity latch `has_error`, so health cannot become falsely green
after the lifecycle state settles from Error to Stopped. A terminal
`disconnect()` invalidates the applied configuration and prevents a concurrent
or later `start()` from reviving the same engine instance.

Every FFT frame receives a timestamp derived from the source block timestamp
plus its first-sample offset divided by the applied sample rate. This preserves
relative time for overlapping FFTs within one I/Q block; the absolute timestamp
remains explicitly host-estimated because P06/P07 do not claim a hardware clock.

Reconfigure is an explicit stop → apply/readback → rebuild queues/DSP → discard
configured transient blocks → resume transition. Hardware-applied values and
`config_generation` remain authoritative, and a late frame from an older
generation cannot cross the rebuilt snapshot queue.

The headless CLI (`python -m esw_dfl.sdr.cli`) provides `devices` and `fixed`
commands. It reports applied settings, CPU backend, analytical rate, distinct
loss counters, latency, peak data and health without printing device serials.
GUI integration remains P10 scope.

### 5.8 CUDA backend and P08H-00 portability boundary

The worktree adds `ComputeBackendKind`, generic availability/error structures,
an optional isolated `sdr_cuda` target, CUDA stream/event/buffer RAII, kernels,
a bounded cuFFT plan cache and CPU/CUDA/AUTO selection. CUDA SDK types remain
under `include/sdr_cuda` and `src/cuda`; CPU builds use a controlled link stub.

The remediation accepts Pluto's canonical `ComplexInt12InInt16Le` with 2048
full scale, emits per-FFT sample-offset timestamps, isolates generation/rate/
retune epochs, accounts staged/partial loss, and uses bounded transactional
fallback with monotonic public frame sequences. CPU and CUDA publish through
separate staged artifacts and are checked by a build manifest/preflight. AUTO is
conservative until a valid local comparative benchmark justifies acceleration;
the current policy is CPU-safe by default, with forced CUDA available when
requested. Connected-Pluto acceptance and independent review remain open.

## 6. Target live data flow

```text
device backend
  -> bounded pool of I/Q blocks
  -> bounded acquisition queue
  -> native window/FFT/detector/calibration pipeline
  -> bounded SpectrumFrame queue
  -> analytical consumers
       -> persistence / detection / measurement / recording
  -> latest-snapshot exchange
  -> GUI renderer
```

Backpressure policy must be explicit for every edge. When loss is unavoidable,
the producer or queue increments counters and marks the discontinuity in the
next observable record.

## 7. Ownership and concurrency

- A source backend owns device handles.
- A bounded native pool owns reusable sample storage.
- Each queue transfer has an explicit ownership rule.
- Python receives batches or immutable snapshots, never per-sample callbacks.
- The GUI thread owns widgets only.
- Worker results carry session/source identity and generation.
- A result is applied only when its identity and generation remain current.
- Stop, reconfigure, source switch and shutdown cancel or drain work according
  to an explicit state transition.

Unbounded queues and silent overwrite are forbidden.

## 8. Time and sequence semantics

Every acquisition block or spectrum frame should carry:

- monotonic sequence;
- source clock timestamp where available;
- host monotonic timestamp;
- discontinuity/loss flags;
- applied configuration generation.

Recorded DFL playback follows recorded timestamps when available. Instrument
sweep time is metadata about acquisition, not a promise that Python/Qt can
render every acquired frame.

Analytical throughput and display throughput are measured separately:

- source frames/s and samples/s;
- FFT or spectrum frames/s;
- analytical frames accepted/dropped;
- rendered snapshots/s;
- queue occupancy and lag;
- cancellation latency.

## 9. Units and calibration

Internal frequencies use Hz and durations use seconds or explicit nanoseconds.
Spectrum units are never inferred from the shape of an array.

Reference calculations distinguish bin power and power spectral density.
Window coherent gain and equivalent noise bandwidth are explicit.

Live SDR output may remain in dBFS/bin or dBFS/Hz without a calibration
profile. Conversion to dBm requires a profile applicable to device identity,
frequency, gain, bandwidth, temperature context and configuration generation.
The result retains calibration provenance.


### 9.1 P09 calibration core

P09 profiles are finalized, versioned and immutable. esw_dfl/sdr/calibration_store.py
validates the identity signature (serial, backend, RF path, sample rate, analog
bandwidth, gain, window normalization, FFT unit, frontend chain, temperature
context and reference plane), stores JSON atomically through a .part file and
rejects incompatible settings. The native CalibrationCurve uses linear dB
interpolation and has an explicit extrapolation flag; the Python applier caches
correction/uncertainty arrays by profile, settings and frequency grid.

A missing or incompatible profile returns dBFS values unchanged and never labels
them dBm. P09 does not control a reference generator, modify live GUI state or
claim laboratory accuracy. Those operations remain outside this bounded package.
## 10. Error model

Expected errors cross the Python boundary as stable categories, including:

- invalid configuration;
- unsupported capability;
- incompatible API/schema;
- source unavailable;
- timeout or cancellation;
- overflow/data loss;
- internal failure.

Exceptions must not escape destructors or native worker threads. User-visible
errors are logged without exposing credentials or bulk measurement contents.

## 11. Portability

The common core uses standard C++20 and fixed-width types. Platform-specific
thread priority, device discovery and dynamic-library loading belong in
adapters.

Windows is the currently verified development target. Linux x64/AArch64 files
express the intended build boundary but remain unverified until run on those
targets. Jetson deployment must retain a complete CPU path even if CUDA is
enabled.

## 12. Extending the project

### New recorded measurement family

1. Detect structure rather than a specific filename.
2. Add a GUI-independent normalized model.
3. Implement and register a parser handler.
4. Add analysis/render/export consumers of the model.
5. Test small generated fragments and read-only behavior.

### New SDR backend

1. Implement capability discovery and readback.
2. Map settings into canonical immutable configuration.
3. Produce bounded, timestamped I/Q blocks.
4. Report sequence gaps, overflow and device errors.
5. Keep device SDK types out of the common core.
6. Add a deterministic replay or mock backend for tests.
7. Validate calibration applicability separately from device reception.

### New acceleration backend

1. Keep CPU result semantics authoritative.
2. Put backend selection behind a capability/configuration boundary.
3. Compare axes, normalization and detector output numerically.
4. Record transfers, synchronization and fallback behavior.
5. Never make optional hardware a requirement for basic operation.

## 13. Verification strategy

- generated XML/binary fixtures for parser and codec behavior;
- deterministic synthetic I/Q and golden numerical vectors;
- C++ unit tests for native contracts and ownership;
- Python unit/contract tests for fallback and bindings;
- integration tests for generation, cancellation and bounded queues;
- performance tests that report stages independently;
- manual GUI smoke tests using external read-only recordings.

Real measurement filenames, paths, screenshots and hashes are intentionally
outside the public repository.

## 14. Build artifacts

Generated native modules, executable bundles, CMake output, Rust `target/`,
caches and measurement exports are not source and are ignored. Public source
history contains code, portable build configuration and deterministic
synthetic fixtures only.
