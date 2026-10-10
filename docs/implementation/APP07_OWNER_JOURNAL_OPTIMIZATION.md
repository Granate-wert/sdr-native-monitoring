# APP-07 owner journal: bounded incremental accounting

2026-10-10. UI V2 performance work, OP01/OP02. This is a software candidate,
not physical multi-SDR, visible Windows, sustained 50 ms or release qualification.

## Preserved contract

The native owner journal keeps the existing owner lock, native reader, event
conversion, identity/counter/ref checks, adapter decisions and Stop/finalization.
No changes to acquisition rate, FFT, DSP, event capacity, presentation queue,
timestamps, gaps, epochs, recording or independent/paired resource ownership.
Host scalar accounting retains the existing 1 MiB per-chain reservation and
shared layer deduction; cache metadata is an **additional** charge, not a new
pool. This is the existing conservative scalar budget, not a claim of RSS or
allocator-peak measurement.

Incomplete evidence remains incomplete. Budget refusal before snapshot commit
still fails journal evidence; downstream authenticated delivery may reject such
evidence. The optimization does not bypass those guards to preserve a frame rate.

## OP01: exact immutable component charges

`NativeOwnerJournal` retains only scalar byte charges, not additional event or
snapshot graphs. Exact immutable event tuples and terminal snapshots are sized
when they change. Current counters, scope, coverage, adapter fields and integer
widths are charged afresh. Duplicate occurrences in current/candidate/history
remain charged as before. Terminal history remains bounded to four snapshots.

Unknown types and mutable subclasses use the original recursive sizing oracle.
They are not charged as zero and are not admitted into an immutable cache.
Prepare, begin, error, adapter, archive and finish transitions update accounting
under the same lock; no successful-drain-only cache lifetime.

## OP02: bounded audit deltas

`OwnerEventAudit` retains the original maximum 256 offers and adds a fixed
enum-state count table plus scalar revision/size metadata. Accepted offers and
state transitions update those counts. Duplicates, missing/foreign references,
invalid transitions and capacity refusal retain the original failure behavior.

For exact typed events, the existing recursive payload charge is maintained
incrementally: original event fields and tuple storage at insertion, state-size
delta at transition. Tuple size is measured rather than inferred from an ABI.
No duplicate retained audit tuple or raw array is introduced. Unsupported graphs
or enum aliases fall back to the original sizing and identity/membership scan.

The revision tracks retained payload changes, **not evidence qualification**.
Every snapshot still checks new counters, native losses, host eviction,
presentation failures, terminal state and evidence failure. COMPLETE is never
cached. Unchanged empty drains still call the reader and validate current state,
but do not recursively rewalk retained immutable events.

## Verification and measured scope

New tests cover both sizing and audit equivalence to the previous oracle:
valid/non-FIFO transitions, duplicate/foreign/missing refs, bounded overflow,
terminal/pending/loss flags, reset/history, mutable fallback, enum identity,
integer-width changes and budget refusal. Broader targeted regression:
172 tests, 163 passed, 9 skipped, no failures/errors. Full source/offscreen
UI V2 regression: 1,559 tests, 1,493 passed, 66 skipped, no failures/errors
in 951.647 s; no product modules imported outside the isolated checkout.
Two compiled native-composition tests were explicitly deferred by the source
gate. Ruff, scoped mypy and compilation checks passed. These are not a new
frozen EXE/native-build, physical RX or visible Windows acceptance test.

Finite local ABBA runs compare OP02 to the frozen OP01 journal plus the original
audit. Empty-drain means: 2.168 to 0.166 ms with no terminal history; 1.714 to
0.190 ms with four histories. Changed 32-offer batches: 2.413 to 1.887 ms and
2.531 to 1.934 ms. Recursive audit event visits per eight unchanged polls fall
from 2048 to zero. These are synthetic local wall times, not RF throughput or
whole-application speedup percentages. Earlier slower changed-batch candidates
and test-helper failures were retained, not removed from the development record.

A separate three-source offscreen Qt fixture uses nominal HackRF 20, AD9363
30.72 and RTL 2.4 MS/s profiles, FFT 4096, 300 waterfall rows and a 20 ms
heartbeat. Both modes use candidate product owners; only the selected synthetic
journal side-load differs. It is not old versus new full-product hardware RX.
All four candidate windows were valid, with heartbeat lateness p95
110.5 / 39.7 / 46.3 / 50.6 ms. Three of four OP01 windows exceeded the unchanged
window limit; the single valid baseline had p95 507.3 ms. There is **no complete
valid matched ABBA block**, no pooled percentile or statistically qualified
speedup. Independent UI review validated all eight raw calculations, source,
runtime, font and terminal-cleanup receipts; it did not approve backend
accounting, hardware correctness or release qualification.

## Remaining work

OP03/OP04: measurement-backed time-axis invalidation, ticks/text/geometry reuse.
OP05: residual preparation/copy costs and selective native migration decision.
OP06: fair GUI scheduling and stable chrome. OP07/OP08: changed physical tests,
visible FHD/QHD/DPI, disk-log profiling, stability/soak, independent broader
review and exact build/package qualification. APP-07 remains PARTIAL.
