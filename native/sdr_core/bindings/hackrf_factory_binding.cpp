#include "hackrf_binding.hpp"

#include "sdr_hackrf/hackrf_live_factory.hpp"

#include <cstdint>
#include <string>
#include <utility>

namespace py = pybind11;

namespace sdr_core::python {
namespace {

[[nodiscard]] sdr_hackrf::HackrfLiveFactoryConfig hackrf_live_factory_config(
    const double center_frequency_hz,
    const double sample_rate_hz,
    const std::uint32_t baseband_filter_hz,
    const std::uint32_t lna_gain_db,
    const std::uint32_t vga_gain_db,
    const bool rf_amplifier_enabled,
    const bool bias_tee_enabled,
    const std::uint32_t fft_size,
    const std::uint32_t hop_size,
    const sdr_core::WindowType window,
    const sdr_core::DetectorType detector,
    const std::uint32_t slot_count,
    const std::uint32_t ready_capacity,
    const std::uint32_t dsp_output_capacity,
    const std::uint32_t presentation_capacity,
    const std::uint64_t configuration_generation,
    std::string source_id
) {
    return sdr_hackrf::HackrfLiveFactoryConfig{
        .center_frequency_hz = center_frequency_hz,
        .sample_rate_hz = sample_rate_hz,
        .baseband_filter_hz = baseband_filter_hz,
        .lna_gain_db = lna_gain_db,
        .vga_gain_db = vga_gain_db,
        .rf_amplifier_enabled = rf_amplifier_enabled,
        .bias_tee_enabled = bias_tee_enabled,
        .fft_size = fft_size,
        .hop_size = hop_size,
        .window = window,
        .detector = detector,
        .slot_count = slot_count,
        .ready_capacity = ready_capacity,
        .dsp_output_capacity = dsp_output_capacity,
        .presentation_capacity = presentation_capacity,
        .configuration_generation = configuration_generation,
        .source_id = std::move(source_id),
    };
}

}  // namespace

void bind_hackrf_factory(py::module_& module) {
    module.def(
        "create_hackrf_runtime_dsp_control",
        [](
            const double center_frequency_hz,
            const double sample_rate_hz,
            const std::uint32_t baseband_filter_hz,
            const std::uint32_t lna_gain_db,
            const std::uint32_t vga_gain_db,
            const bool rf_amplifier_enabled,
            const bool bias_tee_enabled,
            const std::uint32_t fft_size,
            const std::uint32_t hop_size,
            const sdr_core::WindowType window,
            const sdr_core::DetectorType detector,
            const std::uint32_t slot_count,
            const std::uint32_t ready_capacity,
            const std::uint32_t dsp_output_capacity,
            const std::uint32_t presentation_capacity,
            const std::uint64_t configuration_generation,
            std::string source_id
        ) {
            auto config = hackrf_live_factory_config(
                center_frequency_hz,
                sample_rate_hz,
                baseband_filter_hz,
                lna_gain_db,
                vga_gain_db,
                rf_amplifier_enabled,
                bias_tee_enabled,
                fft_size,
                hop_size,
                window,
                detector,
                slot_count,
                ready_capacity,
                dsp_output_capacity,
                presentation_capacity,
                configuration_generation,
                std::move(source_id)
            );
            // Reject malformed values before the first device/library action.
            static_cast<void>(sdr_hackrf::make_hackrf_runtime_dsp_config(config));
            py::gil_scoped_release release;
            return sdr_hackrf::make_official_hackrf_runtime_dsp_session(config);
        },
        py::arg("center_frequency_hz"),
        py::arg("sample_rate_hz"),
        py::arg("baseband_filter_hz"),
        py::arg("lna_gain_db"),
        py::arg("vga_gain_db"),
        py::arg("rf_amplifier_enabled"),
        py::arg("bias_tee_enabled"),
        py::arg("fft_size"),
        py::arg("hop_size"),
        py::arg("window"),
        py::arg("detector"),
        py::arg("slot_count"),
        py::arg("ready_capacity"),
        py::arg("dsp_output_capacity"),
        py::arg("presentation_capacity"),
        py::arg("configuration_generation"),
        py::arg("source_id")
    );
}

}  // namespace sdr_core::python
