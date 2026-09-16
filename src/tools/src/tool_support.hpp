#pragma once
// Internal helpers shared by the tool implementations in src/tools/src.
// Not a public header: nothing outside this directory includes it.
#include <filesystem>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/diagnostics.hpp"
#include "aether/core/effect.hpp"
#include "aether/core/render_types.hpp"
#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/spec.hpp"
#include "aether/tools/registry.hpp"
#include "aether/tools/session.hpp"

namespace aether::tools {

// --- JSON Schema construction ----------------------------------------------

// One `properties` entry: {"type": ..., "description": ...}. `type` may be a
// single JSON type name or a comma separated list ("number,string").
nlohmann::json prop(const std::string& type, const std::string& description);
// A property that accepts any JSON value (parameter values, metadata).
nlohmann::json prop_any(const std::string& description);
// An object schema. `effect_id` is appended automatically because every tool
// accepts it (docs/AGENT_API.md).
nlohmann::json make_schema(nlohmann::json properties, std::vector<std::string> required = {},
                           bool additional_properties = false);

// --- argument access --------------------------------------------------------

bool has_arg(const nlohmann::json& args, const char* key);
const nlohmann::json& arg(const nlohmann::json& args, const char* key);
std::string arg_string(const nlohmann::json& args, const char* key, const std::string& fallback = "");
double arg_number(const nlohmann::json& args, const char* key, double fallback);
int arg_int(const nlohmann::json& args, const char* key, int fallback);
bool arg_bool(const nlohmann::json& args, const char* key, bool fallback);
// Required argument of a given kind; throws Error("bad_argument") with the tool name.
std::string require_string(const nlohmann::json& args, const char* key, const char* tool);
double require_number(const nlohmann::json& args, const char* key, const char* tool);

// --- documents and nodes ----------------------------------------------------

// The node, or Error("E008") naming the ids that do exist.
Node& require_node(Effect& effect, const std::string& id);
const Node& require_node(const Effect& effect, const std::string& id);
Layer& require_layer(Effect& effect, const std::string& id);

// Core's node/layer encoding, reused so tools never invent a second one.
nlohmann::json node_json(const Node& node);
nlohmann::json layer_json(const Layer& layer);
nlohmann::json timeline_json(const Timeline& timeline);

// Records an undo snapshot on construction and rolls the document back when the
// handler throws before commit(): a failed tool call must leave no trace.
class Mutation {
public:
    Mutation(Session& session, Document& doc);
    ~Mutation();
    Mutation(const Mutation&) = delete;
    Mutation& operator=(const Mutation&) = delete;
    void commit();

private:
    Session& session_;
    Document& doc_;
    std::vector<nlohmann::json> redo_;  // restored on rollback: a failed call is not an edit
    bool committed_ = false;
};

// {"ok": true, "diagnostics": ...} plus whatever the tool adds.
nlohmann::json ok_with(const Diagnostics& diagnostics);

// --- parameters -------------------------------------------------------------

// Parses `value` for a spec'd parameter, throwing ToolError("E005") with the
// expected type and an example when it does not fit.
Parameter parse_parameter(const Node& node, const ParamSpec& spec, const nlohmann::json& value);
Value parse_value(const Node& node, const ParamSpec& spec, const nlohmann::json& value);
// The parameter spec, or ToolError("E004") listing near matches.
const ParamSpec& require_param_spec(const Node& node, const std::string& name);
// A short example value for a type, used in error messages.
std::string example_for(const ParamSpec& spec);

// --- paths ------------------------------------------------------------------

// "Fire AOE" -> "fire_aoe"; always a usable file name.
std::string slugify(const std::string& name);
std::filesystem::path ensure_dir(const std::filesystem::path& dir);
// <output_dir>/<name>, creating the directory.
std::filesystem::path output_path(const Session& session, const std::string& file_name);
// examples/ next to the source tree (AETHER_SOURCE_DIR) or next to the binary.
std::filesystem::path examples_dir();
// Output paths are absolute or relative to the session output directory.
std::filesystem::path resolve_output_path(const Session& session, const std::string& path);
// Input paths are taken as given when they exist, else relative to the output directory.
std::filesystem::path resolve_input_path(const Session& session, const std::string& path);
// "0.5" -> "0.500", for frame file names.
std::string time_tag(double time);

// --- compile / runtime ------------------------------------------------------

// The document's current timestep, overridden by an explicit `fixed_dt`
// argument: choosing a timestep once keeps it for the whole session instead of
// silently recompiling back to 1/60.
compiler::CompileOptions compile_options_for(const Document& doc, const nlohmann::json& args);

// --- render helpers ---------------------------------------------------------

// session defaults <- width/height arguments <- `settings` object.
RenderSettings render_settings_for(const Session& session, const nlohmann::json& args);
// args.camera > FrameState camera (the effect's camera node) > session default.
CameraDesc camera_for(const Session& session, const nlohmann::json& args, const std::optional<CameraDesc>& from_state);
// Average of the emitter/mesh node positions at `time`, or the origin.
Vec3 effect_center(const Effect& effect, double time);
// Box-filtered downscale to `width` px keeping the aspect ratio.
Image thumbnail(const Image& image, int width);
// ceil(sqrt(n)) columns, at most 36 evenly sampled frames, packed with pack_flipbook.
std::filesystem::path write_contact_sheet(const std::vector<Image>& frames, const std::filesystem::path& path);

// --- registration (one translation unit per docs/AGENT_API.md section) ------

void register_effect_tools(ToolRegistry& registry);
void register_graph_tools(ToolRegistry& registry);
void register_parameter_tools(ToolRegistry& registry);
void register_timeline_tools(ToolRegistry& registry);
void register_simulate_tools(ToolRegistry& registry);
void register_render_tools(ToolRegistry& registry);
void register_inspect_tools(ToolRegistry& registry);
void register_evaluate_tools(ToolRegistry& registry);
void register_io_tools(ToolRegistry& registry);
void register_history_tools(ToolRegistry& registry);

// Shared by render_preview and export_effect(format=frames|flipbook).
struct PreviewRequest {
    double fps = 24.0;
    double start = 0.0;
    double end = -1.0;  // < 0 = effect duration
    int max_frames = 4096;
};
struct PreviewResult {
    std::vector<std::filesystem::path> paths;
    std::vector<Image> images;
    std::vector<double> times;
    nlohmann::json simulation_statistics;
    nlohmann::json render_statistics;
};
PreviewResult render_sequence(Session& session, Document& doc, const nlohmann::json& args, const PreviewRequest& request,
                              const std::filesystem::path& out_dir, bool write_files);

}  // namespace aether::tools
