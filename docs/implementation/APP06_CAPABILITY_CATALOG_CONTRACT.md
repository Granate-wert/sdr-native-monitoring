# APP-06A: common catalog joins and pre-SDK request compatibility

This file is a cumulative implementation record. The early family table and
its `mode_runtime_unavailable` HackRF Sweep result describe the original
APP-06A slice, not the later optional APP-06C runtime route documented below.
Neither route availability nor a successful source test is APP-06 acceptance.

This is source-level integration into the existing backend for **UI V2**.
It is not completed multi-family Analyzer selection, a common hardware owner,
frozen-package qualification, calibration acceptance or APP-06 completion.
Neither a catalog entry nor an accepted compatibility result authorizes RX.

## One set of facts, explicit identity namespaces

The existing `DeviceCapabilitySnapshot` remains the capability truth model.
The existing `DeviceCalibrationIdentity` remains the adapter/device/firmware
identity used by later calibration/correction gates. An optional, evidenced
`model_id` is an adapter discriminator, not a parsed human-readable label.
HackRF board kind and tinySA Basic/Ultra come from their typed read-only
observations; AD936x ranges continue to come from the actual observation,
including modified firmware, rather than a hard-coded chip-name ceiling.

`DeviceCapabilityBinding` joins an existing operational device/source ID to
those same immutable references. It does not rename operational IDs, frames,
recordings or acquisition epochs into the canonical SHA-256 namespace.
Both canonical references must exist together and agree on family, adapter
and identity key. Unknown-serial routes remain operational candidates, with
neither a stable physical key nor calibration identity claim.

`DeviceCapabilityInventory` now additionally contains bindings and independent
`AdapterRuntimeSnapshot` observations. Each collection has the existing finite
device bound; iterable consumption stops at bound+1. Lookup and exact merge
perform no discovery, SDK load, owner construction, configuration or RX.
Exact duplicate inventories are idempotent. Conflicting capabilities, firmware
identities, operational IDs, adapter families or runtime facts are rejected,
not replaced by a last-write-wins policy. Providers must resolve aliases before
merge; merge cannot infer aliases from labels, addresses or equal ranges.

## Runtime facts are not hardware-support facts

`UNKNOWN`, `UNAVAILABLE` and `AVAILABLE` describe an observed adapter contract
surface. They are independent from physical capabilities. An available contract
does not prove driver/DLL closure, a selected device, a specific acquisition
mode, transport throughput, RF-path validity or frozen EXE acceptance.
An absent runtime observation stays unknown, not an unsupported-device claim.

NativeLive observes the already loaded AD936x surface only: exact integer
observation protocol1, identity admission protocol1 and callable device/fixed
band factories. It makes no new SDK call to populate this runtime reference.
The common retained catalog now has concrete family providers. An available
capability mapper alone still cannot invent an installed acquisition runtime.

## Pure family compatibility, not an activation plan

`admit_source_request()` uses an explicit operational binding, canonical facts,
runtime observation, mode and the existing family-specific request types.
Its immutable result contains a finite refusal reason or compatibility success.
It is **not** a Start permit, issued native activation plan, device readback,
applied setting, RF proof or hardware-continuity guarantee. Subsequent family
owners retain identity, geometry, backend, budget, epoch and cleanup gates.

| Source | RTBW request | Sweep request | Important boundary |
| --- | --- | --- | --- |
| AD936x | Existing `LiveConfiguration`; finite numeric center/Fs/gain/BW, observed ranges | Existing `ContinuousSweepPlanRequest` plus applied profile; one observed tuning range, usable window <= Fs/RF BW | Native geometry/backend/memory/serial revalidation still follow |
| HackRF | Existing `HackrfLiveRequest`; own LNA/VGA/filter and provenance gates | `mode_runtime_unavailable` in this integration slice | Missing host strategy is not a hardware-impossible claim; no Pluto gain translation |
| tinySA Basic/Ultra | `device_mode_unsupported` for complex-I/Q RTBW | Existing bounded `TinySaScanRawRequest`, observed model and one valid input range | Device-reported dBm, no raw I/Q/host FFT; input/settings/readback still follow |

## HackRF firmware Sweep: syntax boundary only (APP-06C partial)

The pinned official libhackrf API has a distinct `hackrf_init_sweep()` /
`hackrf_start_rx_sweep()` receive mode. Its callback data is not the fixed-centre
CI8 stream consumed by the existing HackRF RTBW owner: each 16,384-byte block
starts with `0x7f 0x7f` and an eight-byte little-endian reported tuned frequency,
then interleaved CI8. The native `parse_hackrf_sweep_block()` extracts one
aligned block without allocation or payload copy, and refuses wrong-size or
wrong-marker input. The returned view borrows the callback memory.

`syntax_valid()` means only that a block has this byte layout. The parser
deliberately retains any 64-bit header value, including values outside a
particular product, firmware or requested Sweep range. Neither the decoded
frequency nor the CI8 span is authorized for spectrum publication by this
parser. A future **same-owner** Sweep coordinator must first validate observed
device/API capability, requested ranges and step plan, block order, retune
settling, discontinuities, geometry and epoch; reject or mark gaps without
inheriting a previous block's frequency; and pass admitted CI8 to the common
analytical DSP and progressive Analyzer presentation. It must not feed these
headered bytes into the current fixed-centre RTBW ingress.

The next native `HackrfSweepSequenceGate` layer precomputes the pinned
firmware's one-block-per-tune `LINEAR` or `INTERLEAVED` header sequence from
up to ten sorted, disjoint MHz ranges and a whole-step width. In interleaved
mode the reported headers alternate between the step origin and origin plus
one-quarter step; the configured RF LO offset is **not** added to the header.
This is checked against the pinned `usb_api_sweep.c` recurrence, not inferred
from screen pixels. Callback transfers must be block-aligned and bounded;
each block is offered synchronously to a same-owner sink so a failed handoff
marks the very next block as gapped, even within one transfer. The gate keeps
separate scan and continuity epochs, known skipped-header indices and
unlocated rejection counters; these are **not** RF duty, USB-lossless or
physical sample-loss measurements. The epochs are host-inferred logical
provenance: the firmware header has no unique sweep counter, so an early
repeated origin after other progress is marked as gapped but cannot itself
prove a new physical sweep. It does not inspect RF settling or decide
which FFT bins form a trustworthy usable subband. Both admission layers must
remain upstream of any I/Q-to-spectrum publication.

The next source-owner layer is still **not** a selectable HackRF Sweep mode.
An explicit native `HackrfSweepSession` now uses the same official one-device
port implementation selected through a Sweep-typed factory with mandatory
serial words. Before configuring RF it validates the plan and bounded queue,
opens exactly one matching HackRF One, reads USB API >=0x0104 and checks the
SDK transfer size against complete 16,384-byte blocks. It applies the
requested Fs/filter/LNA/VGA, explicitly keeps amplifier and bias tee off,
initializes firmware Sweep for exactly **one block per tune**, and calls
`hackrf_start_rx_sweep()` rather than the fixed-centre RX start. Header order
is checked in the callback; only admitted CI8 and its reported-header/
logical-epoch metadata are copied into a preallocated bounded queue. Queue
pressure and callback contention are explicit gaps, not silent stale bins.
The queue maximum is 256 blocks of 16,374 CI8 bytes; it is not an unbounded
raw-I/Q recording or a promise of continuous samples.

The source owner does not perform FFT, choose RF-settled/usable bins, stitch
overlap, publish common Spectrum/Waterfall frames, expose a Python/UI V2
Start route, or establish RF coverage. Live gate metrics are deliberately
unavailable until callback quiescence; zero must not mean "no loss". Stop
distinguishes complete handle release from a clean vendor RF-off result and
retains the first error. In pinned libhackrf, a failed RF-off command can
follow successful transfer cancellation, making a second SDK Stop impossible;
the Sweep owner closes the **same** handle instead. Catastrophic SDK thread-
join failure remains an unproven callback-lifetime case and is fail-stop.
The existing fixed-band RTBW session has not yet adopted this Stop-error
recovery path; its separate failure injection and repair remain open.

The next **native analysis-only** layer now consumes copied blocks from that
same Sweep owner. It pins the currently examined interleaved 20 MS/s,
15 MHz requested filter, 7.5 MHz LO offset and 20 MHz step profile. For each
16,384-byte firmware block it transforms only the final `N` CI8 samples,
never carrying FFT history across a retune; the admitted `N` range is
1024–4096. It maps the pinned official host's two **separate 5 MHz numerical
crops** per header into the canonical `ContinuousSweepLineAssembler`. The
line publishes physical `Fs/N` and `N`, plus 5 MHz / `N/4` per-window
analysis geometry; the upper requested stop is exclusive. On-demand native
preview exposes acquired versus pending segments without finalizing a line.
Missing callbacks/headers suppress the remainder of that logical scan and
flush a terminal gap. The quality flags retain uncalibrated values, estimated
host timestamp and unverified retune settling. `Complete` means all planned
segments arrived numerically, **not** measured RF-flatness, filter-edge
validity, lossless USB, RF duty, probability of detection or GUI publication.
These edge bins still follow the host-compatible crop; a characterized guard
or RF qualification must precede any claim of usable physical coverage.
The adapter has no Python/UI V2 Start route yet and does not change the
table's `mode_runtime_unavailable` result for HackRF Sweep.

The next bounded **native runtime owner** now connects that same official
Sweep callback, copied-block queue and numerical analysis on one worker.
Its public drain returns only one ordered reduced publication at a time:
an older terminal `SweepLineFrame` cannot be overtaken by a newer
`SweepProgressFrame`. At most one latest partial and one latest terminal are
retained; superseded publications are counted, not mislabeled as RF/USB
loss. A native preview cadence of 1–100 Hz limits snapshot construction;
it is not analytical FFT rate, GUI frame rate or DWM FPS. Explicit Stop
quiesces the callback, preserves and drains already copied blocks, then
finalizes the line before joining the worker and releasing its owner.
An abnormal worker exit accounts remaining queue blocks only after join.
The source's old standalone Stop retains its default discard policy.
The optional, explicit-confirmation runtime diagnostic is not a Python
factory or selectable UI route; old packaged modules cannot activate Sweep
through this addition.

This foundation does **not** change the table's HackRF Sweep
`mode_runtime_unavailable` result, enable hardware Sweep, alter RTBW Fs/FFT/
gain/detector/grouping, or qualify UI latency/RF coverage. Its native tests
cover malformed blocks, little-endian decoding, borrowed CI8, former RF
boundary values and an aligned multi-block transfer; physical and frozen V2
Sweep acceptance remains open.

Missing identity, range or transport evidence is reported as unverified, not
as a frequency error or confirmed unsupported hardware. tinySA input ranges
are not stitched into one unsupported command. Existing product limits remain
10001 points and 30005 scanraw frame bytes; higher requests are not enabled.
The external correction identity stays separate from device-reported built-in
dBm calibration; no second dBFS conversion or calibration is applied here.

## Existing product paths connected to the guard

NativeLive discovery and route selection retain snapshot and calibration
identity from the **same** already-owned read-only observation. Selection
refreshes firmware/ranges without mutating prior inventories. Serial-based
USB/IP grouping cannot publish a canonical aggregate when firmware or
non-transport capability facts disagree, even if serial and ranges match.
Failed observation cleanup continues to block catalog access and new owners.

For known canonical AD936x devices, `start_admitted()` refuses incompatibility
before dispatch and without changing Live state or taking cleanup ownership.
Raw `start()` also refuses before SDK factory access. The exact immutable
profile used by Start is rechecked after any error-engine recovery and reused
for applied/readback publication; an earlier check cannot validate a later
replacement profile. Explicit unverified routes keep their previous owner
compatibility, but finite scalar validation still precedes factory access.
They do not silently become admitted stable catalog entries.

The stopped CPU Sweep lease captures immutable catalog/profile facts. Its
optional typed validator is called by continuous Sweep preflight before native
segment/configuration/coordinator construction. A refused Start releases the
logical lease without opening a receiver. It does not consult changing Live
configuration. Older explicit/fake leases may omit this callback; that
compatibility is not evidence of stable canonical admission. Existing Sweep
geometry, backend, resource and actual owner checks remain mandatory.

All additions are low-rate control-plane work. No per-FFT catalog rebuild,
new render lane, sample copying, hidden restart, native wire-schema change or
performance claim is introduced.

## Retained control-plane catalog and V2 shutdown

`SourceCapabilityCatalog` retains the SAME provider instances across failures.
It consumes at most 17 provider references and admits 1..16 unique adapters;
per-family outputs and the merged inventory use the existing finite inventory
model. Constructor and lookup open no radio/serial port. Refresh invalidates
old physical facts before new discovery; selected observation invalidates the
selected family's old facts before re-probing. Conflicting or over-bound merged
facts clear the published cache, rather than leaving previously accepted data.
Redacted failure reasons distinguish busy, pending release, contract and provider
failures. A failed release blocks new discovery/selected probes across families;
only explicit close can resume the same retained owners, never hidden retry.

Default service composition constructs one registry alongside NativeLive. Its
effecting operations share NativeLive's non-queue recording/control transaction,
excluding RTBW engine/poller, Sweep leases and unresolved stream release. A
read-only probe cannot stop or replace those owners. This exclusion applies to
the composed graph, NOT unrelated direct SDK/Legacy callers or other processes.
UI V2 binds that same catalog to the Live lifecycle worker: analyzer cancellation,
backend Stop/join, then catalog close. Independent cleanup is attempted even
after another cleanup failure; the first failure remains visible and terminal
shutdown is not falsely accepted. Inert/non-native test graphs may have no catalog.

AD936x uses NativeLive's existing owned observation and operational IDs. HackRF
uses the retained official read-only adapter only if the already-loaded native
module has exact factory protocol2, its sibling manifest matches the module
SHA-256, and the exact three app-local runtime DLL hashes match. No alternate
native loader, user-machine SDK search or compatibility shim is introduced.
This is a STATIC manifest locator, not in-memory shared-DLL/frozen attestation.
Unavailable official runtime produces no SDK probe and no fake hardware support.

tinySA Discover is PnP-only and returns unverified candidates. Selected canonical
observation requires a unique observed USB serial, then a lazy retained serial
owner sends ONLY `version\r`, with DTR/RTS disabled, bounded response and terminal
prompt. The endpoint is resolved before and after the command; a route change,
ambiguous identity, short write, invalid/oversized response or unconfirmed close
fails closed. The Python serial object remains reachable after partial open or
failed close; no raw OS handle is retried. Existing model mapping still requires
normalized, route-free firmware text <=128 characters. Longer/route-like version
strings are refused, not silently truncated or assigned a fake calibration key.
USB serial/model/version do NOT prove continuity, RF accuracy or calibration.
Probing a second tinySA retains both current same-family observations until a
fresh discovery deliberately invalidates them.

## Shared Analyzer source selection (first APP-06C packet)

Production UI V2 now routes its existing Analyzer Discover/select controls
through that SAME retained catalog, not a parallel discovery model. A low-rate
immutable source selection references the exact operational binding and runtime
objects. Its bounded revision is a selection revision, never an RF configuration
generation, receiver epoch, timestamp or continuity claim. Current-state reads
perform no SDK calls and take no long-held I/O lock. The Analyzer reserves idle
control while discovery/observation runs in the existing presenter worker; Start,
mode changes, other control and bounded Sweep tools refuse that reservation.

Discovery publishes at most 32 choices without automatic selection. Labels use
model/transport and an opaque operational-ID suffix, not a serial/COM/URI. Selected
AD936x is observed exactly once through the retained NativeLive provider. Its
actual native snapshot/configuration and current high-Fs RTBW/Sweep paths remain
authoritative. Manual AD936x URI selection stays a separate explicit native
action. Its current operational binding is displayed but is not inserted into
catalog truth or inferred to be an alias of an existing USB/IP device.

The Analyzer's default **Discover USB** action uses the retained catalog's
local-only discovery route. It still enumerates local AD936x, HackRF and tinySA
candidates, but does not start libiio's IP broadcast scan. **USB + IP** is a
separate explicit action over the same catalog and ownership gates. libiio's
network scan has no cancellation handle and can take a long time on some host
networks; the UI warns before offering that route. Neither action silently
retries, selects a substitute device or claims a bounded network-scan latency.

Source change or failed observation clears the prior accepted configuration,
draft, Spectrum/Waterfall/persistence presentation and preparation cache. Delayed
native snapshots with a different source and older selection acknowledgements
cannot restore those facts. Control signals carry this selection separately from
measurement snapshots, including failed commands; no per-FFT catalog formatting
or new renderer lane is introduced. Disposal disconnects its signal and terminal
release drops both selection and device-list references.

New subscribers receive the cached immutable selection before their first
Discover/selection acknowledgement, without opening a device. Cold startup and
foreign selection hide ALL AD936x quick controls, their labels and Apply/Discard
actions, including the FFT/Gain editors moved out of the settings drawer. Returning
to an observed AD936x restores those same editors with a fresh draft, not the
unsubmitted gain from a previously selected source.

HackRF/tinySA are selectable typed control-plane choices at this checkpoint,
NOT completed family acquisition paths. UI hides old Pluto-specific parameters
and refuses their Apply/Start/preflight before native dispatch. The empty native
publication has no device/config/frame and unit `unavailable`; it is not a fake
HackRF/tinySA Live snapshot. tinySA selects local Sweep-only intent, with no
claim of RTBW, raw I/Q or a host FFT. The UI explicitly reports that family
configuration/acquisition integration remains pending. Existing separate tinySA
activation is still compatibility work, not the final shared Analyzer design.

Failed retained release clears selectable facts and visibly requires explicit
Stop. That Stop routes to catalog cleanup even in local Sweep mode and even
without a native snapshot; it never cancels an unrelated receiver/Sweep. Failure
remains visible and quarantined, and only a subsequent explicit retry resumes
the SAME providers. This is composed-graph exclusion, not a process-global or
direct SDK arbiter.

## Common HackRF RTBW (APP-06C acquisition packet)

The next source packet supersedes the earlier HackRF **selection-only** state:
one family router now dispatches beneath the EXISTING Analyzer lifecycle. It
retains the exact dispatched port before SDK effects and uses it for Stop, not
a newly selected source. Native AD936x admission remains atomic. There is no
second lifecycle controller, new renderer, fake Pluto descriptor/applied profile
or automatic fallback. Optional unavailable HackRF runtime remains explicit.

Production DI uses the SAME loaded native module and statically qualified sibling
SDK folder as the retained catalog. Selection binds existing capability/runtime
objects, not a second truth model. A typed HackRF draft keeps LNA/VGA separate,
uses selection-revision and expected-profile-generation conflicts, and performs
NO hardware I/O when staged. Explicit Start rechecks the exact catalog references,
pure capability admission, current enumeration permit and same-opened-handle serial
factory admission. Each attempt receives a fresh generation/application epoch.
Staged/requested Fs and successful setters are NOT hardware rate readback.

The common graph reserves a logical foreign-owner token BEFORE SDK effects.
Native Discover/select/configuration/Start/Sweep/recording refuse that token,
including after failed activation, Stop, observer close or poller join. Only
explicit Stop of the SAME coordinator/identity observer and confirmed join clears
it. This is per-composed-graph exclusion, NOT process-global/Legacy SDK arbitration
or APP-07 parallel resource scheduling. A malformed foreign coarse control is
retained in quarantine; no guessed close, replacement or destructor retry.
Official consumed-pointer-close semantics remain unchanged.

A bounded off-Qt reduced-spectrum poller feeds existing LiveSnapshot/
LiveSpectrumFrame -> shared preparation -> Spectrum/Waterfall. No IQ is passed
to Python. Frames require exact source/profile geometry/generation, dBFS/bin and
the owned application epoch; mismatches fail closed and require Stop. Native
timestamps preserve `host_steady_ns`, estimated/unknown quality and loss flags;
no UTC conversion, hardware timestamp or RF continuity is invented. RX identity
is unknown. Waterfall and age labels do not claim UTC timing for these frames.
Native analytical loss, presentation queue supersession and bridge coalescing
remain distinct; rates are host-ingress/native FFT observations, not RF duty/FPS.
`spectrum_snapshot_rate_hz` measures native presentation pushes BEFORE bounded
queue supersession, bridge coalescing and Qt painting, not visual LPS/FPS. Host
sequence/sample-index discontinuities, timestamp regressions and estimated-clock
block counters retain their native meanings; no hardware overflow availability
is inferred from clean host counters.

UI V2 has explicit HackRF profile staging and Start/Stop on the SAME Analyzer
canvas, amp/bias OFF and CPU only. Source changes discard drafts/history; late
foreign selection/preparation and older profile acknowledgements are rejected.
HackRF host Sweep, persistence and I/Q recording are not integrated in this
common path. TinySA was selection-only at that checkpoint (superseded below). These gaps are visible, not
converted into hardware-impossible claims or synthetic host FFT.

## Retained tinySA acquisition preparation (2026-09-27)

`prepare_tinysa_acquisition` reserves an inert acquisition object from the SAME
provider/backend/observed endpoint. It accepts only the exact retained binding
and runtime references; equal copies or stale selections refuse. This creates
no second capability inventory, serial port, measurement, settings or firmware
effect. A reserved unused owner requires explicit close; all catalog probes
refuse while it or failed cleanup remains pending.

One finite `TinySaOwnedAcquisition.collect` uses one opened Python serial object
for a fresh `version`, `zero ?`, and exactly one buffered `scanraw` command.
Model/canonical identity/firmware facts must match the existing observation via
the SAME pure capability mapping, before zero or measurement commands. USB serial
must be unique; the resolved route/identity cannot change during the transaction.
The endpoint is checked again after confirmed close before publishing the result.
PnP checks narrow races; they are NOT authenticated-device or firmware continuity
proof. Preserved instrument input/settings and upper-band Ultra operation still
require their own mode/admission qualification.

The existing exact parser retains device-reported dBm/built-in calibration
provenance, actual observed zero offset, stop-exclusive integer-Hz grid, and
read-only arrays. The maximum remains 10001 points / 30005 binary frame bytes.
Host completion time is not RF acquisition time. No complex I/Q, host FFT,
external correction, simultaneous-frequency capture, metrological accuracy or
progressive spectrum is inferred from serial chunks. This packet deliberately
does not reinterpret this instrument trace as a Pluto RTBW frame.

Cancellation is cooperative around finite reads; an in-flight port cannot be
closed by another caller without cancellation/join. No measurement retry/reopen
occurs. The exact lazy Python serial object is retained before validation/open;
failed partial-open/close blocks all new probes/owners until explicit confirmed
close. Cleanup also runs when a partial object reports is_open=False. No caller
accesses/retries an OS raw handle. Ordinary Python-object close failures remain
visible; this is not proof against an arbitrary faulty driver or injected SDK.

The existing settings command port now retains its partial-open/failed-close
owner and quarantines commands after a transport failure. Its bound executor
also retains a failed command owner; a new settings plan cannot substitute it.
Only explicit close resumes cleanup, not commands. The command allowlist, no
rollback/retry/persistent writes, and ACK-not-readback semantics are unchanged.
This is NOT new common-Analyzer settings/RBW/LNA/attenuation admission.

At the preparation-only checkpoint the V2 tinySA path was STILL selection-only: the new retained acquisition was a
backend integration prerequisite, not the common Sweep request/router/canvas
wiring. Production Start was not enabled by this preparation API. Actual
Analyzer composition, firmware/input-aware controls, epoch/unit/late-frame guards,
bounded repeat scheduling and physical visible/frozen cells remain mandatory.

## Common tinySA one-pass Analyzer integration (2026-09-27)

Production DI now supplies TinySaCommonAnalyzerService from the SAME retained
catalog and NativeLive graph exclusion. AnalyzerSweepRouter dispatches BELOW
the existing AnalyzerSessionApplicationService, not another controller. Native
Sweep remains native; an immutable TinySaSweepRequest references the EXACT
selected choice/revision. Start rechecks catalog binding/runtime and pure model/
single-input-range admission, reserves the same provider's inert owner, then
claims the graph BEFORE serial effects. No native profile, LO segments, host
FFT, sample rate or host IQ is fabricated for the instrument.

Current instrument mode is explicitly ONE PASS PER START, option 0. An off-Qt
worker emits a complete SweepLineFrame only after exact parser/identity/endpoint
and confirmed-close gates. Instrument quality is explicitly UNKNOWN, not a
clean native/reference bitmask. Original device dBm, stop-exclusive grid,
observed zero, opaque device/firmware axes and host completion semantics survive
the common Spectrum/Waterfall/preparation path. Single configuration generation
and controller epoch are captured in a typed run identity; GUI admission checks
source/selection/epoch/generation/grid/unit, not arrival order. Cancellation
before a complete response publishes an explicit all-unknown terminal gap, not
interpolated serial-chunk measurements. RF timestamp/accuracy/duty remain unknown.

Successful normal one-shot completion invokes the same nonblocking Stop/join
path. No repeated scan, reopen, restart or retry occurs. Acquisition errors stop
polling and remain visible; FAILED close is not automatically retried. Explicit
Stop cancels/joins (the newer finish-on-cancel bound is specified below), closes the same retained object and only
then releases graph exclusion. Failed join/close/release retains the obligation;
Discover/native RX/configure/recording cannot steal this graph. Closing a serial
port does NOT prove immediate termination of the instrument's internal RF scan.

The SAME Analyzer has instrument frequency/points/deadline drafts, RU/EN labels,
2..10001 cap, source reset and explicit unsupported RTBW. Current input/RBW/
atten/LNA/accuracy/spur/repeat remain PRESERVED and NOT READ BACK/CHANGED by
this path; the user must check the physical input/range. Firmware-aware settings
admission, repeated-pass scheduling, Ultra upper-band/current-input qualification,
Basic physical evidence and frozen packaging remain OPEN. Production common
instrument graphs do not expose the old independent tinySA source/analyzer
workspace, avoiding a second serial owner. Isolated compatibility/test graphs
retain their old factories; no process-global SDK arbitration is claimed.

## Explicit repeated tinySA Sweep on the same Analyzer (2026-09-27)

The one-pass default above remains unchanged. A separate, unchecked-by-default
V2 control explicitly requests host repeated passes until Stop. This is NOT the
instrument's `repeat` averaging setting or continuous scanraw option 2/3. Every
measurement still uses buffered option 0; existing input/RBW/atten/LNA/accuracy/
spur/averaging settings are preserved, not changed or read back.

One request holds ONE same serial object and the same graph claim throughout
the loop. Open/reset occurs once. Every pass must consume a complete bounded
binary frame AND its bounded ASCII completion prompt, then recheck the endpoint,
before publication. Only after a cancellable inter-pass wait (UI default 0.1 s)
may the same owner issue fresh version/identity, zero and measurement commands
for the next pass. No input discard, reopen, command retry or replacement owner
is allowed between passes. Invalid/incomplete framing, changed identity, transport
or callback failure exits and closes once; failed close retains the same owner.
The legacy closed one-shot collection keeps its mandatory `port_closed` field
and positional field order; an open-port pass uses a distinct result type and
does NOT claim port closure.

Each complete pass reaches the SAME Spectrum/Waterfall/preparation path with
increasing sequence, a single run epoch/configuration generation, device dBm
and existing UNKNOWN quality. Older prepared sequences cannot replace newer
ones. The producer holds one latest snapshot, not an unbounded queue or a
lossless all-pass history guarantee. Waterfall uses the existing bounded history.
The completed-line rate is HOST publication cadence, not device RF timing,
FFT throughput, visual FPS or raw transport loss. Open-pass elapsed time includes
zero/scan transaction and host collection, excluding version and inter-pass wait.

Explicit Stop cancels and joins before closing/releasing the same graph claim.
Stop between completed passes keeps the last complete trace without a fake gap;
Stop during a consumed unfinished measurement emits a terminal unknown gap with
the next sequence. Failed join/close keeps the owner and blocks another Start.
Port closure still does NOT prove immediate termination of internal instrument
RF scanning. Cancel-to-next-Start physical resynchronization, Basic/Ultra upper
input/settings qualification and frozen executable acceptance remain separate.

tinySA measurement duration is an instrument/profile characteristic, NOT by
itself an application defect. Do not apply SDR FFT-rate/high-Fs or a 50-ms RF
sweep deadline to this instrument. Keep device scan/response duration separate
from completion-to-UI delay and GUI/Stop responsiveness. A slow but valid full
response is not a failed UI freshness gate before that response exists. Serial
chunks must still not be marketed as progressive simultaneous RF measurements.
Actual RF scan duration and RBW were not read back by this path; host elapsed
collection is not a calibrated RF-time measurement. Failure to resynchronize
after cancellation is a different protocol/lifecycle debt, never excused or
diagnosed solely by the instrument's normally low scanning speed.

## tinySA cancellation at a confirmed response boundary (2026-09-27)

The common owned path now finishes a CONSUMED scan response before serial close.
Stop cancels publication/new passes, not the already-running firmware command.
The SAME acquisition worker continues bounded reads through the exact binary
frame AND completion prompt, using the original absolute scan deadline. Only
then is the cancelled result suppressed and a terminal unknown gap emitted.
No abort/abort-on/pause/reset/resume command, input purge, second measurement,
reopen or command retry is introduced. One-shot owned acquisition also requires
the prompt; the legacy standalone collector's default close contract remains.

The shared off-Qt Stop worker joins for at most the requested response deadline
plus five seconds (maximum 125 s). Qt remains responsive and state is Stopping;
the graph remains claimed until join/close/release. A slow remaining pass can
therefore delay Stop completion, not block Qt or license another Start. This
is not immediate hardware RF Stop or a 50-ms tinySA scan/Stop requirement.
Cancellation during a consumed version/zero query also finishes that bounded
ASCII prompt, then refuses the subsequent command. Their existing two-second
deadlines are unchanged; a version/zero cancellation cannot issue a new scan.
Missing/malformed prompt, transport or close failure is NOT successful protocol
recovery; a confirmed transport close alone still does not prove firmware-ready.
Recovery from an already-stranded older session remains separate physical debt.

Immutable cached failure phase/reason codes retain version/zero/scan/close and
deadline/framing/bound/identity/transport distinctions without route/raw response
or injected exception text. First recorded failure survives explicit cleanup.
These are host protocol diagnostics, not RF quality/readback or continuity proof.
Actual current-firmware cancel-to-next-Start and malformed/session recovery still
require physical evidence; software tests do not replace them.

## tinySA same-owner runtime settings and post-pass readback (2026-09-27)

The SAME common Analyzer now admits an immutable TinySaSweepSettingsPlan,
explicit input intent and optional post-pass query policy. Existing setting
types moved to the Qt/transport-free domain; legacy settings/policy modules
re-export the SAME types, not a parallel settings truth model. All defaults
preserve current settings; changing a drawer draft performs no serial work.
The V2 settings button opens a bounded scrollable overlay; RU/EN changes keep
the same draft, Esc closes only the drawer, and controls lock with acquisition.

The EXISTING DeviceCapabilitySnapshot carries an optional evidenced opaque
runtime_control_contract. The tinySA adapter maps an observed terminal revision
suffix 26fc821 (short or full, optional git-describe g prefix) to the pinned
shell SOURCE contract; unknown/dirty/extra suffixes do not qualify. This is
NOT firmware image/build-flag attestation or RF verification. Generic runtime
availability and a device label do not grant setting commands. Unknown firmware
retains explicit preserve-only acquisition compatibility, NOT input/state proof.

Pure request admission checks model/input/range and firmware before open. Basic
LOW covers 0.1..350 MHz, HIGH 240..960 MHz; Ultra has no Basic HIGH input. Basic
has no extra-LNA/automatic-spur command in this contract, uses a 2..600-kHz target
RBW bound, and admits 0..31-dB attenuation only with explicit LOW intent. Basic
HIGH attenuation is frequency-dependent and is not represented by that setter.
Ultra target RBW is 0.2..850 kHz. Explicit LNA-on plus explicit attenuation is
refused. NSPEEDUP/WSPEEDUP, Ultra enabling/config setters and output modes are
not silently invented; upper-band current-input qualification remains OPEN.
Model bounds use the [Basic specification](https://tinysa.org/wiki/pmwiki.php?n=Main.Specification)
and [Ultra specification](https://tinysa.org/wiki/pmwiki.php?n=TinySA4.Specification).
Exact command/query syntax is tied to the [pinned shell source](https://github.com/erikkaashoek/tinySA/blob/26fc821ad3432f929630718cd290314dbc711f48/sa_cmd.c),
not an assumption that all firmware versions expose those commands.

After SAME-owner fresh version/opaque firmware admission, the worker sends at
most one input command and seven exact finite setting commands, consuming each
<=512-byte ASCII prompt within an absolute two-second response deadline. Only
the expected echo/whitespace and prompt is ACK; usage/error plus prompt is NOT
success. No saveconfig/save/flash, output, abort-on, reset, retry/reopen or
rollback is introduced. Input changes may have firmware-defined side effects;
unspecified old state is NOT promised restored/preserved after a mode command.

The configured finite pass uses the existing full scanraw decoder and common
Spectrum/Waterfall. With readback enabled, AFTER that full response the SAME
port queries rbw ?, attenuate ? and sweeptime ?, consuming/validating bounded
numeric answers before publication. Changed V2 drafts request these queries
automatically; preserve-only drafts have a separate explicit query checkbox.
The public typed service can also explicitly retain ACK-only provenance, which
has None actual values and MUST NOT imply readback/whole-profile verification.

TinySaSettingsObservation keeps EXACT plan/input/ACK accounting separate from
actual post-pass RBW, attenuation readout and instrument SCREEN-sweep time.
Quantized/dynamic RBW can differ from the target. SCREEN time is NOT measured
scanraw response/RF duration; attenuation is a host-observed scalar, not a
per-frequency calibration table. Input/LNA/spur/accuracy/repeat have no admitted
readback, even if all prompts acknowledged. Device dBm and built-in calibration
remain separate; external correction was still unapplied in that packet (superseded below). Cached UI labels show
these distinctions instead of inventing Fs/FFT/applied RF or calibrated timing.

Explicit repeated Sweep configures once on ONE port, queries after each completed
pass, and checks fresh version before the next. No setter replay between passes.
Cancellation finishes a CONSUMED setter/query prompt within its ORIGINAL deadline
and refuses subsequent commands/publication; confirmed transport close still is
not firmware-ready or immediate RF Stop. Failed close keeps the same owner and
graph claim until explicit cleanup. First fixed settings/readback phase/reason
survives cleanup; malformed/refused readback does not publish a measured frame.
Exact request/generation/epoch/settings/readback gates also reject a foreign,
missing or unrequested setting observation in prepared UI data.

Software/fake-serial and actual offscreen V2-root evidence is NOT physical Basic,
current-Ultra cancel-to-next-Start, visible Windows/FHD/QHD/DPI/DWM/FPS/soak,
firmware attestation, EXE/frozen closure or RF accuracy acceptance. Naturally
slow tinySA scanning remains a normal instrument characteristic, not an app
defect or an SDR 50-ms acquisition target.

## Explicit tinySA external frequency correction (2026-09-27)

The SAME common Analyzer now captures an optional immutable CalibrationProfile
from the EXISTING CalibrationProfileStore/service/presenter/profile view model.
There is no second store, calibration worker, renderer, serial owner or global
SDR-profile activation. The default is Do not apply. Refresh runs through the
existing off-Qt executor; up to 128 instrument profiles are listed. Store refresh
does not silently replace the selected immutable snapshot with None or a new
version; acquisition locks controls and defers list replacement. Refresh failure
shows a fixed localized status without raw filesystem/vendor exception text.
Both normal child close and parent-shell terminal release unsubscribe the drawer.

The existing profile/signature model admits a typed InstrumentCalibrationContext
with observed model, explicit declared input, opaque exact settings-intent hash,
post-pass RBW/attenuation and device-reported-dBm convention. Schema 2 represents
these instrument correction profiles, retaining the same immutable versioned
JSON store/CSV finalization path. It has no fabricated Fs, analog bandwidth,
gain, normalization, FFT unit or measured-dBFS reference points. The raw serial
field remains unknown: the existing binding's opaque device identity is NOT a
raw serial. Old SDR schema-1 serialized fields and canonical fingerprints remain
unchanged. Profile fingerprints are cached once on immutable construction, not
recomputed per repaint. The existing SDR dBFS-to-dBm operation refuses instrument
profiles; this correction adds dB to existing device dBm exactly once.

Explicit Start requires an observed canonical source/firmware/adapter, an input,
the profile's user-declared frontend chain and exact command-plan intent before
any SDK/serial effect. It forces the SAME owner's existing post-pass readback;
actual RBW/attenuation must match the captured profile. Input/LNA/spur/accuracy
intent and the frontend chain are NOT read back or RF-attested. No instrument
built-in calibration command or persistent write is introduced. The reference
plane is declared by the selected curve, not verified by the instrument.

Correction uses bounded vector interpolation on the EXACT stop-exclusive scan
grid. Scan arrays and curves are capped at 10001 points before result conversion;
the original device values, correction and author-declared curve uncertainty
remain immutable separate arrays. The correction's uncertainty is NOT total RF
measurement uncertainty. NaN gaps survive; infinities/overflow/negative
extrapolated uncertainty refuse invalid results. Curve range must cover actual
grid points unless the user explicitly allows linear extrapolation; no hidden
clamp, extrapolation or resampling of pixel-reduced data occurs.

A post-pass condition mismatch displays the unmodified original device dBm and
a visible refusal, never a fabricated zero correction or a calibrated status.
Applied/interpolated/extrapolated status includes exact profile/version/hash and
declared plane with instrument accuracy UNVERIFIED. Existing source/revision/
run/epoch/generation/unit guards additionally require the exact captured profile
object and same observed signature; a different artifact cannot replace it by
matching its profile id. The existing value-history context isolates corrected
and raw levels without inventing a new RF acquisition epoch. An interrupted
pass carries only the previous display-plane context and an all-unknown gap,
not old correction arrays, settings readback or observed calibration.

Profile selection/apply/disable is source-integrated; a complete user-facing
profile creation/import/compare wizard and correction-aware exports/Replay
remain APP-08/09 work. Backend CSV/JSON/store round trips and offscreen actual
root/fake-serial tests do NOT qualify physical RF accuracy, Basic/Ultra settings,
cancel-to-next-Start, current EXE/frozen/DWM/DPI/soak or tinySA scanning speed.
Naturally slow instrument scans remain separate from UI/processing delays.

## Explicit official CPU package and shared-DLL qualification (2026-09-27)

`build_sdr_release.ps1` accepts explicit HackrfIncludeDirectory/HackrfLibrary
ONLY in a tagged full CPU pipeline. Untagged/CUDA/skip/incomplete requests refuse;
existing tagged outputs are preserved. It invokes the official native StageOnly
lane, freezes THAT extension and its sibling factory2 SDK manifest, and excludes
the active baseline extension from analysis. No canonical activation, system-DLL
replacement, SDK search or installed application promotion is performed.

The explicitly selected app-local libiio bundle must contain exactly the same
libusb bytes as the official SDK. libusb is added once; the two additional SDK
DLLs and manifest come from the same staged native directory. Source snapshots
before/after native build and freeze bind the full pipeline to the selected
native hash. The package includes a shared-runtime input report and all-file
release manifest. A mismatched/duplicate/misplaced native/manifest/DLL payload
refuses qualification even if its files are otherwise listed in that manifest.

Official freeze uses the installed pinned STANDARD spec generator, retaining
console stdout/hide-early policy. A checked operation on its generated binary
TOC keeps the explicit sibling destination and omits PE-analysis root copies
ONLY after confirming every copy has identical selected-input bytes and BINARY
type. Different bytes, missing/repeated canonical entries, unsupported types or
generator-policy drift refuse the build. No already-frozen file is deleted or
substituted. The final verifier also compares all seven libiio hashes/source/
shared libusb against the admitted PRE-freeze report, not only final file hashes.
Default-composition smoke observes the CURRENT retained observation/route and
engine/poller/Sweep/external-owner/release state; missing owner fields fail,
rather than using the obsolete `_native_device` or guessing an inert default.

The hidden exclusive frozen diagnostic loads native exports and libiio VERSION
metadata only. It does NOT construct the HackRF factory, call SDK init/enumerate,
open an IIO context/serial device, create a GUI/Live engine or attempt RX. A bounded
Windows inventory enumerates all current-process modules, not the first matching
basename. Because the native metadata API normally unloads its temporary libiio
reference on return, the diagnostic owns one explicit absolute load-only DLL
reference during metadata/inventory and confirms its release before success.
It never changes native metadata lifetime, creates an IIO context or retries an
ambiguous unload. Required libiio/hackrf/libusb/pthread must each have one package-local
loaded module; any loaded optional libiio transport dependency must also be
package-local and unique. Optional transports may remain lazy. The external
verifier removes developer SDK PATH entries, supplies a deliberately invalid
external LIBIIO_DLL_PATH and rechecks the package/loaded paths and current file
hashes. Truncated/missing/oversized/unstable inventories fail closed.

This is package closure/load-only qualification, NOT in-memory attestation,
arbitrary process-global SDK arbitration, RF/device access, ABI compatibility
under active RX, stable physical identity, UI rendering/DPI/throughput/soak or
APP-06D/release acceptance. The existing owner/quarantine/explicit Start rules
remain unchanged. Baseline CPU/CUDA builds remain without official HackRF unless
the explicit supported package lane is chosen.

## HackRF common Analyzer DSP settings (2026-09-27)

The SAME retained RTBW owner/router/common Spectrum and Waterfall accept six
canonical CPU windows, five power detectors, explicit FFT hop (1..N subject to
the existing queue budget) and 1..256 consecutive analytical FFTs per detector
group. Sample selects the group's last FFT; peak/minimum operate on linear
power; RMS and average-power share the current mean-power implementation.
This is native signal processing, NOT averaging pixel-reduced UI frames.
Existing CPU F64/dBFS-bin/DC-OFF/Kaiser beta8.6 semantics remain explicit.
That DSP-controls checkpoint added no calibration, spur filter, CUDA, Sweep,
persistence or I/Q path; the later persistence integration is specified below.

The existing identity-bound private factory protocol2 remains compatible.
Non-default groups require optional HACKRF_DSP_PROFILE_CONTRACT_VERSION1 and
an exactly matching sibling manifest field; missing/malformed/mismatched
extensions refuse before SDK effects. Catalog locator, standalone native
preflight and frozen diagnostics check the same pair. The factory resolves
enums/optional support before consuming its one-use same-handle permit.
Legacy factory2 receives no extra keyword for its one-FFT default. There is
no alternate loader, hidden runtime substitution or same-permit SDK retry.

UI V2 has a collapsed-by-default localized DSP section using the SAME semantic
theme roles for labels/fields/actions; bounded vertical sizing returns space to
the existing graph. Expansion/locale/draft
editing perform no SDK I/O. Stage updates the immutable existing request,
preserving hidden ingress/output/presentation bounds and explicit queue policy.
All supported FFT powers256..262144 and one bounded non-preset requested Fs
can be retained without nearest-choice substitution. FFT reduction clamps ONLY
the visible draft hop; active profiles remain unchanged until explicit Start.
Controls lock during acquisition; unsupported detector groups are disabled
with a reason, rather than silently reduced to one FFT. Profile readouts are
staged intent, not hardware readback or speed acceptance.

Every common HackRF frame must additionally match the exact producer-reported
window/detector/group/CPU-F64/uncalibrated semantics. Missing or different
metadata fails closed and retains the owner until explicit Stop; request values
never fill missing producer metadata. The host spectrum-stall watchdog uses a
two-second floor, three requested group periods and a120-second hard bound,
not a measured RF/duty/display period. Analytical FFT and native output/Qt paint
counters remain distinct; larger groups naturally produce fewer new spectra.
Software/native numerical tests do not qualify actual hardware/current EXE,
visible Windows/DWM, high-Fs speed, FHD/QHD/DPI or long-soak cells.

## HackRF detector-group latency and loss interpretation (2026-09-27)

A physical USB source-process check used the SAME common application owner and
the exact c9f6837 packaged native/factory2/optionalDSP1/SDK. Three explicit
Start/Stop profiles were observed: requested20MS/s/FFT4096/group8,
16MS/s/FFT16384/group8, and20MS/s/FFT262144/group256. The latter first spectrum
arrived after5.26s, within its10.07s host watchdog, but recorded two software
ingress loss events and nonzero FFT-discard counters. Normal Stop joined the
worker and cleared callbacks/slots/ready depth and the logical owner in all
three observations. This is bounded lifecycle/profile evidence, NOT a
loss-free high-load performance or universal timing guarantee.

The existing CPU gap flush counts staged FFTs AND already computed partial
detector-group contributions as fft_frames_dropped. Never interpret that
counter as exclusively never-computed FFTs, USB packet loss, display omission
or Qt FPS. A source gap resets incomplete groups rather than averaging across
a discontinuity; group completion, native output rate, sampled latest snapshots
and unique visible paints remain different metrics. SDK-requested Fs and
host-admitted sample rate do not establish ADC readback or RF duty.

This check invoked no network/IIO discovery/context or tinySA serial and
constructed no Qt renderer. Current EXE startup/Discover was separately
observed through Windows Computer Use, but a Windows permission dialog blocked
normal UI selection/Start. Neither a backend-only check nor copied package
bytes close that actual EXE/device/transport/UI cell. Security-dialog actions
and firewall rules are never changed automatically; static-path reuse does
not guarantee future prompt suppression.


## HackRF native persistence in common Analyzer UI V2 (2026-09-27)

The SAME CPU RTBW acquisition/DSP worker, retained source owner, application
router and common Spectrum/Waterfall renderer now support native persistence.
It defaults OFF; explicit Stage and Start enable exact rolling or exponential
decay accumulation. Existing live controls remain locked: changing the active
profile requires explicit Stop, Stage and Start, with a new session/epoch.
There is no hidden restart, second renderer, raw-IQ Python loop or fake Pluto
configuration. Typed replacement preserves an existing request's hidden
half-life, rolling-window length, power limits, snapshot cadence and queue bounds.

The canonical shared PersistenceAccumulator receives detector OUTPUTS after
the CPU analytical output boundary and BEFORE the lossy spectrum presentation
queue. A group of eight analytical FFTs contributes one detector output, not
eight density observations. Analytical output eviction may still lose density
input; presentation supersession cannot. Accumulation does not invent hits for
non-finite power bins. Its normalized histogram is not RF duty, continuous
capture proof or hardware pulse-detection probability. Quality flags describe
the last contributing detector frame, not an aggregate RF-quality assessment.

The CPU producer's monotonic detector-output frame sequence is retained across
polls/evictions, not synthesized from the DIFFERENT analytical FFT counter.
Native density endpoints refer to that same output sequence. Native update,
analytical FFT, detector-output, sampled density and Qt paint counts remain
separate. The fixed-band wrapper's old analytical-counter rewrite is removed.

Density has a two-snapshot DropOldest queue, immutable shared native buffers
and bounded polling. Power bins16..4096, rolling outputs1..1000000 and snapshot
cadence10..30Hz are validated without silent clamping; the default cadence is
15Hz. Histogram, exact ring, two queued snapshots, one constructing snapshot
and one service-held snapshot must fit a conservative256MiB allocation policy.
This is separate from the64MiB spectrum queue policy and the existing UI
allocation budget; it is NOT a process RSS cap or a speed/freshness acceptance.

Identity factory protocol2 remains unchanged. Enabled density requires optional
HACKRF_PERSISTENCE_CONTRACT_VERSION1 and the exact sibling manifest pair in
the catalog locator, native preflight and frozen verifier. Both fields absent
remain compatible only with OFF requests. Mismatch, boolean or unknown versions
fail closed; optional enum/config resolution precedes one-use permit consumption
and SDK/device effects. No alternate loader or same-permit retry is introduced.
Older OFF factory calls omit the new keyword entirely.

Common density publication validates exact source/generation/profile/grid,
dBFS/bin, epoch, host clock and accumulation identity before caching. Missing
producer metadata, foreign frames, regressed update sequences or malformed
optional polling retain the owner/error until explicit Stop. Density ahead of
the displayed spectrum is marked pending by the existing bundle coherence
rules rather than attached to a wrong endpoint. Stage/new Start clear old density.
Software/native numerical gates do not qualify the actual frozen EXE, physical
RX throughput, visible Windows/DWM, FHD/QHD/DPI, RF duty or long-soak cells.

A separate bounded physical USB source-process check used exact5bb6a72 code,
staged native5fe9c637 and the same qualified official sibling SDK, not a frozen
GUI. OFF, decay and rolling requests at20MS/s/FFT4096/hop2048/group8, plus decay
at16MS/s/FFT16384/hop8192/group8, used64 power bins. Enabled accumulation counts
equaled native detector-output counts despite spectrum presentation supersession;
approximately14.86/14.86/14.33 native density images/s were published at a15Hz
requested cadence. Reported software ingress/FFT drops were zero in these short
single runs. All four explicit Stops joined the worker, cleared callbacks/slots/
ready depth and the logical owner; retained readonly density views stayed valid
and unchanged after Stop. This does NOT qualify higher/default256 power-bin
loads, lossless USB/RF capture, RF accuracy/Pd, ADC-Fs readback, Qt FPS/LPS,
current-source frozen integration or long-soak performance. Sampled pending
density endpoints were correctly withheld by the existing coherence rule.

## Required further work

### Current frozen HackRF UI observation (2026-09-28)

The exact `68e3c12` CPU onedir EXE (SHA-256 `ce758a274e3300161c354b568ccbabaac826ae9be5f7de45629812c66a2ea8d9`) was visibly driven on Windows through common UI V2: explicit HackRF USB selection, Stage at requested 20 MS/s/FFT4096/group8 with 64-bin exponential native persistence, Start showing live Spectrum/Waterfall/overlay, and explicit Stop returning to a retained last frame with Start enabled. The live UI readout showed approximately 9.76–9.93k analytical FFT/s and 1.22–1.24k native publications/s during a short observation. These displayed estimates are not DWM FPS, ADC readback, USB continuity, RF duty/Pd, 50-ms latency or soak acceptance.

The responding EXE process had one package-local `hackrf.dll`, `pthreadVC3.dll`, `libusb-1.0.dll` and native pyd loaded at the expected **on-disk** file hashes; no `libiio.dll` was loaded in this HackRF-only observation. File/path enumeration is not memory attestation or combined active Pluto/HackRF ABI proof. A subsequent attempted AD936x UI source switch was not observed because the Computer Use helper guarded user-input/minimized state; no Pluto Start was sent. Frozen Pluto/high-Fs/source-switch, genuine Ethernet/tinySA cells and independent review remain open. The diagnostic EXE is not the installed/static release.

### Native quality mask explanation in UI V2 source (2026-09-28)

The raw hexadecimal mask remains in the fixed-height Analyzer status line. Its tooltip and accessible description now decode all 16 defined native `QualityFlag` wire bits in RU/EN while preserving any unknown upper-bit mask. `0x00002001` means uncalibrated plus estimated timestamp; it is **not** an I/Q-loss flag. A zero mask is described only as no reported flags, never as RF-quality proof; an absent mask remains unknown. The source-only change does not expand the graph-stealing status label or alter Spectrum/Waterfall cadence. Unit, UI-widget/locale and full UI V2 software gates passed; no hardware/visible timing or rebuilt EXE acceptance is inferred from those tests. The exact `68e3c12` EXE above predates this tooltip change.

### Exact current frozen cross-family UI observation (2026-09-28)

An exact `4985f3d` CPU onedir diagnostic EXE (SHA-256 `beeaa649e3949c4277cd1fe80d1fcc30f8500daf874ee41d236884df249722ec`) passed its full 35/35 CTest and 355-file/frozen package pipeline, then was driven visibly through UI V2. In one process, explicit Pluto USB RTBW at RF-readback Fs 61.44 MS/s, HackRF USB RTBW at requested Fs 20 MS/s, and Pluto USB RTBW again each produced live Spectrum/Waterfall with explicit Stop between owners. Source switches cleared the preceding family's retained frame and required a fresh staged profile. During the second Pluto Live, read-only module enumeration showed package-local `libiio.dll`, `hackrf.dll` and one `libusb-1.0.dll` loaded together. These are sequential RX and combined loaded-path observations, not simultaneous RX, in-memory DLL hash attestation or long-soak ABI proof.

The HackRF default detector group of one gave approximately 9.7k analytical FFT/s and 9.7k native publications/s but no visible live frame in repeated observations over about nine seconds; a spectrum appeared at/after explicit Stop. Explicit group sizes 64 and 8 retained approximately 9.7–9.9k analytical FFT/s and about 20 MS/s admitted I/Q, while their lower native publication rates (roughly 152 and 1.24k/s) both produced moving Spectrum/Waterfall during Live. This is an open high-publication UI freshness defect/overload hypothesis, not a proven root cause or a reason to silently coarsen detector semantics. The sampled new-Qt-frame period with groups 64/8 was about 16 ms; it is not DWM FPS or 50-ms end-to-end acceptance. Detailed exact-source evidence and scope are recorded in the local APP-06D cross-family report; the diagnostic EXE is not a promoted/static release.

TinySA physical resynchronization/Basic and current-input upper-band qualification,
the full correction-profile authoring/export workflow and remaining HackRF
host-Sweep/IQ strategies and further DSP qualification remain.
Common source-process HackRF RTBW and tinySA runtime settings integration is
not frozen executable or performance acceptance.
Official shared-DLL/frozen qualification and actual high-Fs device/transport cells
remain separate acceptance gates. APP-05 performance debts, APP-06A/B,
APP-06C/D, APP-07 and release acceptance are not closed by this source packet.

### Bounded HackRF UI latest-frame bridge (2026-09-28)

Source `26070a6` adds a native presentation-only drain forwarded through the
existing HackRF DSP/acquisition/runtime owners. A call pops at most the
configured presentation capacity (maximum256), returns the newest consumed
frame and counts the other consumed frames; it cannot chase a continuously
refilling producer forever. Pybind releases the GIL during native work and
materializes at most one frame. The common V2 bridge waits nominally4ms,
not1ms, between polls. This is neither a guaranteed250Hz publication cadence
nor a4ms latency target. Old batch polling remains for existing consumers.

Native Fs, FFT/hop/window/detector grouping and upstream persistence are
unchanged. Coalescing retains canonical source/sequence/generation/unit/loss
metadata and is reported separately from acquisition or analytical FFT loss.
The V2 analyzer requires native latest-frame bridge protocol1 and a callable
drain before Stage/Start; unsupported modules are refused before RX/SDK
effects, with no automatic fallback or restart. Malformed drain results retain
the same owned control until explicit Stop. Identity factory protocol2 is
unchanged. A native/pybind test verifies separate coalescing and unchanged
upstream accumulation; the exact full V2 gate has939pass/66skip/0fail.

An exact-source CPU onedir diagnostic EXE at requested20MS/s visibly updated
Spectrum/Waterfall with group1 at FFT4096/hop2048 and FFT16384/hop8192, before
explicit Stop; the previously observed no-live-frame profile did not recur.
Sampled native FFT rates were about9.7k/s and2.44k/s respectively, with new
Qt-frame readouts about15–20ms and16–19ms. These are short UI observations,
not independent DWM FPS, 50ms end-to-end, hardware Fs, RF duty, lossless USB
or sustained performance acceptance. Native drain versus slower poll effects
were not separately isolated. No release/static installation was promoted.

The heavier FFT16384/group1/64-bin decay profile also stayed visually live,
but its overlay was not present in every observation and later frames explicitly
reported I/Q loss (stopped retained frame:12451840samples/95blocks/0FFT).
That performance cell is NOT accepted; its delta relative to the earlier
build was not isolated. Mask0x00002001 must not override separate loss
metadata. Heavy-density stage timing, loss-mask consistency, overlay coherence,
default256-bin loads, independent review and longer responsiveness checks remain
open alongside physical tinySA/Ethernet/common-family and release gates.

### HackRF density-to-spectrum coherence and bounded bin conversion (2026-09-28)

Source `53015f1` polls native density before the bounded newest-spectrum drain
and publishes both in one validated immutable V2 snapshot. A deterministic
advancing-producer test reproduced an ahead-of-spectrum density endpoint before
the change and passed afterward. The same source passed the exact 1006-test
full V2 gate (66 skipped, zero failed), native35/35 CTest and frozen355-file
package checks. Its diagnostic EXE visibly showed Spectrum, Waterfall and a
colored persistence overlay during HackRF Live at requested20MS/s,
FFT16384/hop8192/group1/64 bins. It also accumulated I/Q loss under this
heavy profile; turning persistence off for a short subsequent run reported
none. These are sequential observations, not causal isolation or long-soak
acceptance. The EXE was closed and is not a promoted static release.

Separate native20-s default256-bin FFT16384 source-process tests found
queue-full I/Q loss even without the UI: baseline admitted roughly17.8–18.1
MS/s in three runs, with 302–336 queue-full drops. Source `db1bfff` bounds
power-bin conversion before integer cast while preserving finite in-range
floor/clamp mapping and renderer row-major output. It passed 35/35 native
tests including 4/64/256-bin reference/boundary/rolling-layout checks.
Its isolated benchmark improved from about5.0–5.2k to5.6–5.8k updates/s
at FFT16384/256; two physical20-s runs admitted19.19–19.38MS/s, but still
had120–124 queue-full drops. Exact `db1bfff` full UI V2 software gate passed
1006 tests/66 skipped/zero failed with no product imports outside checkout;
this does not test the candidate in a frozen GUI. No default256 loss-free
claim, full-UI candidate performance claim, ADC readback, USB/RF duty, DWM
FPS or 50-ms acceptance follows. `QualityFlag::IqDropped` is per affected input whereas cumulative
lost-sample metadata persists; a later mask0x00002001 does not erase loss.
Further exact stage isolation and sustained current-source frozen testing
remain required; the active installed/canonical EXE is unchanged.

### HackRF profiling boundary and unresolved 256-bin throughput (2026-09-28)

The native HackRF DSP now exposes cumulative worker-stage durations only in
an explicit StageOnly profiling build. Ordinary builds have no added hot-path
clock calls and report `stage_timing_available=false` with zero durations.
The separate profiling preset cannot activate the installed module or build
an EXE; timing fields are not RF duty, input-to-paint latency, ADC/USB
throughput or GUI FPS. Full UI V2 source tests passed 1006/66 skipped/zero
failed with the exact ordinary staged build, and both ordinary and profiled
native candidates passed 35/35 CTest. The active canonical module/EXE was
not changed by this diagnostic package.

At requested20 MS/s, FFT16384/hop8192/group1, one approximately20-s
source-process run per OFF/64/256 setting measured DSP push/poll at about
14.6–14.7 s. Native persistence added about 0/2.58/4.33 s respectively,
raising the 256-bin locked worker time to about19.06 s of20 s. The 64-bin
run reported one lock-contention I/Q block drop. The 256-bin profiled run
reported no software loss **in that run only**; earlier alternating ordinary
256-bin runs still had120–124 queue-full drops. Profiling perturbs scheduling,
so these cannot be merged into a lossless acceptance claim. Python bridge
poll durations were much smaller than native persistence but exclude Qt and
DWM work; no GUI freshness bottleneck has been ruled out.

A rejected frequency-tiled density layout regressed the same synthetic
FFT16384/256-bin/15-Hz benchmark and was removed. A cadence-parameterized
standalone benchmark shows snapshot copying costs time, but reducing the
product's 15-Hz cadence cannot eliminate the per-detector-frame accumulation.
At that checkpoint, the proposed next candidate was an explicitly bounded,
per-session persistence worker with exact reduced-output accounting,
backpressure or visible gap counters,
joined Stop and exception quarantine. It is not implemented or accepted by
this diagnostic package. FFT/group/Fs, persistence resolution, calibration,
quality/loss semantics and common UI V2 owner remain unchanged. Physical
tinySA/Ethernet, sustained current-source frozen UI, independent review,
APP-05 timing debts, APP-07 and release remain open.

An opt-in bounded parallel persistence worker was evaluated but **rejected and
removed** before product activation. It passed focused native/factory/pybind
contracts, yet in matched 20/20/45-s physical source-process A/B pairs at
requested 20 MS/s, FFT16384/group1/256-bin density it did not materially
change admitted or analytical FFT rate. Its 45-s run delivered fewer fresh
Spectrum frames to Python (8072 vs 8743) and superseded more in the bounded
presentation queue (78327 vs 76499), despite draining every accepted density
input and reporting no software I/Q drop in those B runs. The inline A path
reported one lock-contention I/Q block per run. These finite observations do
not prove a general scheduler verdict, USB/ADC continuity or visible UI/DWM
timing. The experimental native module was StageOnly from uncommitted source,
not a release/EXE candidate; its staged path was subsequently rebuilt from
clean committed source with 35/35 native tests. The canonical active module,
product UI default and RF settings remain unchanged. Required Spectrum/overlay publication and Qt freshness now
take priority over this rejected worker design.

A further visible check used the **previous** exact `26070a6` diagnostic
EXE, not a rebuilt current-source application. With an explicitly staged
USB HackRF profile (requested 20 MS/s, FFT16384/hop8192/group1, exponential
density with 64 bins), Spectrum and Waterfall updated during Live, but the
retained Stop frame reported 55,967,744 dropped samples / 427 blocks / 0 FFT.
The density image was not visible in one early screenshot and was visible
after the Display panel opened and on a later screenshot. This does not
isolate producer, coherent-layer, projection, upload or repaint cause and
is not a timing or lossless-throughput acceptance. The quality mask 0x2001
still denotes uncalibrated/estimated timestamp, not an I/Q-loss flag;
separate loss metadata must remain visible. The existing Analyzer guard
retains last coherent density for an ahead-of-spectrum endpoint. Before
changing that guard or the worker architecture, measure density publication,
coherence withholding, projection request/completion, ImageItem upload and
actual paint on a matched current-source frozen build.

### Native exponential-density probability boundary (2026-09-28)

Exact source `85b2e97` fixes one confirmed cause of intermittent HackRF UI V2
persistence disappearance. The exponential histogram stores cells as `float`
but accumulates its theoretical weight as `double`. After thousands of FFT
contributions a hot cell can round slightly above the theoretical weight.
The old native probability scale then yielded values such as `1.00000262`;
the strict, intentionally unchanged UI `[0,1]` validation rejected the
whole density layer as `persistence_values_invalid`. The result was an
intermittent blank heat overlay even while coherent native density, Spectrum
and Waterfall continued. Opening Display was not established as the cause.

At the existing native snapshot cadence, the producer now checks the maximum
of its already-copied exponential histogram and, only when it exceeds the
theoretical weight, uses that maximum with a two-float-epsilon margin as the
probability denominator. The per-FFT update loop, raw histogram and Spectrum
scale, FFT/group/Fs, density geometry, snapshot cadence, common owner and
strict UI rejection of genuinely invalid external density are unchanged.
A deterministic 30,000-frame C++ regression proves the old normalization
would have exceeded one and every revised publication remains within range.
The exact clean-HEAD StageOnly native build passed 35/35 CTest (module SHA
`8d6db58d92fb58a6d7e32fb446e43d39e12219d3855bbf90a8d21474e0d110a0`);
the exact full UI V2 source gate passed 1006 tests, 66 skipped, zero failed,
with no product imports outside the checkout.

In one clean-source baseline visible Qt-root USB HackRF run at requested
20 MS/s, FFT16384/hop8192/group1/64-bin decay, five of twelve one-second
samples had a coherent native layer but an unavailable UI layer with
`persistence_values_invalid`; sampled maxima reached `1.00000262`. On the
exact fixed source, separate 18-s and 45-s visible-root runs had zero such
samples after first-frame admission, continued heat uploads/paint and normal
explicit Stop/Close. The 45-s run sampled 589 ImageItem uploads and 2021
paint calls; these are instrumented Qt-object counts, **not** DWM FPS,
end-to-end latency or an acceptance threshold. Both fixed runs reported zero
software I/Q drops **in those runs only**. The baseline and fixed sessions
were not a controlled long-duration throughput benchmark, and no frozen EXE
was built from `85b2e97`. Default256-bin loss, other devices, extended soak,
active shared-DLL/RF duty, UI timing, APP-05 debts, APP-06/07 and release
qualification remain open. The canonical installed module and static EXE
were not changed.

An additional 20-s current-source visible-root run at the same requested
Fs/FFT/hop/group but **256 power bins** used the identical fixed native bytes
under doc-only HEAD `e24f015` with an exact restaged manifest. All 20 sampled
post-first-frame UI density observations remained valid, but explicit Stop
reported **10,485,760 lost I/Q samples / 80 blocks**. Thus the numerical
overlay correction also held in this short 256-bin cell, while high-density
throughput is plainly **not accepted**. The snapshot maximum scan is bounded
to publication cadence but adds a histogram read; its cost needs matched
old/new stage timing and loss tests, not inference from unmatched runs.

### Incremental exponential maximum and bounded 256-bin comparison (2026-09-28)

Product commit `a2cb8ba` preserves the probability correction while removing
one extra full-histogram read from each exponential snapshot. The existing
per-FFT accumulator pass tracks the maximum of float cells; the rare full-grid
decay rebase recomputes it, and reset or geometry change clears it. The
snapshot still copies the full row-major grid, the probability margin and
strict UI density validation remain unchanged, and no FFT/Fs/hop/group,
snapshot cadence, detector, RF setting or presentation owner was altered.
The sustained C++ oracle now runs across a decay rebase and checks the tracked
maximum against a scan of each published snapshot. An independent read-only
review found no actionable defect in this change; the retained full-grid
snapshot and rebase under the HackRF processing lock remain a throughput risk.

In the same standalone FFT16384/2500-frame/15-Hz synthetic benchmark, the
256-bin median changed from about 0.3034 s (prior maximum scan) to 0.2832 s
(tracked maximum) over ten repetitions; the 64-bin median changed from about
0.1445 to 0.1390 s over eight repetitions. These are accumulator-only wall
times, not GUI-frame latency, ADC/USB throughput or a physical acceptance.
The exact clean `a2cb8ba` StageOnly build passed 35/35 native CTest with
module SHA-256 `329fd61f2453c91885bdccfc57106c472c39da44dfbf48ff1911d6d918959e0d`;
the exact full UI V2 source gate passed 1006 tests, 66 skipped, zero failed,
with no product imports outside the checkout. The ordinary native module and
installed/static EXE were not replaced.

Four sequential, explicit Start/Stop, approximately 20-s visible current-source
USB HackRF cells used requested 20 MS/s, FFT16384/hop8192/group1 and 256-bin
decay. Candidate/baseline/candidate/baseline lost respectively 44/39/116/200
I/Q blocks. All four had zero sampled invalid density rows after first-frame
admission, retained visible Spectrum/Waterfall/heat overlay, and normal Stop
and Close. Their upload counts were 221/230/224/236 and paint counts
490/515/513/533; these are instrumented Qt-object counts, not DWM FPS. The
large within-variant loss variation prevents attributing a loss reduction or
increase to this small CPU change. It does **not** make 256-bin capture
lossless or meet APP-05/APP-06D timing targets. Next qualification must
isolate native lock, ingress queue, snapshot copy, bridge, projection and
actual GUI paint on an exact frozen build, followed by sustained common-family
physical runs. Genuine Ethernet and tinySA cells, APP-07 and release remain
open.

A subsequent full, tagged CPU package from doc-only source `9f301ca`
retained the same native module SHA-256 above and passed 35/35 CTest, the
355-file frozen package checks and shared Pluto/HackRF runtime admission.
The diagnostic EXE SHA-256 is
`a1528eb1a1922d8bff2ccba70bfef6ad87f569aa804d4bf98c9ef7d5b0b20d02`.
The first package attempt was correctly refused before freeze because the
system IIO directory carried different `libusb-1.0.dll` bytes; the successful
attempt used the same previously hash-qualified seven-file input bundle as
the prior combined build. No system DLL was replaced. This is frozen
package evidence; the static installation is untouched.

The **same exact tagged EXE** then completed two separate visible USB HackRF
RTBW sessions with explicit Stage/Start/Stop and normal Close: requested
20 MS/s, FFT16384/hop8192/group1, LNA16/VGA20, amp/bias OFF. With 256-bin
exponential persistence, Spectrum, Waterfall and heat density changed before
Stop, but the live readout fell to about 17.7–18.1 admitted I/Q MS/s and
2.0–2.2k analytical FFT/s; the sampled new-Qt-frame period was about
74–80 ms. The final retained frame reported **96,993,280 lost samples /
740 I/Q blocks / zero FFT**. In the separate persistence-OFF control, the
same requested Fs/FFT/hop/group displayed moving Spectrum and Waterfall,
about 20.0–20.3 admitted I/Q MS/s, 2.4k FFT/s and a roughly 20-ms new-Qt
frame period; sampled and stopped status did not report I/Q loss. Both
sessions showed quality mask `0x00002001`, which is not a loss indicator;
loss belongs to separate counters. The contrast is strong evidence of
heavy-density pressure, **not** isolation of the specific native lock,
bridge or GUI stage. These short, sequential UI readouts are not DWM FPS,
RF duty/Pd, ADC/USB continuity, sustained performance, global 50-ms or
release acceptance. The tagged EXE was closed after each Stop; the older
separate user process was not touched.

### HackRF persistence worker substage diagnosis (2026-09-28)

Commit `55f0bab` adds profiling-build-only counters that partition the
existing native persistence call into histogram-update and snapshot-build
time/count. An ordinary build exposes zero counters and makes no added
steady-clock call in the hot path. This is instrumentation, not a new density
algorithm, queue, source owner, calibration, GUI path or RF setting. Both
official StageOnly profiling and ordinary modules passed 35/35 native tests
and preflight; the exact clean-source full UI V2 gate with the ordinary
module passed 1006 tests / 66 skipped / zero failed, with no product imports
outside the checkout. No new EXE was built or promoted.

In one bounded 20-s source-process USB HackRF diagnostic at requested
20 MS/s, FFT16384/hop8192/group1 and 256-bin decay, locked native processing
took 19.999 s. DSP push/poll took 14.522 s; persistence took 5.423 s, split
into 4.532 s histogram updates and 0.886 s constructing 299 snapshots.
The ready queue reached 24/24; software ingress recorded 187 lost I/Q
blocks / 24,510,464 samples (186 queue-full, one lock-contention), with
18.788 admitted MS/s and 2284 analytical FFT/s. Explicit Stop joined the
worker and released RX. Thus snapshot copying is material, but reducing its
15-Hz cadence alone cannot remove the dominant histogram load. This one
profiled run is not a matched frozen GUI cause proof, RF duty/ADC/USB
continuity, DWM FPS, global 50-ms or long-soak acceptance. The next bounded
candidate must preserve every detector contribution and compare histogram
cost, live Spectrum freshness and loss counters on matched UI V2 profiles;
prior tiled/transposed layout and parallel worker experiments were rejected.

An APP-06D diagnostic-only follow-up at exact source `15e72d1` retained the
same product algorithm and added an optional varying-spectrum native benchmark
fixture. Three local histogram arithmetic candidates showed no gain or
regressed that fixture and were fully reverted. The ordinary official native
module passed 35/35 CTest/preflight at the exact source; the public-doc-only
`65d1161` rebuild repeated 35/35 with the same binary SHA. No new EXE was
built or promoted. Four separate 20-s visible Windows UI V2 root sessions
used real USB HackRF RX at requested 20 MS/s, FFT16384/hop8192/group1 with
256→64→64→256 power bins and explicit Stage/Start/Stop/Close. A bounded
`update_sequence` observer measured native-poll-return to first
ImageItem-paint-return median/p95 of about 133/205 and 126/205 ms for the
256-bin runs (235/214 matched paints), versus 108/137 and 107/138 ms for
64 bins (265/267 paints). The projection function itself measured roughly
41 versus 21 ms median in both orders. The first pair reported zero ingress
loss; each reverse-pair run reported one dropped 131072-sample I/Q block.
The observer begins after native polling, ends before DWM scanout, and adds
diagnostic overhead. Four sequential nonrandomized runs do not establish a
causal bin-count delta or sustained performance. A bitwise-equal flattened
Direct-mapper prototype produced only small synthetic gains and was not
promoted into product code. This is neither a global 50-ms/DWM/FPS/RF-duty/
lossless transport acceptance nor a frozen-EXE qualification. APP-06A/B/C/D,
genuine Ethernet/tinySA physical cells, APP-05 timing debt, APP-07 and release
remain open.

### Optional common-Analyzer HackRF Sweep route (APP-06C, 2026-09-28)

An exact, already-loaded official HackRF module may now expose paired optional
Sweep bridge/factory protocol 1. The module and sibling manifest must agree,
the module hash and official factory protocol 2 must match, and the required
runtime DLLs must be qualified before the route is composed. An old or
SDK-OFF module remains selectable for its supported modes but cannot Start
HackRF Sweep. Discovery, draft editing and mode selection do not activate RX.

The new immutable `HackrfSweepRequest` captures the exact selected catalog
choice and revision. It admits whole-MHz bounds, a 20–320 MHz span divisible
by 20 MHz, FFT 1024/2048/4096, LNA 0–40 dB in 8-dB steps, VGA 0–62 dB in
2-dB steps and a requested UI poll ceiling of 1–100 Hz. The native factory
pins requested 20 MS/s, a 15-MHz filter, interleaved 20-MHz retunes, Hann
window, Sample detector and dBFS/bin. The UI poll ceiling does not change
native analytical FFT throughput or prove RF duty or visualization FPS.

The common Analyzer application assigns a positive acquisition/configuration
epoch, checks source identity and runtime compatibility before claiming RX,
and dispatches a single same-owner native Sweep session. Ordered native
partial/terminal publications enter the existing Spectrum/Waterfall bundle
path, with source/epoch/unit/generation guards. A separate compact family
control bar appears on the **same** Analyzer tab; switching RTBW/Sweep intent
does not retune or Start hardware. A benign Live-state notification cannot
silently revert a selected HackRF Sweep to RTBW. Explicit Stop retains the
same owner until release is confirmed; a factory failure with uncertain SDK
effects remains blocked, never falls back to another handle or auto-restarts.

Numerical complete-line LPS is derived from the native completed-line count;
native publication supersession is reported separately from I/Q/USB loss.
This route does not validate RF-flatness or edge coverage, perform I/Q
recording, enable TX/amplifier/bias, or assert lossless transport, pulse
detection probability, DWM FPS, GUI latency or long-soak performance. Wide
partial-preview physical observation, exact frozen EXE UI qualification,
genuine Ethernet/tinySA cells and sustained cross-family acceptance remain
open; APP-06A/B/C/D are still partial.

### Exact frozen visible cross-family follow-up (APP-06C/D, 2026-09-28)

An exact-source `74946ad` CPU onedir diagnostic EXE was built under a
process-local clean `PATH` and passed strict 656-file frozen/package checks.
The earlier inherited-`PATH` candidate was rejected for unrelated Poppler
ICU DLL inputs; no post-freeze repair or static/current promotion occurred.
The candidate EXE SHA-256 is
`A531548965A0939A02BE88B7C93D82C2889505461DBF06F9AD587F9D008B4469`.

One visible Windows UI V2 session explicitly ran USB Pluto RTBW, switched
after Stop to USB HackRF Sweep 100–420 MHz with two separate Start/Stop
epochs, switched back after Stop to USB Pluto RTBW, then ran USB Pluto
Sweep 100–420 MHz. Both source switches cleared the old plot; all five
explicit acquisitions used common Analyzer Spectrum/Waterfall. HackRF showed
partial and complete 64-segment lines before Stop; a mid-pass Stop retained
an explicitly gapped cancellation frame. The Pluto Sweep used a staged
61.44-MS/s, 50-MHz-RF-BW profile and 36-MHz window/2-MHz overlap plan
over ten segments; it showed partial coverage before pass completion and
an explicitly gapped 8/10 cancellation frame. The diagnostic process was
normally closed after explicit Stops.

The Pluto Sweep UI reported about 180 ms between new Qt frames and no
complete-pass period; this is a timing debt, not acceptance. HackRF's
short 24–29-ms complete-pass UI estimates are not sustained or RF-qualified
LPS. Configured/staged Fs, UI FFT counts, readbacks and software counters
do not prove ADC delivery, lossless USB, RF duty/Pd, usable RF coverage or
DWM FPS. Genuine Ethernet, qualified tinySA, high-load/soak, DWM FHD/QHD/DPI
and end-to-end timing remain open; APP-06A/B/C/D are still partial.

### Host-observed Sweep completed-pass period (APP-06D, 2026-09-28)

Source `62dfecb` uses one small rate observer for the Pluto and HackRF Sweep
display services. It samples each native cumulative completed-line counter,
holds a positive LPS estimate across UI polls with no new completed line,
expires the estimate after at least one second or three observed pass periods
(capped at 120 seconds), and resets it at an explicit new Start. A regression
of count or monotonic observation time is rejected. The reciprocal UI period
is a host-observed estimate, not one pass's producer timestamp, RF revisit,
capture duty or pulse-detection probability.

The exact tracked source passed 1005 UI V2 tests with 66 expected skips and
no failure on the pinned Qt 6.11.1 interpreter. A separate official CPU
onedir diagnostic package passed 40/40 native CTest and strict 658-file
frozen checks using the already qualified shared libusb bundle; EXE SHA-256
`846B16488CD623A8C3AB74FD649F1937DA040B32804D7F373F28EB4B8580FC36`.
One short visible USB Pluto 100–420 MHz Sweep showed numeric complete-pass
readouts of about 1801/1836 ms while partial coverage was visible. Explicit
Stop retained a gapped frame and made the live period unavailable; the
diagnostic process was normally closed. This does not resolve the roughly
180-ms new-Qt-frame period or qualify sustained LPS, RF coverage, ADC duty,
lossless transport, DWM FPS, genuine Ethernet/tinySA or APP-06 completion.

### Sweep stage-observer boundary (APP-06D follow-up, 2026-09-28)

The opt-in physical UI V2 Sweep observer now records exact source/epoch/pass/
state/revision joins across host poll return, presenter preparation, Qt model
entry and Spectrum/Waterfall paint return. Optional requested RF bandwidth
and speed preset must match the applied CPU configuration. Host poll return
already follows native latest-slot coalescing and is **not** analytical-ready
time; paint return is not DWM scanout. This observer-only change does not alter
product acquisition, DSP or presentation algorithms.

A separate Python-process physical attempt with the exact frozen native
binary failed at USB Pluto selection before RX, so its empty trace is **not**
a 61.44-MS/s Sweep timing result. The same diagnostic EXE separately showed
working USB Pluto 20-MS/s RTBW Live with package-local IIO/USB DLLs; that does
not explain the Python-process discrepancy or the approximately 180-ms Sweep
cadence. APP-05 timing and APP-06 physical acceptance remain open.

### Frozen Pluto Sweep stage split (APP-06D follow-up, 2026-09-28)

The diagnostic observer gained an **opt-in** local USB Discover selection:
it requires the one descriptor that exactly matches the explicitly requested
USB URI and confirms its presence in the UI V2 catalog. Package-local IIO
reported the attached AD9364 Pluto at `usb:2.4.5`, whereas system
`iio_info` had reported `usb:3.4.5`; manual use of the latter in the frozen
package failed before RX. This is a route-enumeration discrepancy, not a
reason to infer a product failure or to substitute a nearby device silently.

One separately frozen observer EXE with the previously qualified native
module passed two bounded physical UI V2 runs at requested/applied
61.44 MS/s, FFT4096, 50-MHz RF bandwidth and 100–420-MHz ten-segment Sweep,
one `quick` and one `averaged`. Both delivered nine exact-identity partial
Spectrum/Waterfall updates and one numerical complete pass **before Stop**;
Stop produced a painted terminal gap and the process closed. Each had 22/22
matched host-poll→prepare→model→canvas-paint events and no observer overflow.
Estimated native first-sample interval averaged ~183.7 ms and ~181.5 ms;
host poll-return→paint averaged ~6.8 ms for Waterfall and ~30.6–30.7 ms for
Spectrum. Spectrum maxima ~65–70 ms, so the separate APP-05 relevant-paint
target remains open. Those small-run stage timings do not start at analytical
ready, prove actual RF acquisition time, or include desktop scanout.

The current default uses 262144 complex samples per acquisition buffer and
discards two such blocks after each segment restart. A stop/configure/start
and buffer/refill/discard bottleneck is now a concrete **unisolated**
hypothesis; neither the 61.44-MS/s setting nor a single pass proves lossless
USB, RF settling/coverage, sustained LPS or pulse-detection probability.
Both runs emitted post-Stop IIO `READ ... -9` stderr, still unexplained.
No product/native acquisition, DSP, UI rendering or release EXE changed in
this observer packet. APP-05/06D, genuine Ethernet/tinySA and release
qualification remain open; private raw traces and the detailed report are
local-only.

### Native multi-segment Sweep phase timers (APP-06D diagnostic, 2026-09-28)

The coordinator now exposes four additive, scalar host `steady_clock`
phase summaries for **multi-segment** Sweep: successful engine Stop,
Configure and Start calls, plus each wait for a current-generation FFT frame.
Each summary has count, total nanoseconds and maximum nanoseconds; the
coordinator's configured epoch resets all four. A post-Stop snapshot is the
consistent comparison point, since a live relaxed read can straddle updates.
Single-window RTBW-style Sweep remains on its separate continuous path and
reports zero for these phase timers. The native/Python binding and physical
observer require the new timing ABI explicitly before RX when that diagnostic
option is requested. No raw I/Q/FFT array, RF timestamp, hidden restart or
new product setting is added. Clock reads do not change Fs, FFT, detector,
buffer geometry, discard count or render cadence. These are host stage
durations, **not** RF dwell, transport utilization or an APP-05 paint SLA.

The source-side regression checks assert counter reset/phase presence and
reject inconsistent frozen timing summaries. CTest and full UI V2 source
gates are separate from physical/frozen performance qualification. Do not
attribute the prior ~180-ms segment cadence to one phase until the exact
new native artifact is staged, frozen and tested on hardware.

### Exact frozen Pluto Sweep phase attribution (APP-06D, 2026-09-28)

Exact source `99d30d3` StageOnly official native and full UI V2 gates passed;
the separate frozen diagnostic with the same new native module passed static
shared-runtime/hash checks. Two local-Discover physical USB Pluto UI V2
100–420-MHz/61.44-MS/s/FFT4096/50-MHz RF-BW runs at unchanged
262144-sample buffers and two post-retune discarded blocks gave 10 complete
segments and live partial Spectrum/Waterfall before explicit Stop. Across
11 started native phase calls, current-generation FFT wait averaged
~108–110 ms, Start ~39 ms and Configure ~18–19 ms; ten Stops averaged
<1 ms. Host partial interval remained ~181 ms. Both runs had 30 full
262144-sample I/Q blocks and 1270 analytical FFT frames with zero reported
short reads/refill errors/acquisition queue drops; these host counters do not
prove USB losslessness, hardware ADC continuity, RF settling or duty/Pd.
Quick1 and averaged16 output FFT choices had essentially the same cadence.
Host poll-return→Spectrum paint still had a ~69–70-ms maximum in these
short runs, so the separate APP-05 ≤50-ms target remains open. The exact
stage totals include a Stop-cancelled next pass and are not an exact
per-accepted-segment decomposition or a product LPS guarantee. The next
candidate is an explicit smaller diagnostic buffer at unchanged Fs/FFT and
discard policy, followed by RF quality checks; a real stream-preserving
retune needs separate design/proof. No default acquisition/render algorithm,
approved EXE or system setting was changed in this instrumentation packet.
