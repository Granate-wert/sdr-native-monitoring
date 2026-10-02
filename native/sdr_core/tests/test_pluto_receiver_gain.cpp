#include "sdr_pluto/pluto_backend.hpp"
#include "sdr_core/errors.hpp"

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>

#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <string>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

struct Hooks {
    HMODULE module{LoadLibraryA(std::getenv("LIBIIO_DLL_PATH"))};
    using gain_fn = double (*)(int);
    using mode_fn = const char* (*)(int);
    using count_fn = int (*)();
    gain_fn gain{reinterpret_cast<gain_fn>(GetProcAddress(module, "mock_iio_gain"))};
    mode_fn mode{reinterpret_cast<mode_fn>(GetProcAddress(module, "mock_iio_gain_mode"))};
    count_fn mutations{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_rf_mutation_calls"))};
    count_fn buffers{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_live_buffers"))};
    count_fn contexts{reinterpret_cast<count_fn>(GetProcAddress(module, "mock_iio_live_contexts"))};
    Hooks() { require(module && gain && mode && mutations && buffers && contexts, "gain hooks missing"); }
    ~Hooks() { if (module) FreeLibrary(module); }
};

sdr_core::DeviceConfig config(double gain) {
    return {.source_id = "gain-test", .context_uri = "usb:mock",
        .center_frequency_hz = 2'450'000'000.0, .sample_rate_hz = 3'000'000.0,
        .analog_bandwidth_hz = 1'500'000.0, .gain_mode = sdr_core::GainMode::Manual,
        .manual_gain_db = gain, .channel_index = 0U, .buffer_samples = 1024U,
        .schema_version = sdr_core::contract_schema_version};
}
template<class F> void rejected(F&& call, const char* message) {
    bool refused = false;
    try { call(); } catch (const sdr_core::ConfigurationError&) { refused = true; }
    require(refused, message);
}
}

int main() {
    try {
        using Selection = sdr_pluto::ReceiverSelection;
        using Mode = sdr_core::GainMode;
        Hooks hooks;
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "1");
        {
            sdr_pluto::PlutoDevice device("usb:mock");
            const auto one = device.configure(config(17), Selection::Rx1, 4U);
            require(one.receiver_selection == Selection::Rx1 && one.receiver_gains.size() == 1 &&
                one.receiver_gains[0].receiver == Selection::Rx1 && hooks.gain(1) == 17 && hooks.gain(2) == 31,
                "RX1 must not write RX2 gain");
            const auto two = device.configure(config(43), Selection::Rx2, 4U);
            require(two.receiver_selection == Selection::Rx2 && two.receiver_gains.size() == 1 &&
                two.receiver_gains[0].receiver == Selection::Rx2 && two.manual_gain_db == 43 &&
                two.receiver_gains[0].manual_gain_db == 43 && hooks.gain(1) == 17 && hooks.gain(2) == 43,
                "RX2 scalar and per-chain readback must describe PHY RX2, not RX1");
            device.start_stream();
            require(device.refill_receivers().rx2.has_value(), "RX2 receive remains functional");
            device.stop_stream();
            auto agc = config(70);
            agc.gain_mode = Mode::FastAttack;
            const auto agc_applied = device.configure(agc, Selection::Rx2, 4U);
            require(agc_applied.gain_mode == Mode::FastAttack && agc_applied.manual_gain_db == 43 &&
                std::string(hooks.mode(1)) == "manual" && std::string(hooks.mode(2)) == "fast_attack" &&
                hooks.gain(1) == 17 && hooks.gain(2) == 43, "RX2 AGC cannot write either manual gain");
            const auto both = device.configure(config(23), Selection::Both, 4U);
            require(both.receiver_gains.size() == 2 && both.receiver_selection == Selection::Both &&
                both.receiver_gains[0].receiver == Selection::Rx1 && both.receiver_gains[1].receiver == Selection::Rx2 &&
                both.receiver_gains[0].manual_gain_db == 23 && both.receiver_gains[1].manual_gain_db == 23 &&
                hooks.gain(1) == 23 && hooks.gain(2) == 23, "BOTH must configure/read both PHY gains");
            // Different retained per-chain values make an accidental RX1-only rollback detectable.
            static_cast<void>(device.configure(config(17), Selection::Rx1, 4U));
            const auto old = device.configure(config(31), Selection::Rx2, 4U);
            auto change = config(55);
            change.sample_rate_hz = 61'440'000;
            change.analog_bandwidth_hz = 56'000'000;
            change.center_frequency_hz = 2'500'000'000;
            _putenv_s("SDR_MOCK_LIBIIO_RX2_GAIN_FAIL_AT", "55");
            rejected([&] { static_cast<void>(device.configure(change, Selection::Both, 4U)); }, "RX2 gain failure must refuse BOTH");
            require(hooks.gain(1) == 17 && hooks.gain(2) == 31 && std::string(hooks.mode(1)) == "manual" &&
                std::string(hooks.mode(2)) == "manual" && device.applied_config().config_generation == old.config_generation &&
                device.receiver_selection() == Selection::Rx2, "failed BOTH must restore both gains and previous admission");
            device.start_stream();
            const auto restored = device.refill_receivers();
            require(restored.rx2 && !restored.rx1 && restored.rx2->sample_rate_hz == old.sample_rate_hz &&
                restored.rx2->center_frequency_hz == old.center_frequency_hz, "common Fs/LO rollback must be real");
            device.stop_stream();
            _putenv_s("SDR_MOCK_LIBIIO_RX2_GAIN_FAIL_AT", "");
            _putenv_s("SDR_MOCK_LIBIIO_RX2_MODE_MISMATCH", "1");
            rejected([&] { static_cast<void>(device.configure(agc, Selection::Rx2, 4U)); }, "RX2 mode ACK without readback cannot succeed");
            _putenv_s("SDR_MOCK_LIBIIO_RX2_MODE_MISMATCH", "");
            require(device.applied_config().config_generation == old.config_generation && hooks.gain(1) == 17 &&
                hooks.gain(2) == 31 && std::string(hooks.mode(2)) == "manual", "mode mismatch rollback must retain admission");
            // Failed verification invalidates admission rather than reusing an unproven old config.
            _putenv_s("SDR_MOCK_LIBIIO_RX2_GAIN_FAIL_AT", "55");
            _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "1");
            rejected([&] { static_cast<void>(device.configure(change, Selection::Both, 4U)); }, "rollback failure must refuse");
            bool barred = false;
            try { device.start_stream(); } catch (const std::exception&) { barred = true; }
            require(barred && !device.streaming() && hooks.buffers() == 0, "unverified rollback must bar Start");
            _putenv_s("SDR_MOCK_LIBIIO_RX2_GAIN_FAIL_AT", "");
            _putenv_s("SDR_MOCK_LIBIIO_ROLLBACK_FAIL", "");
            static_cast<void>(device.configure(config(20), Selection::Rx1, 4U));
        }
        for (const char* unavailable : {"SDR_MOCK_LIBIIO_NO_RX2_PHY", "SDR_MOCK_LIBIIO_RX2_PHY_ALIAS"}) {
            _putenv_s(unavailable, "1");
            {
                sdr_pluto::PlutoDevice device("usb:mock");
                const auto old = device.configure(config(20), Selection::Rx1, 4U);
                device.start_stream();
                for (const auto selection : {Selection::Rx2, Selection::Both}) {
                    const auto before = hooks.mutations();
                    rejected([&] { static_cast<void>(device.configure(config(42), selection, 4U)); }, "missing/aliased PHY RX2 must refuse");
                    require(device.streaming() && hooks.mutations() == before && hooks.buffers() == 1 &&
                        device.applied_config().config_generation == old.config_generation,
                        "unsupported PHY cannot interrupt/mutate running RX1");
                    require(device.refill_receivers().rx1.has_value(), "RX1 remains usable after refusal");
                }
            }
            _putenv_s(unavailable, "");
        }
        _putenv_s("SDR_MOCK_LIBIIO_TOPOLOGY_DUAL", "");
        require(hooks.buffers() == 0 && hooks.contexts() == 0, "test must leave no buffers or contexts");
        std::cout << "Per-RX PHY gain isolation/readback/rollback and fail-closed admission PASS (mock only)\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
