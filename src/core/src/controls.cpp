// Effect controls: JSON, application onto a document, and the default set.
// The contract is in docs/CONTROLS.md; validation of the definitions lives in
// validate() (E021..E024, W007).
#include "aether/core/controls.hpp"

#include <algorithm>
#include <cmath>
#include <optional>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"

namespace aether {
namespace {

double number_field(const nlohmann::json& j, const char* key, double fallback) {
    auto it = j.find(key);
    if (it == j.end() || it->is_null()) return fallback;
    if (!it->is_number()) throw Error("E005", std::string("control \"") + key + "\" must be a number");
    return it->get<double>();
}

std::string string_field(const nlohmann::json& j, const char* key, const std::string& fallback = {}) {
    auto it = j.find(key);
    if (it == j.end() || it->is_null()) return fallback;
    if (!it->is_string()) throw Error("E005", std::string("control \"") + key + "\" must be a string");
    return it->get<std::string>();
}

// --- value transforms -------------------------------------------------------

double clamp_to_spec(double v, const ParamSpec& spec) {
    if (spec.min && v < *spec.min) v = *spec.min;
    if (spec.max && v > *spec.max) v = *spec.max;
    return v;
}

double combine(double current, ControlOp op, double amount) {
    switch (op) {
        case ControlOp::Multiply: return current * amount;
        case ControlOp::Add: return current + amount;
        case ControlOp::Set: return amount;
        case ControlOp::HueShift: return current;
    }
    return current;
}

// linear RGB -> HSV -> rotate hue -> linear RGB. V is the largest channel, so
// HDR colours (values above 1) round-trip; alpha is never touched.
Color hue_rotate(Color c, double degrees) {
    const float mx = std::max(c.r, std::max(c.g, c.b));
    const float mn = std::min(c.r, std::min(c.g, c.b));
    const float chroma = mx - mn;
    if (!(chroma > 0.0f) || !(mx > 0.0f)) return c;  // grey or black: no hue to rotate

    float hue = 0.0f;
    if (mx == c.r) hue = std::fmod((c.g - c.b) / chroma, 6.0f);
    else if (mx == c.g) hue = (c.b - c.r) / chroma + 2.0f;
    else hue = (c.r - c.g) / chroma + 4.0f;
    hue *= 60.0f;
    hue = std::fmod(hue + static_cast<float>(degrees), 360.0f);
    if (hue < 0.0f) hue += 360.0f;

    const float x = chroma * (1.0f - std::fabs(std::fmod(hue / 60.0f, 2.0f) - 1.0f));
    const float m = mx - chroma;
    float r = 0.0f, g = 0.0f, b = 0.0f;
    if (hue < 60.0f) { r = chroma; g = x; }
    else if (hue < 120.0f) { r = x; g = chroma; }
    else if (hue < 180.0f) { g = chroma; b = x; }
    else if (hue < 240.0f) { g = x; b = chroma; }
    else if (hue < 300.0f) { r = x; b = chroma; }
    else { r = chroma; b = x; }
    return Color{r + m, g + m, b + m, c.a};
}

bool set_component(float& component, ControlOp op, double amount, const ParamSpec& spec) {
    const double combined = clamp_to_spec(combine(static_cast<double>(component), op, amount), spec);
    if (!std::isfinite(combined)) return false;
    const float next = static_cast<float>(combined);
    if (next == component) return false;
    component = next;
    return true;
}

// Folds one binding into one concrete value (a constant or a keyframe value).
// Returns true when the value actually changed.
bool transform_value(Value& value, ControlOp op, double amount, const ParamSpec& spec) {
    const ValueType type = value_type_of(value);
    if (op == ControlOp::HueShift) {
        if (type == ValueType::Color) {
            const Color before = std::get<Color>(value);
            const Color after = hue_rotate(before, amount);
            if (after == before) return false;
            value = after;
            return true;
        }
        if (type == ValueType::Gradient) {
            Gradient gradient = std::get<Gradient>(value);
            bool changed = false;
            for (GradientKey& key : gradient.keys) {
                const Color after = hue_rotate(key.color, amount);
                if (after == key.color) continue;
                key.color = after;
                changed = true;
            }
            if (changed) value = std::move(gradient);
            return changed;
        }
        return false;
    }

    switch (type) {
        case ValueType::Int: {
            const int before = std::get<int>(value);
            const double combined = clamp_to_spec(combine(static_cast<double>(before), op, amount), spec);
            if (!std::isfinite(combined)) return false;
            const int after = static_cast<int>(std::llround(combined));
            if (after == before) return false;
            value = after;
            return true;
        }
        case ValueType::Float: {
            float f = std::get<float>(value);
            if (!set_component(f, op, amount, spec)) return false;
            value = f;
            return true;
        }
        case ValueType::Vec2: {
            Vec2 v = std::get<Vec2>(value);
            bool changed = false;
            for (int i = 0; i < 2; ++i) changed |= set_component(v[i], op, amount, spec);
            if (changed) value = v;
            return changed;
        }
        case ValueType::Vec3: {
            Vec3 v = std::get<Vec3>(value);
            bool changed = false;
            for (int i = 0; i < 3; ++i) changed |= set_component(v[i], op, amount, spec);
            if (changed) value = v;
            return changed;
        }
        case ValueType::Vec4: {
            Vec4 v = std::get<Vec4>(value);
            bool changed = false;
            for (int i = 0; i < 4; ++i) changed |= set_component(v[i], op, amount, spec);
            if (changed) value = v;
            return changed;
        }
        case ValueType::Color: {
            // rgb only: a control must never make something invisible by accident.
            Color c = std::get<Color>(value);
            bool changed = false;
            for (int i = 0; i < 3; ++i) changed |= set_component(c[i], op, amount, spec);
            if (changed) value = c;
            return changed;
        }
        default:
            return false;
    }
}

// --- default generation -----------------------------------------------------

struct Knob {
    const char* suffix;
    const char* label;
};

bool has_param(NodeType type, const char* name) {
    return SpecRegistry::instance().get(type).find_param(name) != nullptr;
}

void bind(std::vector<ControlBinding>& into, const Node& node, const char* name, ControlOp op) {
    if (!has_param(node.type, name)) return;
    into.push_back(ControlBinding{node.id, name, op});
}

bool node_bool_or(const Node& node, const char* name, bool fallback) {
    try {
        return param_bool(node, name);
    } catch (const Error&) {
        return fallback;
    }
}

// `emissive` is a small extra glow on most shipped effects (0.05 - 0.2), so an
// intensity control that only scaled it would look broken. What actually makes
// an additive sprite bright is the HDR magnitude of its `color`, and scaling
// rgb uniformly keeps the hue, so both are bound.
void bind_intensity(std::vector<ControlBinding>& into, const Node& node) {
    switch (node.type) {
        case NodeType::ParticleSystem:
        case NodeType::Beam:
        case NodeType::Decal:
        case NodeType::Trail:
        case NodeType::Mesh:
            bind(into, node, "emissive", ControlOp::Multiply);
            bind(into, node, "color", ControlOp::Multiply);
            break;
        case NodeType::Material: bind(into, node, "emissive_intensity", ControlOp::Multiply); break;
        case NodeType::Light: bind(into, node, "intensity", ControlOp::Multiply); break;
        case NodeType::Volume:
            bind(into, node, "emission", ControlOp::Multiply);
            bind(into, node, "color", ControlOp::Multiply);
            break;
        case NodeType::PostEffect: bind(into, node, "intensity", ControlOp::Multiply); break;
        default: break;
    }
}

void bind_size(std::vector<ControlBinding>& into, const Node& node) {
    switch (node.type) {
        // `size` already scales mesh instances, so mesh_scale (the per-axis
        // shape ratio) is deliberately left alone: scaling both would square
        // the control.
        case NodeType::ParticleSystem: bind(into, node, "size", ControlOp::Multiply); break;
        case NodeType::Trail:
        case NodeType::Beam: bind(into, node, "width", ControlOp::Multiply); break;
        case NodeType::Decal: bind(into, node, "size", ControlOp::Multiply); break;
        case NodeType::Light: bind(into, node, "radius", ControlOp::Multiply); break;
        case NodeType::Volume: bind(into, node, "radius", ControlOp::Multiply); break;
        // A visible mesh is scaled by its transform; an invisible one is only a
        // geometry source for particles, where `radius` is what is baked.
        case NodeType::Mesh:
            bind(into, node, node_bool_or(node, "visible", true) ? "scale" : "radius", ControlOp::Multiply);
            break;
        default: break;
    }
}

void bind_density(std::vector<ControlBinding>& into, const Node& node) {
    if (node.type != NodeType::Emitter) return;
    bind(into, node, "rate", ControlOp::Multiply);
    bind(into, node, "burst_count", ControlOp::Multiply);
}

void bind_opacity(std::vector<ControlBinding>& into, const Node& node) {
    switch (node.type) {
        case NodeType::ParticleSystem:
        case NodeType::Decal:
        case NodeType::Material: bind(into, node, "opacity", ControlOp::Multiply); break;
        case NodeType::Volume: bind(into, node, "density", ControlOp::Multiply); break;
        default: break;
    }
}

// Every colour parameter the node type declares, plus its gradients (rotated
// key by key) so a hue control moves the whole palette, not just the tint.
void bind_hue(std::vector<ControlBinding>& into, const Node& node) {
    for (const ParamSpec& spec : SpecRegistry::instance().get(node.type).params) {
        if (spec.type != ValueType::Color && spec.type != ValueType::Gradient) continue;
        into.push_back(ControlBinding{node.id, spec.name, ControlOp::HueShift});
    }
}

Control make_multiplier(std::string id, std::string label, std::string group, std::vector<ControlBinding> bindings) {
    Control control;
    control.id = std::move(id);
    control.label = std::move(label);
    control.group = std::move(group);
    control.min = 0.0;
    control.max = 3.0;
    control.default_value = 1.0;
    control.value = 1.0;
    control.step = 0.01;
    control.unit = "x";
    control.bindings = std::move(bindings);
    return control;
}

}  // namespace

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

nlohmann::json control_to_json(const Control& control) {
    nlohmann::json bindings = nlohmann::json::array();
    for (const ControlBinding& b : control.bindings)
        bindings.push_back({{"node", b.node}, {"parameter", b.parameter}, {"op", std::string(to_string(b.op))}});
    nlohmann::json j{{"id", control.id},
                     {"label", control.label},
                     {"group", control.group},
                     {"min", control.min},
                     {"max", control.max},
                     {"default", control.default_value},
                     {"value", control.value},
                     {"step", control.step},
                     {"unit", control.unit},
                     {"bindings", std::move(bindings)}};
    return j;
}

Control control_from_json(const nlohmann::json& j) {
    if (!j.is_object()) throw Error("E005", "a control must be a JSON object");
    Control control;
    auto id_it = j.find("id");
    if (id_it == j.end() || !id_it->is_string()) throw Error("E005", "a control needs a string \"id\"");
    control.id = id_it->get<std::string>();
    control.label = string_field(j, "label", control.id);
    control.group = string_field(j, "group");
    control.min = number_field(j, "min", 0.0);
    control.max = number_field(j, "max", 3.0);
    control.default_value = number_field(j, "default", 1.0);
    control.value = number_field(j, "value", control.default_value);
    control.step = number_field(j, "step", 0.01);
    control.unit = string_field(j, "unit");

    auto bindings_it = j.find("bindings");
    if (bindings_it != j.end() && !bindings_it->is_null()) {
        if (!bindings_it->is_array())
            throw Error("E005", "control \"" + control.id + "\": \"bindings\" must be an array");
        for (const nlohmann::json& b : *bindings_it) {
            if (!b.is_object()) throw Error("E005", "control \"" + control.id + "\": a binding must be an object");
            ControlBinding binding;
            binding.node = string_field(b, "node");
            binding.parameter = string_field(b, "parameter");
            const std::string op = string_field(b, "op", "multiply");
            // An unknown op is kept as multiply here and reported as E023 by
            // validate(), so a document with a typo still loads.
            if (!parse_control_op(op, binding.op)) binding.op = ControlOp::Multiply;
            control.bindings.push_back(std::move(binding));
        }
    }
    return control;
}

// ---------------------------------------------------------------------------
// applying
// ---------------------------------------------------------------------------

bool control_binding_is_identity(ControlOp op, double value) {
    switch (op) {
        case ControlOp::Multiply: return value == 1.0;
        case ControlOp::Add: return value == 0.0;
        case ControlOp::HueShift: return std::fmod(value, 360.0) == 0.0;
        case ControlOp::Set: return false;
    }
    return false;
}

double control_effective_value(const Control& control) {
    double v = control.value;
    if (control.min <= control.max) v = clamp(v, control.min, control.max);
    return v;
}

int apply_controls(Effect& effect) {
    int applied = 0;
    for (const Control& control : effect.controls) {
        const double amount = control_effective_value(control);
        for (const ControlBinding& binding : control.bindings) {
            if (control_binding_is_identity(binding.op, amount)) continue;
            Node* node = effect.find_node(binding.node);
            if (node == nullptr) continue;
            const ParamSpec* spec = SpecRegistry::instance().get(node->type).find_param(binding.parameter);
            if (spec == nullptr) continue;

            const Parameter* existing = node->find_param(binding.parameter);
            Parameter updated = existing != nullptr ? *existing : Parameter{spec->default_value};
            bool changed = transform_value(updated.value, binding.op, amount, *spec);
            for (Keyframe& key : updated.track) changed |= transform_value(key.value, binding.op, amount, *spec);
            if (!changed) continue;
            node->parameters[binding.parameter] = std::move(updated);
            ++applied;
        }
    }
    return applied;
}

// ---------------------------------------------------------------------------
// defaults
// ---------------------------------------------------------------------------

std::vector<Control> generate_default_controls(const Effect& effect) {
    static constexpr Knob kKnobs[] = {
        {"intensity", "Intensity"}, {"size", "Size"}, {"density", "Density"}, {"opacity", "Opacity"}};

    const auto collect = [](const std::vector<const Node*>& nodes, const std::string& suffix) {
        std::vector<ControlBinding> bindings;
        for (const Node* node : nodes) {
            if (suffix == "intensity") bind_intensity(bindings, *node);
            else if (suffix == "size") bind_size(bindings, *node);
            else if (suffix == "density") bind_density(bindings, *node);
            else if (suffix == "opacity") bind_opacity(bindings, *node);
            else if (suffix == "hue") bind_hue(bindings, *node);
        }
        return bindings;
    };

    std::vector<const Node*> all;
    all.reserve(effect.nodes.size());
    for (const Node& node : effect.nodes) all.push_back(&node);

    std::vector<Control> controls;

    // --- Global -------------------------------------------------------------
    for (const Knob& knob : kKnobs) {
        std::vector<ControlBinding> bindings = collect(all, knob.suffix);
        if (bindings.empty()) continue;
        controls.push_back(make_multiplier(std::string("global_") + knob.suffix, knob.label, "Global",
                                           std::move(bindings)));
    }
    std::vector<ControlBinding> hue = collect(all, "hue");
    if (!hue.empty()) {
        Control control = make_multiplier("global_hue", "Hue", "Global", std::move(hue));
        control.min = -180.0;
        control.max = 180.0;
        control.default_value = 0.0;
        control.value = 0.0;
        control.step = 1.0;
        control.unit = "deg";
        controls.push_back(std::move(control));
    }

    // --- one group per layer ------------------------------------------------
    for (const Layer& layer : effect.layers) {
        const std::vector<const Node*> nodes = effect.nodes_in_layer(layer.id);
        if (nodes.empty()) continue;
        const std::string group = layer.name.empty() ? layer.id : layer.name;
        for (const Knob& knob : kKnobs) {
            std::vector<ControlBinding> bindings = collect(nodes, knob.suffix);
            if (bindings.empty()) continue;
            controls.push_back(make_multiplier(layer.id + "_" + knob.suffix, knob.label, group, std::move(bindings)));
        }
    }
    return controls;
}

}  // namespace aether
