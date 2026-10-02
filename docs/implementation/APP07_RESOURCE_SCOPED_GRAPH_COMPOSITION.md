# APP-07 resource-scoped Analyzer graph composition (partial)

Current state: the final section documents paired RTBW pane preparation and
the future four-source matrix with RTL in pane4. Earlier "not yet present"
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

### Qualified scheduled Sweep history (2026-09-30)

Exact product `ca4d0249c9fe360d9b5ededdb4c3d9909882a8f2` repairs the
historical Sweep-history limitation above. The same independent-pane
handoff policy now applies to both RTBW and Sweep: an immutable admitted
pane binding on a multi-job time-sliced resource, newer host activation,
different known producer epoch, same mode/source/RX/clock/unit/exact grid
and unchanged instrument display-value context. No DSP/owner/scheduler
change, synthetic RF continuity or hidden restart is introduced.

The capacity-bounded Sweep ring keys passes by **producer epoch + original
sequence**. Progressive revisions and Complete/Gap terminal update only
their own retained row. A confirmed newer epoch inserts one blank Pause
row, with no pass stamp or acquisition timestamp; its RF duration is
unknown. Late old-epoch and evicted updates cannot overwrite current data.
Resize preserves the active segment/cursor. Freeze defers this marker to
the next admission, Hide still admits bounded rows and Clear remains local.
Ordinary one-source epoch changes still reset history. Spectrum/statistical
layers still reset at the measurement epoch; retained Waterfall rows are
not cross-epoch analytical accumulation. Multi-epoch pass labels include
`E<epoch>`; RU/EN tooltip explains P/C/G and the unknown-duration separator.

Eight new regression methods cover epoch collisions, partial→terminal,
wrap/eviction/resize, freeze/hide/clear, compiled two-Sweep board+Empty3/4,
foreign/unqualified/grid/unit/value-context guards and axis directions/
locales. Focused61pass; exact tracked-clean fullV2 **1076total/1010pass/
66skip/0fail340.225s**, no deferred compiled test or outside product import,
unchanged source/native; four historical NaN warnings remain. Scoped Ruff,
isolated mypy, compile/diff PASS. Full official CPU/HackRF package pipeline
passed **40/40 CTest31.53s**, source-snapshot/shared-DLL and658-file frozen
gates; unchanged native34128b77…/C++, no current/static promotion.

Tagged diagnostic EXE:
`dist/SDRNativeMonitoring-CPU-APP07-SWEEPHISTORY-20260930-CA4D024-R1/SDRNativeMonitoring/SDRNativeMonitoring.exe`.
SHA256 `9c3984092a5aa779d527017f173b2e965fdfb7705664b01273b8f27bc9738ed7`.
Visible Windows Computer Use on that exact EXE staged the same physical
USB HackRF RX1 into Sweep140–180MHz and300–340MHz, fixed nominal20MS/s,
FFT4096; two other cells Empty. Stage announced one resource/two time-sliced
jobs/RF gaps, Apply did not start RX, explicit Start all did. Both live
Spectrum/Waterfall pairs showed retained prior epochs and separators.
Pane1 local Freeze left RX/pane2 live; explicit unfreeze resumed its history.
Explicit Stop all retained both histories/not-new, then normal Close layout/
EXE exit and post-run658-file verification passed.

This is a bounded **functional** witness, not DWM/FPS/LPS,50ms,lossless
transport, RF duty/Pd, native leak or sustained release acceptance. Sweep
history capacity controls still need truthful accessibility names (not
seconds/rows-per-second), and independent-layout failure/cleanup, fairness,
sustained timing/density and independent review remain OPEN. APP-07 remains
PARTIAL; APP-05/06 debts and APP-08…14 are not closed by this packet.

### Independent failures, accessibility and high-Fs Stage profile (2026-09-30)

The composed UI V2 graph has regression coverage for independent owner Start/
poll/Stop failures, cancellation of a consumed tinySA response, accepted Start
that cannot be cancelled before cleanup, and retained claim/graph release
failures. Explicit cleanup retry does not silently reopen or restart RX.
Tests retire the actual Qt presentation fixture through DeferredDelete after
terminal owner release. Sweep history controls expose capacity in row blocks,
not RTBW seconds or rows/s; the two new labels are localized in RU/EN.

Product source 0df150cd11bb320c37f93c5e123d80e1927e0089 also separates the
61.44-MS/s AD936x user-plan RF filter request (40 MHz) from its unchanged
36-MHz usable analysis window. It does not weaken exact applied/readback Stage
guards, expand pane coverage, change FFT/hop/detector or implicitly restart.
The low-rate10-MHz RF/window profile is unchanged. Regression fixtures cover
high-Fs exact admission, shared36-MHz coverage and over-limit refusal.

Exact tracked-clean0df full UI V2:1086 total/1020 passed/66 skipped/zero
failures in280.481s, no product import outside the checkout, unchanged source/
native and exact provenance. Historical NaN warnings remain. The exact0df
official CPU/HackRF build passed40/40CTest31.20s,658-file frozen verification
and inert/load-only shared-runtime gates. Native34128b77…/C++ is unchanged;
the separately tagged diagnostic EXE was not promoted to current/static.
Earlier Qt-crashed and RU-failed gates are not counted as passes.

This qualifies software contracts, not a healthy simultaneous physical3RX
matrix on the new EXE, DWM/FPS/LPS,50ms,lossless transport,RF duty/Pd or soak.
Those cells and independent review stay OPEN; APP-07 remains PARTIAL.
The additional planned APP-06E filter branch is described in
[APP06E_SDR_CUT_DC_SPUR_PLAN.md](APP06E_SDR_CUT_DC_SPUR_PLAN.md);
it introduces no implemented-filter claim in this packet.

### High-Fs three-family witness and explicit stopped-resource next run (2026-09-30)

The exact0df diagnostic EXE was operated through the visible UI V2 assignment
editor: USB AD936x RTBW100–108MHz/requested61.44MS/s/FFT4096, USB HackRF
host Sweep140–180MHz/fixed20MS/s/FFT4096, qualified tinySA Ultra device
Sweep200–210MHz/101points, fourth Empty. Stage/Preview, Apply and Start all
were separate actions. Apply left RX stopped. All three occupied Spectrum /
Waterfall pairs then changed over several minutes, with running3 / starting0 /
Stop-required0 and recent host-data labels. AD applied-readback logging
confirmed61.44MS/s,40-MHz RF filter,104-MHz center and20-dB gain without
adjustments; useful pane coverage remains36MHz. Nominal ADC Fs is not the
host-admitted I/Q transport rate.

Explicit Stop of tinySA alone left both SDR peers changing, running2 /
stopped1, and labelled the retained instrument frame not new. That EXE
exposed a genuine lifecycle gap: a stopped resource could not Start again
without closing/re-staging the whole layout. Stop all, Close layout and
normal EXE Close completed. Active process inventory during three-source RX
found package-local native, libiio, HackRF, pthread and one shared libusb;
their five disk hashes matched the release manifest. This is a bounded
mixed-mode functional/loaded-path witness, not process-memory attestation,
all-dependency active ABI, DWM/FPS/LPS,50-ms,lossless USB,RF duty/Pd,DPI or
release/soak acceptance. Genuine Ethernet remains unqualified.

The current source adds an **explicit next Start** for a cleanly stopped
resource, over its SAME retained selected V2 graph. An inert family-specific
factory constructs a new control adapter; endpoint/job/recording/producer-RX
guards and a new exact resource lease precede the family's usual Start.
That Start, not the resource lease, obtains its new single-use native/serial
permit through the existing backend. No Discover, peer graph/owner replacement,
same-permit retry or automatic restart is introduced. A failed Stop/claim
release still retains the same owner/lease and prohibits Start. Failed new
Start retains the new owner for separately explicit Stop/cleanup.

The same bounded per-resource control worker waits without polling after
Stop; only an explicit Start can re-arm it. Terminal layout shutdown first
seals all Start commands, joins every control worker and retires the session's
factories before graph close. A Stop queued before new-run SDK dispatch can
release its fresh lease without opening RX. An accepted Start is immediately
visible as Starting, so duplicate Start and terminal Close cannot race the
fresh-lease admission. Healthy peers keep their existing admissions and jobs.

Host run serials distinguish explicit Stop→Start from scheduled RX visits.
Late activation/epoch packets still refuse; worker layer caches reset only
for the re-armed resource. The first new-run frame resets analytical/display
history instead of inheriting an old run as a scheduled revisit. Subsequent
qualified visits within that run retain the established bounded gap-marked
history policy. Retained stopped frames are never labelled fresh.

The new source lifecycle path still needs an exact clean-HEAD full source
gate, a new frozen EXE and a matched physical Stop→Start witness; the old0df
EXE above does not contain it. Independent review, sustained/fairness and
APP-05/06 transport/performance debts and APP-08…14 remain OPEN. APP-07 is
PARTIAL and the APP-06E Cut DC/spur branch remains separately PLANNED.

### Exact new-run software/frozen and physical result (2026-09-30)

Product90fa168e9df71b998c82886c0c9694dcfbc223b4 completed the qualified
Qt6.11.1 clean-HEAD gate:1092 total/1026 passed/66 skipped/zero failures,
321.233s, deferred[]/outside[] and unchanged source/native/exact provenance.
The first Qt6.11.2 run failed one existing strict raster-runtime guard; it
was not weakened or counted as passing. Scoped focused/lint/type checks pass;
historical NaN/runtime/hook debts remain. Full official CPU/HackRF package
passed40/40CTest31.89s,658-file frozen verification and inert shared-runtime
gates. Native34128b77… unchanged; separate diagnostic package/source90fa168,
EXEc131d3e8…, actual freezer6.21.0/Qt6.11.1, no current/static promotion.

Visible Windows Computer Use on THAT new EXE repeated separate Stage/Apply/
Start all for the same high-Fs three-family2x2/Empty profile above. Initial
three Spectrum/Waterfall pairs changed. Selected Stop/explicit next Start
of HackRF Sweep and AD936x RTBW gave new changing histories while their SDR
peer remained live. AD repeated readback61.44MS/s/RF40MHz/104MHz/gain20/CPU
without adjustments; no Fs/FFT/hop/detector/native-density change.

tinySA Stop enabled its explicit next Start; it produced new frames/history,
then entered Resource error/explicit Stop required after~57 new passes.
Both SDR peers kept changing. Existing aggregate diagnostics did not identify
whether serial, producer identity or pane publication failed; root cause
is UNKNOWN. This is **not stable physical tinySA restart acceptance**, and
must not be attributed to naturally slow instrument sweep without evidence.
Selected explicit Stop cleared its quarantine; no third Start, reset, hidden
reopen or automatic recovery was attempted. Bounded fixed/redacted first-cause
diagnostics followed by a matched next-Start witness are the next open cell.

Five simultaneously loaded package-local native/IIO/HackRF/pthread/one shared
USB disk hashes matched the release manifest. This is not all-nine active
ABI/process-memory/global-SDK qualification. Stop all/Close layout/normal EXE
Close completed with running0/Stop-required0 and no process/window remaining.
Post-witness658-file/native-load verification passed unchanged. No DWM/FPS/LPS,
50-ms, lossless USB, RF duty/Pd, FHD/QHD/DPI, genuine Ethernet, sustained/soak
or release acceptance is inferred. APP-07 remains PARTIAL; the independent
review, fairness/shared topology and APP-05/06 debts plus APP-08…14 are OPEN.

### Wider physical profiles and missing interaction/plan cells (2026-09-30)

Additional bounded Windows tests used the SAME product90fa168 diagnostic
CPU EXE, not a new product build. Standalone AD936x USB RTBW confirmed
applied61.44-MS/s/56-MHz RF filter and56-MHz viewport with changing Spectrum,
native persistence and Waterfall for about98s. Host-admitted I/Q remained
roughly7.1–8.4MS/s, not continuous61.44MS/s. Standalone HackRF requested
20-MS/s/20-MHz RF filter/FFT4096/hop2048 reproduced no live frame at group1
for more than a minute; Stop delivered one retained frame. A separately
explicit Stop/Stage/new Start with group8 produced changing graphs. This
does not accept group1 freshness, prove its root cause, attest hardware
Fs/filter readback or preserve every transient. No hidden grouping change.

Standalone tinySA100–300MHz/1001 points produced changing device-dBm traces
and Waterfall at about0.60s per complete pass. Requested300-kHz RBW could
NOT be applied/read back because the observed firmware's runtime-settings
command contract was unqualified. Settings remained preserve-only. Instrument
identity qualification is not universal shell-settings qualification, and
a disabled UI default is not an actual RBW readback.

A wider physical mixed2x2 used AD936x RTBW36MHz at61.44MS/s, HackRF
Sweep320MHz at20MS/s, tinySA Sweep200MHz/1001 points and Empty4. Separate
Stage/Apply/Start all gave three changing Spectrum/Waterfall pairs; one
HackRF partial admitted trace was visible before complete range coverage.
Selected tinySA Stop left both SDR peers changing; Stop all/Close layout/
normal EXE Close completed. Post-witness658-file package/native verification
passed unchanged. A stable initial tinySA run does not resolve the previously
observed error after explicit next Start. No full-range all-Sweep, sustained
fairness, losslessUSB, RF duty/Pd, DWM/FPS/LPS,50-ms,DPI/soak claim is made.

Current independent-pane admission still limits AD936x to RTBW with36-MHz
usable span at61.44MS/s, HackRF RTBW to10-MHz usable span, and HackRF pane
Sweep to whole-MHz20–320-MHz spans in20-MHz steps/FFT<=4096. Thus standalone
AD56/HackRF20 success cannot be relabelled independent-pane support. Full-range
Sweep for all three physical sources is NOT admitted and was not performed.
The next Sweep packet must qualify the existing AD936x Sweep owner in the
same resource composition and justify wider geometry/memory/deadline budgets,
not simply remove bounds. tinySA per-pane manual RBW is also still missing.

Real V2-widget offscreen tests with fake owners showed that held-middle drag
currently pans Spectrum and linked Waterfall viewport only. Receiver center,
Sweep bounds, producer grid and generation do not change. Desired analysis-
frequency translation is NOT implemented. Physical Windows held-middle input
was not verified: the available Computer Use drag API has no mouse-button
parameter. A future receiver-intent gesture must separate view pan from RF
control, preserve span, coalesce/clamp requests and reject stale ownership;
no hidden restart or pixel-by-pixel retune. If restart is required, use the
separately visible explicit action and gap/epoch contract.

APP-07 remains PARTIAL. These new cells and the HackRF group1/previous tinySA
next-Start errors remain explicit follow-up work; APP-06E is PLANNED and the
expired APP-05 timer was not restarted. No product/source/EXE/static promotion,
TX/amp/bias/firmware/driver/network/firewall/security mutation occurred here.

## First-cause resource diagnostics — 2026-09-30, software packet

The existing owner/session/off-Qt pump now carries one immutable, finite
first-failure boundary and a separate cleanup failure. Start/admission, owner
poll, bounded publication validation, preparation, queue, scheduled advance,
Stop, control claim and lease release are distinct. The already cached tinySA
acquisition phase/reason reaches the pane instead of being erased by two
generic exception boundaries. No serial query, parsing of a vendor exception,
second acquisition model or source restart is added.

The first cause survives failed and successful explicit Stop; a cleanup error
does not replace it. History clears only on an accepted, separately explicit
Start of the new run. The same ownership/lease and failed-resource quarantine
rules remain. Neighbors are not stopped. Unknown exceptions use fixed fallback
codes: a transport/collector code is an observed boundary, not proof of an
external USB or firmware cause.

RU/EN first-cause details appear in the affected pane's timing tooltip and
accessible description, and in a numbered error banner while Stop is required.
After successful cleanup the cause is retained as previous-run history in the
tooltip. The compact timing row is unchanged and text/tooltip writes are
coalesced. Log event details contain one first-cause event and changed cleanup events using
resource ordinal and finite codes/counts only, not raw resource identity,
endpoint, vendor text, serial bytes or traceback. Logging failure cannot strand
the accepted Stop future.

21 new tests and 80 combined focused tests passed using fake owners and the
actual V2 three-family composition. Scoped mypy7/default Ruff/compile/diff
checks passed. Exact full-source/frozen and physical restart gates are pending
at this source checkpoint; they must not be inferred from those unit tests.
This packet does not close prior physical tinySA next-Start/group1 errors,
all-full-range Sweep, receiver-frequency gesture or RBW300-kHz admission.
APP-07 remains PARTIAL; the APP-05 expired timer and APP-06E planned branch
are unchanged. No independent agent review has been performed for this packet.

### Exact first-cause source and frozen qualification

Exact tracked-clean product `835edea` completed 1097 V2 tests: 1031 passed,
66 skipped, zero failures in287.648s; compiled deferred[]/outside-checkout
product imports[]/source-native unchanged/exact provenance. An earlier full
run had ONE old APP05 one-second queue-close observation failure during the
parallel freeze. The isolated test passed; the complete serial retry passed.
The failed log is retained, causality unproven, assertions NOT weakened and
the expired APP05 performance timer NOT restarted.

Full official CPU/HackRF StageOnly 40/40CTest31.31s and658-file frozen native/
inert fake+production V2/libiio0.26/tinySA/shared-DLL gates passed. Initial
SYSTEM-runtime libusb collision was refused before freeze; R1 explicitly used
the previous manifest-verified package's common runtime bundle. No system DLL,
driver or guard was modified. New tagged diagnostic package has EXE SHA
f74c8b7698e2689c184e2c5fc8f9925704cfee14cbce55e843bd569a7da33160,
native34128b77…/paired manifest835edea; canonical/static package unpromoted.

Windows Computer Use launched this exact EXE and read the inert V2 surface.
Capture was black and activation failed; one fresh returned-window recovery
failed identically. NO Discover/Stage/Apply/Start or RX command was sent. The
same process remains open for manual window restoration; there was no blind
input or permission/security action. Physical tinySA Stop→Start and the new
live first-cause witness are NOT qualified by the software/frozen gates.
APP07 remains PARTIAL, independent new-packet review pending. All prior
wideband/Sweep/middle-drag/RBW and group1/physical-nextStart debts remain open.

### Current AD936x continuous-Sweep pane packet

Exact product `febd3f7` adds the selected RX1 continuous-Sweep pane adapter over
the SAME existing application/router/native lease and common V2 preparation.
See APP07_AD936X_PANE_OWNER_CONTRACT.md for W/N versus physical F, exact
source/epoch/grid/quality guards and explicit control boundaries. That earlier
packet retained the64-segment native cap; its64 MiB budget prose was incorrect,
as the backend's established reduced-data budget was128 MiB. Full-range
qualification was OPEN at that checkpoint.

16new/103focused source tests passed; exact clean1113 V2 tests1047pass/66skip/
0fail313.277s, deferred[]/outside[]/source-native unchanged. Full officialCPU
40/40CTest43.20s and658-file frozen pipeline passed. New diagnostic EXE is
not promoted to the approved static/current installation; C++/native hash
unchanged. Existing NaN warning debt is not declared resolved.

Short current-source physical RX observation with the new package native
used three product owners and actual OFFSCREEN V2 panes concurrently: ADUSB
Sweep100..220MHz/Fs61.44/W36/N4096/F8192, HFUSB Sweep100..220/Fs20/F4096,
tinySA100..300/1001point, Empty4. Progressive/terminal graph delivery and
selected Stop3 retaining both SDR peers, then normal Stopall/close passed.
This is NOT frozen visible Windows/50ms/DWM/FHD/QHD/DPI/RFduty/Pd/USB-lossless/
RF-settled/soak acceptance. Native quality flags, including HackRF settling
and progressive missing coverage, remain present. tinySA300k RBW was NOT
applied. All full-range Sweep, actual middle-button RF retune, wider RTBW
pane profiles, matched visible new-EXE cells and independent review remain
OPEN. APP07PARTIAL/APP06Eplanned/fullAPP00..14ACTIVE; APP05 timer not restarted.

### Extended full-range Sweep geometry contract

Common optional geometry version1 is strictly paired across native module,
sibling manifest, source admission, preview and frozen diagnostics: up to2048
segments,128 MiB reduced-data budget and the unchanged2-million-bin limit.
Old64-segment AD and aligned<=320 MHz HackRF modules remain usable only for
their existing compatible plans. Extended/nonaligned requests refuse before
receiver ownership/SDK Start; no fallback DLL, implicit FFT reduction or retry.
Native AD/HackRF budget validation precedes receive allocation or SDK open.
This does not claim bounded total RSS or release/performance acceptance.

HackRF keeps requested Fs20 MS/s, filter15 MHz,20 MHz tuning step, interleaved
firmware headers and two separated5 MHz crops per FFT. Whole-MHz analysis
endpoints within1..6000 MHz require span>=20 MHz. The planned hardware stop
is rounded UP to a whole tuning step, following the official host policy;
the exact analysis stop is preserved and padding is never displayed as measured
coverage. Planned first/last LO centers must fit the observed RF envelope.
Excluded crop-edge bins stay missing; no interpolation fills them.

Full1..6000 MHz analyzes1200 subbands with a1..6001 MHz hardware plan and a
last planned LO center of5993.5 MHz. Explicit F1024/F2048 fit the backend
budget; F4096 does not and refuses before SDK. No user FFT is silently changed.
Partial final5 MHz crops are clipped exactly; wholly out-of-analysis crops
are discarded without a fabricated segment. Padding headers still participate
in the same firmware sequence gate, not a second acquisition algorithm.

UI V2's standalone settings and per-pane preview show rounded capture as a
PLAN, distinct from requested analysis and from hardware readback. Stage and
draft/locale edits do not start RX. Both progressive and terminal publications
continue through the same owner/common Spectrum/Waterfall path. This extension
does not implement RF retuning by held-middle drag, wider RTBW pane windows or
unqualified tinySA RBW commands; those require separate implementation/tests.

### Exact extended-geometry qualification and Windows 2x2 witness

Exact tracked-clean product `a1a8702db7c6befc921073057921b430fc1382ef`
completed1114 V2 tests:1048pass/66skip/0fail314.644s, compiled deferred[],
outside-checkout imports[], same source/native and exact provenance. Existing
NaN warnings remain. Official CPU/HackRF40/40CTest42.54s and the complete
658-file frozen pipeline passed. New separate diagnostic EXE/native hashes
are a49a33bb…/762fceb6… with geometry1 and the exact paired source manifest.
It is NOT promoted to the canonical/static/current installation. Later
qualification-only commits do not relabel these gates or build bytes.

Physical current-source/common-root OFFSCREEN R1 used AD USB70..6000MHz/
Fs61.44/W36/N1024/F2048, HackRF USB1..6000MHz/Fs20/F1024, tinySA USB100..300MHz/
1001points/settingspreserve and Empty4.50.138619s showed progress BEFORE
complete for both SDR panes and complete events for all three. A first private
observer missed a brief AD complete event; its FAIL is retained. R1 repaired
only the observer and explicitly started again after normal shutdown. This
offscreen witness is not frozen Windows, font/DPI or performance acceptance.

Actual Computer Use on the SAME exact new frozen Windows EXE staged/applied
these profiles before explicit Start all. Three independent Spectrum/Waterfall
pairs changed; both SDR panes showed progressive full-range Sweep. Explicit
selected Stop3 retained both SDR owners running; explicit next Start3, without
Discover or peer restart, showed fresh tinySA history and all three resources
running at399.459s after that Start. The prior tinySA fault near57passes did
not recur at these observations, but its cause remains unknown: bounded
next-Start success is not long-soak/causal-remediation acceptance.

Stop all returned0running/0starting/0Stop-required/3stopped; Close layout and
normal window Close removed the test process. Post-close658-file/native
verification passed unchanged. During Live, process module paths/current disk
hashes matched package-local native/IIO/HackRF/pthread and ONE libusb. This is
not in-memory attestation or exhaustive active-ABI/lossless-RF proof. Computer
Use activation/capture/input worked; transient Qt-popup capture/input failures
required fresh observation and supported keyboard navigation, not blind retries.

Known preview debt: HackRF geometry is mislabeled as AD936x in the per-pane
Stage summary. tinySA RBW300k was NOT applied. Shared-resource fairness,
simultaneous tuning-group/RX and genuine Ethernet cells, wider RTBW pane
profiles, RF middle-drag intent, sustained/50ms/DWM/FHD/QHD/DPI/RF duty/Pd/
lossless/settling, independent review and release remain OPEN. APP07PARTIAL,
APP06E CutDC/spur planned/fullAPP00..14ACTIVE; expired APP05 timer not restarted.
No TX/amp/bias enable, firmware/driver/network/firewall/security/system changes.

### Family-specific pane Sweep preview

The per-pane Stage summary no longer labels HackRF geometry as AD936x or
uses AD's analysis N/W for its physical transform. It explicitly shows the
HackRF physical FFT, two disjoint5 MHz windows,20 MHz firmware tuning step,
subband count, grid spacing (not RBW) and reduced-data budget (not RSS).
Rounded hardware capture remains a separate PLAN, not readback. AD keeps
analysis N inside W36 MHz and its separately computed physical FFT.

The HackRF Sweep pane now exposes the backend's existing physical
F1024/2048/4096 choices. F2048 supports the full1..6000 MHz analysis within
the same128 MiB reduced-data budget; F4096 refuses that whole-range request
instead of lowering FFT. This added choice does not expand other pane
families/modes. Stage/Apply remain separate from explicit Start; locale and
draft edits never acquire RF. No native DSP/SDK implementation, queue limits,
Fs, filtering, RTBW width or ownership policy changed in this UI packet.

### Executor-close qualification accounting

The synthetic APP05 executor observer keeps its original never-started count,
including tasks cancelled by normal product shutdown. It now separately counts
only cancellation acknowledged by a real concurrent Future; it does not send
cancellation or retain Future/payload/Qt owner objects. Unexecuted tasks without
that acknowledgement remain explicitly unaccounted, even if a Future failed
without entering its wrapped operation.

The close gate requires every accepted task to have finished or been explicitly
cancelled before execution, with exact attempted/submission-failure/started/
cancelled accounting, started=finished, unchanged exception accounting, no
lost timing samples, no remaining workers and zero presentation reservations.
This corrects the old expectation that ALL admitted tasks must execute even
during cancel-on-close; it does not relabel cancelled tasks as completed or
discard their raw count. The original failing1116-test source run remains a
failed observation, not a performance success. No product lifecycle or native
DSP is changed; the expired APP05 performance timer is not restarted.

### Exact family-preview and shared/time-sliced RX qualification

Exact product/source `a4dcfdbe25c49f87c64abe7ddbee5203dfaca0b3` passed the
complete V2 source gate:1118total/1052pass/66skip/0fail323.110s, compiled
deferred[], outside-checkout imports[], same clean source/native and exact
provenance. Historical NaN warnings remain. Official CPU/HackRF40/40CTest
42.43s and complete658-file frozen gates passed. Separate diagnostic EXE
75acacbd…/native762fceb6…/snapshot98ba2cce… uses the paired a4 manifest,
schema5/factory2/DSP1/persistence1/Sweep1/geometry1 and Qt6.11.1. It is not
promoted to canonical/static/current. Later documentation-only revisions do
not relabel these exact source, build or hardware gates.

Two bounded physical current-source/common-root cells used USB AD936x
Sweep requested Fs61.44MS/s/W36/N1024/physicalF2048 and independent USB HackRF
whole-range1..6000MHz/requested Fs20MS/s/physicalF2048, plus Empty4.
Identical AD100..220MHz panes
shared one capture with223 matching identities. Disjoint AD100..220 and
300..420MHz used one RX/two time-sliced jobs, not two owners. Source host
revisit means were about2.163s against model2.06s; these are not RF periods,
FFT/LPS rates or sampling-continuity estimates. Explicit selected Stop kept
HackRF running; explicit accepted next Start gave fresh AD epochs in both
cells. Normal shutdown/threads[]/outside[]/exact source-native passed.
The first private shared observer clicked before Start became enabled; its
FAIL is retained, and the corrected observer was explicitly run after normal
close. Source time-sliced stderr WRITE/READ -9 after its summary is also
retained: cause unknown, no zero-error SDK-Close or causal-fix acceptance.

Actual Computer Use on the SAME new frozen Windows EXE exercised both
layouts through user controls: Stage/impact before Apply, inert Apply,
explicit Start, three changing Spectrum/Waterfall pairs and SDR progress.
The shared-Stop dialog named both affected AD panes, defaulted to No,
cancelled without stopping either resource, then explicitly confirmed Stop
of AD only. In each layout, a new explicit enabled Start restored fresh AD
histories/epochs while HackRF stayed live; no Discover or peer restart was
sent. Time-sliced UI separately showed observed revisit~2.15..2.19s and
model2.06s. Final Stop-all reached0running/0starting/0Stop-required/2stopped;
Close layout and normal Close removed the GUI process/window. Post-close
658-file/native verification passed. Five package-local active native/IIO/
HackRF/pthread/ONE libusb paths and current disk hashes matched the manifest,
not in-memory attestation or exhaustive active-ABI/RF/lossless proof.

CU activation/capture/input were available, but a transient owned-modal
button index was refused; fresh observation and supported Escape/Left/Return
worked without blind retries or security actions. This1440x912 logical
witness is not DWM/FHD/QHD/DPI/50ms/RF-duty/Pd/settling/soak qualification.
tinySA was not opened in these two-resource cells. Scheduler user priority/
target revisit, simultaneous tuning groups/RX, genuine Ethernet, wider RTBW,
qualified tinySA300k/current input, held-middle RF intent, CutDC/spur,
sustained/independent review and release remain open. APP07 is PARTIAL;
APP06E is planned and the full APP00..14 objective remains active.

### User scheduling intent on the same V2 resource graph

The Pane sources editor now has a collapsed-by-default Schedule section.
Each occupied slot can declare a priority weight1..100 and an optional maximum
revisit target in seconds (0/unset, up to3600s). Empty creates no scheduling
intent. Controls and their RU/EN accessible names are locked with the staged
draft; locale/display expansion never applies RF settings or starts RX.
Numeric fields use the existing range-control theme role in all three themes;
pane numbers use secondary text and excess height stays below the compact
editor instead of spreading rows. The source column receives remaining width.

These fields use the existing bounded smooth weighted cycle (at most400slots
for4panes), the same resource owner/lease/pump and common Spectrum/Waterfall.
They do not change Fs/FFT/window/detector, add an SDK opener, set UI FPS or
silently restart healthy peers. A weight affects recurring active time units;
it is not a guarantee of a proportional FFT/LPS rate or a shorter worst gap.
Initial AD Apply stages the actual first weighted capture, not jobs[0].

Compatible shared views still have ONE capture even with different user
preferences. Their effective capture policy is the largest requested weight
and strictest requested maximum interval. Individual requests and this merge
are retained separately and shown before Apply; no deadline is relaxed or
claimed independently achievable for one shared view. Low-level shared job
contracts continue to require one identical effective policy.

Compilation reports every missed deadline across admitted resources as bounded
typed plan estimates. Refused Stage creates no pane owners/lease/Apply/RX;
fresh source metadata selection may have occurred. A failed graph close keeps
the staged owner for explicit Discard, with the fixed cleanup obligation and
no raw SDK exception in UI. RU/EN details name the pane, modeled maximum and
requested target; confirmed Discard clears the cleanup obligation.

Feasibility is ONLY against the existing declared CaptureEpochCost model,
not transport throughput, full-range acquisition time, RF duty or a hardware
deadline guarantee. Floating comparison removes only accumulated arithmetic
roundoff (1e-12 relative/absolute), not scheduler grace time. An exact0.320s
model boundary remains valid across400slots;0.319s is refused. Actual accepted
host revisit is separate: unknown before a measured return, last interval
within target or last interval exceeded target, not a sustained/RF verdict.
No hidden adaptive Fs/FFT or retry is issued when a target is exceeded.
Multiple capture jobs remain visibly time-sliced even if each job serves a
shared pair. Stopped retained frames are not labeled as current measurements.
The same multi-job resource criterion identifies qualified scheduled returns
for each subscriber's Waterfall history. Shared-pair returns keep separate
bounded histories with a display-only absence separator; a genuine next
explicit Start still resets history. Epoch/run/source/unit/grid guards remain.

Qualification is recorded against exact product
e97a3b7330f6bcc5e78ad675f385e8b6b2501d48, not the earlier a4 artifact or a later
documentation commit. Full V2 source:1135total/1069pass/66skip/0fail315.172s,
exact clean-source/native provenance and no deferred/outside modules. Official
CPU/HackRF build:40/40CTest42.73s and the complete658-file frozen pipeline.
Native762fceb6 remains unchanged. The separate diagnostic scheduling EXE has
disk SHA b05cd0fb and source snapshot63431756; it is NOT current/static promoted.
The earlier1c7d7b4 RU localization failure and older build remain historical.

A short current-source/common-owner USB witness used distinct AD ranges on
one RX at61.44MS/s/W36/N1024/F2048, weights1:3 and targets4.08/2.06s, plus
independent full-range HackRF20MS/s/F2048 and Empty4. Stop-cancel, selected
shared-resource Stop, explicit next Start and normal shutdown passed. Deliveries
and last accepted host intervals are not FFT/LPS, RF duty or sustained cadence.

The SAME new frozen Windows EXE separately passed the actual editor's all-pane
deadline refusal before Apply/RX, explicit admissible Stage/impact preview,
inert Apply, Start all and three progressive Spectrum/Waterfall pairs. The
Windows AD profile used N4096/F8192, not the source witness's N1024/F2048.
Cancelling shared Stop preserved both receivers; confirming it stopped only
AD's two panes while HackRF continued. Explicit next Start refreshed both AD
histories/epochs without Discover or a HackRF restart. Final Stop all reported
zero running/starting/Stop-required resources; Close layout and normal Close
removed the tested window/process. The post-close658-file verifier passed.
Five concurrently loaded package-local native/IIO/HackRF/pthread/ONE USB paths
and current disk hashes matched, not in-memory or exhaustive active ABI proof.

Modeled feasibility is NOT measured feasibility: actual AD host returns included
about4.20..4.23s against4.08s and2.24..2.26s against2.06s, correctly labeled
exceeded; shorter second-pane intervals were labeled last-within, not a sustained
pass. No target-triggered retry, hidden restart or reduced Fs/FFT occurred.
Computer Use capture/input worked with fresh-observation recovery for transient
Qt popup errors, without security changes. The1440x912 logical Windows witness
is not FHD/QHD/DPI/DWM/50ms/RF-duty/Pd/settling/lossless/soak qualification.
Expanded scheduling plus a long staged preview visibly compressed editor rows
and clipped preview text: a concrete UI defect, not visual-polish acceptance.
Bounded readable controls/scrollable preview, SDK cleanup cause, qualified
tinySA300k/current input, held-middle RF intent, wide RTBW, tuning groups,
genuine Ethernet, sustained and independent review/release remain open.
APP07 remains PARTIAL; APP06E is planned and the full release remains active.

### Readable staged editor on the same V2 Analyzer

A long accepted impact preview and expanded scheduling section are now in a
local, bounded editor scroll area instead of shrinking fields to fit above the
plots. Stage/Apply/Discard/Schedule and Plan/reasons stay pinned outside that
scroll area. The editor's preferred height is capped at480 logical pixels;
the parent may give less space while all content remains scrollable. Wrapped
prose gets its full width-dependent height, and fields keep their actual Qt
minimum-size hints. This changes neither the graph/DSP nor presentation queues.

Plan/reasons moves focus to the scroll area and exposes the accepted preview
or refusal reasons. PageDown reaches the last scope/coverage limitation; the
full plain text is also accessible. Discard clears the accessible preview with
the visible one, keeps scheduling values and unlocks the draft without RX.
Scheduling numbers remain compact rather than stretching across equal-width
columns. A staged locale change translates existing mode items in place under
signal blocking; it does not rebuild choices, change item data, apply or Start.

Actual-root fake-SDK regression coverage includes the original1440x912 clipped
control case,60 combinations of5 logical sizes/RU-EN/3themes/expanded-collapsed,
pinned commands, keyboard-reachable accepted/refused text, preserved staged
plan and draft focus after Discard. These source/widget tests are NOT a new
frozen Windows, FHD/QHD/per-monitor-DPI/RF/latency/soak acceptance. Such gates
must use the new exact build, not relabel the earlier e97 artifact.

#### Exact readable-editor qualification, 2026-10-01

The exact product8378c46 received a clean serial full V2 gate:1139 tests,
1073 passed/66 skipped/zero failures, no deferred compiled tests or outside
product modules. Its complete official CPU/HackRF pipeline passed40/40 CTest
and all658-file frozen package gates; the native artifact remained unchanged.
The separate tagged diagnostic package was not promoted to static/current.

Computer Use on that SAME new Windows EXE staged two AD936x USB Sweep ranges
100–220/300–420 MHz with N4096/F8192, weights1:3 and targets4.08/2.06s, plus
independent HackRF1–6000 MHz/F2048 and Empty4. Pinned commands stayed reachable;
Plan/reasons and PageDown exposed the last preview scope line. Changing the
staged locale translated the three mode items and preview while retaining
sources, ranges, FFT and scheduling values. Discard cleared both visible and
accessible preview, unlocked the preserved draft, and visible Tab focus moved
from priority to target. Apply/Start were NOT invoked: this is a bounded editor
witness, not a new RX/FFT-rate/RF/latency/soak acceptance. Normal Close removed
the tested window/process and the post-close658-file/native verifier passed.

Transient Qt-popup capture/input errors and one observer stale-index error
after locale rebuild were retained; fresh observations, keyboard selection
and main-window screenshot clicks recovered without security changes. Helper
focus metadata was sometimes stale despite correct visible caret, so full AT
focus compliance or permanent plugin repair is not claimed. Reported logical
window sizes1154x760/1282x751 are not FHD/QHD/per-monitor-DPI qualification.
The short-height footer and remaining SDK cleanup/RF/device/sustained/review/
release work stay open; APP07 remains PARTIAL. This later documentation tail
does not relabel the exact8378 source/build/witness as its own commit identity.

### Per-pane tinySA settings intent, 2026-10-01

Each tinySA pane now has its own immutable settings/input/readback and optional
external-correction intent. The independent editor reuses the single-source
tinySA drawer and existing profile-store presenter; a selector exposes one
pane's drawer at a time inside the bounded editor. It does not discover/open
serial, select the base Analyzer source or apply a setting on Qt. Changing a
pane source resets that pane's intent; another pane's draft is not changed.

A still-unobserved USB candidate may express requested settings with an
explicit unverified warning. This is NOT command admission or an identified
model: explicit Stage freshly observes the source and the SAME typed
TinySaSweepRequest applies the existing firmware/model/input/range checks.
Observed unsupported firmware remains preserve-only. A refused Stage performs
no measurement/settings commands or RX. Accepted Stage locks the drawer,
shows the verified contract and previews requested commands/input/readback/
correction. Apply still reserves the existing owner; only explicit Start
executes the request on its same serial handle. No flash save, automatic
restore, hidden retry, new firmware command or receiver factory is introduced.

The profile compatibility key already includes the complete settings intent:
different RBW/input/correction on one physical tinySA creates time-sliced
jobs, not a silent shared-settings merge or second independent port owner.
Changed UI settings/correction force available post-pass queries, matching
the single-source UI. The pane shows actual queried RBW separately from the
target; its tooltip/accessibility retains the existing full readout and scope.
ACK is not input/LNA/accuracy/spur/repeat readback, and screen-sweep time is
not scanraw duration. Slow instrument acquisition is not an SDR FFT defect.

Staged locale changes retain the exact source-selection key and settings
through Discard; labels are translated in place without I/O. The maximum
10,001-point policy, device-reported dBm and optional separate correction layer
are unchanged. Fake-serial/source tests do not qualify physical 300 kHz RBW,
RF input, Windows/DPI or sustained multi-source operation; exact current
build/device evidence remains a separate gate. APP07 is still PARTIAL.

The actual version-only observation found the admitted revision followed by
the Ultra hardware line. Contract recognition now accepts only the exact
second-line forms emitted by `cmd_version`/`get_hw_version_text` in the SAME
[firmware source commit](https://github.com/erikkaashoek/tinySA/blob/26fc821ad3432f929630718cd290314dbc711f48/main.c#L2207-L2242).
It does not admit arbitrary trailers, another/dirty revision or unknown
commands. The full normalized response, including HW text, remains in the
firmware identity and same-owner version comparison; no parser/fingerprint
normalization or binary-firmware-attestation claim is introduced. An initial
exact915ba19 full gate's one RU-prose failure is retained, not relabeled PASS;
the untranslated readback/Stop words were corrected, not the catalog guard.

The first current-device settings attempt reached post-pass readback but
refused `300kHz`: the old reader accepted only an unprefixed numeric `Hz`
value. A separate passive parser observation, without extra commands or
response substitution, isolated that format. The pinned
[sa_cmd.c query definitions](https://github.com/erikkaashoek/tinySA/blob/26fc821ad3432f929630718cd290314dbc711f48/sa_cmd.c#L354-L394)
and [chprintf.c SI formatter](https://github.com/erikkaashoek/tinySA/blob/26fc821ad3432f929630718cd290314dbc711f48/chprintf.c#L195-L227)
use `%F`, not a plain float. Readback now converts only the field's admitted
SI scales into base Hz/seconds/dB before the SAME finite observation bounds.
Unknown units/prefixes, arbitrary text, duplicate lines, missing prompts,
deadline failures and out-of-bound scaled values still refuse publication.
This parsing fix does not replace requested RBW with a target-derived actual
value or imply settings/RF/Windows qualification from fake-serial tests.

#### Exact tinySA-settings qualification, 2026-10-01

Exact product42377c4 received a clean serial full V2 gate after its full build
and physical shutdown:1154 tests,1088 passed/66 skipped/zero failures, no
deferred compiled tests or outside product modules, unchanged exact source
and native artifact. The complete official CPU/HackRF pipeline passed40/40
CTest and all658-file frozen package gates. The new tagged diagnostic package
was not promoted to canonical/static/current. Earlier915 RU-prose and636
readback-format failures remain historical evidence, not retroactive passes.

The SAME common source V2 graph/pool/owners/native and Spectrum/Waterfall ran
USB AD936x70–6000 MHz Sweep/Fs61.44/N1024/F2048/W36/overlap2, independent USB
HackRF1–6000 MHz Sweep/Fs20/F2048, tinySA100–300 MHz/1001 points/target300kHz/
explicit LOW, plus Empty4. Actual post-pass tinySA RBW was300000Hz; attenuation
was0dB, screen-sweep time0.125s and host acquisition about0.93s. Device screen
time is not scanraw duration and LOW command ACK is not RF-input readback.
Selected tinySA Stop preserved both SDR deliveries/epochs; explicit next
Start reset only tinySA to a fresh epoch/history. All resources stopped and
the graph shut down normally in this approximately65-second functional run.

This is source-OFFSCREEN plus the exact packaged native artifact, NOT a new
frozen Windows visual/input/RX, DWM/FPS, RF duty/Pd, lossless or sustained
acceptance. Live source-process libiio came from the installed SDK with the
same disk hash as the bundle; it was not all-package-local/in-memory/exhaustive
ABI proof. The separate636 Windows Computer Use diagnostic read the tree but
captured black pixels and failed one fresh activation recovery; no RX input
was sent. Its inert window was left for manual restoration, not killed.
Remaining wide RTBW/RF-middle-pan, genuine Ethernet, SDK-cleanup-cause,
layout/visual polish, sustained/review/release work stays open. APP07 PARTIAL;
APP06E CutDC/spur remains planned. This later documentation tail does not
relabel product42377c4's gates as the documentation commit's own identity.


### Explicit wide RTBW bands and matched Windows qualification, 2026-10-01

Independent panes have an explicit typed RTBW band policy. The existing
edge-trimmed default remains unchanged; Full receive is opt-in and follows
Stop → Stage / impact preview → Apply → explicit Start, never hidden restart.
The optional bounded editor section preserves intent across locale/Discard
and RTBW→Sweep→RTBW, while disabled Empty/tinySA/Sweep controls cannot submit
a wide-RTBW draft. Accepted Stage locks the choice with other RF fields.

| Requested SDR rate | Default filter / analysis span | Full receive filter / allowed span |
| --- | --- | --- |
| AD936x 20 MS/s | 10 / 10 MHz | 20 / 20 MHz |
| AD936x 61.44 MS/s | 40 / 36 MHz | 56 / 56 MHz |
| HackRF 16 MS/s | 14 / 10 MHz | 14 / 14 MHz |
| HackRF 20 MS/s | 15 / 10 MHz | 20 / 20 MHz |

Numeric Fs/filter/span/crop/physical-FFT/hop values and evidence limitations
are previewed BEFORE Apply. Full receive means requested acquisition/filter
coverage, not a calibrated flat or alias-free RF passband, ADC-rate readback,
continuous reception or lossless transport. Fresh capability admission and
AD936x Start readback still apply; HackRF filter setters are ACK-only.
Sweep geometry stays unchanged: AD936x Fs61.44/W36/overlap2/RF40 with the
established analysis-N/physical-F mapping, and existing HackRF host Sweep.
Same-compatible full-band crops can share one owner if their envelope fits;
wide and trimmed profiles with different effective settings time-slice on
that SAME RX rather than silently merging filters or opening another handle.

The complete immutable RTBW FFT grid is already validated against center,
Fs and physical F. Its O(1) coverage property is half-open
`[first bin center, first bin center + Fs)`; the positive boundary is not a
new measured bin. For even F, the final bin center is Fs/F below positive
Nyquist. Resource routing and presentation use the SAME validated bounds.
This admits an exact full-Fs crop, not truncated/shifted/forged grids or an
extra sample. Exact source/session/generation/epoch/Fs/F/hop/unit and memory
guards remain; Sweep/tinySA coverage, native flags, NaN gaps and loss counters
are not rewritten. Native DSP/FFT/group/queue/SDK/lease algorithms are unchanged.

Exact product ff5a5be received 103 focused passes and a clean serial full V2
gate after freeze:1160 tests,1094 passed/66 skipped/zero failures, no deferred
compiled tests or outside product modules, unchanged exact source/native.
Four historical NaN warnings remain. The complete official CPU/HackRF build
passed40/40 CTest and all658-file frozen gates. Its separate tagged diagnostic
was not promoted to canonical/static/current; native code/hash is unchanged.

Matched common SOURCE V2 plus its packaged native ran AD USB RTBW100–156MHz/
Fs61.44/filter-span56MHz and HackRF USB RTBW140–160MHz/Fs20/filter-span20MHz,
both F4096/hop2048 (HFgroup1), tinySA100–300MHz/1001/target300kHz/explicitLOW,
plus Empty4. AD Start read back61.44MS/s/56MHz and retained2Hz LO quantization.
The HF full-Fs positive boundary was admitted without an extra FFT bin;
HF filter ACK is not hardware/RF readback. TinySA queried actual300000Hz,
screen0.125s and host acquisition about0.93s; screen time is not scanraw and
LOW ACK is not RF-input proof. Selected tinySA/HF Stop and explicit next
Start changed only that resource's epoch/history; peers continued. Normal
Stop/shutdown completed in an approximately48-second bounded functional run.
Source OFFSCREEN images are not Windows font/DPI acceptance.

The SAME exact new frozen Windows EXE separately passed Computer Use
inventory/activation/nonblack capture/indexed mouse/keyboard controls. Stage
showed the same full-band numeric plan and freshly admitted tinySA settings;
Apply was inert. Maximized1440×912 logical produced actual2×2, then explicit
Start all showed three changing Spectrum/Waterfall pairs and queried300kHz.
Selected tinySA and HF Stop→explicit next Start each preserved running peers
and produced fresh selected histories without Discover or peer restart.
Windows histories do not expose numeric epoch IDs; source epoch/readback
evidence is not relabeled a frozen GUI hardware getter observation.
Stop all reached zero running/starting/Stop-required, Close layout returned
to an inert Analyzer, and normal Close removed the tested process/window.
Post-close complete658-file/native metadata verification passed unchanged.

Five selected live module paths and associated disk hashes were package-local
and matched the release manifest, with ONE libusb; this is not in-memory or
exhaustive active ABI attestation. Transient Qt-popup capture failures, lagging
accessibility focus/enable metadata and a static navigation accessible name
were retained; fresh observations recovered control without security changes.
Computer Use currently works, not a claim of permanent plugin repair/full AT.
HF Time unknown and tinySA Passes remain honest, not fabricated RF time.

This packet is bounded functional qualification, NOT DWM FPS/50ms, FHD/QHD/
per-monitor DPI, RF flatness/settling/duty/Pd, lossless reception, uninterrupted
30-minute/2-hour soak or release acceptance. Held-middle RF-intent (not just
viewport movement), tuning groups, genuine Ethernet, SDK-cleanup-cause,
sustained/performance/layout polish, independent review and release remain
OPEN; APP07 PARTIAL and APP06E CutDC/spur planned. The expired APP05 timer was
not restarted. No TX/amp/bias enable, firmware, driver, network, firewall,
security or system changes. A later public-documentation commit does not
relabel exact ff5a5be source/build/device gates as its own product identity.

### Resource-scoped RF range replacement foundation, 2026-10-01

This is a backend/control foundation for a real RF shift, NOT the completed
held-middle-button UI gesture. Existing viewport movement is not receiver
tuning. No new UI control invokes these commands in this packet; a later
UI packet must show an impact preview and preserve explicit operator consent.

The staged product handle retains immutable copies of the original pane
drafts and exact selected source/revision facts. A shift changes only one
draft's start/stop and preserves its span, family, mode, Fs/filter/FFT/hop/group,
receive-band policy, tinySA points/settings/input/correction, and every pane's
original priority and maximum-revisit request. Other drafts are retained,
not reconstructed from a merged capture's effective scheduler policy. The
SAME all-pane compiler rechecks capabilities, geometry/budgets and all
deadlines before any Stop. Out-of-range or infeasible proposals refuse; there
is no edge clamp, FFT reduction, source replacement or extra SDK opener.

Requested and effective shifts are distinct. Qualified HackRF host Sweep
ranges use an explicit 1 MHz request quantum; other admitted pane requests
use whole-Hz shifts, rounded nearest with ties away from zero. A sub-quantum
zero shift refuses. This request quantum is not hardware LO/readback accuracy
or a promise that the device applies a whole-Hz center exactly.

| Operation | Allowed effect | Explicitly excluded |
| --- | --- | --- |
| Preview | Compile and validate the same resource's proposed plan off Qt | SDK acquisition/configuration, lease creation, Stop or Start |
| Approved RF Stop | Recheck exact source/run/plan and recording state, then stop/release that resource | Stopping a newer run or an independent peer |
| Apply after confirmed Stop | Replace target host routing and worker-owned presentation bindings/caches | RF setters, owner creation or receiver Start |
| Explicit next Start | Fresh adapter/lease/permit on the same selected graph; normal family admission | Discover, peer restart, stale-frame/epoch relabel |

The impact includes ALL panes sharing or time-slicing the affected physical
RX, even those whose requested ranges stay unchanged. A preview cannot omit
an affected pane. A shared capture can become time-sliced (or vice versa), so
the future UI must display the original/effective priorities and new planned
revisits for all affected panes before approval. Independent resources retain
their exact owner, admission, presentation binding and cache objects.

Preview identity is anchored to the exact schedule object and explicit-run
serial. Normal advances within that accepted time-sliced run do not stale it;
a new Start or accepted plan change does. Recording/source guards are checked
again before approved RF Stop and before Apply. The RF Stop check and actual
Stop share the same owner's native control transaction, closing the recorder
attach race. Ordinary selected/emergency Stop remains available during
recording; a recorder conflict cannot obstruct that cleanup action.

The bounded existing resource worker holds at most one pending RF-plan
command. Accepted Futures cannot detach commands via cancellation. Explicit
Stop outranks a queued plan command; Start and terminal Close cannot bypass
pending control. A partial host/presentation commit blocks further Start until
explicit Stop and terminal layout close; there is no hidden repair/retry.
Stopped handoff-cleanup failures complete their Future, retain a visible
cleanup obligation and keep the worker available for an explicit Stop retry,
rather than killing it with an unresolved completion.

The next admission has a fresh epoch and a PROFILE_OR_RF_PLAN_CHANGE control
gap from the actual last capture at Stop, not the preview-time timeslice.
Multiple Apply actions while stopped preserve that last real admission.
Planned gap and observed host control elapsed are separate: the latter starts
when routing closes for Stop and includes Stop, operator wait and next Start.
Unknown/invalid clocks remain unknown. Neither value proves RF duty, missing
ADC samples, pulse-detection probability or a continuous I/Q stream. Native
quality flags, gaps, time scope, units and loss counters remain unchanged.

Source tests use the real common resource session, graph pool, adapters,
pump and preparer with fake SDKs. They are not a physical RF shift, current
frozen Windows gesture or performance acceptance. Still required: one common
Spectrum/Waterfall held-middle gesture, bounded/default-Cancel impact UI,
single- and multi-pane integration, target caption/history refresh before
Start, shared/time-sliced behavior, matched actual high-Fs hardware/new-EXE
qualification, genuine Ethernet/tuning groups, sustained/review/release.
APP07 remains PARTIAL; APP06E and later APP08–14 gates remain open. No native
DSP, SDK, Fs/group, transport, queue-capacity or resource-budget expansion is
introduced, and the expired APP05 timer is not restarted.

#### Exact source qualification of the RF replacement foundation

Exact product fcad48cb1ac6cb38b7899e6a0cd07c85caba6172 passed the clean
full V2 source gate:1176 total,1110 passed/66 skipped/zero failures in411.659s,
no deferred compiled tests or outside product imports, unchanged source and
native762fceb6. Four historical NaN warnings remain. The separate exact-HEAD
focused set passed120 tests in7.931s, including14 new service-core methods
outside full-V2 discovery. There are30 new methods overall:14 service,7 pure
plan and9 concrete product tests. Scoped lint/type/compile checks passed.

These are source/common-graph tests with fake SDKs plus the existing packaged
native, not a new frozen Python/Windows gesture, physical RF change, native
build/CTest, DWM/50ms or sustained/release acceptance. The old ff5a5be package
and its physical/Windows gates are not relabelled as fcad48c. A later
public-documentation-only commit likewise does not change fcad48c's tested
product identity. The UI gesture and full-feature gates above remain open.

### Common held-middle RF gesture and independent-layout UI

The UI V2 Spectrum and Waterfall now use the same RF-intent ViewBox. A
middle-button hold snapshots a cached scalar measurement/control anchor and
the original frequency-per-pixel scale. Only release after at least three
pixels proposes one offset: grabbing right requests lower RF frequencies.
There are no per-pixel RF setters or viewport-only middle-pan fallback.
Escape, a linked frequency zoom, unavailable control or a changed source/RX/
session/epoch/config/unit cancel the held gesture. Ordinary frame sequence,
progress revision and array allocation are deliberately not anchor fields.
Left/right/wheel presentation behavior retains the common plot path.

The independently assigned 1–4-pane layout connects that proposal to the
existing resource worker, not an extra acquisition/DSP/SDK path. An accessible
numeric RF Shift command is an alternative entry into the SAME preview.
The scrollable, bounded RU/EN dialog shows requested/effective quantum,
every affected shared/time-sliced pane, old/new ranges, original/merged
weights and modeled/target revisits. Cancel is the default; Escape and
Cancel leave RF and the accepted plan unchanged. No command is inferred
from merely opening the dialog or pressing Enter on its default button.

For a running target the explicitly labelled approval authorizes
Stop -> Apply -> Start of that RX only. The same native control transaction
rechecks source/run/recording before Stop. After the stopped host Apply
receipt, Qt installs target captions, revisit/binding maps and clears only
target histories BEFORE submitting the approved fresh Start. A stopped
target only arms the new plan, requiring a separate subsequent Start.
Independent RX owners, epochs and bindings are not restarted/reset.
The pending UI dialog/receipt also prevents terminal layout close; Start
controls cannot bypass the receipt even when the backend Future is done.

An ordinary target Stop/Stop All cancels the remaining chain, never detaches
an already accepted Future, and never retries or restarts after error. If a
cancelled Apply has already committed, its terminal receipt still updates
the GUI before exposing manual Start. An incoherent GUI receipt bars the
target's Start until Stop/layout close. Only known old target bindings are
dropped as stale; arbitrary foreign pane deliveries still refuse. The
timing tooltip keeps host RF-plan-change boundary/control elapsed separate
from the latest producer-frame epoch, RF duty, Pd and sample-loss evidence.

Candidate source checks:19 new methods (7 real Qt viewport middle-event
checks and12 concrete graph/owner/pump/dialog checks);62 focused tests PASS
in31.473s, scoped lint/type checks PASS. Initial pixel-scale test sampled
before initial axis/layout timers settled; two later checks found an input
availability race in the test and a hard-coded MHz suffix in product UI.
The fixture now waits for the actual enabled button/layout; the suffix uses
the catalog. Assertions, recording/identity/capability/deadline guards were
not weakened. A later exact-HEAD gate is a separate qualification.

This completes neither APP07 nor the full RF-shift feature. The DEFAULT
single-source Analyzer presenter path is not yet connected: its complete
AD/HackRF applied profile and typed Sweep request must be preserved, not
reconstructed from the simpler pane draft. Matched new frozen Windows/RX
qualification, actual Windows held-middle input, single-source integration,
genuine Ethernet/tuning groups, sustained/performance/review/release remain
open. Qt offscreen input/fake SDK is NOT a Windows middle-drag, physical RF,
DWM/50ms/FHD/QHD/DPI or lossless-stream acceptance. No native/DSP/Fs/FFT/group,
SDK or queue/memory-budget expansion; expired APP05 timer not restarted.

The FIRST exact f73ade7 full V2 gate completed1195 tests in421.668s with
66 skips and3 Russian-catalog subtest failures: untranslated `Apply` in the
new approval/armed explanations. Exact clean-source/native provenance was
true, deferred compiled/outside-import lists empty. This is retained as a
FAILED gate, not release acceptance. The RU text was subsequently corrected
without changing the translation guard; the corrected source requires its
own exact-HEAD full gate and package identity.

#### Corrected exact source, package and physical RF qualification

Corrected product9425d52d0f3d88fdfed5e47c5bb8da26e273593d passed the clean
full V2 source gate:1195total/1129passed/66skipped/zero failures418.661s,
exact clean-source/native provenance, no deferred compiled tests or outside
product imports. The earlier f73ade7 three-RU-failure gate remains FAILED;
neither its result nor the four historical NaN warnings were erased.

The official CPU/HackRF build of that SAME9425 product passed40/40CTest in
44.06s and the complete658-file frozen/load-only/shared-runtime gates.
Native762fceb6 was unchanged. This is a separate diagnostic package, not a
canonical/static/current-release promotion. Post-Windows-close full658-file
and frozen native verification passed; the source snapshot was unchanged.
A later public-documentation-only commit does not relabel these9425 gates.

A new short physical source+packaged-native Qt test used AD936xUSB61.44MS/s,
RF/analysis56MHz, FFT4096/hop2048, independent HackRFUSB20MS/s/20MHz with
the same FFT/hop/group1, and tinySAUSB100–300MHz/1001/LOW/queried300kHz RBW.
Real Qt middle-press/move/release proposed one+4.477135MHz shift; no RF
proposal occurred before release. Cancel retained the old plan/epoch.
Explicit approval moved only AD100–156 to104.477135–160.477135MHz, changed
its epoch4→6 and activation1→2, and recorded PROFILE_OR_RF_PLAN_CHANGE with
0.2638941s host Stop/wait/Start elapsed. The AD readback retained61.44MS/s,
56MHz and a3Hz center-rounding difference. Independent peer epochs remained
unchanged; HackRF delivered newer frames. This6.501357s observation is NOT
a Windows middle-held gesture, RF accuracy, continuous USB, duty/Pd or soak.
The initial private-runner unsupported-discovery-argument failure occurred
before RX and is retained; only that runner was corrected for the explicit R1.

The SAME new frozen Windows EXE exercised the accessible numeric entry
on three actual RX/trace sources plus Empty4. A+5MHz preview listed only the
AD target and preserved Fs/RF56/FFT/hop/gain. Enter on default Cancel retained
100–156MHz and all three running resources. A new, explicitly approved
Stop→Apply→Start changed AD to105–161MHz and visibly reset ONLY its waterfall
history. HackRF140–160MHz and tinySA100–300MHz plots continued without a
visible peer reset. Numeric epoch/gap was proven in the separate source
observation above, NOT independently read through the Windows timing tooltip.

Before the final Stop, the longer Windows observation exposed a tinySA
first-cause failure: version/identity mismatch at owner-poll/instrument
verification. It was visible as Stop-required while independent SDRs remained
running. No automatic retry/new Start or identity-guard weakening followed.
One explicit Stop all reached0running/0starting/0Stop-required/3stopped;
Close layout and normal window Close removed the test process/window.
This is a bounded RF-control functional witness with a retained tinySA
stability failure, NOT successful sustained cross-family/release acceptance.
The reason for unequal fresh/expected identity remains OPEN; the older
SDK READ/WRITE-9 cleanup cause is a different, still-open investigation.

Five selected active native/IIO/HackRF/pthread/ONEUSB module paths and their
on-disk hashes matched the SAME package manifest. This is not process-memory
or exhaustive active ABI attestation. Current Computer Use capture, activation,
keyboard and coordinate/indexed clicks worked; modal accessibility sometimes
returned null, and a prior indexed modal value-set failed. Fresh screenshot/
keyboard observations recovered control, without security/settings changes
or a permanent-helper-repair claim.1440×912logical is not FHD/QHD/DPI proof.

Still required: DEFAULT single Analyzer exact-profile/presenter RF integration,
shared/time-sliced new-package RF control qualification, genuine Windows
held-middle input, bounded tinySA identity-mismatch cause capture, tuning
groups/genuine Ethernet, sustained/performance/UX/review/release. No source
profile, native DSP, Fs/FFT/group, SDK or queue/memory budget was reduced to
make the tests pass. APP07 remains PARTIAL; the full APP00–14 goal remains
active, APP06E planned and the expired APP05 timer was not restarted.


### Default Analyzer full-profile RF admission foundation — 2026-10-01

The default one-source Analyzer now has an application-level immutable RF
context and inert proposal/compiler, using the same shared lifecycle and
receiver/recorder exclusion. This is groundwork for the remaining presenter/UI
control chain, NOT a newly usable default middle-drag feature or APP07 closure.

All five existing typed strategies retain their complete original profile:
AD936x RTBW changes only center; HackRF RTBW changes only center and preserves
gain stages, FFT/hop/group, queues, persistence and generation; AD Sweep retains
both the full applied Live profile and the actually accepted Sweep request;
HackRF Sweep and tinySA retain their full family request and exact selected
source reference while changing only start/stop. The controller retains the
successful Sweep request with its actual assigned epoch/operation, through
Stop; a failed or newer operation, RTBW or bounded tool cannot borrow it.

Requested/effective half-away rounding is explicit: whole MHz for HackRF Sweep,
integer Hz otherwise. There is no edge clamp, Fs/FFT reduction or allocated
epoch/generation in the compiler. tinySA input/settings and additional
correction applicability are revalidated, not silently extrapolated.

The existing application graph composes inert family preflight from the same
HackRF/tinySA owners. Start reuses its existing gates; SDK identity probing,
claim, factory, lease and actual lifecycle remain at the original boundaries.
AD capability/resource and paired-native extended geometry admission occur
before Stop. Missing contracts, stale exact source/revision/operation/profile,
active/unknown recording and a pane-owned capture claim refuse the RF scope.
Ordinary Stop remains available for recording cleanup. Fresh frame sequences
are not part of the control anchor. The guard performs no implicit Stop,
Apply or Start; Sweep consumers must preserve the existing owned facade and
single terminal-publication obligation. No new executor or native DSP change.

Candidate software checks covered full non-frequency field preservation,
forged/stale contexts, native geometry/memory refusal, recording exclusion,
actual shared graph preflight and owned Sweep terminal order with fake SDK/
serial only. An outdated service test that rejected a whole-MHz padded stop
was corrected to test fractional-MHz refusal plus explicit analysis/capture
padding; the domain/native contracts were not relaxed. Exact-source/full and
matched frozen/physical qualifications are distinct evidence, not implied by
these focused tests or by the old9425 diagnostic EXE.

Still OPEN: default presenter-worker commands, Qt terminal/receipt ordering,
all one-source view history reset before authorized Start, bounded RU/EN
impact preview and numeric/middle input, Stop/close cancelling queued restart,
then exact new-package Windows and physical qualification. The independent
resource RF route remains separate; do not reconstruct a default full profile
from its simpler pane draft. APP07 remains PARTIAL/full APP00–14 ACTIVE.


Exact product source3a38031257559cc817c1365be7585622782695a1 subsequently
passed117 focused tests in4.757s and the full clean-HEAD V2 source gate:
1216total/1150passed/66skipped/zero failures421.612s. Exact source/native
provenance was true; compiled deferred and outside-product import lists were
empty. The four historical NaN validation warnings remain. The main-process
native input was unchanged packaged9425/native762fceb6; this is NOT a newly
built3a frozen GUI, new CTest/RX/Windows performance gate or default UI feature
acceptance. The25 new methods include21 V2,3 controller and1 padding check.
A later public-doc-only commit must not relabel these3a source gates.

### Default Analyzer RF control chain — 2026-10-01, source implementation

The ordinary **UI V2 Analyzer** now connects full-profile RF shifts to its
existing Live and continuous-Sweep presenters. It does not convert a complete
default profile into an independent `PaneSlotDraft`, create a receiver/executor,
or send settings from a GUI callback. The five typed family/mode strategies
retain their admitted Fs, RF filter, FFT/hop/detector groups, gains, persistence,
queues, Sweep geometry and instrument settings. RTBW and Sweep use the same
Spectrum/Waterfall gesture and numeric RF-offset command on the Analyzer tab.

Middle-button release produces one inert proposal, using the original pixel
scale and scalar source/selection/mode/RX/session/epoch/config/clock/unit anchor.
New sequences do not cancel a valid held gesture; a different measurement
identity does. Numeric entry reaches the same serial-worker preflight. The
scrollable RU/EN impact preview includes all affected one-source views and the
unchanged scalar acquisition/settings profile. Cancel is the default action.
While running, an explicit approval authorizes **Stop → Apply → GUI receipt →
Start**; while stopped, Apply only arms that exact full request for the next
explicit Start. Disarm uses ordinary explicit Stop; it does not silently restore
older settings. There is no hidden restart, discovery, retry, edge clamp or
Fs/FFT reduction.

The established Sweep owner delivers its terminal packet on Qt before Apply.
After Apply, frequency controls, model caches and **all 1–4 one-source histories**,
including parked views, acknowledge the new binding before any Start command.
Retired RTBW spectrum, density and waterfall layers cannot return on a Busy or
locale notification. Missing/failed/superseded receipts block every Start until
explicit Stop. Next Start uses the current application receipt once, with fresh
native acquisition epoch allocation at the existing owner. Source/profile and
active/unknown recording checks remain inside the SAME receiver/recorder
transaction at each control boundary.

User Stop cancels queued continuations, including Apply/ACK/Start, and waits
asynchronously for the existing lanes to acknowledge cleanup. Explicit close
intent cancels a pending RF chain even when active RX still bars window close;
read-only `can_close` does not perform Stop. Ordinary active acquisition still
requires the user's explicit Stop. Executor submission and snapshot-preparation
failures finish their RF phase rather than stranding the GUI. Actual operation/
epoch transitions and host control elapsed time are labeled
`PROFILE_OR_RF_PLAN_CHANGE`; they are not ADC loss, RF duty or detection probability.

For a running tinySA, frequency-only preflight can use only the SAME healthy,
claimed acquisition's admitted immutable source facts and unchanged full request.
It rejects changed non-frequency settings/epoch, cancelled/failed/unclaimed
owners and stale selection. New Start still requires released/current catalog
admission and the existing identity/settings checks. This avoids trying to probe
an already owned serial port; it does **not** fix or ignore the separate historical
tinySA version/identity/readback-deadline failures.

Source tests use the actual V2 shell, widgets, serial presenters and shared
application owners with fake physical SDK/serial boundaries. They exercise both
canvases' actual Qt middle events, default Cancel, five strategies, all-view
receipt ordering, stopped arming, full-profile preservation, recording races,
Stop/close cancellation and failure cleanup. Focused and exact-source results
are recorded separately below when available. No new frozen EXE, Windows held-
middle, physical RX, DWM/FHD/QHD/DPI, sustained performance or release acceptance
is implied by these source tests or the unchanged old9425 native module.

APP07 remains PARTIAL. Next: matching new-package Windows/physical qualification
of this default path, shared/time-sliced RF cases, genuine held-middle input,
bounded tinySA first-cause diagnosis, tuning groups/genuine Ethernet, sustained
performance/layout/UX and independent review/release. APP06E remains planned;
the expired APP05 timer is not restarted and full APP00–14 scope is unchanged.

### Empty-serial AD936x route RF admission — 2026-10-01

A physical default-Analyzer qualification found that the connected AD9364
reports empty serials in BOTH IIO `hw_serial` and USB `usb,serial`. Its SAME
read-only owner reports coherent tuning/Fs/filter/gain bounds and the loaded
runtime protocols. Working I/Q does not establish stable physical identity;
canonical capability/calibration mapping correctly continues to refuse this
device. No firmware, network, driver or security setting is changed.

Default RF control can now use an exact descriptor-owned, immutable set of
route-scoped bounds when, and only when, that coherent observation reported a
genuinely empty serial. Missing/malformed/nonempty identity, old protocols,
unknown firmware, unavailable/invalid ranges and pending release cannot acquire
this path. The facts are NEVER published as a stable capability snapshot,
calibration identity or USB/IP alias. Independent catalog-based admission is
not relaxed. The RU/EN RF preview explicitly warns that the physical identity
and calibration join are unverified and same-route device replacement without
a serial may be indistinguishable.

Preview performs no probe or RX mutation. Every effecting boundary checks the
SAME current source/selection, exact facts object, selected URI, owner/runtime,
full typed request and receiver/recording transaction; equal copied facts or
facts from a later selection do not authorize an older proposal. Tuning/Fs/
filter/gain/Sweep-window limits are readbacks, not model assumptions. The usual
native geometry/budget, all-view GUI receipt, explicit Stop/Apply/Start and
ordinary Stop/close semantics remain intact. New Start remains the existing
owner's configure/readback operation, not an identity proof or a hidden retry.

Fifteen new tests cover mapping refusal vs route evidence, strict negative
admission, no alias/calibration claims, stale/copy/runtime/recording races,
full-profile RTBW and Sweep bounds, stopped arming, both locales and the actual
four-view Qt receipt-before-Start chain. Candidate expanded regression:
216 passed, no failures/errors/skips. This candidate result is NOT exact-source,
matching frozen Windows, new physical RF, sustained or release acceptance;
those are recorded separately after qualification. APP07 remains PARTIAL.

### Default RF qualification — exact product 75543d7, 2026-10-01

The corrected product `75543d7a83443ce9fb07a4f96ad26850e00daafc` passed the
full clean-HEAD UI V2 source gate: 1253 total / 1187 passed / 66 skipped /
zero failures or errors in 419.399s. Source/native provenance was exact,
compiled deferred and outside-product import lists empty, and the native
input unchanged. Four historical NaN validation warnings remain. The first
8f full run failed the existing strict Russian prose guard; only catalog
wording was corrected, not its assertions, identity or admission contracts.

The matching, separately tagged diagnostic CPU/HackRF package passed the
official full pipeline without skip-gates: 40/40 CTest in 32.15s, all 658
manifest-covered frozen payload files, shared runtime and source snapshot
verification. The snapshot contains 530 inputs. It uses Qt6.11.1 and the
unchanged native schema5/factory2/DSP1/persistence1/Sweep1/geometry1 contracts,
maximum2048 segments and128MiB reduced-data budget. It is NOT promoted to
canonical/static/current release. Later documentation commits do not relabel
these exact product source/build/hardware results.

Five bounded real-device tests of the SAME source and matching packaged
native module passed on the ordinary Analyzer's four same-source views:
AD9364 USB RTBW at61.44MS/s/RF56MHz/F4096 and Sweep at61.44MS/s/W36MHz/N4096/
F8192; HackRF USB RTBW at20MS/s/RF20MHz/F4096 and Sweep at20MS/s/F2048; tinySA
USB Sweep at100–300MHz/1001points/LOW/targetRBW300kHz. Each exercised actual
Qt middle events, default Cancel, approved numeric+5MHz, all four GUI history
receipts before new Start, a second stopped Apply-only shift, separate Start,
real epoch/operation transitions and normal Stop/shutdown. These OFFSCREEN
source tests are not frozen Windows input, RF accuracy or sustained tests.
The tinySA target is not a newly serialized numeric actual RBW readback;
HackRF setter acknowledgements are not unavailable Fs/filter getter proof.
Initial private-observer stale source-reference/diagnostic-attribute failures
before RX remain recorded; only the observer was corrected, no product guard
was relaxed and no implicit retry was added.

The SAME new frozen Windows EXE was then operated on the default AD USB path
with four views. Explicit RTBW Start showed SDK61.44MS/s/RF56MHz/F4096; default
Cancel preserved2400MHz/epoch3, approved RF+5 produced2405MHz/epoch5, stopped
Apply prepared2410MHz and cleared all four histories without RX, and separate
Start produced epoch7. Sweep on the SAME Analyzer tab progressively displayed
partial coverage before a complete100–1000MHz pass; approved RF+5 changed it
to105–1005MHz/fresh epoch1 with four fresh histories. Stop during a partial
pass retained cancellation/gapped status instead of reporting Complete.
Normal Close removed the tested process/window, followed by repeated full
package/native/source verification. No firewall/network/firmware changes.

Computer Use capture/activation and observed numeric controls were usable;
one stale cached-index error and helper/caption observations remain recorded.
This is not a permanent helper repair or actual Windows held-middle proof.
1440×912 logical captures are not FHD/QHD/DPI, DWM50ms, lossless, RF duty/Pd,
RF accuracy or soak acceptance. Five selected package-local loaded-module
paths/current disk hashes are not in-memory/exhaustive ABI attestation.

APP07 remains PARTIAL/full APP00–14 ACTIVE. The bounded default path unit is
qualified, not the complete APP07 release. Next: matching new-package shared/
time-sliced RF controls with independent peers, genuine Windows held-middle,
bounded historical tinySA identity/readback first-cause diagnosis, tuning
groups/supported dual-RX/genuine Ethernet, sustained/layout/accessibility,
independent review and release. Short tinySA success does not fix the previous
late identity mismatch or readback-deadline failure. APP06E remains planned;
the expired APP05 timer is not restarted. Private reports/runners/raw captures
and device identities are not part of the public publication whitelist.

### Independent SDR Sweep RF controls — exact product 059b34e, 2026-10-02

The preceding frozen product exposed a real UI defect: independent SDR Sweep
panes published progressively but could not enable RF shift. The board anchor
incorrectly required a single configuration generation. SDR Sweep explicitly
has per-segment generations and no global producer generation. Only for an
installed SWEEP binding, the existing exact binding and actual producer epoch
now permit the RF anchor when global config_generation is None. No zero/clock
is invented; per-segment provenance remains unchanged. Missing mandatory
RTBW/instrument generation still refuses the anchor. Native/DSP/SDK/Fs/FFT/
queue/budget/quality/time contracts are unchanged.

Nine new real-Qt/common-owner methods with AD936x/HackRF fake-SDK matrices
cover progress and terminal anchors, actual held-middle events on Spectrum
and Waterfall, stale-epoch refusal, default Cancel, shared-resource impact,
all affected GUI history receipts before Start, stopped Apply-only arming,
and strict RTBW/instrument negatives. Shared approval shifts only the selected
pane: equal requests can become two time-sliced capture jobs on ONE RX. Both
affected histories reset, while an independent fake peer retains its owner,
activation/binding and history. That peer test is not physical peer proof.

Exact clean product `059b34e89c917f7f1cc31266e0fc2c2827f5ed4b` passed focused78
tests and full serial UI V2 source gate1262total/1196passed/66skipped/zero
failures-errors in482.703s. Before/after tracked source and native were exact,
compiled-deferred/outside-import lists empty. Four historical NaN warnings
remain. The matching separately tagged diagnostic CPU/HackRF build passed the
official full no-skip pipeline:40/40CTest34.23s, all658 manifest-covered frozen
files, source530-input snapshot and native/shared-runtime verification.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1/2048segments/128MiB
remain unchanged. No canonical/static/current release promotion occurred.
Later documentation commits never relabel exact059 source/build/hardware.

The SAME new frozen Windows EXE then exercised real HackRF USB Sweep20MS/s/
FFT2048 in two shared100–220MHz panes and two Empty panes. Apply stayed inert,
explicit Start produced two changing Spectrum/Waterfall pairs with RF enabled.
The+5MHz preview showed all affected panes and default Enter Cancel was inert.
Explicit Stop/Apply/Start shifted only pane1 to105–225MHz; pane2 stayed100–220,
both histories were fresh and the one RX time-sliced both requests. Observed
revisit2.11–2.12s exceeded model2.06s and was not hidden. After ordinary Stop,
a second+5MHz Apply-only changed pane1 to110–230, cleared BOTH affected
histories and did not start RX. Separate Start resumed progressive pairs.
Final Stop/CloseLayout/normalClose removed the process/window; repeated full
658-file/native-loader/source-snapshot verification passed.

This Windows RF witness has NO independent physical peer and no exact numeric
GUI epoch/gap readback, true Windows held-middle, DWM50ms, FHD/QHD/DPI, RF duty/
Pd/accuracy, lossless or soak acceptance. Actual Qt middle tests are distinct
from Windows hardware input. Helper popup/index errors and accessibility lag
remain recorded; fresh observation recovery did not change security/system.
HackRF setter acknowledgements are not nonexistent Fs/filter getters.

A separate mixed physical source+matching-native attempt did NOT qualify:
initial/R1 private Empty-observer errors and R2 initial-start timeout are
retained, all with normal Stop/shutdown. A first-failure event occurred, but
the affected resource and finite cause were not captured: cause UNKNOWN, not
a proven tinySA or SDK-cleanup diagnosis. No RF action or mixed R3 retry was
performed. The future private observer now records cached first cause before
cleanup and stops waiting on stop_required; compile-only, no new physical
PASS. Earlier real shared/time-sliced HackRF plus independent tinySA on the
preceding product remains a distinct functional witness, not new059 RF proof.

APP07 remains PARTIAL. Next: bounded pre-cleanup mixed first cause, matching
physical independent-peer RF, strict independent AD admission, supported
tuning groups/dual RX/genuine Ethernet, historical SDK-cleanup cause, actual
Windows input when supported, sustained/layout/accessibility, independent
review and release. APP06E Cut DC/spur remains planned; expired APP05 timer
is not restarted. Private captures/runners/reports are excluded from publish.

### Stopped-frame presentation and mixed RF qualification — 2026-10-02

A dedicated UI V2 design/review agent now handles interface design changes;
the parent retains backend, physical RX, integration and qualification duties.
No simultaneous product writes are permitted during exact tests/builds.
Design review does not constitute whole-backend independent release approval.

A new bounded source Qt + matching native check on exact product059, with only
its verified public-doc tail094, exercised two shared HackRF USB Sweep20MS/s/
FFT2048 panes100–220MHz, independent tinySA100–300MHz/1001 points/LOW/target
RBW300kHz, and Empty4. The initial-start diagnostic passed; the preceding
timeout did not reproduce and its affected resource/cause remains UNKNOWN.
A separate RF check used actual Qt middle-button events: default Cancel was
inert, explicit approval shifted only pane1 by10MHz, preserved pane2's range,
and converted shared capture into time-sliced jobs on ONE HackRF RX. Both
affected histories were empty before Start. The independent tinySA retained
epoch1/activation1 and published a genuinely newer frame after the RF change.
The recorded0.2591743s control interval is host elapsed time, not ADC RF gap,
duty, detection probability or lossless proof. Both runs stopped and shut down
normally with no residual Python threads. This is source059 qualification,
not a frozen Windows independent-peer witness or a timeout causal fix.

Exact product `f060651a1cb4f78eaa117c7c68c8445e9ab5c852` corrects one proven
UI wording defect: an IDLE/STOPPED independent pane with no presented frame
now says “RX stopped · no frame” (localized EN/RU). Ordinary Stop with a
retained frame keeps the existing retained-frame label. It reads only the
currently presented immutable bundle; capture/pump/owners/epochs/gaps,
STARTING/STOPPING/STOP_REQUIRED/RUNNING and native/DSP/SDK/Fs/FFT/budgets are
unchanged. One new actual-Qt method covers inert Apply before first Start;
existing tests additionally cover both locales and automatic cleared-history
receipt before separate Start. Expanded focused74 tests passed. Full exact
serial UI V2 gate1263total/1197passed/66skipped/zero failures-errors654.824s
passed with clean before/after source, unchanged native762, no deferred compiled
tests or outside product imports. Four historical NaN warnings remain.
Two scoped mypy Optional-QDialog diagnostics reproduce identically in the
immutable preceding HEAD; they remain baseline diagnostics, not mypy PASS.

The matching full no-skip diagnostic CPU/HackRF build passed40/40CTest,
all658 frozen files, source530 snapshot, native and shared-runtime checks.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1 and the2048segment/
128MiB budget remain unchanged. The separate tagged package was not promoted
to canonical/static/current release. Its source/build stay exactf060651;
later documentation commits must not relabel them.

The SAME new frozen Windows EXE then checked real HackRF RTBW20MS/s/FFT4096
140–148MHz in one pane plus three Empty panes. Apply remained inert with the
new no-frame label. Explicit Start produced changing Spectrum/Waterfall;
ordinary Stop retained both plots and the retained-frame label. Stopped
numeric+5MHz RF Apply-only changed the range to145–153MHz, cleared both plots,
showed the no-frame label and left RX stopped. Separate Start produced fresh
plots. Final Stop/CloseLayout/normalClose removed the process/window; full
postclose658-file/native/source-snapshot verification passed. The design agent
reviewed the original sequence without finding a new correctness defect.
Accessibility text still lagged some transition screenshots; this is not full
AT coherence, temporal jitter, DPI, DWM50ms, RF accuracy, sustained/soak, numeric
GUI epoch/gap, genuine Windows held-middle, or new frozen Sweep/peer acceptance.

APP07 remains PARTIAL. Next UI design package: a localized, readable RF impact
summary distinguishing the target range, all history-reset panes and one-RX
shared/time-sliced topology while retaining exact profiles/default Cancel/
explicit approval and Apply≠Start. Strict independent AD admission, supported
tuning groups/dual RX/genuine Ethernet, historical first-cause diagnostics,
sustained/layout/accessibility and complete independent release review remain
open. APP06E Cut DC/spur remains planned; the expired APP05 timer is not reset.
Private device identities, raw screenshots, runners and reports are not
included in the public publication whitelist.

## 2026-10-02: readable RF-impact summary — exact b257 qualification

Product `490d2fe0089123f55bed47a775d633f7b4ce78ee` introduced an optional
localized operator summary above the scrollable exact RF profiles.
Corrected product `b2572c9e00c9815b55f6ca1daf6899f06154835f` fixes two Russian
phrases, without relaxing the strict language catalog. The summary separately
identifies the selected requested range, ALL history-reset panes, one physical
RX's shared/time-sliced/dedicated jobs, unchanged independent receivers, and
running approved Stop→Apply→Start versus stopped Apply-only/separate Start.
Exact profiles/weights/revisit and default Cancel remain. The default
Analyzer caller is unchanged. No native/DSP/SDK/Fs/FFT/queue/budget/owner/
admission/recording/epoch/gap/quality/time behavior was modified.

The first exact490 full gate failed:1265total/1197passed/66skipped/2failures.
The new untranslated Russian word was corrected. An unchanged0.5s synthetic
soak navigation-count failure remains historical with root cause UNKNOWN;
its standalone rerun passed, but no threshold/duration or assertion changed.
Corrected exactb257 serial full UI V2 gate passed1265total/1199passed/66skipped/
zero failures-errors508.564s, clean source before/after, no deferred compiled
tests or outside imports, unchanged native762 and four historical NaN warnings.
Focused42 tests included strict localization. Two baseline Optional-QDialog
diagnostics remain; targeted two-file mypy PASS is not whole-project PASS.

Matching official no-skip tagged CPU/HackRF build passed40/40CTest33.71s,
all658 frozen files/source530/native/shared9DLL/ONEUSB and runtime checks.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1 and2048segment/
128MiB limits remain unchanged. This diagnostic package was NOT promoted to
canonical/static/current. Initial490 source/build/inert GUI evidence and later
documentation SHA must never be relabelled correctedb257 qualification.

A separate real source-Qt + matchingb257 native check passed with two shared
HackRF Sweep20MS/s/FFT2048 windows, independent tinySA100–300MHz/1001points/
LOW/target300kHz, and Empty4. Actual Qt held-middle proposals exercised default
Return/Cancel and explicit approval. Only the target range shifted; ONE RX
changed shared→time-sliced; both affected histories were cleared before Start.
The independent instrument retained its epoch and produced newer data.
The recorded0.2521566s control interval is host elapsed time, NOT RF/ADC gap,
duty, detection probability or lossless proof. Normal Stop/shutdown left no
test threads. Short success does not resolve historical instrument/SDK causes.

The SAME corrected frozen Windows EXE separately exercised one HackRF RX
with two shared100–220MHz Sweep windows and Empty3,4. Stage/Apply stayed inert;
explicit Start produced changing Spectrum/Waterfall pairs. The new visible
summary distinguished target105–225MHz, histories1,2 and shared1job→time-sliced
2jobs. Default Return/Cancel preserved both ranges. Explicit StopApplyStart
changed only pane1. After ordinary Stop, Apply-only shifted it to110–230MHz,
cleared BOTH histories and kept RX stopped; a separate Start produced fresh
pairs. Observed revisit about2.10–2.12s versus model2.06s is NOT a guaranteed
maximum or RF-speed result. Final Stop/CloseLayout/normalClose removed the
tested process/window; postclose full658-file/native/source checks passed.

Dedicated UI-agent stills review accepted the EN modal hierarchy, readable
summary/details/defaultCancel/pinned approval at1440×912logical, without
overlap. Small crowded axis/pass labels, dense raw detail fields and unclear
requested-range versus FFT-bin pane-header extent remain UX debt. Three popup
capture errors recovered via one fresh unique-window binding each; accessibility
transition/value lag and modal null-tree remain. No permanent helper repair,
frozen independent-peer/Russian visual/FHD-QHD-DPI/DWM50ms/numericGUIepoch-gap/
genuine Windows held-middle/RF accuracy/duty/Pd/lossless/sustained-soak claim.

APP07 remains PARTIAL. Concrete capability-backed RX2/common tuning-group
native-stream product integration is IMPLEMENTATION OPEN: existing C++ dual-RX
primitives and fake contracts are not a connected product acquisition path.
Strict independent AD identity/admission, genuine Ethernet, historical
first-cause diagnostics, sustained/layout/accessibility and full independent
release review remain open. Dedicated UI agent handles design/development/
review; parent handles backend/RX/integration/provenance. Full APP00–14 goal
remains ACTIVE, APP06E Cut DC/spur planned, APP05 expired timer not restarted.
Private identities/screenshots/runners/reports stay outside public whitelist.

## 2026-10-02: multi-RX prerequisite — actual selected PHY gain control

Native device configuration previously selected RX2 digital samples but still
wrote/read gain on PHY voltage0 (RX1). Windows and Linux implementations now
resolve optional **PHY** voltage1 exactly, separately from the stream's RX1-Q
voltage1. Missing, output, aliased, wrong-ID or incomplete gain controls refuse
RX2/BOTH before interrupting an existing compatible RX1 stream or writing RF.
Retained common LO/Fs/filter and selected gain state are read before Stop.
Successful RX2 writes/reads only its selected gain control; BOTH applies the
single requested gain policy to both selected chains. Distinct simultaneous
gain intents are NOT yet implemented. Common LO/Fs/filter remain one device
transaction, not independently tuned RX channels.

AppliedConfig adds readonly Python `receiver_selection` and `receiver_gains`
with per-chain actual mode/gain. Legacy scalar gain describes the first selected
chain (RX2 for RX2-only), and the integer pool-size configure overload remains
unchanged. Failed configuration restores/verifies all selected modes and prior
manual gains plus common controls; unverifiable rollback invalidates admission
and bars Start until a successful explicit reconfiguration. AGC instantaneous
gain is readback, not a guarantee it remains fixed or can be restored as manual.
No hidden restart or digital-to-RF independence inference is introduced.

Candidate Windows CPU+official-HackRF native build and full CTest 41/41 passed
(33.07s), including new deterministic per-chain isolation/readback/rollback,
mode-ACK mismatch, failed-rollback Start refusal, and missing/aliased PHY
refusal without stopping RX1. Explicit freshly staged Python binding test
1/1 passed (0.153s); Ruff/compile/diff checks passed. These are mock/source
prerequisite results, NOT physical dual-RF qualification or completed product
RX2 ownership. Old b257 EXE/native/Windows results stay tied to b257; its frozen
native is not this changed native. Current-source exact gates and a matching
new frozen package must be recorded separately.

APP07 remains PARTIAL: one-buffer paired acquisition/DSP publication through
the product owner, explicit endpoint/group compiler/composition, combined
resource budgets, group-wide recording/control/receipt transactions and only
then capability-backed UI RX choice are still required. A dedicated UI agent
prepared a future view/layout/refusal plan, not an enabled RX2 selector.

### Exact c88 prerequisite qualification (not completed dual-RX product)

Exact product `c88fbffbb97cf091ca14d35a6ce9a30860edc13b` passed the serial
AFTER-freeze full UI V2 source gate using its matching packaged native:
1265 total, 1199 passed, 66 skipped, no failures/errors, 445.192s; tracked
clean before/after, exact provenance true, deferred/outside modules empty.
Four historical NaN warnings remain. Packaged-native/mock binding 1/1 passed
(0.088s), separately from that full suite. Linux source is equivalent but
Linux compilation was not performed.

The first full pipeline stopped safely before freezing at an installed-IIO
versus HackRF shared-libusb collision; failed partial output is preserved.
A new tagged R1 pipeline with the previously hash-admitted ONEUSB runtime
inputs passed 41/41 CTest (31.23s), all658 frozen files, source531/native
snapshot binding, fake/default offscreen shell, IIO/tinySA/shared9DLL checks.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1 and existing
2048segments/128MiB bounds remain. No system SDK/DLL/network/firewall changes.
R1 diagnostic EXE hash9918d3d0/native72713f92/sourceCONTENT201beac2/snapshot
FILE37f6d9b7; NOT canonical/static/current promoted. Later doc-only commits
must not relabel c88 source/build/HW evidence.

One bounded physical native-only RX1 regression with this packaged native
passed (0.4849014s host elapsed): actual Fs61.44MS/s/filter56MHz/center2450MHz/
gain20dB/RX1 per-chain readback/epoch1, three262144-sample blocks with retained
quality8192. The observed digital scan pair was RX1 only; exact missing-pair
RX2/BOTH refusals preserved RX1 streaming/epoch and subsequent refills.
Final Stop/disconnect completed; post-test all658/native/source checks passed.
Five selected package-local active DLL paths/current file hashes are not
in-memory/exhaustive ABI attestation. ADC Fs readback is NOT uninterrupted
61.44MS/s USB transport, dual RF independence/phase/gain calibration, FFT LPS,
visible Windows UI/input/DPI/DWM timing or sustained-soak acceptance.

Dedicated UI-agent follow-up separates one BOTH requested gain policy from
per-RX actual readback and AGC instantaneous gain. It does not enable RX2 or
independent gain knobs. Next native/product work must also distinguish DSP
analytical burst capacity from latest-wins render queue capacity; existing
DualRxDspPublisher uses one small capacity for both and a CPU-only backend.
No new loss measurement, CPU/AUTO/CUDA equivalence or completed streaming
integration is asserted. APP07 and the complete APP00–14 roadmap remain open.

### Selected single-chain native engine prerequisite (not paired product owner)

The existing `PlutoFixedBandEngine` now accepts a trailing typed
`FixedBandConfig.receiver_selection` for RX1 or RX2. Legacy positional calls
still select RX1. RX2 obtains its actual I/Q lane from the same
`PlutoDevice.refill_receivers` owner and passes that block through the existing
native acquisition queue, selectable DSP backend, recorder, persistence and
Spectrum/Sweep-line paths; it does not create a second stream/context or a raw
Python processing loop. Configuration, metrics and actual applied readbacks
identify the selection. Caller-supplied `source_id` remains unchanged; RX2
frames carry explicit `receiver_selection` metadata, not inferred RF identity.

Retuning continuous Sweep requires the same receiver selection in every
segment and preserves RX2 selection in planned/actual completed or cancelled
publication. BOTH is refused by this single-producer engine: one ordinary
SpectrumFrame must never stand in for a pair. Invalid host configuration is
validated before a running `reconfigure` can Stop; this is not a promise that
every device capability refusal during a valid reconfigure occurs before Stop.
Existing explicit native reconfigure semantics and product approval/control
transactions are not replaced or bypassed.

New native I/Q and Spectrum recordings optionally persist the canonical
digital `receiver_selection` in their JSON sidecars. Spectrum replay and
native I/Q reprocess retain that selection. Missing legacy metadata remains
unspecified, not RX1 by assumption. Unknown/noncanonical values are refused,
and a spectrum writer cannot change selection within one manifest. Existing
binary payload/version, legacy RX1 artifacts, queues, loss accounting and
memory/Fs/FFT/quality/time bounds are unchanged. This field is digital-chain
provenance, not RF connector, calibration, independence or simultaneous-RX
qualification.

The selected-chain durable roundtrip also exposed a pre-existing native
reprocess format mismatch: the writer's canonical `ci12_in_i16_le` (Pluto)
and `ci8_interleaved` (e.g. HackRF) were not recognized by the reader. It now
accepts those exact contract names and retains the earlier `ci12_le`/`ci8`
aliases; ADC precision, binary payload, units and sample interpretation are
not changed. Native tests exercise writer→reprocess→replay for all four
supported sample formats, separately from physical receive qualification.

The product compiler/group adapter and UI selector remain RX1-only. Required
paired acquisition/publication, combined budgets, group-wide control/recording
receipts, actual endpoint admission and only then capability-backed RX choice
remain OPEN. This prerequisite does not complete APP07 or narrow APP00–14.

#### Exact selected-engine prerequisite qualification, 3075bcd

Exact product `3075bcda15662af07a911183fe64d8e510d84d26` passed the serial
AFTER-freeze UI V2 source gate with its matching packaged native: 1265 total,
1199 passed, 66 skipped, no failures/errors, 480.173s; tracked clean before/
after, exact provenance true, deferred/outside modules empty. Four historical
NaN warnings remain. Two explicit packaged-native/mock binding tests passed
(0.892s) separately; candidate native full42/42 passed49.14s, matching official
pipeline full42/42 passed45.20s. All658 frozen files/source533/native and
package-local shared9DLL/ONEUSB/default offscreen/IIO/tinySA checks passed.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1 and existing
2048segments/128MiB limits remain. Linux compilation was not performed.

The new diagnostic package `APP07-RXENGINE-20261002-3075BCD` is not promoted
to canonical/static/current. EXE hash9553a0b3/nativeb69ec506/source-content
ccee8665/snapshot-file6b39c65e belong to3075, not later documentation commits.
The package uses previously hash-admitted runtime DLL inputs, not an old
native module; no SDK/driver/network/firewall/system changes were made.

One bounded physical RX1/common-engine regression on the available USB AD
route functionally passed with the same packaged native (1.6586524s total
host elapsed including setup/cleanup): actual Fs61.44MS/s/filter56MHz/
center2450MHz/gain20dB/RX1/epoch1; one admitted block produced127 analytical
FFT frames, zero native FFT-drop counter and two reduced publications carrying
source/epoch/Fs/quality8193. These are counts, not measured FFT/s or LPS, and
the elapsed script duration is not a scan-period measurement. The observed
digital scan pair was RX1 only; this is not physical RX2/dual-RF qualification.
Stop/disconnect reported streamingfalse/connectedfalse; fresh test-EXE count0,
post-test all658/native/source verifier passed. Five selected package-local
active DLL paths/current diskhashes are not in-memory/exhaustive ABI evidence.

The process also emitted SDK diagnostics `ERROR: READ LINE: -9` and
`ERROR: READ INTEGER: -9`. Their exact operation/phase/cause is UNKNOWN;
no repeat physical run, error-free/soak or SDK-cleanup-fix claim is made.
Keep them open for bounded first-cause capture before cleanup. No continuous
61.44MS/s USB, RF-path/phase/calibration, real GUI/input/DPI/DWM/50ms/duty/Pd/
lossless/sustained acceptance follows from this short prerequisite.

The dedicated UI design/review agent separately specified future producer
FFT/s, completed Sweep LPS, one-stream throughput, pane delivery and actual
paint/DWM metrics. Shared panes must not double acquisition counts; absent
or stale metrics/legacy channel metadata are not zero or implicit RX1. This
is a design note, not implemented multi-RX UI or independent release review.

### Paired DSP burst admission prerequisite — 2026-10-02

The existing native `DualRxDspPublisher` separates bounded analytical burst
from final latest-wins paired publication. Trailing
`max_input_samples_per_push` (default262144) derives analytical capacity as
`B / hop + batch + 2`; final queue remains1..64/default4. For
B262144/FFT1024/hop512/batch1 it reserves515 outputs per channel.
Old positional constructors retain defaults. No raw Python input is added.

Native regression reproduces the former four-slot analytical defect:511
computed FFTs/507 dropped outputs. The corrected bridge pairs all511 first
and512 next FFTs per channel with zero analytical drops; render cap4 can
supersede already-paired outputs independently. Batch4 retains partial batches
across blocks without render-driven flushing. These are deterministic counts,
not FFT/s, LPS, transport-rate or physical dual-RF measurements.

Both payloads undergo structural/sample-bound/RF/index/non-finite preflight
before either DSP mutates. Common sequence/index/generation/rate/center/format
discontinuity resets both histories. Pending pre-gap pairs are abandoned and
counted separately from analytical drops and final-queue supersession; entire
ready burst is checked for pairing before publication. Backend exceptions
reset shared state and propagate to the owner.

Read-only diagnostics conservatively combine both input payloads, DSP working
sets, analytical outputs and final pairs. Existing128MiB component/512MiB
total host-payload ceilings are not doubled. This synchronous bridge estimate
does NOT cover whole-owner pools/tees/recorders/persistence/Sweep, RSS,
transient replacement peak or vendor FFT workspace. Future owner must add
all consumers under the SAME aggregate limits, not reuse two full budgets.

The common backend factory is used. CPU remains compatible default; AUTO
reports requestedAUTO/actualCPU/no invented fallback for each channel.
Forced unavailable CUDA/HIP retains typed refusal, not silent CPU substitution.
A self-tested non-CPU pair also explicitly refuses until vendor working-set/
fallback-replay reservation is qualified. Paired CUDA remains OPEN; ordinary
single-channel backend is unchanged. No Fs/FFT/quality/timestamps are lowered
or changed to satisfy tests, and no hidden restart is introduced.

Acquisition/group owner, pre-coalescing persistence/recording/Sweep consumers,
whole-owner admission and product compiler/UI RX selection remain OPEN. This
is not a simultaneous application stream or a second opener, and does not
complete APP07 or narrow APP00–14. UI V2 development and design review use
a dedicated UI-only agent; root retains native/backend/RX/build/provenance.
Its read-only review identified typed endpoint, common-tuning Stage preview/
refusal, group-impact copy, scoped metrics and Replay channel provenance as
future integration requirements. No new selector/physical dual proof claimed.

#### Exact paired-burst prerequisite qualification, 85187f4

Product `85187f49b3e20a4085632faae41b8b19d28ce45d`: candidate42/42CTest49.82s;
matching official full42/42CTest45.49s/all658 frozen files/source533/native/
shared9DLL/ONEUSB/default offscreen/IIO/tinySA pipeline passed. Diagnostic
`APP07-PAIRBURST-20261002-85187F4` is NOT canonical/static/current/promoted.
EXEb874d6b0/native9f9d315d/source-content7134a8c5/snapshot-fileba3094d0 belong
to85187f4, not later documentation revisions. Qt6.11.1/schema5/factory2/
DSP1/persistence1/Sweep1/geometry1/2048segments128MiB limits unchanged.

Serial AFTER-freeze exact source UI V2 gate1265total/1199PASS/66skip/no
fail-error477.036s; clean before/after/exact provenance true/deferred[]/
outside[]. Four historical NaN warnings retained. Explicit matching-packaged
native/mock binding3PASS1.248s separately (new paired budget plus prior selected
engine/gain). Ruff1/mypy1/compile1/diff passed; post-gate all658/native/source
verifiers passed. No test EXE remained. No physical RX retry/dual RF/paired
application owner/GUI-input-DPI-DWM/performance/lossless/soak acceptance;
historical SDK-9 cause remains UNKNOWN. Whole independent release review OPEN.


### 2026-10-02: paired same-owner FixedBandEngine data plane (partial)

The native backend now has explicit `PairedFixedBandConfig` and
`FixedBandEngine.configure_paired`. It uses the SAME PlutoDevice/context,
ONE IIO buffer, acquisition queue/worker, DSP worker and control lifecycle.
There are two caller-exact RX1/RX2 producer IDs, not a BOTH Spectrum source
or two competing engines. Common URI/LO/Fs/filter/gain/acquisition geometry,
FFT timing/backend/cadence must agree; incompatible/unknown digital peer or
aggregate budget refuses before RF. Ordinary single-producer BOTH still refuses.

Native analytical pair consumers reuse per-chain persistence, continuous
fixed-window Sweep lines, I/Q tee and native recording writers BEFORE final
paired latest-wins reduction. Both partial DSP batches flush at terminal Stop;
all recording workers join/finalize and common buffer closes. Per-channel
recording artifacts are separate, not an atomic group ledger. Spectrum recording
keeps existing selected-snapshot cadence, not every FFT. No raw I/Q, native
callbacks or worker hooks are exposed to Python. Paired retuning Sweep coordinator
and product paired admission/routing remain OPEN.

Reduced readers are paired Spectrum poll/latest-drain and explicit receiver
persistence/line reads. Legacy single Spectrum/history reads refuse paired mode.
Stopped configure only arms; Start is separate. Next configure/Start preserves
one context and refreshes both epochs/histories. Native-only staging drain keeps
the recording reconfigure guard for BOTH channels. No hidden product restart.

Shared discontinuity notifies the owner BEFORE the next synchronized epoch:
stale queued/terminal pairs and history queues are abandoned, both densities
reset, separate abandonment counters remain, and both frames expose exact shared
gaps/BackendDiscontinuity. RF timestamps and hardware-overflow availability stay
honest. Live metrics use protected DSP caches. Device/acquisition fields repeated
in the two channel views are ONE common stream and MUST NOT be summed. Group
wall processing time is charged once, not invented as two per-RX CPU durations.

Both channels' pools/DSP outputs/recording/density/line reservations plus paired
snapshots/common scratch use SAME128MiB component, density256MiB, total512MiB
limits, not two independent full budgets. Single pool accounting now includes
its actual minimum8 blocks. Writer-normalized canonical recording-base aliases
refuse before RF (tested suffix/ASCII alias case). Unicode/case-alias coverage
remains a follow-up before product admission. Payload reservation is not RSS,
transient old+new replacement peak or vendor workspace. No Fs/FFT/quality
reduction, firmware/driver/network/firewall/security change or TX.

Dedicated UI-only design/review agent `gpt-6-sol/high` completed source review;
no UI edits/whole-release review/physical dual-RF proof claimed. Keep selector
disabled until fresh typed RX1/RX2 capability endpoints and a single paired job/
owner/adapter route exist in the ACTUAL V2 graph. Required UI work: AD-only
chain choice beside Source, compact common-tuning group summary, requested vs
actual Stage preview and aggregate cost/refusal, all affected recording/history
recipients, typed localized per-RX captions (not ID suffix inference), group
Stop defaultCancel and separate common/per-RX/pane/paint metrics. Two digital
chains are not independently tunable radios; two panes are not two streams.
Root retains backend/native/RX/build/provenance; no other subagent used here.

#### Exact paired-engine qualification, ea1f4e4

Runtime implementation `109ac56d0d7ef43fc3f3f50046a2e4e4b611c30c` and
whitespace-corrected exact product `ea1f4e493823a30d843743cbac7a19928b0ba0d3`.
Candidate43/43CTest51.37s; official matching full43/43CTest47.17s and full
658-file frozen/source534/native/shared9DLL/ONEUSB/default offscreen/IIO/
tinySA pipeline passed. New mock native cases exercise actual paired acquisition,
distinct digital FFT values, pre-coalescing consumers/recordings/lines, shared
Stop/restart/cancel/gap/flush, aggregate preflight and absent peer/leak guards.
The initial10ms gap injection FAILED (1532pairs/0gaps/0drops);80ms test-only
injection exercised loss without changing product cadence or timeout. Failed
observations retained, not relabelled as passes.

Serial AFTER-freeze source V2 gate1265total/1199PASS/66skip/0fail-error490.139s;
tracked clean before/after/exacttrue/deferred[]/outside[], four historical NaN
warnings retained. Matching packaged native/mock bindings4PASS1.717s separately;
Ruff1/mypy1/compile1/diffPASS. Post-gate frozen/native/source verifiers passed;
no new diagnostic EXE process remained. No physical RX was started this increment.

Diagnostic `APP07-PAIRPIPE-20261002-EA1F4E4` is NOT canonical/static/current/
promoted: EXE5fc44c4e/native8bf23fbf/source-contentfeadce90/snapshot-file73ce8cbd
belong toea1f, not subsequent documentation commits or historical85187/3075.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1/2048segments128MiB
remain unchanged. No physical dual-RF, visible GUI/DPI/DWM/FFT-LPS/50ms/lossless/
duty-Pd/sustained/soak/Linux acceptance; SDK-9 cause remains UNKNOWN.
APP07PARTIAL/fullAPP00–14 goalACTIVE/APP06Eplanned/APP05expired timer not restarted.
NEXT typed paired graph admission/delivery/recording transaction and paired
retuning Sweep, then dedicated UI implementation/review and matched EXE witness;
whole independent release review remains pending.

## 2026-10-02: qualified UTF-8 recording paths before paired product admission

Runtime exact `beb88946be4c02b3dcc8f3ec20f05421b6746dab` closes the explicitly
open Unicode recording-path qualification from ea1f. A new regression test
first reproduced UTF-8 filename corruption on Windows; native writers, recovery
scan, replay reader, offline I/Q reprocess and Python boundaries now preserve
UTF-8/native paths without an ANSI-code-page roundtrip. JSON artifact names
are UTF-8. Repeated bare `.part` suffixes normalize to the actual writer stem.

Paired aliases use SAME writer normalization/absolute weakly-canonical paths
and Windows `CompareStringOrdinal(TRUE)`, independent of CRT locale; failed
comparison refuses. Cyrillic/Latin case aliases, artifact/partial suffix aliases,
embedded NUL and malformed UTF-8 refuse BEFORE RF/files in tests. Unicode
case-equal paths conservatively refuse even in case-sensitive folders.
Reference: [Microsoft ordinal comparison](https://learn.microsoft.com/en-us/windows/win32/api/stringapiset/nf-stringapiset-comparestringordinal).
This is not exhaustive filesystem identity, Windows namespace, hard-link,
external-writer/TOCTOU or atomic group-ledger proof.

Native paired executable now9mock cases, including distinct Unicode directories
and both I/Q+spectrum writers/replay/reprocess with channel provenance. Candidate
full43CTest51.44s; official matching full43/43CTest51.57s/paired1.55s. Matching
packaged bindings5PASS2.530s BEFORE ONE serial fullV2 gate1265total/1199PASS/
66skip/0fail-error468.469s; clean before/after/exacttrue/deferred[]/outside[],
native unchanged and four historical NaN warnings retained. Ruff1/mypy1/
compile1/staged-diff PASS. Postgate658frozen/native/source535inputs PASS.
No physical RX/dual-RF or visible GUI acceptance this increment.

Diagnostic `APP07-UTFPATH-20261002-BEB8894` NOT canonical/static/current/promoted:
EXE678fb8e8/nativea8e87d41/sourceCONTENT9786793a/snapshotFILE35fbf5ab belong to
beb8894, NOT future doc-only commits or oldea1f/85187/3075/059 witnesses.
Qt6.11.1/schema5/factory2/DSP1/persistence1/Sweep1/geometry1/2048segments128MiB
unchanged. Default offscreen startup has no discovery/RX/native-engine creation;
selected9shared DLL paths/diskhashes match, NOT in-memory/exhaustive ABI proof.
DSP/Fs/FFT/gain/cadence/queues/budgets/quality/time/control semantics unchanged;
current static EXE/firewall/main dirty Legacy-DFL preserved.

APP07PARTIAL/fullAPP00–14 goalACTIVE/APP06Eplanned/APP05expiredtimer not restarted.
Actual product compiler/owner remains RX1-only and pair selector disabled.
NEXT typed fresh-topology RX1/RX2 group/capture job, SAME Live/native/control/
recording owner adapter and both producer deliveries, paired retuning Sweep;
then dedicated UI-agent development/design review and matched EXE witness.
Root-owned backend increment used NO new subagents/UI edits. Whole independent
release review, physical Ethernet/sustained/AT-DPI and SDK-9 cause remain OPEN.

## 2026-10-02: paired application Live foundation (partial)

Exact runtime `43c64f53f106b1d68bec547e436c8b715d32dacd` integrates typed
paired RTBW staging/publication/metrics into the SAME Native Live and application
owner. Stage is inert, Start explicit, both native producers retain distinct
source/receiver identities, and common stream counters are counted once.
Legacy single-frame/configuration/Sweep/recording paths refuse a staged pair
before their mutation; this is not yet paired recording or paired pane admission.
Both actual gain readbacks and common RF/epoch coherence are required before
Start; v1 has a common gain policy, not independently observed per-chain controls.
Group discontinuity clears both bounded caches. Stop uses the same native
cancel/join/close lifecycle; unconfirmed release bars another acquisition.

Corrected candidate60 focused tests passed7.698s. Matching diagnostic build
`APP07-PAIRLIVE-20261002-43C64F5` passed43/43CTest46.68s,658 frozen files and
537 source inputs; matching packaged native/mock9 tests passed6.609s BEFORE
the full source gate. Serial exact full V2 R1 passed1265total/1199PASS/66skip/
0fail-error524.497s, clean before/after and unchanged native, no outside product
imports/deferred tests. Post-gate frozen658/source537 verification passed.
Earlier3c build/source gate errors remain separate; the first43 gate was
interrupted without final JSON and had two subprocess-probe errors, cause
UNKNOWN. Those two probes passed separately; R1 success is not a causal fix.

EXE838becda/nativea8e87d41/sourceCONTENT6efcf4bd/snapshotFILE95c2a320 belong
to exact43 runtime, not documentation commits or prior Unicode witnesses.
The diagnostic build is NOT canonical/current/static/promoted. No physical
RX/dual-RF, visible GUI, DPI/DWM/speed/lossless/duty/soak acceptance this increment.
The actual pane compiler/owner remains RX1-only and pair selector disabled.
Typed per-endpoint graph IDs, common owner adapter/both deliveries, recording
group transactions and paired retuning Sweep remain OPEN before UI enablement.

## RTL-SDR extension and future APP-07 four-source layout (planned)

The user added RTL-SDR and explicitly delegated RTL integration to a backend
subagent, with the root orchestrating and a separate UI V2 design agent. Future
four-source tests assign pane1 Pluto, pane2 HackRF, pane3 tinySA, pane4 RTL-SDR.
The user's explicit follow-up makes RTL in pane4 mandatory in future APP-07
RTL hardware tests. If RTL has no admitted driver/runtime/owner, this acceptance
case stays OPEN; Empty, a duplicated source or a mock cannot substitute for RTL.
Historical three-source+Empty4 witnesses remain unchanged. Empty behavior still
has separate coverage. Each pane must retain a distinct actual owner/binding;
RTL is not a clone, HackRF alias or another receiver of the same stream.

The attached RTL USB composite is present, but both bulk interfaces currently
have Windows ProblemCode28 and no driver/service. Physical RTL RX is not
available; no driver/system/security/bias-tee mutation is authorized here.
The antenna input marking25–1700MHz is a user declaration, not an observed
tuner limit. The generic serial00000001 does not establish stable identity.
Initial integration targets optional native RTL acquisition/shared CPU-DSP and
RTBW only, explicit unsupported-Sweep refusal and uncalibrated dBFS/bin.
Runtime absence, identity/capability limits and SDK redistribution/linking review
must be visible, not silently bypassed or claimed qualified. Implementation and
four-source hardware acceptance remain OPEN. APP-07 remains PARTIAL.

## 2026-10-02: RTL RTBW foundation qualification (not hardware support)

Exact runtime `e80c6351e1c07d0c05eb1c1bd4599e9d3a039f7d` adds a distinct
RTL-SDR family, immutable RTBW intent and pure pane profile/compiler branch.
Initial requested profiles are 2.048/2.4 MS/s, FFT1024/2048/4096, half-FFT hop,
CPU Hann sample/peak and uncalibrated dBFS/bin. These are bounded application
choices, not observed tuner maxima, sustained sample rates or calibrated dBm.
RTL Sweep refuses; the conservative digital crop is not an analog passband.

The native standalone target owns an injected SDK-port abstraction, one RX
thread, bounded preallocated CU8 slots, in-place CI8 conversion, shared CPU DSP
and bounded reduced publication. Analytical FFT capacity is separate from the
small latest-wins presentation queue. Host input loss, unknown malformed-pair
cardinality and presentation supersession are distinct, not USB/RF loss metrics.
Exact rate/center readback precedes RX; auto-gain setter acknowledgment does not
establish actual tuner gain. A failed cancellation/join or ambiguous close
retains the owner and bars subsequent RTL Starts; no unsafe detach/reclose.
Synchronous vendor-call duration is not qualified by the mock join deadline.

UI V2 requires coherent protocol/complex-IQ capability facts and an exact
source+revision owner predicate. The predicate defaults false and remains
unwired. Runtime absence, stale selection and predicate failure cannot Stage
or Apply. AD Full Receive to RTL resets only the new row draft to Edge Trimmed,
without modifying a running owner or peer drafts. No actual RTL catalog entry,
Python native binding, official vendor port or application RTL owner is enabled.
The official SDK build option remains OFF (ON explicitly refuses); no SDK or
driver is bundled/installed. Generic-serial session admission remains OPEN.

New no-I/O mock compiler coverage proves distinct Pluto/HackRF/tinySA/RTL
resources, no Empty slot and RTL only in pane4. UI mocks likewise use row4.
This is NOT a four-device acquisition or visible-GUI proof. Pane4 is the future
APP-07 hardware-test assignment, not a global product restriction on RTL layout.
Historical three+Empty4 tests/witnesses remain unchanged and separately scoped.

Root combined52tests/123subtests passed14.40s; a new local-name mypy collision
was corrected without behavior changes, scoped5files passed and repeat34tests
passed1.13s. Matching full diagnostic build
`APP07-RTLBASE-20261002-E80C635` passed44/44CTest54.59s (assertion-active RTL
target2.84s), all658 frozen files/541 source inputs/shared9DLL/ONEUSB/default
fake+native offscreen/IIO/tinySA checks. Serial AFTER-freeze full V2 gate passed
1271total/1205PASS/66skip/0fail-error518.231s, exact/clean before-after, no outside
product imports or deferred compiled tests. Post-gate658/source541 verification
passed. EXE320319e6/nativea8e87d41/sourceCONTENT006adf54/snapshotFILE42fd0d26
belong to exacte80 runtime, never a later documentation commit. The existing
native Python artifact is unchanged; the new RTL target is not exposed there.

This diagnostic is NOT canonical/current/static/promoted. Physical RTL RX,
four-source live/Sweep, visible GUI/DPI/DWM, throughput/duty/lossless/soak and
SDK release qualification remain OPEN. Windows bulk interfaces have Problem28;
no driver/network/firewall/security/bias/firmware mutation or physical RX here.
Two delegated agents used gpt-6-sol/high: RTL integration and UI V2 design/code;
root owns integration review/build/provenance. Whole release review remains OPEN.
APP-07 PARTIAL, APP-06E planned, full APP-00–APP-14 goal ACTIVE.

## 2026-10-02: optional RTL RTBW vertical slice, runtime69f9a64 (PARTIAL acceptance)

Runtime `69f9a64f5e717a11938517216c67f2230cb6a145` adds the Windows
optional unbundled RTL ABI port, compiled coarse control/reduced Spectrum
binding, generic-serial SESSION route, actual common Live/pane owner and UI V2
Stage/readback integration. Default SDK flag remains OFF; explicit ON uses
separate CPU Release staging and a new tagged full package. No current/static
replacement, driver/system change, vendor DLL or external manifest bundling.

Future four-device APP-07 tests MUST use Pluto pane1/top-left, HackRF
pane2/top-right, tinySA pane3/bottom-left, RTL pane4/bottom-right. This is a
test assignment, not a product placement restriction. Missing actual RTL keeps
acceptance OPEN; Empty/mock/duplicate cannot substitute. Old three+Empty4
witnesses retain their original scope.

The runtime requires explicit absolute UTF-8 external DLL/dependency paths,
hash/import-closure admission and fresh exact-one descriptor/tuner/normal-mode
observation. Compiled availability alone never grants Stage/Start. Generic
serial00000001 is not stable calibration identity; matching replacement may
be indistinguishable. Antenna25–1700MHz marking is not observed tuner RF range.
Actual center/Fs readback precedes native RX. CPU Hann sample/peak profiles
2.048/2.4MS/s and FFT1024/2048/4096, half-hop, uncalibrated dBFS/bin are bounded
request choices, not measured throughput, analog passband or device maxima.

CU8 acquisition, conversion, FFT and bounded analytical queues remain native.
Only coarse control and reduced frames cross Python. RTL frame publication
requires exact producer/source/generation/current epoch-clock, center/Fs/FFT/
hop, numerical profile and 1D exactlyFFT-bin arrays. Malformed data refuses
before Live publication and requires explicit Stop. Value/optional SpectrumFrame
ownership replaces the failed shared-holder bridge without changing common
pybind ownership. Host loss, DSP loss and render supersession stay distinct.
Rates require an observed interval; hardware loss/RF duty/Pd remain unknown.

UI Stage is a no-I/O exact cached candidate check, including when default Pluto
is selected. Stage/Apply does not secretly Start. Pane actual Fs/center comes
only from an accepted current RUNNING activation; stopped/retained is unknown.
RTL Sweep, recording and analytical density/persistence are not implemented.
Ambiguous native cleanup pins the owner/module and bars unsafe reopening.

Focused qualification:45/45 native CTest51.16s, RTL31 methods4.629s, earlier134
combined methods9.818s, Ruff29/scopedmypy8/stageddiffPASS. Matching packaged mock
C-ABI bridge ALSO drives actual provider/service Stage/Start/spectrum/Stop:
1/1PASS0.360s. Four actual app graphs with fake controls prove RTLpane4 delivery,
failure/Stop isolation and independent peers; not actual four-device RF.

Full diagnostic `APP07-RTLBRIDGE-20261002-69F9A64`:45/45CTest50.75s,
all658frozen files/source550/shared9DLL/ONEUSB/defaultoffscreen/IIO/tinySA PASS.
EXE5d3a520b/native8e081bfb/sourceCONTENT114c1d3d/snapshotFILEb41ba30d belong to
runtime69f9a64, NEVER a later documentation commit. No vendor/mock RTL DLL or
external manifest is in the package. Post-gate source/658native verification PASS.

The ONE serial AFTER-freeze full V2 gate is NOT PASS:1279total/1212PASS/66skip/
1FAIL499.371s, exact/clean before-after, deferred[]/outside[]. Existing APP05
partial-Stop observer failed: source pass5 was open at GUI intent but completed
before coordinator Stop, so genuine terminal gap6 differed from requested5.
Benchmark/test files are unchanged from the foundation; causality to this
increment is UNKNOWN. One bounded identical standalone diagnostic PASS is NOT
a fix or a replacement for the failed full gate. No repeated full-suite retry,
guard weakening or APP05 expired-timer restart; observer qualification remainsOPEN.

This is a development increment/diagnostic package, NOT canonical/current/static/
promoted or whole-release acceptance. Actual RTL bulk interfaces still Windows
Problem28/no service/INF; actual external SDK absent. Four-device GUI, RTL RF,
shared RTL+HackRF libusb compatibility, DPI/DWM/LPS/throughput/lossless/duty/soak,
SDK linking/redistribution and whole independent release review remainOPEN.
Two dedicated subagents used gpt-6-sol/high: RTL integration and UI V2 design/
code/read-only review. Root retains integration/tests/RX/build/provenance/Git.
APP07PARTIAL/APP06Eplanned/fullAPP00–14 goalACTIVE.

## 2026-10-03: paired RTBW pane composition (runtime 7d89a34, partial)

Runtime `7d89a34a207163edd2c4786674bfb841a50ca9f9` connects two typed AD936x
RX endpoints to pane preparation through the existing paired Live/native
owner. Both chains share ONE device context, buffer, resource lease, common
tuning profile and control/recording transaction. It does not create two
device openers, introduce Python raw I/Q or duplicate native FFT processing.

Admission requires the currently selected device/session, stable canonical
identity and an observed compatible BOTH topology. Single-route empty-serial
admission is not transferred to the paired path. Paired Sweep, non-common
profiles and incomplete endpoint jobs refuse rather than implying independent
tuning. Explicit Start performs the common-profile transaction; Stop confirms
owner release before clearing paired staging. Rearm produces a fresh epoch.

Frames retain their actual distinct caller producer IDs and RX1/RX2 identity.
Immutable endpoint-to-producer admission is resolved only from the active
resource receipt. The operational device route remains available for captions
but cannot authorize or relabel a producer frame. Shared synchronization epoch,
first sample index and gap metadata travel with the analyzer bundle. Worker
preparation clears all same-resource caches at a new pair context; independent
resources remain untouched. Stale, regressive and foreign deliveries refuse.

An actual native-to-analyzer mismatch was reproduced: native exact zero power
is `-inf dB`, but the RTBW domain rejected both infinities. The shared contract
now preserves measured `-inf`, retains NaN as unavailable and still refuses
`+inf`. Waterfall peak reduction preserves a finite peak beside measured zero
and an all-zero bucket at `-inf`; it does not invent a finite floor, bridge NaN
or mutate analytical input. Native DSP, Fs/FFT, cadence, budgets, quality and
time contracts are unchanged. Finite-only spectrum drawing may omit a zero
ordinate; data preservation is not visible-GUI zero-power proof.

Qualification and provenance:

- Matching full diagnostic build `APP07-PAIRPANE-20261002-7D89A34` passed
  45/45 native CTest (51.04 s), all 658 frozen files and the 551-source snapshot,
  shared-runtime/one-libusb, default offscreen, IIO and tinySA checks.
- Actual compiled MOCK-IIO pair through the same Live, product graph/session
  and both prepared Spectrum/waterfall paths passed 8/8 matching-package tests
  (11.384 s). Focused candidate checks passed 58 tests, Ruff and scoped mypy.
  This is not physical dual-RF or a visible frozen GUI witness.
- The first exact full V2 gate at runtime 7d89 ran 1281 tests (530.501 s) and
  failed eight subcases of ONE obsolete APP04 scalar-reference method, with
  66 skips. It incorrectly treated measured `-inf` as a NaN gap. The failed
  log is retained; the reducer was not changed to satisfy that obsolete rule.
- Test-only `b4116e61ff220a815056fa1220a24a8849ba0841` corrects that independent
  scalar oracle while retaining integer-center assignment, physical edges,
  immutable-source and bounded-scratch checks. Nine focused tests passed.
  ONE subsequent serial exact full V2 gate passed 1281 total / 1215 passed /
  66 skipped / 0 failures or errors (501.374 s), with tracked-clean before/after,
  unchanged matching native and no deferred/outside-checkout modules.
  Scope: V2 source with packaged native, NOT frozen GUI.
- Post-gate 551-source and all-658/native verification passed. The EXE remains
  built from runtime 7d89, never relabelled as test-only b411 or a later doc
  commit: EXE SHA256
  `2e22fe75d87a668fc8a040b0dfe37d5060d406ad9c3c89b34b150731135be05c`.
  No current/static/canonical promotion, physical RX or system mutation.
  The earlier exact69 partial-Stop observer failure is historical; this PASS
  is not a causal repair claim for that observation.

The paired user selector remains DISABLED; the ordinary user-plan compiler
remains RX1-only. Still required: typed current-topology/common-tuning admission,
paired retuning Sweep and group recording lifecycle, plus Qt board-wide visible
Spectrum/persistence/waterfall reset BEFORE the first new synchronization epoch
and rejection of queued old-epoch packets. Worker cache reset is not a visible
history-reset or atomic GUI-pair proof.

Future physical APP-07 four-source 2x2 acceptance MUST use:

| Pane | Position | Actual source |
| --- | --- | --- |
| 1 | upper-left | Pluto / AD936x RX |
| 2 | upper-right | HackRF RX |
| 3 | lower-left | tinySA trace |
| 4 | lower-right | RTL RX |

This is a test assignment, not a product placement restriction. Use distinct
requested RF ranges and actual admitted capabilities/readback; preserve SDR
dBFS/bin versus tinySA dBm and report actual Fs/periods without lossless claims.
Selected Stop/restart must leave the other three streams, epochs and histories
intact. All four must publish current physical data. Missing actual RTL leaves
acceptance OPEN: Empty, mock, duplicate or old three-plus-Empty evidence cannot
substitute. RTL Sweep is not implemented and must not be silently emulated.

One reused dedicated UI agent used gpt-6-sol/high for scoped UI implementation
and read-only design/oracle review; root owns backend/integration/tests/build/
provenance/Git. No new RTL integration agent work in this increment. Whole
independent release review, physical four-source/dual-RF, genuine Ethernet,
Windows/DPI/DWM/AT, throughput/duty/lossless/sustained/soak and SDK qualification
remain OPEN. UI V2 only; APP07 PARTIAL, APP06E planned, full APP00-14 goal ACTIVE.
