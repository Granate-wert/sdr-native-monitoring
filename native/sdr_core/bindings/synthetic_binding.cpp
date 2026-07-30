#include "synthetic_binding.hpp"

#include "sdr_core/synthetic_source.hpp"

#include <pybind11/pybind11.h>

namespace py = pybind11;

namespace sdr_core::python {

void bind_synthetic(py::module_& module) {
    py::enum_<SyntheticScenario>(module, "SyntheticScenario")
        .value("EXACT_BIN_TONE", SyntheticScenario::ExactBinTone)
        .value("HALF_BIN_TONE", SyntheticScenario::HalfBinTone)
        .value("TWO_TONES", SyntheticScenario::TwoTones)
        .value("CLOSE_TONES", SyntheticScenario::CloseTones)
        .value("BROADBAND_NOISE", SyntheticScenario::BroadbandNoise)
        .value("DC_OFFSET", SyntheticScenario::DcOffset)
        .value("IMPULSE", SyntheticScenario::Impulse)
        .value("CLIPPING", SyntheticScenario::Clipping)
        .value("IQ_IMBALANCE", SyntheticScenario::IqImbalance)
        .value("CHIRP", SyntheticScenario::Chirp)
        .value("HOPPING", SyntheticScenario::Hopping)
        .value("AMPLITUDE_BURST", SyntheticScenario::AmplitudeBurst);

    py::class_<SyntheticSourceConfig>(module, "SyntheticSourceConfig")
        .def(py::init([](
                 const SyntheticScenario scenario,
                 const std::uint64_t seed,
                 const std::uint32_t sample_count,
                 const double sample_rate_hz,
                 const double center_frequency_hz,
                 const std::uint32_t schema_version
             ) {
            SyntheticSourceConfig config{
                scenario,
                seed,
                sample_count,
                sample_rate_hz,
                center_frequency_hz,
                schema_version,
            };
            validate(config);
            return config;
        }))
        .def_readonly("scenario", &SyntheticSourceConfig::scenario)
        .def_readonly("seed", &SyntheticSourceConfig::seed)
        .def_readonly("sample_count", &SyntheticSourceConfig::sample_count)
        .def_readonly("sample_rate_hz", &SyntheticSourceConfig::sample_rate_hz)
        .def_readonly("center_frequency_hz", &SyntheticSourceConfig::center_frequency_hz)
        .def_readonly("schema_version", &SyntheticSourceConfig::schema_version);

    py::class_<SyntheticSourceSkeleton>(module, "SyntheticSourceSkeleton")
        .def(py::init<SyntheticSourceConfig>())
        .def_property_readonly("config", &SyntheticSourceSkeleton::config)
        .def("descriptor", &SyntheticSourceSkeleton::descriptor)
        .def("capabilities", &SyntheticSourceSkeleton::capabilities)
        .def("block_seed", &SyntheticSourceSkeleton::block_seed);

    module.attr("SYNTHETIC_SCHEMA_NAME") = std::string(synthetic_schema_name);
    module.attr("SYNTHETIC_SCHEMA_VERSION") = synthetic_schema_version;
}

}  // namespace sdr_core::python
