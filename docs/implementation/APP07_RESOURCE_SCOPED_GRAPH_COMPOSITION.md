# APP-07 resource-scoped Analyzer graph composition (partial)

Current state: the final section documents the newer user-operated V2
assignment editor and its bounded physical witness. Earlier "not yet present"
statements below describe the historical intermediate checkpoints, not the
current source. APP-07 is still partial: tagged frozen EXEs have passed bounded
functional cells, but no EXE has release/soak/performance qualification here.

`build_v2_analyzer_application_graph` is now the **same** graph assembly used
by the current single-source UI V2 shell. It can also be called with a new
`SdrApplicationServices` bundle for each independent physical resource. Graph
construction performs no Discover, Select, device open, RX Start or serial
command. Reusing one bundle/graph does not create another receiver.

`compose_v2_pane_resource_session` accepts an already compiled 1–4-slot
`PaneLayout`, exact acquisition groups, separately selected V2 graphs and one
shared receiver lease manager. It uses the existing AD936x RTBW, HackRF RTBW
and tinySA trace pane owners, not another SDK or renderer. It derives physical
identity keys and family from the **selected capability bindings**. Known
duplicate physical identities, reused native/catalog/family owners, wrong
endpoint/family or missing graphs refuse before a lease or RX Start. An
all-Empty layout returns no capture session. A selected source without a
canonical serial-backed identity may join a multi-resource plan **only when
its device family occurs exactly once**. This permits one route-scoped Pluto
alongside independently identified HackRF and tinySA, but never treats two
unidentified AD936x USB/IP routes as two physical receivers. Multiple sources
from one family still require distinct observed canonical identities;
different operational IDs alone never establish that fact.

The fake-SDK/serial three-family 3+1 test now uses this product graph builder
and composition seam: AD936x and HackRF RTBW publish dBFS/bin on different
ranges, tinySA publishes dBm on a third, and slot 4 remains Empty. Three
resource leases coexist; stopping the instrument leaves both SDR owners
active. Separate tests cover inert graph construction and refusal of two
AD936x routes with one canonical physical identity. This is **not** a visible
mixed-source UI, physical three-device RX proof or a frozen Windows EXE.

Still required for the end-user workflow: in-tab per-pane source/range/mode/
Empty editing, explicit Stage/impact Preview/Apply, then a current frozen EXE
on physical AD9364 + HackRF + verified tinySA. The existing `shared_views`
remains correctly labeled as views of one source.

## Independent UI V2 canvas packet (2026-09-29, partial)

`PaneDeliveryPreparer` binds every occupied slot to its exact resource,
capture, RX/trace endpoint, source, frequency crop, mode and unit. It admits
immutable `PaneDelivery` publications through one shared presentation budget
and prepares the existing Spectrum/Waterfall/persistence adapters off Qt.
Instrument `scanraw` uses its exclusive Stop edge; its last reported center
must not be mistaken for the right edge of the requested span. Cross-slot
and out-of-range deliveries refuse before a graph is touched.

`IndependentPaneBoardV2` renders three separate `AnalyzerPaneViewV2` graph
pairs in slots 1–3 and a genuinely Empty slot 4 with no graph allocation.
It accepts only a matching prepared binding, rejects duplicate/stale ordering,
keeps an independent viewport for each crop, and detaches the heavyweight
per-graph display controls into selected-pane overlays so 2×2 geometry fits
inside a 1600×920 board. The existing single-source `shared_views` behavior
and its renderer tests remain unchanged. The fake AD936x/HackRF/tinySA
owner-to-canvas test validates three distinct bundles, dBFS/bin versus dBm,
three waterfall histories, three crop ranges, and Stop-tinySA while both SDR
owners remain active. Its offscreen screenshot is synthetic test evidence,
not a physical RF screenshot or release asset. The board accepts a distinct
`SpectrumProjector` lane per pane from the future product composition; this
small fake harness uses the synchronous renderer and makes no high-density
paint-cadence or UI-latency claim.

The board owns no discovery or RF Start/Stop. It can now be attached to the
existing Analyzer tab by an externally previewed/applied
`PaneProductSessionHandle` and exposes selected/all Start/Stop there. The
in-tab assignment/Apply controller and current frozen EXE remain open. Its
earlier fake-only screenshot is superseded for the narrow three-family
receive/render question by the physical witness below, not by release
acceptance.

## Fair delivery and resource worker packet (2026-09-29, partial)

The standalone 2×2 canvas now has an optional bounded handoff and explicit
per-resource RX pump. `PaneFairDeliveryQueue` holds at most one latest packet
plus one terminal Sweep packet per occupied pane, rotates pane service and
counts supersession separately from any upstream RF, sample, scan or FFT
loss. A late terminal for pass N is delivered before an already queued
progressive pass N+1. `IndependentPaneDeliveryPort` uses one Qt timer and
applies at most one packet per pane per tick; worker threads neither paint
widgets nor post a Qt event per analytical FFT. Its cadence is a GUI
presentation target, **not** measured DWM FPS, physical scan frequency or
guaranteed frame latency.

After a caller explicitly previews and applies one exact plan,
`PaneResourcePump` can spawn one worker for each independently leased
physical resource. It has separate explicit Start/Stop futures, serializes
one resource's Start/poll/scheduled advance/Stop, and never invents a fourth
worker for an Empty slot. One stopped resource leaves its peers running.
For a shared one-RX time-sliced plan, elapsed slot duration alone cannot
trigger a retune: an RTBW pane must receive at least one admitted frame, and
a Sweep pane must receive a terminal Sweep publication first.
Stop of one pane in a shared resource requires acknowledgement of all
affected panes. Failed poll/Start/Stop retains the owner until explicit Stop
or retry; there is no automatic reopen or hidden recovery. The control
workers and Qt port now have an opt-in attachment in the same UI V2 Analyzer
tab. The default single-source view is unchanged until an explicit applied
independent plan is installed. The source/range editor which would let a
normal user create that plan is not yet present.

Fake-owner tests cover three independent resources plus Empty, exact
resource/plan matching, selected versus Stop-all, missing terminal versus
scheduled retune, and failure retention. A separate fake three-family graph
test reaches the canvas through the same queue/timer, with AD936x and HackRF
dBFS/bin versus tinySA dBm on different ranges. These are software boundary
checks, **not** simultaneous physical AD9364/HackRF/tinySA acceptance, a
current frozen EXE, fair-render load measurement or complete 2×2 end-user
workflow. A partial worker-launch failure now releases every pre-Start lease;
a failed release remains retained for explicit Stop retry.

## Physical three-family 2×2 witness (2026-09-29, bounded)

An explicit diagnostic runner used current checkout Python product code with
the older official, hash-checked APP-06D native/SDK siblings in **one Windows
process**. It staged three separately selected application graphs and applied
one exact 3+Empty plan: AD936x USB RX1 100–108 MHz, 20 MS/s RTBW/FFT 4096;
HackRF USB RX1 140–148 MHz, 20 MS/s RTBW/FFT 4096; tinySA Ultra serial
200–210 MHz, 101-point repeated instrument Sweep; slot 4 Empty. The current
USB Pluto exposed no stable serial-backed capability identity, so the
unique-family-only admission above was exercised. The tinySA was version-
confirmed before any scan.

In a visible Windows Qt **standalone pane widget**, after about 12 seconds
of simultaneous RX, the test observed 302 distinct presented Pluto bundles,
311 HackRF bundles and 33 tinySA traces in their respective graph pairs;
the instrument pane retained dBm and the two SDR panes dBFS/bin. The Empty
slot had no graph or worker. Stopping the selected tinySA pane via its button
left both SDR resources running, and each produced another publication after
that Stop. Stop-all and graph/serial/native owner shutdown confirmed for all
three. This is a real physical source/render witness, not a single-source
clone; a screenshot and raw JSON are retained as private local evidence.

A second visible Windows run installed the same externally applied handle
into the **full UI V2 AppShell's existing Analyzer tab**. After 12 seconds
it presented 304 Pluto, 303 HackRF and 34 tinySA bundles in three distinct
graph pairs while slot 4 remained Empty. Selected tinySA Stop again left
both SDRs running with subsequent publications; normal AppShell close
released the pane owners. This confirms the opt-in same-tab composition,
not a user-operable source-assignment workflow.

The measured counts are **not** analytical FFT/s, ADC/USB losslessness,
tinySA maximum-point throughput, RF duty/Pd, DWM frame cadence or a soak
result. The native package predates the current Python change, and neither
test was the final frozen EXE with a complete in-tab assignment UI.
APP-07 remains partial until that user workflow and a same-source frozen
physical qualification are complete.

## User-operated V2 source assignment (2026-09-29, partial)

The same Analyzer tab now has an explicit **Pane sources** editor with four
ordered slots. Each slot may be Empty or select an actually discovered source
and an independent frequency span. AD936x and HackRF currently expose their
qualified RX1 RTBW pane owners, with requested Fs/FFT choices; tinySA exposes
the device-reported dBm Sweep trace with 2..10001 requested points. The
editor does **not** silently convert an SDR wide Sweep into RTBW, claim RX2,
enable an amplifier/bias tee, or infer ADC readback from requested Fs.

One source ID is staged into **one** product graph, regardless of how many
panes use it. Compatible nearby panes share one capture with separate crops;
incompatible or disjoint panes receive bounded time-sliced capture jobs and
the Preview explicitly warns about RF gaps. Different physical sources use
distinct graphs/workers in parallel. The plan compiler rejects spans outside
its declared usable window; a current device snapshot additionally constrains
the tuning range when one exists. No missing capability fact is invented.

User actions are separated: Discover USB (or explicit USB + IP), edit slots,
**Stage/show impact**, **Apply layout**, then **Start selected/all**. Stage
rechecks the selected source on a control worker. The preview lists the
affected panes, capture-job count, sharing/time-slicing and recording
conflicts before Apply. Apply stages the initial AD configuration and reserves
exact leases, but does not Start RX. The old single-source commands are
disabled while a preview is pending. A failed Stage/Apply retains an owner
until explicit Discard/Stop/close; no retry, restart or RF recovery is hidden.
The user can return to the ordinary Analyzer only after explicit Stop of all
resources and **Close layout**, which releases graph owners off the Qt thread.
Human-readable selected-source labels are shown in pane headers while the
exact opaque source/endpoint identity remains available in a tooltip.

A visible Windows current-Python-source + older hash-checked APP-06D native
package diagnostic exercised the *user editor* with USB Pluto, USB HackRF,
COM31 tinySA Ultra and an Empty fourth slot, on 100–108, 140–148 and
200–210 MHz respectively. Stage and Apply had zero active RX; after explicit
Start all, three different graphs updated concurrently. A 12-second bounded
run presented about 300/304/34 Pluto/HackRF/tinySA bundles respectively.
Stopping selected tinySA left both SDR workers running; Stop all, Close
layout and normal AppShell close confirmed. Counts are GUI-admitted bundles,
not FFT/s, losslessness, RF duty or DWM FPS. The first diagnostic run exposed
a test-script click before the close button's enable state settled; a bounded
wait for the actual enabled action made the full explicit close path pass.
This was not a product auto-retry or receiver failure.

Still open: same-source frozen EXE build and visible qualification; genuine
Ethernet and 10001-point tinySA cells; per-pane SDR wide Sweep and qualified
RX2; recording conflict UX, localization/DPI/area/latency/load/soak gates
and release.
APP-07 and the full APP-00…14 objective remain **partial**.

### Exact post-commit physical checks

The exact pushed product source `36c3fd162c41ec1cfabea770f4a2ff79858f7863`
was exercised again in the full Windows UI V2 AppShell with the same older
official APP-06D native/SDK package. The in-tab editor selected USB Pluto
100–108 MHz / requested 20 MS/s / FFT 4096, USB HackRF 140–148 MHz /
requested 20 MS/s / FFT 4096, tinySA Ultra 200–210 MHz / 101 points, and
an Empty fourth slot. Stage and Apply each left active RX count at zero.
After the explicit Start, a 12.021-second observation admitted 309 / 313 /
35 GUI bundles into the respective panes and showed three active resource
owners. Selected tinySA Stop left the two SDR owners running; Stop all,
Close layout, return to ordinary Analyzer, and normal shell close passed.
The fourth slot had no graph or acquisition owner. A read-only, subsequent
USB IIO inventory found one Pluto and reported `hw_model` Z7010-AD9364 and
`ad9361-phy,model: ad9364`; its serial field was empty, so this is a
contemporaneous device-model check, not serial-backed identity attestation.

A separate 12.011-second user-editor check assigned that **one** USB Pluto
RX to two disjoint 100–108 and 200–208 MHz panes, leaving slots 3–4 Empty.
Preview showed one resource and two time-sliced jobs; Stage/Apply again
started no RX. Explicit Start produced 27 and 26 GUI bundles respectively,
then Stop all and Close layout completed. The screenshot showed both live
spectra, but the two waterfalls were sparsely populated, so time-axis and
waterfall cadence are still UX/performance debts. Paired libiio
`READ LINE/INTEGER: -9` text appeared at shutdown in this extra check;
earlier bounded diagnostics made cancellation a plausible cause, not a
universal harmlessness proof. No USB reset, hidden restart or suppression
was applied.

These are short current-Python-source physical observations, **not** a
current-source frozen EXE, lossless transport, RF duty/Pd, measured FFT/s,
DWM FPS or sustained performance acceptance. The APP-07 release gate remains
open; its physical one-RX multi-pane *functional* cell has bounded evidence.

### Exact-source Windows package boundary (2026-09-29)

A separate tagged official CPU/HackRF onedir package was built from exact
source `8c894afcf2d5d98ffa707f7585a7da7b387ccdd0` (product code is
unchanged from the physical user-editor witness). The native StageOnly build
passed 40/40 CTest. The 658-file package, one shared libusb, source/native
manifest, default/inert frozen UI V2 shell, libiio and tinySA load-only
checks all passed. The first build attempt refused a different system
libusb before freeze; the successful tag selected the already hash-checked
common runtime in a process-local environment, without replacing a system
DLL. The diagnostic EXE SHA-256 is
`829181b979569a91a953a3e4665e99cc791f1d76e602e6fc7f61595d431a4998`;
the staged/package native SHA-256 is
`34128b77888cb1b8a9a2178f51ecd953dd3f7811ecfd7276e68d6b0f2fdb4f95`.
An exact-source UI V2 gate passed 1050 tests / 66 expected skips / 0 failures.

The EXE launched to one responding UI V2 window and loaded its package-local
native/HackRF/libusb modules. Computer Use could not activate that returned
window on either the initial or one refreshed attempt, so no UI operation or
RX was sent. This is a package and idle-load witness, **not** a frozen 2×2
physical pass or release acceptance; the earlier source-Python live frames
must not be attributed to the EXE. The tagged candidate remains separate
from the approved static/current installation.

### Visible mixed-source 2×2 on that frozen EXE (2026-09-29)

A later Computer Use session successfully targeted the same exact
`8c894af` diagnostic EXE. Local USB Discover returned three choices.
Within the existing Analyzer tab the user editor assigned AD936x USB
100–108 MHz / requested 20 MS/s / FFT 4096, HackRF USB 140–148 MHz /
requested 20 MS/s / FFT 4096, and a tinySA candidate 200–210 MHz /
101 points. Slot 4 remained Empty. Stage verified the instrument as
tinySA Ultra and Preview reported three independent physical resources,
one job each; Apply displayed three labeled spectrum/waterfall pairs and
the empty fourth cell without starting RX.

Explicit Start all reached `running 3 / Stop required 0`. The visible
AD936x and HackRF spectra/waterfalls and tinySA dBm spectrum/pass-history
all populated before Stop. A second observation roughly four seconds
later showed changing SDR traces and additional tinySA passes. Selecting
pane 3 and using Stop selected gave `running 2 / stopped 1`; both SDR
traces continued to change on a further observation. Stop all reached
`running 0 / stopped 3 / Stop required 0`; Close layout returned to the
ordinary empty Analyzer and normal window Close removed the process.
No alternate source or hidden restart was used. A subsequent read-only
system IIO inventory found exactly one USB Pluto at `usb:3.4.5` with
`hw_model` Z7010-AD9364 and `ad9361-phy,model: ad9364`; its serial field
was empty, so this is a same-day single-device model check, not strict
serial-backed process identity.

This closes the short **functional** frozen-EXE mixed-source 3+Empty
cell that was open in the earlier paragraph. It does not establish
sustained performance, DWM FPS, LPS, RF duty/Pd, lossless transport,
high-Fs operation, in-memory DLL attestation or release acceptance.
Computer Use screenshots were inspected directly, not archived as
independent image artifacts. This frozen EXE predates the per-pane timing
row described below.

### Per-pane host timing for scheduled resources (2026-09-29)

Each occupied pane now has a compact timing row in the same UI V2 Analyzer
grid. A dedicated AD936x or HackRF capture and a tinySA instrument trace
show the age of the latest frame **accepted by the host router** and state
that the resource is not time-sliced. For disjoint panes sharing one RX, the
row additionally shows the last *observed* revisit interval and the
schedule's estimate from configured control/active costs. The internal
`maximum_revisit_s` is the maximum *within that deterministic model*, not
an upper bound on a physical device; actual Stop/retune/Start and first-frame
latencies can make it slower. The compact UI now labels the two values
`revisit / model` rather than `revisit / model max`. Empty
panes have neither a timing row nor a receiver.

An observed visit starts at the first accepted frame from a new confirmed
capture activation. Later FFT, partial Sweep and repeated tinySA
publications within that activation do not manufacture extra visits.
Rejected old tokens/cross-source frames do not update timing. Before a
second accepted visit, the observed interval is explicitly unmeasured;
after Stop, the retained plot is labeled as not new. A backward/invalid
host clock cannot publish a negative or cross-clock interval. Ages update
in coarse buckets to avoid rapidly changing text; the row remains outside
the plots. The RU/EN tooltip distinguishes these host observations from
GUI/DWM paint cadence, ADC rate, RF duty and pulse-detection probability.

This is software-level observability, not a new device command, RF timing
instrument or proof that the displayed graph has the same age as the most
recent host-admitted frame. The earlier physical 3+Empty and one-RX/two-
range runs predate this UI change; the separately frozen EXE above is also
stale with respect to this change. The next section records the new EXE
check. APP-07 remains partial pending the remaining topology, performance
and release gates.

### Exact per-pane-timing EXE and visible 3+Empty follow-up (2026-09-29)

The timing change was committed as product source `5eaafd4` and passed an
exact clean-HEAD UI V2 gate: 1051 tests, 66 expected skips, zero failures,
no product imports from outside this checkout. A new, separate official
CPU/HackRF package from that same source passed 40/40 native CTest and its
658-file frozen/source/native/shared-runtime checks. The tagged diagnostic
EXE is at
`dist/SDRNativeMonitoring-CPU-APP07-TIMING-20260929-5EAAFD4/SDRNativeMonitoring/SDRNativeMonitoring.exe`;
SHA-256 `0b9f7451c31fd0a677f3cb94dbef7f508a2973f73c1c688d51fcfb94b939695a`.
It did not replace the approved static/current installation.

Computer Use then repeated local USB Discover and the same in-tab AD936x
100–108 MHz / 20 MS/s / FFT4096, HackRF 140–148 MHz / 20 MS/s / FFT4096,
tinySA Ultra 200–210 MHz / 101-point, Empty-fourth-slot Stage, Preview,
Apply and explicit Start. All three occupied plots and waterfall histories
updated before Stop; all three new timing rows read a host-accepted data
age below one second in the sampled Live state. After selecting and
stopping only tinySA, its row said the retained frame was not new while
the two SDR rows stayed fresh and the SDR spectra changed. Stop all reached
three stopped resources without Stop-required error; Close layout and
normal EXE close completed. The physical same-RX two-range *measured
revisit* row remains tested in deterministic software only, not in this
three-independent-resource EXE run.

These are short qualitative Windows observations, not a frame-period,
paint/DWM FPS, LPS, RF duty/Pd, lossless USB or long-soak result. The small
observed window still leaves shallow graphs in 2×2; FHD/QHD/DPI and area
qualification remain open. APP-07 and release remain partial/open.

### One physical RX, two disjoint panes in the same frozen EXE (2026-09-29)

The exact `5eaafd4` diagnostic EXE above was separately launched through
Computer Use. USB Discover found three candidates; the user-facing editor
assigned the **same** AD936x USB RX1 to pane 1 at 100–108 MHz and pane 2 at
200–208 MHz, both requested 20 MS/s and FFT4096; panes 3–4 were Empty.
Stage/Preview showed one resource, two time-sliced capture jobs and RF gaps.
Apply opened the 2×2 board without starting RX. Explicit Start all yielded
`running 1`; both occupied spectrum plots changed while their separate
frequency axes remained in the assigned ranges. The host timing rows sampled
an age below one second and last observed revisits around 0.43–0.45 s per
pane, versus the **modeled** 0.16 s. This directly demonstrates that the
model is not a physical maximum. Stop all yielded `running 0 / stopped 1 /
Stop required 0`; both retained frames were marked not new. Close layout and
normal EXE close completed. The EXE still contains the old `model max`
wording; the following source-only wording repair has not been built into
that exact binary.

The source-only UI V2 wording repair uses `revisit / model` in RU and EN and
expands the tooltip to state that configured control/active costs are not a
device guarantee. No Fs, FFT, scheduling, native DSP or RX control changed.
The 0.43–0.45 s figures are short host first-accepted-frame revisit samples,
not an RF duty, ADC, paint/DWM, LPS or long-soak measurement. A sustained
device/profile-specific timing model and runtime feasibility policy remain
open; the UI must not present optimistic admission estimates as deadlines.

The same-RX test verified two changing **spectra**, not durable per-pane
waterfall history. Source inspection found that `AnalyzerPaneViewV2`
currently clears waterfall history when the producer session/epoch changes,
and `WaterfallPane` resets its ring when the incoming grid signature's
configuration generation changes. Scheduled Stop/retune/Start changes both.
Retaining a time-sliced pane's history across such visits, with explicit RF
gap separation and without merging different grids/units/sources, is an
open APP-07 correctness item. The generic single-source epoch-reset tests
must stay valid; this needs an explicit independent-pane policy and a
physical recheck rather than disabling all epoch resets.

### Later 2×2 device-availability recheck (2026-09-29)

The exact existing `5eaafd4` diagnostic EXE (SHA-256
`0b9f7451c31fd0a677f3cb94dbef7f508a2973f73c1c688d51fcfb94b939695a`)
was relaunched visibly for a requested AD9364 + HackRF + tinySA + Empty
recheck. Windows serial enumeration did not include the former tinySA COM31;
local-only USB Discover in the EXE returned **two** choices, AD936x USB and
HackRF USB. A read-only `iio_info` inventory found one USB Pluto context at
`usb:3.8.5`, reporting `Z7010-AD9364` and `ad9361-phy,model: ad9364`; its
serial field was empty, so the silicon check is not serial-backed process
identity. The requested *three-device repeat could not be performed* in
this session. It must not be reported as a new 3+Empty acceptance.

The available partial physical cell used the in-tab 2×2 editor: pane 1
AD936x RX1, 100–108 MHz, requested 20 MS/s and FFT 4096; pane 2 HackRF
RX1, 140–148 MHz, requested 20 MS/s and FFT 4096; panes 3 and 4 Empty.
Stage preview showed two independent resources and one capture job each;
Apply did not start RX. Explicit Start all reached `running 2` and both
assigned Spectrum/Waterfall pairs populated with separate frequency axes
and host data-age labels `<1 s`. Stop selected on pane 1 changed its label
to retained/not new while pane 2 remained fresh and `running 1`; Stop all
reached `running 0 / stopped 2 / Stop required 0`. Close layout returned to
the ordinary Analyzer, and the EXE was closed normally. A current working-
tree synthetic three-family + Empty focused suite passed 10/10 tests; it
does **not** replace the absent physical tinySA cell. No new product EXE,
source edit or performance/soak/RF-duty acceptance is claimed by this check.
The earlier exact `5eaafd4` 3+Empty physical witness above remains the
evidence for that short functional scenario until tinySA is reconnected.

### Time-sliced RTBW pane waterfall continuity (2026-09-29)

An independent pane now retains its own bounded Waterfall history across a
**confirmed scheduled revisit** of one RX. This is deliberately narrower
than turning off epoch resets: the compiled plan must mark that pane
time-sliced on a resource with multiple capture jobs, the admitted delivery
must advance the host activation serial and producer acquisition epoch, and
the pane must retain the same source, receiver, clock domain, unit and exact
physical frequency grid. A new producer session, configuration generation
or persistence accumulation may legitimately accompany that new visit.
The Spectrum measurement and persistence still reset at its epoch boundary.

On the first admitted RTBW row of a qualified revisit, the Waterfall ring
inserts one all-NaN **presentation-only** separator before the new row.
That marker is neither an FFT nor a measured RF-loss count or gap duration;
it consumes ordinary bounded display history. Freeze defers the separator
until an actual row is admitted. Missing producer epoch progress, changed
grid/source/receiver/unit/clock, Sweep mode and ordinary one-source panes
retain their fail-closed history reset. Subsequent FFTs in one activation
do not create more separators. The test suite covers these guards, two
independent pane histories and the generic epoch reset.

Exact product `b4a067bd10a54560386db2b5767391f63389b1fc` passed the
postcommit UI V2 source gate: 1054 tests, 66 expected skips, zero failures,
no product imports outside the checkout. A separate tagged official
CPU/HackRF build passed 40/40 native CTest and its 658-file frozen/source/
native/shared-runtime checks. Its EXE is
`dist/SDRNativeMonitoring-CPU-APP07-WF-20260929-B4A067B/SDRNativeMonitoring/SDRNativeMonitoring.exe`,
SHA-256 `dba197f0b19f6446e7b733b3e589278e67470c61a9625eafef6c6238ab220d72`.
It did not replace the approved static/current installation.

Computer Use operated that exact EXE visibly with one discovered AD936x USB
RX1 assigned to 100–108 and 200–208 MHz, requested 20 MS/s and FFT4096 in
both panes; panes 3–4 were Empty. Stage showed one resource/two time-sliced
jobs with RF gaps, and Apply did not start RX. Explicit Start all showed
`running 1`, two changing Spectrum traces and two Waterfall histories that
grew across multiple alternations instead of resetting to one row. Thin
blank horizontal separation was visible in the short 2×2 observation; the
EXE did not expose an exact per-pane separator count. Host age stayed below
one second and sampled revisits were about 0.43–0.46 s, versus the configured
0.16 s model, which is not a device maximum. Explicit Stop all reached
`running 0 / Stop required 0 / stopped 1`; both panes marked their retained
frame not new. Close layout and normal EXE close completed, and the
658-file manifest reverified afterward.

This closes a **short same-RX frozen-UI waterfall continuity cell**, not
sustained correctness or speed acceptance. The visible separation is a
presentation marker, not a timestamped RF-gap duration or loss count. The
currently absent tinySA serial port prevented a new three-family 3+Empty
physical repeat; the prior exact `5eaafd4` short 3+Empty witness above
remains separate evidence. FHD/QHD/DPI legibility, measured timing
distribution, RF duty/Pd, ADC readback, lossless transport, paint/DWM FPS,
LPS and soak remain open. APP-07 and the release remain partial.

### Responsive three-source + Empty geometry (2026-09-29)

The UI V2 independent-pane surface now keeps its **same four slots and graph
objects** while adapting the presentation to available logical width. At
1200 logical pixels or more, the normal 2×2 grid remains. Below that, the
existing cells reflow into one column inside a vertical scroll viewport;
no owner, source assignment, capture, epoch or graph is recreated. The
command/status rows remain outside the scroll viewport. The selected-slot
actions show compact `Start/Stop N` labels, with full localized meanings in
their accessible names and tooltips. The vertical scrollbar follows the
current V2 theme. This is a visibility fallback, **not** simultaneous
four-panel visibility on physically insufficient screen area.

A fresh-process, inert Qt 6.11.1 matrix used source-like AD936x, HackRF and
tinySA trace bindings with distinct 100–108, 140–148 and 200–210 MHz ranges;
slot 4 stayed Empty. FHD logical viewport probes at 100/150/200/300% used
1920×980, 1280×640, 960×460 and 640×280; QHD probes at 100/150/200/300%
used 2560×1340, 1707×860, 1280×620 and 853×380. The first two FHD and
first three QHD cases kept 2×2 without scrolling. FHD 200/300% and QHD
300% used the one-column vertical fallback with no horizontal scrolling;
all four slots remained reachable, the sampled timing text fit all three
occupied panes, and the requested viewport was not enlarged. The compact
FHD 300% case retained at least 140 logical pixels of Spectrum and 80 of
Waterfall per occupied pane on the virtual canvas. A full applied fake-owner
Qt session separately resized 1280×700 → 960×460 → 640×280 → 1280×700:
commands stayed in bounds, the Empty fourth slot was reachable, no SDK/RX
started and the three graph instances survived the reflow unchanged.

The exact precommit UI V2 source gate passed **1057 tests, 66 expected
skips, zero failures**, outside-checkout product imports `[]`, in 312.308 s;
four historical NumPy invalid-comparison warnings remain. Scoped Ruff,
isolated mypy, compileall and diff checks passed. The ordinary mypy run
still reports 16 pre-existing errors in four imported files, with none in
the edited board/session files. These are **synthetic/offscreen widget
geometry** results, not a Windows per-monitor DPI, full AppShell area,
visible EXE, physical RF, paint FPS/LPS or long-soak acceptance. The 65%
measurement-area objective in the original review applies to the base
single-pane 1366×768 view, not automatically to every 2×2 configuration.

After this source gate, COM31 reappeared. A bounded read-only `version`
request classified it as tinySA Ultra (`tinySA4_v1.4-200-g26fc821`);
no measurement was started by that probe. A new exact-source frozen
AD9364 + HackRF + tinySA + Empty physical repeat is the next separate
qualification cell. APP-07 remains partial.

### Exact frozen three-family 2×2 physical repeat (2026-09-29)

The committed UI V2 source `4a777e3412c4c48d2e0a00d8f3577d71b12d32e9`
was packaged as a separate official CPU/HackRF diagnostic EXE at
`dist/SDRNativeMonitoring-CPU-APP07-GEOM-20260929-4A777E3/SDRNativeMonitoring/SDRNativeMonitoring.exe`.
The EXE SHA-256 is
`de189ed7c39b4c33966d063ceaa1cb618154d2f69bce4b55c16452c1ce3cae7b`.
The pipeline passed 40/40 native CTest, unchanged source-snapshot checks,
and its 658-file frozen/native/shared-runtime verifiers. The approved
static/current installation was not replaced. After the physical run,
the same 658-file package manifest verified again.

Computer Use controlled this exact EXE in a visible Windows UI V2 window.
Local-only USB Discover found three candidates. The applied four-slot plan
used **one separate physical resource per occupied slot**:

| Slot | Source and receiver | Requested range | Presentation |
| --- | --- | --- | --- |
| 1 | Pluto-class AD936x USB, RX1 (`de052fa5`) | 100–108 MHz | RTBW, dBFS/bin, requested 20 MS/s, FFT 4096 |
| 2 | HackRF One USB, RX1 (`812715f0`) | 140–148 MHz | RTBW, dBFS/bin, requested 20 MS/s, FFT 4096 |
| 3 | tinySA Ultra USB (`b49b5c53`) | 200–210 MHz | device sweep, 101 points, calibrated device dBm |
| 4 | Empty | — | no source or acquisition job |

The read-only local IIO context query after normal shutdown identified the
first Pluto-class device as `Z7010-AD9364` with
`ad9361-phy,model: ad9364` at `usb:3.8.5`. The tinySA read-only `version`
query identified `tinySA4_v1.4-200-g26fc821`; no firmware or device
settings were changed by these identity probes. The UI source IDs are
short discovery identifiers, not unique serial-number or RF-path proof.

Stage/Preview showed three independent resources and one job per resource;
Apply remained at `running 0` until the explicit Start all. The visible
session then reached `running 3 / starting 0 / Stop required 0` with the
three different frequency axes. Both SDR Spectrum/Waterfall pairs changed
while live; the tinySA produced its own dBm trace and successive device
sweep rows. The Empty fourth cell never acquired a frame. All occupied
panes reported recent data (`<1 s`) during a short observation. Explicit
Stop selected on slot 3 reached `running 2 / stopped 1` while both SDR
plots continued changing; tinySA correctly retained its last frame as
**not new**. Explicit Stop all reached `running 0 / stopped 3 /
Stop required 0`, and all three frames were marked retained/not new.

At the actual 1440×912 maximized window, all four cells appeared in 2×2.
Restoring that same stopped window to 1154×760 reflowed the **same** cells
into one scrollable column; dragging the board scrollbar exposed the tinySA
trace and Empty fourth cell. Maximizing again restored 2×2 with all three
retained frames and no new RX. Close layout returned to the ordinary
Analyzer; normal EXE Close left no process window. This is a visible
responsive-window witness at two sizes, **not** Windows per-monitor
100/150/200/300% DPI qualification. The offscreen DPI matrix above remains
separate evidence.

This closes the short three-family independent-source **functional** 2×2
cell. It does not establish FFT/LPS or paint/DWM rates, acquisition
continuity, RF duty/Pd, lossless USB, exact physical sampling, calibrated
RF accuracy beyond the instrument's own dBm output, simultaneous
instant-by-instant capture, extended soak, or release acceptance. Slower
tinySA device sweeps are normal and are not an SDR-rate defect. APP-07 and
the full APP-00…14 roadmap remain partial/open.

### HackRF host Sweep in the same independent-source 2×2 (2026-09-30)

Product commit `2ad0a106b9b3e239cf71c59df46b9e18f6c1560b` adds a typed
HackRF Sweep pane profile and reuses the existing application service,
resource lease, Sweep router and shared Spectrum/Waterfall delivery path.
It does not make a separate HackRF SDK owner. The user-operated UI V2 pane
editor now offers HackRF RTBW or host Sweep under one source assignment;
Sweep uses fixed nominal 20 MS/s, FFT1024 or FFT4096, whole-MHz bounds and
20–320 MHz spans in 20 MHz steps. Its frequency crop excludes two FFT bins
at each nominal segment edge because the native Sweep line omits edge bins.
This is a conservative display admission rule, not instrument calibration
or an assertion of flatness. The scheduler can explicitly time-slice RTBW
and Sweep on one HackRF receiver; no hidden restart or synthetic FFT overlap
was introduced. AD936x wide Sweep remains refused by this particular pane
admission packet, not globally removed from the application.

The exact source gate passed 999 UI V2 tests, 66 expected skips and zero
failures, plus focused owner/editor tests. A separate official CPU/HackRF
diagnostic EXE passed 40/40 native CTest and the 658-file frozen/shared-DLL
checks. The tagged EXE is
`dist/SDRNativeMonitoring-CPU-APP07-HFSWEEP-20260930-2AD0A10-R3/SDRNativeMonitoring/SDRNativeMonitoring.exe`,
SHA-256
`a7290fae89d0c3608c116cc5a18a23a7b7c76f3b99266c37a6e229990cdd750e`.
The same 658-file manifest verified after the visible run. It did not
replace the approved current/static installation.

Computer Use visibly staged **three independent resources** in one 2×2
layout: pane 1 AD9364-class USB RX1 RTBW at 100–108 MHz/requested 20 MS/s/
FFT4096; pane 2 HackRF USB RX1 host Sweep nominal 140–260 MHz/20 MS/s/
FFT4096 (display crop about 140.01–259.99 MHz); pane 3 tinySA Ultra USB
device sweep 200–210 MHz/101 points in device dBm; pane 4 Empty. Apply did
not start RX. Explicit Start all reached running 3 with recent, changing
data in all occupied Spectrum/Waterfall views. The HackRF pass numbering
advanced while the other two sources stayed live. Stopping only pane 2
reached running 2/stopped 1 and left AD9364 and tinySA updating; Stop all
reached running 0/stopped 3/Stop-required 0. All stopped panes labelled
their retained last frame as not new. The actual maximized 1440×912 window
showed four 2×2 cells with the Empty slot truly unused; Close layout and
normal EXE exit completed.

This is a short **mixed-mode, mixed-source functional witness**, not a
benchmark or release gate. It does not establish analytical FFT/s, native
publication LPS, paint/DWM FPS, lossless transport, RF duty or pulse-detection
probability, exact ADC sample rate, long soak, DPI matrix or sweep-bin RF
flatness. The tinySA's naturally slow scan is not an SDR performance defect.
APP-07 remains partial; sustained timing, density, failure recovery and
independent review are still required.

### Explicit shared-RX Stop confirmation (2026-09-30)

Product commit `06d7721c5f82c8bbf94ebdc6e14ad2b9546ccb7f` fixes the
selected-pane Stop dialog for a resource serving more than one pane.
The Qt return is compared to Yes **by value**, not Python object identity.
No remains the default. No new receiver, hidden restart, scheduler policy,
Fs/FFT/detector setting or RF-continuity claim is introduced.

Three regression tests cover an equivalent integer Yes return, actual Qt
No-default/explicit-Yes button activation, and the actual pane widget with
fake RTBW/Sweep owners: refusal preserves RX; acknowledgement stops both
affected panes and releases the one resource. The real-modal test fails
under the old identity expression, so this is not merely a permissive mock.
Exact tracked-clean full UI V2:1068 total/1002 passed/66 skipped/zero failed,
380.168s, no deferred compiled test and no product import outside the
checkout. Four historical NaN warnings remain; scoped checks passed.

The new tagged diagnostic full official CPU/HackRF pipeline passed40/40
CTest, source-snapshot, shared-runtime and658-file frozen verification.
EXE:
`dist/SDRNativeMonitoring-CPU-APP07-SHAREDSTOP-20260930-06D7721-R1/SDRNativeMonitoring/SDRNativeMonitoring.exe`,
SHA-256:
`cdc7913dd0db6fe5f4aa482851cd646c5ff09fdb6418204da4e494dfe7657f15`.
The unchanged native SHA34128b77… was used; the current/static installation
was not replaced.

On that exact EXE, visible Computer Use staged one physical USB HackRF RX1
with two time-sliced jobs: pane1 RTBW100–108MHz/requested20MS/s/FFT4096;
pane2 host Sweep140–180MHz/fixed20MS/s/FFT4096; panes3/4 Empty. Stage
announced the affected panes and RF gaps; Apply did not start RX. Explicit
Start all produced changing spectra/running1. Stop selected showed both
affected panes, No left the resource running, and explicit Yes stopped
the common RX: running0/stopped1/Stop-required0, both last frames labelled
retained/not new. Close layout and normal EXE exit completed; the same
658-file frozen package verified afterward. The previous exact2ad EXE
still failed the affirmative action in the matching short scenario.

This qualifies this shared command's short **functional** behavior, not
click-to-idle timing, DWM/FPS/LPS, RF duty/Pd, lossless transport, prolonged
soak or release readiness. The time-sliced Sweep Waterfall still resets
history on each job epoch, unlike the separately qualified RTBW history
with absence separators. Extending its same-grid/provenance/gap policy is
the next bounded APP-07 task; no continuous RF coverage may be fabricated.
APP-07 and the overall roadmap remain partial/open.
