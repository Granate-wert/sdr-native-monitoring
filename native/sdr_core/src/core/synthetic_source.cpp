#include "sdr_core/synthetic_source.hpp"

#include "sdr_core/errors.hpp"

#include <cmath>
#include <iomanip>
#include <sstream>
#include <string>
#include <utility>

namespace sdr_core {
namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw ConfigurationError(message);
}

[[nodiscard]] std::string seed_hex(const std::uint64_t seed) {
    std::ostringstream stream;
    stream << std::hex << std::setfill('0') << std::setw(16) << seed;
    return stream.str();
}

[[nodiscard]] std::uint64_t splitmix64(std::uint64_t state) noexcept {
    state += 0x9E3779B97F4A7C15ULL;
    state = (state ^ (state >> 30U)) * 0xBF58476D1CE4E5B9ULL;
    state = (state ^ (state >> 27U)) * 0x94D049BB133111EBULL;
    return state ^ (state >> 31U);
}

}  // namespace

std::string_view to_wire(const SyntheticScenario value) {
    switch (value) {
    case SyntheticScenario::ExactBinTone:
        return "exact_bin_tone";
    case SyntheticScenario::HalfBinTone:
        return "half_bin_tone";
    case SyntheticScenario::TwoTones:
        return "two_tones";
    case SyntheticScenario::CloseTones:
        return "close_tones";
    case SyntheticScenario::BroadbandNoise:
        return "broadband_noise";
    case SyntheticScenario::DcOffset:
        return "dc_offset";
    case SyntheticScenario::Impulse:
        return "impulse";
    case SyntheticScenario::Clipping:
        return "clipping";
    case SyntheticScenario::IqImbalance:
        return "iq_imbalance";
    case SyntheticScenario::Chirp:
        return "chirp";
    case SyntheticScenario::Hopping:
        return "hopping";
    case SyntheticScenario::AmplitudeBurst:
        return "amplitude_burst";
    }
    invalid("unknown SyntheticScenario");
}

void validate(const SyntheticSourceConfig& value) {
    static_cast<void>(to_wire(value.scenario));
    if (value.schema_version != synthetic_schema_version) {
        invalid("unsupported synthetic schema_version");
    }
    if (value.sample_count == 0U) {
        invalid("sample_count must be positive");
    }
    if (!std::isfinite(value.sample_rate_hz) || value.sample_rate_hz <= 0.0) {
        invalid("sample_rate_hz must be finite and positive");
    }
    if (!std::isfinite(value.center_frequency_hz) || value.center_frequency_hz < 0.0) {
        invalid("center_frequency_hz must be finite and non-negative");
    }
}

SyntheticSourceSkeleton::SyntheticSourceSkeleton(SyntheticSourceConfig config)
    : config_(std::move(config)) {
    validate(config_);
}

const SyntheticSourceConfig& SyntheticSourceSkeleton::config() const noexcept {
    return config_;
}

SourceDescriptor SyntheticSourceSkeleton::descriptor() const {
    SourceDescriptor result{
        SourceType::Synthetic,
        "synthetic:" + std::string(to_wire(config_.scenario)) + ":" + seed_hex(config_.seed),
        "Synthetic " + std::string(to_wire(config_.scenario)),
        {},
        {},
        "synthetic_reference",
        contract_schema_version,
        {
            {"scenario", "\"" + std::string(to_wire(config_.scenario)) + "\""},
            {"seed", std::to_string(config_.seed)},
            {"sample_count", std::to_string(config_.sample_count)},
            {"synthetic_schema_version", std::to_string(config_.schema_version)},
        },
    };
    validate(result);
    return result;
}

DeviceCapabilities SyntheticSourceSkeleton::capabilities() const {
    DeviceCapabilities result{
        "synthetic_reference",
        "synthetic",
        seed_hex(config_.seed),
        "Deterministic synthetic source skeleton",
        "P03",
        {0.0, 6'000'000'000.0, std::nullopt},
        {{config_.sample_rate_hz, config_.sample_rate_hz, std::nullopt}},
        {{config_.sample_rate_hz, config_.sample_rate_hz, std::nullopt}},
        {0.0, 0.0, std::nullopt},
        {GainMode::Manual},
        {SampleFormat::ComplexFloat32Le},
        true,
        false,
        false,
        true,
        true,
        contract_schema_version,
    };
    validate(result);
    return result;
}

std::uint64_t SyntheticSourceSkeleton::block_seed(const std::uint64_t block_index) const noexcept {
    return splitmix64(config_.seed + block_index * 0xD1342543DE82EF95ULL);
}

}  // namespace sdr_core
