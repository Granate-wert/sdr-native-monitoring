#include "sdr_rtlsdr/rtl_runtime.hpp"

#include "sdr_core/configuration.hpp"
#include "sdr_core/errors.hpp"

#include <algorithm>
#include <atomic>
#include <condition_variable>
#include <deque>
#include <exception>
#include <limits>
#include <mutex>
#include <thread>
#include <utility>
#include <vector>

namespace sdr_rtlsdr {
namespace {

constexpr std::uint32_t input_bytes = 32'768U;
constexpr std::uint32_t input_samples = input_bytes / 2U;
constexpr auto component_budget = 64U * 1024U * 1024U;
std::atomic<bool> process_rtl_quarantined{};
std::atomic<bool> process_rtl_owner_active{};
std::atomic<std::uint64_t> next_session_epoch{1U};

[[nodiscard]] bool serial_is_unique_claim(const std::string& value) {
    if (value.size() < 4U || value.size() > 64U || value == "00000001") {
        return false;
    }
    return std::all_of(value.begin(), value.end(), [](const unsigned char character) {
        return character >= 33U && character <= 126U;
    });
}

[[nodiscard]] bool valid_route_string(const std::string& value) {
    return !value.empty() && value.size() < 256U && value.find('\0') == std::string::npos;
}

[[nodiscard]] std::int64_t steady_now_ns() noexcept {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

}  // namespace

void quarantine_rtl_process() noexcept {
    process_rtl_quarantined.store(true, std::memory_order_release);
}

bool rtl_process_quarantined() noexcept {
    return process_rtl_quarantined.load(std::memory_order_acquire);
}

bool try_acquire_rtl_process_lease() noexcept {
    if (process_rtl_quarantined.load(std::memory_order_acquire)) return false;
    bool available = false;
    if (!process_rtl_owner_active.compare_exchange_strong(
            available, true, std::memory_order_acq_rel, std::memory_order_acquire)) return false;
    if (process_rtl_quarantined.load(std::memory_order_acquire)) {
        process_rtl_owner_active.store(false, std::memory_order_release);
        return false;
    }
    return true;
}

void release_rtl_process_lease() noexcept {
    process_rtl_owner_active.store(false, std::memory_order_release);
}

void validate_rtl_profile(const RtlProfile& profile) {
    if (profile.center_hz == 0U ||
        (profile.sample_rate_hz != 2'048'000U && profile.sample_rate_hz != 2'400'000U) ||
        (profile.fft_size != 1024U && profile.fft_size != 2048U && profile.fft_size != 4096U) ||
        profile.hop_size != profile.fft_size / 2U ||
        (profile.detector != sdr_core::DetectorType::Sample &&
         profile.detector != sdr_core::DetectorType::Peak) ||
        profile.slot_count < 2U || profile.slot_count > 32U ||
        profile.ready_capacity == 0U || profile.ready_capacity >= profile.slot_count ||
        profile.presentation_capacity == 0U || profile.presentation_capacity > 64U ||
        profile.configuration_generation == 0U ||
        profile.source_id.empty() || profile.source_id.size() > 128U ||
        profile.source_id.find_first_of("\\/") != std::string::npos ||
        profile.source_id.find("usb:") != std::string::npos ||
        profile.source_id.find("ip:") != std::string::npos) {
        throw sdr_core::ConfigurationError("RTL RX profile is outside its typed bounds or unique-serial policy");
    }
    if (profile.session_route.has_value()) {
        const auto& route = *profile.session_route;
        if (!profile.expected_unique_serial.empty() || !valid_route_string(route.manufacturer) ||
            !valid_route_string(route.product) || !valid_route_string(route.serial) ||
            route.tuner_type == 0U || route.tuner_type > 6U || route.selection_revision == 0U) {
            throw sdr_core::ConfigurationError("RTL session route must be exact, selected and uncalibrated");
        }
    } else if (!serial_is_unique_claim(profile.expected_unique_serial)) {
        throw sdr_core::ConfigurationError("RTL RX requires a unique serial or an explicit session route");
    }
    if (profile.manual_tuner_gain_tenth_db &&
        (*profile.manual_tuner_gain_tenth_db < -1000 || *profile.manual_tuner_gain_tenth_db > 1000)) {
        throw sdr_core::ConfigurationError("RTL manual gain outside bounded tenth-dB range");
    }
    const auto minimum_outputs = (input_samples + profile.hop_size - 1U) / profile.hop_size + 2U;
    if (profile.dsp_output_capacity < minimum_outputs || profile.dsp_output_capacity > 256U) {
        throw sdr_core::ConfigurationError("RTL analytical burst queue is too small or unbounded");
    }
    static_assert(sizeof(sdr_core::SpectrumFrame) <= 1024U,
                  "RTL inline frame metadata exceeds its existing reservation");
    const auto frame_bytes = static_cast<std::uint64_t>(profile.fft_size) * 12U + 1024U;
    // Queue-payload estimate counts the CPU DSP output queue and worker drain
    // separately; it is not a whole-process RSS or FFT scratch-space proof.
    const auto allocation = static_cast<std::uint64_t>(profile.slot_count) * input_bytes +
        static_cast<std::uint64_t>(2U * profile.dsp_output_capacity +
                                   profile.presentation_capacity) * frame_bytes +
        sdr_core::analytical_ready_reserved_bytes(profile.analytical_event_capacity);
    if (allocation > component_budget) {
        throw sdr_core::ConfigurationError("RTL component memory estimate exceeds 64 MiB");
    }
}

struct RtlRuntimeSession::Impl final {
    struct Slot {
        Slot() : bytes(input_bytes) {}
        std::vector<std::uint8_t> bytes;
        std::uint32_t length{};
        std::uint64_t sequence{};
        std::uint64_t first_sample{};
        std::int64_t host_timestamp_ns{};
    };

    Impl(std::unique_ptr<RtlRuntimePort> owned_runtime, RtlProfile requested)
        : runtime(std::move(owned_runtime)), profile(std::move(requested)),
          ready_ring(profile.ready_capacity) {
        slots.reserve(profile.slot_count);
        for (std::uint32_t index = 0U; index < profile.slot_count; ++index) {
            slots.emplace_back();
            free_indices.push_back(index);
        }
        sdr_core::DspOptions options;
        options.dc_removal = profile.dc_removal_block_mean
            ? sdr_core::DcRemovalMode::BlockMean : sdr_core::DcRemovalMode::Off;
        options.output_capacity = profile.dsp_output_capacity;
        options.analytical_event_capacity = profile.analytical_event_capacity;
        options.source.source_type = sdr_core::SourceType::LiveIq;
        options.source.source_id = profile.source_id;
        options.source.display_name = "RTL-SDR Live";
        options.source.backend_id = profile.official_unbundled_runtime
            ? "native.librtlsdr.unbundled.cpu.v1" : "native.rtlsdr.injected.cpu.v1";
        dsp = sdr_core::make_cpu_dsp_backend(std::move(options));
        sdr_core::DspConfig config;
        config.fft_size = profile.fft_size;
        config.hop_size = profile.hop_size;
        config.window = sdr_core::WindowType::Hann;
        config.detector = profile.detector;
        config.unit = sdr_core::SpectrumUnit::DbfsBin;
        config.precision_mode = sdr_core::PrecisionMode::ReferenceF64;
        config.calibration_status = sdr_core::CalibrationStatus::Uncalibrated;
        sdr_core::validate(config);
        dsp->configure(config);
        dsp->enable_owner_presentation();
    }

    ~Impl() {
        if (owns_process_lease) {
            release_rtl_process_lease();
        }
    }

    std::unique_ptr<RtlRuntimePort> runtime;
    RtlProfile profile;
    std::unique_ptr<sdr_core::DspBackend> dsp;
    std::vector<Slot> slots;
    std::vector<std::uint32_t> free_indices;
    std::vector<std::uint32_t> ready_ring;
    std::uint32_t ready_head{};
    std::uint32_t ready_tail{};
    std::uint32_t ready_count{};
    mutable std::mutex slot_mutex;
    std::condition_variable slot_cv;
    std::uint32_t high_water{};
    std::atomic<bool> admission_closed{};
    std::atomic<std::uint64_t> callbacks{};
    std::atomic<std::uint64_t> malformed_callbacks{};
    std::atomic<std::uint64_t> callbacks_after_stop{};
    std::atomic<std::uint64_t> host_dropped_blocks{};
    std::atomic<std::uint64_t> host_dropped_samples{};
    std::atomic<bool> host_loss_cardinality_unknown{};
    std::atomic<std::uint64_t> blocks_admitted{};
    std::atomic<std::uint64_t> samples_admitted{};
    std::atomic<std::uint64_t> sample_cursor{};
    std::atomic<std::uint64_t> worker_failures{};
    std::uint64_t expected_sequence{};
    std::uint64_t expected_sample{};
    std::uint64_t known_missing_blocks{};
    std::uint64_t known_missing_samples{};
    mutable std::mutex dsp_mutex;
    mutable std::mutex presentation_mutex;
    std::deque<sdr_core::SpectrumFrame> presentation;
    std::uint64_t presentation_superseded{};
    std::thread reader;
    std::thread processor;
    mutable std::mutex lifecycle_mutex;
    mutable std::mutex stop_mutex;
    std::condition_variable reader_cv;
    std::condition_variable processor_cv;
    bool reader_done{};
    bool processor_done{};
    int reader_return_status{};
    bool reader_returned_without_stop{};
    bool device_open{};
    bool close_ambiguous{};
    bool owns_process_lease{};
    std::atomic<bool> cleanup_pending{};
    std::atomic<bool> reader_started{};
    RtlAcquisitionReadback readback{};
    RtlStopResult stop_result{};
    Impl* quarantine_next{};

    static void callback(std::span<const std::uint8_t> bytes, void* context) noexcept {
        if (context == nullptr) {
            return;
        }
        auto* const state = static_cast<Impl*>(context);
        try {
            state->admit(bytes);
        } catch (...) {
            // An exception must never unwind through librtlsdr's C callback.
            state->worker_failures.fetch_add(1U, std::memory_order_relaxed);
            state->admission_closed.store(true, std::memory_order_release);
            state->slot_cv.notify_all();
        }
    }

    void admit(const std::span<const std::uint8_t> bytes) {
        const auto sequence = callbacks.fetch_add(1U, std::memory_order_relaxed);
        const auto structurally_valid = !bytes.empty() && bytes.size() <= input_bytes &&
                                        (bytes.size() & 1U) == 0U;
        // Odd byte counts do not identify a complete complex-sample loss.
        const auto samples = structurally_valid || (bytes.size() & 1U) == 0U
                                 ? static_cast<std::uint64_t>(bytes.size() / 2U) : 0U;
        const auto first = sample_cursor.fetch_add(samples, std::memory_order_relaxed);
        if (admission_closed.load(std::memory_order_acquire)) {
            callbacks_after_stop.fetch_add(1U, std::memory_order_relaxed);
            return;
        }
        if (!structurally_valid) {
            malformed_callbacks.fetch_add(1U, std::memory_order_relaxed);
            host_dropped_blocks.fetch_add(1U, std::memory_order_relaxed);
            host_dropped_samples.fetch_add(samples, std::memory_order_relaxed);
            if ((bytes.size() & 1U) != 0U || bytes.size() > input_bytes) {
                if ((bytes.size() & 1U) != 0U) {
                    host_loss_cardinality_unknown.store(true, std::memory_order_release);
                }
                // A partial I/Q pair or oversized vendor callback breaks the
                // byte-stream contract. No later callback can be treated as
                // aligned healthy RF without a new acquisition epoch.
                worker_failures.fetch_add(1U, std::memory_order_relaxed);
                admission_closed.store(true, std::memory_order_release);
                slot_cv.notify_all();
            }
            return;
        }
        std::unique_lock lock(slot_mutex, std::try_to_lock);
        if (!lock.owns_lock() || free_indices.empty() || ready_count >= profile.ready_capacity) {
            host_dropped_blocks.fetch_add(1U, std::memory_order_relaxed);
            host_dropped_samples.fetch_add(samples, std::memory_order_relaxed);
            return;
        }
        const auto index = free_indices.back();
        free_indices.pop_back();
        auto& slot = slots[index];
        slot.bytes.resize(bytes.size());  // preallocated capacity remains 32 KiB
        slot.length = static_cast<std::uint32_t>(bytes.size());
        slot.sequence = sequence;
        slot.first_sample = first;
        slot.host_timestamp_ns = steady_now_ns();
        // CU8 midpoint 128 becomes signed CI8 zero; this is an uncalibrated
        // digital conversion, not an analog/RF power calibration.
        for (std::size_t offset = 0U; offset < bytes.size(); ++offset) {
            slot.bytes[offset] = static_cast<std::uint8_t>(
                static_cast<unsigned int>(bytes[offset]) - 128U);
        }
        ready_ring[ready_tail] = index;
        ready_tail = (ready_tail + 1U) % profile.ready_capacity;
        ++ready_count;
        high_water = std::max(high_water, ready_count);
        blocks_admitted.fetch_add(1U, std::memory_order_relaxed);
        samples_admitted.fetch_add(samples, std::memory_order_relaxed);
        lock.unlock();
        slot_cv.notify_one();
    }

    void process() noexcept {
        try {
            for (;;) {
                std::uint32_t index{};
                {
                    std::unique_lock lock(slot_mutex);
                    slot_cv.wait(lock, [this] {
                        return admission_closed.load(std::memory_order_acquire) || ready_count != 0U;
                    });
                    if (ready_count == 0U) {
                        break;
                    }
                    index = ready_ring[ready_head];
                    ready_head = (ready_head + 1U) % profile.ready_capacity;
                    --ready_count;
                }
                const auto& slot = slots[index];
                auto* const buffer = &slot.bytes;
                const auto release = [this, index](const std::vector<std::uint8_t>*) noexcept {
                    std::lock_guard lock(slot_mutex);
                    free_indices.push_back(index);
                };
                sdr_core::SharedBuffer samples(buffer, release);
                sdr_core::IqBlock block;
                block.source_sequence = slot.sequence;
                block.first_sample_index = slot.first_sample;
                block.timestamp_ns = slot.host_timestamp_ns;
                block.center_frequency_hz = profile.center_hz;
                block.sample_rate_hz = profile.sample_rate_hz;
                block.sample_format = sdr_core::SampleFormat::ComplexInt8Interleaved;
                block.sample_count = slot.length / 2U;
                block.flags = sdr_core::QualityFlag::Uncalibrated |
                              sdr_core::QualityFlag::TimestampEstimated;
                const auto sequence_gap = slot.sequence != expected_sequence;
                const auto sample_gap = slot.first_sample != expected_sample;
                if (sequence_gap || sample_gap) {
                    block.flags = block.flags | sdr_core::QualityFlag::IqDropped;
                    if (slot.sequence > expected_sequence) {
                        known_missing_blocks += slot.sequence - expected_sequence;
                    }
                    if (slot.first_sample > expected_sample) {
                        known_missing_samples += slot.first_sample - expected_sample;
                    }
                }
                expected_sequence = slot.sequence + 1U;
                expected_sample = slot.first_sample + block.sample_count;
                block.samples = std::move(samples);
                block.config_generation = profile.configuration_generation;
                std::vector<sdr_core::SpectrumFrame> frames;
                {
                    std::lock_guard lock(dsp_mutex);
                    dsp->push_iq(block);
                    frames = dsp->poll_spectrum(0U, false);
                }
                std::lock_guard lock(presentation_mutex);
                for (auto& frame : frames) {
                    frame.dropped_iq_blocks_before = known_missing_blocks;
                    frame.dropped_samples_before = known_missing_samples;
                    if (known_missing_blocks != 0U || known_missing_samples != 0U) {
                        frame.quality_flags = frame.quality_flags | sdr_core::QualityFlag::IqDropped;
                    }
                    if (presentation.size() == profile.presentation_capacity) {
                        if (presentation.front().analytical_ready)
                            dsp->record_owner_presentation(*presentation.front().analytical_ready,
                                sdr_core::OwnerPresentationDisposition::Superseded);
                        presentation.pop_front();
                        ++presentation_superseded;
                    }
                    presentation.push_back(std::move(frame));
                }
            }
        } catch (...) {
            worker_failures.fetch_add(1U, std::memory_order_relaxed);
            admission_closed.store(true, std::memory_order_release);
            // The worker failed after consuming its current lease. Unread
            // slots are explicitly abandoned; they are not device loss.
            {
                std::lock_guard lock(slot_mutex);
                while (ready_count != 0U) {
                    const auto index = ready_ring[ready_head];
                    ready_head = (ready_head + 1U) % profile.ready_capacity;
                    --ready_count;
                    host_dropped_blocks.fetch_add(1U, std::memory_order_relaxed);
                    host_dropped_samples.fetch_add(slots[index].length / 2U, std::memory_order_relaxed);
                    free_indices.push_back(index);
                }
            }
            slot_cv.notify_all();
        }
        {
            std::lock_guard lock(lifecycle_mutex);
            processor_done = true;
        }
        processor_cv.notify_all();
    }
};

RtlRuntimeSession::RtlRuntimeSession(std::unique_ptr<Impl> impl) noexcept : impl_(std::move(impl)) {}

RtlRuntimeSession::~RtlRuntimeSession() {
    if (impl_ != nullptr && (impl_->reader.joinable() || impl_->processor.joinable() || impl_->device_open)) {
        const auto result = stop(std::chrono::seconds(5));
        // An SDK timeout or ambiguous close must not kill the whole product
        // or free storage still used by a C callback. Keep the exact owner,
        // threads and SDK handle in a process-lifetime quarantine, and bar
        // all subsequent RTL Starts. No thread is detached or vendor pointer
        // is closed again under uncertain ownership.
        if (!result.complete()) {
            process_rtl_quarantined.store(true, std::memory_order_release);
            static std::atomic<Impl*> quarantine_head{};
            auto* retained = impl_.release();
            auto* prior = quarantine_head.load(std::memory_order_relaxed);
            do {
                retained->quarantine_next = prior;
            } while (!quarantine_head.compare_exchange_weak(
                prior, retained, std::memory_order_release, std::memory_order_relaxed));
        }
    }
}

std::unique_ptr<RtlRuntimeSession> RtlRuntimeSession::start(
    std::unique_ptr<RtlRuntimePort> runtime, RtlProfile profile) {
    validate_rtl_profile(profile);
    if (process_rtl_quarantined.load(std::memory_order_acquire)) {
        throw sdr_core::DeviceError("RTL receiver is quarantined after incomplete cleanup");
    }
    if (!runtime) {
        throw sdr_core::ConfigurationError("RTL runtime port is required");
    }
    auto impl = std::make_unique<Impl>(std::move(runtime), std::move(profile));
    auto session = std::unique_ptr<RtlRuntimeSession>(new RtlRuntimeSession(std::move(impl)));
    auto& state = *session->impl_;
    if (!try_acquire_rtl_process_lease()) {
        throw sdr_core::DeviceError("another RTL receiver owner is active");
    }
    state.owns_process_lease = true;
    if (process_rtl_quarantined.load(std::memory_order_acquire)) {
        throw sdr_core::DeviceError("RTL receiver is quarantined after incomplete cleanup");
    }
    const auto open_status = state.profile.session_route.has_value() ?
        state.runtime->open_selected_session_route(*state.profile.session_route) :
        state.runtime->open_exact_unique_serial(state.profile.expected_unique_serial);
    if (open_status != 0) {
        throw sdr_core::DeviceError("RTL exact-device open failed; native status=" +
                                    std::to_string(open_status));
    }
    state.device_open = true;
    state.cleanup_pending.store(true, std::memory_order_release);
    auto configuration_status = state.profile.manual_tuner_gain_tenth_db ?
        state.runtime->set_manual_tuner_gain(*state.profile.manual_tuner_gain_tenth_db) :
        state.runtime->set_automatic_tuner_gain();
    std::optional<int> cached_gain;
    if (configuration_status == 0) configuration_status = state.runtime->set_sample_rate(state.profile.sample_rate_hz);
    if (configuration_status == 0 && state.runtime->get_sample_rate() != state.profile.sample_rate_hz) configuration_status = -101;
    if (configuration_status == 0) configuration_status = state.runtime->set_center_frequency(state.profile.center_hz);
    if (configuration_status == 0 && state.runtime->get_center_frequency() != state.profile.center_hz) configuration_status = -102;
    if (configuration_status == 0) configuration_status = state.runtime->reset_buffer();
    if (configuration_status == 0) configuration_status = state.runtime->verify_normal_tuner_mode();
    if (configuration_status == 0 && state.profile.manual_tuner_gain_tenth_db) {
        cached_gain = state.runtime->get_cached_tuner_gain();
        if (cached_gain != state.profile.manual_tuner_gain_tenth_db) configuration_status = -106;
    }
    if (configuration_status != 0) {
        state.stop_result.close_called = true;
        state.stop_result.close_status = state.runtime->close();
        if (state.stop_result.close_status == 0) {
            state.device_open = false;
            state.cleanup_pending.store(false, std::memory_order_release);
            throw sdr_core::DeviceError("RTL configuration/readback failed before RX; native status=" +
                                        std::to_string(configuration_status));
        }
        // Close failed with unknown ownership semantics. Retain the faulted
        // owner for diagnosis; a second close or fresh Start is forbidden.
        // Start itself must not return a non-running session as success.
        state.close_ambiguous = true;
        process_rtl_quarantined.store(true, std::memory_order_release);
        throw sdr_core::DeviceError(
            "RTL configuration/readback failed before RX; native status=" +
            std::to_string(configuration_status) + "; close status=" +
            std::to_string(state.stop_result.close_status) + "; owner quarantined");
    }
    state.readback = RtlAcquisitionReadback{
        next_session_epoch.fetch_add(1U, std::memory_order_acq_rel),
        state.profile.sample_rate_hz, state.profile.center_hz, cached_gain.has_value(), cached_gain};
    state.processor = std::thread([&state] { state.process(); });
    state.reader = std::thread([&state] {
        const auto status = state.runtime->read_async(&Impl::callback, &state, input_bytes);
        const auto unexpected = !state.admission_closed.load(std::memory_order_acquire);
        if (unexpected) {
            state.worker_failures.fetch_add(1U, std::memory_order_relaxed);
        }
        state.admission_closed.store(true, std::memory_order_release);
        state.slot_cv.notify_all();
        {
            std::lock_guard lock(state.lifecycle_mutex);
            state.reader_done = true;
            state.reader_return_status = status;
            state.reader_returned_without_stop = unexpected;
        }
        state.reader_cv.notify_all();
    });
    state.reader_started.store(true, std::memory_order_release);
    return session;
}

RtlLatestFrame RtlRuntimeSession::drain_latest_spectrum_frame() {
    std::lock_guard lock(impl_->presentation_mutex);
    RtlLatestFrame result;
    if (!impl_->presentation.empty()) {
        result.coalesced_frames = static_cast<std::uint32_t>(impl_->presentation.size() - 1U);
        for (std::size_t index = 0; index + 1U < impl_->presentation.size(); ++index) {
            const auto& frame = impl_->presentation[index];
            if (frame.analytical_ready) impl_->dsp->record_owner_presentation(*frame.analytical_ready,
                sdr_core::OwnerPresentationDisposition::Coalesced);
        }
        if (impl_->presentation.back().analytical_ready)
            impl_->dsp->record_owner_presentation(*impl_->presentation.back().analytical_ready,
                sdr_core::OwnerPresentationDisposition::Forwarded);
        result.frame = std::move(impl_->presentation.back());
        impl_->presentation.clear();
    }
    return result;
}

RtlMetrics RtlRuntimeSession::metrics() const {
    const auto& state = *impl_;
    RtlMetrics result;
    result.callbacks = state.callbacks.load(std::memory_order_relaxed);
    result.malformed_callbacks = state.malformed_callbacks.load(std::memory_order_relaxed);
    result.callbacks_after_stop = state.callbacks_after_stop.load(std::memory_order_relaxed);
    result.host_input_blocks_dropped = state.host_dropped_blocks.load(std::memory_order_relaxed);
    result.host_input_samples_dropped = state.host_dropped_samples.load(std::memory_order_relaxed);
    result.host_loss_cardinality_unknown =
        state.host_loss_cardinality_unknown.load(std::memory_order_acquire);
    result.blocks_admitted = state.blocks_admitted.load(std::memory_order_relaxed);
    result.samples_admitted = state.samples_admitted.load(std::memory_order_relaxed);
    result.worker_failures = state.worker_failures.load(std::memory_order_relaxed);
    {
        std::lock_guard lock(state.lifecycle_mutex);
        result.reader_returned = state.reader_done;
        result.reader_return_status = state.reader_return_status;
        result.reader_returned_without_stop = state.reader_returned_without_stop;
    }
    {
        std::lock_guard lock(state.slot_mutex);
        result.ready_depth = state.ready_count;
        result.ready_high_water = state.high_water;
        result.slots_in_use = state.profile.slot_count - static_cast<std::uint32_t>(state.free_indices.size());
    }
    {
        std::lock_guard lock(state.presentation_mutex);
        result.presentation_frames_superseded = state.presentation_superseded;
    }
    {
        std::lock_guard lock(state.dsp_mutex);
        result.dsp = state.dsp->metrics();
    }
    return result;
}

RtlStopResult RtlRuntimeSession::stop(const std::chrono::milliseconds timeout) noexcept {
    auto& state = *impl_;
    std::lock_guard stop_lock(state.stop_mutex);
    state.admission_closed.store(true, std::memory_order_release);
    state.slot_cv.notify_all();
    const auto deadline = std::chrono::steady_clock::now() + std::max(timeout, std::chrono::milliseconds(0));
    if (state.reader.joinable()) {
        // librtlsdr returns -2 when cancel precedes read_async's RUNNING
        // transition. Repeat only this bounded cancellation handshake until
        // the reader confirms exit; never close or detach a live callback.
        for (;;) {
            {
                std::lock_guard lock(state.lifecycle_mutex);
                if (state.reader_done) break;
            }
            state.stop_result.cancel_requested = true;
            state.stop_result.cancel_status = state.runtime->cancel_async();
            if (state.stop_result.cancel_status != 0 && state.stop_result.cancel_status != -2 &&
                state.stop_result.first_cancel_error_status == 0) {
                state.stop_result.first_cancel_error_status = state.stop_result.cancel_status;
            }
            std::unique_lock lock(state.lifecycle_mutex);
            if (state.reader_cv.wait_until(lock, std::min(deadline,
                    std::chrono::steady_clock::now() + std::chrono::milliseconds(2)),
                    [&state] { return state.reader_done; })) break;
            if (std::chrono::steady_clock::now() >= deadline) {
                process_rtl_quarantined.store(true, std::memory_order_release);
                return state.stop_result;
            }
        }
        state.reader.join();
        state.stop_result.reader_joined = true;
    } else {
        state.stop_result.reader_joined = true;
    }
    if (state.processor.joinable()) {
        std::unique_lock lock(state.lifecycle_mutex);
        if (!state.processor_cv.wait_until(lock, deadline, [&state] { return state.processor_done; })) {
            process_rtl_quarantined.store(true, std::memory_order_release);
            return state.stop_result;
        }
        lock.unlock();
        state.processor.join();
    }
    state.stop_result.dsp_joined = true;
    if (state.device_open && !state.close_ambiguous) {
        state.stop_result.close_called = true;
        state.stop_result.close_status = state.runtime->close();
        if (state.stop_result.close_status != 0) {
            // The vendor's nonzero close ownership semantics are unknown.
            // Do not retry or destroy a potentially consumed device pointer.
            state.close_ambiguous = true;
            process_rtl_quarantined.store(true, std::memory_order_release);
            return state.stop_result;
        }
        state.device_open = false;
        state.cleanup_pending.store(false, std::memory_order_release);
    }
    if (!state.device_open) {
        state.stop_result.close_called = true;
        state.stop_result.close_status = 0;
    }
    if (state.stop_result.complete() && state.owns_process_lease) {
        state.owns_process_lease = false;
        release_rtl_process_lease();
    }
    return state.stop_result;
}

std::uint64_t RtlRuntimeSession::discard_terminal_spectrum_frames() {
    auto& state = *impl_;
    std::lock_guard stop_lock(state.stop_mutex);
    if (!state.stop_result.complete() || state.reader.joinable() || state.processor.joinable() ||
        state.cleanup_pending.load(std::memory_order_acquire) || state.close_ambiguous)
        throw sdr_core::ConfigurationError("terminal presentation release requires complete RTL Stop/join/close");
    std::lock_guard lock(state.presentation_mutex);
    const auto count = static_cast<std::uint64_t>(state.presentation.size());
    for (const auto& frame : state.presentation) {
        if (frame.analytical_ready) state.dsp->record_owner_presentation(*frame.analytical_ready,
            sdr_core::OwnerPresentationDisposition::Cancelled);
    }
    state.presentation.clear();
    return count;
}

bool RtlRuntimeSession::running() const noexcept {
    if (!impl_->reader_started.load(std::memory_order_acquire)) return false;
    std::lock_guard lock(impl_->lifecycle_mutex);
    return !impl_->reader_done && !impl_->admission_closed.load(std::memory_order_acquire);
}

bool RtlRuntimeSession::cleanup_required() const noexcept {
    return impl_->cleanup_pending.load(std::memory_order_acquire);
}

RtlAcquisitionReadback RtlRuntimeSession::readback() const noexcept {
    return impl_->readback;
}

sdr_core::AnalyticalReadyDrain RtlRuntimeSession::drain_analytical_ready_events(std::size_t max_items) {
    return impl_->dsp->drain_analytical_ready(max_items);
}
}  // namespace sdr_rtlsdr
