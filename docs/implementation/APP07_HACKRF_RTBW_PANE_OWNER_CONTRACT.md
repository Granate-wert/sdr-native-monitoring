# APP-07 HackRF RTBW pane owner (partial)

`HackrfRtbwPaneProfile` carries one complete, immutable
`HackrfLiveRequest`: separate LNA and VGA gains, filter, Fs, FFT/hop,
window/detector/group, native queue and persistence settings, with amplifier
and bias tee disabled by the existing request contract. A pane capture supplies
the center frequency. Compatibility ignores only center and producer
generation; it cannot merge two different DSP or RF-gain intents. The declared
usable span must fit both requested Fs and the selected baseband filter.

`HackrfRtbwPaneOwner` uses the SAME selected `LiveSessionApplicationService`
and its `stage_hackrf_configuration`/`start`/`stop` path, including the existing
catalog, preflight, native coordinator, application control claim and recording
transaction. It does not initialize a second SDK instance or bypass the common
source owner. It admits only the exact selected HackRF source and RX1 endpoint,
confirms the full acknowledged request, source, generation, session, epoch and
unit, and publishes only changed bounded latest snapshots. The same
`PaneResourceSession` validates each resulting bundle and retains owner/lease
after failed Start or Stop until explicit cleanup. Requested Fs and successful
setters are **not** hardware ADC-rate readback or lossless-transport proof.

Fake-native tests cover two time-sliced ranges with Stop→Start/new epoch,
separate LNA/VGA and disabled amplifier/bias, source-specific delivery,
ordinary control exclusion, duplicate-latest suppression, refusal of a generic
single-gain profile before SDK, and failed-stage retained cleanup. These tests
prove one concrete family adapter, not simultaneous HackRF+AD936x+tinySA in
the product or visible UI. The current product composition still has one
global Analyzer selection/exclusion graph. APP-07 needs resource-scoped product
graphs under one shared lease authority before independent sources may run in
parallel; it also needs a concrete tinySA trace owner and per-pane UI wiring.
