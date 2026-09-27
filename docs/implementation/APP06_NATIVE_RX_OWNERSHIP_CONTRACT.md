# APP-06A: native Live RX release barrier

The shared native backend remains the producer for UI V2. This change does
not add a second DSP path, another device catalog, a hidden restart, or a
claim that HackRF/tinySA are already routed into the common Analyzer.

## Stop is a terminal barrier, not pointer clearing

The low-rate lifecycle transaction lock serializes Start, Stop, shutdown,
and known/manual route selection. Stop first closes the publication lane
through the existing stop event. It retains the engine and poller references
until the poller has terminated, native join returned successfully, recording
completion has been captured, and disconnect returned successfully.

The poller join uses the caller's timeout and checks is_alive afterwards.
Calling cleanup from the poller itself cannot be mistaken for joining it.
An exception, still-live poller, failed native join, or failed disconnect
raises a generic release error, preserves both owner references and latches
the failure. No CONNECTED/successful-Stop snapshot is published in that case.
An explicit Stop/shutdown retry can release the retained owner. Raw Start
does not secretly retry that failed cleanup; admitted Start, route selection
and Sweep lease acquisition cannot bypass the retained owner or latch.

A failed request_stop is still followed by the terminal join/disconnect
sequence. If these succeed, they provide the backend contract's release
confirmation. This is not an independent observation of SDK internals.
The existing native join call has no Python timeout argument; this change
does not prove a universal deadline for a misbehaving native driver.

## Failed Start also owns its candidate

A candidate whose configure/Start fails may be discarded or followed by an
alternate route only after native join and disconnect succeed. If that cleanup
fails, the candidate is retained as the service's engine, Start fails, and no
next route is opened. The user/application must explicitly retry Stop.
Ordinary known-safe route fallback and explicit recovery from a stream error
remain available when the previous owner released successfully.

Route selection refuses a current engine, poller, failed-release latch, running
snapshot or Sweep lease before changing the selected URI/device or opening a
manual probe. Selection cannot overtake a pending lifecycle transaction.
close_live retains the same transaction lock through Stop and route clearing.

## Late data and application state

Retaining an engine reference must not reopen its publication lane. Reduced
Spectrum and persistence offers carrying expected_engine are rejected before
conversion and again before commit when the stop event is set or ownership
changed. The application controller retains ERROR and stop_required after a
failed Stop; changing RTBW/Sweep strategy remains forbidden until explicit
Stop succeeds. No RF continuity, packet-loss, frame-rate or calibration fact
is derived from these lifecycle tests.

## Evidence and remaining work

Nine fake-boundary tests cover native join/disconnect failures, a poller that
outlives its timeout, failed-candidate cleanup, late publications, direct/manual
selection, application mode admission and a concurrent Stop/selection barrier.
The initial six-test reproduction on the previous product failed six assertions
plus one early-conversion error (including two cleanup subtests). The later
expanded focused gate passes 24 tests with existing discovery/admission/
shutdown coverage. Hardware and full UI V2 evidence are recorded separately;
fakes do not prove physical SDK failure recovery.

Discovery's temporary-probe close errors, stable identity/alias admission,
same-opened-handle identity revalidation at Start, the common family catalog,
official HackRF frozen closure and final device matrix remain separate APP-06
work. This barrier alone closes neither APP-06 nor APP-05/APP-07/release.
