#include "sdr_hackrf/hackrf_live_factory.hpp"

#include "sdr_core/configuration.hpp"
#include "sdr_core/errors.hpp"
#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <string>
#include <string_view>

namespace sdr_hackrf {
namespace {

[[nodiscard]] bool has_route_or_path(const std::string_view value) {
    std::string normalized(value);
    std::transform(
        normalized.begin(),
        normalized.end(),
        normalized.begin(),
        [](const unsigned char character) { return static_cast<char>(std::tolower(character)); }
    );
    return normalized.find("usb:") != std::string::npos ||
           normalized.find("ip:") != std::string::npos ||
           normalized.find('\\') != std::string::npos ||
           normalized.find('/') != std::string::npos;
}

void validate_factory_config(const HackrfLiveFactoryConfig& config) {
    if (config.configuration_generation == 0U) {
        throw sdr_core::ConfigurationError(
            "HackRF Live factory configuration generation must be nonzero"
        );
    }
    if (config.source_id.empty() || config.source_id.size() > 128U ||
        has_route_or_path(config.source_id)) {
        throw sdr_core::ConfigurationError(
            "HackRF Live factory source id must be route-free and bounded"
        );
    }
    if (config.rf_amplifier_enabled || config.bias_tee_enabled) {
        throw sdr_core::ConfigurationError(
            "HackRF Live factory keeps RF amplifier and bias tee disabled"
        );
    }
    if (!std::isfinite(config.center_frequency_hz) ||
        !std::isfinite(config.sample_rate_hz)) {
        throw sdr_core::ConfigurationError(
            "HackRF Live factory frequencies must be finite"
        );
    }
}

}  // namespace

HackrfRuntimeDspSessionConfig make_hackrf_runtime_dsp_config(
    const HackrfLiveFactoryConfig& config
) {
    validate_factory_config(config);

    HackrfRuntimeDspSessionConfig result;
    result.rx.center_frequency_hz = config.center_frequency_hz;
    result.rx.sample_rate_hz = config.sample_rate_hz;
    result.rx.baseband_filter_hz = config.baseband_filter_hz;
    result.rx.lna_gain_db = config.lna_gain_db;
    result.rx.vga_gain_db = config.vga_gain_db;
    result.rx.rf_amplifier_enabled = false;
    result.rx.bias_tee_enabled = false;
    result.rx.slot_count = config.slot_count;
    result.rx.ready_capacity = config.ready_capacity;
    result.rx.config_generation = config.configuration_generation;
    validate_hackrf_rx_profile(result.rx);

    auto& dsp = result.processing.dsp;
    dsp.dsp.fft_size = config.fft_size;
    dsp.dsp.hop_size = config.hop_size;
    dsp.dsp.window = config.window;
    dsp.dsp.detector = config.detector;
    dsp.dsp.unit = sdr_core::SpectrumUnit::DbfsBin;
    dsp.dsp.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
    dsp.dsp.batch_size = 1U;
    dsp.dsp.averaging_frames = 1U;
    dsp.dsp.calibration_status = sdr_core::CalibrationStatus::Uncalibrated;
    dsp.dsp.calibration_profile_id.clear();
    dsp.dc_removal = sdr_core::DcRemovalMode::Off;
    dsp.source.source_type = sdr_core::SourceType::LiveIq;
    dsp.source.source_id = config.source_id;
    dsp.source.display_name = "HackRF Live";
    dsp.source.uri.clear();
    dsp.source.device_serial.clear();
    dsp.source.backend_id = "native.libhackrf.rx.v1";
    dsp.source.metadata_json.clear();
    dsp.dsp_output_capacity = config.dsp_output_capacity;
    dsp.presentation_capacity = config.presentation_capacity;

    // These shared validators make all native-only call paths fail closed as
    // well; the admission service is not the sole safety barrier.
    sdr_core::validate(dsp.dsp);
    validate_hackrf_fixed_band_dsp_config(dsp);
    return result;
}

}  // namespace sdr_hackrf
