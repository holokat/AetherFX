// Parallel frame-sequence rendering (see aether/render/renderer.hpp).
//
// The simulation is sequential and stays on the caller's thread; rendering a frame only reads the
// FrameState handed to it, so frames are farmed out to a small pool of workers. Every worker owns
// a private renderer, so nothing is shared except the read-only ResourceSet and the settings, and
// the images (and the PNGs written from them) are bit-identical to the single-threaded path.
//
// Memory is bounded: at most `threads * 2` FrameState copies exist at any moment, and submit()
// blocks the producer once the queue is full rather than letting a long sequence accumulate.

#include "aether/render/renderer.hpp"

#include <algorithm>
#include <condition_variable>
#include <cstdlib>
#include <deque>
#include <exception>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/render/image_io.hpp"

namespace aether::render {
namespace {

constexpr int kMaxFrameThreads = 16;

int env_thread_count() {
    const char* value = std::getenv("AETHER_RENDER_THREADS");
    if (value == nullptr || *value == '\0') return 0;
    char* end = nullptr;
    const long parsed = std::strtol(value, &end, 10);
    if (end == value || parsed <= 0) return 0;  // unparseable or non-positive: fall back
    return static_cast<int>(std::min<long>(parsed, kMaxFrameThreads));
}

}  // namespace

int frame_render_thread_count() {
    if (const int from_env = env_thread_count(); from_env > 0) return from_env;
    const unsigned hardware = std::thread::hardware_concurrency();
    const int available = hardware > 1 ? static_cast<int>(hardware) - 1 : 1;
    return clamp(available, 1, kMaxFrameThreads);
}

struct FrameRenderPool::Impl {
    struct Job {
        size_t index = 0;
        FrameState state;
        CameraDesc camera;
        std::filesystem::path path;
    };

    const ResourceSet& resources;
    RenderSettings settings;
    int thread_count = 1;

    std::mutex mutex;
    std::condition_variable has_work;    // workers wait for a job or for `closed`
    std::condition_variable has_room;    // the producer waits for the queue to drain
    std::deque<Job> queue;
    size_t in_flight = 0;                // queued + being rendered: the FrameState copies alive
    size_t capacity = 2;
    bool closed = false;
    std::exception_ptr error;

    std::vector<FrameRenderResult> results;
    std::vector<std::thread> workers;
    bool finished = false;

    Impl(const ResourceSet& res, RenderSettings s, int threads) : resources(res), settings(std::move(s)) {
        thread_count = threads > 0 ? std::min(threads, kMaxFrameThreads) : frame_render_thread_count();
        capacity = static_cast<size_t>(thread_count) * 2;
        workers.reserve(static_cast<size_t>(thread_count));
        for (int i = 0; i < thread_count; ++i) workers.emplace_back([this] { run(); });
    }

    void run() {
        // One renderer per worker: it owns the scratch buffers rasterisation reuses.
        std::unique_ptr<IRenderer> renderer = create_software_renderer();
        for (;;) {
            Job job;
            {
                std::unique_lock<std::mutex> lock(mutex);
                has_work.wait(lock, [this] { return closed || !queue.empty() || error != nullptr; });
                if (queue.empty() || error != nullptr) return;  // drained, or another worker failed
                job = std::move(queue.front());
                queue.pop_front();
            }
            try {
                Image image = renderer->render(job.state, resources, job.camera, settings);
                const RenderStatistics statistics = renderer->last_statistics();
                job.state = FrameState{};  // release the copy as early as possible
                if (!job.path.empty()) write_png(job.path, image);
                {
                    std::lock_guard<std::mutex> lock(mutex);
                    results[job.index].image = std::move(image);
                    results[job.index].statistics = statistics;
                    results[job.index].path = std::move(job.path);
                }
            } catch (...) {
                std::lock_guard<std::mutex> lock(mutex);
                if (!error) error = std::current_exception();
                queue.clear();
                in_flight = 0;
                has_work.notify_all();
                has_room.notify_all();
                return;
            }
            {
                std::lock_guard<std::mutex> lock(mutex);
                --in_flight;
            }
            has_room.notify_one();
        }
    }

    // Joins every worker, then rethrows the first worker error (if any) as an aether::Error.
    void join_and_rethrow() {
        {
            std::lock_guard<std::mutex> lock(mutex);
            closed = true;
        }
        has_work.notify_all();
        for (std::thread& worker : workers) {
            if (worker.joinable()) worker.join();
        }
        workers.clear();
        std::exception_ptr failure;
        {
            std::lock_guard<std::mutex> lock(mutex);
            failure = error;
        }
        if (!failure) return;
        try {
            std::rethrow_exception(failure);
        } catch (const Error&) {
            throw;
        } catch (const std::exception& e) {
            throw Error("render", std::string("render worker failed: ") + e.what());
        } catch (...) {
            throw Error("render", "render worker failed with an unknown exception");
        }
    }
};

FrameRenderPool::FrameRenderPool(const ResourceSet& resources, RenderSettings settings, int threads)
    : impl_(std::make_unique<Impl>(resources, std::move(settings), threads)) {}

FrameRenderPool::~FrameRenderPool() {
    if (!impl_) return;
    // Destroyed without finish() (an exception on the producer side): stop the workers quietly.
    {
        std::lock_guard<std::mutex> lock(impl_->mutex);
        impl_->closed = true;
        impl_->queue.clear();
        impl_->in_flight = 0;
    }
    impl_->has_work.notify_all();
    impl_->has_room.notify_all();
    for (std::thread& worker : impl_->workers) {
        if (worker.joinable()) worker.join();
    }
}

int FrameRenderPool::threads() const { return impl_->thread_count; }

void FrameRenderPool::submit(FrameState state, CameraDesc camera, std::filesystem::path png_path) {
    bool failed = false;
    size_t index = 0;
    {
        std::unique_lock<std::mutex> lock(impl_->mutex);
        impl_->has_room.wait(lock, [this] { return impl_->in_flight < impl_->capacity || impl_->error != nullptr; });
        if (impl_->error != nullptr) {
            failed = true;
        } else {
            index = impl_->results.size();
            impl_->results.emplace_back();
            Impl::Job job;
            job.index = index;
            job.state = std::move(state);
            job.camera = camera;
            job.path = std::move(png_path);
            impl_->queue.push_back(std::move(job));
            ++impl_->in_flight;
        }
    }
    if (failed) {
        impl_->join_and_rethrow();  // always throws: `error` is set
        return;
    }
    impl_->has_work.notify_one();
}

std::vector<FrameRenderResult> FrameRenderPool::finish() {
    if (impl_->finished) return {};
    impl_->finished = true;
    impl_->join_and_rethrow();
    return std::move(impl_->results);
}

}  // namespace aether::render
