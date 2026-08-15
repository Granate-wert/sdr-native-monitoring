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

class OfficialHackrfRxPort final : public HackrfRxRuntimePort {
public:
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
        return status;
    }

    int open_exactly_one_hackrf_one() noexcept override {
        if (!initialized_ || device_ != nullptr) {
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
        }
        hackrf_device_list_free(list);
        if (status != HACKRF_SUCCESS) {
            device_ = nullptr;
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
        if (device_ == nullptr || streaming_ || callback == nullptr) {
            return invalid_state_status;
        }
        callback_ = callback;
        callback_context_ = context;
        const auto status = hackrf_start_rx(device_, &OfficialHackrfRxPort::trampoline, this);
        streaming_ = status == HACKRF_SUCCESS;
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
        const auto status = hackrf_stop_rx(device_);
        if (status == HACKRF_SUCCESS) {
            streaming_ = false;
            callback_ = nullptr;
            callback_context_ = nullptr;
        }
        return status;
    }

    int close_device() noexcept override {
        if (streaming_ || device_ == nullptr) {
            return invalid_state_status;
        }
        const auto status = hackrf_close(device_);
        if (status == HACKRF_SUCCESS) {
            device_ = nullptr;
        }
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
};

}  // namespace

std::unique_ptr<HackrfRxRuntimePort> make_official_hackrf_rx_port() {
    return std::make_unique<OfficialHackrfRxPort>();
}

}  // namespace sdr_hackrf
