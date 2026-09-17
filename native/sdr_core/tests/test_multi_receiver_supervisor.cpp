#include "sdr_pluto/multi_receiver_supervisor.hpp"

#include "sdr_core/errors.hpp"

#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace {

struct Trace {
    std::vector<std::string> calls;
};

class FakeCoordinator final : public sdr_pluto::ReceiverCoordinator {
public:
    FakeCoordinator(
        std::string context_uri,
        std::shared_ptr<Trace> trace,
        const bool fail_start,
        const bool fail_configure,
        const bool fail_request_stop
    ) : context_uri_(std::move(context_uri)),
        trace_(std::move(trace)),
        fail_start_(fail_start),
        fail_configure_(fail_configure),
        fail_request_stop_(fail_request_stop) {}

    void configure(const sdr_pluto::FixedBandConfig&) override {
        trace_->calls.push_back(context_uri_ + ":configure");
        if (fail_configure_) throw sdr_core::DeviceError("injected configure failure");
        state_ = sdr_core::EngineState::Configured;
    }
    void start() override {
        trace_->calls.push_back(context_uri_ + ":start");
        if (fail_start_) throw sdr_core::DeviceError("injected start failure");
        state_ = sdr_core::EngineState::Running;
    }
    void request_stop() override {
        trace_->calls.push_back(context_uri_ + ":request_stop");
        if (fail_request_stop_) throw sdr_core::DeviceError("injected stop failure");
        if (state_ == sdr_core::EngineState::Running) state_ = sdr_core::EngineState::Stopping;
    }
    void join() override {
        trace_->calls.push_back(context_uri_ + ":join");
        if (state_ == sdr_core::EngineState::Stopping ||
            state_ == sdr_core::EngineState::Running) state_ = sdr_core::EngineState::Stopped;
    }
    void disconnect() noexcept override {
        trace_->calls.push_back(context_uri_ + ":disconnect");
        if (state_ != sdr_core::EngineState::Error) state_ = sdr_core::EngineState::Stopped;
    }
    [[nodiscard]] sdr_core::EngineState state() const noexcept override { return state_; }
    [[nodiscard]] sdr_pluto::FixedBandMetrics metrics() const override {
        return {
            .state = state_,
            .has_error = state_ == sdr_core::EngineState::Error,
        };
    }
    void fail_runtime() noexcept { state_ = sdr_core::EngineState::Error; }

private:
    std::string context_uri_;
    std::shared_ptr<Trace> trace_;
    bool fail_start_{};
    bool fail_configure_{};
    bool fail_request_stop_{};
    sdr_core::EngineState state_{sdr_core::EngineState::Created};
};

class FakeFactory final : public sdr_pluto::ReceiverCoordinatorFactory {
public:
    explicit FakeFactory(std::shared_ptr<Trace> trace) : trace_(std::move(trace)) {}

    [[nodiscard]] std::unique_ptr<sdr_pluto::ReceiverCoordinator> create(
        const std::string& context_uri
    ) override {
        ++created;
        const bool fail_start = context_uri == "mock:fail-start";
        const bool fail_configure = context_uri == "mock:fail-configure";
        const bool fail_request_stop = context_uri == "mock:fail-stop";
        auto result = std::make_unique<FakeCoordinator>(
            context_uri, trace_, fail_start, fail_configure, fail_request_stop
        );
        coordinators[context_uri] = result.get();
        return result;
    }

    std::shared_ptr<Trace> trace_;
    std::uint32_t created{};
    std::unordered_map<std::string, FakeCoordinator*> coordinators;
};

[[nodiscard]] sdr_pluto::ReceiverResourcePlan plan(
    const std::string& resource_id,
    const std::string& context_uri,
    const double sample_rate_hz = 3'000'000.0
) {
    sdr_pluto::FixedBandConfig fixed_band;
    fixed_band.device = {
        .source_id = resource_id + "-source",
        .context_uri = context_uri,
        .center_frequency_hz = 433'920'000.0,
        .sample_rate_hz = sample_rate_hz,
        .analog_bandwidth_hz = 1'500'000.0,
        .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = 20.0,
        .channel_index = 0U,
        .buffer_samples = 4096U,
    };
    fixed_band.dsp = {
        .fft_size = 1024U,
        .hop_size = 512U,
        .window = sdr_core::WindowType::Hann,
        .detector = sdr_core::DetectorType::Sample,
        .unit = sdr_core::SpectrumUnit::DbfsBin,
        .precision_mode = sdr_core::PrecisionMode::AccurateF32F64Accum,
    };
    fixed_band.backend = sdr_core::ComputeBackendKind::Cpu;
    fixed_band.allow_runtime_fallback = false;
    fixed_band.snapshot_rate_hz = 60.0;
    return {
        .physical_stream_resource_id = resource_id,
        .context_uri = context_uri,
        .fixed_band = std::move(fixed_band),
        .reservation = {
            .native_memory_reservation_bytes = 1U << 20U,
            .transport_reservation_samples_per_second = sample_rate_hz,
            .publication_reservation_frames_per_second = 60.0,
        },
    };
}

[[nodiscard]] sdr_pluto::MultiReceiverSupervisorConfig two_resource_config(
    const std::string& second_uri = "mock:second"
) {
    return {
        .resources = {plan("physical-first", "mock:first"), plan("physical-second", second_uri)},
        .maximum_resources = 4U,
        .global_budget = {
            .native_memory_reservation_bytes = 3U << 20U,
            .transport_reservation_samples_per_second = 7'000'000.0,
            .publication_reservation_frames_per_second = 180.0,
        },
    };
}

[[nodiscard]] bool throws_configuration(const auto&& callback) {
    try {
        callback();
    } catch (const sdr_core::ConfigurationError&) {
        return true;
    }
    return false;
}

[[nodiscard]] bool throws_native(const auto&& callback) {
    try {
        callback();
    } catch (const sdr_core::SdrNativeError&) {
        return true;
    }
    return false;
}

[[nodiscard]] bool saw(const Trace& trace, const std::string& call) {
    for (const auto& item : trace.calls) {
        if (item == call) return true;
    }
    return false;
}

}  // namespace

int main() {
    try {
        // Admission rejects duplicate physical ownership before a factory may
        // create/open a coordinator.  URI duplication is a second defense;
        // URI remains a route, not the stable lease identity.
        auto validation_trace = std::make_shared<Trace>();
        auto validation_factory = std::make_shared<FakeFactory>(validation_trace);
        sdr_pluto::MultiReceiverSupervisor validation_supervisor(validation_factory);
        auto duplicate = two_resource_config();
        duplicate.resources[1].physical_stream_resource_id = "physical-first";
        if (!throws_configuration([&] { validation_supervisor.configure(duplicate); }) ||
            validation_factory->created != 0U) return 1;
        auto exceeded = two_resource_config();
        exceeded.global_budget.transport_reservation_samples_per_second = 5'000'000.0;
        if (!throws_configuration([&] { validation_supervisor.configure(exceeded); }) ||
            validation_factory->created != 0U) return 2;
        auto forbidden = plan("physical-forbidden", "mock:forbidden");
        forbidden.fixed_band.recorder_enabled = true;
        if (!throws_configuration([&] { sdr_pluto::validate(forbidden); })) return 3;
        // If configure has already opened an adapter resource before a
        // readback error, the entry is still owned by common rollback.
        auto configure_trace = std::make_shared<Trace>();
        auto configure_factory = std::make_shared<FakeFactory>(configure_trace);
        sdr_pluto::MultiReceiverSupervisor configure_rollback(configure_factory);
        if (!throws_native([&] {
                configure_rollback.configure(two_resource_config("mock:fail-configure"));
            }) ||
            !saw(*configure_trace, "mock:fail-configure:request_stop") ||
            !saw(*configure_trace, "mock:fail-configure:join") ||
            !saw(*configure_trace, "mock:fail-configure:disconnect")) return 4;

        // One coordinator per independent resource starts in parallel at the
        // engine level.  A runtime error observed on one does not request a
        // stop on its peer; only an explicit global stop does that.
        auto trace = std::make_shared<Trace>();
        auto factory = std::make_shared<FakeFactory>(trace);
        sdr_pluto::MultiReceiverSupervisor supervisor(factory);
        supervisor.configure(two_resource_config());
        const auto configured = supervisor.metrics();
        if (configured.state != sdr_core::EngineState::Configured ||
            configured.resources.size() != 2U ||
            configured.reserved.native_memory_reservation_bytes != (2U << 20U) ||
            factory->created != 2U) return 5;
        supervisor.start();
        factory->coordinators.at("mock:first")->fail_runtime();
        const auto isolated = supervisor.metrics();
        if (isolated.state != sdr_core::EngineState::Running ||
            isolated.has_global_error || isolated.resources_in_error != 1U ||
            factory->coordinators.at("mock:second")->state() != sdr_core::EngineState::Running) return 6;
        supervisor.stop_resource("physical-first");
        if (saw(*trace, "mock:second:request_stop")) return 7;
        supervisor.stop();
        if (supervisor.state() != sdr_core::EngineState::Stopped ||
            !saw(*trace, "mock:second:request_stop") ||
            !saw(*trace, "mock:second:join") || !saw(*trace, "mock:second:disconnect")) return 8;

        // A local stop failure cannot skip join/disconnect for its own
        // coordinator or request a peer stop.  This is the no-detached-thread
        // rule for the affected resource, not an all-resource shutdown.
        auto local_failure_trace = std::make_shared<Trace>();
        auto local_failure_factory = std::make_shared<FakeFactory>(local_failure_trace);
        sdr_pluto::MultiReceiverSupervisor local_failure(local_failure_factory);
        local_failure.configure(two_resource_config("mock:fail-stop"));
        local_failure.start();
        bool local_stop_failed = false;
        try {
            local_failure.stop_resource("physical-second");
        } catch (const sdr_core::SdrNativeError&) {
            local_stop_failed = true;
        }
        if (!local_stop_failed || !saw(*local_failure_trace, "mock:fail-stop:join") ||
            !saw(*local_failure_trace, "mock:fail-stop:disconnect") ||
            saw(*local_failure_trace, "mock:first:request_stop")) return 9;
        local_failure.stop();

        // A failure during the all-or-nothing start transaction stops, joins
        // and disconnects every participant.  It must not leave a first
        // resource running merely because a later one refused to start.
        auto rollback_trace = std::make_shared<Trace>();
        auto rollback_factory = std::make_shared<FakeFactory>(rollback_trace);
        sdr_pluto::MultiReceiverSupervisor rollback(rollback_factory);
        rollback.configure(two_resource_config("mock:fail-start"));
        bool start_failed = false;
        try {
            rollback.start();
        } catch (const sdr_core::SdrNativeError&) {
            start_failed = true;
        }
        if (!start_failed || rollback.state() != sdr_core::EngineState::Error ||
            !saw(*rollback_trace, "mock:first:request_stop") ||
            !saw(*rollback_trace, "mock:first:join") ||
            !saw(*rollback_trace, "mock:first:disconnect") ||
            !saw(*rollback_trace, "mock:fail-start:disconnect")) return 10;
        rollback.stop();
        if (rollback.state() != sdr_core::EngineState::Stopped) return 11;

        std::cout << "R10-E3 multi-receiver supervisor PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 12;
    }
}
