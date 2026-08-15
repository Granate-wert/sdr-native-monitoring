#pragma once

#include "sdr_hackrf/hackrf_rx_session.hpp"

#include <memory>

namespace sdr_hackrf {

// Implemented only by the optional target compiled against the official
// private libhackrf header/import library. The public surface stays SDK-free.
[[nodiscard]] std::unique_ptr<HackrfRxRuntimePort> make_official_hackrf_rx_port();

}  // namespace sdr_hackrf
