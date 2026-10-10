# APP-07 UI V2: reuse the completed waterfall axis synchronization

2026-10-10. OP03 software change; not hardware, visible-Windows, sustained
responsiveness or release qualification. This applies only to UI V2.

## Behavior

WaterfallPane already synchronizes its time model before uploading an admitted
RTBW or progressive Sweep row. The same existing `axis_already_updated=True`
hint is now used for three immediate control call chains:

| Control | Required first operation | Following upload |
| --- | --- | --- |
| Reactivate presentation | Synchronize the retained current history | Reuse that synchronization |
| Change direction | Commit direction, synchronize row positions and time | Reuse that synchronization |
| Change history/rate | Admit renderer resize, commit configuration, synchronize | Reuse that synchronization |

This removes the second time-model synchronization in each successful active,
visible, nonempty chain. It adds no generalized cache, revision key or retained
data graph. Normal incoming-row deduplication is pre-existing, not a new gain.

`set_render_visible(True)` and direct/default uploads still synchronize the
axis themselves: they have no preceding explicit synchronization. Empty,
hidden and inactive upload guards remain unchanged. A refused history resize
does not commit a configuration or start a synchronization/upload. Reactivation
after inactive row admission still synchronizes the latest retained model.

## Preserved semantics

No changes to row admission, renderer allocation limits, palette, image geometry,
custody/paint receipts, timestamps, gaps, Sweep sequence/revision/state/epoch,
Stop/Start, recording, resource ownership, acquisition, DSP, Fs or FFT settings.
Unknown simultaneous Sweep time remains unknown: zero timestamp sentinels in
ring storage are not promoted to acquisition time. Independent panels remain
independent. The change does not alter actual font/range/style invalidation.

## Measurement interpretation

A finite offscreen comparison used three actual WaterfallPane components,
two typed synthetic RTBW histories and one typed synthetic Sweep history,
2048 presentation columns, 300 retained rows and eight serial 10-second runs
in fixed O,C,C,O,O,C,C,O order. Nominal frequency grids were not SDR sampling
or native-DSP throughput measurements. All eight collection windows were valid.

Per panel, original/candidate axis-sync counts were activation 2/1, direction
toggle/restore 4/2, rate change/restore 4/2, and ordinary show 1/1. Steady row
delivery remained one synchronization per admitted row. Exact retained source
timestamps, gaps and unknown-time Sweep stamps were checked; no recorded
callback or cleanup failure occurred.

The heartbeat-lateness p95 ranges overlapped: original 26.94–30.55 ms and
candidate 29.24–35.05 ms. There is **no demonstrated steady-stream speedup**.
The demonstrated result is elimination of duplicate control-path work only;
it must not be described as a physical multi-SDR Qt-stall fix or a 50 ms SLA.
Qt update/draw-spec counts are not actual-paint/DWM FPS or latency.

Focused regression covers these control paths plus default/show behavior,
history refusal, inactive admission, RTBW time/gaps, progressive Sweep,
visibility, linking, projection bounds and delivery lifecycle. Root focused
regression passed 91 cases. The separate source/fake/offscreen V2 gate completed
1565 selected cases in 844.394 seconds: 1499 passed, 66 skipped, no failures or
errors, and no product modules imported outside the checkout. Two compiled
composition cases were explicitly deferred by that gate. Existing four NaN
comparison warnings were retained rather than suppressed.

Frozen/native packaging, changed physical tests, visible DPI checks, soak and
independent release qualification remain separate evidence gates. These source
tests do not qualify the existing executable or resolve the historical stall.
