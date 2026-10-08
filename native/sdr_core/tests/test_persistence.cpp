#include "sdr_core/persistence.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <iostream>
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

int run_tests() {
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

    // Actual accumulator, same source/config/grid: recipe changes must not
    // mix power populations. UNKNOWN is not silently equivalent to OFF.
    for (const auto mode : {sdr_core::PersistenceMode::RollingExact,
                           sdr_core::PersistenceMode::ExponentialDecay}) {
        auto recipe_config = config;
        recipe_config.mode = mode;
        const auto off = sdr_core::DspProcessingRecipeV1::from_dc_mode(
            sdr_core::DcRemovalMode::Off);
        const auto mean = sdr_core::DspProcessingRecipeV1::from_dc_mode(
            sdr_core::DcRemovalMode::BlockMean);
        const std::optional<sdr_core::DspProcessingRecipeV1> recipes[] = {
            off, mean, std::nullopt, off, std::nullopt, mean};
        auto journal = std::make_shared<sdr_core::LayerReadyJournal>(16U);
        sdr_core::PersistenceAccumulator isolated(recipe_config, journal);
        std::uint64_t previous_accumulation = 0U;
        for (std::size_t index = 0; index < std::size(recipes); ++index) {
            auto input = frame(1 + static_cast<std::int64_t>(index) * 34'000'000LL,
                index + 1U, index % 2U == 0U ? -80.0F : -20.0F);
            input.config_generation = 7U;
            input.dsp_processing_recipe = recipes[index];
            input.first_sample_index = index * 4096U;
            input.window_normalization_version = "power-norm-v1";
            const auto snapshot = isolated.update(input);
            require(snapshot && snapshot->processed_frames == 1U,
                "same-generation recipe transition mixed persistence populations");
            require(snapshot->layer_ready &&
                snapshot->layer_ready->accumulation_sequence > previous_accumulation,
                "recipe transition reused density accumulation identity");
            previous_accumulation = snapshot->layer_ready->accumulation_sequence;
            require((*snapshot->density)[index % 2U == 0U ? 6U : 0U] == 0.0F,
                "recipe transition retained previous power row");
            require(snapshot->processing_metadata &&
                snapshot->processing_metadata->matches(input) &&
                snapshot->processing_metadata->dsp_processing_recipe.has_value() == recipes[index].has_value() &&
                snapshot->first_sample_index == input.first_sample_index,
                "density metadata was not transported from actual contributing frame");
        }
    }

    // Every numerical identity transition resets even under unchanged RF/grid
    // generation. Stable unknown uncertainty (NaN) does not reset each FFT.
    for (int field = 0; field < 18; ++field) {
        sdr_core::PersistenceAccumulator isolated(config);
        auto before = frame(1, 1, -80.0F);
        const auto retained = isolated.update(before);
        auto after = frame(34'000'001LL, 2, -20.0F);
        switch (field) {
        case 0: after.center_frequency_hz = 1.; break;
        case 1: after.sample_rate_hz = 1.; break;
        case 2: after.analog_bandwidth_hz = 1.; break;
        case 3: after.fft_bin_width_hz = 1.; break;
        case 4: after.enbw_hz = 1.; break;
        case 5: after.nominal_rbw_hz = 1.; break;
        case 6: after.fft_size = 512U; break;
        case 7: after.hop_size = 256U; break;
        case 8: after.window = sdr_core::WindowType::Rectangular; break;
        case 9: after.detector = sdr_core::DetectorType::Peak; break;
        case 10: after.averaging_frames = 2U; break;
        case 11: after.window_normalization_version = "power-norm-v1"; break;
        case 12: after.calibration_profile_id = "different"; break;
        case 13: after.estimated_uncertainty_db = 1.; break;
        case 14: after.source.backend_id = "different"; break;
        case 15: after.precision_mode = sdr_core::PrecisionMode::ReferenceF64; break;
        case 16: after.window_normalization_version = ""; break;
        default: break;  // identical signature, UNKNOWN NaN preserved
        }
        const auto snapshot = isolated.update(after);
        require(snapshot && snapshot->processed_frames == (field == 17 ? 2U : 1U),
            "persistence numerical identity reset/stable-UNKNOWN parity incorrect");
        require(snapshot->processing_metadata && snapshot->processing_metadata->matches(after),
            "density numerical contributing signature missing");
        require(retained && retained->processing_metadata && retained->processing_metadata->matches(before),
            "later processing transition mutated retained snapshot metadata");
    }
    {
        auto journal = std::make_shared<sdr_core::LayerReadyJournal>(8U);
        sdr_core::PersistenceAccumulator bounded(config, journal);
        auto input = frame(1, 1, -80.0F);
        input.config_generation = 7U;
        input.window_normalization_version = std::string(sdr_core::persistence_metadata_string_max_bytes, 'x');
        input.calibration_profile_id = *input.window_normalization_version;
        const auto first_bounded = bounded.update(input);
        require(first_bounded && first_bounded->processing_metadata,
            "maximum bounded metadata refused");
        const auto created = journal->summary().created;
        for (int field = 0; field < 2; ++field) {
            auto bad = input;
            if (field == 0) bad.window_normalization_version->push_back('x');
            else bad.calibration_profile_id.push_back('x');
            bool refused = false;
            try { static_cast<void>(bounded.update(bad)); }
            catch (const sdr_core::ConfigurationError&) { refused = true; }
            require(refused && bounded.processed_frames() == 1U && journal->summary().created == created,
                "oversize metadata mutated density/history/creation identity before refusal");
        }
        input.timestamp_ns = 34'000'001LL;
        const auto same = bounded.update(input);
        require(same && same->processed_frames == 2U && same->layer_ready &&
            first_bounded->layer_ready->accumulation_sequence == same->layer_ready->accumulation_sequence,
            "metadata refusal corrupted accepted accumulator");
    }

    {
        auto journal = std::make_shared<sdr_core::LayerReadyJournal>(8U);
        sdr_core::PersistenceAccumulator measured(config, journal), baseline(config);
        auto input = frame(1, 1U, -80.0F);
        input.config_generation = 7U;
        const auto snapshot = measured.update(input), control = baseline.update(input);
        require(snapshot && control && snapshot->layer_ready && !control->layer_ready,
            "density creation evidence opt-in changed default measurement");
        const auto ref = *snapshot->layer_ready;
        require(ref.kind == sdr_core::LayerReadyKind::Density && ref.config_generation == 7U &&
            ref.update_sequence == snapshot->update_sequence && ref.source_frame_sequence == 1U &&
            snapshot->timestamp_ns == 1 && *snapshot->density == *control->density &&
            snapshot->probability_scale == control->probability_scale &&
            snapshot->count_scale == control->count_scale,
            "density evidence changed data/time/normalization");
        input.timestamp_ns = 2;
        input.frame_sequence = 2;
        require(!measured.update(input) && journal->summary().created == 1U,
            "snapshot cadence suppression created false density evidence");
        const auto retained = *snapshot;
        require(retained.layer_ready == snapshot->layer_ready && journal->summary().created == 1U,
            "cached density copy created new evidence");
        measured.reset();
        const auto restarted = measured.update(input);
        require(restarted && restarted->layer_ready &&
            restarted->layer_ready->accumulation_sequence > ref.accumulation_sequence &&
            restarted->layer_ready->creation_sequence > ref.creation_sequence,
            "density reset reused previous accumulation identity");
        input.config_generation = 0U;
        const auto unknown = measured.update(input);
        require(unknown && !unknown->layer_ready && journal->summary().created == 2U,
            "unknown density generation was fabricated");
    }

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

    // A sustained high-rate hot bin rounds in float, while raw_weight_ is
    // double. Every published probability must stay inside the public [0,1]
    // contract without relaxing the renderer's malformed-input validation.
    sdr_core::PersistenceAccumulator sustained(exponential);
    auto hot = frame(1, 1, -80.0F);
    std::uint64_t sustained_snapshots = 0U;
    double theoretical_weight = 0.0;
    double theoretical_decay_scale = 1.0;
    bool old_normalization_would_reject = false;
    // Continue past the decay-scale rebase (about 20 half-lives), so the
    // tracked maximum is checked both before and after full-grid rescaling.
    for (std::uint64_t index = 1U; index <= 60'000U; ++index) {
        hot.frame_sequence = index;
        hot.timestamp_ns = 1 + static_cast<std::int64_t>(index - 1U) * 409'600LL;
        if (index > 1U) {
            theoretical_decay_scale *= std::exp(
                -std::log(2.0) * 409'600.0 / 1.0e9
            );
            if (theoretical_decay_scale < 1.0e-6) {
                theoretical_weight *= theoretical_decay_scale;
                theoretical_decay_scale = 1.0;
            }
        }
        theoretical_weight += static_cast<double>(
            static_cast<float>(1.0 / theoretical_decay_scale)
        );
        const auto published = sustained.update(hot);
        if (!published) continue;
        ++sustained_snapshots;
        const auto maximum = *std::max_element(
            published->density->begin(), published->density->end()
        );
        const auto maximum_double = static_cast<double>(maximum);
        const double expected_weight = maximum_double > theoretical_weight
            ? maximum_double / (1.0 - 2.0 * std::numeric_limits<float>::epsilon())
            : theoretical_weight;
        const double expected_scale = 1.0 / expected_weight;
        require(std::fabs(published->probability_scale - expected_scale) <=
                    expected_scale * 1.0e-12,
                "tracked exponential maximum differs from snapshot scan");
        old_normalization_would_reject |= static_cast<float>(
            maximum / theoretical_weight
        ) > 1.0F;
        require(static_cast<float>(maximum * published->probability_scale) <= 1.0F,
                "exponential probability exceeded one after float accumulation");
    }
    require(sustained_snapshots >= 300U,
            "sustained probability regression did not exercise snapshots");
    require(old_normalization_would_reject,
            "sustained regression never reproduced the old normalization rejection");

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

int main() {
    try { return run_tests(); }
    catch (const std::exception& error) {
        std::cerr << "persistence regression: " << error.what() << '\n';
        return 1;
    }
}
