# SDR Native Monitoring

Windows desktop foundation for standalone software-defined-radio monitoring.

- `sdr-native-monitoring` starts the independent SDR application.
- `dfl-analyzer` remains the separately maintained legacy measurement-container tool.

The SDR launch path contains no DFL parser, spectrogram decoder, or DFL GUI dependency.

## S05 Home/Live UI

The standalone UI provides a short Home-to-Live workflow with device discovery
(USB, IP, or a manual URI), capability-aware controls, requested/applied values,
profile storage, bounded 60 Hz presentation, and explicit dBFS quality status.
Hardware adapters and numeric spectrum-frame rendering remain separate follow-up
work; the default session is an in-memory safe service for local UI validation.

## S06 Wideband Sweep UI

The standalone Sweep workspace now provides Fast/Balanced/Precise planning,
plan preview, stale-plan protection, cancellable background execution, explicit
unknown seam/calibration quality, and atomic JSON summary export. The bundled
in-memory service is safe for UI validation; hardware sweep acquisition and
numeric full-span rendering remain follow-up work.

## S07 Calibration and Measurements

The standalone Calibration workspace provides immutable profile versions, CSV
preview/finalize, applicability rows, correction/uncertainty visualization,
explicit expert override for incompatible settings, and measurement cards that
always show unit, quality, and uncertainty. Uncalibrated or incompatible data
remains in `dBFS/bin`; absolute `dBm` is blocked until a valid profile applies.
