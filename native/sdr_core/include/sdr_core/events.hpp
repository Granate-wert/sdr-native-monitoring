#pragma once

#include "sdr_core/types.hpp"

#include <cstdint>
#include <string>

namespace sdr_core {

// Low-rate diagnostic record crossing the engine boundary through the
// bounded event queue. Critical errors are never silently dropped: when the
// event queue itself overflows, the loss is counted and surfaced as a
// synthesized overflow event at poll time.
struct DiagnosticEvent {
    EventSeverity severity{EventSeverity::Info};
    std::string code;
    std::string message;
    std::int64_t timestamp_ns{};
    std::uint64_t sequence{};
};

// Monotonic host timestamp in nanoseconds for events and I/Q blocks.
[[nodiscard]] std::int64_t host_monotonic_ns() noexcept;

}  // namespace sdr_core
