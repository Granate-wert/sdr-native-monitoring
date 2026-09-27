# APP-06A: AD936x read-only capability owner

This is a bounded correction to the existing `Ad936xLibiioCapabilityAdapter`,
not a second capability model, common Analyzer router or APP-06 acceptance.
The application still targets UI V2. No streaming, RF configuration, firmware,
firewall or Legacy UI change is part of this contract.

## One adapter instance, one low-rate owner

The caller exclusively owns this adapter instance and serializes its calls.
`observe(route)` delegates to the shared PlutoReadOnlyObserver. It creates one
temporary native PlutoDevice, obtains probe, capabilities and receiver topology
from that owned context, and disconnects before returning any observation.
Facts map into the existing immutable DeviceCapabilitySnapshot and calibration
identity. Serial-based aliases keep one opaque identity; a route alone is not
a stable physical identity. Missing serial/firmware or malformed facts fail
closed. Unknown RX topology or shared-LO facts remain unknown.

The shared observer serializes its transaction and explicit close with an RLock.
Neither an adapter nor an observer is a global radio ownership arbiter.
The common catalog/router must retain its own one-owner lifecycle gate;
constructing another adapter must not be used to bypass a failed cleanup.

## Failed release is not successful discovery

Previously, `disconnect()` failure was swallowed and a valid-looking
observation could be returned. Now:

1. The temporary owner remains reachable on the adapter if disconnect fails.
2. No capability observation is returned; errors are generic and route-redacted.
3. Further `observe()` calls on that instance fail without a new native open
   or a hidden cleanup retry, even if the requested route differs.
4. The caller explicitly calls `close()` to retry release. Failure keeps the
   owner and rejection; success clears it and allows another observation.
5. `close()` after successful release, or before the first observation, is
   idempotent and makes no native call.

Probe/capability exceptions still attempt cleanup. If cleanup succeeds, a
later explicit observation is allowed. Constructor failure has no returned
owner to close. No successful release is claimed for an exception or timeout.

## Scope of proof

Fake boundary tests reproduce the former successful-publication/failed-close
bug and a second-open-after-failed-close bug, and cover release retry, alias
identity, invalid facts/routes, probe/capability/constructor failures and idle
close. They prove this adapter's control flow, not native hardware cleanup,
RF continuity or device-swap behavior.

The APP-06A native constructor now opens exactly one context. Its model,
serial, firmware, backend and device identifiers are inspected through that
owner's libiio function table, before control-channel discovery and capability
readback. The probe and capability identity therefore come from the context
that this PlutoDevice subsequently configures for RX, not a separate preflight
open. The shared private implementation is used by Windows and Linux; Windows
mock-driver tests cover distinct serials on every open, one live owner,
idempotent disconnect/destructor and cleanup after version/timeout errors.
Linux source parity is not a substitute for a Linux build or hardware test.

This is not discovery-to-Start identity revalidation or a verified unique
serial on every AD936x firmware. Empty native serial stays empty. A read-only
2026-09-27 check through the installed iio_info 0.26 independently found both
hw_serial and usb,serial empty on the connected AD9364. Model, firmware and a
USB URI cannot manufacture unique physical identity. The strict capability
mapper must still reject that observation; no firmware change is implied.
Older active/frozen binaries retain their earlier constructor until explicitly
rebuilt and qualified; source changes alone do not prove their behavior.

## Coherent observation and existing catalog

The owned `PlutoDevice.receiver_topology()` method copies PHY input channels,
stream scan elements and the owner's context probe under the device mutex.
It does not open another context, enable channels or create a buffer. The
new exact-integer PLUTO_OBSERVATION_PROTOCOL_VERSION=1 distinguishes this
contract from identity admission protocol1 and Spectrum wire schema5.
Windows and Linux sources implement it; only Windows was built here.

The shared observer rejects contradictory URI, normalized serial or firmware
facts under that protocol. Unknown serial stays unknown. An old or malformed
protocol cannot produce an admitted stable capability snapshot. Facts are
copied native transfer objects, not an independent capability truth model.
The legacy standalone topology diagnostic function is not used by this path;
its independent-context behavior is not promoted as coherent evidence.

Ad936xLibiioCapabilityAdapter.map_observation is a pure mapper into the
existing DeviceCapabilitySnapshot/CalibrationIdentity models. It requires
coherent protocol, known serial and firmware. Digital scan pairs are structural
RX-count evidence, not physical RF-path proof; shared LO remains unknown.

NativeLiveSessionService discovery and selection use this same transaction.
Discovery deduplicates exact URIs, checks at most64 returned scan routes and
at most32 logical devices, and publishes only after successful observation
cleanup. Its existing transaction/owner gates prohibit Discover or route
selection during RX, Sweep lease or retained release obligations. Failed
temporary close also blocks new Start and catalog access; explicit Stop/close
is the recovery action, with no hidden restart or fallback to another alias.

DeviceDescriptor optionally references that existing capability snapshot.
capability_inventory() freezes those snapshots without any SDK/discovery call.
Unknown-serial candidates remain usable explicit routes with observed scalar
settings, but do not become stable physical/calibration catalog entries.
Known serial groups USB/IP aliases; conflicting or unverified capability
snapshots cannot manufacture one admitted aggregate. Selection rereads the
actual chosen route and refreshes its profile after successful cleanup.

This catalog currently covers only the AD936x source service. It is not a
completed multi-family runtime registry, common Analyzer selector, per-family
request admission matrix or HackRF/tinySA UI integration. Their remaining
work and unsupported/unknown distinctions must not be inferred from this API.

Analyzer UI V2 still requires APP-06A common catalog and request admission,
APP-06B official HackRF frozen runtime closure, APP-06C source routing and
APP-06D physical matrix. This correction alone closes none of those packages.
