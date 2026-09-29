# APP-07 mixed-source pane contract (partial)

The required product scenario is a 2×2 UI V2 Analyzer with independently
assigned AD936x RX, HackRF RX and tinySA spectrum-trace sources on three
different ranges, plus an explicitly Empty fourth slot. The earlier four
`shared_views` are four views of **one** source and do not satisfy this scenario.
No multi-source product or frozen-EXE capability is claimed by this contract.

## Source-neutral planning without invented instrument geometry

- `PaneLayoutSlot(1..4, request | None)` keeps Empty visible while only occupied
  slots enter `compile_pane_schedule`. An all-Empty layout has no capture plan.
- `ReceiverEndpoint` remains one I/Q RX selection. `SpectrumTraceEndpoint` is
  one instrument-reported spectrum with no fabricated RX1/RX2 or I/Q buffer.
  A trace endpoint cannot share an acquisition group with I/Q endpoints.
- `PaneCaptureProfile` remains an Fs/FFT/hop I/Q profile. It explicitly refuses
  instrument-trace mode. `SpectrumTracePaneProfile` instead declares actual
  point count (2..10001), usable span, measurement unit dBm, exact settings
  identity and bounded scheduling cost. It has no Fs, FFT or hop field.
- The same finite `PaneResourceSession` keeps one external lease and one owner
  per physical resource. Instrument-trace admission contains exact points,
  model, device/firmware fingerprints, configuration generation and epoch,
  with absent I/Q geometry. It accepts only the matching typed tinySA
  `SweepLineFrame.instrument` provenance and dBm unit. Source, endpoint,
  config, epoch, grid, family-specific mode and ownership checks remain.
  A foreign or stale trace is rejected, not displayed as a zero-filled frame.

The scheduling cost and planned revisit are estimates, not observed tinySA
scan speed, RF duty or pulse-detection probability. A slow tinySA trace must
eventually run independently of Pluto/HackRF rendering; this packet does not
yet prove such UI isolation.

## Evidence and remaining acceptance

An inert three-owner test covers an AD936x-like 61.44 MS/s I/Q profile, a
HackRF-like 20 MS/s I/Q profile, a four-point tinySA-style dBm trace and
Empty slot 4. Three resource leases remain active together; each exact bundle
reaches only its assigned pane; foreign source and trace fingerprint are
refused; explicit Stop releases all leases. A separate fake single-instrument
test time-slices two ranges with Stop→Start/new epoch and rejects the previous
activation. This is fake-owner contract proof,
not three physical devices, three concurrent product SDK contexts, or a
visible UI frame-rate test.

The HackRF RTBW and tinySA trace pane owners now exist over the same family
application graphs; see `APP07_HACKRF_RTBW_PANE_OWNER_CONTRACT.md` and
`APP07_THREE_OWNER_TRACE_CONTRACT.md`. The ordinary V2 graph builder and a
resource-scoped three-family session composer are also present; see
`APP07_RESOURCE_SCOPED_GRAPH_COMPOSITION.md`. APP-07 still requires these
graphs to be owned by the actual product tab with per-pane source/range/mode
controls, an actual visible Empty pane,
independent fair projection and Stop/error handling, and a current-source
Windows EXE. The requested physical 3+1 matrix must then show concurrent
AD936x/HackRF/tinySA activity without a slow instrument stalling either SDR,
retain separate dBFS/bin and dBm axes/provenance, and cleanly release all
three resources. Until those gates pass, the current single-source `shared_views`
must not be labeled multi-source support.
