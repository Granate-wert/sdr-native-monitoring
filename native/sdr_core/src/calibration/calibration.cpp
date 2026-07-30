#include "sdr_core/calibration.hpp"

#include "sdr_core/errors.hpp"

#include <algorithm>
#include <cmath>
#include <limits>
#include <utility>

namespace sdr_core {
namespace {

void finite(const double value, const char* name) {
    if (!std::isfinite(value)) {
        throw ConfigurationError(std::string(name) + " must be finite");
    }
}

CalibrationStatus worst_status(const CalibrationStatus left, const CalibrationStatus right) {
    const auto rank = [](const CalibrationStatus value) {
        switch (value) {
        case CalibrationStatus::Invalid:
            return 4;
        case CalibrationStatus::Extrapolated:
            return 3;
        case CalibrationStatus::Interpolated:
            return 2;
        case CalibrationStatus::Applied:
            return 1;
        case CalibrationStatus::Uncalibrated:
        case CalibrationStatus::NotApplicable:
            return 0;
        }
        return 4;
    };
    return rank(left) >= rank(right) ? left : right;
}

}  // namespace

CalibrationCurve::CalibrationCurve(
    std::vector<double> frequencies_hz,
    std::vector<double> correction_db,
    std::vector<double> uncertainty_db
)
    : frequencies_hz_(std::move(frequencies_hz)),
      correction_db_(std::move(correction_db)),
      uncertainty_db_(std::move(uncertainty_db)) {
    if (frequencies_hz_.size() < 2U ||
        frequencies_hz_.size() != correction_db_.size() ||
        frequencies_hz_.size() != uncertainty_db_.size()) {
        throw ConfigurationError("calibration curve needs equal arrays with at least two points");
    }
    for (std::size_t index = 0; index < frequencies_hz_.size(); ++index) {
        finite(frequencies_hz_[index], "frequency_hz");
        finite(correction_db_[index], "correction_db");
        finite(uncertainty_db_[index], "uncertainty_db");
        if (frequencies_hz_[index] <= 0.0 || uncertainty_db_[index] < 0.0) {
            throw ConfigurationError("calibration frequency must be positive and uncertainty non-negative");
        }
        if (index > 0U && frequencies_hz_[index - 1U] >= frequencies_hz_[index]) {
            throw ConfigurationError("calibration frequencies must be strictly increasing");
        }
    }
}

CalibrationSample CalibrationCurve::evaluate(
    const double frequency_hz,
    const bool allow_extrapolation
) const {
    finite(frequency_hz, "frequency_hz");
    if (frequency_hz <= 0.0) {
        return {0.0, std::numeric_limits<double>::quiet_NaN(), CalibrationStatus::Invalid};
    }
    const auto exact = std::lower_bound(frequencies_hz_.begin(), frequencies_hz_.end(), frequency_hz);
    if (exact != frequencies_hz_.end() && *exact == frequency_hz) {
        const auto index = static_cast<std::size_t>(exact - frequencies_hz_.begin());
        return {correction_db_[index], uncertainty_db_[index], CalibrationStatus::Applied};
    }
    std::size_t left{};
    std::size_t right{};
    CalibrationStatus status{CalibrationStatus::Interpolated};
    if (exact == frequencies_hz_.begin()) {
        if (!allow_extrapolation) {
            return {0.0, std::numeric_limits<double>::quiet_NaN(), CalibrationStatus::Invalid};
        }
        left = 0U;
        right = 1U;
        status = CalibrationStatus::Extrapolated;
    } else if (exact == frequencies_hz_.end()) {
        if (!allow_extrapolation) {
            return {0.0, std::numeric_limits<double>::quiet_NaN(), CalibrationStatus::Invalid};
        }
        left = frequencies_hz_.size() - 2U;
        right = frequencies_hz_.size() - 1U;
        status = CalibrationStatus::Extrapolated;
    } else {
        right = static_cast<std::size_t>(exact - frequencies_hz_.begin());
        left = right - 1U;
    }
    const double fraction =
        (frequency_hz - frequencies_hz_[left]) /
        (frequencies_hz_[right] - frequencies_hz_[left]);
    return {
        correction_db_[left] + fraction * (correction_db_[right] - correction_db_[left]),
        uncertainty_db_[left] + fraction * (uncertainty_db_[right] - uncertainty_db_[left]),
        status,
    };
}

PreparedCalibration CalibrationCurve::prepare(
    const std::vector<double>& frequencies_hz,
    const bool allow_extrapolation
) const {
    if (frequencies_hz.empty()) {
        throw ConfigurationError("calibration frequency grid must not be empty");
    }
    PreparedCalibration prepared;
    prepared.status = CalibrationStatus::Applied;
    prepared.correction_db.reserve(frequencies_hz.size());
    prepared.uncertainty_db.reserve(frequencies_hz.size());
    for (const double frequency : frequencies_hz) {
        const auto sample = evaluate(frequency, allow_extrapolation);
        prepared.status = worst_status(prepared.status, sample.status);
        prepared.correction_db.push_back(sample.correction_db);
        prepared.uncertainty_db.push_back(sample.uncertainty_db);
    }
    return prepared;
}

void CalibrationCurve::apply_db(
    std::vector<float>& values,
    const PreparedCalibration& prepared
) {
    if (prepared.status == CalibrationStatus::Invalid ||
        prepared.status == CalibrationStatus::Uncalibrated ||
        prepared.status == CalibrationStatus::NotApplicable ||
        values.size() != prepared.correction_db.size()) {
        throw ConfigurationError("prepared calibration cannot be applied");
    }
    for (std::size_t index = 0; index < values.size(); ++index) {
        values[index] += static_cast<float>(prepared.correction_db[index]);
    }
}

const std::vector<double>& CalibrationCurve::frequencies_hz() const noexcept {
    return frequencies_hz_;
}

}  // namespace sdr_core
