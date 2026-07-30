#include "sdr_core/dsp_backend.hpp"

#include "sdr_core/cuda_backend_link.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <functional>
#include <cstring>
#include <deque>
#include <iostream>
#include <mutex>
#include <optional>
#include <utility>
#include <unordered_map>

namespace sdr_core {

namespace {

// AUTO remains conservative until a committed CPU/CUDA A/B table is available.
// Forced CUDA is still exposed when its device self-test passes.
// Runtime failover wrapper (P08 §11): primary GPU backend with a CPU standby.
// On a recoverable typed failure the primary is destroyed, the CPU backend is
// configured with the same DspConfig, and the first CPU frame carries
// CUDA_FALLBACK. Partial GPU output is never published; the switch and the
// reason are counted and stay visible in metrics(). The next configure()
// retries the primary backend once.
class FailoverDspBackend final : public DspBackend {
public:
    FailoverDspBackend(
        std::function<std::unique_ptr<DspBackend>()> primary_factory,
        DspOptions cpu_options,
        const bool allow_runtime_fallback,
        const ComputeBackendKind requested
    )
        : primary_factory_(std::move(primary_factory)),
          cpu_options_(std::move(cpu_options)),
          allow_runtime_fallback_(allow_runtime_fallback),
          requested_(requested) {
        primary_ = primary_factory_();
        active_ = primary_.get();
    }

    void configure(const DspConfig& config) override {
        std::lock_guard lock(mutex_);
        config_ = config;
        replay_.clear();
        replay_samples_ = 0U;
        replay_exact_possible_ = true;
        committed_output_.clear();
        last_committed_.reset();
        fallback_pending_ = false;
        discontinuity_pending_ = false;
        // Retry the primary on every configure after a fallback (P08 §6).
        if (primary_ == nullptr && active_ != nullptr) {
            try {
                auto candidate = primary_factory_();
                candidate->configure(config);
                primary_ = std::move(candidate);
                active_ = primary_.get();
            } catch (const DeviceError& error) {
                metrics_.last_backend_error = error.code();
                // Stay on the working CPU backend.
            }
        }
        // AUTO deliberately stays on CPU until a reproducible benchmark table
        // is committed. This avoids a guessed crossover policy.
        if (requested_ == ComputeBackendKind::Auto) {
            primary_.reset();
            cpu_ = make_cpu_dsp_backend(cpu_options_);
            active_ = cpu_.get();
        }
        try {
            active_->configure(config);
            config_ = config;
        } catch (const DeviceError& error) {
            if (active_ != primary_.get()) {
                throw;
            }
            if (!allow_runtime_fallback_) {
                ++metrics_.backend_fallback_count;
                metrics_.last_backend_error = error.code();
                throw;
            }
            switch_to_cpu(error.code());
            active_->configure(config);
            config_ = config;
        }
    }

    void push_iq(const IqBlock& block) override {
        std::lock_guard lock(mutex_);
        caller_samples_processed_ += block.sample_count;
        append_replay(block);
        try {
            active_->push_iq(block);
            commit_child_output(false);
        } catch (const DeviceError& error) {
            if (active_ != primary_.get()) {
                throw;
            }
            if (!allow_runtime_fallback_) {
                metrics_.last_backend_error = error.code();
                throw;
            }
            rebuild_cpu_after_failure(error.code());
        }
    }

    [[nodiscard]] std::vector<SpectrumFrame> poll_spectrum(
        const std::size_t max_items,
        const bool flush_partial_batch
    ) override {
        std::lock_guard lock(mutex_);
        try {
            commit_child_output(flush_partial_batch);
        } catch (const DeviceError& error) {
            if (active_ != primary_.get()) {
                throw;
            }
            if (!allow_runtime_fallback_) {
                metrics_.last_backend_error = error.code();
                throw;
            }
            rebuild_cpu_after_failure(error.code());
        }
        std::vector<SpectrumFrame> frames;
        while (!committed_output_.empty() &&
               (max_items == 0U || frames.size() < max_items)) {
            frames.push_back(std::move(committed_output_.front()));
            committed_output_.pop_front();
        }
        return frames;
    }

    void reset() override {
        std::lock_guard lock(mutex_);
        active_->reset();
        replay_.clear();
        replay_samples_ = 0U;
        replay_exact_possible_ = true;
        committed_output_.clear();
        last_committed_.reset();
        fallback_pending_ = false;
        discontinuity_pending_ = false;
    }

    [[nodiscard]] DspBackendMetrics metrics() const override {
        std::lock_guard lock(mutex_);
        DspBackendMetrics result = metrics_;
        result.samples_processed = caller_samples_processed_;
        result.output_pending = committed_output_.size();
        result.requested_preference = requested_;
        result.active_backend = active_->info().kind;
        result.backend_self_test_passed = primary_ != nullptr;
        result.backend_fallback_count = metrics_.backend_fallback_count;
        result.backend_switch_count = metrics_.backend_switch_count;
        result.last_backend_error = metrics_.last_backend_error;
        result.gpu_processing_ns = std::max(metrics_.gpu_processing_ns, active_->metrics().gpu_processing_ns);
        result.h2d_ns = std::max(metrics_.h2d_ns, active_->metrics().h2d_ns);
        result.d2h_ns = std::max(metrics_.d2h_ns, active_->metrics().d2h_ns);
        return result;
    }

    [[nodiscard]] BackendInfo info() const override {
        std::lock_guard lock(mutex_);
        return active_->info();
    }

private:
    struct ReplayBlock {
        IqBlock block;
    };
    struct CommittedFrameKey {
        std::uint64_t generation{};
        std::uint64_t first_sample_index{};
    };

    static constexpr std::uint64_t max_replay_block_samples = 262144U;

    [[nodiscard]] std::uint64_t replay_limit() const noexcept {
        const auto n = static_cast<std::uint64_t>(config_.fft_size);
        const auto hop = static_cast<std::uint64_t>(config_.hop_size);
        const auto groups = static_cast<std::uint64_t>(config_.averaging_frames) +
                           static_cast<std::uint64_t>(config_.batch_size);
        return n + (groups > 2U ? groups - 2U : 0U) * hop + max_replay_block_samples;
    }

    static bool same_epoch(const IqBlock& left, const IqBlock& right) noexcept {
        return left.config_generation == right.config_generation &&
               left.sample_format == right.sample_format &&
               left.sample_rate_hz == right.sample_rate_hz &&
               left.center_frequency_hz == right.center_frequency_hz &&
               left.first_sample_index + left.sample_count == right.first_sample_index;
    }

    static std::size_t sample_width(const SampleFormat format) {
        switch (format) {
        case SampleFormat::ComplexInt8Interleaved: return 2U;
        case SampleFormat::ComplexInt12InInt16Le:
        case SampleFormat::ComplexInt16Le: return 4U;
        case SampleFormat::ComplexFloat32Le: return 8U;
        }
        throw ConfigurationError("unknown sample format in replay");
    }

    void append_replay(const IqBlock& block) {
        if (!block.samples || block.sample_count == 0U) {
            return;
        }
        if (!replay_.empty() && !same_epoch(replay_.back().block, block)) {
            replay_.clear();
            replay_samples_ = 0U;
            replay_exact_possible_ = true;
        }
        const auto width = sample_width(block.sample_format);
        const auto keep = std::min<std::uint64_t>(block.sample_count, max_replay_block_samples);
        const auto start_sample = static_cast<std::uint64_t>(block.sample_count) - keep;
        ReplayBlock copy;
        copy.block = block;
        copy.block.first_sample_index += start_sample;
        copy.block.sample_count = static_cast<std::uint32_t>(keep);
        if (start_sample != 0U) {
            replay_exact_possible_ = false;
            copy.block.timestamp_ns += static_cast<std::int64_t>(
                static_cast<long double>(start_sample) * 1.0e9L / static_cast<long double>(block.sample_rate_hz));
        }
        auto copied_samples = std::make_shared<std::vector<std::uint8_t>>(
            static_cast<std::size_t>(keep) * width
        );
        std::memcpy(
            copied_samples->data(),
            block.samples->data() + static_cast<std::size_t>(start_sample) * width,
            copied_samples->size()
        );
        copy.block.samples = std::move(copied_samples);
        if (start_sample != 0U) {
            replay_.clear();
            replay_samples_ = 0U;
        }
        replay_.push_back(std::move(copy));
        replay_samples_ += keep;
        while (replay_.size() > 1U && replay_samples_ > replay_limit()) {
            replay_samples_ -= replay_.front().block.sample_count;
            replay_.pop_front();
        }
    }

    void commit_child_output(const bool flush_partial) {
        const auto frames = active_->poll_spectrum(0U, flush_partial);
        const auto child_metrics = active_->metrics();
        metrics_.gpu_processing_ns = std::max(metrics_.gpu_processing_ns, child_metrics.gpu_processing_ns);
        metrics_.h2d_ns = std::max(metrics_.h2d_ns, child_metrics.h2d_ns);
        metrics_.d2h_ns = std::max(metrics_.d2h_ns, child_metrics.d2h_ns);
        for (const auto& candidate : frames) {
            if (last_committed_.has_value() &&
                candidate.config_generation == last_committed_->generation &&
                candidate.first_sample_index <= last_committed_->first_sample_index) {
                continue;
            }
            auto frame = candidate;
            frame.frame_sequence = next_public_frame_sequence_++;
            if (fallback_pending_) {
                frame.quality_flags = frame.quality_flags | QualityFlag::BackendFallback;
                fallback_pending_ = false;
            }
            if (discontinuity_pending_) {
                frame.quality_flags = frame.quality_flags | QualityFlag::BackendDiscontinuity |
                                      QualityFlag::FftDropped;
                discontinuity_pending_ = false;
            }
            if (committed_output_.size() >= cpu_options_.output_capacity) {
                committed_output_.pop_front();
                ++metrics_.fft_frames_dropped;
            }
            committed_output_.push_back(std::move(frame));
            ++metrics_.fft_frames_computed;
            last_committed_ = CommittedFrameKey{
                candidate.config_generation,
                candidate.first_sample_index,
            };
        }
    }

    void rebuild_cpu_after_failure(const BackendErrorCode reason) {
        switch_to_cpu(reason);
        if (replay_.empty() || !replay_exact_possible_) {
            discontinuity_pending_ = true;
            ++metrics_.fft_frames_dropped;
            return;
        }
        for (const auto& entry : replay_) {
            active_->push_iq(entry.block);
        }
        commit_child_output(true);
    }

    void switch_to_cpu(const BackendErrorCode reason) {
        primary_.reset();  // release GPU resources before CPU takes over
        auto cpu = make_cpu_dsp_backend(cpu_options_);
        cpu->configure(config_);
        ++metrics_.backend_fallback_count;
        ++metrics_.backend_switch_count;
        metrics_.last_backend_error = reason;
        cpu_ = std::move(cpu);
        active_ = cpu_.get();
        fallback_pending_ = true;
    }

    mutable std::mutex mutex_;
    std::function<std::unique_ptr<DspBackend>()> primary_factory_;
    std::unique_ptr<DspBackend> primary_;
    std::unique_ptr<DspBackend> cpu_;
    DspBackend* active_;
    DspOptions cpu_options_;
    DspConfig config_{};
    bool allow_runtime_fallback_;
    ComputeBackendKind requested_;
    bool fallback_pending_{};
    bool discontinuity_pending_{};
    std::deque<SpectrumFrame> committed_output_;
    std::deque<ReplayBlock> replay_;
    std::uint64_t replay_samples_{};
    bool replay_exact_possible_{true};
    std::optional<CommittedFrameKey> last_committed_;
    std::uint64_t next_public_frame_sequence_{};
    std::uint64_t caller_samples_processed_{};
    DspBackendMetrics metrics_{};
};

[[nodiscard]] std::string unavailable_details(const ComputeBackendKind kind) {
    switch (kind) {
    case ComputeBackendKind::Cuda:
        return "CUDA backend is not compiled (SDR_CORE_ENABLE_CUDA=OFF)";
    case ComputeBackendKind::Hip:
        return "HIP backend is introduced by the P08H branch (not implemented)";
    default:
        return "backend is unavailable";
    }
}

}  // namespace

BackendAvailability backend_availability(const ComputeBackendKind kind) {
    switch (kind) {
    case ComputeBackendKind::Cpu: {
        BackendAvailability result;
        result.compiled = true;
        result.runtime_present = true;
        result.device_count = 1U;
        result.device_supported = true;
        result.self_test_passed = true;
        return result;
    }
    case ComputeBackendKind::Cuda:
        return cuda_link::availability(-1);
    case ComputeBackendKind::Hip: {
        BackendAvailability result;
        result.compiled = false;
        result.reason_code = std::string(to_wire(BackendErrorCode::RuntimeNotFound));
        result.details = unavailable_details(kind);
        return result;
    }
    case ComputeBackendKind::Auto:
        break;
    }
    throw ConfigurationError("backend_availability requires a concrete backend kind");
}

BackendAvailability run_backend_self_test(const ComputeBackendKind kind) {
    switch (kind) {
    case ComputeBackendKind::Cpu:
        return backend_availability(ComputeBackendKind::Cpu);
    case ComputeBackendKind::Cuda: {
        static std::mutex cache_mutex;
        static std::unordered_map<std::string, BackendAvailability> cache;
        std::lock_guard lock(cache_mutex);
        const auto probe = cuda_link::availability(-1);
        if (!probe.device_supported) {
            return probe;
        }
        const auto key = cuda_link::self_test_cache_key(-1);
        const auto found = cache.find(key);
        if (found != cache.end() && found->second.self_test_passed) {
            return found->second;
        }
        const auto result = cuda_link::self_test(-1);
        // Only successful evidence is cached; failures are retried next time.
        if (result.self_test_passed) {
            cache[key] = result;
        }
        return result;
    }
    case ComputeBackendKind::Hip:
        return backend_availability(ComputeBackendKind::Hip);
    case ComputeBackendKind::Auto:
        break;
    }
    throw ConfigurationError("run_backend_self_test requires a concrete backend kind");
}

std::unique_ptr<DspBackend> make_dsp_backend(
    const DspBackendSelectionOptions& selection,
    DspOptions options
) {
    validate(selection);
    switch (selection.preference) {
    case ComputeBackendKind::Cpu: {
        auto backend = make_cpu_dsp_backend(std::move(options));
        return backend;
    }
    case ComputeBackendKind::Hip:
        throw BackendUnavailableError(unavailable_details(ComputeBackendKind::Hip));
    case ComputeBackendKind::Cuda: {
        // Forced CUDA checks the requested device directly (fresh, not the
        // policy-cached default-device self-test).
        const auto availability = cuda_link::self_test(selection.device_id);
        if (!availability.self_test_passed) {
            throw BackendUnavailableError(
                "forced CUDA is unavailable: " + availability.reason_code + " (" +
                availability.details + ")"
            );
        }
        if (selection.allow_runtime_fallback) {
            return std::make_unique<FailoverDspBackend>(
                [options, selection] {
                    return cuda_link::make_backend(
                        options,
                        selection.device_id,
                        selection.plan_cache_capacity
                    );
                },
                std::move(options),
                true,
                selection.preference
            );
        }
        return cuda_link::make_backend(
            options,
            selection.device_id,
            selection.plan_cache_capacity
        );
    }
    case ComputeBackendKind::Auto:
        // No validated A/B evidence is committed yet; CPU is the safe policy.
        return make_cpu_dsp_backend(std::move(options));
    }
    throw ConfigurationError("unknown backend preference");
}

}  // namespace sdr_core
