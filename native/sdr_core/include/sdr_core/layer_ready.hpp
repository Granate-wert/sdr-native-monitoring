#pragma once

#include "sdr_core/analytical_ready.hpp"

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <memory>
#include <optional>
#include <vector>

namespace sdr_core {

enum class LayerReadyKind : std::uint8_t { SweepProgress, SweepTerminal, Density };

// Process/library-local creation evidence, NOT RF time, queue handoff or paint.
// Exact segment coverage/source live in the enclosing immutable Sweep frame;
// they must be checked together, never inferred from a sequence or ring event.
// Unused variant fields are zero, NOT an unknown global Sweep generation.
struct LayerReadyRef {
    LayerReadyKind kind{LayerReadyKind::SweepProgress};
    std::uint64_t producer_instance_id{}, creation_sequence{};
    std::int64_t ready_native_ns{};
    AnalyticalReadyClock clock{AnalyticalReadyClock::NativeSteady};
    AnalyticalReadyClockState clock_state{AnalyticalReadyClockState::Monotonic};
    std::uint64_t sweep_epoch{}, line_sequence{}, revision{};
    std::uint64_t config_generation{}, update_sequence{}, source_frame_sequence{};
    std::uint64_t accumulation_sequence{};
    [[nodiscard]] bool operator==(const LayerReadyRef&) const noexcept = default;
};

struct LayerReadySummary {
    std::uint64_t producer_instance_id{}, created{}, clock_regressions{};
    std::uint64_t event_capacity{}, events_pending{}, events_drained{}, events_lost{};
    std::uint64_t first_lost_creation_sequence{}, last_lost_creation_sequence{};
};
struct LayerReadyDrain {
    std::vector<LayerReadyRef> creations;
    LayerReadySummary summary;
};

// Optional producer-only creation journal. Owner queue dispositions and pane
// obligations are NOT covered. Owners must admit aggregate ring/drain/receipt
// storage before supplying one to a producer; default producers have none.
// Full rings lose evidence explicitly, never wait for Python/UI consumption.
class LayerReadyJournal final {
public:
    using Clock = AnalyticalReadyJournal::Clock;
    static constexpr std::size_t max_capacity = 4096U;
    explicit LayerReadyJournal(std::size_t capacity, Clock clock = nullptr);
    [[nodiscard]] LayerReadyRef sweep(LayerReadyKind kind, std::uint64_t epoch,
        std::uint64_t sequence, std::uint64_t revision = 0U);
    [[nodiscard]] LayerReadyRef density(std::uint64_t generation, std::uint64_t update,
        std::uint64_t source_frame, std::uint64_t accumulation);
    [[nodiscard]] LayerReadySummary summary() const noexcept;
    [[nodiscard]] LayerReadyDrain drain(std::size_t max_items);
    [[nodiscard]] static std::uint64_t reserved_bytes(std::size_t capacity);
private:
    [[nodiscard]] LayerReadyRef create(LayerReadyRef ref);
    Clock clock_;
    std::vector<LayerReadyRef> ring_;
    std::size_t head_{}, pending_{};
    LayerReadySummary summary_;
    std::int64_t last_clock_{};
    bool clock_seen_{}, regressed_{};
    mutable std::mutex mutex_;
    std::mutex drain_mutex_;
};

// Cross-language conservative charge for five density snapshot refs plus
// accumulator pointer/identity. Not a journal/ring reservation or an RSS cap.
inline constexpr std::uint64_t density_layer_scalar_reservation_bytes = 1024U;
static_assert(5U * sizeof(std::optional<LayerReadyRef>) +
    sizeof(std::shared_ptr<LayerReadyJournal>) + sizeof(std::uint64_t) <=
    density_layer_scalar_reservation_bytes);

}  // namespace sdr_core
