#include "sdr_hackrf/hackrf_official_rx_port.hpp"

#include <hackrf.h>

#include <algorithm>
#include <array>
#include <chrono>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

struct hackrf_device {};

namespace {
struct State {
    hackrf_device* live_device{};
    int close_status{};
    int identity_status{};
    int stop_status{};
    std::size_t transfer_buffer_size{32U};
    int close_calls{};
    int exit_calls{};
    int configure_calls{};
    int start_calls{};
    int stop_calls{};
    int invalid_close_calls{};
};
State state;
int board_id = USB_BOARD_ID_HACKRF_ONE;
hackrf_device_list_t device_list{&board_id, 1};

void expect(bool condition, const std::string& message) {
    if (!condition) {
        throw std::runtime_error(message);
    }
}

void test_consumed_close_error_is_returned_once_and_not_retried() {
    for (const int status : {0, -1001}) {
        state = {};
        state.close_status = status;
        auto port = sdr_hackrf::make_official_hackrf_rx_port();
        expect(port->initialize_library() == 0, "init");
        expect(port->open_exactly_one_hackrf_one() == 0, "open");
        expect(port->close_device() == status, "first SDK error is preserved");
        expect(state.live_device == nullptr, "SDK consumed the device on any return");
        expect(port->close_device() == 0, "explicit cleanup resumes after consumed close");
        expect(port->open_exactly_one_hackrf_one() != 0, "no reopen over begun release");
        expect(port->exit_library() == 0, "exit after consumed close");
        port.reset();
        expect(state.close_calls == 1 && state.invalid_close_calls == 0, "no double close");
        expect(state.exit_calls == 1, "one exit");
    }
}

void test_identity_failure_destructor_never_recloses_consumed_pointer() {
    for (const int read_status : {0, -1000}) {
        state = {};
        state.identity_status = read_status;
        state.close_status = -1001;
        auto port = sdr_hackrf::make_official_hackrf_rx_port(
            std::array<std::uint32_t, 4>{1, 2, 3, 4}
        );
        expect(port->initialize_library() == 0, "init");
        expect(port->open_exactly_one_hackrf_one() == (read_status == 0 ? -30004 : read_status),
               "identity mismatch/read error before Configure/RX");
        port.reset();
        expect(state.close_calls == 1 && state.invalid_close_calls == 0, "destructor no double close");
        expect(state.exit_calls == 1, "destructor exits consumed owner");
        expect(state.configure_calls == 0 && state.start_calls == 0, "no RF/RX on identity error");
    }
}

void test_session_stop_recovery_releases_library_without_reclosing_device() {
    state = {};
    state.close_status = -1001;
    sdr_hackrf::HackrfRxProfile profile{};
    profile.center_frequency_hz = 100'000'000.0;
    profile.sample_rate_hz = 10'000'000.0;
    profile.baseband_filter_hz = 8'000'000;
    auto session = sdr_hackrf::HackrfRxSession::start(
        sdr_hackrf::make_official_hackrf_rx_port(), profile
    );
    const auto first = session->stop(std::chrono::milliseconds(120));
    expect(!first.complete() && first.close_status == -1001, "first Stop exposes close error");
    expect(state.close_calls == 1 && state.exit_calls == 0, "cleanup waits for explicit retry");
    expect(session->stop(std::chrono::milliseconds(120)).complete(), "explicit Stop completes cleanup");
    session.reset();
    expect(state.close_calls == 1 && state.invalid_close_calls == 0, "session no double close");
    expect(state.exit_calls == 1 && state.stop_calls == 1, "completed Stop/exit not repeated");
}

void test_sweep_stop_command_error_closes_without_impossible_sdk_retry() {
    state = {};
    state.stop_status = -1002;
    state.transfer_buffer_size = 262'144U;
    auto profile = sdr_hackrf::HackrfSweepProfile{};
    profile.sequence.ranges = {{100U, 120U}};
    auto session = sdr_hackrf::HackrfSweepSession::start(
        sdr_hackrf::make_official_hackrf_sweep_port(
            std::array<std::uint32_t, 4>{0U, 0U, 0U, 0U}
        ), profile
    );
    const auto result = session->stop(std::chrono::milliseconds(100));
    expect(result.complete() && !result.clean() && result.stop_rx_status == -1002,
           "Sweep RF-off error was masked or stranded the owner");
    session.reset();
    expect(state.stop_calls == 1 && state.close_calls == 1 &&
               state.exit_calls == 1 && state.invalid_close_calls == 0,
           "failed Stop retried consumed SDK state or double-closed");
}
}  // namespace

extern "C" {
int hackrf_init() { return 0; }
int hackrf_exit() { ++state.exit_calls; return state.live_device == nullptr ? 0 : -2000; }
hackrf_device_list_t* hackrf_device_list() { return &device_list; }
void hackrf_device_list_free(hackrf_device_list_t*) {}
int hackrf_device_list_open(hackrf_device_list_t*, int, hackrf_device** output) {
    state.live_device = new hackrf_device;
    *output = state.live_device;
    return 0;
}
int hackrf_board_partid_serialno_read(hackrf_device*, read_partid_serialno_t* output) {
    std::fill(std::begin(output->serial_no), std::end(output->serial_no), 0U);
    return state.identity_status;
}
int hackrf_usb_api_version_read(hackrf_device*, std::uint16_t* output) {
    *output = 0x0104U;
    return 0;
}
std::size_t hackrf_get_transfer_buffer_size(hackrf_device*) {
    return state.transfer_buffer_size;
}
int hackrf_set_sample_rate(hackrf_device*, double) { ++state.configure_calls; return 0; }
int hackrf_set_baseband_filter_bandwidth(hackrf_device*, std::uint32_t) { ++state.configure_calls; return 0; }
int hackrf_set_freq(hackrf_device*, std::uint64_t) { ++state.configure_calls; return 0; }
int hackrf_set_amp_enable(hackrf_device*, std::uint8_t) { ++state.configure_calls; return 0; }
int hackrf_set_antenna_enable(hackrf_device*, std::uint8_t) { ++state.configure_calls; return 0; }
int hackrf_set_lna_gain(hackrf_device*, std::uint32_t) { ++state.configure_calls; return 0; }
int hackrf_set_vga_gain(hackrf_device*, std::uint32_t) { ++state.configure_calls; return 0; }
int hackrf_start_rx(hackrf_device*, hackrf_sample_block_cb_fn, void*) { ++state.start_calls; return 0; }
int hackrf_init_sweep(hackrf_device*, const std::uint16_t*, int,
                      std::uint32_t, std::uint32_t, std::uint32_t, sweep_style) {
    ++state.configure_calls;
    return 0;
}
int hackrf_start_rx_sweep(hackrf_device*, hackrf_sample_block_cb_fn, void*) {
    ++state.start_calls;
    return 0;
}
int hackrf_stop_rx(hackrf_device*) { ++state.stop_calls; return state.stop_status; }
int hackrf_close(hackrf_device* device) {
    ++state.close_calls;
    if (state.live_device == nullptr || device != state.live_device) {
        ++state.invalid_close_calls;
        return -9999;
    }
    // Model official SDK behavior, not the former incorrect keep-on-error fake:
    // the pointer is consumed even when the stop command/thread status is red.
    delete state.live_device;
    state.live_device = nullptr;
    return state.close_status;
}
}

int main() {
    try {
        test_consumed_close_error_is_returned_once_and_not_retried();
        test_identity_failure_destructor_never_recloses_consumed_pointer();
        test_session_stop_recovery_releases_library_without_reclosing_device();
        test_sweep_stop_command_error_closes_without_impossible_sdk_retry();
        std::cout << "official close consumption/identity/session retry PASS\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
