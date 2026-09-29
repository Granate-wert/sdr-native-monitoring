# APP-07 three family owner integration (partial)

The same `PaneResourceSession` now accepts concrete adapters over the existing
AD936x RTBW, HackRF RTBW and tinySA instrument Sweep service graphs. No adapter
constructs a second SDK/serial owner: each receives its already selected
`LiveSessionApplicationService` and tinySA also receives its same common
instrument service. Separate independently composed application graphs can
hold three resource leases at once; the current product composition still
creates one global Analyzer graph and does **not** yet expose this topology.

`TinySaTracePaneProfile` binds the source-neutral 2..10001-point dBm trace
profile to an actual typed `TinySaSweepRequest`, including runtime settings,
input mode, deadline, repeat policy, readback and external correction identity.
Only range and controller-assigned epoch change between time-sliced jobs. The
owner checks exact selected source/selection revision, observed model and
device/firmware identity, retained instrument run/generation and full request
intent. The common Analyzer assigns the effective epoch; the pane uses that
actual epoch, never a guessed pre-Start value. The same serial owner executes
version/settings/scan and bounded Stop/join/close. Failed publication or Stop
retains owner/lease until explicit cleanup. The pane owner performs no serial
work on Qt.

Instrument provenance defines Stop as the sweep's exclusive right boundary;
its last frequency point normally precedes Stop. `PaneResourceSession`
therefore checks instrument crop coverage against the typed provenance
start/stop while keeping the existing last-bin rule for I/Q FFTs. The
`SweepLineFrame` constructor independently verifies the complete instrument
frequency grid against that provenance. No dBm to dBFS conversion or fake
Fs/FFT/hop is introduced.

One fake-SDK/serial integration test composes all three real family adapters
as separate graphs in a 2×2 plan: AD936x and HackRF each deliver dBFS/bin on
different ranges, tinySA delivers dBm on a third, slot 4 is Empty, three
leases remain active together, and stopping only tinySA leaves both SDRs
running. Other tests cover tinySA two-range Stop→Start/new epoch, duplicate
cached-trace suppression, generic-profile refusal before serial and ordinary
control exclusion. These are software composition tests, **not** a current
EXE, physical simultaneous devices, firmware qualification or sustained
render/performance evidence. The product still needs resource-scoped source
selection/catalog/recording ownership and per-pane UI V2 projection/commands.
