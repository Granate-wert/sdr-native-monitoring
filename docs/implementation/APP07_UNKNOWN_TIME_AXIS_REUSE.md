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
