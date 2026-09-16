#pragma once
#include <memory>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/frame_state.hpp"

namespace aether::sim {

struct SystemStatistics {
    std::string system_id;
    size_t alive = 0;
    size_t spawned_total = 0;
    size_t died_total = 0;
    size_t peak_alive = 0;
    size_t capacity = 0;
    size_t dropped = 0;   // spawn requests refused by max_particles
    size_t collisions = 0;
};

struct Statistics {
    double time = 0.0;
    uint64_t frame = 0;
    std::vector<SystemStatistics> systems;
    size_t total_alive = 0;
    size_t total_spawned = 0;
    size_t events_fired = 0;
    double last_step_ms = 0.0;
    double total_step_ms = 0.0;
    nlohmann::json to_json() const;
};

// Deterministic fixed-step runtime. Same CompiledEffect => same FrameState sequence.
class IRuntime {
public:
    virtual ~IRuntime() = default;
    virtual std::string backend_name() const = 0;
    virtual void reset() = 0;                  // back to t=0, frame 0, no particles
    virtual void step() = 0;                   // advance one fixed dt
    virtual void simulate_to(double time) = 0; // steps until time() >= time (never interpolates)
    virtual double time() const = 0;
    virtual uint64_t frame_index() const = 0;
    virtual double fixed_dt() const = 0;
    virtual const FrameState& state() const = 0;
    virtual Statistics statistics() const = 0;
    virtual const compiler::CompiledEffect& compiled() const = 0;
};

std::unique_ptr<IRuntime> create_cpu_runtime(const compiler::CompiledEffect& compiled);

}  // namespace aether::sim
