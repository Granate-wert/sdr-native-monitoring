#include "sdr_hackrf/hackrf_runtime_dsp_session.hpp"

#include <mutex>
#include <utility>

namespace sdr_hackrf {

struct HackrfRuntimeDspSession::Impl final {
    std::unique_ptr<HackrfRxSession> source;
    std::unique_ptr<HackrfAcquisitionDspSession> processing;
    mutable std::mutex lifecycle_mutex;
    HackrfRuntimeDspStopResult stop_result{};
};

std::unique_ptr<HackrfRuntimeDspSession> HackrfRuntimeDspSession::start(
    std::unique_ptr<HackrfRxRuntimePort> runtime,
    HackrfRuntimeDspSessionConfig config
) {
    validate_hackrf_fixed_band_dsp_config(config.processing.dsp);
    auto source = HackrfRxSession::start(std::move(runtime), config.rx);
    auto processing = HackrfAcquisitionDspSession::start(
        source->shared_ingress(),
        std::move(config.processing)
    );
    auto impl = std::make_unique<Impl>();
    impl->source = std::move(source);
    impl->processing = std::move(processing);
    return std::unique_ptr<HackrfRuntimeDspSession>(
        new HackrfRuntimeDspSession(std::move(impl))
    );
}

HackrfRuntimeDspSession::HackrfRuntimeDspSession(
    std::unique_ptr<Impl> impl
) noexcept : impl_(std::move(impl)) {}

HackrfRuntimeDspSession::~HackrfRuntimeDspSession() {
    if (impl_ != nullptr && impl_->source->running()) {
        const auto result = stop(std::chrono::seconds(5));
        if (!result.complete()) {
            std::terminate();
        }
    }
}

std::vector<sdr_core::SpectrumFrame>
HackrfRuntimeDspSession::poll_spectrum_frames(const std::size_t max_items) {
    return impl_->processing->poll_spectrum_frames(max_items);
}

HackrfRuntimeDspMetrics HackrfRuntimeDspSession::metrics() const {
    HackrfRuntimeDspMetrics result;
    result.lifecycle_open = impl_->source->running();
    result.source = impl_->source->metrics();
    result.processing = impl_->processing->metrics();
    return result;
}

HackrfRuntimeDspStopResult HackrfRuntimeDspSession::stop(
    const std::chrono::milliseconds callback_timeout
) noexcept {
    std::lock_guard lock(impl_->lifecycle_mutex);
    if (impl_->stop_result.complete()) {
        return impl_->stop_result;
    }
    impl_->stop_result.source_quiesce = impl_->source->quiesce(callback_timeout);
    if (!impl_->stop_result.source_quiesce.complete()) {
        return impl_->stop_result;
    }
    impl_->stop_result.processing = impl_->processing->stop(callback_timeout);
    if (!impl_->stop_result.processing.complete()) {
        return impl_->stop_result;
    }
    impl_->stop_result.source_finalize = impl_->source->finalize_after_drain();
    return impl_->stop_result;
}

}  // namespace sdr_hackrf
