# APP-06A — receiver-owned identity admission

Date: 2026-09-27. Scope: UI V2 shared native service boundary; no Legacy
widget/FFT/rendering/default-cadence changes. This is not whole APP-06 acceptance.

## Admission

`PlutoDevice`, `PlutoFixedBandEngine` and `NativeContinuousSweepCoordinator`
accept optional `expected_serial`. The same receiver-owned IIO context provides
the observed serial and later RF configuration/RX. If identity is known, native
construction compares it before RF writes, channel enables and buffer creation.
Mismatch or missing observed serial rejects and destroys that context before
the constructor returns. Errors do not include expected/observed serials.

Normalization is shared in meaning across Python/C++: trim ASCII whitespace,
lowercase ASCII; empty, `-`, unknown/n/a/none, non-ASCII, embedded whitespace or
controls are not known serials. Supplied invalid expected identity rejects before
context creation. `None`/`nullopt` is explicitly unknown, not stable physical proof.

Native exports `PLUTO_IDENTITY_ADMISSION_PROTOCOL_VERSION = 1`. A known-serial
Python request requires exact integer version 1 before invoking the owner factory;
an old runtime, bool/float/string/future version does not silently downgrade.
Unknown-serial single-route operation retains the two-argument compatibility
call. It does not establish transport aliases or calibration/matrix identity.

## Composition paths

- Live selection: expected discovery serial enters temporary `PlutoDevice`.
- RTBW Start: expected selected serial enters the receiving fixed-band owner.
- Native Sweep lease: selected serial is a private/repr-excluded source field.
- Sequential Sweep: source identity enters the fixed-band owner before Configure.
- Continuous Sweep: both display service and native evidence factory pass the
  source identity to the receiving coordinator constructor.

No separate Python probe verifies a subsequently opened owner. The retained-owner
Stop contract is preserved: a failed join/disconnect still blocks another RX.

## Checked candidate gates

71 focused service tests pass, including stale identity between selection and
Start, old-runtime rejection before factory I/O, known/unknown normalization,
sequential lease release on admission failure and all continuous factory paths.
New files Ruff and identity helper scoped mypy are checked independently.

The staged Windows CPU native candidate passes 34/34 CTest (41.13 s). C++ tests
cover matching, changed-open-context, mismatched, missing and invalid identity for
all three owners. Mock counters prove no RF attr writes/channel enables or IIO
buffers in these admission cases, no retained rejected context and no duplicate
owner open. A separate compiled Python binding test passes all three constructor
keyword paths with the same mock DLL. Its first run exposed a test-fixture API
mistake (`close` instead of native `disconnect`); fixture corrected and rerun.

The candidate was initially built against a dirty source checkpoint; this is
not clean source provenance. Final clean-commit build and actual artifact SHA,
full UI V2 gate and physical observation belong in APP_06_EXECUTION.md.
Windows build only: the Linux source is updated but uncompiled in this run.

## Still open

Serial equality is operational identity, not cryptographic hardware attestation.
Discovery currently combines separate probe/capability/topology observations;
these are not a single coherent capability transaction. Temporary probe cleanup
failures still require explicit retained-owner handling. Unknown-serial devices
cannot supply strict stable physical capability/calibration/alias evidence.

Common existing-capability catalog, normal multi-family Analyzer selection and
runtime routing, shared HackRF package/DLL closure, genuine Ethernet AD9363,
tinySA Standard/Ultra availability and full physical/package matrix remain open.
Do not report a DTO, synthetic mocks or short physical RX as completion.
