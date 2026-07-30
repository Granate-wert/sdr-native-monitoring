#pragma once

#include <stdexcept>
#include <string>

#include "sdr_core/types.hpp"

namespace sdr_core {

class SdrNativeError : public std::runtime_error {
public:
    explicit SdrNativeError(const std::string& message) : std::runtime_error(message) {}
};

class ConfigurationError : public SdrNativeError {
public:
    using SdrNativeError::SdrNativeError;
};

class BackendUnavailableError : public SdrNativeError {
public:
    using SdrNativeError::SdrNativeError;
};

// Runtime failure of a compute device/backend. Carries the P08 vendor-neutral
// error taxonomy; vendor-specific details stay in the sanitized message.
class DeviceError : public SdrNativeError {
public:
    explicit DeviceError(const std::string& message)
        : SdrNativeError(message) {}
    DeviceError(const std::string& message, const BackendErrorCode code)
        : SdrNativeError(message), code_(code) {}

    [[nodiscard]] BackendErrorCode code() const noexcept {
        return code_;
    }

private:
    BackendErrorCode code_{BackendErrorCode::Unknown};
};

class OperationCancelled : public SdrNativeError {
public:
    using SdrNativeError::SdrNativeError;
};

}  // namespace sdr_core
