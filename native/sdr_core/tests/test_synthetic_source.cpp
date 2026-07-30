#include "sdr_core/errors.hpp"
#include "sdr_core/synthetic_source.hpp"

#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {

void expect(const bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

}  // namespace

int main() {
    try {
        using namespace sdr_core;

        const SyntheticSourceConfig config{};
        validate(config);
        const SyntheticSourceSkeleton source(config);
        const auto descriptor = source.descriptor();
        const auto capabilities = source.capabilities();

        expect(descriptor.source_type == SourceType::Synthetic, "source type mismatch");
        expect(descriptor.source_id.find("exact_bin_tone") != std::string::npos, "scenario missing");
        expect(capabilities.supports_continuous_iq, "synthetic source must support continuous IQ");
        expect(capabilities.sample_formats.size() == 1U, "sample-format contract mismatch");
        expect(
            capabilities.sample_formats.front() == SampleFormat::ComplexFloat32Le,
            "synthetic source format mismatch"
        );
        expect(source.block_seed(0U) == source.block_seed(0U), "block seed is not deterministic");
        expect(source.block_seed(0U) != source.block_seed(1U), "block seeds must vary by block");

        auto invalid = config;
        invalid.sample_count = 0U;
        bool rejected = false;
        try {
            static_cast<void>(SyntheticSourceSkeleton(invalid));
        } catch (const ConfigurationError&) {
            rejected = true;
        }
        expect(rejected, "invalid synthetic config was accepted");

        std::cout << "P03 synthetic source skeleton OK\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
