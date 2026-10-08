#pragma once

#include "sdr_core/types.hpp"
#include "sdr_hackrf/hackrf_runtime_dsp_session.hpp"

#include <cstdint>
#include <memory>
#include <string>
#include <array>
#include <optional>

namespace sdr_hackrf {

// Complete route-free input for the R11-N native composition boundary.  The
// public factory accepts this only after the Python control plane has admitted
// its matching R11-M plan.  It deliberately carries neither SDK types nor a
// serial, URI, transport handle, recording, Sweep or compute-backend option.
struct HackrfLiveFactoryConfig {
    double center_frequency_hz{};
    double sample_rate_hz{};
    std::uint32_t baseband_filter_hz{};
    std::uint32_t lna_gain_db{};
    std::uint32_t vga_gain_db{};
    bool rf_amplifier_enabled{};
    bool bias_tee_enabled{};
    std::uint32_t fft_size{};
    std::uint32_t hop_size{};
    sdr_core::WindowType window{sdr_core::WindowType::Hann};
    sdr_core::DetectorType detector{sdr_core::DetectorType::Sample};
    std::uint32_t slot_count{};
    std::uint32_t ready_capacity{};
    std::uint32_t dsp_output_capacity{};
    std::uint32_t presentation_capacity{};
    std::uint64_t configuration_generation{};
    std::string source_id;
    std::uint32_t averaging_frames{1U};
    sdr_core::PersistenceConfig persistence{};
    std::uint32_t analytical_event_capacity{};
    std::uint32_t layer_event_capacity{};
    // Additive CPU stage option. RF hardware DC tracking is not controlled here.
    bool dc_removal_block_mean{};
};

// Pure configuration translation and validation. It does not create a port,
// load an SDK, enumerate a device or invoke any RX operation.
[[nodiscard]] HackrfRuntimeDspSessionConfig make_hackrf_runtime_dsp_config(
    const HackrfLiveFactoryConfig& config
);

// Defined only by the optional target compiled and linked against the
// official libhackrf runtime. The single explicit call may initialize/open
// exactly one HackRF One and start the already validated R11-G/I/J/K owner.
// It is intentionally unavailable in normal CPU/CUDA builds.
[[nodiscard]] std::unique_ptr<HackrfRuntimeDspSession>
make_official_hackrf_runtime_dsp_session(const HackrfLiveFactoryConfig& config,
    std::optional<std::array<std::uint32_t, 4>> expected_serial_words = std::nullopt);

}  // namespace sdr_hackrf
