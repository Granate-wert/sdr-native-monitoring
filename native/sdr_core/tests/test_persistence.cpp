#include "sdr_core/persistence.hpp"

#include <cmath>
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
        .snapshot_rate_hz = 30.0,
    };
    sdr_core::PersistenceAccumulator accumulator(config);

    const auto first = accumulator.update(frame(1, 1, -80.0F));
    const auto second = accumulator.update(frame(34'000'001LL, 2, -20.0F));
    const auto third = accumulator.update(frame(68'000'001LL, 3, -20.0F));

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

    const auto source_axis_frame = frame(102'000'001LL, 4, -20.0F);
    const auto shared_axis = accumulator.update(source_axis_frame);
    require(shared_axis.has_value() && shared_axis->frequencies_hz == source_axis_frame.frequencies_hz,
            "persistence snapshot must retain the immutable source axis");

    sdr_core::PersistenceConfig exponential = config;
    for (int change = 0; change < 4; ++change) {
        sdr_core::PersistenceAccumulator isolated(config);
        auto before = frame(1, 1, -80.0F);
        before.source.source_id = "source-a";
        static_cast<void>(isolated.update(before));
        auto after = frame(34'000'001LL, 2, -20.0F);
        after.source = before.source;
        if (change == 0) after.source.source_id = "source-b";
        if (change == 1) after.config_generation = 2;
        if (change == 3) after.unit = sdr_core::SpectrumUnit::DbfsHz;
        if (change == 2) after.frequencies_hz = std::make_shared<const std::vector<double>>(
            std::vector<double>{2.500e9, 2.501e9});
        const auto reset_snapshot = isolated.update(after);
        require(reset_snapshot.has_value() && reset_snapshot->processed_frames == 1U,
                "persistence identity change retained previous frame count");
        require((*reset_snapshot->density)[0] == 0.0F && (*reset_snapshot->density)[6] == 1.0F,
                "persistence mixed bins across measurement identities");
        require(reset_snapshot->source.source_id == after.source.source_id &&
                reset_snapshot->config_generation == after.config_generation,
                "persistence snapshot lost producer identity");
    }
    exponential.mode = sdr_core::PersistenceMode::ExponentialDecay;
    exponential.half_life_seconds = 1.0;
    sdr_core::PersistenceAccumulator decaying(exponential);
    const auto initial = decaying.update(frame(1, 1, -80.0F));
    const auto after_half_life = decaying.update(frame(1'000'000'001LL, 2, -80.0F));
    require(initial.has_value() && after_half_life.has_value(),
            "exponential snapshots were not emitted");
    require((*after_half_life->density)[0] == 3.0F,
            "lazy exponential decay must retain raw epoch contributions");
    require(std::fabs(after_half_life->probability_scale - (1.0 / 3.0)) < 1e-12,
            "probability scale must normalize the lazy epoch");
    require(std::fabs(after_half_life->count_scale - 0.5) < 1e-12,
            "count scale must carry elapsed exponential decay");
    return 0;
}
