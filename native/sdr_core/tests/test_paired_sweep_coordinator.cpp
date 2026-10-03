#include "sdr_pluto/continuous_sweep_coordinator.hpp"
#include "sdr_core/errors.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdlib>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <thread>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
struct Hooks {
    using Count = int(*)();
    using Arm = void(*)(int, long long, int);
    using Release = void(*)();
    HMODULE module{};
    Count contexts{}, buffers{}, lo{}, created_buffers{}, created_contexts{}, mutations{}, entered{}, expired{};
    Arm arm{};
    Release release{};
    Hooks() {
        const auto size = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", nullptr, 0);
        require(size != 0, "explicit mock path required");
        std::wstring path(size, L'\0');
        const auto written = GetEnvironmentVariableW(L"LIBIIO_DLL_PATH", path.data(), size);
        require(written && written < size, "mock path changed"); path.resize(written);
        module = LoadLibraryW(path.c_str()); require(module != nullptr, "mock load failed");
        contexts = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_live_contexts"));
        buffers = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_live_buffers"));
        lo = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_lo_write_calls"));
        created_buffers = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_created_buffers"));
        created_contexts = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_created_contexts"));
        mutations = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_rf_mutation_calls"));
        entered = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_phase_gate_entered"));
        expired = reinterpret_cast<Count>(GetProcAddress(module, "mock_iio_phase_gate_expired"));
        arm = reinterpret_cast<Arm>(GetProcAddress(module, "mock_iio_set_phase_gate"));
        release = reinterpret_cast<Release>(GetProcAddress(module, "mock_iio_release_phase_gate"));
        require(contexts && buffers && lo && created_buffers && created_contexts && mutations && entered && expired && arm && release,
                "mock hooks missing");
    }
    ~Hooks() { if (module) FreeLibrary(module); }
};
sdr_pluto::PairedContinuousSweepCoordinatorConfig config(bool single = false) {
    sdr_pluto::ContinuousSweepCoordinatorConfig primary;
    primary.epoch = 91;
    primary.display_start_hz = 2'422'000'000.;
    primary.display_stop_hz = single ? 2'458'000'000. : 2'488'000'000.;
    primary.usable_window_hz = 36'000'000.;
    primary.analysis_bins_per_usable_window = 2048;
    primary.output_queue_capacity = 8;
    primary.line_snapshot_rate_hz = 2000;
    primary.segment_frame_timeout_ms = 2000;
    for (int index = 0; index < (single ? 1 : 2); ++index) {
        sdr_pluto::FixedBandConfig fixed;
        fixed.device = {.source_id = "opaque-left", .context_uri = "usb:mock",
            .center_frequency_hz = 2'440'000'000. + index * 30'000'000.,
            .sample_rate_hz = 61'440'000., .analog_bandwidth_hz = 56'000'000.,
            .gain_mode = sdr_core::GainMode::Manual, .manual_gain_db = 20., .buffer_samples = 8192};
        fixed.dsp.fft_size = 4096; fixed.dsp.hop_size = 2048; fixed.dsp.batch_size = 1;
        fixed.backend = sdr_core::ComputeBackendKind::Cpu; fixed.allow_runtime_fallback = false;
        fixed.snapshot_rate_hz = 1.; // Cannot be used as the analytical source.
        fixed.discard_blocks_after_start = 0;
        primary.segments.push_back({fixed, fixed.device.center_frequency_hz - 18'000'000.,
                                         fixed.device.center_frequency_hz + 18'000'000.});
    }
    auto secondary = primary;
    for (auto& segment : secondary.segments) {
        segment.fixed_band.device.source_id = "opaque-right";
        segment.fixed_band.receiver_selection = sdr_pluto::ReceiverSelection::Rx2;
    }
    return {"admitted-resource", primary, secondary};
}
template<class F> void refused(F call) {
    bool failed = false;
    try { call(); } catch (const sdr_core::ConfigurationError&) { failed = true; }
    require(failed, "invalid paired plan admitted");
}
void wait_lines(sdr_pluto::ContinuousSweepCoordinator& owner, std::uint64_t count) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (owner.metrics().completed_lines < count && std::chrono::steady_clock::now() < deadline &&
           !owner.metrics().has_error) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    if (owner.metrics().has_error) throw std::runtime_error(owner.last_error());
    require(owner.metrics().completed_lines >= count, "paired line timeout");
}
void validation(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    const int contexts = hooks.created_contexts(), mutations = hooks.mutations();
    for (int variant = 0; variant < 7; ++variant) {
        auto value = config();
        if (variant == 0) value.secondary.segments.back().fixed_band.device.center_frequency_hz += 1.;
        if (variant == 1) value.secondary.segments.back().usable_stop_hz -= 1.;
        if (variant == 2) value.secondary.segments.front().fixed_band.receiver_selection = sdr_pluto::ReceiverSelection::Rx1;
        if (variant == 3) value.secondary.segments.back().fixed_band.device.source_id = "changed-later";
        if (variant == 4) value.secondary.segments.back().fixed_band.device.manual_gain_db = 19.;
        if (variant == 5) value.resource_id.clear();
        if (variant == 6) {
            value.primary.output_queue_capacity = value.secondary.output_queue_capacity = 64;
            value.primary.analysis_bins_per_usable_window = value.secondary.analysis_bins_per_usable_window = 1024;
            value.primary.display_stop_hz = value.secondary.display_stop_hz = 4'200'000'000.;
            value.primary.segments.clear(); value.secondary.segments.clear();
            auto base = config();
            for (int k = 0; k < 121; ++k) {
                auto a = base.primary.segments.front(); auto b = base.secondary.segments.front();
                const double start = 2'422'000'000. + k * 30'000'000.;
                a.fixed_band.device.center_frequency_hz = b.fixed_band.device.center_frequency_hz = start + 18'000'000.;
                a.usable_start_hz = b.usable_start_hz = start;
                a.usable_stop_hz = b.usable_stop_hz = std::min(start + 36'000'000., 4'200'000'000.);
                if (a.usable_start_hz >= 4'200'000'000.) break;
                value.primary.segments.push_back(a); value.secondary.segments.push_back(b);
            }
            sdr_pluto::validate(value.primary); sdr_pluto::validate(value.secondary);
        }
        refused([&] { owner.configure_paired(value); });
    }
    require(hooks.created_contexts() == contexts && hooks.mutations() == mutations && hooks.buffers() == 0,
            "validation performed hardware access");
    std::cout << "paired validation 7 cases preRF PASS\n";
}
void completed(Hooks& hooks, bool single) {
    const int contexts = hooks.created_contexts(), lo = hooks.lo();
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    auto profile = config(single);
    owner.configure_paired(profile);
    refused([&] { static_cast<void>(owner.poll_lines(0)); });
    owner.start(); wait_lines(owner, single ? 8 : 3);
    owner.stop();
    require(owner.state() == sdr_core::EngineState::Stopped && !owner.metrics().has_error, "paired stop failed");
    require(hooks.created_contexts() == contexts + 1 && hooks.contexts() == 1 && hooks.buffers() == 0,
            "paired owner duplicated context or leaked buffer");
    require(hooks.lo() - lo == static_cast<int>(owner.metrics().segment_reconfigurations), "more than one LO write per step");
    if (single) require(hooks.lo() - lo == 1, "single window retuned per FFT");
    auto lines = owner.poll_paired_lines(0);
    bool complete = false, cancellation = false;
    std::uint64_t epoch = 0;
    for (const auto& pair : lines) {
        require(pair.resource_id == profile.resource_id && pair.primary.epoch == pair.secondary.epoch &&
            pair.primary.line_sequence == pair.secondary.line_sequence && pair.primary.state == pair.secondary.state,
            "paired result not synchronized");
        epoch = pair.primary.epoch;
        require(pair.primary.source.source_id == "opaque-left" && pair.secondary.source.source_id == "opaque-right",
            "producer identity lost");
        if (pair.primary.state == sdr_core::SweepLineState::Gap) {
            cancellation |= std::find(pair.primary.gap_reasons.begin(), pair.primary.gap_reasons.end(),
                sdr_core::SweepLineGapReason::Cancellation) != pair.primary.gap_reasons.end();
            continue;
        }
        complete = true;
        require(pair.steps.size() == profile.primary.segments.size(), "step provenance count wrong");
        require(pair.primary.acquired_segments.size() == pair.steps.size() && pair.secondary.acquired_segments.size() == pair.steps.size(),
            "missing one chain acquisition");
        bool differs = false;
        for (std::size_t k = 0; k < pair.primary.values->size(); ++k)
            if (std::isfinite((*pair.primary.values)[k]) && std::isfinite((*pair.secondary.values)[k]) &&
                std::abs((*pair.primary.values)[k] - (*pair.secondary.values)[k]) > 1e-3f) differs = true;
        require(differs, "RX2 is duplicated RX1 data");
        for (std::size_t k = 0; k < pair.steps.size(); ++k) {
            const auto& step = pair.steps[k];
            require(step.step_index == k && step.synchronization_epoch > 0 &&
                step.config_generation == pair.primary.acquired_segments[k].config_generation &&
                step.config_generation == pair.secondary.acquired_segments[k].config_generation &&
                step.frame_sequence == pair.primary.acquired_segments[k].frame_sequence &&
                step.frame_sequence == pair.secondary.acquired_segments[k].frame_sequence &&
                step.center_frequency_hz == profile.primary.segments[k].fixed_band.device.center_frequency_hz &&
                step.sample_rate_hz == 61'440'000. && step.analog_bandwidth_hz == 56'000'000. && step.fft_size == 4096,
                "actual step receipt mismatch");
        }
    }
    require(complete && cancellation && !owner.poll_paired_progress(), "terminal pair/progress missing or stale");
    require(owner.metrics().analytical_fft_frames > 0 && owner.metrics().secondary_analytical_fft_frames > 0 &&
        owner.metrics().analytical_fft_frames == owner.metrics().secondary_analytical_fft_frames,
        "paired analytical metrics lost secondary chain");
    owner.configure_paired(profile);
    require(owner.poll_paired_lines(0).empty() && !owner.poll_paired_progress(), "rearm retained old pair");
    owner.start(); owner.request_stop(); owner.join();
    const auto rearmed = owner.poll_paired_lines(0);
    require(rearmed.size() == 1 && rearmed.front().primary.epoch > epoch, "rearm reused Sweep epoch");
    owner.disconnect(); require(hooks.contexts() == 0 && hooks.buffers() == 0, "disconnect leaked hardware");
    std::cout << "paired " << (single ? "single continuous" : "retuning") << " receipt/data/LO/rearm PASS\n";
}
void cancellation(Hooks& hooks) {
    for (int path = 0; path < 3; ++path) for (int phase = 1; phase <= 4; ++phase) {
        sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
        const auto profile = config(path == 2);
        owner.configure_paired(profile);
        const auto index = path == 1 ? 1 : 0;
        hooks.arm(phase, static_cast<long long>(profile.primary.segments[index].fixed_band.device.center_frequency_hz), 1);
        owner.start();
        const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
        while (!hooks.entered() && std::chrono::steady_clock::now() < deadline) std::this_thread::sleep_for(std::chrono::milliseconds(1));
        const bool entered = hooks.entered() != 0;
        const int buffers = hooks.created_buffers();
        const auto time = std::chrono::steady_clock::now(); owner.request_stop();
        const auto elapsed = std::chrono::steady_clock::now() - time;
        hooks.release(); owner.join();
        const auto lines = owner.poll_paired_lines(0);
        require(entered && !hooks.expired() && elapsed < std::chrono::milliseconds(100) &&
            owner.state() == sdr_core::EngineState::Stopped && !owner.metrics().has_error &&
            hooks.buffers() == 0 && lines.size() == 1 && !owner.poll_paired_progress(), "paired phase cancellation failed");
        require(lines.front().primary.state == sdr_core::SweepLineState::Gap && lines.front().secondary.state == sdr_core::SweepLineState::Gap &&
            lines.front().primary.source.metadata_json.at("receiver_selection") == "\"RX1\"" &&
            lines.front().secondary.source.metadata_json.at("receiver_selection") == "\"RX2\"" &&
            lines.front().primary.acquired_segments.size() == (path == 1 ? 1U : 0U) &&
            lines.front().secondary.acquired_segments.size() == (path == 1 ? 1U : 0U), "cancellation lost prefix");
        if (phase <= 2) require(hooks.created_buffers() == buffers, "Start occurred after Stop during RF transaction");
        owner.stop(); require(owner.poll_paired_lines(0).empty(), "Stop duplicated terminal gap");
    }
    require(hooks.contexts() == 0 && hooks.buffers() == 0, "phase matrix leaked owner");
    std::cout << "paired 12 phase cancellation cases PASS\n";
}

void progress_and_failure(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    const auto profile = config();
    owner.configure_paired(profile);
    hooks.arm(1, static_cast<long long>(profile.primary.segments[1].fixed_band.device.center_frequency_hz), 1);
    owner.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (!hooks.entered() && std::chrono::steady_clock::now() < deadline) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    const auto preview = owner.poll_paired_progress();
    owner.request_stop(); hooks.release(); owner.join();
    require(preview && preview->primary.revision == 1 && preview->secondary.revision == 1 &&
        preview->steps.size() == 1 && preview->primary.pending_segment_indices.size() == 1 &&
        preview->secondary.pending_segment_indices.size() == 1, "no progressive pair before final step");
    owner.disconnect();

    sdr_pluto::ContinuousSweepCoordinator failed("usb:mock");
    failed.configure_paired(profile);
    _putenv_s("SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ", "2470000000");
    failed.start();
    const auto timeout = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (failed.state() == sdr_core::EngineState::Running && std::chrono::steady_clock::now() < timeout)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    failed.request_stop(); failed.join();
    _putenv_s("SDR_MOCK_LIBIIO_LO_WRITE_FAIL_AT_HZ", "");
    require(failed.state() == sdr_core::EngineState::Error && failed.metrics().has_error &&
        !failed.last_error().empty() && hooks.buffers() == 0, "failed retune was hidden or retried");
    const auto lines = failed.poll_paired_lines(0);
    require(lines.size() == 1 && lines[0].primary.state == sdr_core::SweepLineState::Gap &&
        lines[0].secondary.state == sdr_core::SweepLineState::Gap &&
        lines[0].primary.acquired_segments.size() == 1 && lines[0].secondary.acquired_segments.size() == 1,
        "retune failure did not preserve aligned prefix gap");
    failed.disconnect();
    require(hooks.contexts() == 0 && hooks.buffers() == 0, "fault cleanup leaked owner");
    sdr_pluto::ContinuousSweepCoordinator worker_failed("usb:mock");
    worker_failed.configure_paired(config(true));
    _putenv_s("SDR_MOCK_LIBIIO_REFILL_FAIL", "1");
    worker_failed.start();
    const auto worker_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (worker_failed.state() == sdr_core::EngineState::Running && std::chrono::steady_clock::now() < worker_deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    worker_failed.request_stop(); worker_failed.join();
    _putenv_s("SDR_MOCK_LIBIIO_REFILL_FAIL", "");
    require(worker_failed.state() == sdr_core::EngineState::Error &&
        worker_failed.last_error().find("iio_buffer_refill failed") != std::string::npos,
        "Critical worker cause was masked by generic coordinator error");
    worker_failed.disconnect();
    require(hooks.contexts() == 0 && hooks.buffers() == 0, "worker failure leaked owner");
    std::cout << "paired progressive preview + failed retune firstcause/cleanup PASS\n";
}

void start_race_and_one_sided_failure(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    owner.configure_paired(config());
    owner.set_start_delay_for_test(80);
    const int buffers = hooks.created_buffers();
    owner.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (!owner.start_pending_for_test() && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    const bool pending = owner.start_pending_for_test();
    const auto stop_time = std::chrono::steady_clock::now(); owner.request_stop();
    const auto stop_duration = std::chrono::steady_clock::now() - stop_time;
    owner.join();
    const auto gaps = owner.poll_paired_lines(0);
    require(pending && stop_duration < std::chrono::milliseconds(100) && hooks.created_buffers() == buffers &&
        owner.state() == sdr_core::EngineState::Stopped && !owner.metrics().has_error && gaps.size() == 1 &&
        gaps[0].primary.acquired_segments.empty() && gaps[0].secondary.acquired_segments.empty(),
        "acknowledged Stop raced through native Start admission");
    owner.disconnect();

    sdr_pluto::ContinuousSweepCoordinator nan("usb:mock");
    nan.configure_paired(config()); nan.set_secondary_nan_step_for_test(1); nan.start();
    const auto nan_deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (nan.state() == sdr_core::EngineState::Running && std::chrono::steady_clock::now() < nan_deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    nan.request_stop(); nan.join();
    const auto invalid = nan.poll_paired_lines(0);
    require(nan.state() == sdr_core::EngineState::Error && !nan.last_error().empty() &&
        invalid.size() == 1 && invalid[0].primary.state == sdr_core::SweepLineState::Gap &&
        invalid[0].secondary.state == sdr_core::SweepLineState::Gap &&
        invalid[0].primary.acquired_segments.size() == 1 && invalid[0].secondary.acquired_segments.size() == 1 &&
        invalid[0].steps.size() == 1, "one-sided final-step invalid FFT suppressed paired gap or invented receipt");
    nan.disconnect(); require(hooks.contexts() == 0 && hooks.buffers() == 0, "race/NaN cleanup leaked hardware");
    std::cout << "paired check-to-Start Stop race + one-sided final-step NaN PASS\n";
}

sdr_core::SweepStatisticsConfig statistics(std::uint32_t power_bins = 8) {
    return {8, power_bins, -140., 20., 128U * 1024U * 1024U, 64};
}
void statistics_validation(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    const auto mutations = hooks.mutations(), contexts = hooks.created_contexts();
    for (int k = 0; k < 5; ++k) {
        auto p = config(); p.primary.statistics = p.secondary.statistics = statistics();
        if (k == 0) p.secondary.statistics->window_passes = 0;
        if (k == 1) p.secondary.statistics->power_max_db = -200.;
        if (k == 2) p.secondary.statistics->max_payload_bytes = 1;
        if (k == 3) p.secondary.statistics_snapshot_rate_hz = 0.;
        if (k == 4) {
            // Each publisher fits separately; the combined reservation must not.
            p.primary.statistics = p.secondary.statistics = statistics(192);
            p.primary.statistics->density_columns = p.secondary.statistics->density_columns = 0;
            constexpr std::size_t bins = 3756, slots = 21;
            const auto a = sdr_core::SweepStatisticsAccumulator::required_payload_bytes(*p.primary.statistics, bins, slots);
            const auto b = sdr_core::SweepStatisticsAccumulator::required_payload_bytes(*p.secondary.statistics, bins, slots);
            require(a < sdr_core::sweep_max_reduced_bytes && b < sdr_core::sweep_max_reduced_bytes &&
                a + b > sdr_core::sweep_max_reduced_bytes, "aggregate refusal fixture not individually fitting");
            sdr_pluto::validate(p.primary); sdr_pluto::validate(p.secondary);
        }
        refused([&] { owner.configure_paired(p); });
    }
    require(owner.state() == sdr_core::EngineState::Created && hooks.mutations() == mutations &&
        hooks.created_contexts() == contexts && hooks.buffers() == 0, "statistics refusal mutated owner/RF");
    owner.disconnect();
    std::cout << "paired statistics invalid/individual-fit combined-budget preRF PASS\n";
}
void statistics_pressure(Hooks& hooks, bool single, bool slow_cadence) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    auto p = config(single);
    p.primary.output_queue_capacity = p.secondary.output_queue_capacity = 1;
    p.primary.statistics = statistics(); p.secondary.statistics = statistics(16);
    p.primary.statistics_snapshot_rate_hz = p.secondary.statistics_snapshot_rate_hz = 1.;
    if (slow_cadence) p.primary.line_snapshot_rate_hz = p.secondary.line_snapshot_rate_hz = 1.;
    owner.configure_paired(p); owner.start(); wait_lines(owner, 20); owner.stop();
    const auto m = owner.metrics();
    const auto lines = owner.poll_paired_lines(0);
    require(lines.size() == 1 && lines[0].primary.state == sdr_core::SweepLineState::Gap,
        "statistics latestwins terminal fixture");
    require(lines[0].primary.statistics && lines[0].secondary.statistics, "both statistics snapshots missing");
    const auto& a = *lines[0].primary.statistics; const auto& b = *lines[0].secondary.statistics;
    require(a.source_id == "opaque-left" && b.source_id == "opaque-right" && a.epoch == b.epoch &&
        a.newest_pass_sequence == b.newest_pass_sequence && a.unique_passes_seen == b.unique_passes_seen &&
        a.unique_passes_seen == m.completed_lines + 1 && a.unique_passes_seen > lines.size() &&
        a.retained_passes == 8 && b.retained_passes == 8 && a.power_bins == 8 && b.power_bins == 16,
        "statistics counted UI frames or merged receivers");
    const auto count = std::accumulate(a.observations->begin(), a.observations->end(), std::uint64_t{});
    require(count > 0 && count == std::accumulate(a.histogram_counts->begin(), a.histogram_counts->end(), std::uint64_t{}) &&
        count == std::accumulate(a.density_observations->begin(), a.density_observations->end(), std::uint64_t{}),
        "statistics histogram/density conservation lost");
    bool differs = false;
    for (std::size_t k = 0; k < a.average_db->size(); ++k) {
        if ((*a.observations)[k] == 0) require(std::isnan((*a.average_db)[k]), "missing power became zero");
        if ((*a.observations)[k] && (*b.observations)[k] && std::abs((*a.average_db)[k] - (*b.average_db)[k]) > 1e-3f)
            differs = true;
    }
    require(differs, "statistics duplicated RX1 in RX2");
    if (slow_cadence) require(m.line_cadence_snapshots_suppressed > 0 &&
        m.completed_lines > m.output_queue.pushed && m.segment_reconfigurations == 1,
        "host cadence throttled analytical statistics or retuned each FFT");
    else require(m.output_snapshots_superseded > 0, "highpressure fixture did not coalesce");
    const auto old_snapshot = lines[0].primary.statistics;
    owner.configure_paired(p); owner.start(); wait_lines(owner, 1); owner.stop();
    const auto rearmed = owner.poll_paired_lines(0);
    require(rearmed.size() == 1 && rearmed[0].primary.statistics->epoch > a.epoch &&
        rearmed[0].primary.statistics->unique_passes_seen == owner.metrics().completed_lines + 1 &&
        old_snapshot->unique_passes_seen == a.unique_passes_seen, "rearm retained or mutated old statistics");
    owner.disconnect(); require(hooks.contexts() == 0 && hooks.buffers() == 0, "statistics cleanup leaked");
    std::cout << "paired statistics " << (single ? "single" : "retuning") <<
        (slow_cadence ? " slow host cadence" : " latestwins") << " PASS\n";
}
void statistics_prefix(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    auto p = config(); p.primary.statistics = statistics(); p.secondary.statistics = statistics();
    owner.configure_paired(p); owner.set_secondary_nan_step_for_test(1); owner.start();
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(4);
    while (owner.state() == sdr_core::EngineState::Running && std::chrono::steady_clock::now() < deadline)
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    owner.request_stop(); owner.join();
    const auto lines = owner.poll_paired_lines(0);
    require(owner.state() == sdr_core::EngineState::Error && lines.size() == 1 &&
        lines[0].primary.statistics && lines[0].secondary.statistics, "prefix statistics missing");
    for (const auto* frame : {&lines[0].primary, &lines[0].secondary}) {
        const auto& s = *frame->statistics;
        require(s.unique_passes_seen == 1 && s.retained_passes == 1, "partial revisions double-counted pass");
        bool observed = false, missing = false;
        for (std::size_t k = 0; k < frame->values->size(); ++k) {
            if (std::isfinite((*frame->values)[k])) { observed = true; require((*s.observations)[k] == 1, "measured prefix omitted"); }
            else { missing = true; require((*s.observations)[k] == 0 && std::isnan((*s.average_db)[k]), "missing tail contributed power"); }
        }
        require(observed && missing, "prefix gap fixture not partial");
    }
    owner.disconnect(); require(hooks.contexts() == 0 && hooks.buffers() == 0, "prefix stats cleanup leaked");
    // Independent optional settings: no unwanted primary publisher.
    p.primary.statistics.reset();
    sdr_pluto::ContinuousSweepCoordinator rx2("usb:mock");
    rx2.configure_paired(p); rx2.start(); wait_lines(rx2, 2); rx2.stop();
    const auto optional = rx2.poll_paired_lines(0);
    require(!optional.empty() && !optional.back().primary.statistics && optional.back().secondary.statistics,
        "optional RX2-only stats produced third primary publisher");
    rx2.disconnect();
    std::cout << "paired statistics partial NaN/optional RX2-only PASS\n";
}
void statistics_shared_gap(Hooks& hooks) {
    sdr_pluto::ContinuousSweepCoordinator owner("usb:mock");
    auto p = config(true); p.primary.statistics = p.secondary.statistics = statistics();
    p.primary.segments[0].fixed_band.acquisition_queue_capacity =
        p.secondary.segments[0].fixed_band.acquisition_queue_capacity = 1;
    owner.configure_paired(p); owner.set_dsp_delay_for_test(80); owner.start();
    bool saw_gap = false;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (!saw_gap && std::chrono::steady_clock::now() < deadline && !owner.metrics().has_error) {
        for (const auto& line : owner.poll_paired_lines(0))
            if (!line.steps.empty() && line.steps[0].shared_input_gaps_before >= 2) saw_gap = true;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    owner.set_dsp_delay_for_test(0);
    const auto before = owner.metrics().completed_lines; wait_lines(owner, before + 8); owner.stop();
    const auto lines = owner.poll_paired_lines(0);
    require(saw_gap && !owner.metrics().has_error && !lines.empty(), "shared gap injection unavailable");
    require(lines.back().primary.statistics && lines.back().secondary.statistics, "shared gap statistics snapshots missing");
    const auto& a = *lines.back().primary.statistics; const auto& b = *lines.back().secondary.statistics;
    // Sequence starts at 1: without reset unique == newest, not newest + 1.
    // Require a strictly smaller count so an omitted reset cannot pass.
    require(a.unique_passes_seen == b.unique_passes_seen && a.unique_passes_seen < a.newest_pass_sequence &&
        a.newest_pass_sequence == b.newest_pass_sequence, "shared gap failed to reset both rolling kernels");
    owner.disconnect(); require(hooks.contexts() == 0 && hooks.buffers() == 0, "shared stats gap cleanup leaked");
    std::cout << "paired statistics shared synchronization gap reset PASS\n";
}
}
int main() {
    try { Hooks hooks; validation(hooks); completed(hooks, false); completed(hooks, true); cancellation(hooks); progress_and_failure(hooks); start_race_and_one_sided_failure(hooks);
        statistics_validation(hooks); statistics_pressure(hooks, false, false); statistics_pressure(hooks, true, false);
        statistics_pressure(hooks, true, true); statistics_prefix(hooks); statistics_shared_gap(hooks); }
    catch (const std::exception& error) { std::cerr << error.what() << '\n'; return 1; }
    return 0;
}
