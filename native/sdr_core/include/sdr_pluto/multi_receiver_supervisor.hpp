#pragma once

#include "sdr_pluto/fixed_band_engine.hpp"

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

namespace sdr_pluto {

// An explicit admission reservation, not a measurement or a promised device
// throughput.  E3 sums these finite values before opening any coordinator so
// a future multi-pane UI cannot create an unbounded work backlog by accident.
struct ReceiverResourceBudget {
    std::uint64_t native_memory_reservation_bytes{};
    double transport_reservation_samples_per_second{};
    double publication_reservation_frames_per_second{};
};

// Exactly one independent physical IIO stream owned by this supervisor.  The
// opaque resource id is the lease key; context_uri is only the route used by
// the current Pluto coordinator and must not be used as a hardware identity.
struct ReceiverResourcePlan {
    std::string physical_stream_resource_id;
    std::string context_uri;
    FixedBandConfig fixed_band;
    ReceiverResourceBudget reservation;
};

// E3 deliberately admits only a bounded CPU fixed-band profile.  Recording,
// continuous Sweep, CUDA and pane scheduling have their own later packages.
struct MultiReceiverSupervisorConfig {
    std::vector<ReceiverResourcePlan> resources;
    std::uint32_t maximum_resources{4U};
    ReceiverResourceBudget global_budget;
};

void validate(const ReceiverResourceBudget& value);
void validate(const ReceiverResourcePlan& value);
void validate(const MultiReceiverSupervisorConfig& value);

struct MultiReceiverResourceMetrics {
    std::string physical_stream_resource_id;
    sdr_core::EngineState state{sdr_core::EngineState::Created};
    bool has_error{};
    ReceiverResourceBudget reservation;
    FixedBandMetrics fixed_band;
};

struct MultiReceiverSupervisorMetrics {
    // This is the supervisor lifecycle, not an aggregate of receiver errors.
    // A failed receiver remains visible in resources_in_error while healthy
    // independent resources continue to run.
    sdr_core::EngineState state{sdr_core::EngineState::Created};
    bool has_global_error{};
    std::uint32_t resources_in_error{};
    ReceiverResourceBudget reserved;
    std::vector<MultiReceiverResourceMetrics> resources;
};

// Narrow native coordinator seam.  Production adapts one FixedBandEngine per
// resource; deterministic software tests use a fake implementation.  It
// carries only reduced-frame/lifecycle control and never exposes raw I/Q.
class ReceiverCoordinator {
public:
    virtual ~ReceiverCoordinator() = default;

    virtual void configure(const FixedBandConfig& config) = 0;
    virtual void start() = 0;
    virtual void request_stop() = 0;
    virtual void join() = 0;
    virtual void disconnect() noexcept = 0;
    [[nodiscard]] virtual sdr_core::EngineState state() const noexcept = 0;
    [[nodiscard]] virtual FixedBandMetrics metrics() const = 0;
};

class ReceiverCoordinatorFactory {
public:
    virtual ~ReceiverCoordinatorFactory() = default;

    [[nodiscard]] virtual std::unique_ptr<ReceiverCoordinator> create(
        const std::string& context_uri
    ) = 0;
};

// A process-local, resource-keyed lease owner.  There is one native
// coordinator for each admitted independent resource.  The class has no UI,
// no per-frame Python callback and no scheduler; those boundaries belong to
// E4/E5.  Its shutdown requests every stop before joining any receiver so no
// coordinator thread is left detached when one peer is slow or failed.
class MultiReceiverSupervisor final {
public:
    explicit MultiReceiverSupervisor(
        std::shared_ptr<ReceiverCoordinatorFactory> coordinator_factory = {}
    );
    ~MultiReceiverSupervisor() noexcept;

    MultiReceiverSupervisor(const MultiReceiverSupervisor&) = delete;
    MultiReceiverSupervisor& operator=(const MultiReceiverSupervisor&) = delete;
    MultiReceiverSupervisor(MultiReceiverSupervisor&&) = delete;
    MultiReceiverSupervisor& operator=(MultiReceiverSupervisor&&) = delete;

    void configure(MultiReceiverSupervisorConfig config);
    void start();
    // Stops only the requested resource.  A later full configuration is
    // required before that lease may be reused; E3 has no dynamic scheduler.
    void stop_resource(const std::string& physical_stream_resource_id);
    void request_stop();
    void join();
    void stop();
    void disconnect() noexcept;

    [[nodiscard]] sdr_core::EngineState state() const noexcept;
    [[nodiscard]] MultiReceiverSupervisorMetrics metrics() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace sdr_pluto
