#pragma once

#include <stdexcept>
#include <string>

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

class DeviceError : public SdrNativeError {
public:
    using SdrNativeError::SdrNativeError;
};

class OperationCancelled : public SdrNativeError {
public:
    using SdrNativeError::SdrNativeError;
};

}  // namespace sdr_core
