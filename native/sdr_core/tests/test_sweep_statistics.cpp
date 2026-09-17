#include "sdr_core/sweep_statistics.hpp"

#include <algorithm>
#include <cmath>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <vector>

namespace {
using namespace sdr_core;
constexpr float nan = std::numeric_limits<float>::quiet_NaN();

void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

template <typename F> void rejects(F operation) {
    bool threw = false;
    try { operation(); } catch (const std::exception&) { threw = true; }
    require(threw, "invalid input was accepted");
}

SourceDescriptor source() {
    SourceDescriptor result;
    result.source_id = "test-rx1";
    result.display_name = "Synthetic test RX1";
    return result;
}

SharedArray<double> grid() {
    return std::make_shared<const std::vector<double>>(std::vector<double>{100e6, 101e6, 102e6});
}

SweepStatisticsConfig config(std::uint32_t window = 2) {
    return {window, 4, -100.0, 0.0, 1024U * 1024U};
}

SweepProgressFrame partial(std::uint64_t seq, std::uint64_t revision, std::vector<float> values) {
    SweepProgressFrame f;
    f.source = source(); f.epoch = 7; f.line_sequence = seq; f.revision = revision;
    f.frequencies_hz = grid();
    f.values = std::make_shared<const std::vector<float>>(std::move(values));
    return f;
}

SweepLineFrame terminal(std::uint64_t seq, std::vector<float> values,
                        SweepLineState state = SweepLineState::Complete) {
    SweepLineFrame f;
    f.source = source(); f.epoch = 7; f.line_sequence = seq; f.state = state;
    f.frequencies_hz = grid();
    f.values = std::make_shared<const std::vector<float>>(std::move(values));
    return f;
}

SweepStatisticsAccumulator accumulator(std::uint32_t window = 2) {
    return {config(window), source(), 7, SpectrumUnit::DbfsBin, grid()};
}

void verify(const SweepStatisticsSnapshot& s, const std::vector<std::vector<float>>& rows) {
    require(s.source_id == "test-rx1" && s.epoch == 7 && s.unit == SpectrumUnit::DbfsBin,
            "statistics lost source/epoch/unit");
    for (std::size_t f = 0; f < 3; ++f) {
        std::uint32_t count = 0;
        double power = 0.0;
        std::vector<std::uint32_t> bins(4);
        for (const auto& row : rows) {
            if (std::isnan(row[f])) continue;
            ++count;
            power += std::pow(10.0, static_cast<double>(row[f]) / 10.0);
            const int index = std::max(0, std::min(3, static_cast<int>((row[f] + 100.0F) / 25.0F)));
            ++bins[static_cast<std::size_t>(index)];
        }
        require((*s.observations)[f] == count, "frequency observation denominator is wrong");
        for (std::size_t p = 0; p < 4; ++p) {
            require((*s.histogram_counts)[p * 3 + f] == bins[p], "histogram double count or stale hit");
            require(count == 0 ? std::isnan((*s.probability)[p * 3 + f]) :
                std::abs((*s.probability)[p * 3 + f] - static_cast<double>(bins[p]) / count) < 1e-7,
                "native density probability has wrong denominator");
        }
        require((*s.density_observations)[f] == count, "unpooled density denominator mismatch");
        if (count == 0) {
            require(std::isnan((*s.average_db)[f]), "missing bin became measured zero");
        } else {
            require(std::abs((*s.average_db)[f] - 10.0 * std::log10(power / count)) < 0.00002,
                    "average is not a linear-power mean");
        }
    }
}

void replacement_and_late_terminal() {
    auto a = accumulator();
    require(a.update(partial(0, 1, {-80, nan, nan})), "first partial rejected");
    const auto immutable_first = a.snapshot();
    require(!a.update(partial(0, 1, {-20, -20, -20})), "duplicate revision was recounted");
    require(a.update(partial(0, 2, {-50, -30, nan})), "overlap revision rejected");
    verify(a.snapshot(), {{-50, -30, nan}});
    verify(immutable_first, {{-80, nan, nan}});
    require(a.update(partial(1, 1, {-20, nan, -40})), "second partial rejected");
    require(a.update(terminal(0, {-50, -30, nan}, SweepLineState::Gap)), "retained late gap rejected");
    auto s = a.snapshot();
    verify(s, {{-50, -30, nan}, {-20, nan, -40}});
    require(s.unique_passes_seen == 2 && s.newest_pass_sequence == 1 && s.retained_passes == 2 &&
            s.update_sequence == 4, "revision count confused with unique passes");
    require(!a.update(partial(0, 99, {0, 0, 0})), "terminal pass reopened");
    require(!a.update(terminal(0, {0, 0, 0})), "duplicate terminal accepted");
    require(a.update(terminal(1, {-20, -10, -40})), "terminal replacement rejected");
    verify(a.snapshot(), {{-50, -30, nan}, {-20, -10, -40}});
    require(a.update(terminal(4, {nan, nan, nan}, SweepLineState::Gap)), "empty gap rejected");
    verify(a.snapshot(), {{-20, -10, -40}, {nan, nan, nan}});
    require(!a.update(terminal(0, {0, 0, 0})), "evicted pass resurrected");
    require(!a.update(terminal(3, {0, 0, 0})), "unseen reordered pass admitted");
    a.reset();
    verify(a.snapshot(), {});
    require(a.snapshot().unique_passes_seen == 0 && a.update(terminal(0, {-90, -80, -70})),
            "explicit reset did not allow sequence reuse");
}

void errors_are_atomic_and_bounded() {
    auto a = accumulator();
    require(a.update(partial(10, 1, {-80, -60, -40})), "seed rejected");
    for (int fault = 0; fault < 9; ++fault) {
        auto f = partial(10, 2, {-10, -20, -30});
        if (fault == 0) f.source.source_id = "rx2";
        if (fault == 1) ++f.epoch;
        if (fault == 2) f.unit = SpectrumUnit::DbfsHz;
        if (fault == 3) f.frequencies_hz = std::make_shared<const std::vector<double>>(
            std::vector<double>{100e6, 101e6, 103e6});
        if (fault == 4) f.values.reset();
        if (fault == 5) f.revision = 0;
        if (fault == 6) f.values = std::make_shared<const std::vector<float>>(
            std::vector<float>{-10, -20, std::numeric_limits<float>::infinity()});
        if (fault == 7) f.values = std::make_shared<const std::vector<float>>(
            std::vector<float>{-10, -20, 4000});
        if (fault == 8) f.values = std::make_shared<const std::vector<float>>(2U, -10.0F);
        rejects([&] { static_cast<void>(a.update(f)); });
        verify(a.snapshot(), {{-80, -60, -40}});
        require(a.snapshot().update_sequence == 1, "failed input changed counters");
    }
    auto c = config();
    const auto bytes = SweepStatisticsAccumulator::required_payload_bytes(c, 3);
    c.max_payload_bytes = bytes - 1;
    rejects([&] { SweepStatisticsAccumulator denied(c, source(), 7, SpectrumUnit::DbfsBin, grid()); });
    c.max_payload_bytes = bytes;
    SweepStatisticsAccumulator exact(c, source(), 7, SpectrumUnit::DbfsBin, grid());
    c.max_payload_bytes = std::numeric_limits<std::size_t>::max();
    rejects([&] { static_cast<void>(SweepStatisticsAccumulator::required_payload_bytes(
        c, std::numeric_limits<std::size_t>::max())); });
    c.window_passes = 0;
    rejects([&] { static_cast<void>(SweepStatisticsAccumulator::required_payload_bytes(c, 3)); });
    c = config();
    rejects([&] { SweepStatisticsAccumulator denied(c, source(), 7, SpectrumUnit::DbfsBin,
        std::make_shared<const std::vector<double>>(std::vector<double>{1, 1, 2})); });
    c.max_payload_bytes = 128U * 1024U * 1024U;
    c.window_passes = 500; c.power_bins = 256;
    rejects([&] { static_cast<void>(SweepStatisticsAccumulator::required_payload_bytes(c, 2'000'000)); });
}

void numerical_and_coalescing() {
    auto a = accumulator();
    require(a.update(terminal(0, {0, 0, 0})), "strong input failed");
    require(a.update(terminal(1, {-200, -200, -200})), "weak input failed");
    require(a.update(terminal(2, {nan, nan, nan}, SweepLineState::Gap)), "eviction failed");
    verify(a.snapshot(), {{-200, -200, -200}});

    auto all_revisions = accumulator(7);
    auto final_only = accumulator(7);
    std::mt19937 random(12345);
    std::uniform_real_distribution<float> db(-140, 20);
    std::vector<std::vector<float>> reference;
    for (std::uint64_t seq = 0; seq < 2000; ++seq) {
        std::vector<float> row{db(random), db(random), seq % 3 == 0 ? nan : db(random)};
        require(all_revisions.update(partial(seq, 1, {row[0] - 20, nan, nan})), "revision 1 failed");
        require(all_revisions.update(partial(seq, 2, {row[0], row[1], nan})), "revision 2 failed");
        require(all_revisions.update(terminal(seq, row)), "terminal failed");
        require(final_only.update(terminal(seq, row)), "direct terminal failed");
        reference.push_back(row);
        if (reference.size() > 7) reference.erase(reference.begin());
        verify(all_revisions.snapshot(), reference);
        verify(final_only.snapshot(), reference);
        require(*all_revisions.snapshot().histogram_counts == *final_only.snapshot().histogram_counts,
                "publication count affected statistics");
    }
}

void actual_assembler_overlap_and_gap() {
    SweepLineDefinition definition;
    definition.source = source(); definition.epoch = 7;
    definition.start_frequency_hz = 100e6; definition.stop_frequency_hz = 102e6;
    definition.target_spacing_hz = 1e6; definition.max_inflight_lines = 2;
    definition.segments = {
        {0, 11, 100e6, 101e6}, {1, 12, 101e6, 102e6}
    };
    ContinuousSweepLineAssembler assembler(definition);
    auto segment = [](std::uint32_t index, float value) {
        SweepLineSegmentFrame result;
        result.segment_index = index;
        auto& f = result.spectrum;
        f.source = source(); f.config_generation = 11 + index;
        f.frame_sequence = index; f.timestamp_ns = 123;
        f.center_frequency_hz = 101e6;
        f.sample_rate_hz = f.analog_bandwidth_hz = 4e6;
        f.fft_bin_width_hz = f.enbw_hz = f.nominal_rbw_hz = 1e6;
        f.fft_size = 3; f.hop_size = 1;
        f.frequencies_hz = grid();
        f.values = std::make_shared<const std::vector<float>>(3, value);
        return result;
    };
    auto a = accumulator();
    require(assembler.admit(0, 1000, segment(0, -80)).empty(), "early terminal");
    const auto p = assembler.preview(0);
    require(p && a.update(*p), "actual assembler partial rejected");
    verify(a.snapshot(), {{-80, -80, nan}});
    const auto lines = assembler.admit(0, 1001, segment(1, -20));
    require(lines.size() == 1 && a.update(lines[0]), "actual assembler terminal rejected");
    verify(a.snapshot(), {*lines[0].values});
    require((*a.snapshot().observations)[1] == 1, "RF overlap became two pass observations");
    require(assembler.admit(1, 1002, segment(1, -40)).empty(), "early second terminal");
    require(a.update(*assembler.preview(1)), "second actual partial rejected");
    const auto gaps = assembler.flush(SweepLineGapReason::Cancellation);
    require(gaps.size() == 1 && gaps[0].state == SweepLineState::Gap && a.update(gaps[0]),
            "cancelled assembler pass not finalised");
    verify(a.snapshot(), {*lines[0].values, *gaps[0].values});
}

void bounded_publication_cadence() {
    auto c = config();
    const auto one = SweepStatisticsAccumulator::required_payload_bytes(c, 3);
    const auto four = SweepStatisticsAccumulator::required_payload_bytes(c, 3, 4);
    require(four > one, "retained snapshots omitted from budget");
    c.max_payload_bytes = four - 1;
    rejects([&] { SweepStatisticsPublisher denied(c, source(), 7, SpectrumUnit::DbfsBin, grid(), 10, 4); });
    c.max_payload_bytes = four;
    SweepStatisticsPublisher publisher(c, source(), 7, SpectrumUnit::DbfsBin, grid(), 10, 4);
    auto first = partial(0, 1, {-80, nan, nan});
    publisher.consume(first, 0);
    require(first.statistics != nullptr, "initial native statistics not attached");
    auto revision = partial(0, 2, {-20, -30, nan});
    publisher.consume(revision, 1);
    require(revision.statistics == first.statistics, "publication cadence copied histogram per revision");
    auto final = terminal(0, {-20, -30, -40});
    publisher.consume(final, 100'000'000);
    verify(*final.statistics, {{-20, -30, -40}});
    require(final.statistics != first.statistics && final.statistics->unique_passes_seen == 1,
            "finalisation recounted same pass");
    verify(*first.statistics, {{-80, nan, nan}});
    auto gap = terminal(1, {nan, nan, nan}, SweepLineState::Gap);
    rejects([&] { publisher.consume(gap, -1, true); });
    publisher.consume(gap, 100'000'001, true);
    require(gap.statistics->newest_pass_sequence == 1 && publisher.newest_sequence() == 1,
            "forced terminal snapshot did not include latest pass");
    verify(*gap.statistics, {{-20, -30, -40}, {nan, nan, nan}});
}

void pooled_density_is_bounded_without_reducing_average() {
    auto c = config(); c.density_columns = 2;
    const auto frequencies = std::make_shared<const std::vector<double>>(
        std::vector<double>{100e6, 101e6, 102e6, 103e6, 104e6});
    SweepStatisticsAccumulator a(c, source(), 7, SpectrumUnit::DbfsBin, frequencies);
    auto f = partial(0, 1, {-90, -60, nan, nan, nan}); f.frequencies_hz = frequencies;
    require(a.update(f), "pooled first revision rejected");
    const auto first = a.snapshot();
    require(*first.density_frequency_edges_hz == std::vector<double>{99.5e6, 102e6, 104.5e6},
            "pooled density physical edges are not regular");
    require(*first.density_observations == std::vector<std::uint32_t>{2, 0}, "pooled missing cells counted");
    require((*first.probability)[0] == 0.5F && (*first.probability)[2] == 0.5F &&
            std::isnan((*first.probability)[1]), "pooled probability confused bins with passes");
    f = partial(0, 2, {-20, nan, -40, -80, -80}); f.frequencies_hz = frequencies;
    require(a.update(f), "pooled replacement rejected");
    const auto revised = a.snapshot();
    require(revised.average_db->size() == 5 && (*revised.average_db)[0] == -20 &&
            std::isnan((*revised.average_db)[1]) && (*revised.average_db)[2] == -40,
            "density pooling reduced the measurement average grid");
    require(*revised.density_observations == std::vector<std::uint32_t>{1, 3} &&
            revised.unique_passes_seen == 1, "revision double-counted pooled observations");
    require((*revised.histogram_counts)[1] == 2 && (*revised.histogram_counts)[5] == 1 &&
            (*revised.histogram_counts)[6] == 1, "pooled counts not reassigned on revision");
    for (std::size_t col = 0; col < 2; ++col) {
        double sum = 0;
        for (std::size_t row = 0; row < 4; ++row) sum += (*revised.probability)[row * 2 + col];
        require(std::abs(sum - 1.0) < 1e-7, "pooled probabilities do not sum to one");
    }
    auto last = terminal(1, {-90, -90, -90, -90, -90}); last.frequencies_hz = frequencies;
    require(a.update(last), "pooled terminal rejected");
    last = terminal(2, {nan, nan, nan, nan, nan}, SweepLineState::Gap); last.frequencies_hz = frequencies;
    require(a.update(last), "pooled eviction rejected");
    require(*a.snapshot().density_observations == std::vector<std::uint32_t>{2, 3},
            "eviction left stale pooled observations");
    require(*first.density_observations == std::vector<std::uint32_t>{2, 0}, "snapshot mutated");
    a.reset();
    require(*a.snapshot().density_observations == std::vector<std::uint32_t>{0, 0}, "pooled reset incomplete");
    c = {32, 64, -160, 10, 512U * 1024U * 1024U, 1024};
    require(SweepStatisticsAccumulator::required_payload_bytes(c, 900'000, 12) < c.max_payload_bytes,
            "pooled wide Sweep exceeds admitted payload budget");
    c.density_columns = 0;
    rejects([&] { static_cast<void>(SweepStatisticsAccumulator::required_payload_bytes(c, 900'000, 12)); });
    c.max_payload_bytes = std::numeric_limits<std::size_t>::max();
    c.density_columns = 1; c.window_passes = 5000;
    rejects([&] { static_cast<void>(SweepStatisticsAccumulator::required_payload_bytes(c, 2'000'000)); });
    rejects([&] { SweepStatisticsAccumulator invalid(config(), source(), 7, SpectrumUnit::DbfsBin,
        std::make_shared<const std::vector<double>>(std::vector<double>{1, 2, 4})); });
}
}  // namespace

int main() {
    try {
        {
            const auto zero = -std::numeric_limits<float>::infinity();
            auto a = accumulator();
            require(a.update(terminal(0, {zero, zero, nan})), "zero power rejected");
            const auto first = a.snapshot();
            require((*first.average_db)[0] == zero && (*first.observations)[0] == 1 &&
                    (*first.histogram_counts)[0] == 1 && (*first.probability)[0] == 1 &&
                    std::isnan((*first.average_db)[2]) && (*first.observations)[2] == 0,
                    "zero power confused with unknown density/average");
            require(a.update(terminal(1, {-90, zero, nan})), "mixed zero pass rejected");
            require(std::abs((*a.snapshot().average_db)[0] - (-90.0F - 3.01029995664F)) < 1e-4F &&
                    (*a.snapshot().average_db)[1] == zero && (*a.snapshot().observations)[1] == 2,
                    "zero contributor lost from mean denominator");
            require(a.update(terminal(2, {zero, zero, nan})), "zero eviction rejected");
            require(a.update(terminal(3, {zero, zero, nan})), "finite eviction rejected");
            require((*a.snapshot().average_db)[0] == zero && (*a.snapshot().observations)[0] == 2 &&
                    (*first.observations)[0] == 1, "zero eviction corrupted retained snapshot or mean");
        }
        replacement_and_late_terminal();
        errors_are_atomic_and_bounded();
        numerical_and_coalescing();
        actual_assembler_overlap_and_gap();
        bounded_publication_cadence();
        pooled_density_is_bounded_without_reducing_average();
        std::cout << "Sweep statistics: replacement/order/gaps/budget/identity/numerical/2000-pass oracle PASS\n";
    } catch (const std::exception& error) {
        std::cerr << "Sweep statistics test failed: " << error.what() << '\n';
        return 1;
    }
}
