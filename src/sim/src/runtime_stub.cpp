// TEMPORARY STUB so that dependent modules link before the real runtime lands.
// The runtime agent deletes this file when it implements runtime.hpp.
#include "aether/sim/runtime.hpp"
#include "aether/core/error.hpp"

namespace aether::sim {

nlohmann::json Statistics::to_json() const { return {{"time", time}, {"frame", frame}, {"total_alive", total_alive}}; }
std::unique_ptr<IRuntime> create_cpu_runtime(const compiler::CompiledEffect&) {
    throw Error("not_implemented", "runtime stub: real CPU runtime not built yet");
}

}  // namespace aether::sim
