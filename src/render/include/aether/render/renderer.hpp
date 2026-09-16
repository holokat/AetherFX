#pragma once
#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include "aether/core/image.hpp"
#include "aether/core/render_types.hpp"
#include "aether/core/resources.hpp"
#include "aether/core/frame_state.hpp"

namespace aether::render {

class IRenderer {
public:
    virtual ~IRenderer() = default;
    virtual std::string name() const = 0;
    // Returns a linear HDR RGBA image (alpha = coverage over background).
    virtual Image render(const FrameState& state, const ResourceSet& resources, const CameraDesc& camera,
                         const RenderSettings& settings) = 0;
    virtual RenderStatistics last_statistics() const = 0;
};

// Reference CPU renderer. See docs/ARCHITECTURE.md section 7.
std::unique_ptr<IRenderer> create_software_renderer();

// -------------------------------------------------------------------------------------------
// Parallel frame sequences
// -------------------------------------------------------------------------------------------
// Rendering one frame is stateless: the renderer only reads the FrameState it is handed. A frame
// *sequence* can therefore be rendered on a small pool of workers while the simulation - which is
// strictly sequential - keeps stepping on the calling thread.
//
// Determinism is unaffected: every worker owns a private renderer, no two frames share any state,
// and results come back in submission order, so the images and the files written are bit-identical
// to rendering the same sequence on one thread.

// Workers to use: AETHER_RENDER_THREADS when it parses as a positive integer, otherwise
// max(1, hardware_concurrency - 1). Capped at 16 either way.
int frame_render_thread_count();

struct FrameRenderResult {
    Image image;
    RenderStatistics statistics;
    std::filesystem::path path;  // empty when the frame was not written to disk
};

// A bounded producer/consumer pool for frame sequences. The producer calls submit() once per
// frame; at most `threads() * 2` FrameState copies are ever in flight, so simulating a long
// sequence never pulls the whole thing into memory - submit() blocks instead. An exception thrown
// by a worker is rethrown from submit() or finish() after every worker has been joined, as
// aether::Error unless it already was one.
class FrameRenderPool {
public:
    // `resources` must outlive the pool and must not be mutated while it runs.
    // `threads <= 0` means frame_render_thread_count().
    FrameRenderPool(const ResourceSet& resources, RenderSettings settings, int threads = 0);
    ~FrameRenderPool();
    FrameRenderPool(const FrameRenderPool&) = delete;
    FrameRenderPool& operator=(const FrameRenderPool&) = delete;

    int threads() const;
    // Queues one frame. `png_path` empty keeps the image in memory only; otherwise the frame is
    // tonemapped and written there by the worker.
    void submit(FrameState state, CameraDesc camera, std::filesystem::path png_path);
    // Joins the workers and returns one result per submit(), in submission order. Calling it
    // twice returns an empty vector the second time.
    std::vector<FrameRenderResult> finish();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace aether::render
