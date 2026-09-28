#pragma once

// Test-only subset, not a vendor ABI header or a loadable hardware runtime.
// The real official port is compiled against these fault-injection symbols.
#include <cstddef>
#include <cstdint>

constexpr int HACKRF_SUCCESS = 0;
constexpr int HACKRF_ERROR_NOT_FOUND = -5;
constexpr int USB_BOARD_ID_HACKRF_ONE = 0x6089;

struct hackrf_device;
struct hackrf_device_list_t {
    int* usb_board_ids;
    int devicecount;
};
struct read_partid_serialno_t {
    std::uint32_t part_id[2];
    std::uint32_t serial_no[4];
};
struct hackrf_transfer {
    void* rx_ctx;
    std::uint8_t* buffer;
    int valid_length;
};
using hackrf_sample_block_cb_fn = int (*)(hackrf_transfer*);
enum sweep_style { LINEAR = 0, INTERLEAVED = 1 };

extern "C" {
int hackrf_init();
int hackrf_exit();
hackrf_device_list_t* hackrf_device_list();
void hackrf_device_list_free(hackrf_device_list_t*);
int hackrf_device_list_open(hackrf_device_list_t*, int, hackrf_device**);
int hackrf_board_partid_serialno_read(hackrf_device*, read_partid_serialno_t*);
int hackrf_usb_api_version_read(hackrf_device*, std::uint16_t*);
std::size_t hackrf_get_transfer_buffer_size(hackrf_device*);
int hackrf_set_sample_rate(hackrf_device*, double);
int hackrf_set_baseband_filter_bandwidth(hackrf_device*, std::uint32_t);
int hackrf_set_freq(hackrf_device*, std::uint64_t);
int hackrf_set_amp_enable(hackrf_device*, std::uint8_t);
int hackrf_set_antenna_enable(hackrf_device*, std::uint8_t);
int hackrf_set_lna_gain(hackrf_device*, std::uint32_t);
int hackrf_set_vga_gain(hackrf_device*, std::uint32_t);
int hackrf_start_rx(hackrf_device*, hackrf_sample_block_cb_fn, void*);
int hackrf_init_sweep(hackrf_device*, const std::uint16_t*, int,
                      std::uint32_t, std::uint32_t, std::uint32_t, sweep_style);
int hackrf_start_rx_sweep(hackrf_device*, hackrf_sample_block_cb_fn, void*);
int hackrf_stop_rx(hackrf_device*);
int hackrf_close(hackrf_device*);
}
