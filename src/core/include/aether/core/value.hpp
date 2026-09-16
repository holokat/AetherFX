#pragma once
// Parameter values. A Parameter is a constant Value plus an optional keyframe
// track over effect time (seconds). Curve/Gradient are *value types* used for
// over-lifetime modulation (t in [0,1]); they are not keyframe tracks.
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/enums.hpp"
#include "aether/core/math.hpp"

namespace aether {

enum class ValueType {
    Bool, Int, Float, Vec2, Vec3, Vec4, Color, String, Enum, Curve, Gradient, Ref, FloatList, Vec3List, Json,
};
std::string_view to_string(ValueType t);
bool parse_value_type(std::string_view s, ValueType& out);

struct CurveKey {
    float t = 0.0f;
    float v = 0.0f;
    Interp interp = Interp::Linear;  // interpolation from this key to the next
    bool operator==(const CurveKey&) const = default;
};
struct Curve {
    std::vector<CurveKey> keys;  // sorted by t, t in [0,1]
    Curve() = default;
    Curve(std::initializer_list<CurveKey> k) : keys(k) {}
    static Curve constant(float v) { return Curve{{0.0f, v, Interp::Linear}, {1.0f, v, Interp::Linear}}; }
    static Curve linear(float v0, float v1) { return Curve{{0.0f, v0, Interp::Linear}, {1.0f, v1, Interp::Linear}}; }
    float eval(float t) const;  // clamps outside key range; empty curve -> 1
    bool empty() const { return keys.empty(); }
    bool operator==(const Curve&) const = default;
};
struct GradientKey {
    float t = 0.0f;
    Color color;
    bool operator==(const GradientKey&) const = default;
};
struct Gradient {
    std::vector<GradientKey> keys;  // sorted by t, t in [0,1]
    Gradient() = default;
    Gradient(std::initializer_list<GradientKey> k) : keys(k) {}
    static Gradient constant(Color c) { return Gradient{{0.0f, c}, {1.0f, c}}; }
    Color eval(float t) const;  // clamps; empty gradient -> white
    bool empty() const { return keys.empty(); }
    bool operator==(const Gradient&) const = default;
};

using Value = std::variant<bool, int, float, Vec2, Vec3, Vec4, Color, std::string, Curve, Gradient,
                           std::vector<float>, std::vector<Vec3>, nlohmann::json>;

// Runtime type of a Value. String/Enum/Ref all report ValueType::String;
// the spec decides which one is meant.
ValueType value_type_of(const Value& v);
// True when `v` can be used where `expected` is required (String covers Enum/Ref; Int is
// accepted for Float; Vec3 for Color etc. are NOT accepted).
bool value_compatible(const Value& v, ValueType expected);
// Numeric coercions used everywhere. Throw Error("E005") on a real mismatch.
float value_as_float(const Value& v);
int value_as_int(const Value& v);
bool value_as_bool(const Value& v);
Vec2 value_as_vec2(const Value& v);
Vec3 value_as_vec3(const Value& v);
Vec4 value_as_vec4(const Value& v);
Color value_as_color(const Value& v);
const std::string& value_as_string(const Value& v);
const Curve& value_as_curve(const Value& v);
const Gradient& value_as_gradient(const Value& v);
const std::vector<float>& value_as_float_list(const Value& v);
const std::vector<Vec3>& value_as_vec3_list(const Value& v);
// Componentwise lerp for numeric types; step for everything else.
Value lerp_value(const Value& a, const Value& b, float t);
bool values_equal(const Value& a, const Value& b);

// JSON encoding (see docs/VOCABULARY.md): scalars as-is, vectors/colors as
// arrays, curve as {"keys":[{"t","v","interp"}]} or [[t,v],...], gradient
// as {"keys":[{"t","color"}]} or [[t,[r,g,b,a]],...]. Color also accepts
// "#rrggbb" / "#rrggbbaa" on input (sRGB -> linear).
nlohmann::json value_to_json(const Value& v);
Value value_from_json(const nlohmann::json& j, ValueType expected);  // throws Error("E005")

struct Keyframe {
    double time = 0.0;  // effect seconds
    Value value;
    Interp interp = Interp::Linear;
};

struct Parameter {
    Value value;                   // constant, or the value before the first keyframe
    std::vector<Keyframe> track;   // sorted by time; empty = constant
    Parameter() = default;
    Parameter(Value v) : value(std::move(v)) {}  // NOLINT(google-explicit-constructor)
    bool animated() const { return !track.empty(); }
    Value eval(double time) const;  // constant when no track; clamps outside track range
    void set_keyframe(double time, Value v, Interp interp = Interp::Linear);  // insert or replace
    bool remove_keyframe(double time, double tolerance = 1e-6);
    bool operator==(const Parameter& o) const;
};
// Bare value, or {"value": v, "track": [{"time", "value", "interp"}]}.
nlohmann::json parameter_to_json(const Parameter& p);
Parameter parameter_from_json(const nlohmann::json& j, ValueType expected);

}  // namespace aether
