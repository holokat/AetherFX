// AetherFX GPU module - headless wgpu-native device wrapper.
//
// This is the bootstrap layer for the future GPU particle runtime and GPU
// renderer (docs/ARCHITECTURE.md sections 2 and 7). It owns exactly one
// instance/adapter/device/queue quadruple and nothing else; everything above it
// (pipelines, particle buffers, render targets) belongs in later modules.
//
// The whole module is optional and only compiled when AETHER_WITH_GPU=ON.
//
// Threading: a GpuDevice is not thread-safe. All callbacks used here run
// synchronously inside wgpuInstanceProcessEvents / wgpuDevicePoll on the
// calling thread, so there is no background thread and no locking.

#ifndef AETHER_GPU_DEVICE_HPP
#define AETHER_GPU_DEVICE_HPP

#include <cstdint>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include <webgpu/webgpu.h>

namespace aether::gpu {

/// Human-readable description of the adapter backing a GpuDevice.
/// Every field is best-effort: wgpu-native leaves some of them empty on some
/// backends, in which case the string is "unknown" rather than "".
struct AdapterInfo {
    std::string name{"unknown"};          ///< WGPUAdapterInfo::device, e.g. "Apple M2 Max".
    std::string backend{"unknown"};       ///< "Metal", "Vulkan", "D3D12", ...
    std::string vendor{"unknown"};        ///< WGPUAdapterInfo::vendor, e.g. "apple".
    std::string driver{"unknown"};        ///< WGPUAdapterInfo::description (driver string).
    std::string architecture{"unknown"};  ///< GPU family, e.g. "apple-m2".
    std::string adapter_type{"unknown"};  ///< "discrete", "integrated", "cpu", "unknown".
    std::uint32_t vendor_id{0};
    std::uint32_t device_id{0};

    /// One-line summary suitable for logs and test output.
    [[nodiscard]] std::string to_string() const;
};

namespace detail {

/// Minimal move-only owner for a WGPU handle released by `Release(handle)`.
template <typename Handle, void (*Release)(Handle)>
class Owned {
public:
    Owned() = default;
    explicit Owned(Handle handle) noexcept : handle_(handle) {}
    ~Owned() { reset(); }

    Owned(const Owned&) = delete;
    Owned& operator=(const Owned&) = delete;

    Owned(Owned&& other) noexcept : handle_(std::exchange(other.handle_, nullptr)) {}
    Owned& operator=(Owned&& other) noexcept {
        if (this != &other) {
            reset();
            handle_ = std::exchange(other.handle_, nullptr);
        }
        return *this;
    }

    void reset(Handle handle = nullptr) noexcept {
        if (handle_ != nullptr) {
            Release(handle_);
        }
        handle_ = handle;
    }

    [[nodiscard]] Handle get() const noexcept { return handle_; }
    [[nodiscard]] explicit operator bool() const noexcept { return handle_ != nullptr; }

private:
    Handle handle_{nullptr};
};

}  // namespace detail

/// RAII wrapper around a WGPUBuffer. Does not own the device it came from.
class GpuBuffer {
public:
    GpuBuffer() = default;

    /// Creates `size` bytes of GPU memory. Returns an empty GpuBuffer on failure.
    static GpuBuffer create(WGPUDevice device, std::uint64_t size, WGPUBufferUsage usage,
                            const char* label = nullptr);

    [[nodiscard]] WGPUBuffer handle() const noexcept { return buffer_.get(); }
    [[nodiscard]] std::uint64_t size() const noexcept { return size_; }
    [[nodiscard]] bool valid() const noexcept { return static_cast<bool>(buffer_); }

private:
    detail::Owned<WGPUBuffer, wgpuBufferRelease> buffer_{};
    std::uint64_t size_{0};
};

/// RAII wrapper around a WGPUShaderModule compiled from WGSL source.
class GpuShaderModule {
public:
    GpuShaderModule() = default;

    /// Compiles `wgsl`. Returns an empty module on failure; WGSL diagnostics are
    /// reported through the device's uncaptured-error callback, which
    /// GpuDevice::create_headless routes into the device's last-error string.
    static GpuShaderModule create(WGPUDevice device, std::string_view wgsl,
                                  const char* label = nullptr);

    [[nodiscard]] WGPUShaderModule handle() const noexcept { return module_.get(); }
    [[nodiscard]] bool valid() const noexcept { return static_cast<bool>(module_); }

private:
    detail::Owned<WGPUShaderModule, wgpuShaderModuleRelease> module_{};
};

/// Owns a headless wgpu-native instance + adapter + device + queue.
///
/// "Headless" means no surface and no window: everything is compute and
/// offscreen render targets, which is what the CLI and the CI evaluation loop
/// need. Construction is synchronous even though the underlying WebGPU calls
/// are future-based (see device.cpp for the pumping pattern).
class GpuDevice {
public:
    ~GpuDevice();

    GpuDevice(const GpuDevice&) = delete;
    GpuDevice& operator=(const GpuDevice&) = delete;
    GpuDevice(GpuDevice&&) = delete;
    GpuDevice& operator=(GpuDevice&&) = delete;

    /// Brings up a headless device, preferring a high-performance adapter on any
    /// backend. Returns nullptr on failure; when `error` is non-null it receives
    /// a human-readable reason. A missing adapter yields a message that starts
    /// with "no adapter", which callers (e.g. CI without a GPU) can treat as
    /// "skip" rather than "fail".
    ///
    /// IMPORTANT for the particle runtime: this requests no explicit limits, so
    /// the device gets the WebGPU *default* limits, not the adapter maximums.
    /// Measured on an M2 Max (wgpu-native v29.0.1.1):
    ///     maxComputeInvocationsPerWorkgroup   256 (default) vs 1024 (adapter)
    ///     maxComputeWorkgroupSizeX            256           vs 1024
    ///     maxComputeWorkgroupStorageSize       16 KiB       vs 32 KiB
    ///     maxStorageBufferBindingSize         128 MiB       vs 4 GiB
    ///     maxBufferSize                       256 MiB       vs ~39 GiB
    ///     maxStorageBuffersPerShaderStage       8           vs 31
    /// Defaults are deliberate here: they keep this bootstrap portable and stop
    /// us from silently writing shaders that only run on this laptop. When the
    /// runtime needs more, add an opt-in overload that reads
    /// wgpuAdapterGetLimits() and passes a clamped WGPULimits through
    /// WGPUDeviceDescriptor::requiredLimits - and gate the WGSL on it.
    [[nodiscard]] static std::unique_ptr<GpuDevice> create_headless(std::string* error = nullptr);

    [[nodiscard]] WGPUInstance instance() const noexcept { return instance_.get(); }
    [[nodiscard]] WGPUAdapter adapter() const noexcept { return adapter_.get(); }
    [[nodiscard]] WGPUDevice device() const noexcept { return device_.get(); }
    [[nodiscard]] WGPUQueue queue() const noexcept { return queue_.get(); }

    [[nodiscard]] AdapterInfo info() const { return info_; }

    /// Blocks until every submission made so far has finished on the GPU.
    void wait_idle();

    /// Smoke-test workload: uploads `values` to a storage buffer, runs a WGSL
    /// compute shader that doubles every element, reads the result back and
    /// writes it into `values`. An empty input is a no-op success.
    ///
    /// This exists to prove the upload -> dispatch -> readback round trip on a
    /// new machine; the particle runtime will not call it.
    [[nodiscard]] bool run_compute_double(std::vector<float>& values, std::string* error = nullptr);

private:
    GpuDevice() = default;

    /// Drives the event loop until `done` turns true or the budget runs out.
    /// Returns false on timeout.
    bool pump_until(const bool& done, const char* what, std::string* error);

    /// Moves any error captured by the uncaptured-error callback into `error`.
    /// Returns true if an error was pending.
    bool take_device_error(std::string* error);

    detail::Owned<WGPUInstance, wgpuInstanceRelease> instance_{};
    detail::Owned<WGPUAdapter, wgpuAdapterRelease> adapter_{};
    detail::Owned<WGPUDevice, wgpuDeviceRelease> device_{};
    detail::Owned<WGPUQueue, wgpuQueueRelease> queue_{};
    AdapterInfo info_{};
    std::string device_error_{};
};

}  // namespace aether::gpu

#endif  // AETHER_GPU_DEVICE_HPP
