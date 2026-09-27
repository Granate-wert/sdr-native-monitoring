#include "sdr_core/persistence.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <limits>
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

    // The accumulator may choose a cache-friendly internal layout, but each
    // immutable publication remains renderer-ready [power, frequency].
    auto distinct = frame(1, 1, -90.0F);
    distinct.frequencies_hz = std::make_shared<const std::vector<double>>(
        std::vector<double>{2.400e9, 2.401e9, 2.402e9});
    distinct.values = std::make_shared<const std::vector<float>>(
        std::vector<float>{-90.0F, -50.0F, -10.0F});
    sdr_core::PersistenceAccumulator layout(config);
    const auto layout_first = layout.update(distinct);
    require(layout_first.has_value() && layout_first->density &&
            *layout_first->density == std::vector<float>({
                1.0F, 0.0F, 0.0F,
                0.0F, 0.0F, 0.0F,
                0.0F, 1.0F, 0.0F,
                0.0F, 0.0F, 1.0F}),
            "persistence snapshot lost row-major power/frequency layout");
    distinct.timestamp_ns = 34'000'001LL;
    distinct.frame_sequence = 2U;
    distinct.values = std::make_shared<const std::vector<float>>(
        std::vector<float>{-10.0F, -50.0F, -90.0F});
    static_cast<void>(layout.update(distinct));
    distinct.timestamp_ns = 68'000'001LL;
    distinct.frame_sequence = 3U;
    distinct.values = std::make_shared<const std::vector<float>>(
        std::vector<float>{-90.0F, -90.0F, -90.0F});
    const auto layout_third = layout.update(distinct);
    require(layout_third.has_value() && layout_third->density &&
            *layout_third->density == std::vector<float>({
                1.0F, 1.0F, 2.0F,
                0.0F, 0.0F, 0.0F,
                0.0F, 1.0F, 0.0F,
                1.0F, 0.0F, 0.0F}),
            "rolling expiration or publication transposed the density image");
    require((*layout_first->density)[0] == 1.0F &&
            (*layout_first->density)[11] == 1.0F,
            "later updates mutated an immutable persistence publication");

    for (const std::uint32_t power_bins : {4U, 64U, 256U}) {
        auto mapping_config = config;
        mapping_config.power_bins = power_bins;
        sdr_core::PersistenceAccumulator mapping(mapping_config);
        std::vector<float> values;
        values.reserve(4'104U);
        for (int index = 0; index < 4'096; ++index) {
            values.push_back(-200.0F + static_cast<float>(index) * 0.1F);
        }
        for (const float boundary : {-100.0F, -75.0F, -50.0F, -25.0F,
                                     0.0F, std::numeric_limits<float>::max(),
                                     std::numeric_limits<float>::infinity(),
                                     std::numeric_limits<float>::quiet_NaN()}) {
            values.push_back(boundary);
        }
        sdr_core::SpectrumFrame mapped;
        mapped.frame_sequence = 1U;
        mapped.timestamp_ns = 1;
        mapped.frequencies_hz = std::make_shared<const std::vector<double>>(
            values.size(), 2.4e9);
        mapped.values = std::make_shared<const std::vector<float>>(values);
        const auto result = mapping.update(mapped);
        require(result.has_value() && result->density,
                "persistence boundary mapping did not publish");
        std::vector<float> expected(values.size() * power_bins, 0.0F);
        for (std::size_t column = 0U; column < values.size(); ++column) {
            const auto value = values[column];
            if (!std::isfinite(value)) {
                continue;
            }
            const double scaled =
                ((static_cast<double>(value) - mapping_config.power_min_db) /
                 (mapping_config.power_max_db - mapping_config.power_min_db)) *
                static_cast<double>(power_bins);
            const auto row = scaled <= 0.0 ? 0U :
                scaled >= static_cast<double>(power_bins) ? power_bins - 1U :
                static_cast<std::uint32_t>(std::floor(scaled));
            expected[static_cast<std::size_t>(row) * values.size() + column] = 1.0F;
        }
        require(*result->density == expected,
                "optimized mapping differs from floor/clamp reference");
    }
    return 0;
}
