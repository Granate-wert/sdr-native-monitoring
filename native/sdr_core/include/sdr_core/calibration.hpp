#pragma once

#include "sdr_core/types.hpp"

#include <cstddef>
#include <vector>

namespace sdr_core {

struct CalibrationSample {
    double correction_db{};
    double uncertainty_db{};
    CalibrationStatus status{CalibrationStatus::Invalid};
};

struct PreparedCalibration {
    CalibrationStatus status{CalibrationStatus::Invalid};
    std::vector<double> correction_db;
    std::vector<double> uncertainty_db;
};

class CalibrationCurve final {
public:
    CalibrationCurve(
        std::vector<double> frequencies_hz,
        std::vector<double> correction_db,
        std::vector<double> uncertainty_db
    );

    [[nodiscard]] CalibrationSample evaluate(
        double frequency_hz,
        bool allow_extrapolation = false
    ) const;

    [[nodiscard]] PreparedCalibration prepare(
        const std::vector<double>& frequencies_hz,
        bool allow_extrapolation = false
    ) const;

    static void apply_db(
        std::vector<float>& values,
        const PreparedCalibration& prepared
    );

    [[nodiscard]] const std::vector<double>& frequencies_hz() const noexcept;

private:
    std::vector<double> frequencies_hz_;
    std::vector<double> correction_db_;
    std::vector<double> uncertainty_db_;
};

}  // namespace sdr_core
