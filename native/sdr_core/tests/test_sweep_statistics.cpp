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
        }
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
        if (fault == 8) f.values = std::make_shared<const std::vector<float>>(2, -10);
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
}  // namespace

int main() {
    replacement_and_late_terminal();
    errors_are_atomic_and_bounded();
    numerical_and_coalescing();
    std::cout << "Sweep statistics: replacement/order/gaps/budget/identity/numerical/2000-pass oracle PASS\n";
}
