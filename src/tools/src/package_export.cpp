// export_effect(format="package"): a directory any engine importer can read.
// The compiler already resolves windows, seeds, references and bakes textures
// and meshes; this writes that resolved state out as data files plus
// runtime.json, so an importer never needs our compiler. The format is
// specified field by field in docs/PACKAGE_FORMAT.md - keep the two in step.
#include "package_export.hpp"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <utility>
#include <variant>
#include <vector>

#include "aether/core/controls.hpp"
#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/render/image_io.hpp"
#include "aether/render/renderer.hpp"
#include "aether/sim/runtime.hpp"
#include "tool_support.hpp"

namespace aether::tools {
namespace {

constexpr const char* kPackageFormat = "aetherfx-package";
constexpr const char* kGenerator = "aetherfx 0.1.0";
constexpr int kPackageVersion = 1;
constexpr int kMaxTrackSamples = 4096;

// --- small helpers ----------------------------------------------------------

std::string hex16(uint64_t value) {
    std::ostringstream out;
    out << std::hex << std::setw(16) << std::setfill('0') << value;
    return out.str();
}

// Package-relative paths are always written with '/' so an importer can join
// them on any platform without re-parsing.
std::string pkg_path(const std::string& dir, const std::string& file) {
    return dir.empty() ? file : dir + "/" + file;
}

nlohmann::json id_or_null(const std::string& id) {
    return id.empty() ? nlohmann::json() : nlohmann::json(id);
}

nlohmann::json ids_array(const std::vector<NodeId>& ids) {
    nlohmann::json out = nlohmann::json::array();
    for (const NodeId& id : ids) out.push_back(id);
    return out;
}

// Sample times are written at 6 decimals: far below a frame at any sane fps,
// and short enough that runtime.json stays readable and byte-stable.
double round_time(double t) {
    if (!std::isfinite(t)) return 0.0;
    return std::round(t * 1e6) / 1e6;
}

nlohmann::json float_json(float v) { return value_to_json(Value{v}); }

nlohmann::json color_json(Color c) { return value_to_json(Value{c}); }

nlohmann::json vec2_json(Vec2 v) { return value_to_json(Value{v}); }

nlohmann::json vec3_json(Vec3 v) {
    return nlohmann::json::array({float_json(v.x), float_json(v.y), float_json(v.z)});
}

// --- parameters -------------------------------------------------------------

// The window a node's animated parameters are sampled over. `end` is always a
// real time here: an open window (end < 0) is sampled to the effect duration.
struct SampleWindow {
    double start = 0.0;
    double end = 0.0;
};

SampleWindow sample_window(double start, double end, double duration) {
    SampleWindow w;
    w.start = std::max(0.0, start);
    w.end = end < 0.0 ? duration : end;
    if (!(w.end > w.start)) {  // disabled or degenerate window: fall back to the whole effect
        w.start = 0.0;
        w.end = std::max(duration, 0.0);
    }
    return w;
}

// 32 values at t = i/(n-1) over [0,1], so an importer can index a curve by
// normalized age without implementing our interpolation.
nlohmann::json curve_samples(const Curve& curve, int count) {
    nlohmann::json out = nlohmann::json::array();
    for (int i = 0; i < count; ++i) {
        const float t = count > 1 ? static_cast<float>(i) / static_cast<float>(count - 1) : 0.0f;
        out.push_back(float_json(curve.eval(t)));
    }
    return out;
}

nlohmann::json gradient_samples(const Gradient& gradient, int count) {
    nlohmann::json out = nlohmann::json::array();
    for (int i = 0; i < count; ++i) {
        const float t = count > 1 ? static_cast<float>(i) / static_cast<float>(count - 1) : 0.0f;
        out.push_back(color_json(gradient.eval(t)));
    }
    return out;
}

// [[t, value], ...] at `fps` over the node's window, always landing exactly on
// both ends so an importer can clamp without guessing.
nlohmann::json track_samples(const Parameter& parameter, const SampleWindow& window, double fps) {
    const double step = 1.0 / (fps > 0.0 ? fps : 30.0);
    long long count = static_cast<long long>(std::floor((window.end - window.start) / step + 1e-9)) + 1;
    count = std::max<long long>(1, std::min<long long>(count, kMaxTrackSamples));

    nlohmann::json out = nlohmann::json::array();
    double last = window.start;
    for (long long i = 0; i < count; ++i) {
        last = window.start + static_cast<double>(i) * step;
        out.push_back(nlohmann::json::array({round_time(last), value_to_json(parameter.eval(last))}));
    }
    if (window.end > last + 1e-9)
        out.push_back(nlohmann::json::array({round_time(window.end), value_to_json(parameter.eval(window.end))}));
    return out;
}

const Curve& curve_of(const Value& value, const Value& fallback) {
    if (std::holds_alternative<Curve>(value)) return std::get<Curve>(value);
    static const Curve empty;
    return std::holds_alternative<Curve>(fallback) ? std::get<Curve>(fallback) : empty;
}

const Gradient& gradient_of(const Value& value, const Value& fallback) {
    if (std::holds_alternative<Gradient>(value)) return std::get<Gradient>(value);
    static const Gradient empty;
    return std::holds_alternative<Gradient>(fallback) ? std::get<Gradient>(fallback) : empty;
}

// Every parameter the vocabulary defines for this node type, with the value a
// runtime would see: constants inline, curves/gradients with their keys *and*
// a sample table, animated parameters with their track *and* a sample table.
nlohmann::json parameters_json(const Effect& effect, const Node& node, const SampleWindow& window, double fps,
                               int samples) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    nlohmann::json out = nlohmann::json::object();

    for (const ParamSpec& param : spec.params) {
        const Parameter* stored = node.find_param(param.name);
        const Value& value = stored != nullptr ? stored->value : param.default_value;
        switch (param.type) {
            case ValueType::Curve: {
                const Curve& curve = curve_of(value, param.default_value);
                out[param.name] = {{"keys", value_to_json(Value{curve})}, {"samples", curve_samples(curve, samples)}};
                break;
            }
            case ValueType::Gradient: {
                const Gradient& gradient = gradient_of(value, param.default_value);
                out[param.name] = {{"keys", value_to_json(Value{gradient})},
                                   {"samples", gradient_samples(gradient, samples)}};
                break;
            }
            case ValueType::Ref: {
                // Refs live on ports in practice; resolve either spelling to a plain id.
                std::string id = std::holds_alternative<std::string>(value) ? std::get<std::string>(value) : "";
                if (const std::optional<NodeRef> ref = node.input(param.name)) id = ref->node;
                const Node* target = id.empty() ? nullptr : effect.find_node(id);
                out[param.name] = (target != nullptr && target->enabled) ? nlohmann::json(id) : nlohmann::json();
                break;
            }
            default:
                if (stored != nullptr && stored->animated()) {
                    nlohmann::json entry = parameter_to_json(*stored);  // {"value", "track"}
                    entry["samples"] = track_samples(*stored, window, fps);
                    out[param.name] = std::move(entry);
                } else {
                    out[param.name] = value_to_json(value);
                }
                break;
        }
    }

    // Anything stored that the vocabulary does not know (E004 material): keep it
    // rather than silently dropping data an importer might recognise.
    for (const auto& [name, parameter] : node.parameters)
        if (!out.contains(name)) out[name] = parameter_to_json(parameter);
    return out;
}

// --- resolved references ----------------------------------------------------

// Baked variants live under "<id>", "<id>#1", ...; the count is on the mesh node.
int mesh_variants_of(const compiler::CompiledEffect& plan, const std::string& mesh_id) {
    if (mesh_id.empty()) return 0;
    const compiler::CompiledNode* mesh = plan.find(mesh_id);
    return mesh != nullptr ? std::max(1, mesh->mesh_variants) : 1;
}

nlohmann::json resolved_json(const compiler::CompiledEffect& plan, const compiler::CompiledNode& cn,
                             const Node& node) {
    switch (cn.type) {
        case NodeType::Emitter:
            return {{"particle_system", id_or_null(cn.particle_system)},
                    {"shape", param_string(node, "shape")},
                    {"shape_mesh", id_or_null(cn.shape_mesh)},
                    {"shape_curve", id_or_null(cn.shape_curve)}};
        case NodeType::ParticleSystem:
            return {{"material", id_or_null(cn.material_id)},
                    {"sprite", id_or_null(cn.sprite_id)},
                    {"mesh", id_or_null(cn.mesh_id)},
                    {"mesh_variants", mesh_variants_of(plan, cn.mesh_id)},
                    {"forces", ids_array(cn.forces)},
                    {"colliders", ids_array(cn.colliders)},
                    {"trail", id_or_null(cn.trail)}};
        case NodeType::Material: {
            const MaterialDesc* material = plan.resources.material(cn.id);
            return {{"base_texture", id_or_null(material != nullptr ? material->base_texture : cn.texture_id)},
                    {"noise_texture", id_or_null(material != nullptr ? material->noise_texture : std::string())},
                    {"gradient_texture",
                     id_or_null(material != nullptr ? material->gradient_texture : std::string())}};
        }
        case NodeType::Decal:
            return {{"texture", id_or_null(cn.texture_id)}, {"material", id_or_null(cn.material_id)}};
        case NodeType::Trail:
            return {{"source", id_or_null(cn.source_node)},
                    {"particle_system", id_or_null(cn.particle_system)},
                    {"material", id_or_null(cn.material_id)}};
        case NodeType::Beam:
            return {{"origin_node", id_or_null(cn.origin_node)},
                    {"target_node", id_or_null(cn.target_node)},
                    {"material", id_or_null(cn.material_id)}};
        case NodeType::Event:
            return {{"source", id_or_null(cn.source_node)}, {"targets", ids_array(cn.targets)}};
        case NodeType::Force:
            return {{"noise", id_or_null(cn.noise_node)}};
        case NodeType::Mesh:
            return {{"mesh", id_or_null(cn.mesh_id)},
                    {"variants", std::max(1, cn.mesh_variants)},
                    {"material", id_or_null(cn.material_id)}};
        case NodeType::Collider:
            return {{"mesh", id_or_null(cn.shape_mesh)}};
        case NodeType::Volume:
            return {{"sources", ids_array(cn.sources)},
                    {"forces", ids_array(cn.forces)},
                    {"colliders", ids_array(cn.colliders)},
                    {"material", id_or_null(cn.material_id)}};
        case NodeType::Field:
            return {{"source", id_or_null(cn.source_node)}};
        default:
            return nlohmann::json();
    }
}

// --- textures ---------------------------------------------------------------

// What a texture is for, decided by the port that references it. The lowest
// code wins, so the answer does not depend on node order.
enum class TextureUsage { Sprite = 0, Decal = 1, Noise = 2, Gradient = 3 };

const char* usage_name(TextureUsage usage) {
    switch (usage) {
        case TextureUsage::Sprite: return "sprite";
        case TextureUsage::Decal: return "decal";
        case TextureUsage::Noise: return "noise";
        case TextureUsage::Gradient: return "gradient";
    }
    return "sprite";
}

void note_usage(std::map<std::string, TextureUsage>& usage, const std::string& id, TextureUsage kind) {
    if (id.empty()) return;
    const auto it = usage.find(id);
    if (it == usage.end() || static_cast<int>(kind) < static_cast<int>(it->second)) usage[id] = kind;
}

std::map<std::string, TextureUsage> texture_usage(const compiler::CompiledEffect& plan) {
    std::map<std::string, TextureUsage> usage;
    for (const compiler::CompiledNode& cn : plan.nodes) {
        if (cn.type == NodeType::ParticleSystem) note_usage(usage, cn.sprite_id, TextureUsage::Sprite);
        if (cn.type == NodeType::Decal) note_usage(usage, cn.texture_id, TextureUsage::Decal);
    }
    for (const auto& [id, material] : plan.resources.materials) {
        (void)id;
        note_usage(usage, material.base_texture, TextureUsage::Sprite);
        note_usage(usage, material.noise_texture, TextureUsage::Noise);
        note_usage(usage, material.gradient_texture, TextureUsage::Gradient);
    }
    return usage;
}

// --- OBJ --------------------------------------------------------------------

void append_triple(std::string& out, const char* tag, float x, float y, float z) {
    char buffer[96];
    std::snprintf(buffer, sizeof(buffer), "%s %.6f %.6f %.6f\n", tag, static_cast<double>(x), static_cast<double>(y),
                  static_cast<double>(z));
    out += buffer;
}

// A minimal Wavefront OBJ: positions, uvs, normals and triangles, no material
// library. UVs are written unchanged (v = 0 is the top row of the texture).
std::string obj_text(const std::string& name, const MeshData& mesh) {
    const bool has_uvs = mesh.uvs.size() == mesh.positions.size();
    const bool has_normals = mesh.normals.size() == mesh.positions.size();

    std::string out;
    out.reserve(mesh.positions.size() * 64 + mesh.indices.size() * 24 + 256);
    out += "# ";
    out += kGenerator;
    out += " interchange package (docs/PACKAGE_FORMAT.md)\n";
    out += "# units: metres, +Y up, right-handed; uv origin is the top-left of the texture\n";
    out += "o " + name + "\n";
    for (const Vec3& p : mesh.positions) append_triple(out, "v", p.x, p.y, p.z);
    if (has_uvs) {
        char uv_line[64];
        for (const Vec2& uv : mesh.uvs) {
            std::snprintf(uv_line, sizeof(uv_line), "vt %.6f %.6f\n", static_cast<double>(uv.x),
                          static_cast<double>(uv.y));
            out += uv_line;
        }
    }
    if (has_normals)
        for (const Vec3& n : mesh.normals) append_triple(out, "vn", n.x, n.y, n.z);

    char buffer[96];
    for (size_t i = 0; i + 2 < mesh.indices.size(); i += 3) {
        out += 'f';
        for (size_t k = 0; k < 3; ++k) {
            const unsigned long long v = static_cast<unsigned long long>(mesh.indices[i + k]) + 1;
            if (has_uvs && has_normals) std::snprintf(buffer, sizeof(buffer), " %llu/%llu/%llu", v, v, v);
            else if (has_uvs) std::snprintf(buffer, sizeof(buffer), " %llu/%llu", v, v);
            else if (has_normals) std::snprintf(buffer, sizeof(buffer), " %llu//%llu", v, v);
            else std::snprintf(buffer, sizeof(buffer), " %llu", v);
            out += buffer;
        }
        out += '\n';
    }
    return out;
}

void write_text_file(const std::filesystem::path& path, const std::string& text) {
    std::ofstream out(path, std::ios::binary);
    if (!out.good()) throw Error("io", "export_effect: cannot write \"" + path.string() + "\"");
    out << text;
    out.close();
    if (!out.good()) throw Error("io", "export_effect: cannot write \"" + path.string() + "\"");
}

nlohmann::json bounds_json(const MeshData& mesh) {
    const Bounds bounds = mesh.bounds();
    if (!bounds.valid()) return {{"min", vec3_json(Vec3{0, 0, 0})}, {"max", vec3_json(Vec3{0, 0, 0})}};
    return {{"min", vec3_json(bounds.min)}, {"max", vec3_json(bounds.max)}};
}

// --- render settings --------------------------------------------------------

// RenderSettings::to_json widens floats to doubles, which prints 0.35f as
// 0.3499999940395355. Re-round every non-integer through the same shortest
// round-tripping formatter the rest of the package uses.
void compact_numbers(nlohmann::json& value) {
    if (value.is_object() || value.is_array()) {
        for (auto& child : value) compact_numbers(child);
    } else if (value.is_number_float()) {
        value = float_json(static_cast<float>(value.get<double>()));
    }
}

// The engine defaults with effect.metadata.render_settings applied, so the
// package carries a complete description instead of a patch.
nlohmann::json render_settings_json(const Effect& effect) {
    if (!effect.metadata.is_object()) return nlohmann::json();
    const auto it = effect.metadata.find("render_settings");
    if (it == effect.metadata.end() || !it->is_object()) return nlohmann::json();
    nlohmann::json merged = RenderSettings{}.to_json();
    for (const auto& [key, value] : it->items()) merged[key] = value;
    nlohmann::json out = RenderSettings::from_json(merged).to_json();
    compact_numbers(out);
    return out;
}

}  // namespace

// --------------------------------------------------------------------------

nlohmann::json export_package(Session& session, Document& doc, const nlohmann::json& options,
                              const std::filesystem::path& dir) {
    const Effect& effect = doc.effect;
    const double fps = std::max(1.0, arg_number(options, "fps", 30.0));
    const int samples = std::clamp(arg_int(options, "curve_samples", 32), 2, 1024);
    const bool want_exr = arg_bool(options, "exr", false);
    const bool want_preview = arg_bool(options, "preview", true);
    const bool want_obj = arg_bool(options, "obj", true);

    std::error_code ec;
    if (std::filesystem::exists(dir, ec) && !std::filesystem::is_directory(dir, ec))
        throw Error("io", "export_effect: \"" + dir.string() + "\" exists and is not a directory");
    ensure_dir(dir);

    // The compiled plan is the resolved data the package is made of. An effect
    // with validation errors still exports (allow_errors), so an agent can hand
    // a work in progress to an importer and see what is missing.
    compiler::CompileOptions compile_options = compile_options_for(doc, options);
    const compiler::CompiledEffect& cached = session.compiled(doc, compile_options);
    std::unique_ptr<compiler::CompiledEffect> forced;
    const compiler::CompiledEffect* plan = &cached;
    if (cached.nodes.empty() && !effect.nodes.empty()) {
        compile_options.allow_errors = true;
        forced = std::make_unique<compiler::CompiledEffect>(compiler::compile(effect, compile_options));
        plan = forced.get();
    }
    // The plan's own copy of the document has the controls folded in, so the
    // parameters written below are the ones the runtime would actually use
    // (docs/CONTROLS.md); `effect` stays the source the author edits.
    const Effect& resolved = plan->effect;

    std::vector<std::filesystem::path> written;
    const auto record = [&written](std::filesystem::path path) { written.push_back(std::move(path)); };

    // --- effect.json --------------------------------------------------------
    const std::filesystem::path effect_path = dir / "effect.json";
    save_effect_file(effect, effect_path);
    record(effect_path);

    // --- runtime.json -------------------------------------------------------
    nlohmann::json layers = nlohmann::json::array();
    for (const Layer& layer : effect.layers)
        layers.push_back({{"id", layer.id},
                          {"name", layer.name},
                          {"role", std::string(to_string(layer.role))},
                          {"enabled", layer.enabled}});

    nlohmann::json controls = nlohmann::json::array();
    for (const Control& control : effect.controls) controls.push_back(control_to_json(control));

    nlohmann::json nodes = nlohmann::json::array();
    for (const compiler::CompiledNode& cn : plan->nodes) {
        const Node* node = resolved.find_node(cn.id);
        if (node == nullptr) continue;
        const SampleWindow window = sample_window(cn.start_time, cn.end_time, effect.duration);
        nlohmann::json entry{{"id", cn.id},
                             {"type", std::string(to_string(cn.type))},
                             {"layer", node->layer ? nlohmann::json(*node->layer) : nlohmann::json()},
                             {"parent", node->parent ? nlohmann::json(*node->parent) : nlohmann::json()},
                             {"tier", std::string(compiler::to_string(cn.tier))},
                             {"backend", cn.backend},
                             {"window", {{"start", round_time(cn.start_time)}, {"end", round_time(cn.end_time)}}},
                             {"seed", cn.seed},
                             {"parameters", parameters_json(resolved, *node, window, fps, samples)}};
        nlohmann::json resolved = resolved_json(*plan, cn, *node);
        if (!resolved.is_null()) entry["resolved"] = std::move(resolved);
        nodes.push_back(std::move(entry));
    }

    // --- textures -----------------------------------------------------------
    const std::map<std::string, TextureUsage> usage = texture_usage(*plan);
    nlohmann::json textures = nlohmann::json::array();
    nlohmann::json texture_files = nlohmann::json::array();
    if (!plan->resources.textures.empty()) ensure_dir(dir / "textures");
    render::TonemapSettings tonemap;
    tonemap.exposure = 1.0f;
    tonemap.filmic = false;  // a baked texture is data: encode it, do not grade it
    tonemap.srgb = true;
    for (const auto& [id, texture] : plan->resources.textures) {
        const std::string png = pkg_path("textures", id + ".png");
        render::write_png(dir / png, texture.image, tonemap);
        record(dir / png);
        texture_files.push_back(png);

        nlohmann::json entry{{"id", id},
                             {"file", png},
                             {"width", texture.image.width},
                             {"height", texture.image.height},
                             {"frames", std::max(1, texture.frames)},
                             {"frame_width", texture.frame_width()},
                             {"channels", 4},
                             {"color_space", "linear"},
                             {"encoding", "srgb8"},
                             {"usage", usage.count(id) != 0 ? usage_name(usage.at(id)) : "sprite"}};
        if (want_exr) {
            const std::string exr = pkg_path("textures", id + ".exr");
            render::write_exr(dir / exr, texture.image);
            record(dir / exr);
            texture_files.push_back(exr);
            entry["exr"] = exr;
        }
        textures.push_back(std::move(entry));
    }

    // --- meshes -------------------------------------------------------------
    nlohmann::json meshes = nlohmann::json::array();
    nlohmann::json mesh_files = nlohmann::json::array();
    if (want_obj && !plan->resources.meshes.empty()) ensure_dir(dir / "meshes");
    for (const compiler::CompiledNode& cn : plan->nodes) {
        if (cn.type != NodeType::Mesh) continue;
        const MeshData* base = plan->resources.mesh(cn.id);
        if (base == nullptr) continue;
        const int variants = std::max(1, cn.mesh_variants);

        nlohmann::json files = nlohmann::json::array();
        for (int k = 0; k < variants; ++k) {
            // Resource key "<id>#k" is not a legal file name on every platform.
            const std::string key = k == 0 ? cn.id : cn.id + "#" + std::to_string(k);
            const MeshData* mesh = plan->resources.mesh(key);
            if (mesh == nullptr) continue;
            if (!want_obj) continue;  // a package never names a file it did not write
            const std::string name = k == 0 ? cn.id : cn.id + "_v" + std::to_string(k);
            const std::string file = pkg_path("meshes", name + ".obj");
            write_text_file(dir / file, obj_text(name, *mesh));
            record(dir / file);
            files.push_back(file);
            mesh_files.push_back(file);
        }

        meshes.push_back({{"id", cn.id},
                          {"file", files.empty() ? nlohmann::json() : files.front()},
                          {"variants", variants},
                          {"files", std::move(files)},
                          {"vertex_count", base->positions.size()},
                          {"triangle_count", base->triangle_count()},
                          {"bounds", bounds_json(*base)}});
    }

    // --- materials ----------------------------------------------------------
    nlohmann::json materials = nlohmann::json::array();
    for (const auto& [id, material] : plan->resources.materials) {
        materials.push_back({{"id", id},
                             {"blend", std::string(to_string(material.blend))},
                             {"shading", std::string(to_string(material.shading))},
                             {"base_color", color_json(material.base_color)},
                             {"opacity", float_json(material.opacity)},
                             {"emissive_color", color_json(material.emissive_color)},
                             {"emissive_intensity", float_json(material.emissive_intensity)},
                             {"fresnel_power", float_json(material.fresnel_power)},
                             {"dissolve", float_json(material.dissolve)},
                             {"erosion", float_json(material.erosion)},
                             {"distortion", float_json(material.distortion)},
                             {"soft_particle", material.soft_particle},
                             {"depth_fade", float_json(material.depth_fade)},
                             {"base_texture", id_or_null(material.base_texture)},
                             {"noise_texture", id_or_null(material.noise_texture)},
                             {"gradient_texture", id_or_null(material.gradient_texture)},
                             {"uv_scroll", vec2_json(material.uv_scroll)},
                             {"uv_rotate", float_json(material.uv_rotate)},
                             {"double_sided", material.double_sided},
                             {"temperature_gradient", value_to_json(Value{material.temperature_gradient})}});
    }

    nlohmann::json runtime{{"format", "aetherfx-runtime"},
                           {"version", kPackageVersion},
                           {"effect", effect.name},
                           {"duration", effect.duration},
                           {"seed", effect.seed},
                           {"fixed_dt", plan->fixed_dt},
                           {"source_hash", hex16(plan->source_hash)},
                           {"sampling", {{"fps", fps}, {"curve_samples", samples}}},
                           {"timeline", timeline_json(effect.timeline)},
                           {"layers", std::move(layers)},
                           {"controls", std::move(controls)},
                           {"nodes", std::move(nodes)},
                           {"textures", std::move(textures)},
                           {"meshes", std::move(meshes)},
                           {"materials", std::move(materials)},
                           {"render_settings", render_settings_json(effect)},
                           {"diagnostics", plan->diagnostics.to_json()}};

    const std::filesystem::path runtime_path = dir / "runtime.json";
    write_text_file(runtime_path, runtime.dump(2) + "\n");
    record(runtime_path);

    // --- preview ------------------------------------------------------------
    nlohmann::json preview_files = nlohmann::json::array();
    nlohmann::json notes = nlohmann::json::array();
    if (want_preview) {
        // The preview is a courtesy, not the payload: a graph that cannot
        // simulate still gets a package, with a note saying why it has no images.
        try {
            nlohmann::json preview_args = options;
            nlohmann::json settings = render_settings_json(effect);
            if (!settings.is_object()) settings = nlohmann::json::object();
            if (const nlohmann::json& given = arg(options, "settings"); given.is_object())
                for (const auto& [key, value] : given.items()) settings[key] = value;
            preview_args["settings"] = std::move(settings);

            sim::IRuntime& runtime_ref = session.runtime(doc, compile_options_for(doc, options));
            runtime_ref.reset();
            const RenderSettings render_settings = render_settings_for(session, preview_args);
            const std::filesystem::path preview_dir = ensure_dir(dir / "preview");

            std::vector<Image> images;
            for (const double fraction : {0.25, 0.5, 0.75}) {
                const double time = effect.duration * fraction;
                runtime_ref.simulate_to(time);
                const CameraDesc camera = camera_for(session, preview_args, runtime_ref.state().camera);
                Image image =
                    session.renderer().render(runtime_ref.state(), runtime_ref.compiled().resources, camera,
                                              render_settings);
                const std::string file = pkg_path("preview", "frame_" + time_tag(time) + ".png");
                render::write_png(dir / file, image);
                record(dir / file);
                preview_files.push_back(file);
                images.push_back(std::move(image));
            }
            if (!images.empty()) {
                // One row, full resolution: the point of the sheet is to show the
                // intended look, not to fit a long sequence into a thumbnail grid.
                int rows = 0;
                const Image sheet = render::pack_flipbook(images, static_cast<int>(images.size()), rows);
                render::write_png(preview_dir / "contact_sheet.png", sheet);
                record(preview_dir / "contact_sheet.png");
                preview_files.push_back(pkg_path("preview", "contact_sheet.png"));
            }
        } catch (const std::exception& err) {
            notes.push_back(std::string("preview images were skipped: ") + err.what());
        }
    }

    // --- manifest.json ------------------------------------------------------
    nlohmann::json manifest{{"format", kPackageFormat},
                            {"version", kPackageVersion},
                            {"effect", effect.name},
                            {"duration", effect.duration},
                            {"seed", effect.seed},
                            {"fixed_dt", plan->fixed_dt},
                            {"generator", kGenerator},
                            {"source_hash", hex16(plan->source_hash)},
                            {"files",
                             {{"effect", "effect.json"},
                              {"runtime", "runtime.json"},
                              {"textures", std::move(texture_files)},
                              {"meshes", std::move(mesh_files)},
                              {"preview", std::move(preview_files)}}},
                            {"units", {{"length", "meter"}, {"up", "y"}, {"handedness", "right"}}}};
    if (!notes.empty()) manifest["notes"] = std::move(notes);

    const std::filesystem::path manifest_path = dir / "manifest.json";
    write_text_file(manifest_path, manifest.dump(2) + "\n");
    record(manifest_path);

    nlohmann::json files = nlohmann::json::array();
    for (const std::filesystem::path& path : written) files.push_back(path.string());
    return {{"path", dir.string()},
            {"files", std::move(files)},
            {"manifest", std::move(manifest)},
            {"format", "package"}};
}

}  // namespace aether::tools
