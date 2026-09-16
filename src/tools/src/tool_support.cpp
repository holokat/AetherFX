#include "tool_support.hpp"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <sstream>

#include "aether/core/error.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/validation.hpp"
#include "aether/render/image_io.hpp"

#if defined(__APPLE__)
#include <mach-o/dyld.h>
#elif defined(_WIN32)
#include <windows.h>
#endif

namespace aether::tools {
namespace {

std::vector<std::string> split_types(const std::string& types) {
    std::vector<std::string> out;
    std::string current;
    for (char c : types) {
        if (c == ',') {
            if (!current.empty()) out.push_back(current);
            current.clear();
        } else if (c != ' ') {
            current.push_back(c);
        }
    }
    if (!current.empty()) out.push_back(current);
    return out;
}

std::filesystem::path executable_dir() {
    std::string buffer(4096, '\0');
#if defined(__APPLE__)
    uint32_t size = static_cast<uint32_t>(buffer.size());
    if (_NSGetExecutablePath(buffer.data(), &size) != 0) return std::filesystem::current_path();
    buffer.resize(std::char_traits<char>::length(buffer.c_str()));
#elif defined(_WIN32)
    DWORD written = GetModuleFileNameA(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
    if (written == 0) return std::filesystem::current_path();
    buffer.resize(written);
#else
    std::error_code ec;
    std::filesystem::path self = std::filesystem::read_symlink("/proc/self/exe", ec);
    if (!ec) return self.parent_path();
    return std::filesystem::current_path();
#endif
    std::error_code ec;
    std::filesystem::path path = std::filesystem::weakly_canonical(std::filesystem::path(buffer), ec);
    if (ec) path = std::filesystem::path(buffer);
    return path.parent_path();
}

}  // namespace

// --- JSON Schema ------------------------------------------------------------

nlohmann::json prop(const std::string& type, const std::string& description) {
    const std::vector<std::string> types = split_types(type);
    nlohmann::json j;
    if (types.size() == 1) {
        j["type"] = types.front();
    } else {
        j["type"] = types;
    }
    j["description"] = description;
    return j;
}

nlohmann::json prop_any(const std::string& description) {
    return prop("boolean,integer,number,string,array,object", description);
}

nlohmann::json make_schema(nlohmann::json properties, std::vector<std::string> required, bool additional_properties) {
    if (!properties.is_object()) properties = nlohmann::json::object();
    if (!properties.contains("effect_id"))
        properties["effect_id"] = prop("string", "Effect to act on (\"fx_1\", ...). Omit for the active effect.");
    nlohmann::json schema{{"type", "object"}, {"properties", std::move(properties)}};
    nlohmann::json req = nlohmann::json::array();
    for (const std::string& r : required) req.push_back(r);
    schema["required"] = std::move(req);
    schema["additionalProperties"] = additional_properties;
    return schema;
}

// --- arguments --------------------------------------------------------------

bool has_arg(const nlohmann::json& args, const char* key) {
    if (!args.is_object()) return false;
    auto it = args.find(key);
    return it != args.end() && !it->is_null();
}

const nlohmann::json& arg(const nlohmann::json& args, const char* key) {
    static const nlohmann::json null_value;
    if (!has_arg(args, key)) return null_value;
    return args.at(key);
}

std::string arg_string(const nlohmann::json& args, const char* key, const std::string& fallback) {
    const nlohmann::json& value = arg(args, key);
    return value.is_string() ? value.get<std::string>() : fallback;
}

double arg_number(const nlohmann::json& args, const char* key, double fallback) {
    const nlohmann::json& value = arg(args, key);
    return value.is_number() ? value.get<double>() : fallback;
}

int arg_int(const nlohmann::json& args, const char* key, int fallback) {
    const nlohmann::json& value = arg(args, key);
    if (value.is_number_integer()) return value.get<int>();
    if (value.is_number()) return static_cast<int>(std::lround(value.get<double>()));
    return fallback;
}

bool arg_bool(const nlohmann::json& args, const char* key, bool fallback) {
    const nlohmann::json& value = arg(args, key);
    return value.is_boolean() ? value.get<bool>() : fallback;
}

std::string require_string(const nlohmann::json& args, const char* key, const char* tool) {
    const nlohmann::json& value = arg(args, key);
    if (!value.is_string())
        throw Error("bad_argument", std::string(tool) + ": argument \"" + key + "\" is required and must be a string");
    return value.get<std::string>();
}

double require_number(const nlohmann::json& args, const char* key, const char* tool) {
    const nlohmann::json& value = arg(args, key);
    if (!value.is_number())
        throw Error("bad_argument", std::string(tool) + ": argument \"" + key + "\" is required and must be a number");
    return value.get<double>();
}

// --- documents and nodes ----------------------------------------------------

namespace {

std::string known_node_ids(const Effect& effect) {
    std::string ids;
    int shown = 0;
    for (const Node& n : effect.nodes) {
        if (shown++ == 12) {
            ids += ", ...";
            break;
        }
        ids += (ids.empty() ? "" : ", ") + n.id;
    }
    return ids;
}

}  // namespace

const Node& require_node(const Effect& effect, const std::string& id) {
    const Node* node = effect.find_node(id);
    if (node == nullptr) {
        const std::string known = known_node_ids(effect);
        throw ToolError("E008", "unresolved reference: no node \"" + id + "\" in effect \"" + effect.name + "\"" +
                                    (known.empty() ? "; the effect has no nodes" : "; nodes: " + known),
                        id);
    }
    return *node;
}

Node& require_node(Effect& effect, const std::string& id) {
    return const_cast<Node&>(require_node(const_cast<const Effect&>(effect), id));
}

Layer& require_layer(Effect& effect, const std::string& id) {
    Layer* layer = effect.find_layer(id);
    if (layer == nullptr) {
        std::string known;
        for (const Layer& l : effect.layers) known += (known.empty() ? "" : ", ") + l.id;
        throw ToolError("E014", "unknown layer \"" + id + "\"" +
                                    (known.empty() ? "; the effect has no layers" : "; layers: " + known));
    }
    return *layer;
}

nlohmann::json node_json(const Node& node) {
    Effect holder;
    holder.nodes.push_back(node);
    return effect_to_json(holder)["nodes"][0];
}

nlohmann::json layer_json(const Layer& layer) {
    Effect holder;
    holder.layers.push_back(layer);
    return effect_to_json(holder)["layers"][0];
}

nlohmann::json timeline_json(const Timeline& timeline) {
    Effect holder;
    holder.timeline = timeline;
    return effect_to_json(holder)["timeline"];
}

Mutation::Mutation(Session& session, Document& doc) : session_(session), doc_(doc), redo_(doc.redo_stack) {
    session_.begin_mutation(doc_);  // clears the redo stack, which rollback puts back
}

Mutation::~Mutation() {
    if (committed_) return;
    // The handler threw: put the document back exactly as it was.
    try {
        if (!doc_.undo_stack.empty()) {
            doc_.effect = effect_from_json(doc_.undo_stack.back());
            doc_.undo_stack.pop_back();
        }
        doc_.redo_stack = std::move(redo_);
    } catch (...) {  // NOLINT(bugprone-empty-catch) - a destructor may not throw
    }
}

void Mutation::commit() {
    committed_ = true;
    session_.end_mutation(doc_);
}

nlohmann::json ok_with(const Diagnostics& diagnostics) {
    return {{"ok", true}, {"diagnostics", diagnostics.to_json()}};
}

// --- parameters -------------------------------------------------------------

namespace {

// Levenshtein-ish "looks like a typo" test, cheap and good enough for hints.
bool similar(const std::string& a, const std::string& b) {
    if (a == b) return true;
    if (a.size() + 1 < b.size() || b.size() + 1 < a.size()) return false;
    size_t differences = 0;
    for (size_t i = 0, j = 0; i < a.size() && j < b.size(); ++i, ++j)
        if (a[i] != b[j] && ++differences > 2) return false;
    return true;
}

}  // namespace

std::string example_for(const ParamSpec& spec) {
    switch (spec.type) {
        case ValueType::Bool: return "true";
        case ValueType::Int: return "4";
        case ValueType::Float: return "1.5";
        case ValueType::Vec2: return "[1.0, 1.0]";
        case ValueType::Vec3: return "[0.0, 1.0, 0.0]";
        case ValueType::Vec4: return "[1.0, 1.0, 1.0, 1.0]";
        case ValueType::Color: return "[1.0, 0.5, 0.1, 1.0] or \"#ff8019\"";
        case ValueType::Enum: return spec.enum_values.empty() ? "\"value\"" : "\"" + spec.enum_values.front() + "\"";
        case ValueType::String:
        case ValueType::Ref: return "\"node_id\"";
        case ValueType::Curve: return "[[0.0, 1.0], [1.0, 0.0]]";
        case ValueType::Gradient: return "[[0.0, [1,1,1,1]], [1.0, [1,0,0,1]]]";
        case ValueType::FloatList: return "[0.0, 0.5]";
        case ValueType::Vec3List: return "[[0,0,0], [0,1,0]]";
        case ValueType::Json: return "{}";
    }
    return "null";
}

const ParamSpec& require_param_spec(const Node& node, const std::string& name) {
    const NodeSpec& node_spec = SpecRegistry::instance().get(node.type);
    if (const ParamSpec* spec = node_spec.find_param(name)) return *spec;
    std::string hint;
    for (const ParamSpec& candidate : node_spec.params) {
        if (!similar(name, candidate.name)) continue;
        hint += (hint.empty() ? "" : ", ") + candidate.name;
    }
    if (hint.empty()) {
        for (const ParamSpec& candidate : node_spec.params) {
            if (hint.size() > 160) {
                hint += ", ...";
                break;
            }
            hint += (hint.empty() ? "" : ", ") + candidate.name;
        }
    }
    throw ToolError("E004",
                    "node type \"" + std::string(to_string(node.type)) + "\" has no parameter \"" + name +
                        "\"; did you mean one of [" + hint + "]? (describe_vocabulary lists them all)",
                    node.id, name);
}

Value parse_value(const Node& node, const ParamSpec& spec, const nlohmann::json& value) {
    try {
        return value_from_json(value, spec.type);
    } catch (const Error&) {
        throw ToolError("E005",
                        "parameter \"" + spec.name + "\" of node \"" + node.id + "\" expects " +
                            std::string(to_string(spec.type)) + " but got " + std::string(value.type_name()) +
                            " " + value.dump() + "; example: " + example_for(spec),
                        node.id, spec.name);
    }
}

Parameter parse_parameter(const Node& node, const ParamSpec& spec, const nlohmann::json& value) {
    try {
        return parameter_from_json(value, spec.type);
    } catch (const Error&) {
        throw ToolError("E005",
                        "parameter \"" + spec.name + "\" of node \"" + node.id + "\" expects " +
                            std::string(to_string(spec.type)) + " but got " + std::string(value.type_name()) +
                            " " + value.dump() + "; example: " + example_for(spec),
                        node.id, spec.name);
    }
}

// --- paths ------------------------------------------------------------------

std::string slugify(const std::string& name) {
    std::string out;
    for (char c : name) {
        const char lower = (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c;
        if ((lower >= 'a' && lower <= 'z') || (lower >= '0' && lower <= '9')) {
            out.push_back(lower);
        } else if (!out.empty() && out.back() != '_') {
            out.push_back('_');
        }
    }
    while (!out.empty() && out.back() == '_') out.pop_back();
    return out.empty() ? "effect" : out;
}

std::filesystem::path ensure_dir(const std::filesystem::path& dir) {
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

std::filesystem::path output_path(const Session& session, const std::string& file_name) {
    ensure_dir(session.output_dir());
    return session.output_dir() / file_name;
}

std::filesystem::path examples_dir() {
    std::vector<std::filesystem::path> candidates;
#ifdef AETHER_SOURCE_DIR
    candidates.emplace_back(std::filesystem::path(AETHER_SOURCE_DIR) / "examples");
#endif
    const std::filesystem::path bin = executable_dir();
    candidates.push_back(bin / ".." / ".." / "examples");
    candidates.push_back(bin / ".." / "examples");
    std::filesystem::path up = bin;
    for (int i = 0; i < 5 && !up.empty(); ++i) {
        candidates.push_back(up / "examples");
        up = up.parent_path();
    }
    candidates.push_back(std::filesystem::current_path() / "examples");
    for (const std::filesystem::path& candidate : candidates) {
        std::error_code ec;
        if (std::filesystem::is_directory(candidate / "effects", ec)) return std::filesystem::weakly_canonical(candidate, ec);
    }
    return candidates.front();
}

std::filesystem::path resolve_output_path(const Session& session, const std::string& path) {
    const std::filesystem::path p(path);
    if (p.is_absolute()) return p.lexically_normal();
    ensure_dir(session.output_dir());
    return (session.output_dir() / p).lexically_normal();
}

std::filesystem::path resolve_input_path(const Session& session, const std::string& path) {
    std::filesystem::path p(path);
    std::error_code ec;
    if (std::filesystem::exists(p, ec)) return p;
    if (!p.is_absolute()) {
        const std::filesystem::path in_output = session.output_dir() / p;
        if (std::filesystem::exists(in_output, ec)) return in_output;
    }
    return p;
}

std::string time_tag(double time) {
    std::ostringstream out;
    out.precision(3);
    out << std::fixed << time;
    return out.str();
}

// --- compile / runtime ------------------------------------------------------

compiler::CompileOptions compile_options_for(const Document& doc, const nlohmann::json& args) {
    compiler::CompileOptions options;
    options.fixed_dt = doc.compiled_fixed_dt;
    if (has_arg(args, "fixed_dt")) {
        const double dt = arg_number(args, "fixed_dt", options.fixed_dt);
        if (!(dt > 0.0)) throw Error("bad_argument", "fixed_dt must be > 0 (got " + std::to_string(dt) + ")");
        options.fixed_dt = dt;
    }
    return options;
}

// --- render helpers ---------------------------------------------------------

RenderSettings render_settings_for(const Session& session, const nlohmann::json& args) {
    nlohmann::json merged = session.default_render_settings.to_json();
    if (has_arg(args, "width")) merged["width"] = arg_int(args, "width", merged["width"].get<int>());
    if (has_arg(args, "height")) merged["height"] = arg_int(args, "height", merged["height"].get<int>());
    if (has_arg(args, "height_px")) merged["height"] = arg_int(args, "height_px", merged["height"].get<int>());
    const nlohmann::json& settings = arg(args, "settings");
    if (settings.is_object())
        for (const auto& [key, value] : settings.items()) merged[key] = value;
    RenderSettings out = RenderSettings::from_json(merged);
    out.width = std::max(1, out.width);
    out.height = std::max(1, out.height);
    return out;
}

CameraDesc camera_for(const Session& session, const nlohmann::json& args, const std::optional<CameraDesc>& from_state) {
    const nlohmann::json& camera = arg(args, "camera");
    if (camera.is_object()) {
        nlohmann::json base = (from_state ? *from_state : session.default_camera).to_json();
        for (const auto& [key, value] : camera.items()) base[key] = value;
        return CameraDesc::from_json(base);
    }
    if (from_state) return *from_state;
    return session.default_camera;
}

Vec3 effect_center(const Effect& effect, double time) {
    Vec3 sum{0, 0, 0};
    int count = 0;
    for (const Node& node : effect.nodes) {
        if (!node.enabled) continue;
        if (node.type != NodeType::Emitter && node.type != NodeType::Mesh) continue;
        sum += effect.world_transform(node, time).translation_part();
        ++count;
    }
    return count > 0 ? sum / static_cast<float>(count) : Vec3{0, 0, 0};
}

Image thumbnail(const Image& image, int width) {
    if (image.empty() || width <= 0 || image.width <= width) return image;
    const int height = std::max(1, static_cast<int>(std::lround(static_cast<double>(image.height) * width / image.width)));
    Image out(width, height);
    for (int y = 0; y < height; ++y) {
        const int y0 = static_cast<int>(static_cast<int64_t>(y) * image.height / height);
        const int y1 = std::max(y0 + 1, static_cast<int>(static_cast<int64_t>(y + 1) * image.height / height));
        for (int x = 0; x < width; ++x) {
            const int x0 = static_cast<int>(static_cast<int64_t>(x) * image.width / width);
            const int x1 = std::max(x0 + 1, static_cast<int>(static_cast<int64_t>(x + 1) * image.width / width));
            Color sum{0, 0, 0, 0};
            int count = 0;
            for (int sy = y0; sy < y1 && sy < image.height; ++sy) {
                for (int sx = x0; sx < x1 && sx < image.width; ++sx) {
                    sum += image.get(sx, sy);
                    ++count;
                }
            }
            out.set(x, y, count > 0 ? sum * (1.0f / static_cast<float>(count)) : Color::transparent());
        }
    }
    return out;
}

std::filesystem::path write_contact_sheet(const std::vector<Image>& frames, const std::filesystem::path& path) {
    if (frames.empty()) return {};
    constexpr size_t kMaxCells = 36;
    std::vector<Image> cells;
    const size_t count = std::min(frames.size(), kMaxCells);
    cells.reserve(count);
    for (size_t i = 0; i < count; ++i) {
        // Evenly sampled so a long preview still shows its whole arc.
        const size_t source = frames.size() <= kMaxCells ? i : (i * frames.size()) / count;
        cells.push_back(thumbnail(frames[source], 160));
    }
    // pack_flipbook needs identical sizes; the first cell decides.
    for (Image& cell : cells) {
        if (cell.width == cells.front().width && cell.height == cells.front().height) continue;
        Image fitted(cells.front().width, cells.front().height);
        for (int y = 0; y < fitted.height && y < cell.height; ++y)
            for (int x = 0; x < fitted.width && x < cell.width; ++x) fitted.set(x, y, cell.get(x, y));
        cell = std::move(fitted);
    }
    const int columns = std::max(1, static_cast<int>(std::ceil(std::sqrt(static_cast<double>(cells.size())))));
    int rows = 0;
    const Image sheet = render::pack_flipbook(cells, columns, rows);
    ensure_dir(path.parent_path());
    render::write_png(path, sheet);
    return path;
}

}  // namespace aether::tools
