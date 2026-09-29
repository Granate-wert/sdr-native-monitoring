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

Still open: same-source frozen EXE build and visible qualification; physical
one-RX multi-pane time-slicing under user controls; genuine Ethernet and
10001-point tinySA cells; per-pane SDR wide Sweep and qualified RX2; recording
conflict UX, localization/DPI/area/latency/load/soak gates and release.
APP-07 and the full APP-00…14 objective remain **partial**.
