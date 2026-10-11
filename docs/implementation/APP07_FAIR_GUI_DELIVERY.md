# APP-07 UI V2: fair, soft-budget pane delivery

## Ordered RTBW display preparation (2026-10-11)

Source increment `6e95ce777e0a85131a05ff94b376bc2babd9af6e` changes five UI
modules and adds the ordered-display test. It does not include the separate
experimental full-view repaint change. Later documentation commits must not
relabel this source or the earlier qualified runtime/build675.

Within one already bounded RTBW resource poll batch, every input retains its
original spectrum/small-Waterfall preparation, source/binding/crop/provenance
validation and full scaled-density numerical validation using accounted scratch.
Only the replaceable full persistence display matrix is materialized after
selecting the latest compatible carrier per pane. Invalid replaced input still
fails. Native IQ/FFT/detectors/recording/accumulation and acquisition rates are
unchanged; a reduced count of display packets is not analytical sample loss.

Pending state is worker-only and bounded to one carrier per occupied pane.
Run/activation/session/acquisition/configuration, receiver/pair epoch, gap,
quality/clock and grid/density-semantic changes flush pending inputs in original
order BEFORE the next input invalidates caches. Grids are content-compared.
Single-input preparation, Sweep/instrument full paths and terminal priority are
preserved. Selected matrices still use the original public conversion.

Known stale input reaches the original queue rejection. The preparation hint
shares that queue's locked predicate but never admits work or mutates counters.
Unavailable ordering falls back to mandatory validation and final offer,
preserving PREPARE versus QUEUE error priority; unexpected failures remain
diagnostic. Exact non-negative host-run typing is checked before replacement.
Refs replaced before construction end in PREPARATION_CANCELLED, not fictitious
PREPARED/QUEUE_SUPERSEDED. Stop/plan and first-failure pending/tail dispositions
remain explicit, with existing ownership/joins and independent peer isolation.
New ordered-validation/early-cancellation/pending-peak counters are presentation
metrics, not FFT/s, RF duty or capture drops.

The frozen candidate passed128 targeted/original tests and112 Qt regressions
(some overlap), with zero failures/errors/skips, plus Ruff/compile/scopedmypy.
Independent UI source review and saved-results review approved narrow source
integration, not release or hardware acceptance. Earlier counterexamples and
invalid measurements were retained, not relabelled as passes.

ONE finite synchronous A/B/B/A comparison used identical384 reduced-data inputs
per arm: three independent RTBW profiles20/30.72/2.4MS/s,4096 spectrum bins,
256x4096 densities, batches32, varying normalization and sequence stride128.
All12 selected outputs per arm matched the full selected array/scalar/source/
timestamp oracle. Mean poll wall was1129.28795ms before and787.86030ms after,
30.23% lower in THIS fixture. Whole-arm time INCLUDING drain/hash/oracle was
1182.98425→854.21805ms,27.79% lower. Materializations384→12 replaced372 original
post-prepare queue supersessions with earlier display cancellations; this is
NOT96.875% CPU acceleration. Accounted peak67592288→63338568bytes is not RSS.

This generates reduced data, not raw SDR traffic, and does not run GUI event
loops or acquisition/DSP workers. Nominal Fs are metadata, not readbacks.
No GUI FPS/GIL/50ms/physical loss/duty/Pd/sustained/soak or release guarantee
follows. Batch size/barriers affect real gains; single-input overhead remains
to be measured. Matching frozen software qualification and changed ordinary
rendering/physical multi-source tests remain required. APP07 M7/M8 PARTIAL;
static/current EXE and firewall are unchanged by this source increment.

## Earlier soft-budget GUI scheduling

This source change bounds how much additional pane-application work a Qt timer
turn starts. It does not cap a running handler or promise an 8 ms paint/frame.

`IndependentPaneDeliveryPort` keeps its default 16 ms timer. The additive
`turn_budget_ms` integer defaults to 8 and must be in `[1, interval_ms]`. A
nonempty turn takes its first packet, finishes the actual application/disposition,
then checks elapsed monotonic time before taking another. Each pane is serviced
at most once per turn, including rejection or failure. A long application,
signal callback or subsequent Qt paint can still exceed the allowance.

The existing bounded `PaneFairDeliveryQueue.drain` adds `excluded_panes=()`.
Only a tuple of distinct known pane identities is accepted. Exclusion does not
remove, acknowledge or supersede packets, change metrics or move the cursor.
The default preserves the old multi-pane drain API and terminal/latest priority.
Cursor movement follows real takes; remaining packets stay queued for later
turns instead of being drained and re-offered. A taken packet receives its
normal disposition, and a BaseException does not reject unattempted peers.

Nested turns cannot take work. Delivery-local Start/Stop generation changes
retire the old turn after the current packet's disposition and signals. This is
not a replacement for source epoch, board admission or Stop/custody barriers.
Manual `tick_once` on the owning Qt thread remains legal with the timer stopped.
No RF/FFT/Fs, SDK, acquisition owner, native DSP, recording, source identity,
timestamp/gap/quality or analytical buffer policy is changed.

## Measurement and qualification limits

The private comparison used three typed MOCK RTBW owners (HackRF 20 MS/s,
AD936x 30.72 MS/s, RTL 2.4 MS/s nominal configuration), FFT4096,
2048 presentation bins, full 300-row rings and an identical intentional 6 ms
delay per application. These are not real SDR streams or physical throughput.

One valid original window had delivery-tick p95 32.185 ms/500 applications.
Two valid candidate windows had p95 17.0255 and 16.113 ms/396 and 309
applications. Shorter turns came with fewer presentation updates, more latest
supersession and longer per-pane service gaps. UI supersession is not RF or
sample loss. Reception throughput and actual paint/DWM rate were not measured.

The next original-body overlay failed the unchanged 45-second event-loop
return fence; its intended 10-second interval stretched to 32.1158 seconds.
The cause remains unknown. The fixed comparison stopped at that failure;
rounds 5–8 were not run, and no retry/deadline relaxation was used. Therefore
there is no complete ABBA comparison, established product speedup percentage,
50 ms guarantee or fix claim for the historical physical Qt stall.

## Source verification

The exact five-file candidate passed 51 targeted tests and independent scoped
UI method, product and raw-results review. The root source-only UI V2 gate
ran 1579 tests in 837.224 seconds: 1513 passed, 66 skipped, no failures/errors.
Two compiled-native tests were explicitly deferred, and no product module
was imported from outside the checkout. Four historical NaN-validation warnings
were retained. The first targeted failure was a test adapter completing the
same Future after an empty drain; the adapter was corrected without weakening
product assertions or timeouts, and the original failure log was retained.

Source correctness, scoped independent UI review and software regression remain
separate from qualification. Changed-source visible/HIL tests, qualified time
labels, Sweep pressure, multi-source sustained performance and release
qualification remain required. The scheduling policy is provisional pending
those checks; do not advertise this change as higher FFT or reception rate.

This increment does not build or promote an EXE, change the static installation
or firewall, run physical RX, or qualify the full APP-07 epic.
