// GPU bootstrap smoke test. Proves that wgpu-native links, that a headless
// device comes up on this machine, and that an upload -> compute -> readback
// round trip produces the right numbers, twice in a row.
//
// Only built when AETHER_WITH_GPU=ON. On a machine with no usable adapter
// (headless CI box, container without a GPU) the test SKIPs rather than fails:
// the GPU module is optional and must never turn a red build on a machine that
// simply has no GPU.

#include <catch2/catch_message.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cstddef>
#include <cstdio>
#include <string>
#include <vector>

#include "aether/gpu/device.hpp"

namespace {

constexpr std::size_t kCount = 1024;

std::vector<float> make_input() {
    std::vector<float> values;
    values.reserve(kCount);
    for (std::size_t i = 0; i < kCount; ++i) {
        // Exactly representable, so the doubling comparison can be exact.
        values.push_back(static_cast<float>(i) * 0.5f - 64.0f);
    }
    return values;
}

}  // namespace

TEST_CASE("headless wgpu device runs a compute round trip", "[gpu]") {
    std::string error;
    auto device = aether::gpu::GpuDevice::create_headless(&error);

    if (!device) {
        if (error.rfind("no adapter", 0) == 0) {
            SKIP("no usable GPU adapter on this machine: " + error);
        }
        FAIL("GpuDevice::create_headless failed: " << error);
    }

    const aether::gpu::AdapterInfo info = device->info();
    UNSCOPED_INFO("adapter: " << info.to_string());
    // Catch2 only prints INFO on failure, and the adapter identity is the whole
    // point of this test on a new machine, so print it unconditionally.
    std::printf("[gpu] adapter: %s\n", info.to_string().c_str());
    std::printf("[gpu] vendorID=0x%04x deviceID=0x%04x\n", info.vendor_id, info.device_id);

    REQUIRE_FALSE(info.name.empty());
    REQUIRE_FALSE(info.backend.empty());
    REQUIRE(info.backend != "unknown");
    REQUIRE(device->device() != nullptr);
    REQUIRE(device->queue() != nullptr);
    REQUIRE(device->adapter() != nullptr);

    const std::vector<float> input = make_input();

    SECTION("doubles every element, reproducibly") {
        std::vector<float> first = input;
        std::string first_error;
        REQUIRE(device->run_compute_double(first, &first_error));
        REQUIRE(first_error.empty());
        REQUIRE(first.size() == input.size());

        for (std::size_t i = 0; i < input.size(); ++i) {
            if (first[i] != input[i] * 2.0f) {
                FAIL("element " << i << ": expected " << (input[i] * 2.0f) << ", got " << first[i]);
            }
        }

        // Same device, same input, second dispatch: identical bits. The engine's
        // determinism rule (docs/ARCHITECTURE.md section 6) starts here.
        std::vector<float> second = input;
        std::string second_error;
        REQUIRE(device->run_compute_double(second, &second_error));
        REQUIRE(second_error.empty());
        REQUIRE(second == first);
    }

    SECTION("empty input is a no-op success") {
        std::vector<float> empty;
        std::string empty_error;
        REQUIRE(device->run_compute_double(empty, &empty_error));
        REQUIRE(empty.empty());
        REQUIRE(empty_error.empty());
    }

    SECTION("a size that is not a multiple of the workgroup size is handled") {
        // 1000 = 15 full workgroups of 64 + a 40-invocation tail; the WGSL bounds
        // check must keep the tail from writing past the end of the array.
        std::vector<float> odd(input.begin(), input.begin() + 1000);
        const std::vector<float> expected_source = odd;
        std::string odd_error;
        REQUIRE(device->run_compute_double(odd, &odd_error));
        REQUIRE(odd_error.empty());
        REQUIRE(odd.size() == expected_source.size());
        for (std::size_t i = 0; i < expected_source.size(); ++i) {
            if (odd[i] != expected_source[i] * 2.0f) {
                FAIL("element " << i << ": expected " << (expected_source[i] * 2.0f) << ", got "
                                << odd[i]);
            }
        }
    }
}
