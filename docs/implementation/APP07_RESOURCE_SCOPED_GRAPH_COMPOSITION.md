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
