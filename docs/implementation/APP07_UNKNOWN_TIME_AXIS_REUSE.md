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

## M78-115 follow-up: attribution and growth-equivalence limits

A finite root-owned fixture exercised actual typed reduced-publication
preparation and journal draining with HackRF20MS/s, AD9363 30.72MS/s and
RTL2.4MS/s profile metadata, FFT4096/hop2048. Five fresh processes completed
normally;19,200 raw call durations and1,800 linearly interpolated quantiles were
independently recalculated. One process used a single shared preparer/layout/
256MiB ledger for three independent resources; the other four did not.
This is not a raw-I/Q/DSP-rate simulator, Qt FPS or hardware qualification.

Short-interval Windows thread CPU readings were too coarse for CPU p95/p99.
Cycle counters were retained without estimating CPU frequency or GIL percent.
A finite5ms/1ms/1ms/5ms Python switch-interval diagnostic produced inconsistent
effects; the application's global setting was not changed. Shared-fixture
fresh-prepare p95 varied4.28–7.61ms and journal-drain p95 stayed below0.70ms.
These are observations across different finite cases, not a matched gain or
an explanation of the physical Qt delay. Fresh-waterfall substep attribution
and selective native fusion remain measurement-dependent work.

An actual PyQtGraph-axis oracle captured normalized draw specs, pen/font/
context and geometry. Sampled positive unknown-time histories with1,2,4,17,
60,299 and300 rows matched exactly; empty state0 differed. Thus unnecessary
growth invalidation is a cache candidate, not yet a qualified product change.

The complete oracle FAILED its known-gap control within the fixed three
normal paint/event passes. A wider gap label coincided with axis-gutter
expansion57.6→71.6px, picture invalidation and regeneration. Legitimate layout
expansion is plausible; exact invalidator causality is unknown. Subsequent
Sweep/epoch/conversion controls were not reached. Independent read-only UI
review confirmed only the partial evidence and failed overall result. Neither
the pass cap nor observation timeout was increased to manufacture a PASS.

No product code, build or hardware RX changed in this follow-up. Physical113
responsiveness still fails and its post-cleanup access violation is unresolved.
Next work separates positive unknown-time growth from full semantic/layout
controls before admitting a cache patch. tinySA-inclusive four-pane rotations,
visible/DPI, soak and release qualification remain open; APP-07 is PARTIAL.

Statistical-method correction in M78-116: the M78-115 fixture uses NumPy's
default linear interpolation, not the nearest-rank method used by the M78-114
census. Independent recomputation of all1,800 reported M78-115 quantiles from
the retained samples matches linear interpolation;1,782 differ from nearest
rank. Numerical results and original logs are unchanged; no workload reran.

## M78-116: completed semantic controls and preparation attribution

A separate finite offscreen run completed eleven declared controls and eleven
normal observations. Ten observations generated fresh specs; one additional
spec came from bootstrap, for eleven actual spec calls. Known-gap handling
used exactly three scheduled passes and retained real pause labels. Full Sweep
sequence/revision/state/epoch vectors, inert unannounced-epoch refusal,
explicit boundary retention, changed-generation reset, failed timestamp
conversion recovery and external picture invalidation were verified. The
process exited normally and a distinct UI reviewer checked the raw results.
This closes the limited diagnostic method, not application/HIL acceptance.

Three separate Qt-only teardown variants also completed normally, with no
callback, observer, overflow or cleanup error. They used no native owner, SDK
or SDR; observer restoration before/after graphics release was distinguished
by flushed phase receipts. This finite matrix did not reproduce the prior
physical access violation. Its cause remains unknown; no native cleanup or
hardware fix is inferred.

Preparation measurements used one shared preparer and budget for independent
HackRF20MS/s, AD9363 30.72MS/s and RTL2.4MS/s reduced-publication profiles.
The real sample rates remain profile metadata here, not simulated raw-I/Q
throughput. Independent validation covered3,072 outer calls and288 linear
quantiles. Nested timings show fresh peak reduction/output geometry rebuilt
for every publication; whole-grid validation was already cached. Inclusive
intervals overlap and must not be summed into CPU/GIL percentages.

A private geometry-template prototype matched528 functional cases, including
nonfinite values and geometry transitions. Its fixed component comparison
reduced average kernel wall time about19–44%, with absolute savings only
hundredths of a millisecond. This is not a whole-application improvement and
does not explain seconds-long Qt delays. It is not admitted to production:
shared memory accounting, lifecycle guards and independent review are still
required. No blanket native rewrite or removal of validation is justified by
these measurements. Positive unknown-time axis-growth reuse remains the
next narrowly scoped product candidate; known time, gaps, Sweep and empty
transitions must keep ordinary invalidation and full model derivation.

## M78-116 candidate: positive unknown-time history growth

UI V2 now also retains an existing real axis QPicture during strictly positive
newest-at-top history growth, but only after the complete ordinary time-model
derivation. All model fields except the positive row count must match. Empty
axis timestamps/stamps, no age/gap/Sweep state and unchanged capacity, rate,
origin and producer interval are required. It never creates a cached picture.
Reset, shrink, incomplete-state recovery, known timestamps, gaps, Sweep and
inherited style/view/geometry invalidation keep their ordinary redraw path.

The original and candidate each received exactly300 typed history updates in
fresh finite offscreen Qt processes. Original actual draw-spec calls:300;
candidate:2. All298 eligible candidate setter calls retained the real picture.
All300 derived models and seven corresponding actual-spec/geometry checkpoints
matched exactly, including the distinct initial-layout checkpoint. There were
still300 normal paint calls in each run: acquisition or presentation cadence
was not reduced. The candidate exited normally; callbacks, observer, overflow
and cleanup error lists were empty. Root focused checks passed44 tests and31
subtests; independent source and measurement-method review preceded execution.

Local original-call paint p50/p95 changed2.8163/3.613465ms to0.0664/0.090405ms;
setter p50/p95 changed0.0676/0.096685ms to0.0511/0.0676ms. These paint intervals
include the nested diagnostic spec wrapper and its normalization: the removed
observer work must not be reported as an isolated product speedup. The two
candidate spec samples do not establish a comparable tail-latency distribution.
The defensible result is removal of298 redundant actual spec constructions
with the captured model/spec/geometry unchanged, not a whole-UI percentage,
50ms guarantee, GIL attribution or hardware-delay fix. Recorded sample counts
and linear quantiles were independently recalculated by the root agent.

This candidate changes neither native DSP nor hardware ownership, Fs/FFT,
analytical history, timestamps/gaps/epochs, recording, memory/queue budgets or
source isolation. Matching software/frozen qualification and changed physical
THREE/FOUR-source tests remain separate requirements. tinySA is included in
future four-pane rotations; its instrument scan duration is not a Qt defect.
APP-07/M7/M8 remains PARTIAL and the earlier physical access violation remains
unexplained. No current/static executable promotion is implied by this source
change or by offscreen component evidence.

Qualification follow-up: exact source commit
`34fdfb51e265a77dfd57ed2ea38706019e992be5` produced diagnostic package
`APP07-OPT116-20261010-34FDFB5-R1`. All48 native tests passed (70.74s).
The serial AFTER-freeze UI V2 source tests with that packaged native module
ran1596 tests in940.447s:1530passed,66skipped,0failures/errors. Compiled tests
were not deferred; no product module loaded from outside the checkout.
Source was tracked-clean before/after, native/source provenance matched,
callback-log audit found no uncaught traceback and the665-file package
manifest verified. These are software/packaging checks, NOT frozen visible
GUI, HIL, RF throughput, raster/DPI, whole-release or AV-fix acceptance.

The new cache branch specifically affects the fill/history-growth phase;
the unchanged original branch already reused compatible unknown-time axes
at stable/full row count. Its share of actual hardware UI work must be
measured, rather than assuming the same benefit in sustained operation.
Next: changed THREE hardware profile with bounded low-overhead observation
and terminal phase receipts, then tinySA-inclusive FOUR rotations, visible
2×2/DPI, stability/soak and release. No unchanged retry or timeout extension.

Changed hardware follow-up M78-117 used the SAME source34f/package116/native,
not a newer runtime. One three-source offscreen run completed with actual
process exit0 and normal Stop/owner join/graphics release/hook restoration.
HackRF20MS/s, AD9363 genuineEthernet30.72MS/s/RF30MHz and RTL2.4MS/s retained
FFT4096. Requested10s observation returned10.4528884s within unchanged10.5s
ceiling; initial fresh-bundle readiness took23.6775844s. No timeout was extended.

During that interval all three panels accepted13 new bundles each (about1.244/s).
The26 UNKNOWN-time growth-eligible HackRF/RTL setters retained their real
cached pictures and generated no new axis specs. AD9363's13 setters used
KNOWN-time axes and generated13 specs; this optimization does not cover them.
Across readiness/live, all57 eligible setters retained their pictures, with
no observer error, overflow or continuity violation. Independent read-only UI
review checked all442 raw timing samples and the counter/schema partitions.

The valid measurement window is NOT responsive-UI qualification: the16ms
heartbeat's maximum lateness was610.0299ms and live checker gaps reached
727.6361ms. AD axis paint total398.4014ms contains nested spec221.3792ms;
these wall times cannot be summed/subtracted for exclusive CPU/GIL attribution.
The bounded presentation queue held3 pending packets at both interval edges;
414 offers produced39 deliveries and375 latest-wins supersessions. This is
presentation coalescing, not an analytical FFT-drop measurement or exhaustive
queue-depth proof. Family-native counters and RF-time coverage remain separate.

Historical113 access violation was not reproduced in this changed run, but its
cause remains unknown: source, observer and terminal cleanup changed together.
No AV fix, whole Qt-delay repair, visible Windows/DWM/50ms, four-source, soak
or release acceptance follows. Raw evidence SHA256
`17fdd475a133e791716342b4d449b978c1c36bbeadcd37831ea505adba9945aa`.
APP-07 remains PARTIAL. Next is whole-UI callback/scheduling localisation and
tinySA-inclusive four-source rotations through actual typed resource bindings.

## Independent-pane persistence follow-up — 2026-10-11

Runtime source `a83b6ef860d9d85dea7a0de67679c0f1b00f7849` moves independent-pane
density image preparation from the Qt path to one optional lane per pane on the
existing shared executor and shared allocation ledger. Required spectrum stays
direct; the ordinary single-Analyzer persistence route remains unchanged.
Epoch/gaps, separate histories, full input shapes, native DSP/Fs/FFT, cadence,
quality/time and allocation/timeout guards are preserved. Quiesce, actual worker
retirement, GUI acknowledgment and graphics release remain distinct lifecycle
steps. No new SDR opener or hidden restart is introduced.

One changed synthetic Qt run completed naturally with separately reviewed raw
results. Full apply p50 changed from approximately59–61ms to2.2–2.8ms;
heartbeat interarrival maximum changed185.33→40.33ms. The old inner accept
handler was approximately3.6–4ms, NOT60ms. GUI thread CPU increased
2.5625→2.6875s, with different completed input prefixes272→412. Consequently
this is neither a controlled ABBA throughput/CPU improvement nor RF-to-display,
DWM/paint or universal50ms qualification. Worker-return-to-Qt/upload timing and
observer overhead require the separately qualified next diagnostic.

Diagnostic Windows build of exact runtime a83 passed48/48 native CTests
in69.66s, with621 source inputs and665 verified frozen files. It does not
replace the current/static application. The first full V2 source regression
failed8 tests: two test Stage adapters lacked the newly explicit allocation
budget keyword. Once reachable, one test's teardown also released graphics
before actual density retirement. These failures are retained, not hidden by
longer deadlines or weaker product guards.

Test-only commit `6b4710969574bace7afb8644868d252f2637d932` repairs ONLY those
two fixtures: SAME budget forwarding/identity and original quiesce/retire/release
ordering. Focused regression52/52 passed in64.218s. Distinct static review
approved fixture scope and the SAME-build verification method. The original
a83 source snapshot, package, native and dependency hashes were checked both
before and after ONE changed full regression on test checkout6b:
1631total/1565PASS/66skip/0fail/0error in916.622s, natural process exit0,
tracked-clean before/after, deferred compiled tests[] and outside-checkout
product modules[]. Log/provenance callback audit passed with zero uncaught
tracebacks; four historical NaN warnings remain. That audit does not prove
absence of every silently captured callback. Build/runtime identity remains
a83, never test-only6b or a subsequent documentation commit.

Full-regression receipt SHA256:
`4e4907b6dc4aaf11769e61f92ea68e49bf557aec5e3ede7bb04d82b010a638c0`.
Log SHA256:
`cca344d2b68f2701242444db1f0731aa307faae4f529677eed396ab6ba6b07b5`.

## Full-bandwidth acceptance boundary

RTBW qualification uses the full actually supported RF bandwidth and high
applied/read-back sample rate of each SDR. Do not reduce Fs, bandwidth, FFT,
density or analytical work merely to meet a50ms threshold. The historical
36MHz useful central Sweep crop is NOT an RTBW cap. Historical AD9363
30.72MS/s/RF30MHz comparison evidence is not proof of its current maximum;
current device capability/readback and genuine Ethernet route need separate
verification. ADC sample rate and usable analog bandwidth are distinct.

CI16 I+Q occupies4bytes per complex sample per RX:61.44MS/s requires245.76MB/s,
above even the ideal payload ceilings60MB/s USB2 and125MB/s1GbE. This rules
out continuous full raw transport, not every short buffered full-band FFT frame.
For262144 samples, user-reported effective recording rates6/13MS/s imply
43.691/20.165ms block-service estimates; these are not freshly measured pure
wire times and may already include capture/processing/disk. Do not add capture
again as an established measured fact. Host-side FFT cropping cannot reduce
raw bytes already transported.

Acceptance separates buffer/capture/service period, full/progressive Sweep
period, analytical-ready-to-Qt upload/paint, GUI responsiveness and RF coverage/
gaps. Transport limits do not excuse long Qt handlers. Next: qualify the new
private observer and bounded cleanup without RF, then ONE changed full-band
THREE and FOUR rotations including tinySA, visible Windows/FHD/QHD/DPI,
stability/soak and release. APP-07 M7/M8 and the full APP00–14 goal remain open.
