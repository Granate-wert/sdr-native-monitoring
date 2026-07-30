#pragma once

#include "sdr_core/capabilities.hpp"
#include "sdr_core/types.hpp"

#include <cstdint>
#include <string_view>

namespace sdr_core {

inline constexpr std::string_view synthetic_schema_name = "sdr-synthetic-source";
inline constexpr std::uint32_t synthetic_schema_version = 1;

enum class SyntheticScenario : std::uint8_t {
    ExactBinTone,
    HalfBinTone,
    TwoTones,
    CloseTones,
    BroadbandNoise,
    DcOffset,
    Impulse,
    Clipping,
    IqImbalance,
    Chirp,
    Hopping,
    AmplitudeBurst,
};

[[nodiscard]] std::string_view to_wire(SyntheticScenario value);

struct SyntheticSourceConfig {
    SyntheticScenario scenario{SyntheticScenario::ExactBinTone};
    std::uint64_t seed{0x5344525F50303301ULL};
    std::uint32_t sample_count{1024U};
    double sample_rate_hz{1'024'000.0};
    double center_frequency_hz{100'000'000.0};
    std::uint32_t schema_version{synthetic_schema_version};
};

void validate(const SyntheticSourceConfig& value);

class SyntheticSourceSkeleton final {
public:
    explicit SyntheticSourceSkeleton(SyntheticSourceConfig config);

    [[nodiscard]] const SyntheticSourceConfig& config() const noexcept;
    [[nodiscard]] SourceDescriptor descriptor() const;
    [[nodiscard]] DeviceCapabilities capabilities() const;
    [[nodiscard]] std::uint64_t block_seed(std::uint64_t block_index) const noexcept;

private:
    SyntheticSourceConfig config_;
};

}  // namespace sdr_core
