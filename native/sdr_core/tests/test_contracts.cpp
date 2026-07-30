#include "sdr_core/capabilities.hpp"
#include "sdr_core/configuration.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_core/metrics.hpp"
#include "sdr_core/types.hpp"

#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <string>
#include <vector>

namespace {

template <typename Callback>
bool rejects(Callback&& callback) {
    try {
        callback();
    } catch (const sdr_core::ConfigurationError&) {
        return true;
    }
    return false;
}

}  // namespace

int main() {
    using namespace sdr_core;

    if (contract_schema_version != 3U || contract_schema_name != "sdr-native-contracts") {
        std::cerr << "contract schema identity mismatch" << std::endl;
        return 1;
    }
    if (to_wire(SourceType::LiveIq) != "live_iq" ||
        to_wire(SpectrumUnit::DbfsHz) != "dBFS/Hz" ||
        static_cast<std::uint32_t>(QualityFlag::CudaFallback) != (1U << 14U)) {
        std::cerr << "enum wire values mismatch" << std::endl;
        return 2;
    }

    SourceDescriptor source{
        .source_type = SourceType::Synthetic,
        .source_id = "native-p02",
        .display_name = "Native P02",
        .uri = "synthetic:p02",
        .backend_id = "cpu",
        .metadata_json = {{"purpose", "\"contract-test\""}},
    };
    validate(source);

    DeviceConfig device{
        .source_id = source.source_id,
        .context_uri = source.uri,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 2'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0,
        .gain_mode = GainMode::Manual,
        .manual_gain_db = 10.0,
    };
    validate(device);

    DspConfig dsp{
        .fft_size = 1024U,
        .hop_size = 512U,
        .window = WindowType::Hann,
        .detector = DetectorType::AveragePower,
        .unit = SpectrumUnit::DbfsHz,
        .precision_mode = PrecisionMode::AccurateF32F64Accum,
        .batch_size = 8U,
        .averaging_frames = 4U,
    };
    validate(dsp);

    PersistenceConfig persistence{
        .enabled = true,
        .mode = PersistenceMode::RollingExact,
        .window_frames = 500U,
    };
    validate(persistence);

    SweepConfig sweep{
        .start_frequency_hz = 100'000'000.0,
        .stop_frequency_hz = 200'000'000.0,
        .sample_rate_hz = 2'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0,
        .overlap_hz = 100'000.0,
        .fft_size = 1024U,
        .hop_size = 512U,
    };
    validate(sweep);

    RecordingConfig recording;
    validate(recording);

    if (!rejects([&dsp] {
            auto invalid = dsp;
            invalid.fft_size = 0U;
            validate(invalid);
        }) ||
        !rejects([&dsp] {
            auto invalid = dsp;
            invalid.hop_size = invalid.fft_size + 1U;
            validate(invalid);
        }) ||
        !rejects([&dsp] {
            auto invalid = dsp;
            invalid.unit = SpectrumUnit::Dbm;
            validate(invalid);
        }) ||
        !rejects([&persistence] {
            auto invalid = persistence;
            invalid.power_max_db = invalid.power_min_db;
            validate(invalid);
        })) {
        std::cerr << "invalid configuration was accepted" << std::endl;
        return 3;
    }

    auto iq_bytes = std::make_shared<std::vector<std::uint8_t>>(16U);
    IqBlock iq{
        .source_sequence = 1U,
        .first_sample_index = 0U,
        .timestamp_ns = 1,
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 2'000'000.0,
        .sample_format = SampleFormat::ComplexInt16Le,
        .sample_count = 4U,
        .flags = QualityFlag::TimestampEstimated,
        .samples = iq_bytes,
        .config_generation = 1U,
    };
    validate(iq);

    auto frequencies = std::make_shared<std::vector<double>>(
        std::initializer_list<double>{99.0, 100.0, 101.0, 102.0}
    );
    auto values = std::make_shared<std::vector<float>>(
        std::initializer_list<float>{-90.0F, -80.0F, -70.0F, -60.0F}
    );
    SpectrumFrame frame{
        .source = source,
        .frame_sequence = 1U,
        .first_sample_index = 0U,
        .timestamp_ns = 1,
        .config_generation = 1U,
        .center_frequency_hz = 100.0,
        .sample_rate_hz = 4.0,
        .analog_bandwidth_hz = 4.0,
        .fft_bin_width_hz = 1.0,
        .enbw_hz = 1.5,
        .nominal_rbw_hz = 1.5,
        .fft_size = 4U,
        .hop_size = 2U,
        .window = WindowType::Hann,
        .detector = DetectorType::Sample,
        .precision_mode = PrecisionMode::AccurateF32F64Accum,
        .unit = SpectrumUnit::DbfsBin,
        .frequencies_hz = frequencies,
        .values = values,
        .calibration_status = CalibrationStatus::Uncalibrated,
        .estimated_uncertainty_db = std::numeric_limits<double>::quiet_NaN(),
        .quality_flags = QualityFlag::Uncalibrated,
    };
    validate(frame);

    DeviceCapabilities capabilities{
        .backend_id = "synthetic",
        .device_id = "p02",
        .serial = "test",
        .model = "contract",
        .firmware = "none",
        .tuning_range_hz = {1.0, 6'000'000'000.0, std::nullopt},
        .sample_rate_ranges_hz = {{1'000.0, 100'000'000.0, std::nullopt}},
        .analog_bandwidth_ranges_hz = {{1'000.0, 56'000'000.0, std::nullopt}},
        .gain_range_db = {-10.0, 73.0, 1.0},
        .gain_modes = {GainMode::Manual, GainMode::SlowAttack},
        .sample_formats = {SampleFormat::ComplexInt16Le},
    };
    validate(capabilities);

    EngineMetrics metrics;
    validate(metrics);
    metrics.end_to_end_latency_ms = -1.0;
    if (!rejects([&metrics] { validate(metrics); })) {
        std::cerr << "invalid metrics were accepted" << std::endl;
        return 4;
    }

    std::cout << "sdr_core P02 contracts passed" << std::endl;
    return 0;
}
