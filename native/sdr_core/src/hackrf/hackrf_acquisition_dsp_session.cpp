#include "sdr_hackrf/hackrf_acquisition_dsp_session.hpp"

#include "sdr_core/errors.hpp"

#include <atomic>
#include <exception>
#include <mutex>
#include <thread>
#include <utility>

namespace sdr_hackrf {

struct HackrfAcquisitionDspSession::Impl final {
    Impl(
        std::shared_ptr<HackrfRxIngress> source,
        HackrfAcquisitionDspSessionConfig requested
    )
        : ingress(std::move(source)), dsp(std::move(requested.dsp)) {}

    void run() noexcept {
        bool discard_after_failure = false;
        for (;;) {
            HackrfRxLease lease;
            if (ingress->pop(lease) == HackrfRxPopResult::Stopped) {
                break;
            }
            if (discard_after_failure) {
                lease.reset();
                worker_abandoned_blocks.fetch_add(1U, std::memory_order_relaxed);
                continue;
            }
            try {
                dsp.push(std::move(lease));
                worker_blocks_processed.fetch_add(1U, std::memory_order_relaxed);
            } catch (...) {
                worker_failures.fetch_add(1U, std::memory_order_relaxed);
                discard_after_failure = true;
                state.store(HackrfAcquisitionDspState::Failed, std::memory_order_release);
                ingress->request_stop();
            }
        }
        if (state.load(std::memory_order_acquire) ==
            HackrfAcquisitionDspState::Running) {
            state.store(
                HackrfAcquisitionDspState::StopPending,
                std::memory_order_release
            );
        }
        worker_exited.store(true, std::memory_order_release);
    }

    std::shared_ptr<HackrfRxIngress> ingress;
    HackrfFixedBandDsp dsp;
    std::thread worker;
    mutable std::mutex lifecycle_mutex;
    std::atomic<HackrfAcquisitionDspState> state{
        HackrfAcquisitionDspState::Running
    };
    std::atomic<bool> worker_exited{};
    std::atomic<bool> worker_joined{};
    std::atomic<std::uint64_t> worker_blocks_processed{};
    std::atomic<std::uint64_t> worker_failures{};
    std::atomic<std::uint64_t> worker_abandoned_blocks{};
    bool stop_complete{};
    HackrfAcquisitionDspStopResult stop_result{};
};

std::unique_ptr<HackrfAcquisitionDspSession> HackrfAcquisitionDspSession::start(
    std::shared_ptr<HackrfRxIngress> ingress,
    HackrfAcquisitionDspSessionConfig config
) {
    if (!ingress) {
        throw sdr_core::ConfigurationError("HackRF acquisition/DSP ingress is required");
    }
    auto impl = std::make_unique<Impl>(std::move(ingress), std::move(config));
    auto session = std::unique_ptr<HackrfAcquisitionDspSession>(
        new HackrfAcquisitionDspSession(std::move(impl))
    );
    session->impl_->worker = std::thread([owned = session->impl_.get()] {
        owned->run();
    });
    return session;
}

HackrfAcquisitionDspSession::HackrfAcquisitionDspSession(
    std::unique_ptr<Impl> impl
) noexcept : impl_(std::move(impl)) {}

HackrfAcquisitionDspSession::~HackrfAcquisitionDspSession() {
    if (impl_ != nullptr && impl_->worker.joinable()) {
        const auto result = stop(std::chrono::seconds(5));
        if (!result.complete()) {
            std::terminate();
        }
    }
}

std::vector<sdr_core::SpectrumFrame>
HackrfAcquisitionDspSession::poll_spectrum_frames(const std::size_t max_items) {
    return impl_->dsp.poll_spectrum_frames(max_items);
}

HackrfAcquisitionDspMetrics HackrfAcquisitionDspSession::metrics() const {
    HackrfAcquisitionDspMetrics result;
    result.state = impl_->state.load(std::memory_order_acquire);
    result.worker_exited = impl_->worker_exited.load(std::memory_order_acquire);
    result.worker_joined = impl_->worker_joined.load(std::memory_order_acquire);
    result.worker_blocks_processed =
        impl_->worker_blocks_processed.load(std::memory_order_relaxed);
    result.worker_failures = impl_->worker_failures.load(std::memory_order_relaxed);
    result.worker_abandoned_blocks =
        impl_->worker_abandoned_blocks.load(std::memory_order_relaxed);
    result.ingress = impl_->ingress->metrics();
    result.dsp = impl_->dsp.metrics();
    return result;
}

HackrfAcquisitionDspStopResult HackrfAcquisitionDspSession::stop(
    const std::chrono::milliseconds callback_timeout
) noexcept {
    std::lock_guard lock(impl_->lifecycle_mutex);
    if (impl_->stop_complete) {
        return impl_->stop_result;
    }

    if (impl_->state.load(std::memory_order_acquire) ==
        HackrfAcquisitionDspState::Running) {
        impl_->state.store(
            HackrfAcquisitionDspState::StopPending,
            std::memory_order_release
        );
    }
    impl_->ingress->request_stop();
    impl_->stop_result.callbacks_quiescent =
        impl_->ingress->wait_callbacks_quiescent(callback_timeout);
    if (!impl_->stop_result.callbacks_quiescent) {
        impl_->stop_result.processing_failed =
            impl_->worker_failures.load(std::memory_order_relaxed) != 0U;
        impl_->stop_result.worker_abandoned_blocks =
            impl_->worker_abandoned_blocks.load(std::memory_order_relaxed);
        return impl_->stop_result;
    }

    if (impl_->worker.joinable()) {
        impl_->worker.join();
    }
    impl_->worker_joined.store(true, std::memory_order_release);
    impl_->stop_result.worker_joined = true;
    impl_->stop_result.processing_failed =
        impl_->worker_failures.load(std::memory_order_relaxed) != 0U;
    impl_->stop_result.worker_abandoned_blocks =
        impl_->worker_abandoned_blocks.load(std::memory_order_relaxed);
    if (!impl_->stop_result.processing_failed) {
        impl_->state.store(HackrfAcquisitionDspState::Stopped, std::memory_order_release);
    }
    impl_->stop_complete = true;
    return impl_->stop_result;
}

}  // namespace sdr_hackrf
