#include "sdr_core/calibration.hpp"

#include <cmath>
#include <iostream>
#include <vector>

namespace {

void expect(const bool condition, const char* message) {
    if (!condition) {
        std::cerr << message << "\n";
        std::exit(1);
    }
}

}  // namespace

int main() {
    sdr_core::CalibrationCurve curve(
        {100.0, 200.0, 300.0},
        {1.0, 3.0, 5.0},
        {1.0, 2.0, 3.0}
    );
    const auto exact = curve.evaluate(200.0);
    expect(exact.status == sdr_core::CalibrationStatus::Applied, "exact point status");
    expect(exact.correction_db == 3.0, "exact correction");

    const auto interpolated = curve.evaluate(150.0);
    expect(interpolated.status == sdr_core::CalibrationStatus::Interpolated, "interpolation status");
    expect(std::abs(interpolated.correction_db - 2.0) < 1e-12, "interpolated correction");
    expect(std::abs(interpolated.uncertainty_db - 1.5) < 1e-12, "interpolated uncertainty");

    const auto rejected = curve.evaluate(50.0);
    expect(rejected.status == sdr_core::CalibrationStatus::Invalid, "out-of-range rejection");
    const auto extrapolated = curve.evaluate(50.0, true);
    expect(extrapolated.status == sdr_core::CalibrationStatus::Extrapolated, "extrapolation status");
    expect(std::abs(extrapolated.correction_db - 0.0) < 1e-12, "extrapolated correction");

    const auto prepared = curve.prepare({100.0, 150.0, 250.0});
    expect(prepared.status == sdr_core::CalibrationStatus::Interpolated, "prepared status");
    std::vector<float> values{-10.0F, -20.0F, -30.0F};
    sdr_core::CalibrationCurve::apply_db(values, prepared);
    expect(std::abs(values[0] + 9.0F) < 1e-6F, "native correction fast path");
    expect(std::abs(values[1] + 18.0F) < 1e-6F, "native interpolation fast path");
    return 0;
}
