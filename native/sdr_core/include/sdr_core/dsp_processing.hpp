#pragma once

#include <cstddef>
#include <cstdint>
#include <string_view>
#include <type_traits>

namespace sdr_core {

// Same existing enum/name/underlying type, independent of wire DspConfig/schema5.
enum class DcRemovalMode : std::uint8_t { Off, BlockMean };

inline constexpr std::uint32_t dsp_processing_recipe_version = 1U;
inline constexpr std::size_t processing_policy_max_bytes = 16'384U;

// Actual numerical-stage provenance, NOT full owner/RX/epoch admission. Inline
// storage only; no dynamic JSON, profile, zone or string allocation per FFT.
// Native producers create it from accepted options, never an incoming quality bit.
class DspProcessingRecipeV1 final {
public:
    [[nodiscard]] static DspProcessingRecipeV1 from_dc_mode(DcRemovalMode mode);
    [[nodiscard]] static DspProcessingRecipeV1 from_canonical_policy(std::string_view bytes);
    [[nodiscard]] DcRemovalMode dc_removal() const noexcept { return mode_; }
    [[nodiscard]] std::string_view dc_algorithm() const noexcept;
    [[nodiscard]] std::string_view canonical_policy() const noexcept;
    [[nodiscard]] std::string_view policy_digest() const noexcept;
    [[nodiscard]] bool whole_frame_modified() const noexcept { return mode_ == DcRemovalMode::BlockMean; }

private:
    explicit DspProcessingRecipeV1(DcRemovalMode mode) noexcept : mode_(mode) {}
    DcRemovalMode mode_;
};

static_assert(std::is_trivially_copyable_v<DspProcessingRecipeV1>);
static_assert(sizeof(DspProcessingRecipeV1) == sizeof(DcRemovalMode));

}  // namespace sdr_core
