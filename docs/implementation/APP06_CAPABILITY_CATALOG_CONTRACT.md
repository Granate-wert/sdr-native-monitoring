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
common path. TinySA remains selection-only here. These gaps are visible, not
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

The V2 tinySA path is STILL selection-only: the new retained acquisition is a
backend integration prerequisite, not the common Sweep request/router/canvas
wiring. Production Start has not been enabled by this preparation API. Actual
Analyzer composition, firmware/input-aware controls, epoch/unit/late-frame guards,
bounded repeat scheduling and physical visible/frozen cells remain mandatory.

## Required further work

TinySA family-specific settings/device-dBm trace acquisition and advanced HackRF
DSP controls/strategies remain. Common source-process HackRF RTBW integration is
not frozen executable or performance acceptance.
Official shared-DLL/frozen packaging and actual high-Fs device/transport cells
also remain separate acceptance gates. APP-05 performance debts, APP-06A/B,
APP-06C/D, APP-07 and release acceptance are not closed by this source packet.
