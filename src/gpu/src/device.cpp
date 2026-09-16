// Headless wgpu-native device bring-up and a compute round-trip smoke workload.
//
// Async-callback pattern for this header generation (webgpu.h "futures" era, as
// shipped with wgpu-native v29.0.1.1):
//
//   * Every async entry point (wgpuInstanceRequestAdapter, wgpuAdapterRequestDevice,
//     wgpuBufferMapAsync, wgpuQueueOnSubmittedWorkDone) takes a `...CallbackInfo`
//     by value and returns a WGPUFuture.
//   * We always use WGPUCallbackMode_AllowProcessEvents and then pump
//     wgpuInstanceProcessEvents() until our own `done` flag flips.
//   * DO NOT call wgpuInstanceWaitAny with this build. Measured on v29.0.1.1:
//     it is a stub (src/unimplemented.rs:233) that raises a non-unwinding Rust
//     panic and ABORTS THE PROCESS - with any futureCount and with timeoutNS
//     both 0 and non-zero. Creating an instance that requires
//     WGPUInstanceFeatureName_TimedWaitAny still succeeds, so the feature flag
//     is not a usable capability probe. wgpuGetInstanceFeatures() aborts the
//     same way. Re-test these when bumping wgpu-native; until then,
//     ProcessEvents + a deadline is the only safe wait.
//   * wgpuDevicePoll() (wgpu.h, native extension) is also called in the pump
//     loop. Measured: ProcessEvents alone DOES drive buffer-map callbacks
//     (it polls devices internally), and DevicePoll alone does too; calling
//     both is marginally faster (~5.5 ms vs ~8.5 ms for a 4 KiB readback) and
//     does not depend on ProcessEvents keeping that behaviour.
//   * Callbacks are captureless lambdas -> function pointers, with state passed
//     through userdata1. They fire synchronously on the pumping thread, so no
//     synchronization is needed. Note that wgpu-native currently invokes the
//     adapter and device callbacks synchronously from inside the request call
//     itself, whatever WGPUCallbackMode was asked for - so the first pump
//     iteration usually finds `done` already true. Do not rely on that.

#include "aether/gpu/device.hpp"

#include <chrono>
#include <cstring>
#include <thread>

#include <webgpu/wgpu.h>

namespace aether::gpu {
namespace {

/// How long any single async step may take before we give up. Adapter/device
/// creation is usually sub-millisecond; a hung driver is the failure we want to
/// report rather than deadlock on.
constexpr std::chrono::seconds kAsyncTimeout{10};

/// WGSL workgroup size for the smoke kernel. 64 is the portable sweet spot: it
/// is <= maxComputeWorkgroupSizeX everywhere and <= the 256-invocation
/// maxComputeInvocationsPerWorkgroup floor guaranteed by the WebGPU limits.
constexpr std::uint32_t kWorkgroupSize = 64;

constexpr const char* kDoubleWgsl = R"WGSL(
@group(0) @binding(0)
var<storage, read_write> data: array<f32>;

@compute @workgroup_size(64)
fn main(@builtin(global_invocation_id) gid: vec3<u32>) {
    let index = gid.x;
    if (index >= arrayLength(&data)) {
        return;
    }
    data[index] = data[index] * 2.0;
}
)WGSL";

/// WGPUStringView over a NUL-terminated C string (WGPU_STRLEN means "call strlen").
WGPUStringView view_of(const char* text) {
    WGPUStringView out = WGPU_STRING_VIEW_INIT;
    if (text != nullptr) {
        out.data = text;
        out.length = WGPU_STRLEN;
    }
    return out;
}

WGPUStringView view_of(std::string_view text) {
    WGPUStringView out = WGPU_STRING_VIEW_INIT;
    out.data = text.data();
    out.length = text.size();
    return out;
}

/// WGPUStringView -> std::string. Handles both the explicit-length and the
/// WGPU_STRLEN (NUL-terminated) conventions, and the null/empty case.
std::string to_string(WGPUStringView text) {
    if (text.data == nullptr) {
        return {};
    }
    if (text.length == WGPU_STRLEN) {
        return std::string{text.data};
    }
    return std::string{text.data, text.length};
}

std::string or_unknown(WGPUStringView text) {
    std::string out = to_string(text);
    return out.empty() ? std::string{"unknown"} : out;
}

const char* backend_name(WGPUBackendType backend) {
    switch (backend) {
        case WGPUBackendType_Null: return "Null";
        case WGPUBackendType_WebGPU: return "WebGPU";
        case WGPUBackendType_D3D11: return "D3D11";
        case WGPUBackendType_D3D12: return "D3D12";
        case WGPUBackendType_Metal: return "Metal";
        case WGPUBackendType_Vulkan: return "Vulkan";
        case WGPUBackendType_OpenGL: return "OpenGL";
        case WGPUBackendType_OpenGLES: return "OpenGLES";
        case WGPUBackendType_Undefined:
        case WGPUBackendType_Force32:
        default: return "unknown";
    }
}

const char* adapter_type_name(WGPUAdapterType type) {
    switch (type) {
        case WGPUAdapterType_DiscreteGPU: return "discrete";
        case WGPUAdapterType_IntegratedGPU: return "integrated";
        case WGPUAdapterType_CPU: return "cpu";
        case WGPUAdapterType_Unknown:
        case WGPUAdapterType_Force32:
        default: return "unknown";
    }
}

const char* error_type_name(WGPUErrorType type) {
    switch (type) {
        case WGPUErrorType_NoError: return "no-error";
        case WGPUErrorType_Validation: return "validation";
        case WGPUErrorType_OutOfMemory: return "out-of-memory";
        case WGPUErrorType_Internal: return "internal";
        case WGPUErrorType_Unknown:
        case WGPUErrorType_Force32:
        default: return "unknown";
    }
}

void set_error(std::string* error, std::string message) {
    if (error != nullptr) {
        *error = std::move(message);
    }
}

// --- async result payloads ---------------------------------------------------

struct AdapterRequest {
    WGPUAdapter adapter{nullptr};
    WGPURequestAdapterStatus status{WGPURequestAdapterStatus_Error};
    std::string message;
    bool done{false};
};

struct DeviceRequest {
    WGPUDevice device{nullptr};
    WGPURequestDeviceStatus status{WGPURequestDeviceStatus_Error};
    std::string message;
    bool done{false};
};

struct MapRequest {
    WGPUMapAsyncStatus status{WGPUMapAsyncStatus_Error};
    std::string message;
    bool done{false};
};

struct WorkDoneRequest {
    WGPUQueueWorkDoneStatus status{WGPUQueueWorkDoneStatus_Error};
    bool done{false};
};

}  // namespace

// --- AdapterInfo -------------------------------------------------------------

std::string AdapterInfo::to_string() const {
    return name + " [" + backend + ", " + adapter_type + ", vendor " + vendor + ", arch " +
           architecture + ", driver " + driver + "]";
}

// --- GpuBuffer ---------------------------------------------------------------

GpuBuffer GpuBuffer::create(WGPUDevice device, std::uint64_t size, WGPUBufferUsage usage,
                            const char* label) {
    GpuBuffer out;
    if (device == nullptr || size == 0) {
        return out;
    }
    WGPUBufferDescriptor desc = WGPU_BUFFER_DESCRIPTOR_INIT;
    desc.label = view_of(label);
    desc.usage = usage;
    desc.size = size;
    desc.mappedAtCreation = 0u;
    WGPUBuffer handle = wgpuDeviceCreateBuffer(device, &desc);
    if (handle == nullptr) {
        return out;
    }
    out.buffer_.reset(handle);
    out.size_ = size;
    return out;
}

// --- GpuShaderModule ---------------------------------------------------------

GpuShaderModule GpuShaderModule::create(WGPUDevice device, std::string_view wgsl,
                                        const char* label) {
    GpuShaderModule out;
    if (device == nullptr) {
        return out;
    }
    WGPUShaderSourceWGSL source = WGPU_SHADER_SOURCE_WGSL_INIT;
    source.chain.sType = WGPUSType_ShaderSourceWGSL;
    source.code = view_of(wgsl);

    WGPUShaderModuleDescriptor desc = WGPU_SHADER_MODULE_DESCRIPTOR_INIT;
    desc.nextInChain = &source.chain;
    desc.label = view_of(label);

    out.module_.reset(wgpuDeviceCreateShaderModule(device, &desc));
    return out;
}

// --- GpuDevice ---------------------------------------------------------------

GpuDevice::~GpuDevice() {
    if (device_) {
        // Drain in-flight work so the driver does not tear down resources that
        // the GPU is still reading.
        wait_idle();
    }
    // Members are released in reverse declaration order: queue, device, adapter,
    // instance - which is the order wgpu-native expects.
}

bool GpuDevice::pump_until(const bool& done, const char* what, std::string* error) {
    const auto deadline = std::chrono::steady_clock::now() + kAsyncTimeout;
    while (!done) {
        wgpuInstanceProcessEvents(instance_.get());
        if (device_) {
            // Non-blocking poll; makes map/work-done callbacks become ready.
            wgpuDevicePoll(device_.get(), /*wait=*/0u, nullptr);
        }
        if (done) {
            break;
        }
        if (std::chrono::steady_clock::now() >= deadline) {
            set_error(error, std::string{"timed out waiting for "} + what);
            return false;
        }
        std::this_thread::sleep_for(std::chrono::microseconds{200});
    }
    return true;
}

bool GpuDevice::take_device_error(std::string* error) {
    if (device_error_.empty()) {
        return false;
    }
    set_error(error, device_error_);
    device_error_.clear();
    return true;
}

void GpuDevice::wait_idle() {
    if (!device_ || !queue_) {
        return;
    }
    WorkDoneRequest request;
    WGPUQueueWorkDoneCallbackInfo info = WGPU_QUEUE_WORK_DONE_CALLBACK_INFO_INIT;
    info.mode = WGPUCallbackMode_AllowProcessEvents;
    info.callback = [](WGPUQueueWorkDoneStatus status, WGPUStringView, void* userdata1, void*) {
        auto* out = static_cast<WorkDoneRequest*>(userdata1);
        out->status = status;
        out->done = true;
    };
    info.userdata1 = &request;
    wgpuQueueOnSubmittedWorkDone(queue_.get(), info);
    pump_until(request.done, "queue idle", nullptr);
}

std::unique_ptr<GpuDevice> GpuDevice::create_headless(std::string* error) {
    // Not make_unique: the constructor is private and this is the only factory.
    std::unique_ptr<GpuDevice> self{new GpuDevice()};

    self->instance_.reset(wgpuCreateInstance(nullptr));
    if (!self->instance_) {
        set_error(error, "wgpuCreateInstance returned null (no usable wgpu backend)");
        return nullptr;
    }

    // --- adapter -------------------------------------------------------------
    WGPURequestAdapterOptions adapter_options = WGPU_REQUEST_ADAPTER_OPTIONS_INIT;
    adapter_options.powerPreference = WGPUPowerPreference_HighPerformance;
    adapter_options.backendType = WGPUBackendType_Undefined;  // any backend
    adapter_options.forceFallbackAdapter = 0u;
    adapter_options.compatibleSurface = nullptr;              // headless

    AdapterRequest adapter_request;
    WGPURequestAdapterCallbackInfo adapter_cb = WGPU_REQUEST_ADAPTER_CALLBACK_INFO_INIT;
    adapter_cb.mode = WGPUCallbackMode_AllowProcessEvents;
    adapter_cb.callback = [](WGPURequestAdapterStatus status, WGPUAdapter adapter,
                             WGPUStringView message, void* userdata1, void*) {
        auto* out = static_cast<AdapterRequest*>(userdata1);
        out->status = status;
        out->adapter = adapter;
        out->message = to_string(message);
        out->done = true;
    };
    adapter_cb.userdata1 = &adapter_request;
    wgpuInstanceRequestAdapter(self->instance_.get(), &adapter_options, adapter_cb);

    if (!self->pump_until(adapter_request.done, "wgpuInstanceRequestAdapter", error)) {
        return nullptr;
    }
    if (adapter_request.status != WGPURequestAdapterStatus_Success ||
        adapter_request.adapter == nullptr) {
        std::string why = adapter_request.message;
        if (why.empty()) {
            why = "wgpuInstanceRequestAdapter reported status " +
                  std::to_string(static_cast<int>(adapter_request.status));
        }
        // Callers key on this prefix to decide "skip" instead of "fail".
        set_error(error, "no adapter: " + why);
        return nullptr;
    }
    self->adapter_.reset(adapter_request.adapter);

    // --- adapter info --------------------------------------------------------
    WGPUAdapterInfo raw_info = WGPU_ADAPTER_INFO_INIT;
    if (wgpuAdapterGetInfo(self->adapter_.get(), &raw_info) == WGPUStatus_Success) {
        self->info_.name = or_unknown(raw_info.device);
        self->info_.vendor = or_unknown(raw_info.vendor);
        self->info_.driver = or_unknown(raw_info.description);
        self->info_.architecture = or_unknown(raw_info.architecture);
        self->info_.backend = backend_name(raw_info.backendType);
        self->info_.adapter_type = adapter_type_name(raw_info.adapterType);
        self->info_.vendor_id = raw_info.vendorID;
        self->info_.device_id = raw_info.deviceID;
        // The strings above are copies; the WGPU-owned members must be freed.
        wgpuAdapterInfoFreeMembers(raw_info);
    }

    // --- device --------------------------------------------------------------
    WGPUDeviceDescriptor device_desc = WGPU_DEVICE_DESCRIPTOR_INIT;
    device_desc.label = view_of("aether.gpu.headless");
    device_desc.requiredFeatureCount = 0;
    device_desc.requiredFeatures = nullptr;
    device_desc.requiredLimits = nullptr;  // adapter defaults
    device_desc.defaultQueue.label = view_of("aether.gpu.queue");
    // Device-lost fires during teardown; AllowSpontaneous keeps it from being
    // reported as "cancelled" noise and costs us nothing since we ignore it.
    device_desc.deviceLostCallbackInfo.mode = WGPUCallbackMode_AllowSpontaneous;
    device_desc.deviceLostCallbackInfo.callback = nullptr;
    // Uncaptured errors (WGSL compile failures, validation) have no callback mode
    // and may fire at any time; we only stash the message.
    device_desc.uncapturedErrorCallbackInfo.callback = [](WGPUDevice const*, WGPUErrorType type,
                                                          WGPUStringView message, void* userdata1,
                                                          void*) {
        auto* owner = static_cast<GpuDevice*>(userdata1);
        std::string text = to_string(message);
        if (text.empty()) {
            text = "(no message)";
        }
        owner->device_error_ = std::string{error_type_name(type)} + " error: " + text;
    };
    device_desc.uncapturedErrorCallbackInfo.userdata1 = self.get();

    DeviceRequest device_request;
    WGPURequestDeviceCallbackInfo device_cb = WGPU_REQUEST_DEVICE_CALLBACK_INFO_INIT;
    device_cb.mode = WGPUCallbackMode_AllowProcessEvents;
    device_cb.callback = [](WGPURequestDeviceStatus status, WGPUDevice device,
                            WGPUStringView message, void* userdata1, void*) {
        auto* out = static_cast<DeviceRequest*>(userdata1);
        out->status = status;
        out->device = device;
        out->message = to_string(message);
        out->done = true;
    };
    device_cb.userdata1 = &device_request;
    wgpuAdapterRequestDevice(self->adapter_.get(), &device_desc, device_cb);

    if (!self->pump_until(device_request.done, "wgpuAdapterRequestDevice", error)) {
        return nullptr;
    }
    if (device_request.status != WGPURequestDeviceStatus_Success ||
        device_request.device == nullptr) {
        std::string why = device_request.message;
        if (why.empty()) {
            why = "wgpuAdapterRequestDevice reported status " +
                  std::to_string(static_cast<int>(device_request.status));
        }
        set_error(error, "device creation failed on adapter '" + self->info_.name + "': " + why);
        return nullptr;
    }
    self->device_.reset(device_request.device);

    self->queue_.reset(wgpuDeviceGetQueue(self->device_.get()));
    if (!self->queue_) {
        set_error(error, "wgpuDeviceGetQueue returned null");
        return nullptr;
    }

    return self;
}

bool GpuDevice::run_compute_double(std::vector<float>& values, std::string* error) {
    if (!device_ || !queue_) {
        set_error(error, "run_compute_double called on an uninitialised GpuDevice");
        return false;
    }
    if (values.empty()) {
        return true;
    }
    device_error_.clear();

    const std::uint64_t byte_size = static_cast<std::uint64_t>(values.size()) * sizeof(float);

    GpuBuffer storage = GpuBuffer::create(
        device_.get(), byte_size,
        WGPUBufferUsage_Storage | WGPUBufferUsage_CopyDst | WGPUBufferUsage_CopySrc,
        "aether.compute_double.storage");
    if (!storage.valid()) {
        take_device_error(error);
        if (error != nullptr && error->empty()) {
            set_error(error, "failed to create the storage buffer");
        }
        return false;
    }

    GpuBuffer staging = GpuBuffer::create(device_.get(), byte_size,
                                          WGPUBufferUsage_MapRead | WGPUBufferUsage_CopyDst,
                                          "aether.compute_double.staging");
    if (!staging.valid()) {
        take_device_error(error);
        if (error != nullptr && error->empty()) {
            set_error(error, "failed to create the staging buffer");
        }
        return false;
    }

    // Upload. wgpuQueueWriteBuffer requires a 4-byte aligned size, which
    // sizeof(float) * n always satisfies.
    wgpuQueueWriteBuffer(queue_.get(), storage.handle(), 0, values.data(),
                         static_cast<std::size_t>(byte_size));

    GpuShaderModule shader =
        GpuShaderModule::create(device_.get(), kDoubleWgsl, "aether.compute_double.wgsl");
    if (!shader.valid()) {
        if (!take_device_error(error)) {
            set_error(error, "WGSL compilation of the double kernel failed");
        }
        return false;
    }

    WGPUComputePipelineDescriptor pipeline_desc = WGPU_COMPUTE_PIPELINE_DESCRIPTOR_INIT;
    pipeline_desc.label = view_of("aether.compute_double.pipeline");
    pipeline_desc.layout = nullptr;  // auto layout from the WGSL bindings
    pipeline_desc.compute.module = shader.handle();
    pipeline_desc.compute.entryPoint = view_of("main");

    detail::Owned<WGPUComputePipeline, wgpuComputePipelineRelease> pipeline{
        wgpuDeviceCreateComputePipeline(device_.get(), &pipeline_desc)};
    if (!pipeline) {
        if (!take_device_error(error)) {
            set_error(error, "wgpuDeviceCreateComputePipeline returned null");
        }
        return false;
    }

    detail::Owned<WGPUBindGroupLayout, wgpuBindGroupLayoutRelease> layout{
        wgpuComputePipelineGetBindGroupLayout(pipeline.get(), 0)};
    if (!layout) {
        if (!take_device_error(error)) {
            set_error(error, "wgpuComputePipelineGetBindGroupLayout returned null");
        }
        return false;
    }

    WGPUBindGroupEntry binding = WGPU_BIND_GROUP_ENTRY_INIT;
    binding.binding = 0;
    binding.buffer = storage.handle();
    binding.offset = 0;
    binding.size = byte_size;

    WGPUBindGroupDescriptor bind_group_desc = WGPU_BIND_GROUP_DESCRIPTOR_INIT;
    bind_group_desc.label = view_of("aether.compute_double.bindgroup");
    bind_group_desc.layout = layout.get();
    bind_group_desc.entryCount = 1;
    bind_group_desc.entries = &binding;

    detail::Owned<WGPUBindGroup, wgpuBindGroupRelease> bind_group{
        wgpuDeviceCreateBindGroup(device_.get(), &bind_group_desc)};
    if (!bind_group) {
        if (!take_device_error(error)) {
            set_error(error, "wgpuDeviceCreateBindGroup returned null");
        }
        return false;
    }

    detail::Owned<WGPUCommandEncoder, wgpuCommandEncoderRelease> encoder{
        wgpuDeviceCreateCommandEncoder(device_.get(), nullptr)};
    if (!encoder) {
        if (!take_device_error(error)) {
            set_error(error, "wgpuDeviceCreateCommandEncoder returned null");
        }
        return false;
    }

    {
        detail::Owned<WGPUComputePassEncoder, wgpuComputePassEncoderRelease> pass{
            wgpuCommandEncoderBeginComputePass(encoder.get(), nullptr)};
        if (!pass) {
            if (!take_device_error(error)) {
                set_error(error, "wgpuCommandEncoderBeginComputePass returned null");
            }
            return false;
        }
        const std::uint32_t groups =
            static_cast<std::uint32_t>((values.size() + kWorkgroupSize - 1) / kWorkgroupSize);
        wgpuComputePassEncoderSetPipeline(pass.get(), pipeline.get());
        wgpuComputePassEncoderSetBindGroup(pass.get(), 0, bind_group.get(), 0, nullptr);
        wgpuComputePassEncoderDispatchWorkgroups(pass.get(), groups, 1, 1);
        wgpuComputePassEncoderEnd(pass.get());
    }

    wgpuCommandEncoderCopyBufferToBuffer(encoder.get(), storage.handle(), 0, staging.handle(), 0,
                                         byte_size);

    detail::Owned<WGPUCommandBuffer, wgpuCommandBufferRelease> commands{
        wgpuCommandEncoderFinish(encoder.get(), nullptr)};
    if (!commands) {
        if (!take_device_error(error)) {
            set_error(error, "wgpuCommandEncoderFinish returned null");
        }
        return false;
    }
    WGPUCommandBuffer raw_commands = commands.get();
    wgpuQueueSubmit(queue_.get(), 1, &raw_commands);

    // --- readback ------------------------------------------------------------
    MapRequest map_request;
    WGPUBufferMapCallbackInfo map_cb = WGPU_BUFFER_MAP_CALLBACK_INFO_INIT;
    map_cb.mode = WGPUCallbackMode_AllowProcessEvents;
    map_cb.callback = [](WGPUMapAsyncStatus status, WGPUStringView message, void* userdata1,
                         void*) {
        auto* out = static_cast<MapRequest*>(userdata1);
        out->status = status;
        out->message = to_string(message);
        out->done = true;
    };
    map_cb.userdata1 = &map_request;
    wgpuBufferMapAsync(staging.handle(), WGPUMapMode_Read, 0, static_cast<std::size_t>(byte_size),
                       map_cb);

    if (!pump_until(map_request.done, "wgpuBufferMapAsync", error)) {
        return false;
    }
    if (map_request.status != WGPUMapAsyncStatus_Success) {
        std::string why = map_request.message;
        if (why.empty()) {
            why = "status " + std::to_string(static_cast<int>(map_request.status));
        }
        set_error(error, "staging buffer map failed: " + why);
        return false;
    }

    const void* mapped =
        wgpuBufferGetConstMappedRange(staging.handle(), 0, static_cast<std::size_t>(byte_size));
    if (mapped == nullptr) {
        wgpuBufferUnmap(staging.handle());
        set_error(error, "wgpuBufferGetConstMappedRange returned null");
        return false;
    }
    std::memcpy(values.data(), mapped, static_cast<std::size_t>(byte_size));
    wgpuBufferUnmap(staging.handle());

    if (take_device_error(error)) {
        return false;
    }
    return true;
}

}  // namespace aether::gpu
