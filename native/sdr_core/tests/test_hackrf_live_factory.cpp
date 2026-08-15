#include "sdr_hackrf/hackrf_live_factory.hpp"
#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <array>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

sdr_hackrf::HackrfLiveFactoryConfig valid_config() {
    return {
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = 10'000'000.0,
        .baseband_filter_hz = 8'000'000U,
        .lna_gain_db = 16U,
        .vga_gain_db = 20U,
        .rf_amplifier_enabled = false,
        .bias_tee_enabled = false,
        .fft_size = 4096U,
        .hop_size = 2048U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .slot_count = 32U,
        .ready_capacity = 24U,
        .dsp_output_capacity = 8U,
        .presentation_capacity = 4U,
        .configuration_generation = 7U,
        .source_id = "native.hackrf.live",
    };
}

void test_pure_translation_retains_every_bounded_field() {
    const auto config = valid_config();
    const auto translated = sdr_hackrf::make_hackrf_runtime_dsp_config(config);

    expect(translated.rx.center_frequency_hz == config.center_frequency_hz,
           "factory lost center frequency");
    expect(translated.rx.sample_rate_hz == config.sample_rate_hz,
           "factory lost sample rate");
    expect(translated.rx.baseband_filter_hz == config.baseband_filter_hz,
           "factory lost filter bandwidth");
    expect(translated.rx.lna_gain_db == config.lna_gain_db,
           "factory lost LNA gain");
    expect(translated.rx.vga_gain_db == config.vga_gain_db,
           "factory lost VGA gain");
    expect(!translated.rx.rf_amplifier_enabled && !translated.rx.bias_tee_enabled,
           "factory enabled RF auxiliary controls");
    expect(translated.rx.slot_count == config.slot_count &&
               translated.rx.ready_capacity == config.ready_capacity,
           "factory lost ingress bounds");
    expect(translated.rx.config_generation == config.configuration_generation,
           "factory lost configuration generation");
    expect(translated.processing.dsp.dsp.fft_size == config.fft_size &&
               translated.processing.dsp.dsp.hop_size == config.hop_size,
           "factory lost FFT geometry");
    expect(translated.processing.dsp.dsp.window == config.window &&
               translated.processing.dsp.dsp.detector == config.detector,
           "factory lost canonical DSP choice");
    expect(translated.processing.dsp.dsp.unit == sdr_core::SpectrumUnit::DbfsBin,
           "factory admitted calibrated/dBm output");
    expect(translated.processing.dsp.dsp.precision_mode ==
               sdr_core::PrecisionMode::ReferenceF64,
           "factory did not select the CPU reference precision");
    expect(translated.processing.dsp.source.source_id == config.source_id &&
               translated.processing.dsp.source.uri.empty() &&
               translated.processing.dsp.source.device_serial.empty() &&
               translated.processing.dsp.source.metadata_json.empty(),
           "factory leaked a device route or identity");
    expect(translated.processing.dsp.dsp_output_capacity == config.dsp_output_capacity &&
               translated.processing.dsp.presentation_capacity == config.presentation_capacity,
           "factory lost DSP/publication bounds");
}

void test_invalid_values_fail_before_any_official_owner_exists() {
    using Mutator = void (*)(sdr_hackrf::HackrfLiveFactoryConfig&);
    const std::array<std::pair<const char*, Mutator>, 10U> cases{{
        {"route", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.source_id = "usb:route"; }},
        {"upper-route", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.source_id = "USB:route"; }},
        {"generation", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.configuration_generation = 0U; }},
        {"amplifier", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.rf_amplifier_enabled = true; }},
        {"frequency", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.center_frequency_hz = 6.1e9; }},
        {"sample-rate", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.sample_rate_hz = 21e6; }},
        {"filter", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.baseband_filter_hz = 1'000'000U; }},
        {"gain", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.lna_gain_db = 13U; }},
        {"fft", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.fft_size = 3000U; }},
        {"capacity", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) {
             value.presentation_capacity =
                 sdr_hackrf::hackrf_fixed_band_max_presentation_capacity + 1U;
         }},
    }};
    for (const auto& [label, mutate] : cases) {
        auto invalid = valid_config();
        mutate(invalid);
        bool rejected = false;
        try {
            static_cast<void>(sdr_hackrf::make_hackrf_runtime_dsp_config(invalid));
        } catch (const sdr_core::ConfigurationError&) {
            rejected = true;
        }
        expect(rejected, std::string("factory accepted invalid ") + label);
    }
}

}  // namespace

int main() {
    try {
        test_pure_translation_retains_every_bounded_field();
        test_invalid_values_fail_before_any_official_owner_exists();
        std::cout << "HackRF Live factory tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "HackRF Live factory test failure: " << error.what() << '\n';
        return 1;
    }
}
