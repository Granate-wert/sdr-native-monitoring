#include "sdr_core/persistence.hpp"

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <vector>

namespace {

sdr_core::SpectrumFrame frame(
    std::int64_t timestamp_ns,
    std::uint64_t sequence,
    float value
) {
    sdr_core::SpectrumFrame result;
    result.frame_sequence = sequence;
    result.timestamp_ns = timestamp_ns;
    result.frequencies_hz = std::make_shared<const std::vector<double>>(
        std::vector<double>{2.400e9, 2.401e9}
    );
    result.values = std::make_shared<const std::vector<float>>(
        std::vector<float>{value, value}
    );
    return result;
}

void require(const bool condition, const char* message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

}  // namespace

int main() {
    sdr_core::PersistenceConfig config{
        .enabled = true,
        .mode = sdr_core::PersistenceMode::RollingExact,
        .window_frames = 2U,
        .half_life_seconds = 1.0,
        .power_min_db = -100.0,
        .power_max_db = 0.0,
        .power_bins = 4U,
        .snapshot_rate_hz = 1.0e9,
    };
    sdr_core::PersistenceAccumulator accumulator(config);

    const auto first = accumulator.update(frame(1, 1, -80.0F));
    const auto second = accumulator.update(frame(2, 2, -20.0F));
    const auto third = accumulator.update(frame(3, 3, -20.0F));

    require(first.has_value() && second.has_value() && third.has_value(),
            "persistence snapshots were not emitted");
    require(third->frequency_bins == 2U && third->power_bins == 4U,
            "persistence snapshot geometry is incorrect");
    require(third->density && third->density->size() == 8U,
            "persistence density has incorrect bounded size");
    require((*third->density)[0] == 0.0F,
            "rolling exact window retained an expired power row");
    require((*third->density)[6] == 2.0F && (*third->density)[7] == 2.0F,
            "rolling exact window did not retain the current rows");
    require(accumulator.processed_frames() == 3U,
            "persistence processed frame counter is incorrect");
    return 0;
}
