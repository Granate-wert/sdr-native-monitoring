#pragma once

#include "sdr_core/types.hpp"
#include "sdr_hackrf/hackrf_runtime_dsp_session.hpp"

#include <cstdint>
#include <memory>
#include <string>

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
make_official_hackrf_runtime_dsp_session(const HackrfLiveFactoryConfig& config);

}  // namespace sdr_hackrf
