# APP-07 resource-scoped Analyzer graph composition (partial)

## SAME-owner Start receiver admission repair (2026-10-09)

The current AD single-RX adapter now binds the actual native Start receipt's
`RX1` to an immutable `PaneCaptureAdmission.endpoint_receiver_ids` mapping.
Inert Stage still reports an unknown receiver; it does not infer hardware state
from the planned RX1 endpoint. The capture mapping must agree with the typed
endpoint, any known staged producer identity and the same analytical owner
scope. Frame admission compares against that confirmed capture binding.

An absent mapping retains the exact legacy expectation, including `None`;
unknown is not a wildcard and incoming frames never authorize themselves.
Actual RX2/noncanonical readback refuses on this RX1-only adapter; a partial
Start keeps the existing lease until explicit Stop. Rearm obtains fresh Start
readback. Source/session/epoch/generation/Fs/FFT/hop/crop/recording checks remain.
No native/DSP/queue/cadence changes, paired/Sweep redesign or second opener.

The original-composition regression first failed with zero pane deliveries,
then passed with one RX1 delivery and zero rejects. Nine new positive/negative
tests plus related owner/session/identity/rearm tests passed (68 tests).
These are source/mock tests, not proof of physical RF or visible UI operation.
Candidate packaging, matching full V2 qualification and the changed-path
hardware witness are separate gates. APP-07/M7/M8 remain PARTIAL; the separate
four-source independent-AD identity admission dependency is not bypassed.
Historical 095 hardware results and 096 red evidence below remain historical.

Qualification checkpoint: diagnostic package source1a671c4 passed 48 native
CTest cases and frozen inventory checks. The full V2 run at test-only descendant
bf07ec2 finished with 1490 passes, 66 skips and one failure: a G04 subprocess
access violation while the Python 3.13.1 asynchronous watchdog printed stacks.
That failed run remains failed; isolated success does not replace it.
The G04 test-only phase correction arms the same watchdog inside the actual
blocked callback, preserves the 80ms deadline/250ms block and all original
queue/progress/Stop assertions, and closes before callback return.
The old-interpreter crash persisted after this phase correction. An isolated
local Python3.13.11 experiment passed 21 related tests and the archived original
G04 test. This is diagnostic evidence, not a qualified runtime migration or
proof of the previous crash's cause. No hardware rerun, static EXE promotion,
firewall mutation, full-regression PASS or mandatory APP07 acceptance follows.

Current state: the final section documents the software-qualified explicit
USB/IP route editor; physical/performance/release paths remain open. Earlier "not yet present"
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


## 2026-10-03: paired visible UI V2 epoch guard (runtime 97d4f53, partial)

Runtime `97d4f53db5e1704964263903629c6ce1b5ee98db` clears BOTH paired panes'
visible Spectrum, persistence and waterfall histories before applying the first
accepted frame of a new shared synchronization context. A queued older packet
or late projection cannot restore the cleared peer. Independent physical
resources retain their data. This is sequential group invalidation, not atomic
paired painting or physical dual-RF evidence.

Presentation bindings now carry the explicit receiver-chain enum from the exact
AcquisitionGroup. Preparation requires paired metadata if and only if the group
is paired, and verifies the actual RX chain before cache mutation/allocation.
The board uses that same authority and retains a resource context floor across
value-equal RF-plan receipts. Older run/activation/session/acquisition/sync data,
metadata downgrade/injection and duplicate deliveries refuse before visual
mutation. RX1/RX2 captions come from typed selection rather than endpoint suffix;
instrument trace is identified by measurement mode, not a fabricated RX chain.

Qualification:

- The actual compiled MOCK-IIO -> same Live/product/resource/preparer -> actual
  offscreen Qt rearm test reproduced stale RX2 history BEFORE the fix and passed
  AFTER the correction. New focused Qt tests cover peer preservation, stale
  contexts and late Spectrum/persistence results. No physical RX was used.
- Full matching diagnostic build `APP07-PAIRUI-20261003-97D4F53` passed 45/45
  native CTest (55.36 s), all 658 frozen files, the 551-source snapshot, shared
  runtime/one-libusb, default offscreen, IIO and tinySA checks.
- Matching-package native/Qt composition passed 9/9 tests (6.543 s). ONE serial
  after-freeze exact full V2 gate passed 1288 total / 1222 passed / 66 skipped /
  zero failures or errors (473.005 s); tracked-clean before/after, unchanged
  matching native, no deferred or outside-checkout modules. Scope: V2 source
  with matching packaged native, not visible frozen GUI. Four historical NaN
  RuntimeWarnings were retained.
- Post-gate source/native/all-658 verification passed and no test EXE or mock RX
  remained running. EXE SHA256:
  `64b048c5fe1868db1b5859b1c42fc4e53bfadf1591c6a5d6e00562133a872419`.
  This EXE is built from runtime 97d4f53, never relabelled as later documentation.
  It is diagnostic, not current/static/canonical/promoted.
- Root's earlier candidate command had one nonexistent test-module ImportError
  alongside 78 passing tests; the target error is retained, not a product-fix
  claim. The correct RF shift/gesture modules separately passed 37 tests.
  Scoped Ruff, mypy, compilation and staged-diff checks passed.

The visible-history prerequisite is now implemented; paired user selection
remains DISABLED and the ordinary compiler RX1-only. Typed current-topology/
common-tuning user admission and impact preview, paired retuning Sweep/group
recording lifecycle and physical acceptance still remain. DSP/Fs/FFT/cadence,
queues/budgets, control/lease/recording, quality/time contracts are unchanged.

Future physical APP-07 2x2 MUST retain Pluto upper-left, HackRF upper-right,
tinySA lower-left and **RTL lower-right (pane 4)**. All four must deliver current
physical data with distinct requested ranges and actual readback; stopping or
restarting one must preserve the other streams/epochs/histories. This is a test
assignment, not a global placement restriction. Empty/mock/duplicate cannot
substitute for RTL; RTL Sweep remains unavailable, never silently emulated.

One reused dedicated UI agent used gpt-6-sol/high for scoped design/development
and final read-only committed-diff review; no new scoped defect found. Root
retains backend/native/integration/tests/build/provenance/Git. This is not whole
independent release approval. APP07 PARTIAL, APP06E planned, APP00-14 goal ACTIVE;
APP05 expired timer not restarted. Physical dual-RF/four-source, genuine Ethernet,
SDK first-cause, Windows/AT/DPI/DWM, throughput/duty/lossless/sustained/soak and
whole independent release review remain OPEN. UI V2 only; old UI, user changes,
current/static EXE and system/firewall/driver/firmware/security are untouched.


## 2026-10-03: paired user Stage/Apply/Start (runtime 050914e, partial)

Runtime `050914e8369414a62cce74419b1df656f52b3947` adds typed RX1/RX2
user-plan intent and fresh selected topology/session/identity receipts. Only
compatible common AD936x RTBW pairs compile: one graph/resource/group/capture
job and one native owner/context/buffer. Stage is selection-only; Apply checks
all paired receipts before configuration or leases; Start retains actual applied
configuration/readback guards. Lone RX2, paired Sweep, incompatible common
profiles/window, stale selection and missing stable identity explicitly refuse.
Per-pane crops and scheduling requests remain distinct; no implicit fallback.

A compiled MOCK regression reproduced old-plan rearm adopting a replacement
same-device session. Composition now captures the original Stage source-choice
object/revision/snapshot; the owner checks it before RF mutation on every Start.
RF preview remains inert; explicit Stop/Apply stays stopped until separate Start.
Both reduced producers then carry fresh native epochs through the same graph.

Qualification: candidate 90 tests passed (25.999 s); matching full tagged build
`APP07-PAIRSTAGE-20261003-050914E` passed 45/45 native CTest (53.83 s),
658 frozen files/551-source snapshot/shared runtimes/one-libusb/default offscreen/
IIO/tinySA checks. Matching packaged MOCK Stage/owner tests passed 14/14
(15.091 s). ONE serial after-freeze exact V2 gate passed 1296 total / 1230 passed /
66 skipped / no failures or errors (519.533 s); tracked-clean before/after,
exact provenance, unchanged native, no deferred/outside-checkout modules.
Four historical NaN warnings retained. Post-gate source/package checks passed.
This is source V2 with packaged native, not physical dual-RF or visible paired GUI.
Scoped Ruff/compile/diff and mypy seven changed files passed; broad mypy retains
nine errors in two unchanged imported files, not a whole-project mypy pass.

EXE SHA256 `af3b0059ed7442edb6989e57fff522db9c243e8f1c167e203623804abc9b7213`.
Diagnostic only, not current/static/canonical/promoted. Later documentation
commits never relabel this runtime build. Native DSP/Fs/FFT/cadence/queue/budget/
quality/time/recording policies unchanged. No physical RX in this increment.

Pair selector remains DISABLED. NEXT dedicated UI-only implementation/review:
typed pane-to-RX/common tuning preview, both affected histories, Start selected/
all group confirmation before enqueue (default Cancel), common RF-shift impact
and fixed localized refusal reasons. UI read-only integration review used reused
gpt-6-sol/high; root owns backend/integration/tests/build/provenance/Git. Not
whole-backend independent approval. Paired Sweep/group recording and physical
four-source Pluto1/HackRF2/tinySA3/RTL4 lower-right acceptance remain OPEN.
APP07 PARTIAL/APP06E planned/full APP00–14 goal ACTIVE/APP05 timer not restarted.
Main user changes and old UI are preserved; only this public contract mirrored.

## 2026-10-03: RTL WinUSB owned-handle guard (runtime da3df7b, partial)

Runtime `da3df7bdb1036d5d20783f57a123076f6e1dc5ec` corrects the RTL port's
post-open identity validation. On Windows, the SDK index-descriptor helper
attempted a second USB open while the primary WinUSB interface was occupied,
causing selected-device open to fail before acquisition. Both selected-session
and exact-serial routes now compare before-open descriptors with descriptors
read from the SAME owned handle. Count, identity-change, descriptor-read-error,
tuner/mode and cleanup refusal guards remain intact; no serial-only fallback.

A mock denied the second index open and reproduced the original failure:
44/45 native tests passed, one official-port test terminated with an exception.
The correction passed 45/45 native tests and 27 matching packaged RTL
binding/pure/product/UI tests (1.396 s). The full tagged build
`APP07-RTLWINUSB-20261003-DA3DF7B` passed its native/frozen/shared-runtime/
source/offscreen checks. ONE serial after-freeze V2 gate passed 1296 total /
1230 passed / 66 skipped / no failures or errors (493.337 s), exact provenance,
tracked-clean before/after, unchanged native and no outside-checkout modules.
Four historical NaN warnings remain. Post-gate base 658-file and 551-source
snapshot checks passed. This gate is source V2 with packaged native, not GUI RX.

The human explicitly authorized RTL driver and library installation, overriding
the earlier no-driver-mutation restriction ONLY for RTL setup. The selected RTL
interface now uses WinUSB with Windows status OK/problem code 0. Other device
drivers, firmware/EEPROM, TX/bias, firewall/security and system PATH were not
changed. Official RTL-SDR Blog V1.4.0 x64 is provisioned locally with hash
admission, not committed or included in the pristine base package. Its
redistribution/linking review remains OPEN.

A separate LOCAL diagnostic package preserves the pristine base and records
its additional runtime in a separate provision record and regenerated exact
662-file manifest. Matching native plus the real SDK passed bounded physical
100 MHz / 2.4 MS/s / FFT4096 RX: 286 delivered snapshots, 3519 computed FFT,
zero host-input drops/worker failures, quality flag 8209 and 2371 presentation
supersessions retained. Stop joined reader/DSP and closed successfully.
The real application RTL service separately passed inert Stage, explicit Start,
95 distinct snapshots with actual center/Fs and numerical provenance, then
Stop/release with no first fault or quarantine. SDK initial direct-sampling/PLL
messages remain recorded; these are not claims of clean RF, lossless transport,
display FPS/LPS, sustained reception, GUI interaction or four-source acceptance.

EXE SHA256 `c6c18d51508f27359ac23e5f7629c0e98f06d9b3865f0bf01c41c276cfd9517d`;
native SHA256 `5fa47cd1685db15fbfd40c9b03fe531e2775c2a9c871a9d1637bb790109e78e8`.
The LOCAL add-on does not relabel the pristine base qualification or older
candidate physical proof. Neither package is current/static/canonical/promoted.
RTL still supports the bounded RTBW profiles, CPU/Hann/half-hop and automatic
gain; RTL Sweep, bias control, raw-IQ recording and absolute-power calibration
are not enabled by driver setup. DSP/Fs/FFT/cadence/queue/budget/quality/time
contracts and the disabled paired AD selector are unchanged.

NEXT remains dedicated UI-only paired selection/impact implementation and review,
paired Sweep/group recording, and physical four-source 2x2 with Pluto1, HackRF2,
tinySA3 and RTL4 lower-right. No new subagents/models were used for RTL setup or
this native fix; earlier gpt-6-sol/high UI review is separate, not whole-backend
approval. APP07 PARTIAL/APP06E planned/full APP00–14 goal ACTIVE; APP05 timer
not restarted. All bounded RX sessions stopped; no installer/test EXE left open.

## 2026-10-03: full APP-07 orchestrator plan accepted

The human adopted all 33 sections of the long-running orchestration specification,
APP07-A…L and milestones M0…M12. Compact local `docs/codex/` now contains bootstrap,
invariants, current state/work, roadmap/acceptance, HIL/performance plans, task/agent
contracts, ADR, session summaries and evidence index. Private conversation/state/
evidence remain ignored, not published here; export separately before moving the
workspace, because a fresh Git clone does not restore these private files.

M0 read-only rehydration completed: Git/source/artifact/existing evidence reconciliation,
two scoped audits and independent plan review. Seven JSON-syntax YAML files and
39 mandatory criteria checked. No feature/native/UI edits, new build/test-suite,
SDK/SDR access, system mutation or static promotion. Runtime remains da3df7b;
this later documentation cannot relabel its source/native/EXE/physical evidence.

Blocks: A resources; B paired user assignment; C lifecycle/epoch; D paired retuning
Sweep; E RTL production; F physical four-source 2x2; G fault isolation; H performance;
I long-run; J visible Windows UI; K release; L closure. Source/mock/physical/release
scopes remain distinct. Fixed-band paired lines are NOT paired retuning Sweep;
local RTL DLL and short RX are NOT production/soak/HIL. PASS needs indexed evidence;
relevant implementation changes invalidate previous PASS to VERIFY.

Bounded Native/UI/Sweep/HIL/QA/Performance/Review/Release delegation is accepted for
APP-07; root retains contracts/integration/acceptance. Source writers use isolated
worktrees/disjoint allowlists; reviewer differs from author. Eight child roles run
in waves with root plus at most three active children in this environment. Hardware,
exact build/gates and performance are serialized; one HIL owner, real lock before
parallel hardware-capable agents. YAML lease alone is not interprocess enforcement.
No new TX/bias/firmware/driver/firewall/security authority follows from this plan.
Dispatch configured two gpt-6-luna/high audits and one gpt-6-sol/high reviewer;
runtime model attestation is not exposed by the collaboration tools.

NEXT: M1 shared resource/refusal contract freeze; M2 paired user UI; M3 paired Sweep;
M4 RTL production; M5 harness; M6 physical Pluto1/HackRF2/tinySA3/RTL4; M7 faults;
M8 measured baseline/performance; M9 stability/soak; M10 visible UI; M11 release;
M12 all mandatory PASS with evidence/root closure. APP07 remains PARTIAL. Full
APP00–14 objective and expired APP05 timer unchanged. Fresh goal API returned paused,
not ACTIVE; this plan adoption did not create or change a goal. Main dirty work and
current/static EXE remain preserved.

## 2026-10-03: M1 typed paired admission refusals — source-only increment

Product source `1e72f9bb5b3f84fc96309c71cef76dcb8bf6684d` adds the domain
`PaneUserRefusal` contract and propagates typed reasons through user-plan Stage
and the all-receipt before-RF Apply preflight. Initial missing stable identity,
unavailable dual topology, changed selection/session, unsupported paired assignment
or mode, common profile conflict and common window conflict are distinguishable.
Existing guards and explicit paired Sweep refusal remain; no time slicing/fallback,
second opener, hidden Start/restart, lower Fs/FFT or altered RF/DSP/budget policy.

Only known typed plan/Stage errors propagate their reason. Unexpected SDK/graph
operation failures keep fixed generic text; no string parsing or driver-path leak.
Cleanup failure remains the actionable CLEANUP_REQUIRED with retained pool and
separate original failed_reason; existing revisit estimates remain available.
Legacy exception constructors and the current generic visible messages remain
compatible. The paired selector is STILL DISABLED; localized reason presentation,
typed RX preview and group Start confirmation belong to the dedicated UI V2 packet.

The frozen six-file candidate passed 86 focused pure/offscreen tests (5.781 s),
64 broader source-isolation/RF/geometry/tinySA tests (40.956 s), and five compiled
native MOCK-IIO Stage cases (7.946 s) using the unchanged da3df7b native artifact.
Ruff/compile on six files and staged-diff check passed. Scoped mypy on three touched
modules passed with follow-imports=silent. Initial full-follow mypy found 11 errors:
two introduced narrowing errors were fixed; nine in unchanged dependencies remain.
This is NOT whole-tree mypy success. Deliberate fault-test messages are retained.

Independent gpt-6-sol/high read-only review checked immutable patch SHA256
`15dab1fc058e8b4b3356301821526bc93d9d40a852687ad4b888d521fd9c43b2` and found
no blocking issue in scope; it did not rerun tests or approve the whole release.
Impact of the shared control boundary is high, protection partial. Root integrated
a source progress commit, not an automatic production merge/promotion. No new build,
full regression, physical RX, visible GUI, four-source HIL, performance, soak or Linux
qualification occurred. Existing EXE/native/physical witnesses remain da3df7b;
neither this source commit nor later docs can relabel those artifacts.

M0 is complete; M1 remains IN_PROGRESS with the refusal subpacket complete and
paired retuning Sweep step/epoch/arbitration contract freeze still next. All final
APP07-A…L acceptance guards remain. APP07 PARTIAL/full APP00–14 goal freshly
confirmed ACTIVE; expired APP05 timer not restarted. Main dirty work and static EXE
unchanged. Only the public contract is mirrored/published; private state/evidence
remain local. Configured model IDs are recorded, runtime attestation unavailable.

## 2026-10-03: M1 exact common paired Sweep contract freeze — source7fff9b9

Product source `7fff9b96e386d9502fbf45da72d773664bc31655` centralizes bounded
common step geometry in `domain/continuous_sweep_geometry.py` and reuses it in the
existing native single Sweep factory. Segment count/usable windows/overlap, RF Fs,
FFT, speed, cadence and preflight budget policy are preserved. Finite huge draft
midpoint computation avoids overflow; native RF bounds still gate real hardware.

`domain/paired_sweep.py` freezes typed low-rate selection/stopped prepared-Apply
and active-step admission. Receipt includes exact resource/session, actual Sweep/
acquisition/synchronization epochs, line/step/generation, requested usable bounds,
actual center/Fs/RF bandwidth/FFT, and immutable common plan/profile/revision.
Both typed RX observations must align in sample/time/clock/gap provenance and exact
producer assignment; per-chain quality masks stay distinct. Actual coverage must
contain the declared step. Unknown RF timestamp/domain stays unknown, not host time.
Stop/plan-change owner invalidation and fresh observed epoch remain implementation
obligations, not RF authority granted by constructing a Python value.

Independent review found an R0 stale-plan defect: changed later stop/overlap could
retain step0 and reuse old output. R1 exact whole intent binding and regressions
close it. Final focused source/offscreen:67total/58PASS/9skip/0fail-error1.348s;
explicit unchanged-native MOCK14/14PASS14.270s; Ruff5/compile5/scopedmypy3silent/
stageddiffPASS. No full regression/new native/new EXE or physical RX was performed.
Two old presenter tests still FAIL (duplicate failed-Stop signal; poll-fault stop
barrier). Both reproduce with baseline12eab0d factory and unchanged dependencies;
cause remains OPEN separate UI/QA debt, not hidden or dismissed as fixed.

Read-only Luna native design audit and independent Sol R0/R1 review completed.
Reviewed R1 patch SHA256 `0c5598da81abc7d8214a54f7d738bc243131eeac06e136cba6b92ef73cbb9a59`.
No blocking R1 scalar-contract finding; shared-path impact high, protection partial,
source-progress acceptance only, NOT auto-merge/whole release approval. Configured
IDs gpt-6-luna/high and gpt-6-sol/high; runtime attestation unavailable.

M1 contract/source freeze COMPLETE. This does NOT implement paired retuning Sweep,
enable a paired selector or close APP07-D01..D07. Next M2 dedicated UI V2 workflow,
then M3 same-owner native paired coordinator: analytical pair input BEFORE render
LatestWins reduction, separate native assemblers, one tuner/context/buffer/lease,
aligned Gap on one-sided loss, aggregate non-doubled budgets and bounded Stop/flush.
UI agent does not open SDR; source writers use isolated worktrees, independent
review, root integration and serial exact build/HIL. M4 RTL production and M5–M12
physical4-source/isolation/performance/soak/visual/release/closure remain open.

APP07 PARTIAL/full APP00–14 ACTIVE; expired APP05 timer not restarted. Historical
da3df7b native/EXE/physical proofs remain that source, never this later source/doc.
Current/static EXE unchanged, main dirty LegacyDFL preserved/no product sync.
Only public contract mirrored/published; private state/evidence remain local.

## 2026-10-03: M2 paired fixed-RTBW UI V2 workflow — source95875f2

Source `95875f2374b5bc061ca40e8d8a25c6cc46b3a6fa` enables typed RX1/RX2
assignment beside Source in the existing seven-column V2 pane editor. Observed
digital scan-pair count is only advisory: unknown/single topology disables RX2;
unsupported retained intent remains explicit/correctable, not silently changed.
Fresh Stage and all-receipt before-RF Apply remain authoritative. Chip names do
not prove a second RF path. Explicit Empty clears its draft; locale/passive
refresh preserve typed intent. There is no per-pane BOTH or paired Sweep fallback.

Preview shows typed pane-to-chain assignments, one shared capture/common requested
LO/Fs/filter/gain/FFT/hop/window/averaging, shared history impact and unchanged
independent peers. Requested values are not readback; actual values remain unknown.
All21 typed refusal codes have bounded EN/RU text; cleanup has actionable priority
and retains the original refusal. No raw SDK string parsing or rich-text rendering.

Initial paired Start is explicit and defaults to Cancel. Start All obtains paired
impact confirmation BEFORE any resource enqueue; Cancel leaves pair and peers
unchanged. Postmodal terminal/RF/startable-set guards bar stale authorization.
Single shared RX is not mislabeled dual pair. Stopped Apply still arms only; native
ownership/RF/DSP/Fs/FFT/cadence/quality/epoch/gap/budgets/recording policy unchanged.

Dedicated gpt-6-sol/high UI writer and distinct independent Sol R0/R1 reviewer
completed; root verified exact six-file R1 patch SHA256
`73dfee7236e4a2f689759c832b765cad9a6060ccd42ea29e2c814c5f1ada126d`.
R0 minor RU unit debt was corrected and both locales/unknown tested. Final root
integrated119 tests PASS33.121s; Ruff6/compile6/scopedmypy3silent/diff PASS.
Initial mypy shadow errors and incomplete RTL mock plan were corrected; product
required groups contract remains strict. Not whole-tree mypy/full-regression PASS.

Old da3 native standalone Stage attempt failed5 during DLL load BEFORE Stage/RX.
An explicit hash-admitted prior3DLL preload diagnostic then passed unchanged5case
bodies7.554s with Mock IIO ONLY. Original failure and isolated5skip are retained:
this is not a vanilla suite, product-loader, new native or package fix.

Separate QA-only source`adfea0c2ee005ea9a42a06d6349456cd51b72c8b` corrects baseline
presenter tests: shutdown retry is a second Stop attempt, not a duplicate error;
Qt completion must be pumped instead of blocking the GUI test thread. Root34PASS.
Separate existing close-failure/shut-down-executor cleanup defect was reproduced
on MOCK and remains OPEN; no production lifecycle fix is claimed.

M0/M1 complete; M2 source workflow complete but matching EXE/visible witness OPEN.
Next qualify exact harness/build/source gate and matching V2 workflow, then M3 native
paired retuning; M4 RTL production and M5..M12 HIL/fault/performance/soak/visual/
release/closure remain OPEN. No new EXE/build/fullgate/physicalRX/dualRF/four-source/
visible Windows/DPI/performance/soak/Linux/release acceptance or static promotion.
APP07 PARTIAL/full APP00..14 goal ACTIVE; APP05 timer not restarted. Main dirty work
preserved/no product sync. Private state/raw evidence local; public summary only.
Configured model IDs recorded; runtime model attestation unavailable.

## M2 matching source/package qualification — 2026-10-03, source ff02c7d

Test-only source d31f5dd07ce2c9a45ad47285331d7fbfd06bc7f8 registers only the
explicit native test module parent directory for the full mock case/cleanup.
No PATH mutation, SDK preloads, production-loader or system changes. Three paired
native/mock test entrypoints retain exact module identity and explicit Mock IIO.

First d31 matching build passed, but exact full V2 gate failed one legacy deadline
localization assertion: 1319total/1252PASS/66skip/1FAIL. This failure and package
remain historical, not silently relabeled. Dedicated UI agent corrected the
canonical typed EN/RU refusal in ff02c7df501e66ed019a027056b8a498b93fd33e:
modeled-not-RF, actionable target/weights/ranges, explicit noApply/RX assurance.
Unused legacy key removed; actual beforeRF/allpane tests strengthened. Independent
review found no blocker; root34PASS and reviewed-patch equality verified.

Full matching no-skip tagged CPU HF+RTL build at ff02:45/45CTest51.59s,554source,
658frozen files/defaultoffscreen/IIO/tinySA/shared9DLL verification PASS.
One serial exact full V2 gate AFTER freeze:1320total/1254PASS/66skip/0failure-error,
521.492s; cleanbeforeafter/exacttrue/deferred[]/outside[]. Historical four NaN
warnings retained. Matching packaged native/mock/helper/admission22PASS19.099s.
Postgate source snapshot and frozen manifest verified unchanged.

Tag APP07-PAIRUI-20261003-FF02C7D is a diagnostic candidate, NOT static/current or
promoted release. EXE SHA256cc741be7602dc97b349a54773756cb9c546781ab1aba90dcc81b2bae9c76cdb2;
native5fa47cd1685db15fbfd40c9b03fe531e2775c2a9c871a9d1637bb790109e78e8;
sourceCONTENT83f0305613257b1870098a79ccf40a293b527d54e077c86f30aa43dbe13049b4.
Native C++ bytes unchanged; matching incremental pipeline/manifest binds ff02,
not a new DSP change or relabel of historical da3/d31 physical evidence.

M2 source/build/fullgate complete; visible Windows typed-workflow witness remains
OPEN. APP07 PARTIAL; paired Sweep still refuses until M3. R14 cleanup retry defect
remains separate OPEN. No new physical RX/dualRF/HIL/DPI/DWM/performance/soak/Linux/
release acceptance; current static EXE/firewall and main dirty LegacyDFL preserved.
Root owns backend/build; configured gpt-6-sol/high UI author and distinct reviewer.

## M2 visible single-RX witness and receiver affordance — 2026-10-03

The immutable FF02C7D diagnostic EXE was exercised on real Windows with a
physical USB AD936x reporting only digital RX1. Stage/Apply did not start RX;
two explicit Start/Stop cycles produced changing spectrum and waterfall, followed
by normal Close and verified process absence. The unavailable RX2 popup choice
did not change RX1. This is SINGLE-RX evidence, not positive paired-RX acceptance.
Post-close the same554 source inputs and658 frozen files verified unchanged.
Native analytical FFT and publication counters are not Qt/DWM paint FPS, RF duty,
continuous transport, or a four-source performance baseline.

A dedicated UI writer and distinct read-only reviewer, both configured
gpt-6-sol/high, then prepared and reviewed a bounded three-file source correction:
RX2 has the explicit localized `RX2 · unavailable` / `RX2 · недоступен` label
whenever the existing topology guard disables it. Typed item data, selected RX2
intent, refusal policy, tooltip/accessibility reason, and explicit Start semantics
are preserved. The popup expands for the label; the compact closed selector may
elide its suffix and still needs a matching EN/RU Windows readability witness.
Root verified byte-identical reviewed/integrated patches,40 focused offscreen
tests PASS6.597s, Ruff3/compile3/diff PASS. An initial guessed nonexistent test
module caused an import error; the corrected real module suite passed. That
diagnostic is retained and is not a production defect.

No native/backend/RF/epoch/quality/budget change. The annotation source is NOT
contained in the prior FF02C7D EXE: its matching new build/regression/visible
witness remains OPEN. Dense Preview and visible requested-versus-actual readback
are separate UX debt. M2 remains IN_PROGRESS, APP07 PARTIAL; M3 paired native
retuning and M4 RTL production precede four-source HIL/fault/performance/soak/
visual/release closure. R14 cleanup retry defect remains OPEN. No physical dualRF,
DPI/DWM/50ms/lossless/soak/Linux or release acceptance, static promotion, main
product sync, firewall/security/system change is claimed.


## M3 native paired analytical input prerequisite — 2026-10-03

The SAME FixedBandEngine paired DSP worker now supports one native-only bounded
PairedSpectrumAnalyticalSink. Every validated RX1/RX2 pair reaches it AFTER the
existing per-chain consumers and BEFORE cadence selection/LatestWins rendering.
No second opener/context/stream, extra analytical queue, raw-IQ or per-FFT Python
callback. begin carries the actual owner AppliedConfig; shared gaps invalidate
before a new pair; terminal partial batches use the same sink and one noexcept
finish. A sink exception terminates the common owner, not an independent peer.
The immutable conservative retained-payload reservation is counted ONCE in the
existing shared Sweep128MiB / whole512MiB envelope before device probe/RF.
This is a trusted internal C++ extension, not arbitrary plugin time enforcement,
RSS qualification, or permission to block acquisition on UI/I/O.

R0 independent review found a real first-cause defect: a secondary gap-cleanup
exception could replace the initial consume/backend failure. The new compound
owner regression FAILED before correction (2.25s, total2.31s), retained.
R1 captures/rethrows the first push/flush exception through best-effort cleanup
and avoids a second gap callback when the epoch was already invalidated.
Three publisher regressions plus the compound owner case protect that path.
Distinct configuredgpt-6-sol/high reviewer found the P1 source-closed/no new
blocker in the immutable five-file R1 patch
e05ccf82ff3dd779d49765d2935dea82a1784636dac5ec089f12538ba18fb9ba.

Matching all-target Windows CPU HF+RTL native build PASS; full45/45CTest56.65s
(paired owner15cases2.39s); serial explicit-new-module binding/application/
Sweep-contract28PASS13.317s, zero skips. Native SHA256
7ab4f78ca1e287cb0f1fdeac61d1caf35079bf5266396132e5bf72425c55a4ba.
R0 results remain separate; historical getenv/CRLF warnings not suppressed.
No new EXE/fullV2/frozen-package/physicalRX/dualRF/GUI/DPI/throughput/soak/Linux/
release acceptance. Prior FF02 EXE unchanged and cannot be relabeled as this source.

M3 is IN_PROGRESS, NOT complete: next implement paired coordinator/dual assemblers,
one shared LO step, typed actual step/source/epoch receipt, common mode lease,
whole-plan aggregate budget and cancellation/recording controls BEFORE product
paired Sweep admission. The existing refusal remains. M2 positive paired/readback
and annotation matching ENRU visible witness, R14 lifecycle fault, M4 RTL and
M5..M12 qualification remain OPEN. UI V2 ONLY/main dirty work/static firewall
preserved; full APP00..14 goal ACTIVE, APP05 expiredtimer not restarted.
## M3 native paired Sweep coordinator — 2026-10-03

ContinuousSweepCoordinator now has an explicit paired native configuration.
ONE FixedBandEngine/context/buffer performs each common retune; a native
analytical sink, before render reduction, feeds separate RX1/RX2 assemblers.
The bounded output publishes complete/gapped pairs and progressive paired
previews with distinct source data and actual step generation, synchronization
epoch, sample index/time, center, Fs, filter and FFT. Common-plan/profile
mismatches and the combined reduced-memory budget refuse before RF. The
existing shared 128MiB Sweep / 512MiB total limits are not doubled.

One-window capture remains continuous, with no per-line retune; reduced-line
host cadence stays separate from calculated FFT and render cadence. Paired
rearm allocates a fresh Sweep epoch and clears old results. Common IQ counters
are counted once; analytical RX1 and RX2 counters are separate. This native
step DTO is NOT yet the full owner-bound session/acquisition-clock/intent receipt.

Independent R0 review found three P1 defects: Critical worker causes were
masked, Stop could race between the final flag check and Start, and one-sided
final-step failure could lose the paired terminal gap. R1 joins the failed
owner before reading the earliest Error/Critical, uses a native one-shot Start
admission gate, validates BOTH spectra before assembly, and commits receipts
after both admissions. Exceptional asymmetric assembly invalidates both lines
as gaps; already-emitted failure gaps are not duplicated by cleanup.
A pre-claim Stop prevents Start. An already-claimed control operation may be
in flight; this does NOT promise zero RF writes after a Stop request.
Ordinary unguarded Start stays unchanged; native test/gate APIs are not Python controls.

Matching all-target Windows CPU HF+RTL build passed. Full46/46CTest55.18s,
new coordinator target1.62s; serial explicit-current-module regression of
28 existing binding/application/Sweep contracts passed12.444s with zero skips.
Native SHA25624637297ccb6bc6afa25eff919bec2066626e5314ee551262703ec6170ba6a4a.
The distinct configured gpt-6-sol/high reviewer source-closed all three findings
in immutable seven-file R1 patch
7c043a092112e9d390d7c6f06e5cb083656e525a64ac1a3ae73a48933d523c47.
Read-only QA used configured gpt-6-luna/high. Runtime model attestation unavailable.
The reproduced Critical failure and intermediate duplicate-gap failure remain
retained; green final results do not erase them.

This is bounded native source progress, NOT enabled product paired Sweep.
Reduced Python bindings, full owner receipts, SAME Live/control/recording owner
and exclusive mode lease, paired statistics, adversarial integration and
physical qualification remain M3 gates. No UI guard removed, no new EXE/fullV2/
frozen-package/physicalRX/dualRF/GUI/throughput/DPI/soak/Linux/release proof.
Prior FF02 EXE and static firewall path remain unchanged. M2 positive paired/
matching ENRU/readback, R14 cleanup, M4 RTL and M5..M12 remain OPEN.

## M3 paired reduced Sweep Python boundary — 2026-10-03

The existing native coordinator now exposes validated immutable paired config,
read-only paired terminal/progressive envelopes and actual native step receipts.
The reduced protocol has its own explicit version1 marker, NOT a claim of
product mode/lease/receipt admission. Existing single APIs retain their meaning:
single line/progress reads refuse paired mode; scalar discard supports bounded
paired drain. Python sees per-RX SourceDescriptor metadata and reduced arrays,
never raw I/Q, analytical callbacks, StartAdmissionGate or native test controls.
Array capsules retain native shared storage after parent/coordinator release;
writeability cannot be re-enabled. Common IQ counters remain once, RX1 analytical
FFT counter and secondary RX2 analytical counter are separately available.

Matching alltarget WindowsCPU build passed. Serial33/33 tests15.850s0skip include
five new compiled-boundary cases and28 existing binding/application/commonSweep
contracts. Native full46/46CTest53.36s, pairedtarget1.30s. New cases cover invalid
common plans/epoch/cadence/queue/timeout/statistics, immutable config/receipts,
explicit Start, one mock context/buffer/commonLO, distinct RX data/readback,
progress before terminal, Stop prefix gap, first error cause, fresh rearm epochs,
legacy reader refusal and reduced-array lifetime. Tests are mock ONLY, not RF
accuracy/transport throughput/FFT-LPS/GUI/soak evidence. Initial fixture/wire-case
errors were corrected; their failed logs remain retained, not product bug claims.

The native module hash is
8771c8fe748c34322c56cc2ebadcc11b0535f5bff678f4ea39c56be04e76187b.
Full product integration remains mandatory: both native statistics consumers,
SAME NativeLive control/recording owner and exclusive RTBW/Sweep lease, full
owner-bound session/selection/plan/readback/epoch receipts, paired pane delivery,
actual application regression and physical/visible qualification. Current product
pairedSweep refusal is NOT lifted. A native step receipt is not a fabricated
acquisition/RF-clock receipt. No new EXE/fullV2/frozenpackage/physicalRX/GUI/DPI/
performance/soak/Linux/release proof; FF02 EXE and staticfirewall path unchanged.

## M3 paired native Sweep statistics — 2026-10-03

The native paired coordinator now owns two independently optional per-RX
SweepStatisticsPublishers. Both consume each admitted common-step progress or
terminal pass before preview throttling and latest-wins publication. Multi-step
Sweep still admits one analytical pair per common LO step, not every repeated
FFT at that step. Single-window statistics consume every admitted pair before
the configured host line cadence; snapshot cadence controls refresh cost only.
Partial revisions replace the same pass. Missing NaN bins contribute no power
or histogram/density observations. Terminal gaps force a fresh snapshot.

Both kernels and retained reduced snapshots are counted once in the existing
shared128MiB component/512MiB whole-owner admission before allocation/RF. Each
chain's own payload limit also applies; independently fitting chains can refuse
when their sum exceeds the shared cap. Paired configure does not allocate the
ordinary third RX1 publisher. This is payload admission, not an allocator/RSS/
transient-peak or unlimited caller-retention guarantee.

Shared synchronization gaps preserve the measured terminal prefix and reset both
rolling histories before fresh paired data. Rearm creates fresh Sweep epoch and
kernels; retained snapshots remain immutable. completed_lines counts constructed
analytical passes, not queue pushes or paint FPS. The separate
line_cadence_snapshots_suppressed counter reports unpublished statistical passes,
not native input/FFT loss or queue eviction. Compiled statistics protocol version1
is a native boundary marker, not product lease/receipt admission. The native-only
DSP-delay fault-injection seam is not exposed to Python.

Matching WindowsCPU HFRTL build and final46/46CTest56.69s plus serial37/37 binding/
application tests28.913s0skip passed. Four new compiled cases cover per-RX data,
cadence/coalescing, optional RX2-only settings, progressive/terminal replacement,
read-only nested arrays and lifetime after owner release. Native tests additionally
cover combined-budget refusal, one-sided invalid FFT prefix and shared-gap reset.
Independent source review found a weak reset assertion; it was strengthened.
A deliberate mock reset-omission mutation failed the intended assertion, then
reset was restored and the exact final gates passed. This is mock regression
protection, NOT an observed hardware fault or physical dual-RX proof.

Native module SHA256:
5f2f7e97a90623c468f5c3d497ca5d47f769da5b7f04149f906980d5a053df74.
Immutable reviewed five-file patch SHA256:
460b797aea96958feb00947f6fe7f438763d72074c33e33669d76840ba35f01b.
Review/QA agents were configured gpt-6-sol/high and gpt-6-luna/high; runtime model
attestation unavailable. Root implemented, built and verified the exact candidate.

Product pairedSweep refusal remains in place pending SAME NativeLive control/
recording owner, exclusive modelease and full owner-bound observed receipts.
Sequential exceptional allocations are not an atomic persistent group ledger.
No new EXE/fullV2/frozen-package/physicalRX/GUI/performance/soak/Linux/release
qualification; FF02 EXE/staticfirewall path unchanged. APP07/fullAPP00..14 OPEN.

## M3 SAME NativeLive ownership authority prerequisite

Sweep reservations now carry an individual opaque owner token. An expired lease
cannot authorize a newer reservation; repeated old release is inert. Actual
NativeLive still supplies the recorder/control transaction and selected route.
Arm/Start recording and Live Apply/Stop/Close/shutdown refuse before changing state
while Sweep owns it; callers must close Sweep first, not invalidate its route.
The token also fences the SAME selected session, URI, device and immutable
applied-profile objects. A changed owner snapshot refuses before construction.

The continuous plan factory binds its constructed coordinator/display owner to
that same reservation. It refuses stale construction and a second constructor
even through another factory holding the same lease. Lease release first closes
the registered owner. Failed cleanup retains owner and lease, bars new Start,
and allows explicit cleanup retry. Start uses the same low-rate control lock;
acquisition/DSP/polling remain outside it. Reentrant construction/release cannot
retire a pending owner. Failed native construction relies on the native
constructor's RAII contract; it does not fabricate a cleanup receipt or retry.

Standalone/fake leases retain optional callbacks for compatibility, not actual
product ownership authority. Direct native evidence callers must still use the
factory control transaction for Start. Sequential Sweep adapter lifecycle is
separate; this prerequisite does not certify every external native caller.
Paired product admission, selection/session/epoch/full per-step receipts and the
actual paired pane CaptureJob bridge remain OPEN. No UI enablement, matched EXE,
physical multi-source, performance or release qualification follows from this
ownership prerequisite.

## M3 paired application lease and native plan admission

An internal paired Sweep factory now obtains authority through the actual
Live application graph. Admission reads the selected source/revision inside
the SAME native recording/control transaction, pins the selected choice by
object identity, and reserves an individual application claim. The native
lease still fences its own stopped session/route/device/applied profile.
Every factory control/constructor action rechecks both boundaries. A caller's
request revision is not accepted as the current application's revision.

Both chain plans preserve the explicit producer IDs and typed RX1/RX2 selection.
They use the same requested Sweep geometry, physical Fs/FFT/filter/gain policy,
buffer and cadence; native combined configuration validates BOTH reduced and
statistics payloads against ONE aggregate budget before any context opens.
Paired reduced/statistics protocol v1 is mandatory; no single-producer display
fallback is allowed. Building/configuring a stopped plan does not Start RX.

The factory may then construct ONE identity-bound native coordinator and an
explicit native Start can produce both reduced streams/statistics. A second
constructor refuses. Its paired adapter accepts ONLY the exact prebuilt paired
configuration and always rechecks app/native authority on Start; it exposes no
single-plan configure or unrestricted native coordinator. Cleanup uses the
same owner/control lock but permits stale selection, so it cannot strand RX.
Cleanup failure retains BOTH application and native
authorities; successful retry releases them only after native disconnect.
Closed application factories cannot reacquire a stale reservation.
Existing-lease operations require an already-held exact application claim under
the application lock; a paused old Start/cleanup cannot recreate its claim after
concurrent release, even if it passed an earlier released-flag check.

This is a backend prerequisite verified on the compiled mock IIO lane, not
enabled paired Sweep in product panes. Product-assigned acquisition/run epochs,
full observed per-step receipts, paired display conversion and both-producer
CaptureJob delivery remain OPEN. No physical RX, UI change, new EXE, full V2
gate, four-source HIL, throughput, soak or release qualification is claimed.

## M3 paired run identity and observed reduced publications

The paired application lease now allocates an application acquisition-attempt
epoch inside the exact admitted Start transaction. It is NOT copied from the
native configuration/Sweep/synchronization epochs. A failed Start consumes its
attempt number but installs no active run. Configure remains inert; Stop and
request_stop invalidate the run before native cleanup, including failed cleanup.

The bound coordinator offers bounded observed line/progress polling. Both native
scalar step prefixes are checked against the exact owner-held request, selected
device serial, typed RX1/RX2 metadata, actual generation/sync/Fs/filter/center/FFT,
sample indices/time and chain acquisition flags BEFORE either reduced grid is
converted. One immutable run retains session/device/topology/selection revision
and common plan/profile. BOTH converted views are checked together; their grids,
partitions and acquisitions cannot disagree. Native first observed Sweep epoch
is retained separately and cannot change within a run or regress across rearm.

Only actual immutable native packets read from that coordinator enter this
admission path. Caller-constructed domain receipts do not authorize hardware.
Unknown timestamp provenance/clock domain remain UNKNOWN/None; retained producer
timestamps do not become hardware timing. The native step wire does not expose
gain readback, so requested gain/profile is intent, NOT per-step observed gain.
Paired admission requires a known normalized serial, not just a nonblank
placeholder. Receipt comparison rejects an absent normalized selected serial.

Product reservation protocol v1 adds a conservative immutable downstream payload
reservation to the SAME native Sweep sink. Typed receipt/scalar-object overhead,
BOTH full owned terminal arrays (36 bytes/bin/pair/retained slot), plus a
conservative 64 bytes/bin sequential conversion/validation scratch allowance,
and 64 bytes/density-cell per RX for statistics validation temporaries count
inside the aggregate
128MiB component and whole-owner512MiB preflight BEFORE context/RF. Observed
drain batches cannot exceed their admitted output capacity; no second allowance
or silent quality/FFT/Fs reduction. Legacy native-only configs retain zero extra
reservation, but older modules cannot admit this product conversion path.

This advances the internal BOTH-receiver reduced conversion path, not user pane
enablement. Common CaptureJob delivery, terminal archival versus active-history
rules, full remaining readback qualification and paired pane admission remain
OPEN. A zero-prefix native planned-source gap has no observed serial and is
explicitly NOT admitted as an observed physical receipt. Existing native terminal
queues/partial flush are unchanged; after Stop
active observed polling refuses rather than re-admitting retired history. No
EXE, physical RX, visible UI, HIL, performance or release proof is implied by
source/compiled-mock tests. A matching native build/test is required separately.

## M3 paired Sweep on the common Analyzer envelope

`bundles_from_paired_sweep` converts one typed observed pair into BOTH common
Analyzer bundles. It preserves exact primary/secondary reduced frame objects,
statistics, quality, segment receipts and grids without copying or relabelling
the native Sweep epoch. The two bundles share the same immutable paired evidence.
Typed RX1/RX2 and selected session come from that validated pair, not producer
ID suffixes. Each bundle's acquisition identity uses the application-assigned
attempt epoch; its spectrum still carries the independent native Sweep epoch,
and every step keeps its native synchronization/configuration epochs.
Accumulation identity includes BOTH attempt and native Sweep epochs to prevent
cross-run history compatibility. This does not invent an RF timestamp or one
configuration generation for a retuned whole Sweep; those remain unknown on the
whole-line identity. Per-step details remain available on `paired_sweep.steps`.

Mismatched session/RX/epoch, foreign or cloned frame objects and RTBW paired
metadata refuse. Ordinary single-producer Sweep and RTBW envelopes are unchanged.
Constructing a bundle is NOT authorization to receive it into an active pane:
the common CaptureJob/activation must still match the exact admitted run and
atomically validate BOTH chains. That adapter/atomic delivery and remaining
readback/terminal semantics remain OPEN; existing paired Sweep pane refusals are
NOT lifted by the pure Analyzer conversion. No UI edit or physical claim.

## M3 typed paired Sweep CaptureJob backend

`Ad936xPairedSweepPaneProfile` retains one exact paired request and full common
tuner plan. Differently cropped pane ranges never become two independent LO
plans. A paired job requires BOTH typed producers and a crop for each.
`Ad936xPairedSweepPaneOwner` is an inert adapter over the SAME selected Live
application, lease and registered coordinator. Its factory borrows the exact
pane control claim only from inside that owner's existing transaction; a token
outside the transaction is insufficient. Native close must succeed before
release; failed cleanup retains the factory/claim for explicit Stop retry.

An owner-issued run binds `PaneCaptureAdmission`; `accept_paired_sweep` checks
the same run object and current activation and prepares BOTH views before
committing any delivery or freshness state. Lone, mixed, repeated or stale
pairs cannot partially refresh one pane. Progress/terminal ordering remains
separate from the application attempt epoch. Pending or wholly unmeasured crops
still receive their explicit masks but do not count as a fresh measured visit.
No frame or scientific metadata is copied, relabelled or turned into a zero.

This backend path is exercised with the actual NativeLive/factory/coordinator
using compiled mock IIO. It does not yet enable the UI V2 plan compiler's
paired Sweep selector or qualify full gain/readback, terminal archival,
matching EXE/visible UI, dual physical RF, four-device HIL or release.

## Paired Sweep Stop-terminal archive

The SAME admitted coordinator retires active authority BEFORE Stop and permits
`poll_retired_archive` only after a confirmed join. It drains at most the
already reserved output queue capacity; ordinary active observed polling still
refuses, and configure/Start removes the previous retired identity.

The pane owner retains one bounded terminal archive before native context/lease
close and releases it before a new capture reservation. Acquired prefixes keep
both actual step receipts and explicit terminal gaps. A zero-prefix terminal
uses a separate `PairedSweepUnobservedTerminal`: planned, all-missing, no acquired
RF/serial/step claim and not an Analyzer bundle. Retired output never feeds the
active pane pump. Failed archival still attempts hardware cleanup, reports its
cause, and requires explicit release confirmation; failed cleanup retains owner
authority for retry. An unadmitted failed Start does not invent an archive run.

This is reduced-output retention, not a lossless recorder or atomic recording
ledger. Callers retaining output own its lifetime. That terminal-only increment
did not include actual per-step RX gains; the additive contract below supplies
them. GUI/physical/release acceptance is not implied by the archive backend path.

## Paired Sweep actual gain readback

Every accepted step now retains two ordered actual PHY readbacks: typed RX1
and RX2, gain mode and numeric gain in dB. The SAME native coordinator validates
the transactional AppliedConfig before arming a step and confirms unchanged
readback when its analytical consumer begins. Missing, reordered, nonfinite
or wrong-mode values refuse; no second device open or Python IQ path is added.
The receipt is a fixed-size array, counted by sizeof in the existing aggregate
Sweep payload budget. Product conversion remains inside the SAME reserved
4KiB/step metadata envelope; no additional memory allowance is created.

The current product supports a common manual-gain profile: BOTH actual chain
values must agree exactly with the actual common scalar. They need not equal
the requested number after existing device clamp/quantization. The request is
retained separately and is never substituted for an unknown readback. Neither
the backend rollback tolerance nor a hardware-test assertion is a new product
acceptance tolerance. Generic native AGC modes are not narrowed by this
validator, but they do NOT become supported paired product modes.

The binding exposes an immutable tuple of read-only gain objects and additive
PLUTO_PAIRED_SWEEP_GAIN_RECEIPT_PROTOCOL_VERSION=1. An older module refuses
product admission before context open. Reduced and statistics protocol versions
remain unchanged. Python observations require typed per-chain gain; BOTH
metadata sets validate before either reduced grid is converted. Progressive,
completed and observed terminal-prefix receipts retain those values; a
zero-prefix planned terminal still has no actual gain observations.

Native/compiled-mock tests cover multi-retune gain retention, actual values
different from intent, malformed receipt refusals, and a later RX2 mismatch
ending the scan with its valid acquired prefix and original cause. This is
source/native qualification, not physical gain calibration or RF accuracy.
The UI V2 paired Sweep plan compiler, matched EXE, visible Windows workflow,
four-source HIL, performance/soak and release qualification remain separate.

## APP-07 orchestration plan and V2 paired Sweep user workflow

The full long-running APP-07 specification supplied on 2026-10-03 is adopted:
resource architecture, paired user assignment/lifecycle/Sweep, RTL production,
four physical sources, isolation, performance, soak, visible UI and release
qualification. Repository state and indexed evidence, not conversation memory
or illustrative PASS values, determine acceptance. The canonical acceptance
layout is Pluto top-left, HackRF top-right, tinySA bottom-left and RTL-SDR
bottom-right. This does not restrict production source assignment. Mock/replay,
empty panes and duplicated streams do not satisfy the four-device gate.

Runtime source dad2f2ec1ee14f9617d2208f4e6d3c3c6317cafd connects the UI V2
paired Sweep editor to the SAME typed acquisition job/native owner. Explicit
RX1/RX2 crops may differ or be disjoint: one common envelope and LO sequence
covers both. Incompatible modes/Fs/FFT or observed tuning-range gaps refuse;
the pair is never silently converted to two independent tuners/time slices.
Stage validates current selected identity/topology without applying RF.
Stopped Apply prepares the exact common profile; Start is separate.

Both reduced producers reach pane preparation progressively, before a whole
scan finishes. Typed paired Sweep metadata remains distinct from paired RTBW.
Ordinary tuning and a new zero-gap line preserve histories; a new admitted
attempt or observed shared input gap clears BOTH histories before fresh data.
Older line/pre-gap sibling output refuses and independent peer history is
outside that clear. These are source/mock guarantees, not paint-rate claims.

Explicit stopped RF Apply also stages the shifted common profile under the
SAME application/recording control transaction, without RX/context/restart.
A non-confirming Apply latches the existing consistency fault and bars Start;
routing is not claimed to roll back atomically. Explicit Stop/Close remain
available. Preview reports requested common configuration and both crops;
its reduced-memory estimate is ONE-chain, not the aggregate pair budget.
Actual combined native/product reservations are checked before context open.

Qualification for this source: 370 serial tests in 35 modules, zero skips,
136.963 seconds; five actual compiled-mock user-flow tests passed separately.
Ruff/compilation and scoped mypy passed; dependency-wide mypy still reported
16 errors in four unchanged out-of-patch files, not a global type-check PASS.
Distinct immutable source review found no scoped blocker. Frozen/committed
patch SHA256 800792732c71134f7e532471c1258ed3a8edb559394474cc99c9b2b29cebdea6.

The native module remains the earlier 4735406 build (SHA256
8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f).
No new EXE/physical dual-RX/Windows visual/HIL/performance/soak qualification
is implied. APP-07/M3 remain IN_PROGRESS. Next is a matching diagnostic
EXE/manifest and exact full-V2 gate, followed by admitted paired visible and
independent-peer isolation checks, then RTL production and remaining HIL,
fault, performance, stability and release gates. UI V2 only; dirty legacy
checkout and current static EXE/firewall remain unchanged.

## Matched diagnostic build and confirmed-Stop QA qualification

The subsequent full pipeline built diagnostic tag
APP07-PAIRSWEEP-20261003-8FB9ED5 from exact source
8fb9ed51299cb833e2b1ebc4b99738bab2190357, which is documentation atop runtime
dad2f2e. Native SHA256 remains 8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f;
actual configure/build and 46/46 CTest (56.78 seconds) regenerated matching
build metadata. The 660-file package, 559-input source snapshot, shared nine-DLL
runtime/one USB runtime, default/native offscreen shell, IIO and tinySA checks
passed. Twenty packaged compiled-mock tests passed separately in 31.035 seconds.
EXE SHA256 f405ae8884050d3f9ab854ef916552b95bc3942f4118a83ae4f3ed72bb48cd43.
These are build/package checks, not physical RX or visible Windows acceptance.

Two initial full V2 attempts failed and remain recorded. The first used an
incorrect Qt6.11.2 test environment against actual frozen Qt6.11.1; the exact
experimental-version guard was preserved. The corrected environment exposed
one independent-restart method with four failure entries. A continuous tinySA
or HackRF source can validly deliver a newer same-run trace between a pre-click
snapshot and confirmed Stop; requiring the earlier object at STOPPED was invalid.
A failed subtest then skipped its Restart and caused later resource-count errors.
Controlled mock/Qt witnesses exercised this schedule; the historical failing
run's exact dynamic timeline is not claimed to have been captured.

QA-only commit 98f600632aff4df6ecefdd7c5369fabfac96aba3 changes two test files,
not product acquisition, DSP, presentation cadence, owners, epochs or budgets.
Both continuous fake sources must advance before Stop, then the exact confirmed
STOPPED bundle must remain stable across another delivery tick. Explicit Restart
still requires a fresh owner/epoch, correct source and unchanged independent peers.
The projection-Stop test now holds a real projector Future after settling an
existing poll under a test-only timer interval; terminal delivery and pending-poll
clear remain required. Distinct immutable review and root 22/22 focused tests
passed. Patch SHA256 fe039a4007ccc58b7d966ff7ec117ad1ab3aeb7833a7214fb07adbaad7cedc8b.

One serial exact full V2 gate at this QA-only commit passed: 1,331 testcases,
1,265 passed, 66 skipped, zero failures/errors, 528.011 seconds; actual Qt6.11.1.
Tracked source stayed clean before/after, no deferred compiled tests or product
modules outside the checkout, native hash unchanged. Four historical NaN warnings
remain visible. Postgate source snapshot and all 660 package hashes verified.
The EXE/build source identity remains 8fb9, NOT the later QA-only commit.

This package remains diagnostic, not current/static/promoted. Actual freezer
PyInstaller6.21.0 differs from project dev pin6.22.3; a pinned local build lane
and version admission remain release requirements. APP-07/M3 remain IN_PROGRESS:
cleanup-retry/lifecycle cause work, admitted paired Windows workflow, genuine
four-source HIL/peer isolation, RTL production, performance/soak and release
qualification are still open. No new physical RX, Computer Use, device/system/
firewall/driver/firmware changes or dirty-legacy product synchronization occurred.

## Explicit Close retry retains the same control lane

Runtime commit 782985298c352fede95c6f63ae73c99dab6768a1 fixes a reproduced
shared Sweep presenter shutdown defect. After a failed Stop and failed service
Close, the owner was retained but its control executor had already been shut
down, preventing an explicit cleanup retry. Both direct shutdown and the UI V2
split Close path now retire that SAME executor only after service Close succeeds.
Failed Close retains the same owner/executor and presentation caches; the closing
state bars Start and polling. There is no replacement owner, rediscovery, hidden
restart, automatic retry or RF/DSP/cadence/epoch/budget change.

New regressions failed before the fix at premature executor retirement. They
exercise repeated Close failure, explicit retry, first Stop diagnostic reported
once, successful independent owner cleanup not repeated, worker-thread Close,
and terminal cache release only after all cleanup callbacks acknowledge.
Distinct immutable review found no scoped blocker. Root scoped regression:
70/70 passed in 31.579 seconds. Frozen and committed patch SHA256:
85b9fbbcf02138fbee84b43eb87f30ac1c703f985ecc05b16bede1d2eb50320f.
Ruff/compilation/scoped mypy passed; dependency-following mypy reported seven
unchanged errors in live_configuration_patch.py, confirmed against an exact
baseline shadow. This is not a repository-wide type-check PASS.

One serial full V2 source gate at exact commit 7829852 passed: 1,332 testcases,
1,266 passed, 66 skipped, zero failures/errors, 535.398 seconds. Tracked source
stayed clean before/after, with no deferred compiled tests or product modules
outside the checkout. The unchanged compiled native module has SHA256
8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f.
Four existing NaN warnings remain visible. This gate uses current Python source
and that native module; it is NOT a newly frozen GUI/EXE or physical SDK test.
The diagnostic 8fb9 EXE remains its earlier source identity and is not promoted.

The historical Qt deleted-label shutdown trace is a separate open investigation.
A direct test-scene retirement gap is a candidate, not an established cause;
a later successful suite without the trace is not a causal fix. APP-07/M3 stay
IN_PROGRESS. Matching build/visible paired workflow, physical peer isolation,
RTL production, four-source HIL, performance, soak and release remain required.

## Direct-scene QA retirement and exact freezer admission

QA-only commit 5a8e25c79255b7b7347e0b4a5d1617b97044d666 makes direct UI V2
scene fixtures follow the existing terminal contract: producer shutdown must
acknowledge first, then graphics retire while Qt objects remain valid, then
close/deferred deletion. A failed producer join or partial graphics retirement
keeps the SAME scene for an explicit retry. Two injected regressions caught
premature deletion in the initial candidate; that rejected candidate was not
integrated. Independent immutable review accepted the corrected fixture;
root scoped regression passed 41/41 in 13.249 seconds. This changes tests only,
not product rendering. The historical Qt deleted-label trace remains unexplained.

Build-tool commit a90a5aee270990d54c1ab18d07cca71c8265dc6f enforces the sole
exact PyInstaller dev pin from pyproject.toml before generating a spec, creating
release outputs or starting native work. Direct official-freezer invocation
uses the same guard. Existing console/stdout + hide-early policy and source,
native, shared-DLL and child-PATH guards are unchanged. Full pipeline provenance
now records the selected interpreter, required/actual freezer version and module
path; it remains pipeline-bound, NOT binary-attested. Diagnostic skip modes do
not manufacture this provenance.

A new ignored workspace-local environment actually has Python3.13.1,
PyInstaller6.22.3 and Qt6.11.1. It inherits existing system packages and is NOT
a hermetic/full-lock reproduction. Global Python and the application .venv are
unchanged. The actual older6.21.0 generator is rejected by the CLI and PowerShell7
pipeline before native work/output creation. An initial WindowsPowerShell5
argument-quoting failure happened before preflight and is retained as a separate
shell compatibility issue, not counted as pin-admission evidence.

Independent immutable source review passed. One serial combined scoped gate at
exact clean a90a5ae passed 67/67 tests with no skips in 18.690 seconds, using
Qt6.11.1 and the unchanged native8dab module. Ruff/compilation/diff checks passed.
This is NOT a full V2 gate, new frozen EXE, physical RX or release qualification.
The old diagnostic8fb9 EXE retains its original source/toolchain identity and is
not current/static/promoted. Next: a new tagged full matching build with the
explicit pinned interpreter, then admitted paired visible/independent-peer tests.
APP-07/M3 and the full APP00–14 roadmap remain in progress.

## Matching pinned diagnostic package and full V2 gate

The next build used exact clean source
`5facbd8f2c8f95c2d0cc60130b51ff7ac52b84c3`, runtime7829852,
QA5a8e25c and build-toolsa90a5ae. A new diagnostic package,
`APP07-PINNED-20261003-5FACBD8`, used explicit local Python3.13.1,
PyInstaller6.22.3, hooks2026.8 and actual Qt6.11.1. This environment inherits
system packages; it is NOT hermetic or a full uv.lock reproduction.
Global Python, SDKs, current/static EXE and firewall settings are unchanged.

The ordinary no-skip CPU/HackRF/external-RTL bridge build passed 46/46
native CTest cases in 60.94 seconds. The package contains660 files and a559-input
source snapshot. Frozen/native/schema, offscreen shells, package-local IIO,
tinySA and shared-runtime checks passed. Matching packaged compiled-mock
bindings passed20/20 in30.701 seconds. These are not hardware tests.

The original private build launcher exited1 AFTER successful package/runtime
and binding checks: its full-V2 launcher redirected stdout into the SAME log
file opened by the test runner, causing a Windows handle PermissionError
BEFORE the suite started. That failure is retained and is not relabelled.
A separate gate-only launcher used distinct stdout/log paths; no native build,
freeze or binding rerun. The actual one full V2 suite passed1,334 testcases:
1,268 passed,66 skipped, zero failures/errors,536.487 seconds. Exact provenance,
tracked-clean before/after, deferred[] and product-outside-checkout[] were
verified; four existing NaN warnings remain. Absence of the historical
deleted-label trace in this run does NOT establish its cause or a causal fix.

The matched EXE SHA256 is
`cab0e7aa4d7db534375b218dc5940e7b464f0216c49ed75c4a008aa501c3f76a`;
native SHA256 remains
`8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f`.
Source snapshot content SHA256:
`d5629fc1dc57b31264a56965154d0e1fe6c97b5ea254c5023c941df4c874920e`.
Source snapshot file SHA256:
`2bc0c48aa762d1667af1f6b673449ca7cb1b75e06cbcef60bf8685ebc9eb8368`.
Release manifest SHA256:
`c45ef7b487cc95847c834b7de28664abaff4097c630a67440f47ceb9e0b151e0`.
An independent read-only reviewer checked selected source/binary/runtime hashes
and gate receipts without rerunning them; no scoped mismatch was found.
Reviewer configured as gpt-6-sol/high; runtime model attestation unavailable.
This selected review is not a whole-release approval.

The SAME new Windows EXE was launched and maximized, its pane editor opened,
and USB discovery completed. Offered entries: AD936x USB, HackRF USB and an
UNVERIFIED tinySA candidate; no RTL was offered in this bounded inventory.
This is not proof of physical RTL absence, RX2 capability, tinySA admission or
four-device HIL. All panes remained Empty; no Stage/Apply/Start/RX occurred.
Four observations/five original JPEGs are retained locally. Logical1440x912
screenshots do not certify physical FHD/QHD/scaling or render smoothness.

Normal Close was followed by a fresh empty target-window inventory and zero
SDRNativeMonitoring processes. Source snapshot and all660 package hashes
were verified unchanged after Close. No system/security/TX changes or other
user-app closure occurred. The Computer Use observe/action/refresh workflow
was used for actual Windows observation, not as a substitute for SDR testing.

All33 sections of the adopted orchestration plan remain reconciled. APP-07/M3
remain IN_PROGRESS; the package is diagnostic, NOT current/static/promoted.
Next is fresh admitted positivepaired/independent-peer Stop/history/epoch
isolation on this matched package, with explicit refusal if RX2 is unavailable,
then RTL production and exclusive four-source HIL. Faults, performance, soak,
visible DPI/gesture acceptance and full release qualification remain mandatory.
Later documentation commits do NOT relabel this5fac source/build evidence.

## Matching 5FAC physical peer witness and RTL production audit — 2026-10-03

The SAME diagnostic source/build5facbd8/runtime7829852/native8dab EXEcab0,
not the later documentation HEAD, was exercised in actual Windows UI V2.
Stage showed requested AD936x USB RX1 RTBW2400..2456MHz/Fs61.44MS/s/
FFT4096/filter56MHz and HackRF USB RX1 RTBW100..120MHz/Fs20MS/s/
FFT4096/filter20MHz. Pane3 used physical tinySA Sweep100..300MHz/1001points,
LOW input, ManualRBW300kHz, Normal accuracy, preserved LNA/atten/spur and no
extra correction. Pane4 Empty created no receiver. Apply armed the layout
without RX or frames; separate Start all produced changing spectra/waterfalls
on all three sources. tinySA displayed post-pass RBW300kHz. The SDR rates and
filters here are requests, not a collected native readback or continuity claim.

Stop selected Pluto reached two running resources and one stopped, with the
AD plot explicitly marked retained/not-new. HF/tinySA continued visibly fresh
and tinySA pass history advanced. Separate Start selected restored Pluto and
three running resources without clearing the visible peer histories. Numeric
GUI peer epochs/control receipts were not captured, so this is visible peer
continuation, not exact epoch preservation proof. RX2 was explicitly unavailable
in the chain selector and was not selected; no positive physical paired claim.

Stop all reached running0/starting0/Stoprequired0/stopped3; Close layout and
normal Close followed. Immediate closing inventory was retained; fresh inventory
was empty and no SDRNativeMonitoring processes remained. The559-input source
snapshot and all660 package file hashes were unchanged after Close. Original
21 observations/21 JPEGs are retained locally. No product/build/system/security/
firewall/TX/amp/bias mutation; current/static package remains unchanged.

A distinct gpt-6-sol/high read-only stills review identified P2 cramped nested
scrolling in tinySA settings and P2 dense Stage impact below the viewport near
Apply, plus P3 small low-contrast headers/status/axis labels. The operator
observed a wheel gesture change Preserve to Normal in the stopped draft; Normal
was explicitly shown in the Stage command preview before RX. Stills alone do
not establish wheel-event causality. Track these as dedicated UI V2 tasks, not
DSP faults. Logical1440x912 is not FHD/QHD/DPI or smoothness qualification.

A gpt-6-luna/high read-only M4 packaging audit, checked by root against source
and files, explains why RTL is not offered: the bridge is compiled but this660-
file package lacks rtlsdr.dll and rtl_external_runtime.json. Runtime qualification
therefore refuses BEFORE native USB enumeration. This does not establish that
physical RTL is absent. The prior DA3 local runtime add-on and its short RX proof
are separate artifacts and cannot be transferred to this build or treated as
production redistribution approval.

Next M4 packet: explicit opt-in RTL runtime inputs, hash-admitted sidecar,
exact dependency set/shared-libUSB collision checks, frozen package verification
and independent review. License/notices approval and matching new-package RTL
workflow precede production claims. Dedicated UI fixes must retain typed
Stage/Apply/Start and resource guards. Positive paired Sweep/RX2, four physical
sources, faults, performance, soak, visible DPI/gesture and full release remain
OPEN. APP-07/M3 remain IN_PROGRESS; full APP00..14 goal unchanged. Configured
reviewer model IDs were checked; runtime model attestation unavailable.

## 2026-10-03 — M4 explicit RTL runtime source admission

Build-tool source commit `19afb68cf79056faff6d7cd7f35c9013bf6add81`
adds optional paired `-RtlRuntimeDirectory` / `-RtlRuntimeManifest`
inputs to a tagged full CPU pipeline. Bridge-only `-EnableRtlOfficial`
without these inputs remains supported. No system SDK or static/current
package is changed. This source qualification does not relabel the existing
5facbd8 diagnostic EXE or its native/physical witnesses.

Input schema `app07-rtl-runtime-input-v1` binds `rtlsdr.dll`, up to eight
dependencies, origin and one to eight notices to exact SHA-256 values.
Bounded strict JSON/filenames and x64 PE/DLL/export/import validation reject
incomplete or conflicting sets; dependency reachability is rooted at
rtlsdr.dll. Static export validation is not ABI or RF accuracy proof.
Shared libUSB must have identical selected bytes. Combined HF+RTL freezing
runs the unchanged exact-nine shared-runtime guard before the separate
RTL guard; generic CPU and HF-only paths are not weakened.

Admission binds native source commit, actual source snapshot and input
hashes. Freeze revalidates those bindings before generating the spec.
Canonical sibling DLLs, external-runtime sidecar and notices are included
in the checked payload; final verification checks the actual release
inventory, provenance, closure, notices and duplicate/canonical paths.
Generated PowerShell provenance alone permits UTF-8 BOM; input/admission
metadata remain bounded strict UTF-8 with duplicate-key refusal.

Root independently reproduced R0 null-export and unreachable-cycle gaps,
retained their failing evidence, then qualified corrected R1/R2/R3.
R3 integrated serial source/mock/static gate: 57/57 PASS, zero skips or
failures, 13.3140955 seconds, six source hashes unchanged during the gate.
Ruff, compileall, scoped five-file mypy and staged diff checks passed.
Distinct gpt-6-sol/high reviewed the frozen source; gpt-6-luna/high wrote
the isolated candidate. Dedicated gpt-6-sol/high completed a UI V2
P2 design-only packet; no UI implementation was performed here.
Configured model IDs are known; runtime attestation is unavailable.

M4 and APP07-E02 remain PARTIAL/IN_PROGRESS: actual admitted vendor set,
component notices/redistribution review, matching new package, RTL service
workflow, shared-runtime coexistence, four physical sources, faults,
performance, soak, visible DPI and full release qualification are still
OPEN. Notice hashes do not establish redistribution rights. Positive
paired RF/Sweep acceptance remains separate. No build, EXE, SDK load,
RX, ComputerUse or system/security mutation occurred in this increment.

## 2026-10-03 — UI V2 usability and local RTL diagnostic package

UI implementation commit `b60507a48b8702df53c234c2149899a1c00191e4`
makes the stopped multi-pane editor use one outer scroll surface, without a
nested tinySA drawer. Closed combo wheel events scroll the surface without
silently changing the draft; keyboard and explicit popup selection remain.
The accepted-Stage summary uses typed layout slots and the same prepared
plan/preview, groups identical typed tinySA requests, preserves distinct
requests and full detail, and disables/refuses Apply if the complete summary
cannot be shown. Standalone Analyzer tinySA settings retain their scroll
contract. No domain/native/Fs/FFT/gain/epoch/owner/recording guard was changed.

Root source/offscreen checks: 52 tests, 170 subtests, 34.33 seconds; Ruff,
compileall, scoped two-file mypy and staged diff passed. A distinct configured
gpt-6-sol/high review found no scoped blocker. Initial clipping failure and
fixture/allocation corrections are retained; this is not visible Windows/DPI
acceptance.

Fresh local-only diagnostic tag `APP07-RTL-20261003-B60507A` contains 665
verified files and 561 source inputs. EXE SHA-256
`398f0de3c3a497338cc583aad3fb4c095e20ee8f46b8b1aa9bbd92c7f53d2b90`;
native SHA-256
`8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f`.
Native sources were unchanged, so Ninja incrementally verified the existing
module without recompiling C++. All 46 CTest passed in 60.22 seconds; matching
packaged-native mock bindings passed 39 tests in 30.088 seconds. Exact shared
nine-DLL/one-libUSB and static actual RTL runtime/notices checks passed.
Static RTL admission does not load the SDK or prove ABI/RX/redistribution.

The RTL DLL is hash-admitted from a local RTL-SDR Blog V1.4.0 archive, with
exact tag COPYING and truthful provenance notices. Static-component versions,
corresponding binary source and redistribution obligations remain unresolved;
no proprietary-license rewrite, distribution approval or current/static
promotion is inferred. Optional pyqtgraph.opengl/OpenGL collection warning
is retained without suppression.

Initial exact-b605 full V2 regression retained one error in an old RTL preview
fixture lacking required typed layout slots: 1338 total, 1271 passed,
66 skipped, one error, 524.530 seconds. Test-only commit
`a5b2e63ae93b68332b2c1702073299faee216225` repairs that fixture, preserves
requested-versus-actual unknown/filter assertions, and adds pane/Empty/no-
install checks. Root focused61 tests/172 subtests passed in36.47 seconds,
with separate independent review. Product and native source are identical to
b605; this later test commit does not relabel the b605 EXE/source manifests.

Corrected serial full V2 source gate at a5b2e63 with immutable b605 native:
1338 total, 1272 passed, 66 skipped, zero errors/failures, 536.816 seconds;
tracked clean before/after, exact provenance true, deferred[]/outside[].
The reviewed one-file fixture difference and all665 frozen package hashes
were checked before/after. This is source/mock/offscreen, not a new build
or visible Windows/HIL/release witness. Four historic NaN warnings retained.

A private cooperating-process Windows hardware-lease prototype passed five
temporary-child-process tests in2.629 seconds after cleanup/quarantine and
zero/one-byte crash-record corrections. It is not yet integrated with a real
HIL runner and is not physical cleanup or isolation evidence.

Direct user extension: include the separately added AD9363 device and future
multi-pane rotation. Keep canonical2x2 Pluto/HackRF/tinySA/RTL, repeat with
AD9363 replacing AD9364, then use distinct AD9363+AD9364 together with other
sources and rotate through every pane position. Require fresh identity/
route/topology/readback, no duplicate USB/IP aliases, explicit Stage/Apply/
Start, stale-data rejection, old-owner release and exact independent-peer
epoch/history preservation for source-scoped operations. Current Analyzer
bars a new Stage while a layout is installed: deliberate full-layout rotation
uses StopAll/CloseLayout/reStage/Apply/separateStart with all resets declared.
Live pane-only rebinding remains unqualified, not silently promised. Chip labels do not prove RX2 or61.44MS/s.
These are planned mandatory HIL scenarios, not new physical observations.

APP-07 remains IN_PROGRESS, M4/RTL production PARTIAL. Positive physical
pairedRX/Sweep, actual RTL workflow, four-source HIL, rotation, faults,
performance, soak, visible Windows/DPI and full release remain OPEN.
Configured agents: gpt-6-sol/high UI writer and distinct reviewer,
gpt-6-luna/high private lease helper; runtime model attestation unavailable.

## 2026-10-03 — explicit USB discovery and physical RTL lifecycle

UI commit `d6895e5` and backend commit
`6b3e86bcb0f4202f711770e631f4bd2723ff6405` separate explicit local discovery
from automatic startup. The USB action and fresh local pane Stage now enumerate
RTL; AD local discovery uses the existing USB-only method without an IP fallback.
Automatic startup still skips RTL, while deliberate full discovery keeps USB+IP.
Missing AD local-provider support fails that provider closed. Source identity,
Stage-without-RX, ownership and stale-route guards remain unchanged.

Root checks passed 104 targeted source/mock/offscreen tests, Ruff and scoped
mypy. A distinct configured gpt-6-sol/high immutable review found no scoped
blocker; this is not whole-release approval. One old runtime-count fixture failed
on the unchanged baseline and was corrected to the exact four-family tuple.

Fresh diagnostic tag `APP07-LOCAL-20261003-6B3E86B` binds the exact source above:
561 source inputs and 665 verified frozen files. EXE SHA-256
`b3f3eacb93f1314996234e1c0e1a1dc18fada262c6072525b944184250a1078f`;
native SHA-256
`8dabdb1d27a4e4568a5e0665ef431f34cc32190d10617bf226383705aa44f14f`.
Unchanged C++ was incrementally verified, not recompiled; 46 CTest passed in
58.91 seconds. Shared nine-DLL/one-libUSB, static RTL runtime/notices and default
offscreen checks passed. Later documentation commits do not relabel this build.
Serial full V2 regression: 1341 total, 1275 passed, 66 skipped, zero failures/
errors, 542.761 seconds; tracked clean before/after, exact provenance true,
deferred[]/outside[], unchanged native, and postgate source/package verification.
Historic NaN and freezer warnings remain recorded, not suppressed.

Actual local discovery through the same V2 services and new packaged native
found USB AD936x, HackRF, tinySA candidate and RTL with no provider failures.
Fresh RTL pane-graph Stage observed the selected RTL2838/R820T route without RX.
Separate physical RTL service qualification then exercised five bounded cycles:
100 MHz/2.4 MS/s/FFT4096 Start, Stop/Start, 145 MHz retune, 433.92 MHz/2.048 MS/s
rate application, and closed-graph reopen at 100 MHz/2.4 MS/s. Each produced 47
distinct observed reduced spectra; epochs advanced 1..5, requested center/Fs/
FFT/generation matched frame metadata, Stop was confirmed, final shutdown left
no SDR worker threads or quarantine. Total host test time was 18.611 seconds.
An initial harness request-source mismatch was refused before Start and retained;
the corrected harness used the selected source ID, without changing product guards.

This is bounded service/native functionality, not frozen visible UI, four-source
coexistence, RF accuracy, sustained throughput, render FPS, lossless capture or
soak. SDK stderr retained direct-sampling initialization messages and `PLL not
locked`; their exact operation-phase cause is not established. Reported quality
8209 includes Uncalibrated, TimestampEstimated and AdcOverload; rate-change cycle
8193 retains Uncalibrated/TimestampEstimated. Overload flags are not removed or
declared clean RF. Generic `snapshots_emitted` remains zero for RTL despite real
frames; use the defined RTL bridge counters, not that default as emission proof.
Automatic gain is exercised; manual tuner gain/table/readback remains OPEN.

The separately expected IP AD9363 is not yet currently qualified: an earlier
`ip:pluto.local` discovery does not supersede the later TCP timeout or prove a
distinct physical device/chip/topology. No network/driver/firewall change or
restart of the existing old diagnostic UI was performed. Canonical four-source
HIL, AD9363 replacement/dual-AD/slot rotation, faults, performance, soak, visible
DPI and release remain OPEN. M4/APP-07 stays PARTIAL/IN_PROGRESS; redistribution/
static-component/corresponding-source HOLD and no current/static promotion remain.
No new agents were dispatched for the build/RX continuation; prior scoped UI
writer and distinct reviewer were configured gpt-6-sol/high, without runtime
model attestation.

## RTL native manual gain prerequisite — 2026-10-03

Source `41df342` plus corrective `e4c3186` adds optional exact integer tenths-of-dB
manual tuner gain to the native RTL owner and binding. Absence stays automatic.
The selected owned handle supplies a discrete table; Start rechecks that table,
rejects values rather than rounding, applies manual mode/value and verifies the
SDK cached setting after Fs/center/reset before starting RX workers. Failed
configuration closes the owner before RX; existing lease/quarantine guards remain.
The cache is not independently measured RF gain or gain-mode readback.

Independent read-only Sol review found and drove two corrections: malformed
optional tables no longer disable automatic observation, and FC2580's `{0}`
sentinel/no-op gain setters do not imply supported manual 0 dB. Missing optional
exports retain the automatic lane. Manual unavailable/invalid cases explicitly
refuse. The bounded table allocation assumes the pinned hash-admitted SDK ABI;
the upstream API has no capacity argument, so it is not hostile-DLL containment.

Exact corrected Windows CPU stage: 46/46 CTest passed in 55.99 seconds, followed
by 35 RTL source/compiled-binding tests in 6.414 seconds, Ruff and compilation
checks. Native SHA-256 is
`312236334b50f730723b1f30a472b0e2bd209ef27a40fcd0b279612138f303a8`.
Mock ABI tests cover signed/zero/exact gains, invalid tables, cache/mode/setter
failures, auto-only legacy DLL, malformed-table auto RX and FC2580 auto/manual
refusal, including no-reader/one-close assertions. R1 review has scoped source
approval; initial R0 blockers and their fixes remain documented.

This does **not** expose manual gain in UI V2 yet. Typed product intent, discrete
selected-session capability, manifest/version admission, cached-setting display,
inert Stage validation, dedicated UI implementation and bounded physical manual
gain acceptance are next. Current-head RTL physical service status returns to
VERIFY because relevant native code changed; earlier `6b3e86b` automatic RX proof
is retained as historical evidence, not relabeled for this module. No new EXE,
full V2 gate, physical manual RX, four-source HIL, soak or release qualification
is claimed. APP-07/M4 remains partial; static EXE and system configuration unchanged.

## Typed RTL gain backend — 2026-10-04

Product source `26e2c46` plus corrective `73a03c9` integrates the native gain
prerequisite into immutable requests, selected-session discrete capabilities,
inert Stage admission, Start, SDK-cache snapshots and strict build/runtime
version checks. None remains Auto; zero is an explicit Manual value. Unsupported
or stale capabilities refuse instead of rounding or fabricating a gain range.
Legacy Auto-only modules remain compatible when both gain-contract declarations
are absent. Full pane profile compatibility distinguishes different gains.

Independent read-only Sol review found an ambiguous-cleanup defect: a native
factory could quarantine after failed close without returning a control, while
Python Stop released the common graph claim. Corrective `73a03c9` requires
confirmed nonquarantined cleanup before release, including superficially complete
Stops. Clean failures release only on explicit Stop. Unknown cleanup retains
the claim and error. Tests cover both paths and a table change after Stage that
refuses before native gain setters or RX reader startup.

A typed publication receipt separates requested gain from the SDK cached
setting and binds it to the exact frame source/configuration/epoch. This is
not independently measured RF gain, gain-mode readback or calibrated dBm.
Auto has no admitted manual cache. UI must preserve that distinction.

Matching Windows CPU stage for `73a03c9`: 46/46 CTest passed in 55.37 seconds;
61 source and compiled synthetic-ABI/product tests passed in 6.920 seconds.
Native bytes remain `312236334b50f730723b1f30a472b0e2bd209ef27a40fcd0b279612138f303a8`;
the incremental build declares source `73a03c9` and gain contract version 1.
Scoped Ruff, type, compilation and diff checks passed. Corrected backend and
publication-source review is approved, not a full release qualification.

Dedicated UI V2 work follows in an isolated worktree: Auto default, confirmed
discrete Manual choices, requested versus SDK-cache captions and stale/retained
guards. No new EXE, full V2 gate, physical manual-gain acceptance, four-source
HIL, performance, soak or production-release approval is claimed by this packet.
APP-07/M4 remains partial. The static executable and system settings are unchanged.

## RTL gain UI and bounded physical acceptance — 2026-10-04

UI V2 source `053c28f` adds Auto/default and exact discrete Manual gain choices
from the fresh selected RTL session. Manual zero is distinct from Auto. Stale
selection/capability changes reset to Auto; unsupported choices refuse rather
than round. Requests retain the full pane profile, and frame captions separate
requested settings from the SDK cache using source/configuration/epoch guards.
UI does not own hardware, and no DSP/Fs/FFT/queue/epoch policy was changed.

The first exact full V2 gate exposed a hardcoded initial `Auto` label. Corrective
source `7cf584e` uses the localization catalog and adds initial EN/RU coverage.
The failed `053c28f` gate remains historical evidence, not a successful candidate.
Dedicated Luna implementation and independent Sol source review preceded root
integration. Root qualification used the corrected immutable `7cf584e` source:

- 55 focused UI tests passed; 3 private cleanup guard tests passed without SDK access.
- 46/46 native CTest passed in 56.75 seconds; diagnostic package verification
  admitted 665 frozen files and 561 source-input entries.
- Serial full V2: 1349 total, 1283 passed, 66 skipped, no failures/errors,
  552.623 seconds; tracked source clean before/after, exact provenance retained.
  Four historical NaN warnings remain recorded.

The new diagnostic EXE SHA-256 is
`8f12ee0bf5e64c82af950910a442b2cfb449b6ea7211d351bdad388486a9042b`.
Native bytes remain
`312236334b50f730723b1f30a472b0e2bd209ef27a40fcd0b279612138f303a8`;
this is a matching new source/build manifest, not a newly changed C++ binary.

One bounded physical RTL run through the actual V2 application services and
matching packaged native module passed five explicit Stage/Start/Stop cycles:

| Cycle | Center | Fs | Requested gain | Fresh observed snapshots |
|---|---:|---:|---|---:|
| Auto | 100 MHz | 2.4 MS/s | Auto | 47 |
| Manual | 100 MHz | 2.4 MS/s | 0 dB | 48 |
| Manual and retune | 145 MHz | 2.4 MS/s | 0.9 dB | 47 |
| Restore Auto | 145 MHz | 2.4 MS/s | Auto | 48 |
| Close/reopen and change Fs | 433.92 MHz | 2.048 MS/s | 0.9 dB | 48 |

FFT was 4096 throughout. The runner validated configuration, admitted epoch and
requested-versus-cached gain on every observed frame. Total 238 snapshots are
polling observations, not FFT/s or displayed FPS. All Stops were confirmed;
final graph shutdown left no SDR threads, claim/control/poller, or native RTL
quarantine. The cooperating-process hardware lease was released with an explicit
cleanup receipt. There were no operation/cleanup exceptions. SDK PLL/direct
sampling warnings and quality flags 8193/8209 remain evidence; no RF calibration,
warning-free operation, sustained performance or losslessness is inferred.

APP07-E01 (physical RTL service RX) and E03 (bounded gain/center/Fs/StopStart/
closeReopen workflow) have current-source physical evidence. E02 and M4 remain
partial: production dependency coexistence, redistribution/corresponding-source
approval and four-source release qualification are still open. This was not a
visible frozen-EXE test. The static/current executable and firewall are unchanged.

## AD9363 Ethernet 100 Mbps prerequisite — 2026-10-04

On explicit user request, the AD9363 device-only Ethernet subnet configuration
was aligned with the existing PC Ethernet subnet. USB/RNDIS access was retained;
the same physical serial was verified across the transport aliases. Both ends
reported an actual 100 Mbps link, not 1 Gbps. Environment/config readback was
verified; persistence across a reboot was not tested. No PC routing, firewall,
driver, firmware image or other SDR network configuration was changed.

A separate native RX1/FFT smoke used the matching `31223633` native binary from
the earlier diagnostic package. Current device `2r2t` mode admitted at most
30.72 MS/s; the 61.44 MS/s request explicitly refused before Start. No firmware
mode was forced. The corrected bounded harness ran 20.0069 seconds at center
2450 MHz, Fs 30.72 MS/s, RF bandwidth 30 MHz and FFT 4096:

- 217 blocks / 56,885,248 delivered complex samples;
- 2.8433 MS/s delivered, corresponding to 90.985 Mbps canonical I/Q payload
  (not Ethernet wire rate including protocol overhead);
- 27,388 computed FFTs, 214 polled fresh spectra;
- zero host refill/FFT/acquisition drops; hardware overflow counter unavailable;
- normal Stop/disconnect and released hardware lease.

SDK READ LINE/READ INTEGER `-9` messages remain unexplained. This proves bounded
Ethernet receive/FFT operation on the 100 Mbps line, not continuous 30.72 MS/s
transport, GUI LPS, dual RX, calibrated RF, or multi-device stability. Device
aliases must not be counted as independent sources. AD9363 product/multipane,
distinct dual-AD, canonical 2x2 and all-pane rotation acceptance remain open.

## Experimental RX1 Ethernet + RX2 USB on one AD9363 — 2026-10-04

The user explicitly requested an isolated same-device split-transport experiment.
Two diagnostic native contexts were enclosed by one root hardware lease; no
production owner/alias/admission rule was changed. USB and Ethernet were verified
against the same physical serial and both exposed two digital I/Q channel pairs.
That topology does not establish two physical RF inputs on an AD9363.

Common settings were identical: center 2450 MHz, Fs 30.72 MS/s, RF bandwidth
30 MHz, FFT 4096, buffer 262144. Selected channels were Ethernet RX1 and USB RX2.
The current Ethernet link was 100 Mbps. Short individual baselines delivered
approximately 2.74 MS/s on Ethernet RX1 and 7.05 MS/s on USB RX2 with fresh FFT
snapshots. These are bounded host-delivery observations, not sustained limits.

Simultaneous fresh streaming **failed in both start orders**:

- Ethernet RX1 first, then USB RX2: both Start calls returned, but over the next
  6.0049 seconds both streams delivered zero new samples and zero new spectra.
  Both still reported RUNNING without a host refill error. RUNNING alone was
  therefore not treated as a healthy receive result.
- USB RX2 first, then Ethernet RX1: the first stream initially delivered data;
  after the second Start a native USB RX2 worker error triggered immediate
  cleanup. SDK `READ LINE/READ INTEGER -9/-138` diagnostics remain unexplained.

The initial early-exit result is preserved. A second bounded diagnostic recorded
both counters and exercised the planned reverse order; it did not weaken the
fresh-data criterion. Both owners were explicitly stopped/disconnected, original
common RF settings and gain modes were restored/read back, and the hardware
lease was released. AGC instantaneous gain was not treated as a restorable fixed
setting. No firmware, server, driver, network or product code was modified.

ADI's [libiio internals documentation](https://wiki.analog.com/resources/tools-software/linux-software/libiio_internals)
describes shared server-held capture buffers, recreation on client channel-mask
changes and server/client demultiplexing for multi-client reads. Shared capture
or mixed-transport handling is a plausible investigation path, not a proven
cause of this failure. The result does not establish universal impossibility
or promise additive USB/Ethernet throughput. Production paired RX retains one
owner/context/buffer on one chosen transport with native fanout. APP-07 physical
paired, four-source, rotation, performance and release acceptance remain open.

### 2026-10-04: first four-physical-source delivery and isolation witness

A private, bounded hardware runner now uses the actual UI V2 application graph,
fresh source Stage, inert Preview, explicit Apply, per-resource pump and the
normal Qt delivery path. One cooperating-process hardware lease encloses all
SDK access and confirmed Stop/terminal drain/join/graph close. It does not replace
the product path with four bare native engines or mock/replay streams.

The witness uses unchanged runtime/build `7cf584e`, its qualified packaged native
extension and source UI with Qt 6.11.1. Six Qt DLL/extension files match the package
on disk. It is **not** a frozen EXE, visible Windows/DWM paint, exhaustive loaded
module, DPI, RF-accuracy, throughput or soak qualification. Documentation commits
must not relabel that runtime/build.

The four actual sources were mapped to separate logical slots:

| Slot | Source | Requested profile |
|---|---|---|
| 1 | AD936x / user-labelled AD9364 USB unit | RTBW, 61.44 MS/s, 56 MHz receive band, FFT 4096 |
| 2 | HackRF | RTBW, 20 MS/s, 20 MHz receive band, FFT 4096 |
| 3 | tinySA | Repeated instrument Sweep, 100–300 MHz, 1001 points, LOW, RBW 300 kHz with readback requested |
| 4 | RTL-SDR | RTBW, 100 MHz center, 2.4 MS/s, FFT 4096, 1 MHz display crop |

Ten immutable pane observations prove advancing source sequences, exact selected
producer identities, separate resource/endpoint mapping and correct units:
AD936x/HackRF/RTL `dBFS/bin`, tinySA instrument `dBm`. Initial accepted Qt delivery
counts were 70/108/2/40; the last observation was 305/373/8/235. These are **not**
paint FPS or a count of every analytical FFT.

Explicit RTL Stop reached STOPPED while all three peers continued. Its separate
Start admitted a new epoch and activation, both 1→2; subsequent new-epoch data
advanced. HackRF Stop/Start demonstrated the same pattern. Across those actions,
independent peer source/session/config/epoch/activation/unit/clock/LO/Fs/FFT/range
anchors remained unchanged and history was not cleared. A delivery completed
while RTL Stop was in progress; the result proves eventual STOPPED, not immediate
publication cessation or RF silence at the click. All owners subsequently closed,
with zero retained resources and SDR workers; the lease was released.

Independent review first refused the runner's retained-frame restart race and
incomplete fidelity predicates. The corrected runner waits for an actually
accepted new activation/epoch, allows sequence reset across epochs, then requires
new sequence advancement. Producer/unit/generation/full-frame geometry checks
have nine pure regression tests. Root independently revalidated the actual log,
and a separate reviewer accepted only its bounded functional scope.

Important limitations are retained:

- RTL SDK reported direct-sampling enable/disable messages and `PLL not locked!`
  at initial Start and restart. Cause and RF effect are unproven; this is not
  warning-free startup or evidence of correct/calibrated RF tuning.
- tinySA's 300 kHz RBW was requested with readback enabled, but this log does not
  contain the actual settings object. Do not substitute intent for saved readback.
- Four logical occupied slots were exercised, but actual 2×2 widget geometry and
  screenshots were not captured. F01 remains partial; visible UI acceptance is open.
- The approximately 17-second start-event-to-PASS span includes configuration and
  cleanup, excludes earlier package preflight, and is not a sustained-rate benchmark.
- Current AD9363 capabilities expose a maximum 30.72 MS/s in its present topology,
  while the product draft/compiler currently offers AD profiles at 20/61.44 MS/s.
  An explicitly capability-qualified 30.72 MS/s product/UI profile is needed before
  peak AD9363 replacement/rotation; no silent downgrade or firmware change is allowed.

Bounded functional F02–F05 are now supported by physical evidence. Shared Pluto
group events, positive paired RX/Sweep, AD9363 replacement, dual AD devices,
source rotations, mixed supported Sweep, faults, performance, soak, matched visible
EXE and release qualification remain separate open work. No product source,
current/static EXE, firewall, driver, firmware or system setting changed in this
increment; the previous split-transport experiment was not repeated.

### 2026-10-04: canonical 2×2 geometry and same-frame settings witness

An expanded private hardware harness now records actual UI V2 widget geometry,
offscreen Qt rasters and metadata from each already delivered frame. It uses
the same unchanged `7cf584e` source/runtime and qualified packaged native module.
All four physical sources run concurrently at the prior profiles; resizing
1920×1080 → 2560×1440 → 1920×1080 does not change their source, configuration,
epoch, activation, RF range or FFT/sample-rate anchors. Fresh source sequences
and accepted Qt deliveries advance after every resize and capture.

Three saved PNGs and eleven pane observations establish an actual nonstacked
canonical grid: AD936x upper-left, HackRF upper-right, tinySA lower-left, RTL
lower-right. Each pane has a visible spectrum/waterfall pair and readable source,
range and unit captions. At FHD, cells are 958×494 logical pixels; at QHD they
are 1278×674. Raster dimensions are 1920×1080 and 2560×1440 with DPR 1. These
are offscreen QWidget captures—not real-monitor, Windows DWM, scaling, paint-FPS,
frozen EXE or release qualification.

The first capture attempt retained a headless-test defect: nearly all text was
square glyphs. The standalone harness had omitted the existing V2 shell font
bootstrap. The corrected harness uses that same application-local registration
of installed Windows Segoe UI and Consolas, checks sampled Latin/digit/Cyrillic
glyph availability before discovery, and records font hashes. It installs or
changes no system font and changes no product source. Original defective rasters
remain preserved; independent inspection of all corrected captures verifies
readable labels. The original apparent caption overflow was not carried forward
as a product defect after correct typography removed it.

Saved tinySA frame provenance now includes queried RBW **300 kHz**, attenuation
**0 dB** and screen-sweep-time readout **4.487 s**. The last value is an instrument
screen readout, not host-measured scanraw duration. LOW input and command ACKs
remain requested/acknowledged intent, not measured input-mode readback. SDR native
quality masks and estimated timestamp quality are retained unchanged; unknown
RF time, losses and calibration are not inferred from a readable raster.

Root offline validation checks the actual log, PNG bytes/hashes/dimensions,
canonical rectangles, same-frame receipts, unchanged peer anchors and freshness.
An independent reviewer verified those artifacts and inspected all three rasters.
Eight pure verifier tests support rejection of clipped/hidden/stacked layouts,
cross-frame receipts and absent readback; those fixtures are not hardware proof.
All owners subsequently Stop/drain/join/close, leaving zero retained resources
and SDR workers with the cooperating-process lease released.

F01's physical fresh-source canonical-2×2 criterion is now satisfied within this
bounded offscreen source-UI scope; earlier Stop/Restart isolation F02–F05 remains
supported by its separate witness. J visible Windows UI and K matched-EXE/release
remain open. A remaining UX issue is the long RTL supplemental SDK explanation
clipping at the FHD right edge; source/range/unit captions remain readable. RTL
again reports `PLL not locked!`; its cause and RF effect remain unknown. No
warning-free, RF-accuracy, sustained-rate, lossless, soak or full APP-07 claim is
made. Next work is mixed supported Sweep, then capability-qualified AD9363
30.72 MS/s admission, device rotations and the remaining qualification gates.

### 2026-10-04: mixed Sweep progress witness and retained instrument failure

A new bounded physical run uses the same unchanged `7cf584e` runtime/package:
AD936x Sweep 300–620 MHz at requested 61.44 MS/s, HackRF Sweep 100–300 MHz at
20 MS/s, tinySA instrument Sweep 100–300 MHz with 1001 points/requested RBW
300 kHz, and RTL RTBW at 2.4 MS/s around 100 MHz. Actual UI V2 Stage/Apply,
resource workers, fair delivery queue and pane board are exercised; no mock,
replay, duplicated source or fabricated RTL Sweep is substituted.

For AD and HackRF, two scans each have a nonterminal partial publication accepted
by the Qt pane board before a complete publication of the **same scan**. Recorded
source, epoch, sequence, activation, grid size and segment generations agree;
partial pending/NaN coverage becomes complete without missing bins. AD terminal
receipts contain ten acquisition records at 61.44 MS/s/physical FFT 8192,
36 MHz analysis window and analysis N=4096; HackRF contains forty records at
20 MS/s/FFT 4096, 5 MHz analysis window and N=1024. This directly witnesses
progressive spectrum delivery before scan completion. It does not establish
monitor paint timing, FPS, every FFT, continuous transport throughput or RF time.
Unpaired Sweep global configuration/session/clock identities remain unknown;
actual generations and acquisition metadata stay with their individual segments.

The saved FHD offscreen Qt raster shows four readable, spatially distinct
spectrum/waterfall pairs with correct units. TinySA's same-frame provenance
contains queried RBW 300 kHz, attenuation 0 dB and screen-sweep-time 4.487 s;
the latter is a device screen readout, not host scan duration. RTL and HackRF
Stop/Start admit fresh local activation/epoch while independent peer anchors,
histories and fresh deliveries are preserved. HackRF progress after its restart
is observed, but a same-scan post-restart terminal pair was not saved.

**The complete candidate failed.** After 110 tinySA prepared publications, its
owner reports `version / identity`: a successfully parsed version maps to
capability facts unequal to the retained expectation. The failing fresh
fingerprint was not recorded, so the differing field and root cause remain
unknown. This is not a demonstrated HackRF restart failure, and the evidence
does not justify weakening the identity guard, changing firmware or blindly
retrying RX. All four resources subsequently Stop/drain/join/close, with zero
retained resources/workers and the hardware lease released. RTL PLL warnings
and the known FHD supplemental-text clipping remain explicit.

The private proof collector also had a separate first-eight-scans limitation:
late valid progress could no longer form a witness. A pure regression reproduces
that false negative; a separately reviewed rolling bounded window fixes only
future evidence collection, with thirteen pure tests passing. It does not
repair the tinySA failure or retroactively prove an unsaved post-restart pair.
The next step is a reviewed, bounded, redacted expected-versus-fresh identity
diagnostic on the same tinySA owner, without extra commands or relaxed admission.
Mixed Sweep remains partial; full-range, paired RX/Sweep, AD9363 capability
admission/rotations, faults, performance, soak, visible Windows/DPI, matched EXE,
licensing and release qualification remain open. No product code, current/static
EXE, firmware, network, firewall, driver or security setting changed here.

## 2026-10-04: bounded mixed Sweep acceptance and tinySA diagnostic

The next independently reviewed diagnostic used the existing inert tinySA
acquisition-factory seam. The SAME serial owner observes its already-read version
response and returns the original bytes unchanged. The inherited model/firmware
identity guard, commands, framing bounds and refusal before measurement remain
unchanged. Only bounded comparison hashes, typed model/control tokens and changed
field names are retained; there is no second serial reader, extra command or retry.
Eight new diagnostic tests, thirteen rolling-witness tests and twenty-five existing
owned-acquisition tests passed, with lint/compile checks. These are software proofs,
not substitutes for the physical result below.

One actual four-source UI V2 application-graph run completed successfully with
the unchanged `7cf584ecde116fba3dd27a4e31061cbf5d6a217b` runtime/native build:

- AD936x USB Sweep at 61.44 MS/s, physical FFT8192, 36-MHz usable windows;
- HackRF Sweep at 20 MS/s and FFT4096;
- tinySA instrument trace, 200-MHz span, 1001 points, requested/read-back RBW300kHz;
- RTL-SDR independent RTBW at 2.4 MS/s and FFT4096.

Six same-scan partial-to-complete pairs were accepted by the actual Qt delivery
path: two initial AD pairs, two initial HackRF pairs and two HackRF pairs after
Stop/Start. Their source, epoch, activation, scan sequence and segment generations
match; per-segment acquisition records retain actual Fs/FFT. RTL and HackRF
individual Stop/Start preserve independent peer identities/history and allow fresh
peer data. Two hashed, readable FHD 2x2 source-UI rasters accompany sixteen pane
observations. This is offscreen source UI, not visible frozen-EXE/DPI/paint proof.

All 141 observed tinySA version comparisons matched the admitted identity, with
zero observer failures. The earlier version/identity failure was NOT reproduced
and its intermittent cause remains UNKNOWN; this clean run is not a product fix
or a retrospective promotion of the failed run. The fixed 180-second observation
window and approximately 196-second total event span are not soak or throughput
qualification. RTL PLL warnings and FHD supplemental-caption clipping remain.

All four resources Stop/drain/join/close, leaving zero retained resources/workers;
the hardware lease is released and source/native snapshots remain unchanged.
Independent protocol and actual-log/raster reviews support a bounded APP07-F09
PASS. Overall APP07 remains in progress: AD9363 peak-rate capability profiles,
paired physical RX/Sweep, rotations, full-range tests, faults, performance, soak,
visible Windows/DPI, matched EXE and redistribution/release qualification remain.

An additional explicitly requested same-AD9363 experiment repeated Ethernet RX1
plus USB RX2. Individual short baselines delivered approximately 2.74 and 7.20
MS/s respectively, but both start orders failed to deliver simultaneous fresh
data. One order stalled both streams; the reverse order reported an acquisition
refill failure with code -138. Exact server/driver cause remains unknown, and no
universal impossibility or additive-link-throughput conclusion is made. Both
experimental owners closed and original RF settings/gain modes were restored.
Production paired capture still uses one owner/context/buffer and one transport.

## AD936x lower-rate profile and explicit Sweep window — 2026-10-04

User clarification: the established 36 MHz analysis-window crop applies to
**Sweep**, because edge distortion was observed outside that window. It is not
an RTBW limit. RTBW is intended to use the full available receive/filter band;
displaying a smaller frequency crop does not reduce the configured sample rate.

The new explicit 30.72 MS/s mode is capability-qualified, not inferred from
an AD9363 label, and does not impose an 18 MHz window. Its profile requests a
30 MHz RF filter and defaults to a 30 MHz Sweep window, with 1 MHz overlap.
An explicit smaller `sweep_window_hz` is allowed (including 18 MHz), but must
exceed overlap and fit sample rate, RF filter and the 36 MHz Sweep ceiling.
This is engineering intent, not a measured flatness/calibration guarantee.

At 61.44 MS/s the established Sweep profile remains RF filter 40 MHz, usable
window 36 MHz and overlap 2 MHz. Full RTBW remains RF filter/window 56 MHz;
at 30.72 MS/s full RTBW is 30 MHz. Native Apply/Start readback still decides
whether the exact requested hardware settings are accepted. The legacy pure
draft API retains its explicit edge-trim compatibility default; new UI V2
AD/HackRF RTBW drafts now select Full Receive by default, with explicit
band choices preserved. UI source implementation is complete; matched-build/HIL
qualification is pending.

Analysis N refers to bins inside the selected Sweep W. Physical FFT F is
ceilPow2(Fs*N/W), never a relabelled N or silently reduced workload. Paired RX
must resolve one common Fs/filter/W/overlap/tuner sequence, though pane display
crops can differ. Unsupported or inconsistent choices refuse before RF.

Backend/compiler source and mock Stage/Preview tests are implemented. UI V2 uses
capability-derived profiles, an optional AD Sweep window control and a preview
that distinguishes RF filter, W, N and physical FFT. Both mode-specific Fs
requests survive passive source refresh, including a narrowed capability snapshot:
unsupported intent remains visible and must be explicitly corrected or refused,
not silently lowered. Explicitly selecting a new source may choose a new admitted
default. The new control has pane-scoped EN/RU accessibility names.

Root integration review found and corrected a lost opposite-mode Fs cache before
integration. Dedicated UI source and root focused tests cover the regression;
independent backend contract review passed, but the later independent UI review
attempt was unavailable due an agent usage limit. No independent UI approval is
inferred. No new physical, frozen-GUI, throughput, RF-flatness or release claim
is made here. Requested 2x2 screenshots from multiple hardware configurations
remain a test deliverable; source/offscreen and visible frozen-EXE evidence must
be labelled separately.

## Matched full-RTBW profile qualification — 2026-10-04

Runtime source `a8d3aa50dfefbad343e67b616203e122e98a5c5d` has a new matching
diagnostic Windows CPU package. It is not promoted to current/static/release.
The native bytes and DSP/queue/budget contracts are unchanged. All 46 native
tests passed; the serial exact-source V2 gate passed 1371 tests (1305 passed,
66 skipped), and package/source integrity was checked before and afterwards.

Physical AD9363 replacement testing exposed a host staging defect: the requested
30 MHz RF filter was rounded to a nearby 30.72 MHz preset, so the strict owner
refused Start before native AD receive. A source regression reproduced this.
The fix adds 30 MHz only to range/step-admitted RF-filter presets; sample-rate
choices, unknown-range fallback and exact native Start/readback guards remain.
The 47 focused source tests include this regression and negative range/step cases.

Two bounded four-physical-source configurations subsequently passed through the
same UI V2 application graph and packaged native module:

| Position | Configuration A | Configuration B |
|---|---|---|
| Top left | AD9364 association: RTBW 61.44 MS/s, RF filter/window 56 MHz, FFT 4096 | AD9363 association: RTBW 30.72 MS/s, RF filter/window 30 MHz, FFT 4096 |
| Top right | HackRF RTBW 20 MS/s, 140–160 MHz, FFT 4096 | Same |
| Bottom left | tinySA 100–300 MHz, 1001 points, queried RBW 300 kHz | Same |
| Bottom right | RTL RTBW 2.4 MS/s, 99.5–100.5 MHz, FFT 4096 | Same |

All four received fresh, separately identified data; counters advanced through
1920×1080 → 2560×1440 → 1920×1080 captures. Six original Qt rasters retain
same-frame identity/epoch/configuration/quality and geometry receipts. These are
source UI/offscreen captures with real hardware, not mock traces, visible frozen
EXE screenshots, physical-monitor DPI/DWM or FPS/transport/soak qualification.
Actual AD owner routes were USB in both cases; this does not qualify Ethernet.
Both runs stopped, drained, joined and closed with zero retained resources or
workers, a released hardware lease and unchanged source/native snapshots.

Root visual inspection found clipped long RTL status text at FHD; this remains
a dedicated UI follow-up. Independent UI approval is still open. AD9363 Sweep
window qualification, device/pane rotations, dual-AD and positive paired RF,
isolation requalification, performance, soak, visible Windows/DPI, packaging
licensing and release qualification remain open; APP07 is not fully qualified.

## AD9363 mixed Sweep window qualification — 2026-10-04

The same diagnostic runtime `a8d3aa50dfefbad343e67b616203e122e98a5c5d`
passed two separate bounded four-physical-source UI V2 runs. AD9363 USB swept
300–620 MHz at actual per-segment Fs 30.72 MS/s, analysis N4096 and physical
FFT 8192. The explicit 30 MHz window used 11 segments; the optional 18 MHz
window used 19 segments. The smaller window is not a mandatory AD9363 limit.
These tests do not establish RF flatness or calibration; 30 MHz RF filter is
the compiled intent, not a filter readback in the accepted acquisition records.

Both runs included HackRF Sweep 100–300 MHz at 20 MS/s, tinySA 100–300 MHz with
1001 points/queried RBW 300 kHz and RTL RTBW 99.5–100.5 MHz at 2.4 MS/s.
For each profile, two AD and two HackRF scan pairs prove that an actual partial
spectrum was accepted by Qt before the same scan's complete spectrum. Two more
HackRF pairs were observed after its explicit restart. Per-segment acquisition
records, source/epoch/scan identity and ordering were audited, not inferred
from screenshots or a final spectrum alone.

Independent RTL and HackRF Stop/Start preserved peer identities, epochs and
histories while the peers continued receiving fresh data. Each run included a
fixed 180-second post-restart diagnostic; tinySA identity comparisons matched
142 and 145 times respectively, with zero observer failures. RTL PLL warnings
remain recorded and do not establish RF accuracy. Historical tinySA failures
were not reproduced here; their cause is still unknown, not declared fixed.

Four original 1920×1080 Qt rasters show both window configurations before and
after restart. These are source UI/offscreen captures with real physical SDR,
not visible frozen EXE, monitor DPI/DWM, FPS, transport-rate or soak acceptance.
Both runs stopped, drained, joined and closed all owners; retained resources
and workers were zero, the hardware lease was released and source/native
snapshots remained unchanged. No product or system setting changes were made.

Current bounded mixed-Sweep and independent-source isolation criteria are
requalified. All-slot device rotations, simultaneous distinct AD9363/AD9364,
positive paired RF, product Ethernet, fault injection, performance, soak,
visible Windows/DPI and independent UI/release qualification remain open.

## All-slot physical pane rotations — 2026-10-04

The same runtime `a8d3aa50dfefbad343e67b616203e122e98a5c5d` passed eight
bounded four-physical-source runs through UI V2: four layouts with AD9363 USB
at actual Fs 30.72 MS/s and full RTBW RF filter/window 30 MHz, then the same
four layouts with Pluto mini AD9364 USB at 61.44 MS/s and full 56 MHz. Both use
FFT 4096. HackRF used 20 MS/s/140–160 MHz/FFT 4096, RTL 2.4 MS/s/99.5–100.5 MHz/
FFT 4096, and tinySA instrument Sweep 100–300 MHz/1001 points/queried RBW 300 kHz.

| Layout | Upper left | Upper right | Lower left | Lower right |
|---|---|---|---|---|
| 0 | Selected AD | HackRF | tinySA | RTL |
| 1 | RTL | Selected AD | HackRF | tinySA |
| 2 | tinySA | RTL | Selected AD | HackRF |
| 3 | HackRF | tinySA | RTL | Selected AD |

Each physical source occupied every actual pane position with fresh, separately
identified data. Forty observations retain real pane/source/producer, epoch,
unit, configuration and advancing counters. Eight original 1920×1080 Qt rasters
include same-frame provenance and spatial 2×2 geometry receipts; all originals
were inspected. Long RTL status text still clips at FHD, an open UI follow-up.

Each layout used explicit StopAll/drain/join/Close before fresh Stage/Apply and
explicit Start. All eight runs closed with zero retained resources/workers,
no native RTL quarantine, a released hardware lease and unchanged source/native
snapshots. Seven new private pure validation tests cover negative assignments
and receipts; root audited actual logs and original image hashes. No independent
UI/release approval is inferred. One RTL PLL warning per run remains recorded.

These are real-hardware source-UI/offscreen captures, not visible frozen EXE,
monitor DPI/DWM, paint FPS, continuous sample delivery or RF-accuracy evidence.
Full-layout teardown deliberately affects every owner and does not prove live
per-source replacement preserving unaffected peers. The two AD units were
tested separately, not simultaneously; actual routes were USB, not Ethernet.
Mixed-Sweep all-slot rotation, five-device selected-slot replacement, dual-AD,
positive paired RF, product Ethernet, faults, performance, soak, visible Windows
and independent UI/release/licensing qualification remain open. APP07 remains
partially qualified. No product/build/system change or static-package promotion
was made for this rotation packet.

## Dual-AD admission and mixed-Sweep pane rotations — 2026-10-04

Six physical Stage-only requests exposed a current identity-admission limitation:
the AD9363 unit has an observed stable serial, while Pluto mini AD9364 supplies
an empty serial and only an operational USB route. With both units in one plan,
the same-family identity guard refuses Stage in either order, before Apply,
Start or RF changes. Five source/mock tests confirm refusal, cleanup and the
different case of two distinct observed serials. This is negative admission
evidence, not a simultaneous streaming failure or positive dual-AD qualification.
Different route IDs or location-dependent Windows instance IDs must not be
promoted to stable serial/calibration identity. An owned native USB-session
attestation contract is proposed for further investigation, not implemented;
the guard remains intact. The generic refusal needs an actionable typed reason.

The unchanged runtime `a8d3aa50dfefbad343e67b616203e122e98a5c5d` also passed
seven new bounded physical four-source mixed-Sweep layouts using the rotation
table above: AD9363 layouts 1–3 and AD9364 layouts 0–3. AD9363 layout 0 was
already qualified in the separate window test. Together these establish basic
all-position coverage for both AD choices in RTBW and mixed Sweep, not live
per-source replacement.

AD9363 swept 300–620 MHz at actual Fs 30.72 MS/s, usable window 30 MHz,
analysis N4096 and physical FFT 8192 over 11 segments. AD9364 swept the same
range at 61.44 MS/s, window 36 MHz, N4096 and physical FFT 8192 over 10 segments.
Compiled RF-filter intents 30/40 MHz are not segment filter readbacks.
HackRF swept 100–300 MHz at actual per-segment Fs 20 MS/s/physical FFT 4096;
its terminal usable 5 MHz/N1024 must not be confused with the physical FFT or
a full 20 MHz usable Sweep window. tinySA used 100–300 MHz/1001 points/LOW/
queried RBW 300 kHz, and RTL used RTBW 99.5–100.5 MHz/2.4 MS/s/FFT 4096.

Thirty-five observations retain actual pane assignments and advancing counters.
Twenty-eight same-scan partial-before-complete pairs demonstrate progressive
acceptance by Qt for AD and HackRF. Seven original 1920×1080 Qt rasters include
same-frame provenance and geometry receipts; original log/image hashes were
audited and all images inspected. These are real-hardware source-UI/offscreen
captures, not visible frozen EXE, monitor DPI/DWM, paint FPS, transport-rate,
RF-accuracy or soak evidence. Long RTL status and some right-edge Sweep HUD
text remain clipped and require dedicated UI follow-up.

An initial test failed in the private geometry validator because immutable
tinySA command acknowledgements are a tuple, whereas the validator expected
a list. The failed log/image were retained. Seven pure regression tests now
cover runtime tuples and serialized lists while rejecting wrong or reordered
commands. This correction changes only the private test harness, not product
code, device settings, command-content guards or test timing.

All seven successful runs stopped, drained, joined and closed with zero retained
resources/workers, no native RTL quarantine, a released hardware lease and
unchanged source/native snapshots. Each retained an RTL PLL warning; AD9364
layout 1 also emitted READ LINE/READ INTEGER -9. Their exact SDK origin and
operation phase remain unknown. Functional delivery and cleanup do not prove
error-free SDK operation or repair of historical failures.

APP07 remains partially qualified. Simultaneous distinct AD admission, selected
slot replacement preserving independent peers, positive paired RF, product
Ethernet, faults, performance, soak, visible Windows/DPI, independent UI/release
review and licensing remain open. No product change, new build, system mutation
or static/current package promotion was made in these two test packets.

## USB-session identity API feasibility — 2026-10-04

A subsequent read-only physical probe through the exact packaged libiio DLL
(reported version 0.26) held one USB context for each Pluto without enabling
channels, creating acquisition buffers, starting RX or changing RF settings.
Both contexts reported actual backend `usb`, descriptor `0456:b673` and
backend context URIs `usb:2.18.5` and `usb:2.19.5`. The first reported its
known serial; the mini reported explicitly empty USB and hardware serial
strings, rather than absent attributes.

The [official libiio 0.26 USB backend](https://github.com/analogdevicesinc/libiio/blob/v0.26/usb.c)
constructs the context URI from its libusb device's bus/address/interface and
obtains USB identity attributes from that device descriptor. Source inspection
and observed DLL behavior support API feasibility, not binary reproducibility
or cryptographic device authentication.

Thirteen private pure tests distinguish missing versus empty observations and
reject wrong backend/route/descriptors, duplicate sessions, two interfaces of
one USB device, contradictory serials and the same known serial on different
routes. Interface numbers must not create independent physical-resource keys.
USB bus/address is a connection-scoped observation, not stable calibration
identity; neither location-dependent identifiers nor a generated session UUID
can supply a permanent serial.

Repeated reads from the same handles matched, but context attributes are cached:
this is not a fresh liveness or hot-swap test. Product integration must obtain
these facts from the same native context that will perform acquisition, retain
its lifetime/cleanup claims, handle unresolved USB/IP aliases and explicitly
define replacement/revalidation behavior before changing the admission guard.
The current native probe still exposes caller URI rather than these optional
observed fields; that prerequisite is the next implementation task.

All read-only contexts were destroyed through the SDK, no acquisition workers
or buffers existed, the hardware lease was released and source/native snapshots
were unchanged. The void destroy API supplies no exhaustive device-release
readback. No product admission change, new build, RF test, UI change or system
mutation occurred. Simultaneous product RX on the two Pluto units remains open;
this API feasibility result is not a substitute for that acceptance test.

## Native owned-context observations implemented — 2026-10-04

Runtime source `574f4e5797a389925773f8d9ef55e37c4e201574` adds four
optional, read-only `PlutoContextProbe` fields: `backend_uri`,
`usb_vendor_id`, `usb_product_id` and `usb_serial`. The original `uri`
continues to mean the requested route. A shared reader copies attributes from
the same context held by the native owner or temporary probe; no second context
is opened for an owner's getter. Absent attributes become `None`; observed
empty strings stay empty. Malformed or contradictory values are retained as
raw observations, not silently normalized into an admission decision.

Windows and Linux source paths use this reader. Windows compiled successfully;
Linux compilation is not established. The StageOnly CPU/HackRF/RTL build passed
all 46 native tests in 55.00 seconds and one matching Python-binding test in
0.719 seconds. The staged native artifact SHA256 is
`d187a372a347d2cdfdeaea3faefc764e71d3bd9a0cbfa352692a56e984b60d77`.
Tests distinguish an owning context from an earlier temporary probe, exercise
missing/empty/invalid attributes, enforce read-only bindings and verify no RF
mutation, buffer allocation or leaked mock context during observation.

A separate physical read-only check held two native `PlutoDevice` owners using
that exact staged module and the previously qualified libiio runtime. They
reported USB routes `2.18.5` and `2.19.5`, descriptor `0456:b673`, and known
versus explicitly empty USB serials. Their topology observations retained the
same context fields. No RF configuration, channel enable, acquisition buffer,
Start or refill was performed. Both owners disconnected, flags became false
and the hardware lease was released. This is actual native-owner metadata
evidence, not simultaneous product RX or an exhaustive USB-handle release audit.

These fields remain cached, untrusted inputs to future connection validation:
they do not establish liveness, stable calibration identity, hot-swap detection
or USB/IP alias exclusion. Product admission, UI, DSP settings and control/
epoch policies are unchanged. Next work is typed connection assertions against
the actual owner before RF, resource claims that reject aliases and preserve
uncertain cleanup ownership, and an explicit connection-lifetime policy.

The old a8 diagnostic EXE/native artifact is unchanged. Earlier physical 2×2
evidence remains attached to that source/build, not relabeled as this candidate.
Current candidate HIL criteria require revalidation with a matching package
and actual RX; independent review, full UI regression, visible Windows/DPI,
performance, soak and release qualification remain open. No static/current
package promotion or system mutation was made.

## 2026-10-04: opt-in owned USB connection assertion and lifetime claim

Runtime commit eb2325b introduces typed ExpectedUsbConnection on all three
native Pluto owner constructors: device, fixed-band engine and continuous
Sweep coordinator. The optional trailing argument preserves existing calls.
Host values are range checked before opening a context. The actual newly
owned context must confirm USB backend, bus/address/interface, VID/PID and
consistent known or genuinely empty USB/hardware serials before RF operations.
The requested URI, label and a previous temporary probe cannot substitute for
that check. A separate USB admission protocol is required; no legacy fallback
is allowed when an explicit assertion was requested.

Explicitly asserted native owners use a cooperative process-local lifetime
claim. Different interfaces at one USB bus/address are one resource. A known
serial also excludes aliases across USB addresses. Stop retains the claim;
disconnect releases it only after SDK context destruction returns. A mock
destruction barrier tests retention while close is pending. Missing/invalid/
changed connection facts refuse; failed admission destroys its context before
releasing the claim. This does not provide cross-process exclusion or prove
exhaustive hardware/OS release from libiio's void-return destruction call.

The Qt-free service factory can explicitly construct these typed owners.
Current product descriptor/Stage/composition paths do not yet request the
new assertion, and the unknown-serial same-family guard is unchanged. Ordinary
legacy owners do not participate in the opt-in claim registry. Product-wide
USB/IP alias exclusion, session invalidation, stable calibration identity and
same-port indistinguishable hot-swap remain unsolved; cached attributes are
not liveness. Do not relax application admission based solely on this increment.

Exact staged CPU/HackRF/RTLofficial native build: 46/46 CTest PASS in53.53s,
native SHA256 033724259775926cc05af06cf254341472296f48a92eb8080bf7c3c3c59ed6ca.
Additional checks:19 Python identity/USB tests,8 unchanged Stage guards,2
matching native binding tests. Initial candidate45/46 native failure is retained:
the HackRF test waited for numerical completion rather than actual terminal
publication. Its wait now checks both with the unchanged3-second deadline and
all geometry assertions; no HackRF runtime implementation changed. Initial
binding witness also incorrectly called Stop on an idle engine. Test-only
fd339c2 corrects that witness and preserves the existing RUNNING-only guard;
the native build remains attributed to eb2325b, not fd339c2.

A separate exact physical metadata-only witness held two asserted Pluto USB
owners concurrently, one with known serial and one genuinely empty. Duplicate
asserted owners refused, an expected descriptor mismatch refused, and explicit
reopen after disconnect succeeded. No RF configure, buffer, Start or refill
occurred; all owners disconnected and the cooperating-process lease released.
This is not simultaneous application RX or RF/transport performance proof.
Windows compiled; Linux source equivalent is not Linux build qualification.

The current/static EXE and old a8 diagnostic package are unchanged. No new
frozen EXE/full V2/current RX/visible Windows-DPI/soak/independent-review/release
acceptance is claimed. Next: wire assertions and bounded session/resource
claims into the actual product before approving dual-Pluto admission, then
qualify a matching package. Final2×2 RX reports must include original
screenshots for different source assignments/modes, with build/resolution/
scaling and explicit capture scope; previous screenshots are not new-build
acceptance evidence.

## 2026-10-04: product USB session admission and current 2×2 qualification

Backend b10b0d5 and UI integration 5fdb8f0 connect the previously opt-in native
USB assertion to selected product owners. Actual context observations travel
through the device descriptor, source choice, captured Stage selection and
composition into the same RTBW/Sweep native owner. A requested assertion requires
the supported native protocol; missing, invalid or changed facts cannot silently
fall back to another interface or IP. Stage checks the aggregate layout before
owner creation, Compose rejects a changed selection/revision, and uncertain
close retains its graph and claims until a successful explicit retry.

Two distinct observed USB connections can now be admitted even if one device
has a genuinely empty serial. Different interfaces at the same bus/address are
still aliases; known serial aliases and unresolved unknown USB/IP relationships
refuse. These are bounded connection-lifetime observations, not permanent
calibration identity, liveness, indistinguishable hot-swap detection, or global
cross-process exclusion. No invented serial or relaxed paired-tuner architecture
was introduced. Stage/Apply remain inert with respect to RX; Start is explicit.

Exact diagnostic package APP07-USB-20261004-5FDB8F0 was built from 5fdb8f0:
46/46 native CTest in54.20s;665 package files verified;3 packaged MOCK binding
tests in2.776s. The full V2 gate ran serially after freezing:1375 total,
1309 passed,66 skipped,0 failures/errors in488.254s, exact provenance with clean
tracked source before and after. Native SHA256 is
033724259775926cc05af06cf254341472296f48a92eb8080bf7c3c3c59ed6ca;
the C++ bytes are unchanged from the prior native increment, but this matching
build manifest and source snapshot are attributed to 5fdb8f0, not a later
documentation-only commit. Existing NaN and optional OpenGL freezer warnings
remain recorded; they were not suppressed.

Six separate physical four-source workflows passed, with eight original PNG
captures at1920×1080 and2560×1440, DPR1.0:

- Canonical AD9363 USB / HackRF / tinySA / RTL RTBW layout, FHD and QHD.
- mini AD9364 USB RTBW with RTL/HackRF/tinySA in rotated positions, FHD.
- Two different Pluto USB devices, each RX1, plus HackRF and tinySA,
  in two different layouts, FHD/QHD and FHD respectively.
- Mixed AD9364 Sweep / HackRF Sweep / tinySA Sweep / RTL RTBW.
- Rotated mixed AD9363 Sweep / HackRF Sweep / tinySA Sweep / RTL RTBW.

Every source delivered advancing frame counters and visible spectrum/history
bundles. RTBW actual settings included AD9363 Fs30.72MS/s with30MHz RF window,
mini AD9364 Fs61.44MS/s with56MHz RF window, HackRF20MS/s, and RTL2.4MS/s.
The AD9363 value is the observed current firmware profile, not a universal chip
limit. Sweep used the respective30/36MHz useful AD windows,4096 analysis bins
and8192 physical FFT. tinySA returned1001 points over100–300MHz and actual
300kHz RBW. Fs is an acquisition setting, not lossless transport throughput.

The two mixed Sweep workflows retained eight accepted partial-before-complete
pairs across Pluto and HackRF: each partial result belonged to the same scan as
its later complete result, with pending segments preserved rather than painted
as fresh measured data. This verifies progressive UI delivery, not DWM paint
cadence or a claimed FFT/LPS performance target. Each workflow ended with normal
Stop/terminal drain/worker join/graph close, no retained resources and a released
cooperating-process hardware lease. RTL startup SDK warnings remain in evidence;
no RF accuracy or SDK-cause fix is inferred.

Captures are actual UI V2 QWidget.grab/source-UI/offscreen renders with physical
sources and matching packaged native/Qt bytes. They are not mock/replay, but are
also not visible frozen-EXE desktop screenshots or Windows monitor-DPI/DWM proof.
The gallery records exact mapping, ranges, modes, capture scope and image hashes.
Root inspected all original rasters. Long RTL detail is clipped at FHD and
technical typography/frequency formatting remain UI follow-up work. A separate
scoped UI review approved the Stage/composition change; this is not whole-release
approval. Sweep Fs selector recoverability is a separate queued UI finding.

Canonical fresh four-source delivery/geometry and mixed progressive delivery
have current bounded evidence. Selected-source Stop/Restart isolation still needs
current-head revalidation after the admission change. Dual-USB AD RTBW is now
positive bounded evidence, not simultaneous paired RX1/RX2 of one device,
independent dual-AD Sweep, product Ethernet, or hot single-pane replacement.
Performance, fault/unplug recovery, soak, visible Windows/DPI, runtime licensing
and independent release qualification remain open. APP-07 is still PARTIAL.
Current/static EXE, firewall and the dirty legacy checkout were not changed.

## 2026-10-04: current-package selected-source isolation qualification

The same immutable 5fdb8f0 diagnostic package was used for two additional
physical four-source mixed-Sweep workflows, one with AD9363 USB at30.72MS/s/
30MHz useful window, the other with mini AD9364 USB at61.44MS/s/36MHz useful
window. Each also used HackRF20MS/s Sweep, tinySA1001-point100–300MHz Sweep
with actual300kHz RBW, and RTL2.4MS/s RTBW. The documentation HEAD during
these tests was8ef5ec7; it is not the source identity of the5fdb8f0 build.
No product source or native bytes were changed and completed build/full-gate
qualifications were not repeated or relabeled.

Actual UI V2 selected Stop and separate Start were exercised for RTL and
HackRF in each workflow. Stop reached STOPPED; the restarted target received
new epoch and activation1→2 and advancing new data. All three independent
peers continued with unchanged identity/configuration/epoch/activation/clock/
unit and nondecreasing histories. tinySA repeatedly advanced sequence1→9
and history2→10 in both runs while RTL delivered initial and restarted data.
Restarted HackRF again delivered accepted partial-before-complete results
from the same scan. There were26 four-pane observations,8 action receipts,
12 progressive pairs and4 original before/after FHD PNG captures. Sixteen
pure predicate tests also passed; they are auditor tests, not additional RX.

Both workflows ended with normal Stop, terminal drain, worker join and graph
close, zero retained resources/workers, unchanged source/native inputs and
a released cooperating-process lease. The unchanged665-file diagnostic
package and its native-artifact command were reverified after testing.
Existing RTL startup SDK warnings remain recorded. Root inspected all
original captures; clipped RTL detail and small/inconsistent technical
labels remain UI follow-up work.

These are bounded source-UI/offscreen physical-functional isolation results,
not visible frozen-EXE Windows-DPI/DWM or FFT/LPS/latency/soak measurements.
Software fault injection, actual USB removal/reconnect, native worker failure,
paired RX1/RX2, product Ethernet and independent dual-AD Sweep are not proven
by this increment. Current selected-source isolation criteria now have
bounded evidence; APP-07 remains PARTIAL. Current/static EXE, firewall and
the dirty legacy checkout are unchanged. End-of-test galleries retain
exact device-to-pane mapping, modes, ranges, capture scope and image hashes.

## 2026-10-04: controlled owner-poll software fault with four physical sources

Two further bounded four-source workflows used the unchanged5fdb8f0 diagnostic
package/native033724. Documentation HEAD during RX wasb1c9c8d, not a new build
source. One canonical layout used mini AD9364 Sweep/HackRF Sweep/tinySA Sweep/
RTL RTBW; a second rotated layout used tinySA/RTL/AD9363 Sweep/HackRF Sweep.

A private diagnostic hook installed on one already-owned adapter instance
under its control lock deliberately raised one software exception from
poll_bundles. This did not unplug USB, kill a native acquisition worker, change
RF settings, or mutate a driver, hub, network, firmware or security setting.
Actual physical native RX remained the source of all measured spectra.

For RTL and separately HackRF, the resource pump latched the finite first
cause owner_poll/operation_failed with no instrument cause and moved only the
target to STOP_REQUIRED. Its owner and lease remained retained. Start selected
and Start all were disabled, Stop selected remained available, and the UI showed
a sanitized explicit error. No raw private exception text was exposed.
The three independent peers continued advancing with unchanged identity/
configuration/epoch/activation/clock/unit and nondecreasing histories.

Clicks on disabled Start controls did not retry: one hook invocation, unchanged
target run serial/owner/presented spectrum/history and immutable first cause.
An explicit Stop released the target lease; a separate Start used a fresh
adapter owner and new epoch/activation1→2, cleared the previous failure and
delivered fresh data without resetting peers. Recovered HackRF again delivered
two partial-before-complete results from their same scans.

Across both workflows:20 observations,10 progressive pairs,6 original FHD
before/fault/recovery PNG captures and24 pure hook/predicate tests passed.
Both processes ended normally with0 retained resources/workers, released
cooperating-process leases and unchanged source/native inputs. The665-file
package/native-artifact command and source snapshot were reverified.
Existing RTL startup warnings and AD9363 READ LINE/INTEGER -9 output remain
recorded; their exact SDK operation/cause is unknown. Normal product cleanup
does not imply a universally error-free SDK path or identify that SDK cause.

Captures are source-UI/offscreen physical-functional evidence with matching
packaged native/Qt bytes, not visible frozen-EXE Windows DPI/DWM/FPS/latency or
soak qualification. This is partial failure-isolation evidence for the actual
pump handling an injected adapter error, not native-worker death or physical
USB removal/reconnect proof. These remaining fault classes, presentation
backlog, performance, visible UI and independent release qualification remain
open. APP-07 remains PARTIAL; no current/static EXE promotion occurred.

## 2026-10-04: four-source presentation backlog and a UI freshness finding

Two bounded physical four-source workflows reused unchanged runtime5fdb8f0,
native033724 and the665-file diagnostic051 package. Documentation HEAD during
RX was a47430a, not the build source. One layout used AD9364USB Sweep/HackRF
Sweep/tinySA Sweep/RTL RTBW; another rotated tinySA/RTL/AD9363USB Sweep/HackRF.

Only the Qt delivery timer was paused for20 seconds. Qt event processing,
state/control timer, native acquisition and pane preparation remained active.
Each run recorded40 interval samples at0.5 seconds plus control/resume receipts.
The presented identities/sequences/revisions/histories and delivered counter
remained unchanged; all four owner prepared-publication counters advanced.
Pending packets never exceeded6 against the existing8-packet four-pane bound.
Queue offered increments were1679 and1610; latest/terminal supersession was
counted explicitly as presentation coalescing, not IQ loss or RF continuity.

While delivery was still paused, explicit selected Stop released one lease,
and separate Start established a new activation2 without rearming peers.
After delivery resumed, target epoch2/current activation was accepted and old
target tokens did not return. Other panes resumed with unchanged measurement
identity/epochs and retained histories. Both processes closed normally with
zero retained resources/workers, released cooperating-process leases and
unchanged source/native inputs.92 observations,10 same-scan progressive pairs,
6 original FHD PNG captures and34 pure predicate tests passed. Original image
hashes/geometry and complete logs passed offline audit. Package/source snapshot
were reverified. No physical unplug/native-worker death, actual paint/RSS/soak
or continuous transport throughput qualification follows from these counters.

Visual inspection and independent read-only UI source review found a P1
presentation issue: the prominent Data age value follows latest host-router
input even when the displayed plot has not updated for20 seconds. The tooltip
disclaims paint scope but does not sufficiently distinguish displayed freshness.
The planned UI-only repair will label Host input age separately from Plot last
updated, timestamp only successful per-pane GUI acceptance, and explicitly mark
retained prior-activation graphs until a fresh admitted packet is shown.
Rejected/stale packets or hardware restart must not reset plot-update timing.
This finding is not fixed by the backlog test and is not a native/RF error.

Current APP07-G04 is PARTIAL for timer-pause/control/resume evidence; whole Qt
thread stalls, RSS/render pressure, remaining native/physical fault classes,
performance, visible Windows DPI/DWM, soak and release qualification stay open.
Next work is the dedicated UI freshness repair and source-specific performance
baseline. APP-07 remains PARTIAL; current/static EXE and system settings unchanged.

## 2026-10-04: separate per-pane host and displayed-data freshness

UI-only runtime9b635e2 (original11af plus corrected spacing/status follow-up)
labels Host input age separately from Plot last updated. A per-pane monotonic
timestamp advances only after successful GUI data acceptance. Rejected/stale
packets and hardware Start do not reset it. Unknown/nonfinite/backwards clock
values remain unknown. A retained previous-activation graph is explicitly marked
until a packet from the current admitted activation is displayed; affected RF/
paired history clears invalidate the matching timestamps. These ages do not
claim RF time, per-bin sweep freshness, real paint or DWM completion.

The first candidate's full regression exposed a1280x700 extra-scroll regression
and three obsolete whole-status assertions. The corrected candidate reclaims
vertical grid/cell spacing without reducing font size, plot minimums, the two
timing rows or control guards. Independent scoped UI source review found no
blocker. Native/DSP/Fs/FFT/gain/budgets and RF/Start/Stop policy are unchanged.

A matching665-file diagnostic package passed46 native tests in53.13s. Serial
full UI V2 regression AFTERfreeze passed1381 total:1315 passed,66 skipped,
zero failures/errors in469.658s, exact tracked-clean source before/after,
no product modules outside the checkout or deferred compiled tests.
The initial synthetic RTL native-test assertion failure remains retained;
its exact causal branch is unknown and was not declared fixed by later passes.

Two new physical four-source workflows used canonical AD9364USB/HackRF/tinySA/
RTL and rotated tinySA/RTL/AD9363USB/HackRF. Pluto and HackRF ran progressive
Sweep, tinySA instrument Sweep, RTL RTBW. Only the GUI delivery timer was paused
for20s; acquisition/preparation and Qt control processing continued. Each run
recorded47 observations, including40 interval samples. Plot age reached20–21s
while host input remained fresh. Pending packets stayed at most6 of8, offered
increments1668/1614 were coalesced explicitly, and no packets were delivered
during pause. These are presentation counters, not RF/IQ loss or FFT throughput.

Explicit selected Stop/Start of RTL, then separately HackRF, created activation2
and epoch2 without resetting the three independent peers. The prior-activation
warning appeared only on the selected pane, survived until new admitted data,
and cleared after resume; stale target epochs did not return. Ten accepted
partial-before-complete pairs from their same scans confirmed progressive Sweep.
Both processes closed normally with zero retained resources/workers, released
cooperating-process leases and unchanged source/native inputs.

Eight original1920x1080/DPR1 PNGs cover two configurations and normal/pause/
retained-prior-activation/resumed states. Complete logs, timestamps, image
geometry/hashes and control receipts passed offline audit. Root and independent
UI reviewer inspected all eight: no timing-label/control overlap at this size.
Waterfall dark horizontal patterns differed between some before/pause images;
unchanged accepted-history counters are NOT pixel-identical-image proof.
Large sparse/black waterfall regions and dense RTL details remain visual debt.
AD9363 SDK `ERROR: WRITE ALL: -9` is retained with exact SDK phase/cause unknown;
combined stdout ordering does not establish cleanup causality.

Captures are physical source-UI/offscreen evidence using matching packaged Qt/
native bytes, not visible frozen-EXE Windows DPI/DWM/paint-latency qualification.
No50ms, continuous transport/lossless/duty/Pd, RF calibration, paired RF or
Ethernet qualification follows. Current/static EXE and system settings remain
unchanged. APP-07 remains PARTIAL. Next is M8 source-specific metric definitions,
instrumentation and performance baseline; remaining fault classes, stability,
visible UI and independent release qualification remain open.

## 2026-10-04 — M8 exploratory four-source performance baseline (056)

This packet measures the unchanged immutable runtime source
`9b635e229c734a8c29b3dfe225ebf481b7487aa9`, native SHA256
`033724259775926cc05af06cf254341472296f48a92eb8080bf7c3c3c59ed6ca`,
matching packaged Qt6.11.1 at documentation HEAD
`1f86c4abda9bd9c7ea6cc5259a6d0c9197fe7805`. Later documentation commits do not
relabel these source/build/physical results. No product/UI/native changes,
new build, static/current promotion or system/security changes were made.

Two serialized physical layouts, each with 15s warmup and 61 scalar observations
across a 60s steady window, were measured on the same active resource owners:

| Pane | Layout A | Layout B, rotated by two slots |
|---|---|---|
| top-left | mini AD9364 USB | tinySA Ultra USB |
| top-right | HackRF USB | RTL-SDR USB |
| bottom-left | tinySA Ultra USB | full AD9363 USB |
| bottom-right | RTL-SDR USB | HackRF USB |

AD9364 Sweep300–620MHz: actualFs61.44MS/s, useful window36MHz, logicalN4096,
physicalFFT8192. AD9363 Sweep300–620MHz: actualFs30.72MS/s, useful30MHz,
logicalN4096/physical8192; this is a lower admitted profile, not a permanent18MHz
limit. Both are USB witnesses, not Ethernet. HackRF Sweep100–300MHz uses actual
Fs20MS/s/FFT4096. tinySA LOW100–300MHz uses1001points/manual observedRBW300kHz,
device-calibrated dBm. RTL RTBW99.5–100.5MHz uses actualFs2.4MS/s/FFT4096/hop2048.
No implicit sample-rate/FFT/quality reduction occurred.

| Source | Computed FFT/s, A / B | Full Sweep/s, A / B | Successful GUI data applications/s, A / B |
|---|---:|---:|---:|
| AD9364 / AD9363 |349.52 /263.35|0.566 /0.383|5.55 /4.18|
| HackRF |unknown /unknown|40.70 /39.10|39.90 /39.10|
| tinySA |not an IQ/FFT source|native device counter unavailable|0.73 /0.73|
| RTL |1172.10 /1171.78|not Sweep|39.30 /39.79|

These boundaries are deliberately distinct. HackRF's current Sweep binding
does not export existing analytical DSP/decoded-IQ counters; callback bytes
include protocol headers and are not substituted for IQ or FFT counts. tinySA's
physical scan cadence is not itself a UI defect. Successful Qt data application
is not paint FPS or compositor presentation. Analytical-ready-to-paint latency
and actual Spectrum/Waterfall paint FPS remain unmeasured, not zero.

Native AD received samples averaged4.363/3.287MSps across the entire host Sweep
window; RTL admitted samples averaged2.40046/2.39981MSps. These are not the
configured hardware sampling frequency, continuous RF coverage, USB link
capacity, acquisition duty or detection probability. In particular the measured
AD full320MHz scan periods are approximately1.77s/2.61s; high FFT calculation
counts alone do not establish Spectrozir-class scan speed. Comparing different
frequency spans, physicalN, detectors or devices is not a like-for-like benchmark.

Own-process total CPU145.47%/139.14% of one logical core corresponds to
9.09%/8.70% normalized across16logical CPUs. RSS211533824→215445504 and
210317312→213979136bytes, peaks216641536/215777280. Minute growth approximately
3.7MiB/3.5MiB is neither leak proof nor long-run stability proof. Private commit
and post-cleanup memory are also retained. Queue pending sampled maxima2/3 of8
at1Hz are not guaranteed transient maxima. Native scalar query overhead was
measured (largest observed HF query~5.22/5.02ms); instrumentation is not free.

Observed host/native input, analytical FFT-drop, refill/gapped-line and worker
failure counters stayed zero in the available native scopes. Hardware overrun
and RF continuity cannot be inferred from this. Presentation coalescing is
separate: RTL native final-frame supersession increased58864/58852; common UI
queue latest supersession1376/952 and terminal supersession388/255, stale0.
These intentionally skipped presentation snapshots are not analytical FFT loss.

The initial uninstrumented attempt failed after two tinySA traces at the typed
`owner_poll / instrument_failure / version / identity` boundary; first exact
cause remains unknown, and it is not relabeled PASS. A bounded diagnostic using
the existing inert factory seam observes the same already-read version bytes,
retains redacted comparison hashes, and returns identical bytes to the unchanged
inherited guard. No extra serial command/open/reset, identity weakening or retry
inside the owner is introduced. Its first measurement attempt exposed a private
observer error (nonexistent RTL snapshot/counter API), not a product defect;
that failure is retained. Corrected interface mocks cover the actual same-owned
RTL native scalar control. Corrected complete windows retained59/60 equal
version checks, zero observer failures, no mismatch and no SDK ERROR lines;
this does not prove the earlier identity failure or historical SDK-9 was fixed.

Nine scalar/interface tests, six offline audit/negative tests and eight existing
version-observer mock tests passed. Offline audits independently recompute all
rates from122 observations and verify source/runtime/native/Qt attribution,
progressive partial-before-same-scan-complete witnesses, terminal cleanup and
four original1920×1080/DPR1 PNG hashes/frame receipts. Captures were taken before
warmup and after the steady window, not during performance measurement. Root
inspected all four. No new subagent or independent UI/release review occurred.

Captures remain physical SOURCEQt/offscreen evidence, not visible frozen-EXE
Windows DPI/DWM/paint-latency qualification. Sparse waterfall history, HF dark
horizontal strips/Y-axis autorange and occasional accepted-region HUD proximity
to the pane badge are recorded visual debt, not declared fixed. Partial spectrum
extents at capture are progressive pending portions of that scan.

All acquisition owners stopped, terminal queues drained, threads joined, graphs
closed, cooperating hardware leases released and source/native hashes preserved.
This is not RF-silence or pre-test RF-settings-restoration proof. M8/H01 is
PARTIAL, actual paint/latency H02 remains TODO and APP-07 remains PARTIAL. Next:
qualify exported HackRF analytical metrics, measure AD per-step retune/refill/DSP/
publication timing, and define the analytical-ready→actual-paint offered/disposition
ledger before optimization/latency qualification. Paired/dual-AD/Ethernet and
additional profiles, remaining faults, stability/soak, visible UI, independent
release qualification, closure and later APP08…14 remain open.

## M8-057 — HackRF Sweep analytical counters and two physical 2×2 baselines

Runtime `b39bf9631f174529bee35df38e482dabf72e0281` exports SAME native-analysis
snapshot counters for accepted CI8 payload, computed/dropped FFT, DSP-tail
samples and pending output. Optional metrics contract1 matches the native build
manifest; missing/half-paired/wrong-type versions refuse build preflight. Older
artifacts with both metadata items absent remain valid, but their new metrics
remain unknown. Factory/bridge1, schema5, FFT/Fs/quality/lifecycle/queue policy
are unchanged. No raw I/Q moved to Python and no UI product change.

One accepted firmware block contains 8187 complex CI8 samples excluding header;
only its final FFT-N tail is analyzed. One FFT yields two disjoint 5 MHz crops.
Decoded payload, DSP input, callback bytes, full sweeps, GUI admissions and
actual paint are different measures. New fields project actual native counters,
not Python estimates from blocks or callback bytes.

Exact diagnostic build APP07-HFMETRICS-20261004-B39BF96:
46/46 CTest in53.23s; frozen665 files; full serial V2 1381 total/1315PASS/66skip/
0fail-error in469.060s, trackedclean before/after, exact provenance, no deferred
compiled tests or product imports outside checkout. Packaged new-contract tests
6PASS. Native SHA8f841e98b52e17139ecacb5fb4d867f91d5ba3a516782872e07c2118cc6ef424;
EXE SHA333ba0c42453ba9f3d70855a3b3685ac7a4d538d3e5cb994c6c3a38c10e8fbfa.
Diagnostic only, not static/current promotion or independent release approval.
A later documentation commit must not relabel this build's source provenance.

Two physical sourceQt/offscreen windows, each15s warmup/60s steady/61samples:
A AD9364mini USB Sweep300..620/Fs61.44/useful36/logical4096-physical8192,
HackRF Sweep100..300/Fs20/FFT4096, tinySA LOW100..300/1001/RBW300k/device-dBm,
RTL RTBW99.5..100.5/Fs2.4/FFT4096.
B tinySA/RTL/AD9363full USB/HackRF rotation2; AD3 uses actual30.72/useful30/
logical4096-physical8192. This is fresh layout reassignment, not hot rotation or
Ethernet proof. Four original1920×1080/DPR1 images attributed by hash, geometry,
source/resource, config and producer receipts, captured outside measurement.

| Native/host metric | A | B |
|---|---:|---:|
| AD computed FFT/s |349.48|263.52|
| AD completed Sweep/s |0.566|0.383|
| HF computed FFT/s |814.27|782.04|
| HF accepted CI8 payload MSamples/s |6.666|6.403|
| HF DSP-tail MSamples/s |3.335|3.203|
| HF completed Sweep/s |40.71|39.10|
| HF GUI admissions/s |39.08|40.16|
| RTL computed FFT/s |1172.63|1172.53|
| tinySA GUI trace admissions/s |0.733|0.733|
| Own CPU whole16-logical machine % |9.30|8.58|
| Own RSS delta MiB/60s |8.29|3.33|
| Sampled max queue /capacity |2/8|2/8|

Known native input/FFT/gap error counters were zero; presentation supersession
was nonzero and separate. No device overrun counter/continuous ADC/RF-duty/
lossless/soak inference. No SDK error-line matches or tiny identity observer
failures in these two runs; historical first causes remain UNKNOWN, not fixed.
Both runs terminal0owners/workers/leaseReleased. Four screenshots root-inspected;
accepted-segment HUD may overlap pane badge, autorange/sparse history remain UI
review debt. These are SAME-native/Qt sourceUI captures, not visible frozenEXE,
WindowsDPI/DWM/actualpaint-FPS or50ms qualification.

Existing exported AD live host-stage aggregate counters were also sampled:
A approximate mean Stop0.59/configure16.07/Start47.82/framewait115.25ms.
Independent relaxed live reads do not yield exact post-Stop fences, per-step
percentiles, RF dwell, or causal separation of refill/DSP/publication. Inspect
the existing counters before adding duplicate instrumentation.

Evidence ID m8-hfmetrics-b39bf96-057. Private logs/audits/gallery retained locally.
No new subagent/model call or independent whole-backend/release review.
M8/H01 and APP07 remain PARTIAL; actualpaint/latency H02 TODO. Next M8-058:
attribute missing first-FFT/refill/DSP/publication stages, then analytical-ready
to first relevant paint-return offered/disposition ledger. No repeated baseline
without new scope. Remaining paired/dualAD/Ethernet/fault/stability/visible/release/
closure requirements and APP08..14 stay open.

## 2026-10-04 M8-058 — observed shared Pluto ingress timing

Immutable runtime source `60f59794872a36496bd01af3025c1dc59a8ab7cb`.
Five additive readonly ContinuousSweepCoordinatorMetrics fields:
`source_refill_calls`, `source_refill_wait_ns`,
`source_canonicalization_ns`, `source_inter_refill_gap_ns`,
`source_inter_refill_gap_count`.

Re-use existing PlutoDevice StreamMetrics at admitted segment snapshots,
delta-accounted across configure generations and incrementally in single-window
Sweep. One common paired input counted once, not summed per RX; configure resets
totals; stopped reads are nonconsuming. No new SDK query/timebase/RF/Fs/FFT setting,
restart, queue budget or raw-I/Q Python path. Live reads relaxed; attempts/errors/
cancellations observed there are not a complete terminal-tail or successful-block
counter. Inter-refill gaps exclude reset/reconfigure downtime; no RF duty/Pd claim.

Matched diagnostic APP07-INGRESS-20261004-60F5979:
46/46 CTest in53.62s; 665 frozen files; exact serial V2 1381 total/1315 PASS/
66 skip/0 failure-error in500.311s; clean before/after, exact provenance,
no deferred compiled tests or product imports outside checkout.
Two matching packaged binding tests passed; schema5/Qt6.11.1 unchanged.
Native SHA df50fe46d12ed8ac6027bf1875b4db81cdd819e354a012763c039bad96fd0d05.
EXE SHA 6a3a34766aa850326c0fcf2d28a0403cc9241410a26fab0fe68f2c7f0e65e0a9.
No static/current promotion; a documentation commit does not relabel the build.

Two physical sourceQt/offscreen windows, each15s warmup/60s steady/61 observations,
with unchanged explicitly selected057 profiles:
A AD9364mini USB/HackRF/tinySA/RTL canonical;
B tinySA/RTL/AD9363full USB/HackRF rotation2.
AD4 Sweep300..620/Fs61.44/useful36/logical4096-physical8192;
AD3 Sweep300..620/Fs30.72/useful30/logical4096-physical8192 (not forced18).
HackRF Sweep100..300/Fs20/FFT4096; tinySA LOW100..300/1001/actualRBW300k/
device-dBm; RTL RTBW99.5..100.5/Fs2.4/FFT4096.
Fresh layout reassignment, not hot replacement or Ethernet qualification.
The Sweep useful-window policy is not an automatic RTBW-bandwidth limit.

| Native/host metric | A: AD4 canonical | B: AD3 rotation2 |
|---|---:|---:|
| AD computed FFT/s |350.330|262.282|
| AD completed Sweep/s |0.566|0.383|
| Observed refill wait mean, ms/attempt |33.977|54.153|
| Observed canonicalization mean, ms/attempt |1.296|1.295|
| Observed inter-refill gap mean, ms/gap |0.002436|0.002237|
| HF computed FFT/s |813.807|781.483|
| HF completed Sweep/s |40.690|39.067|
| HF GUI admissions/s |38.043|35.171|
| RTL computed FFT/s |1171.695|1171.691|
| RTL GUI admissions/s |37.327|35.970|
| tinySA GUI trace admissions/s |0.733|0.749|
| Own CPU /16-logical whole-machine % |9.118|9.463|
| Own RSS delta MiB/60s |12.043|2.965|
| Sampled presentation queue /capacity |3/8|3/8|

Refill attempts1002/750, gap counts668/500; host wall timers, not RF timestamps
or analytical-ready. Existing coordinator approximate stage delta-means A/B ms:
Stop0.600/0.620, configure16.256/15.659, Start45.965/45.846,
framewait116.488/176.977. Independent live reads are not exact postStop fences,
per-step percentiles or causal DSP/publication separation. Refill wait much
greater than canonicalization suggests investigating first-buffer/transport/
start lifecycle before assuming FFT arithmetic is the principal latency cost.

Native HF/RTL FFT rates within about0.3% of057, not a claimed speedup.
B GUI admissions about12% lower than057 (HF40.16→35.17, RTL40.98→35.97/s)
with higher ownCPU; exact cause UNKNOWN. Do not select a better unchanged rerun
or interpret admissions as paint FPS. Known analytical/input error/drop counters
zero, supersession separate; deviceoverrun/continuousADC/RFduty unknown.
No lossless, sustained61.44USB or leak-proof claim from one minute.
No SDK error-line matches/tiny observer mismatch in these runs; historical causes
remain UNKNOWN, not fixed. Both runs terminal0owners/workers/leaseReleased.

Four original1920×1080/DPR1 screenshots before/after measurement root-inspected,
hash/geometry/source receipts preserved. SAME packaged native/matchingQt sourceUI,
**not visible frozenEXE/DPI/DWM/50ms qualification**. Visible UI debt remains:
accepted-segment caption overlap, denseRTL detail, sparse waterfall fill and
autorange smoothness; stills do not prove temporal flicker.

Evidence `m8-ingress-60f5979-058`; private logs/audits/gallery retained locally.
Reused gpt-6-sol/high UI-only read-only paint-contract review058; reused
gpt-6-luna/high isolated UI-only relevantSpectrum cadence059 candidate is not
included in this runtime/build/screens. Root owns native/backend/RX/build.
No independent whole-backend/release approval. APP07/M8/H01 PARTIAL, H02 TODO.
Next narrow UI059 review/integration and root native pre-coalescing ready-ID/
validatedclock/disposition, then distinct Waterfall/Visual paint identity.
Remaining paired/dualAD/Ethernet/fault/soak/visible/release/closure and APP08..14
stay open. Do not restart completed058 qualifications.

## M8-059 — relevant Spectrum paint cadence, software PASS / physical PARTIAL

Runtime root7937a61 + test-only8f8ce5aa6dca4689c5102007d7eaaebf3fb75ad4
integrates independently scoped-reviewed UI candidate2e1604e +4ca9dd1.
Only UI V2 paint_cadence.py/scene.py and the existing regression test change.
Displayed CURRENT scalar key, visible inner curve, clipped viewport/event region
must agree; cadence counts only after the enclosing base paintEvent returns.
A fresh unpainted key plus an actual disjoint1x1 repaint stays uncounted until
a later relevant paint. No native/DSP/RF/FsFFT/gap/quality/queue/control changes.

Root focused81PASS/3subtests13.00s/Ruff/diff PASS. New matching diagnostic build:
46/46CTest55.27s/665frozen/runtime/source hashes verified; serial full V2
1384total/1318PASS/66skip/0fail490.630s/exacttrue/cleanbeforeafter/deferred[]/
outside[]. Four historical NaN-validation warnings retained.
Native unchangeddf50fe46d12ed8ac6027bf1875b4db81cdd819e354a012763c039bad96fd0d05;
EXEd3e7dbf0f4dc0564e5ac82c247cd50cefde22701ca944c70a7595b388b55c2b2.
APP07-PAINT-20261004-8F8CE5A is diagnostic, not promoted static/current/release.

Matching real A four-source sourceQt window PASS: AD4USB canonical/HFUSB/
tinyUSB/RTLUSB,15swarmup+60ssteady/61samples/progressiveSweep/original2FHDPNG.
Private bounded scalar observer counts SAME production cadence commits only:
AD5.547292/HF34.433194/tiny0.732976/RTL35.066220 per second, distinct from
GUI admissions5.563923/36.598622/0.732972/36.482013 and native FFT rates.
No per-FFT Python callback/new paint/SDK command or unsupported ready latency.
Not DWM FPS, full offered ledger, native-rate improvement or RF continuity.

Matching B AD3USB rotation2 FAIL: all4fresh initialFHDPNG and11steady samples,
then tinySA owner_poll/instrument_failure/version/identity at observation24.
Only firmware fingerprint differed, same model/device/control; normalized
second line25->26chars/rawresponse67->68bytes. Exact changed-text/transport
cause UNKNOWN; no firmware mutation, UI-causality, guard relaxation, automatic
retry or60s acceptance. Firstfailure/redacted diagnostics and originalPNG retained.
Bothruns normalfinal0resources/0workers/leaseReleased/source-nativeunchanged/
PIDsabsent; postphysical665/source integrity verified. Three originals rootviewed.
SourceQt matching packaged native/Qt NOT visibleWindowsEXE/DPI/DWM qualification.
BothPluto USB here, no Ethernet/pairedRF/hotrotation proof. Existing UIdebt remains.

Luna gpt-6-luna/high authored059 in prior isolated packet; Sol gpt-6-sol/high
independently scoped-reviewed it. This packet reuses Sol for UI-only060 design;
root integration/native/backend/RX/build/provenance. No whole release approval.
APP07/M8/H01 PARTIAL, H02 TODO for complete ready-clock/offered-disposition/
coverage/Waterfall/Visual paint ledger. No50ms/p95/p99/soak/release claim.
Next root060 analytical-ready producer receipts/clock/coverage/dispositions and
bounded tinySA mismatch investigation; do not repeat unchanged B to obtainPASS.

## M8-060 — native CPU analytical-ready producer foundation

Runtime3143fbbc1ff09b63f0d76e26331005fe3af857c4 adds optional ephemeral
AnalyticalReadyRef after CPU detector values/axis/metadata are complete and
BEFORE output eviction. Native producer instance and lifetime offer sequence
do not alias on reset/configure; RF/sample timestamp remains unchanged.
This is detector-ready, not fully stitched Sweep/density/Qt/DWM readiness.
The feature version is ANALYTICAL_READY_CONTRACT_VERSION=1; wire schema5 and
recording/replay formats remain unchanged. Historical frames carry None.

Bounded optional0..4096 scalar native ring, allocated once, no retained frame/
I/Q/NumPy or Python callback per FFT. Full ring drops NEW evidence and reports
exact lost-event count and first/last bounds, not a fabricated complete trace.
Existing hardware owners remain summary-only(capacity0); real-owner ring budget/
drain admission remains open. Added inline AD single/paired receipt backlog is
charged within existing ceilings. This is not an exhaustive RSS budget proof.
FIFO retirement refuses duplicate/foreign/out-of-order closure. Conservation:
offered = handed_off + producer_superseded + producer_cancelled + outstanding;
events_generated = drained + pending + lost. HandedOff is NOT painted.
Regressing native clocks retain original values and latch regression; no zero
clipping or assumed comparability with Python. Vendor journals remain unsupported.

Exact clean matching build47/47CTest56.19s/665frozen; packaged5binding tests
PASS0.008s; serial full V2 1384total/1318PASS/66skip/0fail489.763s/exacttrue/
cleanbeforeafter/deferred[]/outside[]. Four historical NaN warnings retained.
Native5060a085be18f721312a361e89c66ebb8d1a6c7d1cc712620bf2141969b9cd7e;
EXE274874bce44648ea89c6913da0abe275fcc2b2ffbe4bd02e2b4785ee36045938.
APP07-READY-20261004-3143FBB is diagnostic, not static/current/release promoted.
Candidate binding failures were test constructor/enum/exception mismatches;
corrected without changing existing product guards and retained as diagnostics.

Root native/backend/build/testing only; no subagent/model calls or UI changes
in this increment. Independent backend/release approval remains OPEN. No new
physical RX,2x2 or screenshots;059 gallery remains059 evidence, not3143 HIL.
APP07/M8/H01 PARTIAL/H02TODO/fullAPP00..14 retained. Next measured native-host
clock bridge, budgeted actual-owner journals, validated process/resource/RX/run/
epoch typed adapters, downstream dispositions, independent Sweep/density offers
and coverage, then dedicated UI terminal implementation/review. No50ms/p95/p99/
WindowsDPI/DWM/soak/RFduty/lossless/dualRF/Ethernet qualification from these tests.

## M8-061 — measured native-host ordering bridge and actual frame adapters

Implementation8238294 plus exact corrections1064416639b398ec0118d6cc44509c916e67e015
carry immutable DetectorReadyReceipt through the actual Pluto single/paired,
HackRF and RTL reduced-frame converters. The original native ready timestamp,
producer instance, offer sequence and generation are preserved, not restamped
at polling. Native C++ bytes, DSP/Fs/FFT/gain/queues/budgets/quality/epoch/time
and recording wire remain unchanged; no product UI edits in this packet.

Native steady-clock samples are bracketed by host perf-counter observations.
Only strict measured ordering produces conservative host bounds; no assumed
same epoch, guessed offset, rate interpolation or extrapolation. Outside/equal
samples and observed regression/probe failure remain unknown. Sixty-four
scalar probes per owner, sampled once per existing poll; no per-FFT Python
callback, frame/IQ retention or cached-read probe. RTL probes use SAME selected
native module as its actual owned control, not a catalog/default loader.
Stage/Preview/construction remain inert; only explicit Start initializes probes.
These adapter/process scopes are not independently attested resource/capture/
RX/run/activation identities and do not replace outer PaneDelivery admission.

Initial exacte997 full V2 had9 failures at synthetic numerical fixtures crossing
real native receipts through fake modules. Failures retained. Corrected fixtures
use SAME selected module inertly and remove receipt when fabricating another
source/generation; numerical/marker assertions and product refusals preserved.
Own receipt text canonicality tightened. Corrected exact106 freshbuild:
47/47CTest54.58s/665frozen/shared9DLL/ONEUSB/offscreen gates PASS;
packaged6bindingsPASS0.213s; serial fullV2 1384total/1318PASS/66skip/0fail-error
494.595s/exacttrue/cleanbeforeafter/deferred[]/outside[].
Four historical NaN warnings retained. Native unchanged
5060a085be18f721312a361e89c66ebb8d1a6c7d1cc712620bf2141969b9cd7e;
EXE0ebfb25a79edb9193ef1fcda8e9e9dfce8b146c9b0a888bd7a4fbe5121b34fbb.
APP07-BRIDGE-20261004-1064416-R1 diagnostic, NOT static/current/release promoted.
Do not relabel e997 qualification or later documentation commit as106 build.

Fresh five-source discovery and TWO sequential actual four-source 2x2 tests:
A canonical AD9364USB/HackRF/tinySA/RTL;
B rotated tinySA/RTL/AD9363USB/HackRF.
Both15swarmup+60ssteady/61samples/progressiveSweep/normalStopClose PASS.
AD4 Sweep300..620MHz Fs61.44/useful36/logicalN4096/physical8192;
AD3 same range explicit admitted lowerFs30.72/useful30/logical4096/physical8192,
NOT fallback or fixed18MHz policy. HF Sweep100..300/Fs20/FFT4096;
tiny LOW100..300/1001points/manualRBW300k/devicecalibrateddBm;
RTL RTBW99.5..100.5/Fs2.4/FFT4096. USB here, not Ethernet/pairedRF/hotrotation.

Native computedFFT/s A:AD350.412/HF813.999/RTL1171.972;
B:AD262.437/HF781.679/RTL1172.253. Separate SAME production distinct relevant
Spectrum paint-return commits/s A:AD5.562/HF35.787/tiny0.733/RTL36.554;
B:tiny0.733/RTL36.808/AD4.166/HF35.692. Not DWM FPS, allFFT LPS or latency.
CPU machine9.296%/8.854% on16logical CPUs; sampled queue max3 both.
Four original1920x1080/DPR1 PNG root-viewed, sourceQt with matching native/Qt,
NOT visible frozen WindowsEXE/DPI/DWM acceptance. Slow tinySA is expected;
short run and partially populated histories are not soak/leak acceptance.

Eight logged RTL RTBW ready receipts match source/gen/epoch/session/PID and
strict measured probe bounds. Host intervals20.2382..21.5965ms are uncertainty,
NOT ready-to-paint latency. Stitched AD/HF Sweep and tinySA correctly have no
detector-ready sidecar; full Sweep/density producer coverage remains OPEN.
Bounded same-read tinySA differing-byte observer adds no serial command/retry
or identity weakening. A59/B60 checks, no mismatch/observererror. Historical059
failure cause remains UNKNOWN, not claimed fixed. No new SDK-9 lines in these
two logs; historical SDK firstcause also remains UNKNOWN.
Both runs terminal0resources/workers/leaseReleased/PIDsabsent; postRX exact106
source snapshot and all665 frozen inventory PASS. All build/gate/RX terminal.

Root backend/build/RX/evidence only; no new subagents/model calls061.
Prior Sol UI-only design is not this backend/release independent approval.
APP07/M8/H01 PARTIAL, H02 TODO/full APP00..14 retained.
NEXT budgeted actual-owner journal/drain/fullouter capture-resource-RX-session-
run-activation attribution/downstream dispositions, independent Sweep/density
readiness and coverage, then dedicated UI layer terminals. No50ms/p95/p99,
RFduty/Pd/lossless, sustained/soak, WindowsDPI/DWM, dualRF/Ethernet or whole
independent release qualification follows from this bounded packet.

## M8-062 — concurrent actual-owner analytical journals

Exact runtime source: d37a92862899d6971f227340934849a393ccfe91.
Pluto single/paired, HackRF runtime/acquisition/DSP and RTL expose bounded
scalar analytical-event drains on their SAME existing native owners.
No second hardware opener, raw I/Q transfer, per-FFT Python callback or UI edit.

Offer/retire/summary are protected by a short journal mutex. A drain allocates
its finite batch outside that producer lock and atomically returns events plus
the post-drain lifetime summary. A separate drain mutex serializes native drain
allocations. Capacity and positive drain batches are limited to4096; zero batch
means currently pending in the admitted ring. Ring plus one native drain payload
is charged to existing component/paired aggregate budgets. Caller-retained
Python objects, other batches, allocator overhead and process RSS are not
exhaustively covered. Default owner capacity remains0 until the host consumer
is integrated; old profiles/Fs/FFT/backend policy and acquisition are preserved.

Exact conservation at each returned summary:
offered = handed_off + producer_superseded + producer_cancelled + outstanding;
events_generated = events_drained + events_pending + events_lost.
A full/disabled journal drops NEW evidence with explicit loss accounting,
not raw input or FFT loss. HandedOff means native output-queue handoff,
NOT a fulfilled pane obligation or paint. The original ready time and producer
lifetime remain unchanged. Unsupported vendor evidence stays unsupported.

Pluto reads require exact admitted RX1/RX2; BOTH, absent or unconfigured channels
refuse. Paired rings/producers remain distinct under one shared owner and common
capacity admission. HF and RTL drains perform no SDK, Start, Stop or retuning.
Final native events remain readable after Stop, before owner release.
Append-only binding contract OWNER_ANALYTICAL_READY_CONTRACT_VERSION=1;
spectrum wire schema5 and recording formats unchanged.

Candidate native47/47 and concurrent10koffers/20kevents conservation PASS.
Initial binding failure was only the test's wrong public class name; corrected,
product guards unchanged. Exact matching diagnostic build47/47CTest54.34s,
665frozen/runtime/source/offscreen gates PASS. AFTER freeze serial fullV2
1384total/1318PASS/66skip/0fail-error489.109s, exacttrue/cleanbeforeafter,
deferred[]/outside[]. Four historical NaN warnings retained. Separate matching
packaged8bindings PASS0.208s. All build/gate handles terminal; no physical RX.
Native d80e7a9dd900bc9fe4a6d99c4630d96f29210d7e6f1f464e8df3cc98a226230e;
EXE2713214365061672d9f1167dae869be1456a448c85a916d671a8511033509139.
APP07-OWNER-20261004-D37A928-R0 is diagnostic, NOT static/current/release promoted.
Later documentation commits must not relabel this exact runtime/build.

APP07/M8/H01 remain PARTIAL; H02 TODO. This is the native prerequisite, not
completion of M8-062. Automatic budgeted service drains and preserved terminal
journals, full validated process/resource/capture/RX/session/run/activation
attribution, exact downstream dispositions, independent stitched Sweep/density
readiness/coverage, then per-layer UI terminals remain mandatory. New mutex
overhead and actual acquisition/paint latency are not physically qualified here.
Prior061 physical2x2 gallery remains061 evidence, never d37 hardware acceptance.
Dependent physical qualification needs refresh after relevant native changes;
historical successful evidence is retained, not silently transferred or erased.
No new subagents/model calls062; previous UI-only review is not current native
or release approval. No50ms, DWM/DPI, RF duty/Pd, lossless, Ethernet/dualRF,
sustained/soak or independent whole-release acceptance follows from this packet.

## M8-062 — actual automatic service journal consumers and fresh 2x2 evidence

Exact runtime c2259d45b41895278a6516b4fdc9a8c9d918c209; test-only terminal
observer fixture ba48fcb124254785f8ba5f54bb23a6461a573ce3. Never relabel the
c2259 source/build as the later fixture or documentation HEAD.
UI V2 only; no UI product edits in this backend increment, no second SDR opener.

Typed immutable scalar domain snapshots validate native conservation, producer,
generation, sequence, original ready time and declared loss. SAME supported
CPU/Auto service profiles now explicitly admit4096event journals; default
native configuration remains0 and unsupported vendors are not forced to CPU.
Existing Pluto single/paired, HackRF coordinator and RTL workers drain at most
256events per tick. Cached public reads never drain or access hardware.
Host scalar reservation1MiB per chain/4retained terminal windows is separate
from native existing aggregate ceilings, not a whole-process RSS guarantee.
Malformed or failed readers latch INCOMPLETE, not fake zero counters or changed
hardware lifecycle. Stop terminal capture occurs after acknowledged native
Stop/join and before owner release; final pending/outstanding must be zero.
The host clock/process/source/RX/session/owner-run scope is not yet the complete
physical-resource/capture/run/activation admission and pane-disposition ledger.
HandedOff means native output, not painted; finite host eviction stays explicit.

107focusedPASS13.297s/Ruff11/mypy8/compile11. Matching diagnostic build47/47CTest
53.65s/665frozen/runtime/source gates PASS. Initial fullV2 retained2subtesterrors
in an old SimpleNamespace terminal observer missing the new journal hook.
TESTONLYba48 corrects that fixture/order; unchanged runtime serialR1:
1384total/1318PASS/66skip/0fail-error498.488s/exacttrue/cleanbeforeafter,
deferred[]/outside[]; four historical NaN warnings retained.
Native d80e7a9dd900bc9fe4a6d99c4630d96f29210d7e6f1f464e8df3cc98a226230e;
EXE be1244cad955e667f38303cd0489fa7277bba3d5a727d865b711b9a41ac53573.
APP07-HOSTJOURNAL-20261004-C2259D4-R0 remains diagnostic, not static/current.

Fresh sequential physical4source tests, each15s warmup/60s steady/61samples:
A AD9364USB/HackRF/tinySA/RTL canonical; B tinySA/RTL/AD9363USB/HackRF rotation2.
AD4 Sweep300..620MHz/Fs61.44/useful36/logicalN4096/physicalFFT8192;
AD3 same range/explicitFs30.72/useful30/N4096/FFT8192, not fixed18 or fallback.
HF Sweep100..300/Fs20/FFT4096; tiny100..300/1001points/manualRBW300k/device-dBm;
RTL RTBW99.5..100.5/Fs2.4/FFT4096. Both AD tests are USB, not Ethernet.
Computed FFT/s: AD349.44/263.44, HF814.18/781.31, RTL1172.36/1172.02.
Complete Sweep/s: AD0.566/0.383, HF40.71/39.07; tiny GUI admissions0.733/0.750/s.
FFT, completeSweep, GUIadmission and relevant Qtpaint-return are distinct rates.

Actual automaticRTL terminal journal A91663offers/183326events, B92767/
185534; nativepending/outstanding/loss/regression0. Hostevictions183294/185518
mean a finite last-window, NOT complete history or lossless reception.
tinySA59/60same-read version checks no mismatch; historical059causeUNKNOWN,
not claimed fixed. RTL startupPLL/directsampling warnings retained.
Four original1920x1080DPR1 PNG root-reviewed, sourceQt with matching packaged
Qt/native, not visible frozenEXE/DPI/DWM evidence. Partial sweep and accumulating
history are visible; slow tinySA is expected. No actualpaint latency/50ms claim.
A/B terminalStop/drain/join/close0owners-workers/leaseReleased/PIDsabsent;
postRX source snapshot and all665frozen inventory PASS.

APP07/M8/H01 PARTIAL/H02 TODO. NEXT full typed outer attribution and all-offers
downstream dispositions, separate stitchedSweep/density coverage, then dedicated
UI per-layer terminals/review. Ethernet/pairedRF/fault/soak/visibleWindowsDPI/
DWM/independentwhole-release acceptance remains open. Root only/no new
subagents or model calls; historical UI-only approval is not backend approval.

## M8-063 — exact RTBW owner-offer to pane namespace and refreshed gallery

Exact runtime 1d6ac718d7297ef1eaa289d8090a0c7e1f2a3a3a; later doc-only commits must not relabel this source/build.
SAME actual native journal authenticates producer and offer before a ready receipt gets owner_run_id. Legacy/incomplete/unsupported evidence stays unknown without stopping measurement or inventing counters/time. Cached application access uses only the SAME dispatched owner; no native/SDK competing drain or second SDR open.

Typed immutable PaneAnalyticalIdentity retains the exact ready receipt and owner clock/process/run/source/session/generation/epoch, plus resource/capture/endpoint/pane/hostrun/activation. Active admission checks ordered endpoint-producer identities and paired common owner lifetime. Stale/foreign owner, producer or offer regression cannot acquire a fresh pane namespace. Distinct panes may reference one offer; fanout is not extra FFTs or physical streams. At most two endpoint receipt scalars are retained. No UI/C++/DSP/Fs/FFT/gain/cadence/quality/budget change.

Focused103PASS4explicit compiled-lane skip; separate14matchingcompiled PASS; 14owner testsPASS. Ruff17/mypy14/compile17/diff PASS. Test fixture failures retained; existing safety guards not weakened. Exact diagnostic build47/47CTest55.15s/665frozen/runtime/source PASS. ONE after-freeze serial fullV2:1384total/1318PASS/66skip/0fail-error487.647s, exacttrue/cleanbeforeafter/deferred[]/outside[]; four historical NaN warnings retained.
EXE3d55b192baf964193788a50edeabe30eca68b82e8f75e217b4f4c27a08f5695d; native d80e7a9dd900bc9fe4a6d99c4630d96f29210d7e6f1f464e8df3cc98a226230e;
source CONTENT41a43b27df9461fcd25dadb2068da1969a3b0b1dab02c36b37b3fad7a4addc11 / FILE289fd35f04ad91e17e082bf916400f49d79fd8442a9816add33c22420cf4aa29.
APP07-OUTERSCOPE-20261004-1D6AC71-R0 is diagnostic, not static/current promoted.

Fresh sequential physical four-source tests, each15s warmup/60s steady/61samples:
A AD9364USB/HackRF/tinySA/RTL canonical; B tinySA/RTL/AD9363USB/HackRF rotation2. Same explicit profiles as062: AD4 Fs61.44/useful36MHz, AD3 Fs30.72/useful30MHz, logicalN4096/physicalFFT8192; HF Fs20/FFT4096; tiny100..300MHz/1001points/manualRBW300k/device-dBm; RTL RTBW99.5..100.5MHz/Fs2.4/FFT4096. Not Ethernet or fixed18MHz. Sweep36MHz restriction is not RTBW full-bandwidth restriction.
ComputedFFT/s: AD350.51/263.32, HF813.88/781.46, RTL1172.02/1171.93; completeSweep/s AD0.566/0.383, HF40.69/39.07; tinyUIadmissions0.733/0.733. Distinct rates, not DWM FPS/latency. Different profiles do not isolate a rotation effect.

Bounded read-only observer after SAME committed deliveries: RTL3541/3585 exact qualified namespaces, zero unknown or observererrors; no perFFT hook/SDK access/new opener. SAME automatic terminal journal91615/92831offers; pending/outstanding/nativeeventloss/regressions0. Host evictions183214/185630 explicit, NOT full all-offers history or lossless proof. tiny59/60same-read checks no mismatch/observererror; historical059cause still unknown. RTL SDK startup warnings retained.

Four original1920x1080DPR1 PNG root-reviewed (before/after each configuration); sourceQt with matching packaged Qt/native, not visible frozen EXE or Windows DPI/DWM evidence. All sources fresh; AD/HF progressive frames precede same-scan completion. A/B Stop/drain/join/close zero owners/workers/leaseReleased/PIDsabsent; postRX665inventory/source snapshot PASS.

APP07/M8/H01 PARTIAL; H02 TODO. This completes the bounded RTBW outer-binding prerequisite, not mandatory downstream all-offers dispositions, independent Sweep/density readiness/coverage, or per-layer UI paint terminals. Ethernet/pairedRF/fault/soak/visibleWindowsDPI/50ms/Pd/independent whole-release acceptance remain open. Root only/no new agents-modelcalls063; prior LunaUI/SolUI reviews are not current backend/release approval. Full APP00..14 goal unchanged.

## M8-064 — actual adapter packet dispositions and fresh 2x2 gallery

Exact runtime 3f7b0471323d6269910c7b1e2f24c128d81c5958; UI V2 only.
Immutable SAME-owner scalar adapter counters distinguish publication, rejection,
cancellation and latest-drain coalescing. All native handed-off offers remain
the denominator; offer gaps do not infer coalescing or RF loss. Unknown bindings,
foreign/stale producer/generation/lifetime, malformed scalar and host-budget
refusals retain explicit incomplete evidence without changing acquisition.
Existing AD single/paired, HF and RTL pollers record the actual outcome; no
perFFT callbacks, extra SDK read, new owner, raw Python IQ, C++/DSP/Fs/FFT/gain/
cadence or queue-budget change. Cached reads and terminal retention stay bounded
under the existing 1MiB/chain scalar reservation. Native terminal complete is
not adapter/pane/paint complete. Unsupported evidence is not fabricated zero.

99focused source/mock tests and15matching packaged compiled/mock tests PASS;
Ruff8/mypy5/compile8. Matching diagnostic build47/47CTest54.15s/665frozen/runtime/
source/ONEUSB/offscreen gates PASS. ONE serial fullV2 AFTERfreeze1384total/
1318PASS/66skip/0fail-error498.171s/exacttrue/cleanbeforeafter/deferred[]/
outside[]; four historical NaN warnings retained. Initial mypy enum accesses,
one detector-mismatched test fixture and nonexistent test-module loader failure
were retained and corrected; no product admission guard was weakened.
Native d80e7a9dd900bc9fe4a6d99c4630d96f29210d7e6f1f464e8df3cc98a226230e;
EXE2b02c1c9e3ade6bd463fb23da2bda299a448e77a05ba2ec855327efc520161c3.
APP07-ADAPTER-20261004-3F7B047-R0 is diagnostic, not static/current promoted.

Fresh discovery found twoADUSB routes/HF/RTL/tiny. Two sequential physical
2x2 runs, each15s warmup/60s steady/61samples: A AD4USB/HF/tiny/RTL canonical;
B tiny/RTL/AD3USB/HF rotation2. Explicit AD4Fs61.44/useful36MHz and
AD3Fs30.72/useful30MHz/logicalN4096, HFFs20/FFT4096, tiny100..300MHz/
1001points/LOW/manualRBW300k/device-dBm, RTL99.5..100.5/Fs2.4/FFT4096.
Sweep36MHz restriction is not RTBW full-bandwidth restriction; AD3 smaller
profile is not fixed18MHz. USB is not an Ethernet witness.

A bounded all-source freshness confirmed. B initial fresh4/progressiveAD-HF
and independentRTL/cleanup confirmed, but tiny admissions14->20 then unchanged
through samples10..60; visible frame age53s. Root does NOT qualify continuous
four-source freshness B merely because the exploratory runner exits0. Cause
UNKNOWN, not asserted normal slow scan/UI bug/rotation causality; no unchanged
rerun selecting a green result. tiny same-read version checks59/21 without
mismatch/observererrors. Next diagnosis requires cached worker/command/deadline
first-cause before Stop, not competing serial reads or shorter test profiles.
RTL startup warnings retained. Four original FHD/DPR1 images inspected: source
Qt with matching packagednative/Qt, not desktop frozenEXE/DPI/DWM acceptance.
AD progress label may overlap pane badge near plot right edge; UI follow-up.

Actual adapter A3469published+10407coalesced, B3522+10566, zero rejected/cancelled/
unqualified/bindingfailures. Native terminal handoffs91719/92895; explicit
unclassified77843/78807. Native presentation supersession is a separate actual
boundary (rtl_runtime.cpp), not RF loss; full pane/queue/layer-paint ledger
remains open. A/B normalStop/drain/join/close0owners-workers/leaseReleased/
originalPythonprocesses gone/postRX665inventory-sourcePASS.

APP07/M8/H01 PARTIAL/H02TODO. Mandatory062 step6 remaining owner/pane/queue/paint
dispositions, step7 Sweep/density readiness/coverage, step8 dedicatedUI terminals/
review still required. No50ms/lossless/dutyPd/Ethernet/pairedRF/soak/release proof.
Rootonly/no newagent-modelcalls064; prior UI reviews not current backend/release
approval. FullAPP00..14 goal unchanged; staticEXE/firewall/main dirtyproduct
preserved, public whitelist only. Later documentation HEAD must not relabel
3f7b047 source/build/physical evidence.


## M8-065 — native owner presentation and cadence dispositions

Exact runtime 5bcf2fe8472239acfce4b64d2b9d8400ea4fd871, UI V2 only.
The SAME native producer journal now contains bounded scalar counts for actual
forwarding, queue supersession, latest-drain coalescing, cancellation and
cadence suppression in AD single/paired, HackRF and RTL owner paths. Per-chain
paired counters preserve ONE device/context/acquisition owner. These are actual
decisions, not inference from sequence gaps or RF/input loss. Unsupported
evidence remains unavailable; host validation rejects malformed/regressing
counters and leaves unclassified handoffs explicit. No per-FFT Python callback,
raw I/Q transfer, second event ring, larger queue/budget, RF/FFT/cadence change.

Matching diagnostic build:47/47CTest56.19s/665frozen/runtime/source/ONEUSB/
offscreen gates PASS. Serial fullV2:1384total/1318PASS/66skip/0fail492.357s,
exact provenance and tracked-clean source before/after. Matching packaged
compiled/mock focused tests58PASS6.216s. Interrupted first gate has no final
result and is not PASS; its partial log remains retained.
Native ca6ec0535fbb9ee3bdfd0f7446614be1c147dd3f27585cb49bc767254c852554;
EXE3460dd77b4dca93d1bff96da9a1913e71752487880d393620f05fa2064d717ee.
APP07-OWNERQUEUE-20261004-5BCF2FE-R0 is diagnostic, not static/current promoted.

Scalar conservation is not an exhaustive per-ID owner/pane/paint ledger.
Ordinary Stop still retains drainable queued frames; explicit final-release
disposal, pane obligations, Sweep/density readiness and UI terminals remain
mandatory. No new physical065 test: previous064 gallery is historical, not
current-source hardware evidence; changed HIL requirements return to VERIFY.
tinySA rotated stale-frame first cause remains UNKNOWN before next HIL.
End-of-tests galleries must include actual 2x2 configurations, source/mode/RF/
transport captions, build identity, freshness observations and honest partial/
error states. Source Qt captures are not visible packaged Windows EXE/DPI/DWM.
APP07/M8 PARTIAL/H02TODO; fullAPP00..14 goal unchanged. Root only, no new agents
or current independent backend/release review. No main product/static/firewall
change. Later documentation HEAD must not relabel exact5bcf build evidence.


## M8-066 — explicit final native presentation release

Exact runtime 8bc9e944f780604d223a15d17c4cd84fc5b55c03, UI V2 only.
AD single/paired, HackRF and RTL owners now expose a strict final presentation
release after acquisition/processing join and acknowledged Stop. It cancels
actual queued frame references once, before final journal capture and owner
release. Ordinary native Stop still permits post-Stop draining. Running,
incomplete Stop and ambiguous close refuse disposal; host failure is retained
as INCOMPLETE, not zero or a successful final receipt. Paired receivers use the
SAME owner/context; per-chain capture does not duplicate physical acquisition.
Legacy unsupported protocol remains unavailable. No RF/Fs/FFT/gain/cadence,
quality/time, native queue budget or raw-I/Q Python boundary changes.

Exact diagnostic build APP07-OWNERRELEASE-20261004-8BC9E94-R0:47/47 native tests,
665 frozen files/runtime/source/shared DLL/ONEUSB/offscreen gates verified.
Serial full V2:1384 total/1318 PASS/66 skip/0 fail,492.520s; tracked source clean
before/after, exact provenance. Matching packaged compiled/mock tests65 PASS
6.202s after that gate; postgate inventory and posttest source snapshot verified.
Initial candidate test-only metrics field compile failure retained; corrected
without product guard reduction. Candidate duration56.00s is NOT exact build
CTest duration (latter not captured). Four historical NaN warnings retained.
Native1f715cc9737d8731f07d6a71a1a8dd1442e08c81c8d0706dd6378652ec4a84c7;
EXEf8e2b319a9bef14889b751307b88d2225603c56f949329458e7846cb171073f0.
Diagnostic package not static/current promoted. Later documentation commit must
not relabel this source/build. No current066 physical test or screenshot.

Still mandatory: original-ID owner dispositions with explicit bounded event
loss/coverage, pane/queue obligations, independent Sweep/density readiness,
UI layer terminal evidence and independent review. Scalar conservation and
zero queued residual are NOT exhaustive per-ID or paint acceptance. Before new
HIL diagnose tinySA stale state from SAME cached worker/command/read progress;
no competing serial read or unchanged green-selecting retry. At end of physical
tests deliver original 2x2 galleries for exercised configurations/rotations with
source, mode, RF range, actual transport, build, freshness and partial/error
captions. Historical source Qt images are not current packaged EXE/DPI/DWM proof.
APP07/M8 PARTIAL/H02 TODO; full APP00..14 unchanged. Root only this increment,
no new subagent/current independent backend-release review. Main dirty product,
static EXE/firewall preserved; public whitelist only. No whole-release claim.


## M8-067 — original-ID native owner custody evidence

Exact runtime d67b3ee99e662dd1774800bce6bafa40c44d27db, UI V2 only.
The five actual owner dispositions now append ORIGINAL ready refs to the SAME
bounded native event ring. No second ring, unbounded identity registry, arrays,
raw I/Q transfer or producer-rate Python callback. Owner event protocol2 adds
explicit generated-event conservation:2*offered-outstanding+owner dispositions.
Ready ref/presentation/release contracts stay1. Counter-space reservation allows
three events per offer before exhaustion; full/disabled rings declare exact
new-evidence loss/bounds. Invalid/excess owner decisions do not append events.
Internal queue hooks are not an exhaustive native per-ID dedup registry.

Host reads known v1/v2 schemas explicitly; v2 requires original event kinds,
strict native event count and matching presentation totals. Unknown/missing/
malformed evidence refuses rather than falling back to fabricated zero.
Bounded custody audit retains at most256 scalar original refs/states, charged
inside the existing1MiB host reservation including index storage. Duplicate
terminal cannot settle a different offer; ready time/generation/clock identity
must match the original. Native all-offers counters remain denominator.
Only small stopped runs with complete retained offer/retirement/owner evidence,
no native loss, window eviction, overflow, accounting/drain/release failure can
claim complete NATIVE ID custody. Larger/incomplete runs remain explicit. This
is not adapter/pane/queue/paint, RF duty/loss or global50ms acceptance.
No RF/Fs/FFT/gain/cadence/quality-time/native queue ceiling changes.

Exact diagnostic APP07-OWNERIDS-20261005-D67B3EE-R0:47/47CTest54.24s/665frozen/
source/runtime/sharedDLL/ONEUSB/offscreen gates verified. Serial fullUIV2:
1384total/1318PASS/66skip/0fail493.203s, exact source/native/cleanbeforeafter.
Additional matching packaged compiled/mock/root tests76PASS6.697s aftergate,
source snapshot unchanged. Candidate47CTest56.39s/76compiled6.521s/24Qt0.576s/
Ruff7mypy3compile7 PASS retained separately. First pure legacy test fixture
error corrected explicit v1 defaults, not v2 fallback or product guard change.
Historical warnings retained. Native055dc369341754dd8f11a57fc25d5441bbd63e0717567d01cf94989816459104;
EXE24ea72f219f310548d602fb92fd26dbf1b3dacd589c1d5129e4f22ce36ecadf3.
Diagnostic not static/current promoted. Later doc SHA must not relabel d67 build.

Still mandatory: actual pane fanout/admission/prepare/queue obligations, separate
Sweep/density ready provenance, dedicated UI layer terminals/review, cached
tinySA stale first cause before new HIL, current2x2 galleries/rotations, remaining
fault/performance/soak/visibleDPI/release qualification. No current067 physical
run or new screenshots;064 gallery historical only. End physical tests include
original2x2 source/mode/RF/actualtransport/build/freshness/partial-error captions.
APP07/M8 PARTIAL/H02TODO/fullAPP00..14 unchanged. Root only this increment,
no new agents/current independent backend-release review. Dirty main product,
static EXE/firewall unchanged; public whitelist only.
# M8-068: graph-scoped native Spectrum pane custody (root contract)

Software checkpoint: runtime `f7257d438e1d7736b7a694d981f5f2c3d95d8b9a`,
47 native CTest passes / 665 frozen inventory verified / exact serial full
UI V2 1384 total, 1318 passed, 66 skipped, zero failures (665.512 s) / 91
matching packaged-native compiled-mock/root custody tests passed (7.139 s).
Source clean before/after and unchanged native SHA256 `055dc369…` verified.
These are source/mock/offscreen software gates, not current physical/visible
performance or complete UI callback qualification. A distinct gpt-6-sol/high
UI-only read-only design review is complete; implementation and whole backend/
release review remain separate. Root retained-plot Stop policy is frozen:
confirmed Stop ends pending UI custody while preserving the last plot; later
retained repaint is not a new delivery. No new 2x2 hardware gallery in 068.

The applied resource graph now issues an immutable per-pane obligation only
after exact current measurement admission. The reference preserves the original
native Spectrum ready receipt plus owner/resource/capture/RX/pane/run/activation
binding and a fresh graph custody namespace. It does not identify new hardware.
Fanout into two panes is two deliveries of ONE native offer, not two FFTs.

The graph owns one additional declared 1 MiB host scalar component with at most
256 records/128 events/four fixed pane markers. It stores no measurement arrays
or device handles. Pending records are not evicted; capacity failure leaves the
delivery untracked without changing acquisition. Terminal/event evictions and
unknown/untracked/duplicate admissions are explicit. Existing native and paired
aggregate memory, Fs/FFT, epoch, control, queue and quality policies remain.

Routing-close Stop/retune/error cancels only unclaimed ADMITTED records of the
affected resource. Actual preparation/queue/Qt/paint owners must report their
own transitions using the exact original reference. The contract's stage enum
does NOT by itself prove those UI callbacks or visible paint. Host receipt time
is not native-ready/RF/photon time; invalid clock readings remain unknown.

This scope is native RTBW Spectrum only. Sweep, instrument trace, persistence
and waterfall require independent ready provenance and remain unqualified here.
Root unit/fake graph/queue tests are not physical acceptance or M8/H02 closure.
Dedicated UI implementation/review, frozen package/full regression, cached
tinySA first-cause diagnostics and current HIL remain required. At the end of
new HIL, provide original 2x2 screenshots of each tested configuration/rotation
with source/native/EXE provenance; do not relabel historical galleries.

## M8-069: UI V2 Spectrum delivery lifecycle

Runtime checkpoint: `4ec0060b21cda8ea40415dcddd4679e79f5ae472`.
The UI now carries the original native RTBW Spectrum delivery reference through
worker preparation, the bounded delivery queue, pane admission, CURRENT curve
installation and the return of the relevant Spectrum viewport paint call.
Each stage reports the same reference; fanout remains pane deliveries, not
additional FFTs. Unsupported/absent readiness remains unqualified.

Latest requested and actually displayed sources are separate. Asynchronous
projection is not a paint. A failed Spectrum setter before CURRENT installation
rejects its delivery; later Waterfall/chrome failure does not invalidate an
already accepted or committed Spectrum delivery. Replacement, queue refusal,
cancelled preparation and untouched failed-batch tails have explicit outcomes.

A successful Stop captures pending original references before queued Qt
confirmation. It closes only those references, including already drained queue
entries, without closing new-run deliveries. Ordinary Stop retains the last
plot; repainting retained pixels does not create another delivery receipt.
Terminal shutdown releases source arrays and prevents late projection callbacks
from restoring slots. Observer failures do not change measurement behavior.

Full regression exposed a terminal memory defect: a dynamically created Qt
paint class captured scene-bound callbacks and kept a Live snapshot reachable.
Moving callbacks to widget instance fields fixed the two unchanged memory
tests; weakening their assertions or adding only a weak timer did not fix it.
A regression test preserves the distinction between Stop-retained pixels and
terminal release. The failed earlier `22204b8` run remains separate evidence.

Verification at exact runtime `4ec0060`: root expanded 155 tests (154 passed,
one skipped, 75.488 s); independent read-only UI source review; fresh diagnostic
CPU build with 47/47 native tests (56.43 s) and 665 frozen files; one serial full
UI V2 run with 1404 tests (1338 passed, 66 skipped, zero failures, 494.516 s);
111 matching packaged-native/mock/UI tests (6.822 s). Source clean before/after,
matching native SHA256 `055dc369…`, EXE SHA256 `df327e4f…`. Existing NaN warnings
and expanded-suite Qt teardown diagnostics are retained, not suppressed.
The earlier synthetic RTL fixture failure remains an unresolved release risk;
this UI-only correction is not a native timing repair.

These are source/mock/offscreen software proofs, not DWM/display latency,
physical acquisition, DPI, sustained four-source operation or release approval.
The diagnostic build is not promoted to the static current application. Requested
agents: gpt-6-luna/high UI author and distinct gpt-6-sol/high UI reviewer; exact
runtime variants are not attested. Root performed integration/build/verification.
APP-07/M8 remains PARTIAL; H02 remains TODO. Next: same-owner tinySA cached
progress/first-cause diagnostics before new hardware rotations and original 2x2
screenshots. Sweep/density readiness, performance/soak/visible/release remain open.
## M8-070: same-owner tinySA acquisition diagnostics

Runtime checkpoint: `b5dd2a1398ae26ebaeb1da6e23d98d8be04b02b5`.
The existing retained tinySA acquisition now keeps bounded immutable scalar
progress and its first fault before cleanup. Diagnostics distinguish command
write/flush/read, readback, publication and close. The service associates these
facts with the actual configuration generation and retains terminal diagnostics
after Stop. Reading the cache performs no serial operation, SDK query or clock
read and does not open another owner.

Deadlines retain their originating clock domain: existing scan/zero integer
monotonic nanoseconds and version/settings floating monotonic seconds. Existing
loop observations are reused; missing or invalid facts remain unknown. A blocked
flush has no response deadline until the existing flush returns. Diagnostic
counter overflow marks partial coverage without changing acquisition behavior.
Earlier framing faults are preserved separately from later close errors; the
existing caller error precedence is unchanged. Cancellation is not a vendor
fault. No commands, timeouts, RF settings, point counts, RBW, DSP, queues, native
policy or UI graphics were changed.

Verification: 14 new causal tests and 147 expanded tests (6.440 s); Ruff on six
files and mypy on four files; exact diagnostic build with 47/47 native tests
(54.14 s), 665 frozen files and 577 source inputs; one serial full UI V2 run
after source freeze with 1409 tests (1343 passed, 66 skipped, zero failures,
514.503 s); matching packaged-native/mock/UI focus 111 tests (7.159 s) and
tinySA focus 147 tests (6.786 s). Source clean before/after and inventory
unchanged. Native SHA256 `055dc369...` is unchanged; EXE SHA256 `40ae6b98...`.
The diagnostic package is not promoted to the static current application.
The later documentation commit must not relabel the runtime or build source.

This establishes software diagnostics, not a fix or explanation for the earlier
rotated tinySA stale trace, physical four-source stability, RF accuracy, visible
Windows/DPI/DWM latency or release acceptance. Root performed this backend
increment without new subagent/model calls; whole independent backend/release
review remains open. APP-07/M8 is PARTIAL and H02 is TODO. Next: serialized
physical 2x2 tests with same-owner cached first-cause observations before Stop,
AD9364/AD9363 rotations and original screenshots. Separate Sweep/density
readiness, fault/performance/soak/visible/release gates remain mandatory.

## M8-072: independent native Sweep and density creation foundation

Runtime checkpoint: `3ba9fe3f6bc0451a41da8b92ca480b7d93c2074b`.
The actual core Sweep assembler and persistence accumulator can optionally
retain a separate readonly creation receipt after materializing their output.
Sweep progress, terminal lines and density snapshots are distinct variants;
detector-ready time, RF/acquisition time and assembled-output readiness are
not interchangeable. The default producers have no journal or extra clock read.

A bounded scalar journal shares a collision-free producer namespace with the
detector journal. It preserves original creation IDs, explicit evidence loss,
sticky clock regression and one serialized drain reservation. It is creation
evidence only, not queue handoff, owner authentication, pane delivery or paint.
Repeated progress previews retain the same receipt for an acquired revision;
the preview's temporary terminal representation creates no terminal event.
Density reset has a distinct accumulation identity; unknown generation stays
unqualified. Data, quality, scales, RF timestamps and cadence remain unchanged.
Frame metadata is charged in existing native/Python component budgets, with a
matching conservative density scalar reservation; ceilings are not increased.

Verification: candidate 47/47 native tests, 15 compiled tests and 72 neighboring
source/mock/offscreen tests; Ruff on seven files and mypy on four source files.
The exact matching diagnostic build passed 47/47 native tests (54.18 s), 665
frozen files and 581 source inputs. One serial full UI V2 run after freeze passed
1409 tests (1343 passed, 66 skipped, zero failures, 488.398 s), with exact clean
source/native provenance before and after. Matching packaged-native focus
passed 111 prior tests, 15 compiled tests and 72 neighbors. Initial quality/mode
fixture errors were corrected only in fixtures; existing validation was kept.

Root performed this increment without new subagent/model calls or physical RX.
The diagnostic EXE is not promoted to the static current application. This is
not hardware, visible Windows/DPI/DWM, latency, soak or release acceptance.
APP-07/M8 remains PARTIAL and H02 remains TODO. Next: actual same-owner lifetime
journals, aggregate ring/drain admission, queue and Stop dispositions, typed
product frame adapters and clock/owner authentication, then dedicated UI layer
delivery/paint integration and independent review. Whole independent backend
and release review remains open; the full application roadmap is unchanged.

## M8-073: creation journals connected to the actual FixedBand owner

Runtime checkpoint: `25d955406f95dff0f231696d4f4dac957d0dd663`.
The existing single/paired Pluto FixedBand owner now supports optional lifetime
journals for actual density snapshots and single-window Sweep lines. Each RX
and layer has a distinct native producer identity. There remains one device,
context, IIO buffer and acquisition/control lifecycle; no second hardware open
or raw-IQ Python callback was added.

The new trailing `layer_event_capacity` defaults to zero: no journal allocation
or readiness clock read. Enabled layers reserve their ring plus one native
serialized drain within the unchanged component AND paired aggregate budgets.
Validation/allocation occurs before RF configuration. Exact RX1/RX2 drains are
readonly and bounded; BOTH, unadmitted receivers, disabled layers and oversized
drains refuse explicitly. Stop/join keeps evidence drainable, while configure
creates fresh identities without starting RX. Overflow retains explicit creation
loss counts, not an invented FFT/input-loss counter. Measurement timestamps,
data, cadence, queues, Fs/FFT/gain settings and quality remain unchanged.

Verification: the exact matching diagnostic build passed 47/47 native tests
(54.42 s), including paired ownership/aggregate-budget tests, 665 frozen files
and 581 source inputs. One serial full V2 regression after freeze ran 1409 tests:
1343 passed, 66 skipped, zero failures (481.269 s), exact clean source/native
before and after. An additional 21 creation/owner/contract tests passed against
the packaged native module. Nine neighboring binding cases skipped in the
candidate check were not counted as PASS.

This is optional native owner integration, not complete product UI integration
or measured acceleration. Retuning Sweep and HackRF journals, service adapters,
owner-authenticated clocks, queue/Stop dispositions and all-layer paint evidence
remain open. No physical RX, visible Windows/DPI/DWM, latency or soak qualification
was performed, and the current static EXE was not replaced. Root performed this
increment without new subagents/model calls; independent backend/release review
remains open. APP-07/M8 is PARTIAL, H02 is TODO, and the full roadmap is unchanged.

## M8-074: retuning Sweep keeps actual creation identity across the whole plan

Runtime checkpoint: `9028ec65ef972f9733844d70d8c9925207ec5f10`.
The actual Pluto coordinator now owns an optional bounded creation journal for
the configured Sweep plan, rather than recreating it for each RF step/pass.
Paired RX keeps independent RX1/RX2 journals through assembler resets and shared
synchronization gaps. Actual intermediate and terminal frames retain their
original creation references; planning/Stage does not invent measured frames.

The one-window path passes a native-only journal into the SAME FixedBand DSP
assembler, before relay coalescing. No second device open or raw-IQ Python path
was added. Stop/disconnect retains finite evidence; explicit reconfigure creates
fresh producer identities. Typed RX drains refuse ambiguous/unselected receivers,
and journal overflow remains explicit. Journal ring plus serialized drain is
charged inside the unchanged Sweep component and shared paired/owner budgets.
The trailing capacity defaults to zero. RF settings, analytical samples,
measurement timestamps, quality and publication cadence remain unchanged.

Verification: exact diagnostic build, 47/47 native tests (55.59 s), 665 frozen
files and 581 source inputs. One serial full V2 regression after freeze passed:
1409 total, 1343 passed, 66 skipped, zero failures (471.949 s), exact clean
source/native before and after. Additional packaged checks passed for all five
coordinator scenarios, existing FixedBand owner and immutable layer admission.
These include selected RX2, paired RX, single-window and retuning lifetimes,
creation loss conservation and shared-budget refusal before RF configuration.
Initial candidate failures were retained and test setup errors corrected;
production timeout, memory, identity and RF guards were not weakened.

This is native creation instrumentation, not measured UI acceleration or
hardware/visible Windows/DPI/DWM/soak/release acceptance. HackRF density/Sweep
journals, actual service adapters/clock-owner admission/downstream dispositions
and all-layer UI V2 delivery/paint integration remain open. No physical RX,
new subagent/model calls, system mutation or static EXE promotion occurred.
APP-07/M8 remains PARTIAL, H02 remains TODO, and the full roadmap is unchanged.

## APP-07 M8-075 — HackRF density/Sweep owner creation evidence

Runtime source: `2ad49f57fe51f2f8ec0e21aeefdaf15d3388e014`.
UI V2 only. This extends the native owner integrations of M8-073/M8-074,
not their historical hardware or latency qualification.

HackRF RTBW density now uses the optional creation journal on the SAME
HackrfFixedBandDsp, reached through its acquisition and runtime sessions.
HackRF Sweep uses the SAME analysis assembler journal through its runtime
session. Readonly bounded drains preserve original creation references after
joined Stop. A fresh session gets a fresh producer identity; disabled journals
refuse explicitly rather than fabricating valid zero evidence.

Both public factories accept trailing `layer_event_capacity=0`; zero remains
disabled, so existing callers do not gain new creation-clock calls.
`HACKRF_LAYER_CREATION_CONTRACT_VERSION=1` is additive API evidence, NOT an
RF/device capability. Factory2, schema5, DSP1, persistence1 and Sweep1 are
unchanged. Production service capability gating/automatic drains are next.

Ring plus one serialized native drain are admitted before RF/SDK startup in
the existing density256MiB/Sweep128MiB component limits. No budget ceiling,
Fs/FFT/gain/cadence/quality/acquisition-time or queue policy was increased or
reduced. These reservations are not a whole-process RSS or arbitrary retained
Python-object bound. Full rings lose diagnostic events explicitly and do not
wait for UI consumption; returned frames keep their original creation receipt.

Exact diagnostic build passed 47/47 native tests (54.92s), 665 frozen files and
581 source inputs. ONE serial full V2 regression after freeze ran 1409 tests:
1343 passed, 66 skipped, zero failures/errors (468.115s), with exact source/native
and clean tracked source before/after. Matching packaged compiled/root focus
passed 30 tests (10.333s), including actual stopped mock-runtime density,
factory refusals before SDK, default-off and existing creation/owner/admission
checks. Posttest source snapshot and package inventory verified. The first
candidate runtime test used invalid60Hz persistence snapshots; its failure was
retained and ONLY the new fixture corrected to admitted30Hz. Guards unchanged.

Девлог: для HackRF подключена исходная готовность persistence и Sweep до
объединения кадров для UI; повторное чтение не создаёт новые события, Stop
сохраняет диагностику, переполнение учитывается отдельно. Это улучшение
корректности и измеримости, не измеренный процент ускорения FPS.

No physical RX, visible Windows/DPI/DWM, latency percentile, soak or full release
acceptance follows from these tests. Static/current EXE and main product code
remain unchanged; no system changes or new agents/model calls occurred.
Next: SAME-owner product service conversion/clock authentication/queue-Stop
outcomes, then dedicated UI V2 per-layer delivery and review. APP07/M8 remains
PARTIAL, H02 TODO, and the full APP00..14 goal remains unchanged.

## APP-07 M8-076 — original layer references through the UI V2 delivery path

Qualified software runtime source: `f384ec88bbc462b97006a25c5405e2e7aa6badcf`.
The main implementation commit is `4d7dca6`; the corrected source above includes
the hidden-history and test-fixture follow-up. Documentation commits must not
relabel either runtime, its native module, or its physical evidence.

The existing Pluto single/paired and HackRF service paths now authenticate and
convert their original native creation journals into the SAME resource-scoped
delivery ledger. Spectrum, Persistence density, and Sweep retain their own
original source references. RTBW Waterfall uses the original Spectrum creation
with a separate view obligation. Progressive Sweep remains visible, but only
a terminal complete/gap publication has the completed Waterfall-row obligation.

Worker preparation, fair queue admission/drain/supersession, UI admission, and
the individual canvas callbacks settle the exact original view references.
Spectrum paint cannot prove Waterfall or Persistence paint. Repainting an
unchanged item cannot mint another completed delivery. Persistence settles at
its actual image commit and relevant canvas return, including asynchronous
replacement, Clear, cancellation, and stale-worker failure. A confirmed queued
Stop clears only its captured resource/view obligations; ordinary Stop retains
the displayed measurement, while terminal layout cleanup releases source arrays.

The graph still uses its bounded 1 MiB ledger, 256 records and 128 events.
Each packet carries at most three distinct view references. No new acquisition
loop, raw-IQ Python delivery, array-copy queue, hardware owner, hidden restart,
or relaxed Fs/FFT/gain/quality/epoch/time/memory policy was introduced. Missing
diagnostic evidence does not prevent the actual measurement from being shown.
Pane counters now count view obligations, not analytical FFTs or RF samples.

A reproduced hidden-Waterfall defect was corrected: accepting a row into the
bounded history now returns history admission even when painting is disabled.
This consumes a pending Sweep visit boundary instead of carrying it into the
next update and resetting retained rows. Hidden admission creates no paint
receipt. Frozen, ordering, cadence and allocation refusals remain explicit.

Exact corrected build: 47/47 native tests (59.17s), 665 frozen files, 587 source
inputs, shared runtime hashes and ONE USB library verified. One serial full V2
gate AFTER freeze: 1422 tests, 1356 passed, 66 skipped, zero failures/errors
(487.736s); exact provenance and clean tracked source before/after.
Matching packaged-native/source focus: 62 passed (21.639s). Expanded root
regression: 549 passed (136.471s). Stricter Qt fixture/terminal regression:
103 passed and 48 subtests (13.91s). Scoped Ruff/compile checks passed;
scoped typing is not a whole-project typing claim.

The first `4d7dca6` full gate failed (two error subtests and two failures) and
was retained. The release observer mock lacked a mandatory service field;
only that fixture was corrected. A separate combined Qt run exposed deferred
deletion from preceding paint-test fixtures. Those owned fixtures now deliver
their DeferredDelete events and verify actual destruction; paint-once and
Stop-retains-pixels assertions remain unchanged. Original callback object
identity was not exhaustively traced: this is not a demonstrated vendor/product
Qt-retirement fix, and Qt exception capture was not suppressed.

Девлог: подтверждения Spectrum, Waterfall и Persistence разделены по реальным
стадиям доставки и отрисовки; сохранён прогрессивный Sweep; устранён сброс истории
скрытого водопада. Это проверенное улучшение корректности и измеримости.
Процент ускорения, FPS, DWM latency и вероятность обнаружения RF-импульса
по этим тестам не вычисляются.

UI-only implementation/testing used gpt-6-luna/high; distinct read-only UI
review used gpt-6-sol/high. Root retained backend/native, build, hardware and
provenance ownership. These reviews are not whole-backend/release approval.

Next: qualify the CURRENT physical layout AD9364 USB / HackRF / AD9363 genuine
Ethernet 1 Gbit / RTL, rotate source assignments, and collect original UI
screenshots and per-view latency evidence. tinySA support remains, but is
excluded from this current physical layout. RTL HF routing remains unverified;
two labelled antenna connectors are not two concurrent receiver channels.
Visible Windows/DPI/DWM, sustained/soak, paired RF and full release remain open.
Static/current EXE and main product source are unchanged. APP-07/M8 remains
PARTIAL, H02 TODO, full APP-00…APP-14 goal ACTIVE.

## M8-078 — reconnected RTL and current USB/Ethernet admission

The current f384ec8 build/native (documentation HEAD1477f8e at test time) was
used for serialized current physical discovery and explicit AD9363 Ethernet
selection. This is not a relabel of earlier 075/077 hardware evidence.

RTL reconnect is confirmed: MI_00 WinUSB/StatusOK, serial00000001, R820T.
A bounded normal-tuner100MHz/2.4MSps/FFT4096 run admitted19202048 samples,
computed9375 FFTs and returned763 polled fresh spectra in8.0050133s.
Stop/flush/join completed and the USB handle/lease was released.
Initialization PLL-not-locked/direct-sampling messages are retained.
These counts are not GUI FPS/LPS, latency, continuity or HF-input qualification.

Five operational catalog entries are not five physical receivers: AD9363 USB
and its discovered network alias share the observed serial. The explicit
genuine Ethernet route ip:192.168.1.54 selects that same logical device.
Current Windows Ethernet2 reports1Gbps; device-side1Gbps proof remains the
earlier077 measurement. Current capability readback caps Fs at30.72MSps.
Unknown-serial ip:pluto.local is not admitted as another independent Pluto.

The intended current2x2 is AD9364USB / HackRF / AD9363Ethernet / RTL, with
tinySA excluded only from this physical batch; tinySA backend remains.
The actual production identity guard refuses unknown-serial AD9364USB plus
stable-serial AD9363Ethernet before RF. It currently has no evidence-bound
cross-route exclusion proof for this combination. Separately, pane Stage
carries network-discovery intent but not exact URI intent; discovery still
prefers the AD9363 USB route. Network discovery alone cannot prove Ethernet RX.

Next root backend packet: typed explicit route intent through pane Stage and
the SAME graph pool, fresh USB/IP alias-exclusion evidence, negative/stale/
duplicate-identity tests and native pre-RF expected-identity checks.
No fabricated serial, USB expectation attached to an Ethernet owner, relaxed
alias guard or silent transport fallback is permitted.
Then qualify actual four-source RX, Stop/Restart isolation, per-view receipts,
rotations and original screenshots. No current four-source screenshot or PASS
was produced by this admission diagnostic.

Existing targeted admission/graph-pool/user-plan tests:51passed,
54subtests passed in1.33s. Source snapshot unchanged; no product implementation,
static EXE, driver, firmware, network, firewall or security setting changed.
No new subagent was used. APP-07/M8 remains PARTIAL, H02 TODO; full roadmap
APP-00…APP-14 remains ACTIVE. Whole release/visible UI/performance/soak remain open.

## M8-079 — explicit operational route through the current pane owner

Runtime/source/build fbb9ec8db9c06eec0d6521dd4c0b59910715e3da; earlier f384
hardware evidence remains separately scoped. The new immutable USB/IP route
intent is retained by PaneSlotDraft, Stage and the RF context. Stage selects
and confirms the exact URI through the SAME fresh product graph. Explicit IP
may be absent from USB/broadcast discovery only with a fresh stable owned
identity matching the selected logical source. Unknown unadvertised IP refuses.

The SAME NativeLive owner pins URI/source/normalized serial/USB facts and
checks them before configuration, Start, paired staging and Sweep lease.
Explicit routes have one Start candidate, not automatic USB failover.
An immutable admission also retains the exact selected object/revision;
ALL explicit routes are checked before any initial resource configuration.
Single/paired RTBW/Sweep capture owners reject a changed route selection
before capture. Shared-source conflicting routes refuse before discovery.
No additional context/owner/data pipeline, widget redesign, DSP/Fs/FFT/gain/
queue/quality/epoch/time-policy change is introduced.

Software qualification:14 new mock route cases; broad211 passed/172subtests
with five child-import failures retained, then canonical module + explicit
mock USB context/backend resolves those five (5passed9.523s). This is a runner
environment correction, not a lowered product guard. Ruff/compile13 and
scoped mypy5 pass; nine historical transitive typing errors remain outside
that scope. Matching full pipeline47/47 native tests57.18s/665 frozen files/
589 source entries; ONE serial-after-freeze UI V2 gate1436total/1370passed/
66skipped/0failure-error494.271s, exact source/native/clean-before-after true.
Historical NaN warnings retained. This is not visible Windows qualification.

Actual current079 single-pane Ethernet witness: SAME product Stage/Apply
remain RX-inert, explicit Start on ip:192.168.1.54, observed AD9363 serial
10400094c295000f0c001400a9be0c169e;30.72MSps/RF30MHz/center2450MHz/gain20dB/
FFT4096/CPU.180 prepared bundles in6.003723s, sequence126..39676; actual cached
native route stays IP. Stop/flush/join/graph-close confirms zero retained
resources/workers, source snapshot unchanged, physical lease released.
These are prepared bundles, not painted FPS/LPS, transport throughput,
continuous30.72MSps, RF accuracy, dual-RX or four-source acceptance.
Read-only Windows routing confirms this destination via Ethernet2/ASIX,
interface4/source192.168.1.101/on-link192.168.1.0/24; adapter Up/1Gbps.
This is current host-side proof, not a new device-side link-speed query.

The remaining current hardware blocker is fresh evidence-bound alias exclusion
for unknown-serial AD9364USB versus known AD9363Ethernet. Existing parallel
identity guard is unchanged and still refuses this mixed combination.
No USB expectation is attached to the Ethernet owner. Next: bind fresh actual
USB/IP observations and selection revisions, reject aliases/stale/missing proof,
then four-source RX/isolation/rotations/screenshots. Route controls in the V2
editor require the dedicated UI-agent workflow; backend intent alone is not
a finished user-facing selector.

Diagnostic EXE SHA256
f71d1d9521b6639b27fb2a90c5eeb88ea9f570dddc5631cec7e9ca26691fa37c;
native SHA256
5fa7a0632922586ed7311604854edcbeb2cf3f2736ef63f241b12d2046f4188e.
Static/current EXE, main user product work and system settings are unchanged.
Root only this increment; historical Luna/Sol UI reviews are not backend079
approval. APP-07/M8 remains PARTIAL/H02 TODO/full APP-00…APP-14 goal ACTIVE;
visible/DPI/DWM, paired RF, performance/soak and whole release remain open.

## M8-080 — bounded USB/IP alias exclusion (implementation contract)

The explicit known IP owner may now retain a separate immutable USB alias
witness copied by its SAME existing read-only observer after successful
disconnect. The witness requires a known serial agreeing with the selected IP
canonical identity, matching firmware/adapter, exact USB connection facts and
copied USB capability/topology. It is NOT a USB acquisition expectation on the
Ethernet engine and never gives an unknown-serial source a stable identity.

For an unknown-serial USB source alongside this known IP source, Stage requires
the known source's freshly discovered USB connection and a new stopped-owner
observation. Both source assignment orders are supported. Duplicate canonical
identities, USB device addresses (even different interfaces), serial aliases,
missing evidence and unknown IP still refuse. Distinct USB-only and independent
family cases keep their established rules. The pure validator does not itself
prove provenance: product composition validates the SAME retained native witness
and source-selection lifetime, not a caller boolean or reconstructed receipt.

Initial Apply validates all retained selections/routes. Both AD capture owners
retain a pure immutable peer-selection guard, without cross-owner hardware
locks. The known IP control re-observes the USB witness before stopped Apply,
Start, paired staging and Sweep lease acquisition. Changed readback/topology or
failed observation/release revokes the witness; stale plans cannot restart it.
Acquisition still uses the selected IP route and its native expected serial;
the unknown USB owner still uses its SAME-context USB expectation before RF.
Stop/cleanup is not conditional on witness validity and remains explicitly
retryable after an uncertain observer release. No new acquisition context,
IQ path, FFT/DSP/queue/quality/time policy or UI widget is introduced.

This is bounded admission, not physical uniqueness against serial/firmware
clones, an undetectable identical same-port swap, external application locks,
link throughput or RF accuracy. A missing/disconnected known USB alias cannot
be replaced by a display name, stored address or invented serial. Current
physical 2x2, rotations, actual Windows screenshots, per-view performance and
release qualification remain to be established on the matching new build.

### M8-080 — exact software qualification and interrupted physical trial

Runtime source `37d9c8e34e82763df764ef0f41c24babf642a8e3` was built as a
fresh diagnostic `APP07-USBIP-20261006-37D9C8E-R0`, not the static/current release.
The matching native build passed all 47 CTests (60.89s), package verification
covered 665 frozen files and 590 source entries. One serial full V2 run after
source freeze completed 1448 tests: 1382 passed, 66 skipped, no failures/errors
(504.408s), exact provenance and tracked-clean state before/after. Four historical
NaN warnings are retained. Targeted alias tests passed 142 cases/147 subtests;
these source/mock results do not prove hardware uniqueness or physical HIL.

Fresh read-only discovery identified four physical sources represented by five
operational choices and completed normal graph shutdown. The subsequent current
2x2 trial used AD9364USB (requested Fs61.44MS/s/RF56MHz), HackRF20MS/s,
AD9363 explicit `ip:192.168.1.54` (Fs30.72MS/s/RF30MHz) and RTL2.4MS/s,
FFT4096. Stage/Apply were inert. After explicit Start, no four-source freshness
or Stop/flush/join receipt was obtained; the owned test process was interrupted.
No screenshots, rotation, normal cleanup or simultaneous receive PASS came from
this trial. The exact blocked operation is unknown: the diagnostic timeout was
checked only after potentially blocking Qt events and observation callbacks.

The prior-held hardware lease record is preserved pending explicit recovery.
Process absence alone is not a hardware-cleanup receipt; no automatic lock reset
or unchanged RX retry is permitted. UI source review identified an unnecessary
synchronous Close eligibility state-lock query while Start is pending, but this
is not a demonstrated cause of the physical stall. The next UI V2 packet removes
that redundant query while retaining the original final close authority and
requires a bounded contention regression, independent review and matched build.
Native RF/DSP/quality/time/epoch/queue/budget policies are not weakened.

M8-080 diagnostic EXE SHA256:
`02a64f99cb2976a5ad425ed2e65e05d4384605d7d528dba0752dcf2887713ba5`.
Native SHA256:
`5fa7a0632922586ed7311604854edcbeb2cf3f2736ef63f241b12d2046f4188e`.
Unchanged native bytes do not relabel the source/build of prior 079 witnesses.
APP-07/M8 remains PARTIAL/H02 TODO. Paired RF, current four-source HIL,
visible Windows/DPI/DWM, performance, stability and release review remain open.

## M8-081/082 — responsive close eligibility and terminal Qt ownership

The UI V2 independent pane session no longer queries the synchronous Close
eligibility state lock redundantly while Start is pending or the session is not
stopped. The existing final close authority remains responsible for admission;
this does not introduce a second owner, bypass a lease or change hardware state.
Bounded contention tests cover the distinction. This is a source-level correction,
not a demonstrated explanation of the interrupted M8-080 physical trial.

Spectrum and Waterfall terminal release now retain an exact ownership plan
before pyqtgraph close operations can clear axis/label references. Structural
close, both ViewBox drains and exact scene/layout detachment must complete
before any wrapper is destroyed. All surviving captured objects are checked
for ownership before deletion; checks are repeated at each targeted deletion.
Only the captured receiver's DeferredDelete is delivered, without a global
Qt event drain or forced garbage collection. Partial structural/deletion failure
retains the plan for explicit retry, including retry after the ViewBox has
already been destroyed. Waterfall retries do not repeat history clearing.
Retired RF controls ignore late duplicate cancel events. Ordinary Stop retains
the last visible data; terminal release is not ordinary Stop.

Named ViewBoxes are unsupported by this terminal helper and explicitly refused
before mutation, including deletion-only retries. Simulated foreign-parent
tests check guard behavior; they are not proof of safe arbitrary C++ reparenting.
An actual reparent diagnostic crashed at the diagnostic mutation itself before
the retry guard, and is retained as a failed diagnostic, not a product PASS.
Earlier native access violations and the physical stall have no established
causal explanation. No vendor/global registry modification is made.

### Exact diagnostic software qualification

Product source `9032106f91c9da5818519b5582a7cf9568f48f48` incorporates the
independently reviewed UI changes and tests through normal commits. Expanded
normal-plugin UI regression passed 96 tests plus 20 subtests (29.92s).
The matching fresh diagnostic `APP07-QTOWN-20261006-9032106-R0` passed all
47 native CTests (61.44s), with 665 frozen runtime files and 590 source entries.
One serial complete UI V2 run after source freeze completed 1468 tests:
1402 passed, 66 skipped, no failures/errors (488.416s). Exact source/native
provenance and tracked-clean state before/after were verified. The observer
recorded zero uncaught Qt callbacks and restored its hooks; a separate raw-log
audit found zero tracebacks. Source snapshot and runtime verification passed
again after the run. Absence of observed exceptions is not proof that no
locally handled or silent failures exist.

Diagnostic EXE SHA256:
`4057f8796056250d90ec7ea155a51eee937efb839339f6ffb3e5d0a7dc8a71c2`.
Native SHA256:
`5fa7a0632922586ed7311604854edcbeb2cf3f2736ef63f241b12d2046f4188e`.
The full V2 gate exercises current V2 sources with the matching packaged native
module, not visible frozen Windows GUI operation. Broader mypy still reports
17 inherited errors across five files; matching baseline diagnostics were
verified. Helper-only mypy, scoped Ruff/compile and whitespace checks passed;
this is not a whole-project typing PASS.

No new physical RX, lease reset or current/static release promotion occurred.
The interrupted hardware trial still lacks a normal Stop/flush/join receipt.
The next dedicated UI V2 design task is the real editor's explicit USB/IP route
workflow over the existing typed route contract; backend support alone is not
a finished user-facing transport selector. Current four-source HIL, positive
paired RF, actual per-view paint latency/FPS, visible FHD/QHD/DPI acceptance,
stability and independent release qualification remain open. APP-07/M8 remains
PARTIAL/H02 TODO, with the full APP-00 through APP-14 objective unchanged.

## M8-083 — explicit observed USB/IP routes in the UI V2 pane editor

AD936x source choices now publish an immutable, bounded tuple of at most 32
unique typed operational routes copied only from the same selected descriptor's
URI and alternate URIs. Missing, malformed or oversized metadata supplies no
route choices; there is no synthesized alias, new SDK probe or inferred serial,
physical connector, Ethernet speed or calibration identity.

The real independent-pane editor offers Automatic/default and exact observed
USB/IP choices for AD936x only. A shared source has one route request: a newly
joined pane adopts the destination group's retained intent, and peer controls
show the same read-only choice with a visible wrapped affected-pane cue.
Passive discovery refresh preserves a pinned route and the complete requested
AD profile, including mode, Fs, FFT, range, analysis band/window, priority and
revisit target. A disappeared route/source remains explicitly unavailable and
blocks preparation until a deliberate user choice; refresh never silently
replaces a pin with Automatic. Non-AD sources have no Pluto route selector.
An unadvertised IP still uses the existing global manual-URI selection workflow,
which publishes that exact descriptor, not a new pane URI parser.

Prepared Preview reads the retained typed plan/context, not subsequently edited
widgets. It labels requested intent, Stage-confirmed selection and Apply
revalidation; none of those establish physical readback or link throughput.
Stage/Apply remain inert with respect to RX Start. Hardware identity, alias
exclusion, common-owner and stale-revision guards remain unchanged.

### Regression correction and exact software qualification

The first integrated source `f92ea67e40e7f3a4bec59ee854c55ec19a12fad0`
built successfully, but its complete V2 run failed: two obsolete typed test
fixtures, stretched mode-control geometry and two untranslated Russian phrases.
That failed evidence is retained. The correction clears AD-only routes when a
fixture changes family, uses a real compiled RTL plan and retained RF context,
keeps the mode/points stack at its adaptive preferred height, and fully localizes
the Russian copy. Existing relative height, RF refusal and localization guards
were not weakened.

Corrected product source `9f4a91752c81603a67df894a805cbf703e6ce86a` passed
a distinct immutable UI review and root expanded regression: 154 tests plus
1445 subtests (14.74s after normal integration). Scoped Ruff/compile checks cover
all ten changed files. Two-product-file mypy still reports 16 errors across
four other files, exactly matching the prior source's diagnostic lines; this
is not whole-project typing approval.

Fresh diagnostic `APP07-ROUTEUI-20261006-9F4A917-R1` passed 47/47 native CTests
(57.20s), 590 source-input entries, 665 frozen files, startup/runtime checks and
one serial full V2 run after freeze: 1478 total, 1412 passed, 66 skipped, no
failures/errors (497.751s). Source/native provenance and tracked-clean state
before/after match. The observer recorded zero uncaught Qt callbacks; a separate
log audit found zero tracebacks. Four historical NaN warnings remain visible.
Post-run source snapshot and runtime inventory verification passed.

Diagnostic EXE SHA256:
`e263eee2ac189aeff631e8ecc4b2d00ce5c595cd625bb04213fb3501eff20a03`.
Native SHA256:
`5fa7a0632922586ed7311604854edcbeb2cf3f2736ef63f241b12d2046f4188e`.
These results qualify this software packet only. The full V2 runner uses current
V2 sources with the matching packaged native module, not a visible frozen
Windows hardware session. Later documentation commits do not relabel the build's
source commit. No physical RX, lease reset, system/firewall change or static/current
EXE promotion occurred. The interrupted M8-080 hardware trial still has no
normal Stop/flush/join receipt or established cause.

Next: actual analytical-ready-to-relevant-Spectrum/Waterfall-paint measurement
and bounded first-cause/watchdog evidence for future physical admission, then
the current AD9364USB/HackRF/AD9363Ethernet1G/RTL layout and rotations after
hardware recovery. Positive paired RF, current four-source HIL, visible
FHD/QHD/DPI, latency/FPS, stability and independent release qualification remain
open. APP-07/M8 remains PARTIAL/H02 TODO; the full APP-00 through APP-14 objective
is unchanged.

## M8-084 prerequisite: declared host clocks and paint-return boundaries

Root source `87b6dbde77c6d64ef691f295ee933334dd02ffc1` adds an optional
measurement contract, not a new performance result or release qualification.

Native-ready bounds now declare the host clock only when the adapter retains
the builtin `perf_counter_ns` callable. Custom/injected and legacy clocks stay
unknown. Known detector/layer bounds must belong to their receipt's process.
No native/host clock offset, drift extrapolation, RF timestamp or sample
continuity is inferred from these declarations.

An immutable paint-return receipt retains the original graph/pane/view,
resource, run/activation and ready identity through its existing obligation.
It carries a sample before the selected base paint and a sample after its
successful return. The latter is an observation AFTER return, not the exact Qt
exit instant or DWM/photon presentation. Conservative elapsed intervals for the
enclosed base return and the after-return sample remain separate. Negative
lower bounds denote uncertainty and are not clipped to zero or replaced by a
midpoint.

The ledger's own timestamp still describes later bookkeeping. The optional
paint path cannot borrow that timestamp as a paint receipt; when declared
clocks agree, bookkeeping delay is evaluated separately. Foreign view/ref,
clock/process or contradictory ordering cannot produce qualified timing.
Legacy two-argument stage callbacks remain valid but supply no paint timing.

Additional scalar receipt retention is charged to the existing shared 1 MiB
host graph component. Paint callbacks reuse the admission's conservative
reference weight instead of recursively traversing Sweep coverage. Native DSP,
Fs/FFT/gain, capture cadence, queues and RF/epoch/control policy are unchanged.

A separate explicit diagnostic watchdog uses CPython faulthandler rather than
a Qt timer checked after `processEvents`. Its deadline writes one nonfatal
trace; it never kills/restarts the application, stops RX, releases leases or
declares recovery. It is restricted to a diagnostic process owning the
process-global faulthandler facility, not automatically installed in product UI.

Root prerequisite verification: 19 new tests passed (2.041s), and 141 targeted
tests passed without skips (10.540s), including the unchanged exact 9f4 packaged
native module and the declared CMake mock IIO. Scoped mypy covers nine source
files; Ruff/compile checks cover eleven files. Failed setup/fixture runs are
retained; typed density accumulation/owner/receiver guards were not weakened.

Actual Qt sampling and product-board forwarding are the next dedicated UI-only
implementation/review task. This prerequisite does not qualify physical
latency, current four-source HIL, paired RF, visible Windows/DPI/DWM, soak or
release. The last fully software-qualified diagnostic build remains exact
9f4; it must not be relabelled as this source. APP-07/M8 remains PARTIAL,
H02 TODO, and the full APP-00 through APP-14 objective remains open.

## Exact retained delivery-stage prerequisite (M8-084 follow-up)

An all-view Qt/real-ledger probe found that a completed Waterfall/persistence
reference could be reattached after Stop, despite a passing focused suite.
A scalar last-completed sequence cannot decide this: `<=` also refuses older
still-pending asynchronous density work; `==` forgets completed predecessors.
The rejected UI candidates have not been integrated or used for a new EXE.

`PaneDeliveryLedger.delivery_stage_if_retained` and the corresponding
`PaneResourceSession.pane_delivery_stage_if_retained` now expose a cached
observation from the same bounded graph ledger. Only the original retained
reference object is observed, without recursively comparing Sweep identities.
Copied/foreign/evicted references and lock contention return `None` (unknown),
not approval. The read uses a nonwaiting lock acquisition, samples no clock,
changes no counters and allocates no history/tombstones. Existing graph limits
remain 256 records, 128 events and a shared 1 MiB scalar component.

This is a point-in-time observation, not an admission reservation or a new
control authority. Normal stage transitions remain authoritative. The future
UI integration must explicitly wire the lookup, preserve legacy callbacks,
distinguish pending from terminal work and reconcile unavailable custody
without inventing a paint receipt or changing acquisition/rendered-data policy.

Seven new tests cover exact-reference identity, low pending versus newer
terminal work, terminal eviction with protected pending work, nonwaiting
contention, inert clock/counters/storage and the actual session facade. The
root targeted set passes 41 tests (2.824s); scoped mypy passes two source files,
and Ruff/compile/diff checks pass three files. A failed initial test fixture is
retained separately. This is software prerequisite evidence, not accepted UI,
physical latency, sustained HIL, soak, visible Windows or release qualification.
The last fully software-qualified build remains 9f4; APP-07/M8 stays partial.

## Confirmed Stop / UI relinquishment prerequisite (M8-084 R3)

Receiver Stop and UI custody release are different boundaries. A successful
receiver Stop intentionally leaves drained/UI-admitted/paint-scheduled records
for the actual UI owner to settle. A completed-reference tuple from an earlier
snapshot can become stale before Qt handles it; a view-local UNKNOWN slot also
misses later declined references. Snapshot absence is not proof of completion.

`PaneResourceSession.pane_ui_stop_refs_after_stop(resource_id)` now captures
ALL retained original UI-stage references of that resource, including ones no
view could attach. It checks cached terminal/inactive state and released lease
under the existing control/state locks; failed Stop or rearm refuses capture.
It neither queries hardware nor acknowledges UI clearing. The immutable tuple
is finite (at most the existing 256 ledger records), not a negative-membership
or physical-time receipt.

AFTER Qt actually relinquishes every captured reference, the separate
`reconcile_ui_stop_cleared(refs)` conditionally settles only exact original
records still at QUEUE_DRAINED, UI_ADMITTED or PAINT_SCHEDULED. Already terminal,
evicted, copied-equal, foreign or repeated tokens are no-ops without fabricated
paint returns, duplicate terminal notes or accounting failures. Uncaptured newer
references and independent resources are untouched. Ordinary strict `note`
validation remains unchanged. This operation is not automatic receiver cleanup.

The pump exposes `capture_ui_stop_refs` and `reconcile_ui_stop_cleared` as
explicit tasks on its SAME existing stopped resource worker. No new worker,
hardware command or queue allowance is added. This matters because registering
a callback on an already-done Future runs it inline on the registering thread:
a Stop callback alone is not an off-Qt guarantee. Pending tasks retain existing
control Start/join guards. Product UI must additionally bind/gate the ENTIRE
capture -> exact Qt detach -> off-Qt acknowledgement handoff, including gaps
between tasks, failure/retry and Close. That integration is not yet qualified.

Both ledger operations reuse its shared 1 MiB / 256-record / 128-event limits;
they retain no extra batches/tombstones. Capture is finite read-only; the
conditional acknowledgement records only genuine Stop-clear transitions and
their bookkeeping timestamps. Neither is a per-frame paint callback. Sustained
UNKNOWN admission can still exhaust bounded measurement capacity until Stop;
untracked/unknown coverage must remain explicit, not interpreted as successful
paint or a zero delay. This prerequisite does not solve that live-run condition.

Twelve new tests and the 105-test targeted suite pass (3.308s), covering actual
mock session/worker dispatch, repeated/stale batches, exact versus copied refs,
eviction/older pending, shared panes, failed Stop/rearm, affected-resource guards
and control-task Start/join gating. Ruff/compile checks cover four files. Scoped
mypy for the two service files passes; adding the pump exposes nine diagnostics
in two unchanged dependencies, reproduced exactly on the immutable 3d41 source
baseline (not a whole-project mypy PASS). An initial shared test fixture used a
nonexistent attribute; its failed log is retained separately.

No Qt rendering integration, new EXE/full gate, physical RX, latency percentiles,
M8-080 recovery/cause, visible Windows/DPI/DWM, soak or release is proven here.
The last fully software-qualified build remains 9f4; APP-07/M8 remains partial.

## Idle serial worker temporary release (M8-084 follow-up)

The stopped resource worker must not retain its last completed scalar operation,
Future or result merely because its next command has not arrived. Plan execution
now uses a short-lived helper frame; the idle loop deletes its operation/Future
locals after completion. The same worker, control slot, error mapping and
active-plan-before-completion ordering are preserved. Caller-owned Futures,
callbacks and exception tracebacks remain caller-owned; this is not a general
cyclic-GC or whole-process memory-leak repair.

Two weak-reference regressions reproduce the old retention on success and
failure, then verify release at the existing worker's next idle boundary without
forced GC, a later command, Qt processing or another thread. The targeted suite
passes 107 tests (3.349s). Ruff/compile/diff pass; scoped typing still reports
nine unchanged dependency diagnostics, byte-identical to the immutable baseline
(not whole-project mypy PASS). Separate UI callback-cycle, hidden Waterfall
admission and repeated persistence scheduling repairs remain unqualified.
No new EXE, physical RX, latency, soak or release acceptance follows.

The subsequent UI residency regression exposed a separate completed Stop
Future retained in the same idle loop. Both successful and failed Stop clear
their active-stop slot before completion, then now delete that loop-local
Future as well. Two new regression-first tests fail on the previous source
and pass after this repair without GC or another command. Five targeted
backend modules pass 83 tests (0.564s); an initial incorrect module name is
preserved as a test-launch failure. No control/failure/retry policy is changed,
and the separate UI callback-cycle repair still needs its matching Qt proof.

## Subsequent exact UI qualification (M8-084 R6)

The earlier pending statements are historical. Integrated source1323ff0 has
passed the matched diagnostic native build (47 CTest) and original full UI V2
gate:1511total/1445PASS/66skip/0fail-error,538.102s, exact provenance, zero
observed uncaught Qt callbacks and raw tracebacks. Strict audit and665-file
post-verification pass. Original52a gate failures remain preserved. Separate
UI-only author/reviewer approval is not independent backend/release approval.
This software evidence is not physical RX, latency/DWM/DPI/soak or promotion
of the static current EXE. APP-07 remains partial.

## Finite ready-to-paint statistics (M8-085, source only)

The diagnostic API `PaneResourceSession.pane_paint_timing_summary()` reads one
immutable snapshot of the existing graph ledger. It does not poll an SDR,
drain native outputs, take a new clock sample, trigger a paint, acquire an
owner lock or change measurement/rendering state. Call it from diagnostics,
not an acquisition loop or Qt paint callback.

## Population and identity

Statistics cover only **retained unique PAINT_RETURNED events**, not all
native FFTs, all admitted frames, a lifetime history or RF capture coverage.
Groups keep graph, typed owner/source/session/configuration/epoch, resource,
capture, endpoint, pane, host run/activation, native producer, view and layer
kind separate. Different panes displaying one native offer remain different
presentation obligations, not additional FFTs or independent RF streams.

Each group reports paint-return count, timed-return count and all timing-state
counts. Missing receipts, unknown/foreign clock mappings and invalid order are
not silently dropped from the denominator. No valid samples means quantiles
are `None`, never zero. Snapshot pane/view counters are cumulative graph
counters; do not reinterpret them as group/window counters.

## Quantiles and uncertainty

For n eligible intervals, nearest rank for percentage p is ceil(p*n/100).
Sort lower endpoints and upper endpoints independently; their rank values
bound the corresponding order statistic of every possible point assignment
inside the measured intervals. p50/p95/p99 retain integer nanoseconds and
signed lower bounds. No midpoint, interpolation or clipping is applied.

Two boundaries remain distinct:

- ready to base Qt paint return, bounded by before-paint/after-return samples;
- ready to the sampled time after return, not the exact return instant.

Ledger bookkeeping time is never substituted for either boundary. Overlapping
ready/paint intervals retain signed uncertainty; negative bounds are not a
measured negative latency. These statistics do **not** measure DWM/photon
presentation, RF latency, scan period, duty, loss, detection probability or FPS.

## Retention and limits

The existing 128-event/256-record, four-pane/12-view scalar snapshot limits
remain unchanged. Mutable or oversized snapshots and duplicate/foreign events
are explicitly refused rather than repaired or deduplicated. Graph eviction,
event-drop, duplicate, accounting and clock-failure counters accompany the
result. `lifetime_complete` is always false: even a zero-eviction snapshot alone
cannot prove all-offers/lifetime coverage or a performance SLA.

Sorting is diagnostic finite-window work outside the ledger lock, not added
per-FFT or per-paint work. Producer queues, native budgets, clock mappings,
Fs/FFT/gain/cadence/quality/time and Stop semantics are unchanged.

## Acceptance boundary

Synthetic/domain/actual cached-session tests prove calculation, separation,
bounded input and absence of owner actions, not hardware performance. APP07-H02
still requires actual source/layout profiles, measured coverage, Spectrum and
Waterfall rates, current UI/render observations and latency evidence. Physical,
visible Windows, DWM and release qualification remain separate gates.

## Cached paint diagnostic observation (M8-086, source only)

`capture_pane_paint_observation(reader)` reads the SAME graph's existing
immutable scalar ledger once and derives its conditional summary from that
exact snapshot. Both raw event/ref/clock evidence and summary are returned;
no second native drain, SDR open, forced repaint or resource-owner lock.
No persistent buffer is created by this API. It is a diagnostic consumer,
not work added to acquisition or paint callbacks.

The observation envelope brackets snapshot copying and summary calculation.
Its query cost is not ready-to-paint latency and excludes executor scheduling,
JSON serialization and log output. Only the retained built-in perf_counter_ns
has a declared observation clock; injected clocks stay unknown. Snapshot
clock provenance is independently retained, never overwritten. Bad clock
samples/regression refuse the observation without mutating the ledger.

Repeated snapshots overlap. Their counts/quantiles must NOT be added to form
window FPS or lifetime percentiles. A later window audit must deduplicate
original graph/event IDs and disclose crossing cohorts, evictions, unknowns,
query cost and sampling/storage losses. This API supplies evidence for that
audit; it does not itself perform or qualify such a window measurement.

Private HIL wiring may execute this cached read on its existing control
executor and emit scalar evidence. That preparation and fake-session tests
are not a physical HIL execution, hardware recovery, performance baseline,
visible Windows/DWM proof, matching frozen EXE or release qualification.

### M8 offline observation-window audit

`audit_pane_paint_window` accepts at most64 original typed observations of one
graph and one declared host clock. Observation envelopes must be ordered and
bracket an explicit half-open host window. It deduplicates identical graph/event
IDs; changed evidence, changed obligation identity, late admission, regressed
coverage counters, mismatched summaries and future stamps are refused.

Before-paint/after-return enclosures are classified as before, inside, crossing
either/both boundaries, after or unknown. Only wholly inside usable enclosures
enter conditional interval quantiles, calculated from original events, never
summed snapshot quantiles. Admission cohorts refer to ledger stamps, not exact
native admission. Missing event IDs are unobserved IDs, not inferred paints or
RF loss. Counter deltas cover observation envelopes, not exact window edges.

This is bounded offline diagnostic work with no persistent product observer,
SDK/Qt call or acquisition/render queue expansion. `lifetime_complete` remains
false. Neither zero counters nor inside-only quantiles prove complete coverage,
actual FPS, DWM presentation,50ms performance or physical release acceptance.

### Calibration immutable-publication prerequisite

Independent calibration services may share one profile store. Finalized
profile/version publication must not overwrite another writer's winner after
an outdated absence check. Each writer now owns an exclusive same-directory
temporary file; publishing uses a no-replace hard link. An identical winner is
idempotent; a different winner is explicitly refused. Unsupported filesystems
fail closed, never fall back to replacement. Only the caller-owned temporary
file is cleaned during normal success/refusal/failure.

This fixes storage publication, not paired-RX calibration integration. There
is no changed calibration math/applicability/units or RF/GUI behavior. It is
not proof of power-loss durability, hostile filesystem/hardlink protection,
cross-host/network-filesystem behavior or absolute RF accuracy.

### Explicit RX calibration profile contract

`CalibrationSignature.receiver_chain` is one typed `ReceiverChain.RX1` or
`RX2`, or absent/unknown. It is not BOTH, an RF connector, a device-name suffix,
or independent tuner evidence. Profiles with explicit RX use schema3 and
compare this field during applicability. Different/unknown current RX refuses
such a profile; settings changes deactivate an incompatible active profile.

Legacy SDR schema1 and instrument schema2 retain their original serialization
and fingerprints; missing receiver is never silently RX1. A legacy unscoped
profile refuses an explicitly scoped current RX, but legacy-to-legacy behavior
is preserved. Schema3 requires a canonical rx1/rx2 field; schema1/2 cannot hide
one. Instrument corrections may not declare a digital SDR RX. Old readers
reject schema3 rather than reinterpret it as schema1. No automatic migration
or existing finalized-profile rewrite occurs.

This is a domain/service prerequisite. Product adapters must still construct
exact signatures from admitted source/current configuration and typed endpoint
and apply corrections to original analytical data with coverage/revision/unit
provenance. Current live SDR adapters still report uncalibrated; this contract
alone does not turn native or paired live data into calibrated dBm or prove RF
accuracy. Native DSP/queues/Fs/FFT/recording and UI controls are unchanged.

### Observed gain policy prerequisite

`AppliedLiveConfiguration.observed_gain_mode` is a typed `ObservedGainMode`
or unknown. The native readback mapper preserves canonical MANUAL/SLOW_ATTACK/
FAST_ATTACK/HYBRID enum names and adds `gain_mode` to the confirmed readback
fields only for those values. Missing, unknown, numeric or bare string replies
do not imply manual gain and do not inherit an older provisional receipt.
Legacy/prepared receipts default to unknown. Numeric gain readback and requested
configuration remain distinct and unchanged. This enables a later exact
calibration signature; it does not apply calibration or establish RF accuracy.

### Exact live calibration signature join

`build_live_calibration_signature` joins one typed endpoint with the exact
device capability/calibration identity, complete applied RF/gain readbacks and
producer-reported normalization semantics. It rejects BOTH, source/resource/
topology mismatch, unknown identity/backend/normalization, non-digital input
units and AGC. Operator frontend/reference context is explicit, not inferred
from a digital RX or a display label. No hardware call, profile activation or
correction is performed by this pure helper. Its caller still must establish
the same admitted acquisition/epoch association before using the signature.

Spectrum provenance has an optional normalization-version field; the native
mapper reads it only if actually reported, never from configuration or a host
default. Existing producers do not report it yet and therefore cannot pass
this join. This packet does not change the native wire schema, claim physical
RF accuracy, or enable calibrated Live dBm. Original arrays/coverage/time/quality
and UI remain unchanged. Native producer declaration and actual analytical
correction with scoped profile/processing revision remain required.

### Producer power normalization declaration

CPU and CUDA DSP publication declare optional host metadata `power-norm-v1`:
bin power is |FFT|²/(N·coherent_gain)²; PSD is |FFT|²/(Fs·sum(window²)).
The existing unit field selects the branch; the declaration is emitted with
the numerical producer frame, not copied from a requested configuration.
CPU exact-bin/all-window and PSD Parseval tests retain their original numerical
tolerances and also check this declaration. CUDA source carries the equivalent
declaration/test but CPU-only qualification is not GPU runtime evidence.

Default/unsupported/deserialized frames retain unknown. This optional host
metadata does not alter enum wire schema5, recording format, RF calibration,
FFT math, gain, timing or queues. Rebuilt matching native bindings are required;
old modules remain unknown. Same-acquisition signature admission and actual
analytical calibration corrections still require implementation.

### Current-frame calibration admission and units

`build_current_frame_calibration_signature` requires the original spectrum
object in a current running owner snapshot, explicit RX, session/source,
generation/epoch/host clock, applied geometry and canonical producer DSP
semantics. Clones, retained stopped frames, changed epochs/generations/RX,
retunes and backend discontinuity refuse. Logical endpoint/device identity
and actual paired producer source ID remain separate. Caller must obtain the
current snapshot from its owner; a retained old snapshot is not proof of
current admission. This helper performs no hardware IO or profile activation.

Calibration math preserves the input convention: dBFS/bin -> dBm/bin,
dBFS/Hz -> dBm/Hz. Missing/incompatible/out-of-range profiles retain the raw
input unit and values; unsupported/absolute input conventions refuse. Legacy
calls without settings retain their existing dBFS/bin convention. Corrections
never rewrite the original frame arrays, epoch, quality or coverage. Native
Live absolute-unit guard is unchanged; automatic adapter/UI activation and
scoped corrected analytical publications remain pending.

### Public calibration serialization schemas

`docs/schemas/sdr_calibration_profile.schema.json` describes the legacy
schema1 serializer, including nullable undeclared range bounds.
`docs/schemas/sdr_monitor_calibration_profile.schema.json` describes canonical
UI V2 finalized schema1 (legacy SDR), schema2 (instrument correction), and
schema3 (explicit single RX). These contracts are separate: a legacy payload
with null bounds is not a canonical V2 serialization. Schema3 requires rx1/rx2;
schema2 cannot fabricate SDR sampling/gain/FFT fields or a digital RX.

Offline tests resolve both schemas through a local registry, without HTTP
fetches. `jsonschema==4.25.1` is a development dependency, not a runtime
hardware admission gate. Structural validation does not replace domain
checks for finite numbers, exact Python integer types, ordered frequency
grids, matching reference planes or applicability. JSON Schema accepts an
integral float such as 3.0 where the domain deliberately rejects it.
Neither validation nor a serialized profile proves physical RF accuracy.

### Selected current-frame analytical correction

`CalibrationService.correct_current_spectrum` joins the admitted current
owner snapshot to an explicit RX/frontend and captures one immutable selected
profile under the service lock. Computation runs outside the lock; selection
changes cannot mix profile ID/version/fingerprint within that result.
The result retains the exact raw frame and session receipt plus separate
read-only corrected values/uncertainty. Native frame arrays, quality, coverage,
epoch, recordings and history are not rewritten. Missing/incompatible profiles
or any bin outside coverage retain whole-frame raw units; no implicit
activation, expert applicability bypass or extrapolation is performed.
Finite ordered grids are required. A completed result is a captured revision,
not a promise that it remains selected forever: downstream publications must
check current owner admission and selected profile revision before display.
Automatic owner/presenter integration and paired independent profile stores
remain pending; this service API alone does not make Live calibrated.

The SDR array correction builds each profile grid once per call and uses
vectorized search/interpolation rather than invoking scalar `evaluate` for
every FFT bin. Exact nodes retain stored values; linear end-segment
extrapolation remains explicit and the default out-of-range refusal is
whole-frame. Scalar `evaluate` remains available as the equivalence oracle.
This reduces host analytical overhead only; it does not change FFT, sampling,
transport, renderer cadence, quality or acquisition continuity guarantees.

`LiveSessionApplicationService.calibrated_current_spectrum` now performs an
explicit analytical pull through its existing `current_snapshot` routing,
not a second hardware opener. After correction it re-reads that same owner
and checks the original frame, signature, session and current selection.
Each selection service has an opaque receipt owner and monotonic revision;
clear/reselect of the same fingerprint (ABA), deactivation, or a receipt from
another selection service refuses. Profile computation does not hold the
selection lock or acquisition control authority. This is point-in-time
admission, not a guarantee against future Stop/retune/selection changes.
Callers must use a distinct selection service per endpoint and invoke the
pull from a preparation worker; automatic V2 presenter/render wiring and the
endpoint selection registry are still pending. The native/raw bundle remains
unchanged and must not be relabelled as calibrated by this optional operation.

`SdrApplicationServices.receiver_calibration` now assembles a bounded
`ReceiverCalibrationRegistry` when its calibration port is a concrete
`CalibrationService`. Custom protocol-only ports remain explicitly unsupported
instead of receiving a fabricated store. Scope identity is admitted device/
firmware/adapter plus logical source, physical stream resource and single RX;
pane/endpoint captions do not determine it. Same RX bindings share selection;
RX1/RX2 and changed identities have separate selections sharing file storage.
Lookup performs no RF IO or activation and capacity exhaustion refuses without
silent eviction. Explicit release/clear invalidate retained receipts.

`LiveSessionApplicationService.calibrated_receiver_spectrum` obtains that scope
from its current device, uses the same-owner analytical pull, then checks
current scope membership too. A retired service cannot be admitted by retaining
and reactivating an old reference. This remains explicit worker-side analytics;
V2 profile controls/preparation/render integration are not automatic yet.

### Captured Live calibration publication — package 101

`LiveSessionApplicationService.captured_calibration_lane` creates an inert,
explicit single-Live-owner endpoint/frontend binding. Its preparation authenticates
the exact cached running snapshot, computes outside the application control lock,
and returns a distinct analytical publication retaining the original raw frame.
Ordinary same-context sequence advancement is allowed at completion/delivery;
the existing strict-current calibration APIs retain their original semantics.

Application control dispatch and pane control transactions invalidate captured
receipts via a monotonic host-only revision, even if later metadata returns to
the same values. Current cached state, applied configuration, identity/resource,
RX/producer/session/epoch/normalization, observed gaps and registry membership/
selection revision are rechecked. Control contention refuses without waiting on
RF work. Close or a different frontend binding invalidates lane authority.
Delivery ordinals prevent a delayed older result rolling back an admitted successor.
This does not modify native quality, clocks, DSP, acquisition or raw histories.

No internal frame queue/cache is introduced; the consumer remains responsible
for bounded active/pending work and charging raw/corrected/uncertainty retention
to its existing aggregate budget. Validation is point-in-time, not an atomic
physical/paint transaction. Pane-owned graph and Sweep explicitly refuse this
single-owner boundary; they require their own captured publication authority.
Those integrations, UI commands/preparation/render wiring and matched GUI
qualification remain required; this prerequisite is not APP-07/APP-08 closure.

### Explicit calibration commands — package 102

Captured Live lanes expose inert `preview_selection(profile)` and explicit
`apply_selection(preview)`; `None` is an explicit Clear proposal. Comparison
uses the current admitted frame/applied/frontend signature, never the desired
signature copied from the profile. Preview does not mutate current settings or
activate profiles. Incompatible application refuses before selection mutation;
normal application has no expert override.

The short application control guard keeps Stop/retune commands from crossing
selection admission. Registry membership plus service selection owner/revision
are checked atomically under the registry/service lock order. Retired scope,
selection ABA, repeated application, foreign binding or acquisition-context
change refuses. Ordinary same-context sequence advancement remains allowed.
Same-RX panes share selection revisions; different RX scopes do not.

This running single-Live command boundary performs no RF IO, file write or
Start. Stopped/no-frame selection workflow, paired/Sweep owner authority and
V2 controls/render integration remain required work, not implicitly implemented.

### Exact-source pre-admission — package 103

The display path must use `lane.capture(admit_source)` before correction. It
authenticates and captures the exact raw publication, immutable selected profile
and selection receipt, then invokes the synchronous worker admission callback
outside application/RF and selection locks. Refusal performs no correction and
returns no consumer handle. The frozen handle retains raw plus immutable/scalar
authority metadata, not the whole LiveSnapshot or persistence arrays.

Use the SAME aggregate presentation budget in this order: admit the handle's
actual exposed backing roots; reserve `handle.derived_output_bytes` against that
handle; `lane.correct(handle)` without recapture; commit the actual result roots;
then perform normal validity/delivery checks. Source admission is not replaced
by `observe()` or an estimate based on visible slice length. The declared output
layout is two owned float64 arrays (values and uncertainty), checked before/after
correction. Incompatible/no-profile/out-of-coverage fallback has the same bound.

Selection/control/context invalidation before or during correction refuses;
ordinary same-context producer advancement does not replace the captured input.
Caller-owned reservation scopes release on failure/cancel/refusal/Close. Existing
`prepare()` remains an explicitly unbudgeted analytical convenience and MUST NOT
be used as the UI source-admission guarantee. This bounds admitted exposed raw
backing and retained outputs, not numerical scratch, opaque native allocator
capacity, Qt storage, total RSS or transient peaks. No budget increase is made.
UI wiring and owner-specific paired/Sweep/stopped workflows remain required.

### Typed CURRENT calibration binding — package 104

The ordinary Live application exposes an array-free `current_calibration_binding(frontend)`
receipt from its SAME cached observation. Logical device and physical resource
come from admitted topology; endpoint ID is the actual producer, and RX selection
uses only exact canonical RX1/RX2 metadata. Label, URI suffix and serial never
select a channel. Actual readback/normalization/signature guards still apply.

`captured_bound_calibration_lane(registry, binding)` accepts only the SAME owner
receipt and verifies it before returning the lane and on every observation.
Session/epoch/generation/control revision, producer, applied configuration and
signature changes refuse instead of silently rebinding. Same-context ordinary
frame advancement remains allowed. No spectrum/persistence arrays are retained
in the binding; no hardware query/open/Start/RF change/profile selection occurs.
Pane-owned or Sweep observations refuse and require their distinct authorities.
This running single-RX binding does not implement stopped/paired/Sweep calibration
or imply physical RF-path accuracy. UI must explicitly replace/close old bindings.

### Observed calibration clock domains — package 106

Strict current-frame admission accepts the two explicitly supported producer
clock domains `host_steady_ns` and `unix_ns`, only when the SAME current snapshot
and original frame agree. Unknown domains and mixed domains still refuse.
Admission does not convert, relabel or compare timestamps between clock domains;
the captured context retains the actual domain for subsequent invalidation.

A native/mock paired-publication probe exposed the previous steady-only refusal.
After fixing that refusal, the actual AD936x operational descriptor ID and
canonical capability snapshot ID still differ and the calibration identity
guard refuses. Their authoritative binding must be implemented and qualified
before claiming native AD936x calibration usability. Synthetic calibration
tests and this clock fix do not establish a working paired calibration adapter,
physical reference accuracy, visible UI acceptance or release qualification.

### Admitted native calibration boundary — package 107

`DeviceDescriptor.capability_binding` retains the existing typed
`DeviceCapabilityBinding` from the SAME owned AD936x observation. Its operational
source, canonical snapshot and calibration identity must match the descriptor
exactly. Refresh and verified route merging carry a new coherent receipt; a
conflicting/unverified merge cannot retain it. Operational and canonical IDs
are not renamed. Calibration signature and receiver registry validate this
receipt. Legacy descriptors with equal IDs still require consistent canonical
identity; unequal IDs without the explicit receipt refuse. Foreign source,
snapshot, firmware or resource do not borrow an existing RX selection.

Ordinary native Live now retains the actual applied `PlutoReceiverSelection`
enum in its snapshot and spectrum bridge. Unknown/old readback and BOTH are not
silently assigned RX1. This metadata is not proof of a physical RF input path.

Healthy RUNNING Analyzer sessions retain `stop_required`: dispatched acquisition
must still be stopped/joined later. Captured analytics admits that case only
through the SAME ordinary RTBW owner, with no pending control, lifecycle error,
failed RF receipt or source release. Transitional/error phases and ambiguous
cleanup-required legacy observations refuse; Stop still invalidates consumers.
The flag itself is neither cleared nor redefined by calibration.

Native zero linear power is `-inf` in log power. Current/captured correction
preserves it without flooring or inventing a sample; NaN/+inf power and unordered
or nonfinite frequencies refuse. Existing uncertainty/status/raw-unit fallback
and two-array output budget remain unchanged. Native/mock regression uses
ordinary Live binding/capture/delivery/Stop and per-RX paired publication joins
across rearm. It does not implement paired calibration consumer authority or
qualify physical RF, UI, sustained performance, an EXE or a release.

### M7/M8 measurement and admission boundaries

An isolated native RX baseline may establish host-delivered sample and computed
FFT counter deltas for one admitted resource. It does not qualify four-source
composition, independent peer continuity, pane delivery, Qt paint, DWM, or soak.
Keep nominal ADC Fs, host-delivered samples/s, computed FFT/s, published frames/s,
Sweep LPS, and actual paint-return observations separate. Zero observed native
queue/FFT drops does not establish lossless RF capture without a hardware loss
counter. Report counter-read envelopes and unknown cache freshness explicitly.

Keep requested and actual RF settings separate. A benchmark must report an
observed LO quantization difference, declare its narrow tolerance before RX,
and validate frame metadata against the actual readback. This is not permission
to lower Fs/filter/FFT or change the product's RF admission contract silently.
Diagnostic serialization must preserve the complete summary in its own namespace
so the observer's scope cannot collide with the surrounding experiment's scope.

An unknown-serial USB AD936x and a known IP AD936x remain subject to the original
typed parallel-identity and fresh USB-alias guards. OS device names, board labels,
different requested rates, or a host Gigabit link do not replace those witnesses.
Do not repeatedly retry an unchanged refused Stage, relabel an isolated baseline
as a 2x2 result, or treat an operator reconnect as the interrupted process's
Stop/flush/join receipt. Keep the unresolved admission condition explicit.

Raw context attributes must distinguish absent from explicitly empty values;
the existing fallback serial string does not establish that distinction.
Observing different raw serial presence, firmware, kernel or board labels is
not by itself a qualified cross-transport physical exclusion witness. Do not
invent a stable identity from those diagnostic fields or weaken the existing
parallel-source guard to make a particular test pass.

The current supported USB-alias path needs a freshly discovered, SAME-serial
USB candidate for the selected known IP owner. Such a connection can supply
read-only alias evidence while acquisition stays on the explicitly admitted
Ethernet route. Its absence is an unresolved current admission condition, not
proof that Ethernet reception is broken or a permanent USB requirement for
Ethernet deployment. A different admission mechanism needs its own reviewed
contract and negative/changed-path qualification before use.

## Current typed single-RX delivery regression (2026-10-09, unresolved)

A three-source original UI V2 diagnostic (Empty1/HackRF2/AD9363 Ethernet3/RTL4)
at source6186a4c/nativef8 did not qualify lifecycle: the initial frame predicate
timed out, followed by normal Stop/terminal drain/join/close. Its final cached
observation showed fresh HackRF/RTL deliveries but zero AD pane preparations,
while the AD owner remained RUNNING with a spectrum. This is not four-source,
paint/performance/soak acceptance or proof of a network failure.

An original-composition mock reproduces a concrete integration defect: current
native AppliedConfig reports typed RX1 and the converter retains RX1, while
Ad936xRtbwPaneOwner still declares legacy-unknown None. PaneResourceSession's
exact receiver guard then rejects that bundle. This is consistent with the
hardware boundary, not proof of its exclusive cause or the older interrupted
test's cause. The product repair is not implemented in this documentation step.

The repair must bind receiver admission to the SAME owned Start-readback,
preserving selected endpoint validation and source/session/epoch/config/profile
guards. Do not erase RX1, turn None into a wildcard, authorize identity from the
first incoming frame, or open a second owner. Qualify current/legacy/refusal and
rearm paths before a changed-path physical test; no unchanged RX retry. The
separate independent-AD identity dependency and APP-07 partial status remain.
