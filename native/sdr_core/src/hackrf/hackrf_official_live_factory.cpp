#include "sdr_hackrf/hackrf_live_factory.hpp"

#include "sdr_hackrf/hackrf_official_rx_port.hpp"

#include <utility>

namespace sdr_hackrf {

std::unique_ptr<HackrfRuntimeDspSession>
make_official_hackrf_runtime_dsp_session(const HackrfLiveFactoryConfig& config,
    std::optional<std::array<std::uint32_t, 4>> expected_serial_words) {
    // Configuration is fully translated and validated before the first SDK
    // operation. This is the one explicit side-effecting R11-N boundary.
    auto translated = make_hackrf_runtime_dsp_config(config);
    auto runtime = make_official_hackrf_rx_port(expected_serial_words);
    return HackrfRuntimeDspSession::start(std::move(runtime), std::move(translated));
}

}  // namespace sdr_hackrf
