# APP-06A: AD936x read-only capability owner

This is a bounded correction to the existing `Ad936xLibiioCapabilityAdapter`,
not a second capability model, common Analyzer router or APP-06 acceptance.
The application still targets UI V2. No streaming, RF configuration, firmware,
firewall or Legacy UI change is part of this contract.

## One adapter instance, one low-rate owner

The caller exclusively owns this adapter instance and serializes its calls.
`observe(route)` creates a temporary native PlutoDevice, obtains its probe and
capabilities, and disconnects it before returning any capability observation.
Facts map into the existing immutable DeviceCapabilitySnapshot and calibration
identity. Serial-based aliases keep one opaque identity; a route alone is not
a stable physical identity. Missing serial/firmware or malformed facts fail
closed. Unknown RX topology or shared-LO facts remain unknown.

The adapter is not thread-safe and is not a global radio ownership arbiter.
The future common catalog/router must retain its own one-owner lifecycle gate;
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

Optional receiver topology is still obtained through its existing separate
native read-only probe, after this temporary device has closed. It is not a
same-handle topology attestation; shared LO remains unknown. Physical family
support and configured ADC rate must keep their existing evidence qualifiers.

Analyzer UI V2 still requires APP-06A common catalog and request admission,
APP-06B official HackRF frozen runtime closure, APP-06C source routing and
APP-06D physical matrix. This correction alone closes none of those packages.
