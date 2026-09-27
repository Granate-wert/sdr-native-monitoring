# APP-06A: common catalog joins and pre-SDK request compatibility

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

## Required further work

TinySA physical resynchronization/Basic and current-input upper-band qualification,
the full correction-profile authoring/export workflow and advanced HackRF DSP controls/strategies remain.
Common source-process HackRF RTBW and tinySA runtime settings integration is
not frozen executable or performance acceptance.
Official shared-DLL/frozen packaging and actual high-Fs device/transport cells
also remain separate acceptance gates. APP-05 performance debts, APP-06A/B,
APP-06C/D, APP-07 and release acceptance are not closed by this source packet.
