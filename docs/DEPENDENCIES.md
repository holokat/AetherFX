# Dependencies

Policy (from the project brief): before adding any dependency, verify the
upstream project is alive, verify and record the license, pin a version or
commit, confirm redistribution terms, and avoid anything that blocks future
commercial use. Update this table in the same commit that adds the dependency.

Verified 2026-09-17 against GitHub / Homebrew.

| Dependency | Use | Version pinned | License | Redistribution notes | Status |
|---|---|---|---|---|---|
| nlohmann/json | JSON graph serialization | v3.12.0 | MIT | keep license text | in use (FetchContent) |
| Catch2 | unit tests | v3.16.0 | BSL-1.0 | test-only | in use (FetchContent) |
| stb (stb_image, stb_image_write) | PNG/JPG read, PNG write | pinned commit in `cmake/Dependencies.cmake` | MIT / Unlicense (dual) | keep license text | in use (FetchContent) |
| tinyexr | EXR write/read (linear HDR frames) | pinned commit in `cmake/Dependencies.cmake` | BSD-3-Clause (bundles miniz, MIT) | keep license texts | in use (FetchContent) |
| wgpu-native | cross-platform GPU (Vulkan/Metal/DX12) | v29.0.1.1 (prebuilt release, SHA256 pinned in cmake/WgpuNative.cmake) | Apache-2.0 OR MIT | static lib linked; keep NOTICE | optional (`AETHER_WITH_GPU`); verified headless Metal compute on macOS arm64 2026-09-17; `wgpuInstanceWaitAny` unimplemented in this version (use ProcessEvents polling) |
| Slang | shader authoring / cross compilation | v2026.18 | Apache-2.0 WITH LLVM-exception | keep NOTICE | planned (GPU phase) |
| Jolt Physics | rigid debris (Tier 2) | v5.6.0 | MIT | keep license | planned |
| OpenVDB / NanoVDB | sparse volumes (Tier 3) | 13.1.0 (Homebrew) | Apache-2.0 (v12+; MPL-2.0 before) | keep license | planned |
| OpenImageIO | image IO, EXR sequences, flipbooks | 3.1.17 (Homebrew) | Apache-2.0 | heavy dep chain; optional adapter | planned (`AETHER_WITH_OIIO`) |
| meshoptimizer | mesh optimization / LOD for export | v1.2 | MIT | keep license | planned (export phase) |
| Dear ImGui | editor UI | v1.92.9b | MIT | keep license | planned (editor phase) |
| MaterialX | material interchange | latest 1.39.x | Apache-2.0 | optional | planned (interchange) |
| OpenUSD | scene interchange | latest 25.x | Tomorrow Open Source Technology (modified Apache-2.0) | optional; verify terms before shipping | planned (interchange) |
| Python `mcp` | MCP server | >=2.2 | MIT | python only | in use |
| Python `pydantic` | EAD schema | >=2.x | MIT | python only | in use |
| Python `numpy`, `pillow` | evaluation metrics, image handling | current | BSD / MIT-CMU | python only | in use |
| Python `anthropic` | Claude vision adapter | current | MIT | optional extra `[claude]` | optional |

Tooling: CMake >= 3.28, Ninja, Apple clang 21 / GCC 13+ / MSVC 2022, Python >= 3.10.

Rejected / deferred:

* GLM - not needed; `aether/core/math.hpp` is ~300 lines and avoids a dependency.
* glslang/shaderc - superseded by Slang for our purposes.
* pybind11/nanobind - deferred; the Python client speaks JSON-RPC to the CLI,
  which keeps the Python package pure and the MCP server trivial. Revisit for
  in-process performance.
