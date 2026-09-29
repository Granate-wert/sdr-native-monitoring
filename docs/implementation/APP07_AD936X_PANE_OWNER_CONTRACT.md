# APP-07 AD936x RTBW pane-owner boundary

This contract describes the currently implemented, opt-in application/backend
seam. It is **not** an enabled multi-device UI feature or a declaration that
APP-07 is complete.

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
- Current `start_native_recording_now` is not yet subordinated to the pane
  resource lease between transactions. A production UI must arbitrate that
  command under the same resource owner before enabling multi-pane control;
  epoch rejection alone cannot make a surprise recorder restart acceptable.

## Supported plan and admission

The adapter currently admits only one explicit AD936x **RX1** endpoint, RTBW,
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

## Scope of evidence and remaining work

Fake native module tests cover two separate application graphs, one receiver
retuned between two bands, native-poller-to-pane publication, armed-recording
conflicts, missing RF readback, source/RX2 mismatch, bounded LO quantization
and control-lock collision. A bounded RX-only Pluto USB observation at
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
