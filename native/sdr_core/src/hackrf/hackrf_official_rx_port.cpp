#include "sdr_hackrf/hackrf_official_rx_port.hpp"

#include <hackrf.h>

#include <chrono>
#include <cstdint>
#include <limits>
#include <memory>
#include <span>

namespace sdr_hackrf {
namespace {

constexpr int wrong_device_count_status = -30'001;
constexpr int wrong_device_kind_status = -30'002;
constexpr int invalid_state_status = -30'003;
constexpr int wrong_device_identity_status = -30'004;

class OfficialHackrfRxPort final : public HackrfSweepRuntimePort {
public:
    explicit OfficialHackrfRxPort(std::optional<std::array<std::uint32_t, 4>> expected_serial_words)
        : expected_serial_words_(expected_serial_words) {}

    ~OfficialHackrfRxPort() override {
        if (streaming_) {
            static_cast<void>(stop_rx());
        }
        if (device_ != nullptr) {
            static_cast<void>(close_device());
        }
        if (initialized_) {
            static_cast<void>(exit_library());
        }
    }

    int initialize_library() noexcept override {
        if (initialized_) {
            return invalid_state_status;
        }
        const auto status = hackrf_init();
        initialized_ = status == HACKRF_SUCCESS;
        if (initialized_) {
            device_close_consumed_ = false;
            stop_attempted_ = false;
        }
        return status;
    }

    int open_exactly_one_hackrf_one() noexcept override {
        if (!initialized_ || device_ != nullptr || device_close_consumed_) {
            return invalid_state_status;
        }
        auto* const list = hackrf_device_list();
        if (list == nullptr) {
            return HACKRF_ERROR_NOT_FOUND;
        }
        int status = HACKRF_SUCCESS;
        if (list->devicecount != 1) {
            status = wrong_device_count_status;
        } else if (list->usb_board_ids == nullptr ||
                   list->usb_board_ids[0] != USB_BOARD_ID_HACKRF_ONE) {
            status = wrong_device_kind_status;
        } else {
            status = hackrf_device_list_open(list, 0, &device_);
            if (status == HACKRF_SUCCESS && device_ == nullptr) {
                status = invalid_state_status;
            }
            // Verify the SAME opened handle before returning success to the
            // session that configures RF and starts RX. Enumeration may race
            // a physical device swap; its opaque Python permit is not enough.
            if (status == HACKRF_SUCCESS && expected_serial_words_) {
                read_partid_serialno_t observed{};
                status = hackrf_board_partid_serialno_read(device_, &observed);
                if (status == HACKRF_SUCCESS) {
                    for (std::size_t index = 0; index < expected_serial_words_->size(); ++index) {
                        if (observed.serial_no[index] != (*expected_serial_words_)[index]) {
                            status = wrong_device_identity_status;
                            break;
                        }
                    }
                }
            }
        }
        hackrf_device_list_free(list);
        if (status != HACKRF_SUCCESS && device_ != nullptr) {
            // Official close consumes this handle even on an error return.
            // Destructor cleanup must never call close on it a second time.
            static_cast<void>(close_device());
        }
        return status;
    }

    std::uint32_t transfer_buffer_size() const noexcept override {
        if (device_ == nullptr) {
            return 0U;
        }
        const auto size = hackrf_get_transfer_buffer_size(device_);
        if (size > std::numeric_limits<std::uint32_t>::max()) {
            return 0U;
        }
        return static_cast<std::uint32_t>(size);
    }

    int read_usb_api_version(std::uint16_t& version) noexcept override {
        if (device_ == nullptr || streaming_) {
            return invalid_state_status;
        }
        version = 0U;
        return hackrf_usb_api_version_read(device_, &version);
    }

    int initialize_sweep(const HackrfSweepSequencePlan& plan) noexcept override {
        if (device_ == nullptr || streaming_ || plan.ranges.empty() ||
            plan.ranges.size() > hackrf_sweep_max_ranges) {
            return invalid_state_status;
        }
        std::array<std::uint16_t, 2U * hackrf_sweep_max_ranges> ranges{};
        for (std::size_t index = 0U; index < plan.ranges.size(); ++index) {
            ranges[2U * index] = plan.ranges[index].start_mhz;
            ranges[2U * index + 1U] = plan.ranges[index].stop_mhz;
        }
        const auto style = plan.style == HackrfSweepStyle::Interleaved
            ? INTERLEAVED : LINEAR;
        return hackrf_init_sweep(
            device_, ranges.data(), static_cast<int>(plan.ranges.size()),
            static_cast<std::uint32_t>(hackrf_sweep_block_bytes),
            plan.step_width_hz, plan.offset_hz, style
        );
    }

    int set_sample_rate(const double value) noexcept override {
        return device_ != nullptr ? hackrf_set_sample_rate(device_, value)
                                  : invalid_state_status;
    }

    int set_baseband_filter(const std::uint32_t value) noexcept override {
        return device_ != nullptr
            ? hackrf_set_baseband_filter_bandwidth(device_, value)
            : invalid_state_status;
    }

    int set_center_frequency(const std::uint64_t value) noexcept override {
        return device_ != nullptr ? hackrf_set_freq(device_, value)
                                  : invalid_state_status;
    }

    int set_rf_amplifier(const bool enabled) noexcept override {
        return device_ != nullptr
            ? hackrf_set_amp_enable(device_, enabled ? 1U : 0U)
            : invalid_state_status;
    }

    int set_bias_tee(const bool enabled) noexcept override {
        return device_ != nullptr
            ? hackrf_set_antenna_enable(device_, enabled ? 1U : 0U)
            : invalid_state_status;
    }

    int set_lna_gain(const std::uint32_t value) noexcept override {
        return device_ != nullptr ? hackrf_set_lna_gain(device_, value)
                                  : invalid_state_status;
    }

    int set_vga_gain(const std::uint32_t value) noexcept override {
        return device_ != nullptr ? hackrf_set_vga_gain(device_, value)
                                  : invalid_state_status;
    }

    int start_rx(HackrfRxBytesCallback callback, void* context) noexcept override {
        return start_stream(callback, context, false);
    }

    int start_rx_sweep(HackrfRxBytesCallback callback, void* context) noexcept override {
        return start_stream(callback, context, true);
    }

    int start_stream(HackrfRxBytesCallback callback, void* context,
                     const bool sweep) noexcept {
        if (device_ == nullptr || streaming_ || callback == nullptr) {
            return invalid_state_status;
        }
        callback_ = callback;
        callback_context_ = context;
        const auto status = sweep
            ? hackrf_start_rx_sweep(device_, &OfficialHackrfRxPort::trampoline, this)
            : hackrf_start_rx(device_, &OfficialHackrfRxPort::trampoline, this);
        streaming_ = status == HACKRF_SUCCESS;
        if (streaming_) {
            stop_attempted_ = false;
        }
        if (!streaming_) {
            callback_ = nullptr;
            callback_context_ = nullptr;
        }
        return status;
    }

    int stop_rx() noexcept override {
        if (!streaming_ || device_ == nullptr) {
            return invalid_state_status;
        }
        stop_attempted_ = true;
        const auto status = hackrf_stop_rx(device_);
        if (status == HACKRF_SUCCESS) {
            streaming_ = false;
            callback_ = nullptr;
            callback_context_ = nullptr;
        }
        return status;
    }

    int close_device() noexcept override {
        if (streaming_ && !stop_attempted_) {
            return invalid_state_status;
        }
        if (device_ == nullptr) {
            // An explicit session retry can finish library cleanup without
            // retrying an already consumed SDK pointer. The first close
            // error has already been returned to the caller.
            return device_close_consumed_ ? HACKRF_SUCCESS : invalid_state_status;
        }
        const auto status = hackrf_close(device_);
        device_ = nullptr;
        streaming_ = false;
        callback_ = nullptr;
        callback_context_ = nullptr;
        device_close_consumed_ = true;
        stop_attempted_ = false;
        return status;
    }

    int exit_library() noexcept override {
        if (!initialized_ || device_ != nullptr || streaming_) {
            return invalid_state_status;
        }
        const auto status = hackrf_exit();
        if (status == HACKRF_SUCCESS) {
            initialized_ = false;
        }
        return status;
    }

private:
    static int trampoline(hackrf_transfer* transfer) noexcept {
        if (transfer == nullptr || transfer->rx_ctx == nullptr) {
            return 1;
        }
        auto& self = *static_cast<OfficialHackrfRxPort*>(transfer->rx_ctx);
        if (self.callback_ == nullptr) {
            return 1;
        }
        const auto timestamp = std::chrono::duration_cast<std::chrono::nanoseconds>(
            std::chrono::steady_clock::now().time_since_epoch()
        ).count();
        if (transfer->buffer == nullptr || transfer->valid_length <= 0) {
            return self.callback_({}, timestamp, self.callback_context_);
        }
        return self.callback_(
            std::span<const std::uint8_t>(
                transfer->buffer,
                static_cast<std::size_t>(transfer->valid_length)
            ),
            timestamp,
            self.callback_context_
        );
    }

    hackrf_device* device_{};
    HackrfRxBytesCallback callback_{};
    void* callback_context_{};
    bool initialized_{};
    bool streaming_{};
    bool stop_attempted_{};
    bool device_close_consumed_{};
    std::optional<std::array<std::uint32_t, 4>> expected_serial_words_;
};

}  // namespace

std::unique_ptr<HackrfRxRuntimePort> make_official_hackrf_rx_port(
    std::optional<std::array<std::uint32_t, 4>> expected_serial_words) {
    return std::make_unique<OfficialHackrfRxPort>(expected_serial_words);
}

std::unique_ptr<HackrfSweepRuntimePort> make_official_hackrf_sweep_port(
    std::array<std::uint32_t, 4> expected_serial_words) {
    return std::make_unique<OfficialHackrfRxPort>(expected_serial_words);
}

}  // namespace sdr_hackrf
