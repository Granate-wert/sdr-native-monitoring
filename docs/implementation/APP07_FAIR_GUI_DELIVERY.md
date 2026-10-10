# APP-07 UI V2: fair, soft-budget pane delivery

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
