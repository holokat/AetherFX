#pragma once
// The vocabulary registry: single source of truth for node types, their
// parameters (type/default/range/enum/animatable) and ports. Implemented in
// src/core/src/spec.cpp following docs/VOCABULARY.md exactly.
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/effect.hpp"
#include "aether/core/value.hpp"

namespace aether {

struct ParamSpec {
    std::string name;
    ValueType type = ValueType::Float;
    Value default_value;
    std::optional<double> min;                // numeric types, per component for vec/color
    std::optional<double> max;
    std::vector<std::string> enum_values;     // ValueType::Enum
    bool animatable = false;
    std::string description;
    std::string units;                        // "m", "s", "deg", "1/s", ""
};

struct PortSpec {
    std::string name;
    std::vector<NodeType> accepts;  // empty = any
    bool multi = false;             // accepts a list of refs
    bool required = false;
    std::string description;
};

struct NodeSpec {
    NodeType type = NodeType::Emitter;
    std::string description;
    int version = 1;
    int default_tier = 0;                  // 0 analytic, 1 particles, 2 physics, 3 volumetric
    bool spatial = false;                  // has position/rotation/scale, may have a parent
    bool time_bound = false;               // has start_time/duration/phase
    std::vector<ParamSpec> params;
    std::vector<PortSpec> inputs;
    std::vector<std::string> outputs;      // output port names ("" default is implied)

    const ParamSpec* find_param(std::string_view name) const;
    const PortSpec* find_input(std::string_view name) const;
};

class SpecRegistry {
public:
    static const SpecRegistry& instance();
    const NodeSpec& get(NodeType t) const;
    const NodeSpec* find(std::string_view type_name) const;
    const std::vector<NodeSpec>& all() const { return specs_; }

    // Machine-readable vocabulary (schema/vocabulary.json is generated from this).
    nlohmann::json to_json() const;
    // JSON Schema (draft 2020-12) for an effect document (schema/effect.schema.json).
    nlohmann::json effect_json_schema() const;

private:
    SpecRegistry();
    std::vector<NodeSpec> specs_;  // indexed by static_cast<int>(NodeType)
};

// Parameter access with spec defaults. Throws Error("E004") for unknown
// parameters. `time` is effect time for keyframe tracks.
Value param_value(const Node& node, std::string_view name, double time = 0.0);
float param_float(const Node& node, std::string_view name, double time = 0.0);
int param_int(const Node& node, std::string_view name, double time = 0.0);
bool param_bool(const Node& node, std::string_view name, double time = 0.0);
Vec2 param_vec2(const Node& node, std::string_view name, double time = 0.0);
Vec3 param_vec3(const Node& node, std::string_view name, double time = 0.0);
Vec4 param_vec4(const Node& node, std::string_view name, double time = 0.0);
Color param_color(const Node& node, std::string_view name, double time = 0.0);
std::string param_string(const Node& node, std::string_view name, double time = 0.0);
Curve param_curve(const Node& node, std::string_view name);
Gradient param_gradient(const Node& node, std::string_view name);
std::vector<float> param_float_list(const Node& node, std::string_view name);
std::vector<Vec3> param_vec3_list(const Node& node, std::string_view name);
nlohmann::json param_json(const Node& node, std::string_view name);

// Effective (possibly defaulted) window of a time-bound node *before* phase
// resolution: {start_time, end_time}; end < 0 means "until effect end".
struct TimeWindow {
    double start = 0.0;
    double end = -1.0;
    bool contains(double t, double effect_duration) const {
        double e = end < 0.0 ? effect_duration : end;
        return t >= start && t < e;
    }
};
TimeWindow node_window(const Node& node);

}  // namespace aether
