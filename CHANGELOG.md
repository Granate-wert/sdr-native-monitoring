# Changelog

This changelog records public development milestones. Internal work logs,
machine-specific paths, measurement filenames, screenshots, release hashes and
private review artifacts are intentionally excluded.

## Unreleased

### P16UI-06 — Offline DFL workspace

- added an `OfflineDflWorkspace` for the read-only DFL workflow: open/load
  sessions, session tree with trace/waterfall children, frame navigation,
  playback (loop/no-skip), markers, heatmap enable/disable/recalculate,
  channel-power results with per-row identity, exports and workspace
  persistence;
- added an `OfflineDflPresenter` that owns the pyqtgraph renderers, frame
  loading, playback, heatmap persistence and exports while the widget stays a
  thin snapshot renderer (renderer widgets are reparented, never recreated);
- wired the workspace into the AppShell through an `offline_dfl_factory`
  parameter with lazy attachment on first activation;
- added `offline.*` i18n keys (RU/EN) and public re-exports;
- added 16 offscreen tests (tree, frame spin, markers, playback, heatmap,
  channel-power results, export menu, close, AppShell factory attach,
  placeholder without factory, i18n RU/EN); combined offline + app shell
  suites 39/39 PASS; full suite 636 run with one pre-existing legacy heatmap
  failure and 8 skips; ruff PASS; mypy clean on all P16UI-06 files.
- Status: implemented locally, awaiting review; not yet committed.

### P16UI-05 — Wideband Sweep workspace

- added a plan-before-run `SweepWorkspace`: range editor (start/stop/
  overlap) plus expert fields (sample rate, bandwidth, FFT/hop, dwell,
  settling, discard, edge margin, DC exclusion);
- added a plan preview with segment diagram, coverage check, segment count
  and ETA computed before any device operation;
- added throttled run progress with a status badge and cancellable
  execution through the sweep presenter worker thread;
- added a quality/seam view from P13 stitching evidence and a
  `SweepSpectrumView` that renders the already-stitched full-span frame
  directly — no GUI stitching, missing segments always explicit
  (`MISSING_SEGMENT` bins, NaN values, seam rows);
- added sweep profile presets (`SweepProfileStore`) with atomic `.part`
  writes and corrupt-file errors;
- replaced the legacy "Start P13 sweep" GUI block with the new workspace;
- added a deterministic `FakeSweepService` (including `fail_reconfigure_at`
  → FAILED mid-sweep) for offline runs and tests;
- added 21 offscreen tests (plan validation, no-gap preview, invalid
  overlap, cancellation, missing segment, quality strip, seam display, no
  P13 text, no main-thread acquisition, direct stitched-grid renderer,
  presenter lifecycle, profile store, i18n, AppShell factory) plus a
  poll-budget benchmark (idle poll ~0.3 µs, full render ~4.6 µs per poll,
  fake sweep completes in ~0.024 s);
- regression suites P16UI-04/P12/P13: 93/93 PASS; ruff PASS; mypy clean on
  all P16UI-05 files.
- Status: implemented locally, awaiting review; not yet committed.

### P08/P08H-00 remediation — locally verified, live acceptance pending

- completed canonical Pluto `ComplexInt12InInt16Le` CUDA decoding with 2048
  full scale and clipping parity;
- aligned CUDA non-divisor-hop scheduling, per-FFT timestamps, generation/
  retune epochs, quality propagation and exact staged/partial loss accounting;
- added bounded transactional CPU fallback with replay/discontinuity semantics,
  monotonic public frame sequences and generic backend metrics;
- added keyed backend self-test, conservative AUTO policy, separated CPU/CUDA
  artifact lanes with manifest/preflight, and stage timing counters;
- added CPU/CUDA full-batch benchmark coverage for float32 and Int12 with
  median/p95 samples plus memory plateau checks;
- updated Pluto hardware acceptance to discover a URI, preflight capabilities,
  verify applied readback and report `NOT_VERIFIED` without hardware;
- native CPU (12/12) and CUDA (15/15) CTest lanes plus the 451-test Python
  suite pass locally; production sign-off still requires connected-Pluto
  evidence and an independent review.

### P07 — Headless fixed-band Pluto pipeline

- connected Pluto refill, a bounded native acquisition queue and the P05 CPU
  DSP backend in a device-specific `FixedBandEngine` without adding libiio to
  the portable common core;
- added explicit configure/start/stop/join/reconfigure lifecycle, applied
  readback generations and transient-block discard before resumed analysis;
- kept every admitted analytical FFT in native processing while rate-limiting
  only bounded latest-wins Python snapshots (60 Hz default);
- separated device/IQ/FFT loss, expected stop discard, diagnostic-event loss
  and superseded render snapshots in coherent metrics and health reporting;
- made Pluto output-pool capacity follow the engine queue ownership bound and
  made device metrics polling independent from blocking refill;
- added a coarse Python service and JSONL CLI for discovery and fixed-band
  diagnostics without per-block callbacks or device-serial output;
- added mock end-to-end, 100-cycle lifecycle, running reconfigure, slow polling,
  bounded snapshot, overflow/drop and controlled error regressions;
- made status/metrics queries independent of a blocking refill, made disconnect
  terminal under a concurrent start, and retained an error latch after join;
- corrected exact sample accounting for evicted queue items, per-FFT timestamps
  inside overlapped blocks and bounded priority delivery of Critical events;
- tightened Python integer validation and sanitized serial-field variants in
  headless discovery output;
- added an opt-in real-Pluto soak harness reporting applied settings, FFT rate,
  latency, queue bounds, loss and post-warmup private-memory stability;
- kept CUDA, calibration, sweep, recording, TX and desktop GUI integration out
  of P07.

### P06 — Pluto/AD936x libiio RX backend on Windows

- added runtime discovery of libiio 0.26 and Pluto contexts over explicit or
  scanned USB/IP URIs, without linking libiio into the portable common core;
- added structural fallback discovery for the AD936x PHY, RX streaming device,
  RX LO and two input channels;
- added immutable capability snapshots and transactional configuration of
  center frequency, sample rate, analog bandwidth and manual/AGC gain with
  exact readback and best-effort rollback;
- added non-cyclic RX refill into an eight-block bounded native pool, ordered
  sequences/sample indices, estimated timestamps, short-read/error metrics and
  signed AD936x int12-in-int16 normalization;
- made output-pool exhaustion explicit loss: consumed device blocks advance
  sequence/sample indices, increment exhaustion/drop counters and expose a gap
  after retained buffers are released;
- extended CPU DSP input support and clipping detection for the normalized
  AD936x format;
- added GIL-released cancellation, stop, disconnect and reconnect paths while
  keeping TX unavailable;
- added a Python device service, mock libiio CI target, overflow/rollback/
  short-read/cancel regressions and gated real-hardware tests;
- added an RX-only benchmark that reports measured samples/s, payload, process
  CPU, errors and cancel latency without exposing a device serial;
- verified 3 MS/s continuously for 60 seconds and exercised 2.1/3/5 MS/s in a
  rate sweep on the current hardware; capability ranges are not represented as
  measured transport throughput;
- verified physical USB unplug during active RX: blocking refill returned a
  controlled error without watchdog, crash or deadlock, stale contexts cleared,
  and discovery/configure/RX/reconnect passed after reattachment;
- completed independent safety review and fixed buffer cancel/stop lifetime,
  exact sample bounds, transactional rollback invalidation, required normalized
  gain-mode readback, structural channel fallback, strict int12 layout, blocking
  constructor/configure GIL release and overflow-safe format-shift validation;
- added deterministic regressions for every review finding, including
  `shift=UINT_MAX`; final independent re-review verdict: approved.

### Qt test isolation fix

- fixed progressive GUI test-suite degradation: `deleteLater()` without an
  explicit `QEvent.DeferredDelete` flush left every test `MainWindow` alive
  (~900 widgets per window), making each later window and Qt stylesheet/font
  propagation progressively slower;
- added a shared `shutdown_window` teardown helper that cancels background
  work, drains the thread pool and flushes deferred deletions;
- `MainWindow.closeEvent` now explicitly deletes the orphan pyqtgraph
  ViewBoxMenu tree of the channel-power plot;
- `_apply_theme` no longer re-applies an unchanged application stylesheet;
- measured effect: previously slow files run ~20-30x faster
  (267s → 9s, 201s → 12s, 115s → 10s per file) and the suite no longer
  degrades across tests;
- fixed a pre-existing timing race in the rolling-burst heatmap test that
  asserted before the final playback target was applied;
- the full test suite now completes green: 431 tests OK, 3 skipped in ~36 s
  on the current control machine (previously it did not finish in over 4 hours).

### Repository hygiene

- separated public source and deterministic fixtures from local engineering
  documentation;
- excluded agent instructions, internal reports and captured measurement
  artifacts;
- replaced machine-specific manual-test inputs with environment-supplied
  external paths;
- documented the public architecture, verification flow and implementation
  boundary.

### P04/P05 acceptance corrections

- made recorder `BLOCK` policy apply real backpressure instead of producing
  uncounted `Full` rejections;
- made buffer-pool slot return fixed-capacity and non-allocating;
- fixed overlap scheduling when `hop_size` does not divide `fft_size`;
- flush/rebase on center-frequency and generation changes so an FFT never
  mixes samples from different physical configurations;
- preserve quality flags across detector averaging and account discarded
  partial averages;
- let FFT batches span I/Q blocks and publish the final partial batch at
  shutdown;
- annotate retained latest-wins frames atomically with exact upstream and
  boundary loss counters plus `FFT_DROPPED`;
- added focused native regressions for each corrected contract.

## P05 — CPU FFT/DSP backend

- added a portable CPU DSP backend behind a replaceable `DspBackend`
  contract: I/Q unpack (ci16/cf32), optional block-mean DC removal, overlap
  assembly, six symmetric windows with coherent gain and ENBW, power/PSD and
  linear-domain detectors;
- added a replaceable FFT provider with a vendored BSD-3-Clause pocketfft
  implementation;
- added gap-safe stream handling: discontinuities rebase the assembler
  without stitching stale samples;
- extended the engine consumer to run the DSP stage on every block with
  bounded latest-wins spectrum output and exact FFT loss accounting;
- added drain-on-completion for finite runs;
- extended the wire contract to schema version 3 (power-of-two FFT sizes for
  the DSP configuration);
- verified numerics against all twelve golden vectors inside documented
  tolerances and added native/Python parity tests and FFT/pipeline
  benchmarks.

Device backends, CUDA and calibrated units remain outside this milestone.

## P04 — Native buffering, queues, lifecycle and metrics

- added bounded native queues with explicit overflow policies (block,
  drop-newest, drop-oldest, latest-wins) and exact drop/abandon accounting;
- added a preallocated reference-counted buffer pool compatible with the
  existing I/Q block contract;
- added an engine state machine (created → configured → running → stopping →
  stopped/error) with cooperative cancellation and clean thread shutdown;
- added lock-free atomic metrics counters with periodic latest-wins snapshots;
- added a deterministic synthetic producer/consumer transport with an
  optional recorder tee;
- added bounded diagnostic events with throttled overflow reporting;
- extended the wire contract to schema version 2 with EngineState,
  OverflowPolicy and EventSeverity enums;
- added C++ queue/pool/lifecycle/stress tests (including a one-million-block
  bounded-memory run) and Python lifecycle contract tests.

FFT processing, device backends and recording remain outside this milestone.

## P03 — Synthetic and golden-reference foundation

- added twelve deterministic synthetic complex-I/Q scenarios;
- added explicit NumPy float64 reference DSP;
- defined ADC normalization, window coherent gain, equivalent noise bandwidth,
  bin-power, PSD and linear detector semantics;
- added schema-versioned, deterministic NPZ golden vectors;
- added native synthetic-source contract scaffolding;
- added C++ and Python numerical/contract tests.

Production FFT processing and live acquisition remain outside this milestone.

## P02 — Canonical SDR contracts

- added matching C++ and Python source, I/Q block and spectrum-frame contracts;
- added capabilities, device state, units, quality flags and immutable
  configuration;
- added backward-compatible source descriptors to the measurement domain;
- verified exact wire-enum agreement;
- added read-only NumPy views with native lifetime ownership.

## P01 — Native core bootstrap

- added portable C++20 common core;
- added CMake presets for Windows CPU builds and Linux scaffolding;
- added a coarse-grained pybind11 module;
- added API/schema compatibility checks and Python fallback;
- verified exception translation and GIL release for long native calls.

## P00 — Architecture baseline

- established control-plane/data-plane separation;
- defined bounded queue, ownership, loss, calibration and portability rules;
- preserved the imported DFL analyzer as a read-only offline source;
- established CPU as the required numerical reference and CUDA as optional.

## Imported DFL analyzer baseline

The imported application includes streamed DFL parsing, waterfall/spectrum
navigation, markers, measurements, heatmap persistence, activity logging and
export. Measurement files and derived artifacts are not repository assets.
