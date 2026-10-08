#include "sdr_core/dsp_processing.hpp"
#include "sdr_core/errors.hpp"

namespace sdr_core {
namespace {

// Exact schema1 canonical recipes, cross-checked against the domain serializer
// and SHA256 in binding tests. Static digests are valid ONLY for these two fixed
// recipes; a future profile/comparison recipe needs real bounded admission.
constexpr std::string_view off_policy =
    R"({"compare_raw":false,"dc_mode":"off","schema":"sdr-processing-policy","schema_version":1,"spur_mode":"off","spur_profile":null})";
constexpr std::string_view block_mean_policy =
    R"({"compare_raw":false,"dc_mode":"block_mean_v1","schema":"sdr-processing-policy","schema_version":1,"spur_mode":"off","spur_profile":null})";
constexpr std::string_view off_digest =
    "sha256:ea4bbcf4d10b2def528a74327c67e3799144dc403b68f4caccb477e181e16d42";
constexpr std::string_view block_mean_digest =
    "sha256:35edc4d70fe1adfa8e3862c4128a6ab3cb1f33de27fd94af4eb9dd4cdb272187";

}  // namespace

DspProcessingRecipeV1 DspProcessingRecipeV1::from_dc_mode(const DcRemovalMode mode) {
    if (mode != DcRemovalMode::Off && mode != DcRemovalMode::BlockMean) {
        throw ConfigurationError("unknown native DC mode; no silent OFF fallback");
    }
    return DspProcessingRecipeV1(mode);
}

DspProcessingRecipeV1 DspProcessingRecipeV1::from_canonical_policy(const std::string_view bytes) {
    if (bytes.empty() || bytes.size() > processing_policy_max_bytes) {
        throw ConfigurationError("native processing policy exceeds bounded canonical input");
    }
    if (bytes == off_policy) { return from_dc_mode(DcRemovalMode::Off); }
    if (bytes == block_mean_policy) { return from_dc_mode(DcRemovalMode::BlockMean); }
    throw ConfigurationError(
        "CPU recipe1 supports exact canonical OFF/BlockMean with spur OFF and no comparison; "
        "malformed, noncanonical or unsupported policy refused"
    );
}

std::string_view DspProcessingRecipeV1::dc_algorithm() const noexcept {
    return mode_ == DcRemovalMode::BlockMean ? "block_mean_v1" : "off";
}
std::string_view DspProcessingRecipeV1::canonical_policy() const noexcept {
    return mode_ == DcRemovalMode::BlockMean ? block_mean_policy : off_policy;
}
std::string_view DspProcessingRecipeV1::policy_digest() const noexcept {
    return mode_ == DcRemovalMode::BlockMean ? block_mean_digest : off_digest;
}

}  // namespace sdr_core
