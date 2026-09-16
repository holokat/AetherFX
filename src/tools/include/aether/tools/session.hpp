#pragma once
// Session: everything an agent manipulates. Owns documents, undo/redo, caches.
#include <filesystem>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/effect.hpp"
#include "aether/render/renderer.hpp"
#include "aether/sim/runtime.hpp"

namespace aether::tools {

struct Document {
    std::string id;                          // session-unique handle ("fx_1", ...)
    Effect effect;
    std::optional<std::filesystem::path> path;
    std::vector<nlohmann::json> undo_stack;  // snapshots (effect_to_json) before each mutation
    std::vector<nlohmann::json> redo_stack;
    // caches, invalidated on mutation
    std::shared_ptr<compiler::CompiledEffect> compiled;
    std::shared_ptr<sim::IRuntime> runtime;
    uint64_t compiled_hash = 0;
    bool dirty = false;
    // Which compile the live runtime belongs to, and the timestep it was built
    // with (a different fixed_dt is a different compile).
    uint64_t runtime_hash = 0;
    double compiled_fixed_dt = 1.0 / 60.0;
    // Last results, for inspect_statistics ("last known").
    nlohmann::json last_simulation_statistics;  // null until something simulated
    nlohmann::json last_render_statistics;      // null until something rendered
};

class Session {
public:
    explicit Session(std::filesystem::path output_dir = "out");

    // Documents
    Document& create(Effect effect);                     // becomes active
    Document& open(const std::filesystem::path& path);   // becomes active
    Document& get(const std::string& id);                // throws Error("no_such_effect")
    Document& active();                                  // throws Error("no_active_effect")
    // args["effect_id"] when present, else active(). The tool argument every
    // tool accepts (docs/AGENT_API.md).
    Document& document_for(const nlohmann::json& args);
    Document* find(const std::string& id);
    void set_active(const std::string& id);
    bool close(const std::string& id);
    std::vector<std::string> ids() const;

    // Mutation protocol: call begin_mutation() before changing doc.effect,
    // end_mutation() after. Records undo snapshot, clears redo, invalidates caches.
    void begin_mutation(Document& doc);
    void end_mutation(Document& doc);
    bool undo(Document& doc);
    bool redo(Document& doc);
    size_t max_undo = 200;

    // Compiled/runtime caches (rebuilt when the effect hash changed).
    const compiler::CompiledEffect& compiled(Document& doc, const compiler::CompileOptions& options = {});
    sim::IRuntime& runtime(Document& doc, const compiler::CompileOptions& options = {});
    render::IRenderer& renderer();

    const std::filesystem::path& output_dir() const { return output_dir_; }
    void set_output_dir(std::filesystem::path p) { output_dir_ = std::move(p); }
    // Per-session render settings/camera defaults that tools may override per call.
    RenderSettings default_render_settings;
    CameraDesc default_camera;

private:
    std::filesystem::path output_dir_;
    std::map<std::string, Document> documents_;
    std::vector<std::string> order_;  // creation order, for list_effects
    std::string active_id_;
    int next_id_ = 1;
    std::unique_ptr<render::IRenderer> renderer_;
};

}  // namespace aether::tools
