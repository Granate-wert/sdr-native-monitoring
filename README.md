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
