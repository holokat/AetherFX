// docs/AGENT_API.md "io": saving, loading and exporting.
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/validation.hpp"
#include "aether/render/image_io.hpp"
#include "package_export.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

std::string hash_hex(uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << value;
    return out.str();
}

std::filesystem::path save_document(Session& session, Document& doc, const nlohmann::json& args) {
    std::filesystem::path path;
    if (has_arg(args, "path")) path = resolve_output_path(session, arg_string(args, "path"));
    else if (doc.path) path = *doc.path;
    else path = output_path(session, slugify(doc.effect.name) + ".json");
    ensure_dir(path.parent_path());
    save_effect_file(doc.effect, path);
    doc.path = path;
    doc.dirty = false;
    return path;
}

nlohmann::json save_effect(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::filesystem::path path = save_document(session, doc, args);
    return {{"path", path.string()}, {"ok", true}, {"effect_hash", hash_hex(effect_hash(doc.effect))}};
}

nlohmann::json load_effect(Session& session, const nlohmann::json& args) {
    const std::filesystem::path path = resolve_input_path(session, require_string(args, "path", "load_effect"));
    std::error_code ec;
    if (!std::filesystem::is_regular_file(path, ec))
        throw Error("io", "load_effect: no effect document at \"" + path.string() + "\"");
    Document& doc = session.open(path);
    return {{"effect_id", doc.id},
            {"effect", effect_to_json(doc.effect)},
            {"path", path.string()},
            {"diagnostics", validate(doc.effect).to_json()}};
}

nlohmann::json export_json(Session& session, Document& doc, const std::filesystem::path& path) {
    ensure_dir(path.parent_path());
    save_effect_file(doc.effect, path);
    doc.path = path;
    doc.dirty = false;
    return {{"path", path.string()},
            {"files", nlohmann::json::array({path.string()})},
            {"manifest", nlohmann::json()},
            {"format", "json"}};
}

nlohmann::json export_frames(Session& session, Document& doc, const nlohmann::json& options,
                             const std::filesystem::path& dir) {
    PreviewRequest request;
    request.fps = options.value("fps", 24.0);
    request.start = options.value("start", 0.0);
    request.end = options.value("end", -1.0);
    const PreviewResult sequence = render_sequence(session, doc, options, request, dir, true);

    nlohmann::json files = nlohmann::json::array();
    for (const std::filesystem::path& file : sequence.paths) files.push_back(file.string());
    nlohmann::json manifest{{"effect", doc.effect.name},
                            {"duration", doc.effect.duration},
                            {"fps", request.fps},
                            {"frames", sequence.paths.size()},
                            {"loop", false},
                            {"effect_hash", hash_hex(effect_hash(doc.effect))}};
    return {{"path", dir.string()}, {"files", std::move(files)}, {"manifest", std::move(manifest)}, {"format", "frames"}};
}

nlohmann::json export_flipbook(Session& session, Document& doc, const nlohmann::json& options,
                               const std::filesystem::path& path) {
    nlohmann::json render_args = options.is_object() ? options : nlohmann::json::object();
    if (!render_args.contains("width")) render_args["width"] = 256;
    if (!render_args.contains("height")) render_args["height"] = 256;

    PreviewRequest request;
    request.fps = options.value("fps", 24.0);
    request.start = options.value("start", 0.0);
    request.end = options.value("end", -1.0);
    request.max_frames = 1024;
    const PreviewResult sequence = render_sequence(session, doc, render_args, request, path.parent_path(), false);
    if (sequence.images.empty()) throw Error("io", "export_effect: the flipbook has no frames");

    const int columns = std::max(1, options.value("columns", static_cast<int>(std::ceil(std::sqrt(
                                                                 static_cast<double>(sequence.images.size()))))));
    int rows = 0;
    const Image sheet = render::pack_flipbook(sequence.images, columns, rows);
    ensure_dir(path.parent_path());
    render::write_png(path, sheet);

    nlohmann::json manifest{{"effect", doc.effect.name},
                            {"duration", doc.effect.duration},
                            {"fps", request.fps},
                            {"frames", sequence.images.size()},
                            {"columns", columns},
                            {"rows", rows},
                            {"frame_width", sequence.images.front().width},
                            {"frame_height", sequence.images.front().height},
                            {"loop", false},
                            {"effect_hash", hash_hex(effect_hash(doc.effect))}};
    const std::filesystem::path manifest_path = path.string() + ".manifest.json";
    std::ofstream out(manifest_path);
    if (!out.good()) throw Error("io", "export_effect: cannot write \"" + manifest_path.string() + "\"");
    out << manifest.dump(2) << "\n";
    out.close();

    return {{"path", path.string()},
            {"files", nlohmann::json::array({path.string(), manifest_path.string()})},
            {"manifest", std::move(manifest)},
            {"format", "flipbook"}};
}

nlohmann::json export_effect(Session& session, const nlohmann::json& args) {
    Document& doc = session.document_for(args);
    const std::string format = require_string(args, "format", "export_effect");
    const std::filesystem::path path = resolve_output_path(session, require_string(args, "path", "export_effect"));
    const nlohmann::json options = arg(args, "options").is_object() ? args.at("options") : nlohmann::json::object();

    if (format == "json") return export_json(session, doc, path);
    if (format == "frames") return export_frames(session, doc, options, path);
    if (format == "flipbook") return export_flipbook(session, doc, options, path);
    if (format == "package") return export_package(session, doc, options, path);
    throw Error("bad_argument", "export_effect: unknown format \"" + format +
                                    "\"; expected \"json\", \"frames\", \"flipbook\" or \"package\"");
}

}  // namespace

void register_io_tools(ToolRegistry& registry) {
    registry.add({"save_effect",
                  "Write the effect document to disk as JSON. Without `path` it overwrites the file it was loaded "
                  "from, or writes <output_dir>/<effect>.json. Saving is not a mutation and does not touch undo. "
                  "Returns {path, effect_hash}.",
                  make_schema({{"path", prop("string", "Destination file; absolute or relative to the output dir.")}}),
                  false, "io"},
                 save_effect);

    registry.add({"load_effect",
                  "Load an effect document from disk, add it to the session and make it active. Older schema "
                  "versions are migrated forward. Returns {effect_id, effect, diagnostics}.",
                  make_schema({{"path", prop("string", "Effect .json file to load.")}}, {"path"}), true, "io"},
                 load_effect);

    registry.add({"export_effect",
                  "Export the effect: \"json\" writes the document, \"frames\" renders a PNG sequence into a "
                  "directory, \"flipbook\" renders a sprite sheet plus a <path>.manifest.json describing the grid "
                  "(fps, frames, columns, rows, frame size, effect hash) so a game engine can play it back, and "
                  "\"package\" writes an engine-agnostic interchange directory (manifest.json, effect.json, the "
                  "resolved runtime.json, baked textures as PNG, meshes as OBJ and preview images) that an Unreal, "
                  "Unity or Godot importer can rebuild the effect from without this compiler - see "
                  "docs/PACKAGE_FORMAT.md. Returns {path, files, manifest}.",
                  make_schema({{"format", prop("string", "\"json\", \"frames\", \"flipbook\" or \"package\".")},
                               {"path", prop("string", "Destination file (json/flipbook) or directory "
                                                       "(frames/package; may end in .aetherfx).")},
                               {"options", prop("object", "frames/flipbook: fps, columns, width, height, start, end, "
                                                          "camera, settings. package: fps (animated-parameter "
                                                          "sampling rate, default 30), curve_samples (32), exr "
                                                          "(false), preview (true), obj (true), plus width, height, "
                                                          "camera and settings for the preview images.")}},
                              {"format", "path"}),
                  false, "io"},
                 export_effect);
}

}  // namespace aether::tools
