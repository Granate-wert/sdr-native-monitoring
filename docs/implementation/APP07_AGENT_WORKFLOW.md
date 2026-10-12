# APP-07 bounded agent workflow

Version152, accepted2026-10-12. The team uses finite task packets and separate
M7 fault/isolation and M8 performance completion gates.

## Roles

The root orchestrator owns backend/native, physical SDR access, build/source
provenance, integration, domain contracts and final acceptance. One assigned
UI/measurement author changes an exact file allowlist. A distinct read-only
reviewer checks frozen subjects and scoped evidence. The same Luna maintainer
updates the HTML roadmap after the root accepts a result or records a blocker.
QA, performance and release are sequential functions, not permanent agents.
No recursive delegation or child integration/push is permitted.

## Task contract

Every packet declares one mode: ORGANIZATION_ONLY, REVIEW_ONLY, IMPLEMENTATION
or VALIDATION. Organization covers documents/state/HTML; review covers reading
and a report; implementation covers an explicit change; validation covers
specified checks. Each packet has one finite result, exact file ownership,
base revision, invariant/contract references, a verification method, stop
conditions, owner/model and a30–60minute checkpoint. A checkpoint reports a
result or blocker; elapsed time is not acceptance.

New defects outside the packet enter a separate queue. New user directions
are propagated to every affected agent before incompatible actions continue.
Major implementations use separate worktrees; small private diagnostic jobs
may use disjoint file allowlists. Worktrees do not isolate SDR hardware.
Physical testing has one owner. Source files are frozen for matched tests,
measurements and builds; evidence records before/after revision and hashes.

## Review and repeat rules

Review verdicts are ACCEPT, FIX_CONCRETE_FINDINGS or INSUFFICIENT_EVIDENCE.
Required current defects are separate from future improvement suggestions.
Recheck affected findings after repairs; broader review requires changed risk.
Only the root verifies evidence, changes acceptance and integrates a whitelist.

Before repeating a check, record the changed input/hypothesis, uncertainty to
resolve and decision for either outcome. Two failures of a measurement method
stop qualification and lead to one minimal reproducer and method review.
Two identical product failures without progress require a changed hypothesis.
Do not enlarge timeouts/queues or lower Fs, FFT, bandwidth or visible workload
to pass. Use one maintained harness, bounded counters and sampled detail.
Use targeted checks per patch and full qualification on an integration candidate.

## State and completion

The local repository memory has one current assignment, canonical acceptance,
roadmap and evidence index; historical details are read on demand. Operational
state stays within80KiB. HTML is a projection, not an independent source of
status or a continuously running agent. Reports contain changed files/version,
verified behavior, unknowns, evidence and any required contract decision.

M7 closes only when G01–G04 prove connection-loss/worker isolation, bounded
backlog and deterministic Stop/rearm, including current physical evidence and
independent review. FPS and complete optional tracing do not block M7.
M8 closes only when H01–H03 prove a reproducible baseline, actual per-view Qt
paint measurements and acquisition/analytics versus presentation separation,
with matched comparisons and physical corroboration. Long-run stability,
Windows/DPI and release qualification remain M9–M11. Unit/mock/offscreen tests
do not satisfy those physical or visible acceptance gates.
