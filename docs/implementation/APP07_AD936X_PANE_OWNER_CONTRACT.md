# APP-07 AD936x RTBW pane-owner boundary

This contract describes the currently implemented, opt-in application/backend
seam. It is **not** an enabled multi-device UI feature or a declaration that
APP-07 is complete.
The separate `APP07_MIXED_SOURCE_TRACE_CONTRACT.md` records the required
AD936x + HackRF + tinySA + Empty 2×2 target and the source-neutral trace
planning boundary; neither document claims product multi-source activation.

## Ownership and control

- `PaneResourceSession` reserves one `ReceiverLeaseManager` key per physical
  stream resource before it calls an externally supplied owner. A production
  composition must derive this key from verified physical identity, not from
  a URI alias or an unverified model string, and must share the authority with
  every legacy/current source path that can start the same receiver.
- `Ad936xRtbwPaneOwner` uses the existing `LiveSessionApplicationService`,
  which in turn uses the single Analyzer lifecycle and native AD936x service.
  It does not construct an IIO context or side-channel acquisition engine.
- The native port's `pane_capture_control_transaction()` is the same
  reentrant, fail-fast low-rate lock used by its native recording commands.
  `PaneResourceSession` holds that transaction across recording-conflict
  verification and Stage/Start or Stop/retune. It never holds the lock in the
  I/Q, FFT, native writer or presentation hot path.
- On the first explicit pane Start, the same `LiveSessionApplicationService`
  retains an owner-token claim. Ordinary V2 Discover/Select/Apply/Start/Stop,
  Sweep Start and shutdown commands refuse while it is held. The pane's
  transaction authorizes only its own token; confirmed explicit Stop releases
  the claim before the external resource lease. A Start failure after the
  claim is acquired, or an unconfirmed Stop, retains it for explicit cleanup.
  Apply without Start still reserves only the
  `PaneResourceSession` lease, so product composition must share that lease
  authority before advertising an applied multi-pane plan.
- Current `start_native_recording_now` is not yet subordinated to the pane
  resource lease between transactions. A production UI must arbitrate that
  command under the same resource owner before enabling multi-pane control;
  epoch rejection alone cannot make a surprise recorder restart acceptable.

## Supported plan and admission

The original `Ad936xRtbwPaneOwner` admits only one explicit AD936x **RX1** endpoint, RTBW,
manual gain, uncalibrated `dBFS/bin`, a known native window/detector name,
and an overlap within the existing Live limit. Sweep, dual-RX, auto gain,
absolute-unit correction, HackRF and tinySA plans refuse before lease or
native Start. The selected source must be idle, connected and already staged.

For each capture, the existing Live application stages the exact planned
center/Fs/RF bandwidth/gain/FFT/hop/window/detector. Native Start must return
the same source and a producer epoch, with center/Fs/RF bandwidth/gain in the
native readback field set. Missing RF readback or a changed Fs, bandwidth,
gain or DSP setting is a refusal that retains the same owner for explicit
Stop. Hardware LO quantization is accepted only within half a physical FFT
bin and only if the requested pane remains within the usable capture window
with the same half-bin tolerance; a larger shift is refused. DSP settings are
the configured native request, not independent analog RF readback.
`PaneCaptureProfile.unit`
participates in shared-capture compatibility and the owner receipt gate.

`poll_resource()` drains the same application's bounded published snapshots
on a worker. Each immutable `AnalyzerFrameBundle` is checked against the
current activation, source/session/generation/epoch/unit, receiver identity,
Fs/FFT/hop and pane coverage before delivery. NaN gaps, native loss and
quality metadata are not rewritten. Poll failure closes publication and
retains the owner/lease until explicit Stop.
Current native Live/RTBW `poll_frames()` is a non-destructive latest-snapshot
read; the UI presenter and pane owner do not compete for a consumed queue item.

## Scope of evidence and remaining work

Fake native module tests cover two separate application graphs, one receiver
retuned between two bands, native-poller-to-pane publication, armed-recording
conflicts, missing RF readback, source/RX2 mismatch, bounded LO quantization
and control-lock collision. Tests also cover normal V2 Live commands being
refused across a pane retune and admitted again after explicit Stop. A bounded
RX-only Pluto USB observation at
61.44 MS/s delivered two successive RTBW panes under distinct producer epochs,
with zero host-gate rejects and explicit Stop/release. It was not an EXE/UI
observation and did not prove throughput continuity or RF duty. These checks
do **not** prove two physical receivers run concurrently, a process-wide
SDK arbiter, alias-safe physical resource keys, real recording restart
coordination, UI responsiveness, DWM FPS, RF duty, pulse detection probability
or a frozen Windows release. Product UI V2 still needs owner factory and
assignment controls, source-identity-based shared leasing, activation-bound
delivery, fair rendering and physical/frozen qualification. Do not mark
APP-07 complete from this backend seam alone.

## Current UI V2 continuous-Sweep pane composition

The V2 product composes `Ad936xPaneOwner` over that same RTBW adapter and the
same `AnalyzerSweepRouter`. `Ad936xSweepPaneProfile` binds the existing
`LiveConfiguration` and `ContinuousSweepPlanRequest` to the exact selected
source object and selection revision. It is not another RF-settings model,
SDK opener, DSP implementation or renderer. One endpoint remains RX1; this
does not advertise simultaneous independent RX chains on one AD936x device.

The user can select RTBW or Sweep on the common Analyzer pane. AD936x Sweep
explicitly fixes requested Fs at 61.44 MS/s, usable W at 36 MHz, overlap at
2 MHz (34 MHz stride), and requested RF filter at 40 MHz. The FFT/N selector
means **analysis N inside W** only in AD936x Sweep. The minimum power-of-two
physical transform F supporting W/N is calculated and shown before Apply:
N1024/F2048, N4096/F8192, N16384/F32768. Grid spacing W/N is neither RBW nor
ENBW. The preview includes segment count and the existing conservative
reduced-data memory estimate, not total RSS or measured RF/transport speed.

The existing pure continuous-Sweep preflight decides segment, grid, physical
FFT and memory admission before a pane lease or RX. The optional common
geometry contract version1 admits up to2048 segments, subject to the existing
2-million-output-bin bound and128 MiB reduced-data budget. This is not an RSS
or whole-process allocation guarantee. Earlier prose saying64 MiB was wrong:
the established backend spectrum budget was already128 MiB; the separately
bounded visual queue is not that backend budget.

Extended plans (>64 segments) require the ALREADY loaded native artifact's
own sibling manifest, matching geometry version/capacity/budget and artifact
SHA. Old artifacts retain their bounded compatibility; they refuse extended
plans before a pane lease or receiver Start. There is no alternate loader,
fallback, hidden FFT reduction or retry. Full-device-range physical/frozen
qualification remains separate work; a bounded2x2 test is not its substitute.

For70..6000 MHz, W36/overlap2 produces175 segments. N1024/F2048 and
N4096/F8192 fit the reduced-data budget; N8192/F16384 exceeds that budget,
and N16384/F32768 also exceeds the output-grid bound. These are pure planning
results, not measured RF settling, transport speed or sweep duration.

Stage/Apply do not start RX. Accepted explicit Start stages the exact CPU
profile, then delegates to the existing continuous-Sweep owner/lease through
the common application. RTBW/Sweep time-slices first confirm Stop, record
the planned control boundary and start a new common Analyzer Sweep epoch.
Sweep's pane admission exposes requested Fs/physical F but no fabricated
continuous RTBW hop or analog readback receipt. Existing native lease
capability/identity/readback admission remains authoritative. Identical
Sweep plans may share one capture; different plans or modes time-slice one
RX rather than opening another owner. Stop failure retains that same owner
and claim for explicit cleanup, without retry, reopen or hidden restart.

The native producer's established ID is
`continuous-sweep:native-sweep:<selected-source-id>`. Only this exact binding
is normalized to the canonical selected source at the pane boundary, along
with an optional statistics parent. Epoch, NaN gaps, quality/loss masks,
acquisition metadata and last-admitted-segment position are not rewritten.
Progress and terminal publications must match the retained epoch, unit,
segment partition, output grid and physical transform contract. Foreign
source/profile/geometry refuses and requires explicit Stop. Bounded
watermarks deliver new progress before complete-pass coverage while keeping
terminal events separate; repeated/older progress cannot overwrite its
terminal. Both use the established V2 spectrum/waterfall preparation path.

Source tests exercise the actual common graphs, user editor and 2x2 pane
presentation with synthetic reduced AD936x/HackRF frames and fake tinySA
serial acquisition. AD936x terminal coverage is deliberately withheld until
its progressive spectrum is visible. This is software evidence, NOT physical
three-device Sweep, visible Windows/DWM/50 ms/FHD/QHD/DPI, RF duty, lossless
transport, current tinySA RBW300k, held-middle RF retune, soak or release
acceptance. APP-07 and the full APP-00..14 objective remain OPEN.
