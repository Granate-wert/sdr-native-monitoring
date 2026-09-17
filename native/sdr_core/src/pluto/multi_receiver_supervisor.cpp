#include "sdr_pluto/multi_receiver_supervisor.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <exception>
#include <limits>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <unordered_set>
#include <utility>

namespace sdr_pluto {

namespace {

[[noreturn]] void invalid(const std::string& message) {
    throw sdr_core::ConfigurationError(message);
}

[[nodiscard]] bool nonblank(const std::string& value) {
    return !value.empty() &&
        std::any_of(value.begin(), value.end(), [](const unsigned char value) {
            return !std::isspace(value);
        });
}

[[nodiscard]] bool positive_finite(const double value) {
    return std::isfinite(value) && value > 0.0;
}

[[nodiscard]] ReceiverResourceBudget sum_reservations(
    const std::vector<ReceiverResourcePlan>& resources
) {
    ReceiverResourceBudget result;
    for (const auto& resource : resources) {
        if (result.native_memory_reservation_bytes >
            std::numeric_limits<std::uint64_t>::max() -
                resource.reservation.native_memory_reservation_bytes) {
            invalid("receiver native-memory reservations overflow");
        }
        result.native_memory_reservation_bytes +=
            resource.reservation.native_memory_reservation_bytes;
        result.transport_reservation_samples_per_second +=
            resource.reservation.transport_reservation_samples_per_second;
        result.publication_reservation_frames_per_second +=
            resource.reservation.publication_reservation_frames_per_second;
    }
    if (!positive_finite(result.transport_reservation_samples_per_second) ||
        !positive_finite(result.publication_reservation_frames_per_second)) {
        invalid("receiver reservation totals must remain finite and positive");
    }
    return result;
}

void shutdown_coordinators_noexcept(
    std::vector<std::unique_ptr<ReceiverCoordinator>>& coordinators
) noexcept {
    // The three phases preserve global bounded shutdown: first signal every
    // worker, then join every worker, then release every physical context.
    for (const auto& coordinator : coordinators) {
        try {
            coordinator->request_stop();
        } catch (...) {
        }
    }
    for (const auto& coordinator : coordinators) {
        try {
            coordinator->join();
        } catch (...) {
        }
    }
    for (const auto& coordinator : coordinators) {
        coordinator->disconnect();
    }
}

class FixedBandReceiverCoordinator final : public ReceiverCoordinator {
public:
    explicit FixedBandReceiverCoordinator(const std::string& context_uri)
        : engine_(context_uri) {}

    void configure(const FixedBandConfig& config) override {
        static_cast<void>(engine_.configure(config));
    }
    void start() override { engine_.start(); }
    void request_stop() override { engine_.request_stop(); }
    void join() override { engine_.join(); }
    void disconnect() noexcept override { engine_.disconnect(); }
    [[nodiscard]] sdr_core::EngineState state() const noexcept override {
        return engine_.state();
    }
    [[nodiscard]] FixedBandMetrics metrics() const override {
        return engine_.metrics();
    }

private:
    FixedBandEngine engine_;
};

class FixedBandReceiverCoordinatorFactory final : public ReceiverCoordinatorFactory {
public:
    [[nodiscard]] std::unique_ptr<ReceiverCoordinator> create(
        const std::string& context_uri
    ) override {
        return std::make_unique<FixedBandReceiverCoordinator>(context_uri);
    }
};

[[nodiscard]] std::shared_ptr<ReceiverCoordinatorFactory> default_factory() {
    return std::make_shared<FixedBandReceiverCoordinatorFactory>();
}

}  // namespace

void validate(const ReceiverResourceBudget& value) {
    if (value.native_memory_reservation_bytes == 0U ||
        !positive_finite(value.transport_reservation_samples_per_second) ||
        !positive_finite(value.publication_reservation_frames_per_second)) {
        invalid("receiver resource reservation must contain finite positive memory, transport and publication budgets");
    }
}

void validate(const ReceiverResourcePlan& value) {
    if (!nonblank(value.physical_stream_resource_id) || !nonblank(value.context_uri)) {
        invalid("receiver resource id and context uri must not be blank");
    }
    validate(value.fixed_band);
    validate(value.reservation);
    if (value.fixed_band.device.context_uri != value.context_uri) {
        invalid("receiver plan context uri must match fixed-band device context uri");
    }
    if (value.fixed_band.backend != sdr_core::ComputeBackendKind::Cpu ||
        value.fixed_band.allow_runtime_fallback) {
        invalid("R10-E3 admits explicit CPU fixed-band profiles without runtime fallback only");
    }
    if (value.fixed_band.persistence.enabled || value.fixed_band.recorder_enabled ||
        value.fixed_band.recording.enabled || value.fixed_band.recording.record_iq ||
        value.fixed_band.recording.record_spectrum) {
        invalid("R10-E3 multi-resource supervision does not admit recording or persistence");
    }
    if (value.fixed_band.continuous_sweep_line.has_value() &&
        value.fixed_band.continuous_sweep_line->enabled) {
        invalid("R10-E3 multi-resource supervision does not admit continuous Sweep");
    }
    if (value.reservation.transport_reservation_samples_per_second <
        value.fixed_band.device.sample_rate_hz ||
        value.reservation.publication_reservation_frames_per_second <
        value.fixed_band.snapshot_rate_hz) {
        invalid("receiver resource reservation cannot be below its fixed-band requested transport or publication rate");
    }
}

void validate(const MultiReceiverSupervisorConfig& value) {
    if (value.maximum_resources == 0U || value.maximum_resources > 4U ||
        value.resources.empty() || value.resources.size() > value.maximum_resources) {
        invalid("R10-E3 supports 1..4 admitted receiver resources within the configured bound");
    }
    validate(value.global_budget);
    std::unordered_set<std::string> resource_ids;
    std::unordered_set<std::string> context_uris;
    for (const auto& resource : value.resources) {
        validate(resource);
        if (!resource_ids.insert(resource.physical_stream_resource_id).second) {
            invalid("a physical stream resource may have one E3 lease only");
        }
        if (!context_uris.insert(resource.context_uri).second) {
            invalid("one context uri may have one E3 coordinator only");
        }
    }
    const auto total = sum_reservations(value.resources);
    if (total.native_memory_reservation_bytes > value.global_budget.native_memory_reservation_bytes ||
        total.transport_reservation_samples_per_second >
            value.global_budget.transport_reservation_samples_per_second ||
        total.publication_reservation_frames_per_second >
            value.global_budget.publication_reservation_frames_per_second) {
        invalid("receiver resource reservations exceed the declared global budget");
    }
}

class MultiReceiverSupervisor::Impl final {
public:
    explicit Impl(std::shared_ptr<ReceiverCoordinatorFactory> coordinator_factory)
        : coordinator_factory_(coordinator_factory ? std::move(coordinator_factory) : default_factory()) {}

    ~Impl() noexcept {
        shutdown_locked_noexcept();
    }

    void configure(MultiReceiverSupervisorConfig config) {
        validate(config);
        std::lock_guard lock(mutex_);
        if (state_ == sdr_core::EngineState::Running ||
            state_ == sdr_core::EngineState::Stopping) {
            invalid("cannot reconfigure multi-receiver supervisor while it is running");
        }
        shutdown_locked_noexcept();

        std::vector<Entry> configured;
        configured.reserve(config.resources.size());
        try {
            for (const auto& resource : config.resources) {
                auto coordinator = coordinator_factory_->create(resource.context_uri);
                if (!coordinator) {
                    invalid("receiver coordinator factory returned null");
                }
                configured.push_back(Entry{resource, std::move(coordinator)});
                // Retain ownership before configure: a coordinator can have
                // opened an adapter context before reporting a readback error,
                // so the common rollback must signal/join/disconnect it too.
                configured.back().coordinator->configure(resource.fixed_band);
            }
        } catch (...) {
            std::vector<std::unique_ptr<ReceiverCoordinator>> coordinators;
            coordinators.reserve(configured.size());
            for (auto& entry : configured) {
                coordinators.push_back(std::move(entry.coordinator));
            }
            shutdown_coordinators_noexcept(coordinators);
            state_ = sdr_core::EngineState::Error;
            has_global_error_ = true;
            throw;
        }
        entries_ = std::move(configured);
        reserved_ = sum_reservations(config.resources);
        config_ = std::move(config);
        state_ = sdr_core::EngineState::Configured;
        has_global_error_ = false;
    }

    void start() {
        std::lock_guard lock(mutex_);
        if (state_ != sdr_core::EngineState::Configured) {
            invalid("multi-receiver supervisor must be configured before start");
        }
        try {
            for (auto& entry : entries_) {
                entry.coordinator->start();
            }
        } catch (...) {
            shutdown_locked_noexcept();
            state_ = sdr_core::EngineState::Error;
            has_global_error_ = true;
            throw;
        }
        state_ = sdr_core::EngineState::Running;
    }

    void stop_resource(const std::string& physical_stream_resource_id) {
        std::lock_guard lock(mutex_);
        if (state_ != sdr_core::EngineState::Running) {
            invalid("multi-receiver supervisor must be running before a resource can stop");
        }
        const auto found = std::find_if(entries_.begin(), entries_.end(),
            [&physical_stream_resource_id](const Entry& entry) {
                return entry.plan.physical_stream_resource_id == physical_stream_resource_id;
            });
        if (found == entries_.end()) {
            invalid("requested physical stream resource is not leased by this supervisor");
        }
        std::exception_ptr failure;
        try {
            found->coordinator->request_stop();
        } catch (...) {
            failure = std::current_exception();
        }
        try {
            found->coordinator->join();
        } catch (...) {
            if (!failure) failure = std::current_exception();
        }
        found->coordinator->disconnect();
        if (failure) {
            // The local coordinator is fully torn down before its lifecycle
            // error reaches the caller; healthy peers retain their leases.
            has_global_error_ = true;
            std::rethrow_exception(failure);
        }
    }

    void request_stop() {
        std::lock_guard lock(mutex_);
        if (entries_.empty()) return;
        if (state_ == sdr_core::EngineState::Running) {
            state_ = sdr_core::EngineState::Stopping;
        }
        std::exception_ptr failure;
        for (auto& entry : entries_) {
            try {
                entry.coordinator->request_stop();
            } catch (...) {
                if (!failure) failure = std::current_exception();
            }
        }
        if (failure) {
            has_global_error_ = true;
            std::rethrow_exception(failure);
        }
    }

    void join() {
        std::lock_guard lock(mutex_);
        std::exception_ptr failure;
        for (auto& entry : entries_) {
            try {
                entry.coordinator->join();
            } catch (...) {
                if (!failure) failure = std::current_exception();
            }
        }
        if (failure) {
            has_global_error_ = true;
            state_ = sdr_core::EngineState::Error;
            std::rethrow_exception(failure);
        }
        if (state_ == sdr_core::EngineState::Stopping) {
            state_ = sdr_core::EngineState::Stopped;
        }
    }

    void stop() {
        std::lock_guard lock(mutex_);
        shutdown_locked_noexcept();
        if (!entries_.empty() || state_ != sdr_core::EngineState::Created) {
            state_ = sdr_core::EngineState::Stopped;
        }
    }

    void disconnect() noexcept {
        std::lock_guard lock(mutex_);
        shutdown_locked_noexcept();
        if (!entries_.empty() || state_ != sdr_core::EngineState::Created) {
            state_ = sdr_core::EngineState::Stopped;
        }
    }

    [[nodiscard]] sdr_core::EngineState state() const noexcept {
        std::lock_guard lock(mutex_);
        return state_;
    }

    [[nodiscard]] MultiReceiverSupervisorMetrics metrics() const {
        std::lock_guard lock(mutex_);
        MultiReceiverSupervisorMetrics result;
        result.state = state_;
        result.has_global_error = has_global_error_;
        result.reserved = reserved_;
        result.resources.reserve(entries_.size());
        for (const auto& entry : entries_) {
            auto fixed_band = entry.coordinator->metrics();
            const auto coordinator_state = entry.coordinator->state();
            const bool resource_error = fixed_band.has_error ||
                fixed_band.state == sdr_core::EngineState::Error ||
                coordinator_state == sdr_core::EngineState::Error;
            result.resources_in_error += resource_error ? 1U : 0U;
            result.resources.push_back(MultiReceiverResourceMetrics{
                .physical_stream_resource_id = entry.plan.physical_stream_resource_id,
                .state = coordinator_state,
                .has_error = resource_error,
                .reservation = entry.plan.reservation,
                .fixed_band = std::move(fixed_band),
            });
        }
        return result;
    }

private:
    struct Entry {
        ReceiverResourcePlan plan;
        std::unique_ptr<ReceiverCoordinator> coordinator;
    };

    void shutdown_locked_noexcept() noexcept {
        std::vector<std::unique_ptr<ReceiverCoordinator>> coordinators;
        coordinators.reserve(entries_.size());
        for (auto& entry : entries_) {
            coordinators.push_back(std::move(entry.coordinator));
        }
        shutdown_coordinators_noexcept(coordinators);
        entries_.clear();
        config_.reset();
        reserved_ = {};
    }

    std::shared_ptr<ReceiverCoordinatorFactory> coordinator_factory_;
    mutable std::mutex mutex_;
    std::optional<MultiReceiverSupervisorConfig> config_;
    std::vector<Entry> entries_;
    ReceiverResourceBudget reserved_;
    sdr_core::EngineState state_{sdr_core::EngineState::Created};
    bool has_global_error_{};
};

MultiReceiverSupervisor::MultiReceiverSupervisor(
    std::shared_ptr<ReceiverCoordinatorFactory> coordinator_factory
) : impl_(std::make_unique<Impl>(std::move(coordinator_factory))) {}

MultiReceiverSupervisor::~MultiReceiverSupervisor() noexcept = default;
void MultiReceiverSupervisor::configure(MultiReceiverSupervisorConfig config) {
    impl_->configure(std::move(config));
}
void MultiReceiverSupervisor::start() { impl_->start(); }
void MultiReceiverSupervisor::stop_resource(const std::string& physical_stream_resource_id) {
    impl_->stop_resource(physical_stream_resource_id);
}
void MultiReceiverSupervisor::request_stop() { impl_->request_stop(); }
void MultiReceiverSupervisor::join() { impl_->join(); }
void MultiReceiverSupervisor::stop() { impl_->stop(); }
void MultiReceiverSupervisor::disconnect() noexcept { impl_->disconnect(); }
sdr_core::EngineState MultiReceiverSupervisor::state() const noexcept { return impl_->state(); }
MultiReceiverSupervisorMetrics MultiReceiverSupervisor::metrics() const {
    return impl_->metrics();
}

}  // namespace sdr_pluto
