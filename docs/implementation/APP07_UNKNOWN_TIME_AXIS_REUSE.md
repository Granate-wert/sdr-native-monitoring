# APP-07 UI V2: safe reuse of an unchanged unknown-time waterfall axis

This change avoids rebuilding a PyQtGraph axis picture for the same effective
unknown-time RTBW model. It does not suppress waterfall data admission or paint,
and it does not freeze or fabricate producer/RF time.

## Local reuse rules

WaterfallTimeAxis still normalizes inputs, converts the entire supplied
timestamp vector and derives intervals, gaps and Sweep state before deciding
whether a picture can remain valid. Reuse is limited to:
- both previous and current time qualification are unknown;
- no accepted timestamp vector and no non-null Sweep stamp;
- the bounded scalar presentation model is unchanged;
- previous derivation completed successfully; and
- the inherited AxisItem picture is still non-null.

Known-time or qualified-empty transitions, Sweep partial/complete/gap stamps,
direction/rate/display/capacity changes and incomplete derivation cannot use
this shortcut. An externally cleared picture is never restored; inherited
font/style/range/resize invalidation and explicit locale changes still follow
normal drawing. No timestamp-array identity check, retained-history hash,
global cache or Qt object moved off the GUI thread is introduced.

A failed conversion keeps the original exception and partial-mutation behavior
but marks the presentation model incomplete. The next valid update must clear
the old picture before subsequent identical completed updates can reuse a new
one. This repairs a recovery defect found in the first review candidate.

## Reproducible component observation

One matched BEFORE/AFTER pair used an actual offscreen WaterfallPane at
1920x1080, 2048 presentation columns, a full 300-row ring and 60 successful
steady admissions per run. These are UI samples, not FFT/DSP throughput.
The unknown renderer timestamp placeholders remain unqualified; the axis
accepts no time vector. Existing model, font, locale and view-range controls
had equal receipts, including normal invalidation and rebuilding.

| Steady observable | BEFORE | AFTER |
| --- | ---: | ---: |
| Admitted rows | 60 | 60 |
| Axis setter calls | 60 | 60 |
| Non-null pictures discarded by setter | 60 | 0 |
| Real draw-spec entries | 60 | 0 |
| Python axis paint entries | 60 | 60 |
| Paint-entry inclusive p95 | 4.9660 ms | 0.1097 ms |
| Setter inclusive p95 | 0.1288 ms | 0.0730 ms |
| Fixed 60-admission interval | 1.4154225 s | 0.9935082 s |

Quantiles sort integer-ns samples and use floor(q*(n-1)).
The absent AFTER draw-spec timing is unavailable, not zero latency.
Timings are nested, observer-perturbed wall measurements from one fixed-order
pair; they cannot be summed or generalized to a stable whole-app percentage.
Setter median did not improve in this pair. Paint entry is not DWM/compositor
present, and a configured cadence is not achieved RF/FFT throughput.

## Verification and remaining qualification

Nine focused regression tests cover unchanged/empty unknown models,
known/unknown transitions, full conversion including errors, recovery after
failed changed scalar inputs, direction/rate/capacity, gaps, Sweep states and
external cache invalidation. A 41-test targeted set passed in 6.263 seconds;
scoped Ruff, compilation, mypy and staged whitespace checks passed.
Independent UI source, method and raw-results reviews were performed.

The exact candidate source-only UI V2 gate ran 1588 tests in 1071.433 seconds:
1522 passed, 66 skipped, no failures/errors. Two compiled-native tests were
explicitly deferred. No product module was loaded from outside the checkout;
four historical NaN-validation warnings were retained.
Source commit: 77a389359f85e935fc0521535e6d5a6f783e6c29.
This document is separate from the source/build identity.

No acquisition owner, native DSP, Fs/FFT/hop, receiver identity, epoch/gap,
quality, custody/queue/buffer budget, recording or source isolation policy was
changed. Backend/native integration, physical three/four-source operation,
known-time/Sweep sustained coverage, visible Windows FHD/QHD and scaling,
Stop/Start under real load, soak and matching release qualification remain
required. APP-07 stays PARTIAL. This source increment does not certify a fix
for the historical hardware Qt stall or a 50 ms latency guarantee.

## Matching build and changed physical observation — 2026-10-10

A fresh diagnostic CPU package was built from exact source77a3893, not from
the later documentation commit. The official native pipeline passed48 tests
in70.70s. Its matching frozen/source UI V2 gate ran1590 tests in954.736s:
1524 passed,66 skipped, no failures/errors; the two previously deferred compiled
tests were now admitted. Source/native/runtime provenance and callback audit
passed; four historical NaN-validation warnings remained. This is software
qualification of a diagnostic package, not static promotion or release proof.

ONE changed actual three-SDR observation used HackRF20MS/s, AD9363 over
Ethernet30.72MS/s and RTL2.4MS/s, FFT4096. Fresh data from all three arrived
in9.3616723s, but the requested10s observation returned after20.8305897s,
exceeding its unchanged10.5s ceiling. The interval was rejected, not reclassified
as a successful longer benchmark; the lifetime supervisor had not requested
Stop. Inclusive observer timings showed long axis/paint/preparer/journal calls,
but cannot separate CPU work from scheduling/GIL contention or be summed.

Cached cleanup confirmed no retained owners or SDR workers and released the
lease. The process subsequently exited with0xc0000005; a matching Windows
ApplicationError identifies python313.dll, not the originating cause. No crash
stack was retained; cause remains UNKNOWN. Cleanup receipt is not clean process
exit or whole-run PASS. There was no unchanged physical retry or deadline increase.

## Finite model-growth census — not an optimization comparison

A separately reviewed ONE sourceQt/offscreen component census completed with
normal process exit,736 per-setter model/picture receipts and79 timing series
containing2884 observations. It used the same source77 and qualified runtime.

| Model phase | Admissions | Draw-spec entries | Setter picture discards | Paint entries | Inclusive paint p95 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Unknown-time growing history |300|300|299|300|2.4171ms|
| Unknown-time full ring/wrap |60|0|0|60|0.0972ms|
| Known-time growing history |300|300|298|300|1.1012ms|
| Known-time full ring/wrap |60|60|60|60|1.2249ms|

This census uses nearest-rank ceil(q*n)-1 quantiles, unlike the earlier112
fixed-pair summary above. Absent draw-spec timing is unavailable, not0ms.
These phases differ in state/admission count; their timings are not a matched
before/after gain or application-wide percentage. Unknown growth changes row
count and initial capacity; known full-ring progression changes timestamp
content. Unknown full/wrap retains the same picture on all60 setters.

Concrete controls preserved pause/gap representation, rejection of regressed
pane timestamps, separate defensive axis input, Sweep revision replacement,
gap append, unannounced epoch refusal, explicit generation reset and failed
conversion recovery. External font/locale/view observations do not constitute
exhaustive visual/DPI invalidation acceptance. A single unloaded dispatch
control does not explain physical lateness or establish hardware50ms behavior.

The next UI investigation is rendering-equivalence during unknown-time growth:
FIRST compare actual specs, text and tick geometry across growing states.
Do not simply remove rows from the cache key. Preserve complete-model/time/
Sweep guards, direction/origin/capacity/rate/view/geometry/style/font/locale,
external invalidation and explicit clear/recovery semantics. No new product
change is included in this qualification document. Root must separately
attribute journal/preparer thread CPU versus wall time and localize the exit
fault before a changed physical qualification.

## Restored tinySA finite instrument check

The newly connected tinySA Ultra passed ONE separate retained-owner scan:
100–300MHz,1001 points, requested and queried actual RBW300kHz. Scanraw duration
was1.4293399s; the enclosing collect took1.4357432s. Its queried screen-sweep
time4.487s is a different quantity, not the measured scanraw duration. The
firmware stop-exclusive grid ends at299.8MHz. Exactly one scan, no retries,
confirmed port closure/lease release and normal process exit were recorded.

Values retain device-reported built-in calibrated dBm provenance; no external
correction, hardware timestamp or metrological verification is inferred. This
standalone functional check is not a sustained rate, UI or four-source proof.
Four-pane rotations with each Pluto variant, HackRF, tinySA and RTL remain
required. Slow instrument scanning must be distinguished from GUI delays.
APP-07/M7–M8 remains PARTIAL; physical responsiveness, visible/DPI, soak and
release acceptance are not closed by these software/component results.
