# APP-06A — Pluto route aliases, 2026-09-27

Scope: shared backend consumed by UI V2; no Legacy widget edits. This is an
identity prerequisite, not completion of the common multi-family Analyzer.

Automatic USB/IP alias merging now requires the same observed, normalized
ASCII serial. Empty/placeholder/non-ASCII/control-containing serials remain
unknown. Discovery counts, similar model names, default IPs and stale opaque
keys cannot establish that two routes are the same receiver. Serial hashes
use the same normalization as grouping; raw serials are not used as UI ids.

Unknown-serial routes remain separately selectable candidates. A route hash
is not stable physical identity, calibration binding, or proof of two radios.
The presently connected Pluto reports empty `hw_serial` and `usb,serial` in
independent read-only IIO inspection. Do not change its firmware or invent a
serial to make the capability matrix appear complete.

Five test methods first reproduced twelve failing assertions/subtests against
the preceding implementation (after correcting a test fixture). After the
fix, 29 focused tests covering aliases, owner release, discovery, admission
and shutdown pass. New files Ruff and diff-check pass. No native activation,
RX/configuration, EXE rebuild or firewall change in this alias-only checkpoint.

Remaining: recheck a known expected serial on the actual opened native owner
before Configure/Start, including RTBW, sequential Sweep and continuous Sweep;
keep temporary probe cleanup failures owned; map all families into the existing
capability inventory and common Analyzer routing; complete physical matrix and
package acceptance. A successful discovery alone does not prove later identity.
