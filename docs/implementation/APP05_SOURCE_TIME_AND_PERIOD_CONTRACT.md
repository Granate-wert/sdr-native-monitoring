# UI V2: source-time axis and distinct observed periods

Implementation checkpoint: 2026-09-27. APP-05 remains OPEN. This document
describes source contracts, not a release, RF continuity, scanout or 50-ms
performance acceptance. Work concerns UI V2 only; Legacy/DFL is not the target.

## Waterfall time axis

RTBW axis labels form a stable 1/2/5 relative-age ladder. Tick positions are
interpolated between observed producer timestamps, not derived from GUI FPS
or the current wall clock. A producer-time pause greater than twice the
typical positive row interval is not filled with synthetic time ticks.
The lower median observed interval, at least the configured display interval,
avoids treating an ordinary slow producer as a continuous gap.

The configured history span is reserved during ring fill. Spacing hysteresis
avoids alternating labels at a threshold; labels may change on a genuine
extent/resize change. The axis gutter may grow for wider text or font changes,
but is not repeatedly shrunk for every new row. Unknown timing has no precise
age labels. Stop/freeze does not advance time. Sweep keeps pass/revision and
`P/C/G` row-state labels, latest-row promotion and collision culling.

Tests: `tests/ui_v2/test_app05_waterfall_stable_time_ticks.py`, existing
progressive-Sweep axis tests, and the opt-in full-composition layout probe
`scripts/probe_app05_time_axis_layout.py`. Its fake local history input is
layout evidence only, not analytical acquisition, physical monitor or DWM.

## New Qt frame period

`UniquePaintCadence` stores scalar publication identities and at most 512
host-monotonic paint-return times. It owns no frame, array, receiver or timer.
The current curve's source/epoch/generation/RX/unit/mode scope, sequence,
partial revision and terminal state identify a publication. Repainting an
unchanged source for chrome, persistence or zoom does not count as a new frame.
Older sequences, lower same-pass partial revisions, and partial rollback after
terminal do not count. Scope change, Hide or Clear starts a fresh measurement.

The shown mean interval uses the retained distinct paint times in the last
four seconds. It is unavailable until two distinct paints, when the last is
older than one second, or RX is stopped. This is Qt paint-return of an admitted
curve, not changed pixels, DWM frames, sensor revisit, FFT/s or RF duty.
The graphics wrapper preserves the injected pyqtgraph widget used by observers.

## Host buffer and complete-pass periods

Append-only `LivePerformance` fields carry native host-received block count,
block rate and configured buffer size. Rate uses counter deltas at the existing
native-metrics sampling boundary; missing counters, first observation, resets
and invalid elapsed time are unavailable, not zero. No extra device call is
introduced. RTBW shows the reciprocal of this observed host block rate.

Sweep shows the reciprocal of completed-line LPS only after a completed pass.
That aggregate mean is not per-frequency revisit or dwell. Both fields use the
existing status pacing. The GUI reports RF duty/revisit as unknown/unmeasured,
and Stop clearly distinguishes a retained frame from live acquisition.

High verified applied ADC Fs is the primary device load. Host-delivered I/Q
rate and its ratio to applied Fs are separate diagnostics. That ratio alone
must never be relabelled as RF duty, continuity, probability or a transport
loss rate. USB, Ethernet and each device require separate profiles.

## Conditional temporal pulse encounter

`domain/pulse_encounter.py` implements a bounded, Qt/device-free conditional
model for one pulse with uniformly distributed start time on a finite horizon.
For a capture interval `[a,b]`, pulse duration `tau` and minimum continuous
overlap `delta`, valid start times are `[a+delta-tau,b-delta]`, provided both
interval and pulse are long enough. Adjacent capture windows merge first;
clipped opportunity intervals then merge so overlaps are not double-counted.

The caller must explicitly provide a complete ledger qualified for the chosen
frequency/RX/clock and HARDWARE or SYNTHETIC timing. Incomplete, estimated,
unknown or replay timing returns `None`. At most 4096 windows are accepted.
This is temporal encounter, not amplitude/detector Pd; SNR, calibration,
periodic phase and pulse repetition require separate assumptions/models.
Current host-estimated Pluto timing is not sufficient to populate an RF
percentage in the UI. Physical producer-ledger integration remains OPEN.

## Remaining acceptance gaps

No claim that APP-05/APP-07 or the release is complete. Global all-layer
freshness, control tails, long soak, physical device/transport matrix and
packaged-current-EXE evidence are separate gates. Short source runs or a
passing geometry probe cannot waive them.

An explicit populated-data layout probe measured approximately 60.9% useful
ViewBox area at 1366x768, below the 65% design target; the earlier APP-02 area
test measures the prepared/empty state. Add populated/long-label states to
responsive design acceptance. Do not recover area by silently hiding a layer,
shrinking text or discarding diagnostic provenance. At fractional DPR exact
requested physical pixel size may be impossible for integer QWidget sizes;
record actual rounding rather than accepting a different target silently.

Local ignored detailed evidence and screenshots live in the main workspace
under `docs/reviews/codebase_ui_2026-09-07/` and
`evidence/app05_timebox_20260927/`. They are not embedded in this source
contract or automatically published with Git commits.
