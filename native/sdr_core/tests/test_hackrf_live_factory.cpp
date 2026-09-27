#include "sdr_hackrf/hackrf_live_factory.hpp"
#include "sdr_hackrf/hackrf_fixed_band_dsp.hpp"

#include "sdr_core/errors.hpp"

#include <array>
#include <cmath>
#include <cstring>
#include <iostream>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

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
    auto config = valid_config();
    config.persistence.enabled = true;
    config.persistence.mode = sdr_core::PersistenceMode::RollingExact;
    config.persistence.power_bins = 32U;
    config.persistence.window_frames = 123U;
    const auto translated = sdr_hackrf::make_hackrf_runtime_dsp_config(config);
    expect(translated.processing.dsp.persistence.enabled &&
           translated.processing.dsp.persistence.mode == sdr_core::PersistenceMode::RollingExact &&
           translated.processing.dsp.persistence.power_bins == 32U &&
           translated.processing.dsp.persistence.window_frames == 123U,
           "factory silently replaced the native persistence profile");

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
    expect(translated.processing.dsp.dsp.averaging_frames == config.averaging_frames,
           "factory lost detector group size");
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
    const std::array<std::pair<const char*, Mutator>, 12U> cases{{
        {"group-zero", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.averaging_frames = 0U; }},
        {"group-large", +[](sdr_hackrf::HackrfLiveFactoryConfig& value) { value.averaging_frames = 257U; }},
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

void test_factory_profile_reaches_canonical_ci8_detector_not_display_average() {
    using sdr_core::DetectorType;
    using sdr_core::WindowType;
    const std::array windows{WindowType::Rectangular, WindowType::Hann,
        WindowType::BlackmanHarris4Term, WindowType::FlatTop, WindowType::Nuttall, WindowType::Kaiser};
    const std::array detectors{DetectorType::Sample, DetectorType::Peak,
        DetectorType::NegativePeak, DetectorType::Rms, DetectorType::AveragePower};
    for (const auto window : windows) {
        for (const auto detector : detectors) {
            auto config = valid_config();
            config.fft_size = config.hop_size = 256U;
            config.window = window;
            config.detector = detector;
            config.averaging_frames = 4U;
            const auto translated = sdr_hackrf::make_hackrf_runtime_dsp_config(config);
            sdr_hackrf::HackrfFixedBandDsp dsp(translated.processing.dsp);
            sdr_hackrf::HackrfRxIngress ingress({
                .slot_count = 4U, .slot_bytes = 512U, .ready_capacity = 3U,
                .center_frequency_hz = config.center_frequency_hz,
                .sample_rate_hz = config.sample_rate_hz,
                .config_generation = config.configuration_generation,
            });
            const std::array<std::int8_t, 4> amplitudes{16, 32, 64, 48};
            for (std::size_t index = 0U; index < amplitudes.size(); ++index) {
                std::vector<std::uint8_t> bytes(512U, 0U);
                for (std::size_t sample = 0U; sample < 256U; ++sample) {
                    std::memcpy(bytes.data() + sample * 2U, &amplitudes[index], 1U);
                }
                expect(ingress.admit_callback(bytes, static_cast<std::int64_t>(1000U + index * 25600U)) ==
                    sdr_hackrf::HackrfRxAdmissionResult::Admitted, "synthetic CI8 input refused");
                sdr_hackrf::HackrfRxLease lease;
                expect(ingress.try_pop(lease), "synthetic CI8 lease unavailable");
                dsp.push(std::move(lease));
                if (index < 3U) {
                    expect(dsp.poll_spectrum_frames().empty(), "detector group emitted prematurely");
                }
            }
            const auto frames = dsp.poll_spectrum_frames();
            expect(frames.size() == 1U, "four FFT group must emit exactly one reduced spectrum");
            const auto& frame = frames.front();
            sdr_core::validate(frame);
            expect(frame.window == window && frame.detector == detector && frame.averaging_frames == 4U,
                "producer metadata does not describe selected detector group");
            double expected_power = 7680.0 / 65536.0;
            if (detector == DetectorType::Sample) { expected_power = 2304.0 / 16384.0; }
            if (detector == DetectorType::Peak) { expected_power = 4096.0 / 16384.0; }
            if (detector == DetectorType::NegativePeak) { expected_power = 256.0 / 16384.0; }
            expect(std::abs((*frame.values)[128U] - 10.0 * std::log10(expected_power)) < 0.0001,
                "factory profile detector did not operate on canonical linear CI8 power");
            expect(dsp.metrics().dsp.fft_frames_computed == 4U && dsp.metrics().dsp.fft_frames_dropped == 0U,
                "detector groups changed analytical FFT accounting");
        }
    }
}

}  // namespace

int main() {
    try {
        test_pure_translation_retains_every_bounded_field();
        test_invalid_values_fail_before_any_official_owner_exists();
        test_factory_profile_reaches_canonical_ci8_detector_not_display_average();
        std::cout << "HackRF Live factory tests passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "HackRF Live factory test failure: " << error.what() << '\n';
        return 1;
    }
}
