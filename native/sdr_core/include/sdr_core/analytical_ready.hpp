#pragma once

#include <cstddef>
#include <cstdint>
#include <mutex>
#include <vector>

namespace sdr_core {

// A producer receipt, not an RF timestamp or a Qt paint receipt. IDs are
// process/library-local: consumers MUST also bind the native process/session,
// resource, receiver, run and activation. Never persist this as replay timing.
enum class AnalyticalReadyClock : std::uint8_t { NativeSteady, };
enum class AnalyticalReadyClockState : std::uint8_t { Monotonic, Regressed, };
struct AnalyticalReadyRef {
    std::uint64_t producer_instance_id{};
    std::uint64_t offer_sequence{};
    std::uint64_t config_generation{};
    std::int64_t ready_native_ns{};
    AnalyticalReadyClock clock{AnalyticalReadyClock::NativeSteady};
    AnalyticalReadyClockState clock_state{AnalyticalReadyClockState::Monotonic};
    [[nodiscard]] bool operator==(const AnalyticalReadyRef&) const noexcept = default;
};

enum class AnalyticalReadyEventKind : std::uint8_t {
    Offered, HandedOff, ProducerSuperseded, ProducerCancelled,
    OwnerForwarded, OwnerSuperseded, OwnerCoalesced, OwnerCancelled, OwnerCadenceSuppressed,
};
struct AnalyticalReadyEvent {
    std::uint64_t event_sequence{};
    AnalyticalReadyRef ref;
    AnalyticalReadyEventKind kind{AnalyticalReadyEventKind::Offered};
};
// Actual owner decisions AFTER DSP handoff, not producer loss or layer paint.
// Scalar totals alone have no per-offer coverage claim. Original refs also
// enter the SAME bounded event ring; loss prevents exhaustive ID coverage.
// Internal owners
// report each decision once, at the queue/cadence boundary; never infer IDs
// from offer-sequence gaps. Unsupported vendors leave supported=false.
enum class OwnerPresentationDisposition : std::uint8_t {
    Forwarded, Superseded, Coalesced, Cancelled, CadenceSuppressed,
};
struct OwnerPresentationSummary {
    bool supported{};
    std::uint64_t forwarded{}, superseded{}, coalesced{}, cancelled{}, cadence_suppressed{};
    std::uint64_t accounting_failures{};
};
struct AnalyticalReadySummary {
    bool supported{};
    std::uint64_t producer_instance_id{};
    std::uint64_t offered{};
    std::uint64_t handed_off{};
    std::uint64_t producer_superseded{};
    std::uint64_t producer_cancelled{};
    std::uint64_t outstanding{};
    std::uint64_t clock_regressions{};
    std::uint64_t events_generated{};
    std::uint64_t events_drained{};
    std::uint64_t events_lost{};
    std::uint64_t first_lost_event_sequence{};
    std::uint64_t last_lost_event_sequence{};
    std::uint64_t event_capacity{};
    std::uint64_t events_pending{};
    std::uint64_t event_storage_bytes{};
    OwnerPresentationSummary presentation;
};

// Atomic post-drain summary: conservation may be checked without racing the
// producer. Events are scalar, never IQ/frame/array payloads.
struct AnalyticalReadyDrain {
    std::vector<AnalyticalReadyEvent> events;
    AnalyticalReadySummary summary;
};
[[nodiscard]] std::uint64_t analytical_ready_reserved_bytes(std::uint32_t capacity);

// Native single-owner primitive. The owning output queue supplies exactly one
// FIFO retirement per offer (duplicate/out-of-order retirements refuse); this
// is NOT the downstream pane-obligation ledger.
// Ring storage is allocated once, no arrays/frames/callbacks are retained.
// Full/disabled rings drop NEW evidence, report the loss and never stall DSP.
// Capacity zero is explicit summary-only mode, not complete per-ID evidence.
class AnalyticalReadyJournal final {
public:
    using Clock = std::int64_t (*)() noexcept;
    static constexpr std::size_t max_event_capacity = 4096U;
    explicit AnalyticalReadyJournal(std::size_t event_capacity = 0U, Clock clock = nullptr);
    AnalyticalReadyJournal(const AnalyticalReadyJournal&) = delete;
    AnalyticalReadyJournal& operator=(const AnalyticalReadyJournal&) = delete;
    [[nodiscard]] AnalyticalReadyRef offer(std::uint64_t config_generation);
    void retire(const AnalyticalReadyRef& ref, AnalyticalReadyEventKind kind);
    void enable_owner_presentation() noexcept;
    void record_owner_presentation(const AnalyticalReadyRef& ref, OwnerPresentationDisposition kind) noexcept;
    [[nodiscard]] AnalyticalReadySummary summary() const noexcept;
    [[nodiscard]] std::vector<AnalyticalReadyEvent> poll_events(std::size_t max_items);
    [[nodiscard]] AnalyticalReadyDrain drain(std::size_t max_items);
private:
    void append(const AnalyticalReadyRef& ref, AnalyticalReadyEventKind kind) noexcept;
    Clock clock_;
    std::vector<AnalyticalReadyEvent> events_;
    std::size_t head_{}, pending_{};
    AnalyticalReadySummary summary_;
    std::int64_t last_clock_ns_{};
    bool clock_seen_{}, clock_regressed_{};
    mutable std::mutex mutex_;
    std::mutex drain_mutex_; // one native drain allocation in flight per journal
};

// Native sample for an externally bracketed clock bridge. A unit name alone
// does not establish comparability with Python perf_counter or another process.
[[nodiscard]] std::int64_t analytical_ready_clock_ns() noexcept;

}  // namespace sdr_core
