#include "sdr_hackrf/hackrf_sweep_session.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <condition_variable>
#include <exception>
#include <mutex>
#include <string>
#include <thread>
#include <utility>
#include <vector>

namespace sdr_hackrf {
namespace {

void require_success(const int status, const char* const stage) {
    if (status != 0) {
        throw sdr_core::DeviceError(
            std::string("HackRF Sweep stage failed: ") + stage +
            " (status " + std::to_string(status) + ")"
        );
    }
}

void validate_profile(const HackrfSweepProfile& profile) {
    // Reuse the existing HackRF One receive-control admission without
    // applying its dummy fixed centre. The Sweep owner never calls set_freq.
    validate_hackrf_rx_profile(HackrfRxProfile{
        .center_frequency_hz = 100'000'000.0,
        .sample_rate_hz = profile.sample_rate_hz,
        .baseband_filter_hz = profile.baseband_filter_hz,
        .lna_gain_db = profile.lna_gain_db,
        .vga_gain_db = profile.vga_gain_db,
        .rf_amplifier_enabled = false,
        .bias_tee_enabled = false,
        .slot_count = 1U,
        .ready_capacity = 1U,
        .config_generation = profile.config_generation,
    });
    if (profile.ready_capacity == 0U ||
        profile.ready_capacity > hackrf_sweep_max_ready_blocks ||
        profile.config_generation == 0U) {
        throw sdr_core::ConfigurationError("HackRF Sweep queue/generation is invalid");
    }
}

}  // namespace

struct HackrfSweepSession::Impl final {
    struct SinkContext {
        Impl* self{};
        std::int64_t timestamp_ns{};
    };

    Impl(std::unique_ptr<HackrfSweepRuntimePort> owned_runtime,
         HackrfSweepProfile requested)
        : runtime(std::move(owned_runtime)),
          profile(std::move(requested)),
          gate(profile.sequence),
          ready(profile.ready_capacity) {}

    static bool block_sink(const HackrfSweepBlockDecision& decision,
                           void* const opaque) noexcept {
        if (!decision.admitted()) {
            return true;
        }
        auto& context = *static_cast<SinkContext*>(opaque);
        return context.self->enqueue(decision, context.timestamp_ns);
    }

    bool enqueue(const HackrfSweepBlockDecision& decision,
                 const std::int64_t timestamp_ns) noexcept {
        if (!accepting.load(std::memory_order_acquire)) {
            return false;
        }
        const auto written = write_sequence.load(std::memory_order_relaxed);
        const auto read = read_sequence.load(std::memory_order_acquire);
        if (written - read >= ready.size()) {
            ready_full_drops.fetch_add(1U, std::memory_order_relaxed);
            return false;
        }
        auto& slot = ready[written % ready.size()];
        std::copy(decision.interleaved_ci8.begin(),
                  decision.interleaved_ci8.end(), slot.interleaved_ci8.begin());
        slot.reported_tuned_frequency_hz = decision.reported_tuned_frequency_hz;
        slot.scan_epoch = decision.scan_epoch;
        slot.continuity_epoch = decision.continuity_epoch;
        slot.plan_index = decision.plan_index;
        slot.range_index = decision.range_index;
        slot.step_index = decision.step_index;
        slot.interleave_phase = decision.interleave_phase;
        slot.known_skipped_headers_before = decision.known_skipped_headers_before;
        slot.host_timestamp_ns = timestamp_ns;
        slot.config_generation = profile.config_generation;
        slot.gap_before = decision.gap_before;
        slot.new_scan = decision.new_scan;
        write_sequence.store(written + 1U, std::memory_order_release);
        blocks_queued.fetch_add(1U, std::memory_order_relaxed);
        const auto depth = static_cast<std::uint32_t>(written + 1U - read);
        auto high = ready_high_water.load(std::memory_order_relaxed);
        while (depth > high &&
               !ready_high_water.compare_exchange_weak(
                   high, depth, std::memory_order_relaxed)) {}
        return true;
    }

    static int callback_bridge(const std::span<const std::uint8_t> bytes,
                               const std::int64_t timestamp_ns,
                               void* const opaque) noexcept {
        if (opaque == nullptr) {
            return 1;
        }
        auto& self = *static_cast<Impl*>(opaque);
        self.callbacks_active.fetch_add(1U, std::memory_order_acq_rel);
        struct ExitGuard final {
            Impl& owner;
            ~ExitGuard() {
                if (owner.callbacks_active.fetch_sub(
                        1U, std::memory_order_acq_rel) == 1U) {
                    owner.callback_cv.notify_all();
                }
            }
        } guard{self};
        self.callbacks_seen.fetch_add(1U, std::memory_order_relaxed);
        self.callback_bytes_seen.fetch_add(bytes.size(), std::memory_order_relaxed);
        if (!self.accepting.load(std::memory_order_acquire)) {
            return 1;
        }
        if (self.callback_gate.test_and_set(std::memory_order_acquire)) {
            self.callback_gate_drops.fetch_add(1U, std::memory_order_relaxed);
            self.pending_callback_drops.fetch_add(1U, std::memory_order_relaxed);
            return 0;
        }
        struct GateGuard final {
            Impl& owner;
            ~GateGuard() { owner.callback_gate.clear(std::memory_order_release); }
        } gate_guard{self};
        if (self.pending_callback_drops.exchange(
                0U, std::memory_order_acq_rel) != 0U) {
            self.gate.note_downstream_drop();
        }
        if (timestamp_ns <= 0) {
            self.invalid_timestamp_callbacks.fetch_add(1U, std::memory_order_relaxed);
            self.gate.note_downstream_drop();
            return 0;
        }
        SinkContext context{&self, timestamp_ns};
        static_cast<void>(self.gate.admit_transfer(bytes, &Impl::block_sink, &context));
        return self.accepting.load(std::memory_order_acquire) ? 0 : 1;
    }

    void cleanup_failed_start() noexcept {
        if (device_open) {
            if (runtime->close_device() == 0) {
                device_open = false;
            }
        }
        if (library_initialized && !device_open) {
            if (runtime->exit_library() == 0) {
                library_initialized = false;
            }
        }
    }

    std::unique_ptr<HackrfSweepRuntimePort> runtime;
    HackrfSweepProfile profile;
    HackrfSweepSequenceGate gate;
    std::vector<HackrfSweepQueuedBlock> ready;
    std::atomic<std::uint64_t> write_sequence{};
    std::atomic<std::uint64_t> read_sequence{};
    std::atomic<std::uint64_t> callbacks_seen{};
    std::atomic<std::uint64_t> callback_bytes_seen{};
    std::atomic<std::uint64_t> callback_gate_drops{};
    std::atomic<std::uint64_t> invalid_timestamp_callbacks{};
    std::atomic<std::uint64_t> pending_callback_drops{};
    std::atomic<std::uint64_t> blocks_queued{};
    std::atomic<std::uint64_t> blocks_popped{};
    std::atomic<std::uint64_t> blocks_abandoned{};
    std::atomic<std::uint64_t> ready_full_drops{};
    std::atomic<std::uint32_t> ready_high_water{};
    std::atomic<std::uint32_t> callbacks_active{};
    std::atomic<bool> accepting{true};
    std::atomic<bool> gate_readable{};
    std::atomic<bool> running{};
    std::atomic_flag callback_gate = ATOMIC_FLAG_INIT;
    mutable std::mutex consumer_mutex;
    std::mutex lifecycle_mutex;
    std::mutex callback_wait_mutex;
    std::condition_variable callback_cv;
    HackrfSweepStopResult stop_result{};
    std::uint16_t usb_api_version{};
    std::uint32_t transfer_bytes{};
    bool library_initialized{};
    bool device_open{};
    bool close_succeeded{};
    bool exit_succeeded{};
};

std::unique_ptr<HackrfSweepSession> HackrfSweepSession::start(
    std::unique_ptr<HackrfSweepRuntimePort> runtime,
    HackrfSweepProfile profile
) {
    if (!runtime) {
        throw sdr_core::ConfigurationError("HackRF Sweep runtime is required");
    }
    validate_profile(profile);
    auto impl = std::make_unique<Impl>(std::move(runtime), std::move(profile));
    try {
        require_success(impl->runtime->initialize_library(), "library_init");
        impl->library_initialized = true;
        require_success(impl->runtime->open_exactly_one_hackrf_one(), "open_exactly_one");
        impl->device_open = true;
        require_success(impl->runtime->read_usb_api_version(impl->usb_api_version),
                        "usb_api_version");
        if (impl->usb_api_version < hackrf_sweep_min_usb_api) {
            throw sdr_core::DeviceError("HackRF Sweep requires USB API 0x0104 or newer");
        }
        impl->transfer_bytes = impl->runtime->transfer_buffer_size();
        if (impl->transfer_bytes == 0U ||
            (impl->transfer_bytes % hackrf_sweep_block_bytes) != 0U ||
            impl->transfer_bytes / hackrf_sweep_block_bytes >
                impl->gate.plan().max_transfer_blocks) {
            throw sdr_core::DeviceError("HackRF Sweep transfer exceeds block plan");
        }
        require_success(impl->runtime->set_sample_rate(impl->profile.sample_rate_hz),
                        "sample_rate");
        require_success(impl->runtime->set_baseband_filter(
                            impl->profile.baseband_filter_hz), "baseband_filter");
        require_success(impl->runtime->set_rf_amplifier(false), "rf_amplifier_off");
        require_success(impl->runtime->set_bias_tee(false), "bias_tee_off");
        require_success(impl->runtime->set_lna_gain(impl->profile.lna_gain_db),
                        "lna_gain");
        require_success(impl->runtime->set_vga_gain(impl->profile.vga_gain_db),
                        "vga_gain");
        require_success(impl->runtime->initialize_sweep(impl->gate.plan()),
                        "init_sweep");
        require_success(impl->runtime->start_rx_sweep(&Impl::callback_bridge,
                                                       impl.get()), "start_rx_sweep");
        impl->running.store(true, std::memory_order_release);
    } catch (...) {
        impl->accepting.store(false, std::memory_order_release);
        impl->cleanup_failed_start();
        throw;
    }
    return std::unique_ptr<HackrfSweepSession>(
        new HackrfSweepSession(std::move(impl))
    );
}

HackrfSweepSession::HackrfSweepSession(std::unique_ptr<Impl> impl) noexcept
    : impl_(std::move(impl)) {}

HackrfSweepSession::~HackrfSweepSession() {
    if (impl_ && impl_->running.load(std::memory_order_acquire)) {
        if (!stop(std::chrono::seconds(5)).complete()) {
            // The SDK callback still holds Impl. As in the fixed-band owner,
            // destruction after failed Stop must never become use-after-free.
            std::terminate();
        }
    }
}

bool HackrfSweepSession::try_pop(HackrfSweepQueuedBlock& output) noexcept {
    std::lock_guard lock(impl_->consumer_mutex);
    const auto read = impl_->read_sequence.load(std::memory_order_relaxed);
    const auto written = impl_->write_sequence.load(std::memory_order_acquire);
    if (read == written) {
        return false;
    }
    output = impl_->ready[read % impl_->ready.size()];
    impl_->read_sequence.store(read + 1U, std::memory_order_release);
    impl_->blocks_popped.fetch_add(1U, std::memory_order_relaxed);
    return true;
}

HackrfSweepSessionMetrics HackrfSweepSession::metrics() const noexcept {
    HackrfSweepSessionMetrics result;
    result.callbacks_seen = impl_->callbacks_seen.load(std::memory_order_relaxed);
    result.callback_bytes_seen = impl_->callback_bytes_seen.load(std::memory_order_relaxed);
    result.callback_gate_drops =
        impl_->callback_gate_drops.load(std::memory_order_relaxed);
    result.invalid_timestamp_callbacks =
        impl_->invalid_timestamp_callbacks.load(std::memory_order_relaxed);
    result.blocks_queued = impl_->blocks_queued.load(std::memory_order_relaxed);
    result.blocks_popped = impl_->blocks_popped.load(std::memory_order_relaxed);
    result.blocks_abandoned = impl_->blocks_abandoned.load(std::memory_order_relaxed);
    result.ready_full_drops = impl_->ready_full_drops.load(std::memory_order_relaxed);
    const auto read = impl_->read_sequence.load(std::memory_order_acquire);
    const auto written = impl_->write_sequence.load(std::memory_order_acquire);
    // A concurrent pop can advance read after this snapshot. Read first and
    // clamp to the physical ring bound; never wrap a live depth readout.
    result.ready_depth = static_cast<std::uint32_t>(std::min<std::uint64_t>(
        written >= read ? written - read : 0U, impl_->ready.size()
    ));
    result.ready_high_water = impl_->ready_high_water.load(std::memory_order_relaxed);
    result.callbacks_active = impl_->callbacks_active.load(std::memory_order_acquire);
    result.accepting_callbacks = impl_->accepting.load(std::memory_order_acquire);
    if (impl_->gate_readable.load(std::memory_order_acquire)) {
        result.sequence = impl_->gate.metrics();
    }
    return result;
}

HackrfSweepStopResult HackrfSweepSession::stop(
    const std::chrono::milliseconds callback_timeout,
    const bool preserve_ready_for_owned_drain
) noexcept {
    std::lock_guard lifecycle_lock(impl_->lifecycle_mutex);
    if (!impl_->running.load(std::memory_order_acquire)) {
        return impl_->stop_result;
    }
    impl_->accepting.store(false, std::memory_order_release);
    if (!impl_->stop_result.stop_rx_called) {
        impl_->stop_result.stop_rx_called = true;
        impl_->stop_result.stop_rx_status = impl_->runtime->stop_rx();
    }
    if (impl_->stop_result.stop_rx_status != 0 && !impl_->close_succeeded) {
        // The pinned SDK cancels transfers BEFORE its RF-off command. When
        // that command fails, a second hackrf_stop_rx can never succeed:
        // transfers_setup is already false. Its close operation joins the
        // transfer thread and consumes the handle even on an error return.
        // Never retry Stop; close the same handle while Impl remains alive.
        impl_->stop_result.close_called = true;
        impl_->stop_result.close_status = impl_->runtime->close_device();
        if (impl_->stop_result.close_status != 0 &&
            impl_->stop_result.first_close_error == 0) {
            impl_->stop_result.first_close_error = impl_->stop_result.close_status;
        }
        impl_->close_succeeded = impl_->stop_result.close_status == 0;
        if (!impl_->close_succeeded) {
            return impl_->stop_result;
        }
        impl_->device_open = false;
    }
    {
        std::unique_lock wait_lock(impl_->callback_wait_mutex);
        impl_->stop_result.callbacks_quiescent = impl_->callback_cv.wait_for(
            wait_lock, callback_timeout, [this] {
                return impl_->callbacks_active.load(std::memory_order_acquire) == 0U;
            }
        );
    }
    if (!impl_->stop_result.callbacks_quiescent) {
        return impl_->stop_result;
    }
    impl_->gate_readable.store(true, std::memory_order_release);
    if (!preserve_ready_for_owned_drain) {
        std::lock_guard consumer_lock(impl_->consumer_mutex);
        const auto written = impl_->write_sequence.load(std::memory_order_acquire);
        const auto read = impl_->read_sequence.load(std::memory_order_relaxed);
        const auto abandoned = written - read;
        impl_->read_sequence.store(written, std::memory_order_release);
        impl_->blocks_abandoned.fetch_add(abandoned, std::memory_order_relaxed);
        impl_->stop_result.abandoned_blocks += abandoned;
    }
    if (!impl_->close_succeeded) {
        impl_->stop_result.close_called = true;
        impl_->stop_result.close_status = impl_->runtime->close_device();
        if (impl_->stop_result.close_status != 0 &&
            impl_->stop_result.first_close_error == 0) {
            impl_->stop_result.first_close_error = impl_->stop_result.close_status;
        }
        impl_->close_succeeded = impl_->stop_result.close_status == 0;
        if (!impl_->close_succeeded) {
            return impl_->stop_result;
        }
        impl_->device_open = false;
    }
    if (!impl_->exit_succeeded) {
        impl_->stop_result.exit_called = true;
        impl_->stop_result.exit_status = impl_->runtime->exit_library();
        if (impl_->stop_result.exit_status != 0 &&
            impl_->stop_result.first_exit_error == 0) {
            impl_->stop_result.first_exit_error = impl_->stop_result.exit_status;
        }
        impl_->exit_succeeded = impl_->stop_result.exit_status == 0;
        if (!impl_->exit_succeeded) {
            return impl_->stop_result;
        }
        impl_->library_initialized = false;
    }
    impl_->running.store(false, std::memory_order_release);
    return impl_->stop_result;
}

std::uint64_t HackrfSweepSession::discard_ready_after_stop() noexcept {
    std::lock_guard lifecycle_lock(impl_->lifecycle_mutex);
    if (impl_->running.load(std::memory_order_acquire) ||
        impl_->callbacks_active.load(std::memory_order_acquire) != 0U) {
        return 0U;
    }
    std::lock_guard consumer_lock(impl_->consumer_mutex);
    const auto written = impl_->write_sequence.load(std::memory_order_acquire);
    const auto read = impl_->read_sequence.load(std::memory_order_relaxed);
    const auto abandoned = written - read;
    impl_->read_sequence.store(written, std::memory_order_release);
    impl_->blocks_abandoned.fetch_add(abandoned, std::memory_order_relaxed);
    impl_->stop_result.abandoned_blocks += abandoned;
    return abandoned;
}

bool HackrfSweepSession::running() const noexcept {
    return impl_->running.load(std::memory_order_acquire);
}

std::uint16_t HackrfSweepSession::observed_usb_api_version() const noexcept {
    return impl_->usb_api_version;
}

std::uint32_t HackrfSweepSession::transfer_bytes() const noexcept {
    return impl_->transfer_bytes;
}

#if defined(SDR_CORE_ENABLE_TEST_HOOKS)
void HackrfSweepSession::test_hold_active_callback(
    std::atomic<bool>& entered,
    const std::atomic<bool>& release
) noexcept {
    impl_->callbacks_active.fetch_add(1U, std::memory_order_acq_rel);
    entered.store(true, std::memory_order_release);
    while (!release.load(std::memory_order_acquire)) {
        std::this_thread::yield();
    }
    if (impl_->callbacks_active.fetch_sub(1U, std::memory_order_acq_rel) == 1U) {
        impl_->callback_cv.notify_all();
    }
}
#endif

}  // namespace sdr_hackrf
