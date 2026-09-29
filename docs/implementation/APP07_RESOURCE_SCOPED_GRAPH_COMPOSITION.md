# APP-07 resource-scoped Analyzer graph composition (partial)

`build_v2_analyzer_application_graph` is now the **same** graph assembly used
by the current single-source UI V2 shell. It can also be called with a new
`SdrApplicationServices` bundle for each independent physical resource. Graph
construction performs no Discover, Select, device open, RX Start or serial
command. Reusing one bundle/graph does not create another receiver.

`compose_v2_pane_resource_session` accepts an already compiled 1–4-slot
`PaneLayout`, exact acquisition groups, separately selected V2 graphs and one
shared receiver lease manager. It uses the existing AD936x RTBW, HackRF RTBW
and tinySA trace pane owners, not another SDK or renderer. It derives physical
identity keys from the **selected capability bindings** and refuses unknown
identity, duplicate source/physical identity, reused native/catalog/family
owner, wrong endpoint/family or missing graph before creating a lease or
starting a receiver. An all-Empty layout returns no capture session. The
`PaneResourceSession` itself also requires unique canonical identities for
any multi-resource plan; distinct operational USB/IP IDs alone are not proof
of independent devices.

The fake-SDK/serial three-family 3+1 test now uses this product graph builder
and composition seam: AD936x and HackRF RTBW publish dBFS/bin on different
ranges, tinySA publishes dBm on a third, and slot 4 remains Empty. Three
resource leases coexist; stopping the instrument leaves both SDR owners
active. Separate tests cover inert graph construction and refusal of two
AD936x routes with one canonical physical identity. This is **not** a visible
mixed-source UI, physical three-device RX proof or a frozen Windows EXE.

Still required: expose independently selected resource graphs through one
Analyzer tab, bind per-pane source/range/mode/Empty and prepared fair rendering,
own explicit selected/Stop-all/close and recording conflicts, then qualify a
current EXE on physical AD9364 + HackRF + verified tinySA. The existing
`shared_views` remains correctly labeled as views of one source.

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

The board is **not yet wired into the user-facing Analyzer tab or a frozen
EXE**. It owns no discovery, RF Start/Stop, worker/supervisor or scheduler
timer. Fair coalescing, terminal Sweep preservation under pressure, selected
Start/Stop and Stop-all UI, per-pane configuration, recording conflicts and
safe product shutdown remain APP-07 work. USB Pluto and HackRF are enumerated
on the development host; a bounded version-only COM31 probe identified
tinySA Ultra. That is source availability only, not concurrent three-device
RX, UI frame-rate, RF duty or metrological acceptance.
