# APP-07 resource-scoped Analyzer graph composition (partial)

Current state: the final section documents the newer user-operated V2
assignment editor and its bounded physical witness. Earlier "not yet present"
statements below describe the historical intermediate checkpoints, not the
current source. APP-07 is still partial and no frozen EXE is qualified here.

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
