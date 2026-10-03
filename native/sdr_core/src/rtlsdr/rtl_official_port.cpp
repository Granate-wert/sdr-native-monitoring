#include "sdr_rtlsdr/rtl_official_port.hpp"

#include "sdr_core/errors.hpp"

#if !defined(_WIN32)
#error The unbundled official RTL port is currently Windows-only.
#endif

#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <Windows.h>
#include <bcrypt.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <memory>
#include <set>
#include <span>
#include <string>
#include <utility>
#include <vector>

namespace sdr_rtlsdr {
namespace {

// These are locally declared ABI function-pointer signatures from the
// upstream Osmocom rtl-sdr public API, not a copied SDK header or binary.
// The external runtime and its redistribution/legal status remain separate.
using Device = void;
using VendorCallback = void (__cdecl *)(unsigned char*, std::uint32_t, void*);

[[nodiscard]] std::wstring utf8_to_wide(const std::string& input) {
    if (input.empty() || input.size() > 32'768U || input.find('\0') != std::string::npos) {
        throw sdr_core::ConfigurationError("RTL runtime path is empty or contains NUL");
    }
    const auto size = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                                         static_cast<int>(input.size()), nullptr, 0);
    if (size <= 0 || size > 32'768) {
        throw sdr_core::ConfigurationError("RTL runtime path is not bounded UTF-8");
    }
    std::wstring output(static_cast<std::size_t>(size), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                            static_cast<int>(input.size()), output.data(), size) != size) {
        throw sdr_core::ConfigurationError("RTL runtime path UTF-8 conversion failed");
    }
    return output;
}

[[nodiscard]] std::filesystem::path checked_path(const RtlExternalFile& file) {
    const std::filesystem::path path(utf8_to_wide(file.absolute_utf8_path));
    if (!path.is_absolute() || path.has_root_name() == false ||
        file.sha256_hex.size() != 64U ||
        !std::all_of(file.sha256_hex.begin(), file.sha256_hex.end(), [](const char value) {
            return value >= '0' && value <= '9' || value >= 'a' && value <= 'f';
        })) {
        throw sdr_core::ConfigurationError("RTL runtime needs an absolute path and lowercase SHA-256");
    }
    return std::filesystem::weakly_canonical(path);
}

[[nodiscard]] std::string file_sha256(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream || stream.tellg() <= 0 || stream.tellg() > 64 * 1024 * 1024) {
        throw sdr_core::DeviceError("RTL external runtime file is absent or exceeds 64 MiB");
    }
    stream.seekg(0);
    BCRYPT_ALG_HANDLE algorithm{};
    BCRYPT_HASH_HANDLE hash{};
    DWORD object_bytes{};
    DWORD returned{};
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0 ||
        BCryptGetProperty(algorithm, BCRYPT_OBJECT_LENGTH,
                          reinterpret_cast<PUCHAR>(&object_bytes), sizeof(object_bytes),
                          &returned, 0) < 0 || object_bytes == 0U || object_bytes > 65'536U) {
        if (algorithm != nullptr) BCryptCloseAlgorithmProvider(algorithm, 0);
        throw sdr_core::DeviceError("RTL runtime SHA-256 provider unavailable");
    }
    std::vector<std::uint8_t> object(object_bytes);
    std::array<std::uint8_t, 32U> digest{};
    auto status = BCryptCreateHash(algorithm, &hash, object.data(), object_bytes,
                                   nullptr, 0, 0);
    std::array<std::uint8_t, 64U * 1024U> chunk{};
    while (status >= 0 && stream) {
        stream.read(reinterpret_cast<char*>(chunk.data()), static_cast<std::streamsize>(chunk.size()));
        const auto count = stream.gcount();
        if (count > 0) {
            status = BCryptHashData(hash, chunk.data(), static_cast<ULONG>(count), 0);
        }
    }
    if (status >= 0 && !stream.bad()) {
        status = BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0);
    }
    if (hash != nullptr) BCryptDestroyHash(hash);
    BCryptCloseAlgorithmProvider(algorithm, 0);
    if (status < 0 || stream.bad()) {
        throw sdr_core::DeviceError("RTL runtime SHA-256 read failed");
    }
    constexpr char digits[] = "0123456789abcdef";
    std::string result;
    result.reserve(64U);
    for (const auto value : digest) {
        result.push_back(digits[value >> 4U]);
        result.push_back(digits[value & 0x0fU]);
    }
    return result;
}

[[nodiscard]] std::filesystem::path module_path(HMODULE module) {
    std::wstring text(32'768U, L'\0');
    const auto length = GetModuleFileNameW(module, text.data(), static_cast<DWORD>(text.size()));
    if (length == 0U || length >= text.size()) {
        throw sdr_core::DeviceError("RTL loaded module path cannot be verified");
    }
    text.resize(length);
    return std::filesystem::weakly_canonical(std::filesystem::path(text));
}

[[nodiscard]] std::string lower_ascii(std::string value) {
    for (char& character : value) {
        if (character >= 'A' && character <= 'Z') character = static_cast<char>(character + ('a' - 'A'));
    }
    return value;
}

[[nodiscard]] bool is_admitted_system_import(const std::string& name) {
    static const std::set<std::string> known{
        "kernel32.dll", "kernelbase.dll", "advapi32.dll", "user32.dll", "bcrypt.dll",
        "ntdll.dll", "ole32.dll", "oleaut32.dll", "ws2_32.dll", "setupapi.dll",
        "cfgmgr32.dll", "vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll",
        "ucrtbase.dll", "msvcrt.dll"};
    return known.contains(name) || name.starts_with("api-ms-win-") || name.starts_with("ext-ms-win-");
}

// Inspect a bounded PE import graph before LoadLibraryEx can execute DllMain.
// Unknown local imports, delay imports, and malformed PE offsets fail closed.
[[nodiscard]] std::vector<std::string> direct_imports(const std::filesystem::path& path) {
    std::ifstream stream(path, std::ios::binary | std::ios::ate);
    if (!stream || stream.tellg() <= 0 || stream.tellg() > 64 * 1024 * 1024) {
        throw sdr_core::DeviceError("RTL dependency PE image is absent or unbounded");
    }
    const auto size = static_cast<std::size_t>(stream.tellg());
    std::vector<std::uint8_t> bytes(size);
    stream.seekg(0);
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(size))) {
        throw sdr_core::DeviceError("RTL dependency PE image cannot be read");
    }
    const auto at = [&bytes](const std::size_t offset, const std::size_t count) -> const std::uint8_t* {
        if (offset > bytes.size() || count > bytes.size() - offset) {
            throw sdr_core::DeviceError("RTL dependency PE image has invalid bounds");
        }
        return bytes.data() + offset;
    };
    const auto read = [&at]<typename T>(const std::size_t offset) -> T {
        T value{};
        std::memcpy(&value, at(offset, sizeof(T)), sizeof(T));
        return value;
    };
    const auto dos = read.template operator()<IMAGE_DOS_HEADER>(0U);
    if (dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew < 0) {
        throw sdr_core::DeviceError("RTL dependency is not a PE image");
    }
    const auto nt_offset = static_cast<std::size_t>(dos.e_lfanew);
    const auto signature = read.template operator()<DWORD>(nt_offset);
    if (signature != IMAGE_NT_SIGNATURE) throw sdr_core::DeviceError("RTL dependency PE signature invalid");
    const auto file_offset = nt_offset + sizeof(DWORD);
    const auto file = read.template operator()<IMAGE_FILE_HEADER>(file_offset);
    const auto optional_offset = file_offset + sizeof(IMAGE_FILE_HEADER);
    const auto magic = read.template operator()<WORD>(optional_offset);
    IMAGE_DATA_DIRECTORY imports{};
    IMAGE_DATA_DIRECTORY delays{};
    if (magic == IMAGE_NT_OPTIONAL_HDR64_MAGIC) {
        const auto optional = read.template operator()<IMAGE_OPTIONAL_HEADER64>(optional_offset);
        if (file.SizeOfOptionalHeader < sizeof(IMAGE_OPTIONAL_HEADER64) ||
            optional.NumberOfRvaAndSizes <= IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT) {
            throw sdr_core::DeviceError("RTL dependency PE directories unavailable");
        }
        imports = optional.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
        delays = optional.DataDirectory[IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT];
    } else if (magic == IMAGE_NT_OPTIONAL_HDR32_MAGIC) {
        const auto optional = read.template operator()<IMAGE_OPTIONAL_HEADER32>(optional_offset);
        if (file.SizeOfOptionalHeader < sizeof(IMAGE_OPTIONAL_HEADER32) ||
            optional.NumberOfRvaAndSizes <= IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT) {
            throw sdr_core::DeviceError("RTL dependency PE directories unavailable");
        }
        imports = optional.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
        delays = optional.DataDirectory[IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT];
    } else {
        throw sdr_core::DeviceError("RTL dependency PE architecture is unsupported");
    }
    if (delays.VirtualAddress != 0U || delays.Size != 0U || file.NumberOfSections > 96U) {
        throw sdr_core::DeviceError("RTL dependency delay imports or sections are unsupported");
    }
    const auto section_offset = optional_offset + file.SizeOfOptionalHeader;
    static_cast<void>(at(section_offset, static_cast<std::size_t>(file.NumberOfSections) * sizeof(IMAGE_SECTION_HEADER)));
    const auto rva_offset = [&](const DWORD rva, const std::size_t length) -> std::size_t {
        for (WORD index = 0U; index < file.NumberOfSections; ++index) {
            const auto section = read.template operator()<IMAGE_SECTION_HEADER>(
                section_offset + static_cast<std::size_t>(index) * sizeof(IMAGE_SECTION_HEADER));
            const auto start = static_cast<std::uint64_t>(section.VirtualAddress);
            const auto span = static_cast<std::uint64_t>(section.SizeOfRawData);
            if (rva >= start && static_cast<std::uint64_t>(rva) + length <= start + span) {
                const auto offset = static_cast<std::size_t>(section.PointerToRawData) +
                                    static_cast<std::size_t>(rva - start);
                static_cast<void>(at(offset, length));
                return offset;
            }
        }
        throw sdr_core::DeviceError("RTL dependency PE import RVA invalid");
    };
    std::vector<std::string> result;
    if (imports.VirtualAddress == 0U && imports.Size == 0U) return result;
    if (imports.VirtualAddress == 0U || imports.Size < sizeof(IMAGE_IMPORT_DESCRIPTOR) ||
        imports.Size > 65'536U) {
        throw sdr_core::DeviceError("RTL dependency PE import table invalid");
    }
    for (std::size_t index = 0U; index < imports.Size / sizeof(IMAGE_IMPORT_DESCRIPTOR); ++index) {
        const auto rva = static_cast<std::uint64_t>(imports.VirtualAddress) +
                         index * sizeof(IMAGE_IMPORT_DESCRIPTOR);
        if (rva > 0xffffffffULL) throw sdr_core::DeviceError("RTL dependency import RVA overflow");
        const auto descriptor = read.template operator()<IMAGE_IMPORT_DESCRIPTOR>(
            rva_offset(static_cast<DWORD>(rva), sizeof(IMAGE_IMPORT_DESCRIPTOR)));
        if (descriptor.Name == 0U) return result;
        const auto name_offset = rva_offset(descriptor.Name, 1U);
        const auto end = std::find(bytes.begin() + static_cast<std::ptrdiff_t>(name_offset),
                                   bytes.begin() + static_cast<std::ptrdiff_t>(std::min(bytes.size(), name_offset + 128U)), 0U);
        if (end == bytes.begin() + static_cast<std::ptrdiff_t>(std::min(bytes.size(), name_offset + 128U))) {
            throw sdr_core::DeviceError("RTL dependency PE import name unbounded");
        }
        result.push_back(lower_ascii(std::string(reinterpret_cast<const char*>(bytes.data() + name_offset),
                                                static_cast<std::size_t>(end - (bytes.begin() + static_cast<std::ptrdiff_t>(name_offset))))));
    }
    throw sdr_core::DeviceError("RTL dependency PE import table lacks terminator");
}

struct Api final {
    using Count = std::uint32_t (__cdecl *)();
    using UsbStringsByIndex = int (__cdecl *)(std::uint32_t, char*, char*, char*);
    using Open = int (__cdecl *)(Device**, std::uint32_t);
    using Close = int (__cdecl *)(Device*);
    using UsbStrings = int (__cdecl *)(Device*, char*, char*, char*);
    using TunerType = int (__cdecl *)(Device*);
    using GetMode = int (__cdecl *)(Device*);
    using SetUint32 = int (__cdecl *)(Device*, std::uint32_t);
    using GetUint32 = std::uint32_t (__cdecl *)(Device*);
    using SetInt = int (__cdecl *)(Device*, int);
    using GetGains = int (__cdecl *)(Device*, int*);
    using Reset = int (__cdecl *)(Device*);
    using ReadAsync = int (__cdecl *)(Device*, VendorCallback, void*, std::uint32_t, std::uint32_t);
    using Cancel = int (__cdecl *)(Device*);

    HMODULE module{};
    Api* quarantine_next{};
    Count count{};
    UsbStringsByIndex strings_by_index{};
    Open open{};
    Close close{};
    UsbStrings strings{};
    TunerType tuner_type{};
    GetMode direct_sampling{};
    GetMode offset_tuning{};
    SetUint32 set_rate{};
    GetUint32 get_rate{};
    SetUint32 set_center{};
    GetUint32 get_center{};
    SetInt set_gain_mode{};
    GetGains get_gains{};
    SetInt set_gain{};
    GetMode get_gain{};
    Reset reset{};
    ReadAsync read_async{};
    Cancel cancel{};

    ~Api() { if (module != nullptr) FreeLibrary(module); }
};

std::atomic<Api*> quarantined_api_head{};

void retain_uncertain_api(std::unique_ptr<Api> api) noexcept {
    quarantine_rtl_process();
    auto* retained = api.release();
    auto* prior = quarantined_api_head.load(std::memory_order_relaxed);
    do {
        retained->quarantine_next = prior;
    } while (!quarantined_api_head.compare_exchange_weak(
        prior, retained, std::memory_order_release, std::memory_order_relaxed));
}

template <typename Function>
[[nodiscard]] Function symbol(HMODULE module, const char* name) {
    const auto value = GetProcAddress(module, name);
    if (value == nullptr) throw sdr_core::DeviceError("RTL external runtime API is incomplete");
    return reinterpret_cast<Function>(value);
}

[[nodiscard]] std::unique_ptr<Api> load_api(const RtlExternalRuntime& runtime) {
    const auto library = checked_path(runtime.library);
    if (library.filename() != L"rtlsdr.dll" || runtime.dependencies.size() > 8U ||
        file_sha256(library) != runtime.library.sha256_hex) {
        throw sdr_core::DeviceError("RTL external runtime name/hash was not admitted");
    }
    std::vector<std::pair<std::filesystem::path, std::string>> dependencies;
    for (const auto& file : runtime.dependencies) {
        const auto path = checked_path(file);
        if (path.parent_path() != library.parent_path() || path.filename() == library.filename() ||
            file_sha256(path) != file.sha256_hex ||
            std::any_of(dependencies.begin(), dependencies.end(), [&path](const auto& item) {
                return item.first.filename() == path.filename();
            })) {
            throw sdr_core::DeviceError("RTL external dependency name/hash was not admitted");
        }
        dependencies.emplace_back(path, file.sha256_hex);
    }
    std::map<std::string, std::filesystem::path> admitted;
    admitted.emplace("rtlsdr.dll", library);
    for (const auto& [path, _hash] : dependencies) {
        const auto name = lower_ascii(path.filename().string());
        if (!admitted.emplace(name, path).second || is_admitted_system_import(name)) {
            throw sdr_core::DeviceError("RTL dependency name conflicts with runtime/system import");
        }
    }
    std::set<std::string> referenced;
    for (const auto& [image_name, image_path] : admitted) {
        for (const auto& imported : direct_imports(image_path)) {
            if (is_admitted_system_import(imported)) {
                // DLL_LOAD_DIR participates in import resolution. Refuse a
                // local shadow of a system/API-set import before DllMain.
                if (std::filesystem::exists(library.parent_path() / imported)) {
                    throw sdr_core::DeviceError("RTL system import is shadowed in runtime directory");
                }
                continue;
            }
            if (!admitted.contains(imported)) {
                throw sdr_core::DeviceError("RTL dependency import closure is not hash-admitted");
            }
            referenced.insert(imported);
        }
    }
    for (const auto& [image_name, _image_path] : admitted) {
        if (image_name != "rtlsdr.dll" && !referenced.contains(image_name)) {
            throw sdr_core::DeviceError("RTL declared dependency was not found in import closure");
        }
    }
    // A different family may already have loaded a libusb of the same name.
    // Resolve that collision before this DLL's DllMain/import binding runs.
    if (const auto loaded_main = GetModuleHandleW(L"rtlsdr.dll"); loaded_main != nullptr &&
        module_path(loaded_main) != library) {
        throw sdr_core::DeviceError("RTL runtime name is already loaded from a different path");
    }
    for (const auto& [path, expected_hash] : dependencies) {
        const auto loaded = GetModuleHandleW(path.filename().c_str());
        if (loaded != nullptr &&
            (module_path(loaded) != path || file_sha256(path) != expected_hash)) {
            throw sdr_core::DeviceError("RTL dependency is already loaded from a different runtime");
        }
    }
    auto api = std::make_unique<Api>();
    api->module = LoadLibraryExW(library.c_str(), nullptr,
        LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_SYSTEM32);
    if (api->module == nullptr || module_path(api->module) != library ||
        file_sha256(library) != runtime.library.sha256_hex) {
        throw sdr_core::DeviceError("RTL external runtime load/path/hash failed");
    }
    for (const auto& [path, expected_hash] : dependencies) {
        const auto loaded = GetModuleHandleW(path.filename().c_str());
        if (loaded == nullptr || module_path(loaded) != path || file_sha256(path) != expected_hash) {
            throw sdr_core::DeviceError("RTL external dependency did not load from admitted path");
        }
    }
    api->count = symbol<Api::Count>(api->module, "rtlsdr_get_device_count");
    api->strings_by_index = symbol<Api::UsbStringsByIndex>(api->module, "rtlsdr_get_device_usb_strings");
    api->open = symbol<Api::Open>(api->module, "rtlsdr_open");
    api->close = symbol<Api::Close>(api->module, "rtlsdr_close");
    api->strings = symbol<Api::UsbStrings>(api->module, "rtlsdr_get_usb_strings");
    api->tuner_type = symbol<Api::TunerType>(api->module, "rtlsdr_get_tuner_type");
    api->direct_sampling = symbol<Api::GetMode>(api->module, "rtlsdr_get_direct_sampling");
    api->offset_tuning = symbol<Api::GetMode>(api->module, "rtlsdr_get_offset_tuning");
    api->set_rate = symbol<Api::SetUint32>(api->module, "rtlsdr_set_sample_rate");
    api->get_rate = symbol<Api::GetUint32>(api->module, "rtlsdr_get_sample_rate");
    api->set_center = symbol<Api::SetUint32>(api->module, "rtlsdr_set_center_freq");
    api->get_center = symbol<Api::GetUint32>(api->module, "rtlsdr_get_center_freq");
    api->set_gain_mode = symbol<Api::SetInt>(api->module, "rtlsdr_set_tuner_gain_mode");
    // Optional exports: absence must not disable the established automatic lane.
    api->get_gains = reinterpret_cast<Api::GetGains>(GetProcAddress(api->module, "rtlsdr_get_tuner_gains"));
    api->set_gain = reinterpret_cast<Api::SetInt>(GetProcAddress(api->module, "rtlsdr_set_tuner_gain"));
    api->get_gain = reinterpret_cast<Api::GetMode>(GetProcAddress(api->module, "rtlsdr_get_tuner_gain"));
    api->reset = symbol<Api::Reset>(api->module, "rtlsdr_reset_buffer");
    api->read_async = symbol<Api::ReadAsync>(api->module, "rtlsdr_read_async");
    api->cancel = symbol<Api::Cancel>(api->module, "rtlsdr_cancel_async");
    return api;
}

[[nodiscard]] std::string bounded_string(const std::array<char, 256U>& source) {
    const auto end = std::find(source.begin(), source.end(), '\0');
    if (end == source.end()) throw sdr_core::DeviceError("RTL USB descriptor was not terminated");
    return std::string(source.begin(), end);
}

[[nodiscard]] RtlObservedCandidate strings_by_index(Api& api, const std::uint32_t index) {
    std::array<char, 256U> manufacturer{};
    std::array<char, 256U> product{};
    std::array<char, 256U> serial{};
    if (api.strings_by_index(index, manufacturer.data(), product.data(), serial.data()) != 0) {
        throw sdr_core::DeviceError("RTL USB descriptor observation failed");
    }
    return {index, bounded_string(manufacturer), bounded_string(product), bounded_string(serial)};
}

[[nodiscard]] RtlObservedCandidate strings_after_open(Api& api, Device* device) {
    std::array<char, 256U> manufacturer{};
    std::array<char, 256U> product{};
    std::array<char, 256U> serial{};
    if (api.strings(device, manufacturer.data(), product.data(), serial.data()) != 0) {
        throw sdr_core::DeviceError("RTL opened-device descriptor readback failed");
    }
    return {0U, bounded_string(manufacturer), bounded_string(product), bounded_string(serial)};
}

[[nodiscard]] bool same_strings(const RtlObservedCandidate& a, const RtlObservedCandidate& b) {
    return a.manufacturer == b.manufacturer && a.product == b.product && a.serial == b.serial;
}

[[nodiscard]] std::vector<int> tuner_gains(Api& api, Device* device) {
    if (!api.get_gains || !api.set_gain || !api.get_gain) return {};
    // The provisioned SDK ABI has no buffer-length argument. Its trusted table
    // is static per tuner (V1.4.0 maximum29); allow bounded extensions up to256.
    // This is not containment of a malicious/native ABI-violating DLL.
    const auto count = api.get_gains(device, nullptr);
    if (count <= 0 || count > 256) throw sdr_core::DeviceError("RTL gain table count invalid");
    std::array<int, 256> values{};
    if (api.get_gains(device, values.data()) != count) {
        throw sdr_core::DeviceError("RTL gain table changed during owned observation");
    }
    std::vector<int> result(values.begin(), values.begin() + count);
    if (std::any_of(result.begin(), result.end(), [](int value) { return value < -1000 || value > 1000; }) ||
        !std::is_sorted(result.begin(), result.end()) ||
        std::adjacent_find(result.begin(), result.end()) != result.end()) {
        throw sdr_core::DeviceError("RTL gain table values invalid");
    }
    return result;
}

class OfficialPort final : public RtlRuntimePort {
public:
    explicit OfficialPort(std::unique_ptr<Api> api) : api_(std::move(api)) {}

    int open_exact_unique_serial(const std::string& serial) noexcept override {
        try {
            const auto count = api_->count();
            if (count == 0U || count > 8U) return -10;
            std::uint32_t index{};
            std::uint32_t matches{};
            for (std::uint32_t candidate = 0U; candidate < count; ++candidate) {
                if (strings_by_index(*api_, candidate).serial == serial) {
                    index = candidate;
                    ++matches;
                }
            }
            if (matches != 1U) return -11;
            const auto before = strings_by_index(*api_, index);
            const auto status = open_checked(index, before, 0U);
            if (status != 0) return status;
            try {
                if (api_->count() == count &&
                    same_strings(before, strings_after_open(*api_, device_))) return 0;
            } catch (...) {
                const auto close_status = close();
                return close_status == 0 ? -13 : -14;
            }
            const auto close_status = close();
            return close_status == 0 ? -15 : -16;
        } catch (...) { return -12; }
    }

    int open_selected_session_route(const RtlSessionRoute& expected) noexcept override {
        try {
            if (api_->count() != 1U) return -20;
            const auto before = strings_by_index(*api_, 0U);
            if (before.manufacturer != expected.manufacturer || before.product != expected.product ||
                before.serial != expected.serial) return -21;
            const auto status = open_checked(0U, before, expected.tuner_type);
            if (status != 0) return status;
            try {
                // Revalidate the SAME owned handle, not a second index-based
                // USB open that WinUSB can refuse while this interface is held.
                if (api_->count() == 1U && same_strings(before, strings_after_open(*api_, device_))) return 0;
            } catch (...) {
                const auto close_status = close();
                return close_status == 0 ? -25 : -26;
            }
            const auto close_status = close();
            return close_status == 0 ? -23 : -24;
        } catch (...) { return -22; }
    }

    int set_sample_rate(const std::uint32_t value) noexcept override {
        return device_ != nullptr ? api_->set_rate(device_, value) : -1;
    }
    std::uint32_t get_sample_rate() noexcept override {
        return device_ != nullptr ? api_->get_rate(device_) : 0U;
    }
    int set_center_frequency(const std::uint32_t value) noexcept override {
        return device_ != nullptr ? api_->set_center(device_, value) : -1;
    }
    std::uint32_t get_center_frequency() noexcept override {
        return device_ != nullptr ? api_->get_center(device_) : 0U;
    }
    int set_automatic_tuner_gain() noexcept override {
        return device_ != nullptr ? api_->set_gain_mode(device_, 0) : -1;
    }
    int set_manual_tuner_gain(const int gain) noexcept override {
        if (!device_ || !api_->get_gains || !api_->set_gain || !api_->get_gain) return -103;
        try {
            const auto gains = tuner_gains(*api_, device_);
            if (!std::binary_search(gains.begin(), gains.end(), gain)) return -104;
            auto status = api_->set_gain_mode(device_, 1);
            if (status == 0) status = api_->set_gain(device_, gain);
            return status;
        } catch (...) { return -105; }
    }
    std::optional<int> get_cached_tuner_gain() noexcept override {
        if (!device_ || !api_->get_gain) return std::nullopt;
        return api_->get_gain(device_);
    }
    int reset_buffer() noexcept override { return device_ != nullptr ? api_->reset(device_) : -1; }
    int verify_normal_tuner_mode() noexcept override {
        if (device_ == nullptr) return -1;
        const auto tuner = api_->tuner_type(device_);
        const auto direct = api_->direct_sampling(device_);
        const auto offset = api_->offset_tuning(device_);
        return tuner > 0 && tuner <= 6 && direct == 0 && offset == 0 ? 0 : -1;
    }
    int read_async(const RtlBytesCallback callback, void* context,
                   const std::uint32_t buffer_bytes) noexcept override {
        if (device_ == nullptr || callback == nullptr || context == nullptr) return -1;
        callback_ = callback;
        context_ = context;
        const auto status = api_->read_async(device_, &OfficialPort::vendor_callback,
                                             this, 8U, buffer_bytes);
        callback_ = nullptr;
        context_ = nullptr;
        return status;
    }
    int cancel_async() noexcept override {
        return device_ != nullptr ? api_->cancel(device_) : -1;
    }
    int close() noexcept override {
        if (device_ == nullptr) return -1;
        const auto status = api_->close(device_);
        if (status != 0) {
            retain_uncertain_api(std::move(api_));
        }
        device_ = nullptr;
        return status;
    }

private:
    static void __cdecl vendor_callback(unsigned char* bytes, const std::uint32_t length,
                                         void* context) noexcept {
        auto* const self = static_cast<OfficialPort*>(context);
        if (self == nullptr || self->callback_ == nullptr) return;
        try {
            if (bytes == nullptr) self->callback_({}, self->context_);
            else self->callback_(std::span<const std::uint8_t>(bytes, length), self->context_);
        } catch (...) {
            // No C++ exception may cross the vendor C callback boundary.
            quarantine_rtl_process();
        }
    }

    int open_checked(const std::uint32_t index, const RtlObservedCandidate& before,
                     const std::uint32_t expected_tuner) noexcept {
        Device* opened{};
        const auto status = api_->open(&opened, index);
        if (status != 0 || opened == nullptr) {
            if (opened != nullptr) {
                retain_uncertain_api(std::move(api_));
            }
            return status != 0 ? status : -30;
        }
        device_ = opened;
        try {
            const auto after = strings_after_open(*api_, device_);
            const auto tuner = api_->tuner_type(device_);
            const auto direct = api_->direct_sampling(device_);
            const auto offset = api_->offset_tuning(device_);
            if (!same_strings(before, after) || tuner <= 0 || tuner > 6 ||
                (expected_tuner != 0U && tuner != static_cast<int>(expected_tuner)) ||
                direct != 0 || offset != 0) {
                const auto close_status = close();
                return close_status == 0 ? -31 : -32;
            }
        } catch (...) {
            const auto close_status = close();
            return close_status == 0 ? -33 : -34;
        }
        return 0;
    }

    std::unique_ptr<Api> api_;
    Device* device_{};
    RtlBytesCallback callback_{};
    void* context_{};
};

}  // namespace

std::vector<RtlObservedCandidate> enumerate_rtl_candidates(const RtlExternalRuntime& runtime) {
    auto api = load_api(runtime);
    const auto count = api->count();
    if (count > 8U) throw sdr_core::DeviceError("RTL candidate inventory exceeds eight devices");
    std::vector<RtlObservedCandidate> result;
    result.reserve(count);
    for (std::uint32_t index = 0U; index < count; ++index) {
        result.push_back(strings_by_index(*api, index));
    }
    return result;
}

RtlObservedCandidate observe_single_rtl_candidate(const RtlExternalRuntime& runtime) {
    if (!try_acquire_rtl_process_lease()) {
        throw sdr_core::DeviceError("RTL selected probe conflicts with active or quarantined owner");
    }
    struct Lease final {
        bool release{true};
        ~Lease() { if (release) release_rtl_process_lease(); }
    } lease;
    auto api = load_api(runtime);
    if (api->count() != 1U) {
        throw sdr_core::DeviceError("RTL session observation requires exactly one current candidate");
    }
    auto candidate = strings_by_index(*api, 0U);
    Device* device{};
    const auto status = api->open(&device, 0U);
    if (status != 0 || device == nullptr) {
        if (device != nullptr) {
            retain_uncertain_api(std::move(api));
            lease.release = false;
        }
        throw sdr_core::DeviceError("RTL selected read-only open failed");
    }
    bool valid{};
    try {
        const auto after = strings_after_open(*api, device);
        const auto tuner = api->tuner_type(device);
        const auto direct = api->direct_sampling(device);
        const auto offset = api->offset_tuning(device);
        valid = same_strings(candidate, after) && tuner > 0 && tuner <= 6 &&
                direct == 0 && offset == 0;
        if (valid) {
            candidate.tuner_type = static_cast<std::uint32_t>(tuner);
            candidate.direct_sampling = false;
            candidate.offset_tuning = false;
            candidate.tuner_gains_tenth_db = tuner_gains(*api, device);
        }
    } catch (...) { valid = false; }
    const auto close_status = api->close(device);
    if (close_status != 0) {
        retain_uncertain_api(std::move(api));
        lease.release = false;
    }
    if (!valid || close_status != 0) {
        throw sdr_core::DeviceError("RTL selected probe/readback/close failed closed");
    }
    return candidate;
}

std::unique_ptr<RtlRuntimePort> make_official_rtl_port(const RtlExternalRuntime& runtime) {
    return std::make_unique<OfficialPort>(load_api(runtime));
}

}  // namespace sdr_rtlsdr
