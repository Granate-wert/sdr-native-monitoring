# APP-06: retained read-only family ownership

This is a control-plane prerequisite for the common UI V2 catalog/router,
not completed multi-family Analyzer support or a global radio ownership arbiter.
Existing DeviceCapabilitySnapshot/calibration models and family unit semantics
are unchanged. No streaming, RF setting, firmware, driver or firewall action
is added by this contract.

## Retained provider transaction

HackrfCapabilityAdapter, TinySaCapabilityAdapter and HackrfActivationPreflightService
share a low-rate RetainedReadOnlyObserver. One retained provider instance
serializes factory/probe/close with an RLock. Returned facts or activation
permits are admitted only after successful close. Failed reads still attempt
one close; failed close keeps the port reachable and cleanup_pending=true.
Further observe/verify calls reject without a new factory, native read or
implicit cleanup retry. Explicit close retries release; successful close
clears the pending owner, and an idle close is a no-op.

Identity preflight preserves its finite refusal categories. A failed or
pending observation cannot issue a native activation permit. Invalid plans
are refused before factory access without clearing outstanding obligations.

The common router must retain these provider instances and check their release
obligations before switching families. Constructing another provider, dropping
a failed provider, or using a one-shot temporary adapter is not a release proof.
This helper is not a second capability model or a process-global SDK registry.

## Factory and concrete-port obligations

Factories must return the owner before SDK acquisition in probe. In particular,
LibhackrfReadOnlyPort construction now only stores paths and state; library,
enumeration and device opening are lazy. Thus a failed partial open remains
reachable in the provider instead of being lost in a throwing constructor.
LibhackrfRuntimeIdentityPort remains enumeration-only and likewise leaves
partial resources with its caller. Neither port retries an unsuccessful probe.

Concrete ports serialize probe/close, latch failed reads or begun release,
and reject another probe until that owner is closed. Close stops immediately
at the failed phase. It retains the failed resource and all dependencies,
and clears each successfully released resource only after the SDK call returns
successfully. The closed flag is set only after all phases finish:

1. Read-only device close, where applicable.
2. Enumeration-list free.
3. Library initialization exit.
4. DLL-directory handle close, then Python library reference release.

An explicit retry resumes the remaining phases; completed phases are not
repeated. Enumeration-only ports have no device-open or RF-control symbols.
These are reference/lifecycle obligations, not in-memory DLL-unload attestation.
SDK calls with no return status are considered completed only on normal return;
this does not establish recoverability of arbitrary native crashes or partially
executed foreign code. SDK-global concurrency is still the common owner's job.

## Evidence boundaries

Fault-injected tests cover negative return/exception during device close,
list/exit/dependency phase failures, failed partial opens, no-SDK construction,
blocked competing observations, explicit retry and idempotence. Provider tests
also retain probe+close failures, redact errors, and prevent permit publication.
They do not simulate physical USB unplugging or prove actual SDK close-failure
recovery on hardware. Positive real probes are a separate gate.

tinySA retains device-reported dBm, separate optional external correction and
no raw I/Q/host FFT claims. This change applies to its injected capability port,
not acceptance of every serial sweep/settings collector or Standard/Ultra cell.
Official HackRF frozen runtime closure, canonical identity join, shared UI V2
selection/router/controls and the high-Fs hardware matrix remain APP-06 work.
