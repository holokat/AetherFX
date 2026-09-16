// Value / Parameter implementation: typed values, their JSON encoding
// (docs/VOCABULARY.md) and keyframe tracks over effect time.
#include "aether/core/value.hpp"

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <utility>

#include "aether/core/error.hpp"

namespace aether {
namespace {

constexpr std::array<std::pair<ValueType, std::string_view>, 15> kValueTypes{{
    {ValueType::Bool, "bool"}, {ValueType::Int, "int"}, {ValueType::Float, "float"},
    {ValueType::Vec2, "vec2"}, {ValueType::Vec3, "vec3"}, {ValueType::Vec4, "vec4"},
    {ValueType::Color, "color"}, {ValueType::String, "string"}, {ValueType::Enum, "enum"},
    {ValueType::Curve, "curve"}, {ValueType::Gradient, "gradient"}, {ValueType::Ref, "ref"},
    {ValueType::FloatList, "float_list"}, {ValueType::Vec3List, "vec3_list"}, {ValueType::Json, "json"}}};

[[noreturn]] void type_error(const Value& v, std::string_view wanted) {
    throw Error("E005", std::string("expected ") + std::string(wanted) + " value, got " +
                            std::string(to_string(value_type_of(v))));
}

// Shortest decimal text that round-trips back to the same float, parsed as a
// double. Keeps documents readable (0.09 rather than 0.08999999612569809) and
// keeps float -> json -> float exact. Deterministic: no locale is ever set.
double compact_float(float f) {
    if (!std::isfinite(f)) return static_cast<double>(f);
    char buf[64];
    for (int prec = 1; prec < 9; ++prec) {
        std::snprintf(buf, sizeof(buf), "%.*g", prec, static_cast<double>(f));
        if (static_cast<float>(std::strtod(buf, nullptr)) == f) return std::strtod(buf, nullptr);
    }
    std::snprintf(buf, sizeof(buf), "%.9g", static_cast<double>(f));
    return std::strtod(buf, nullptr);
}

nlohmann::json num(float f) { return compact_float(f); }

float json_number(const nlohmann::json& j, std::string_view what) {
    if (!j.is_number()) throw Error("E005", std::string("expected a number in ") + std::string(what));
    return j.get<float>();
}

// Reads a fixed-length numeric array.
template <int N>
std::array<float, N> read_array(const nlohmann::json& j, std::string_view what) {
    if (!j.is_array() || j.size() != static_cast<size_t>(N))
        throw Error("E005", std::string("expected an array of ") + std::to_string(N) + " numbers for " +
                                std::string(what));
    std::array<float, N> out{};
    for (int i = 0; i < N; ++i) out[static_cast<size_t>(i)] = json_number(j[static_cast<size_t>(i)], what);
    return out;
}

bool parse_hex_color(const std::string& s, Color& out) {
    size_t begin = (!s.empty() && s[0] == '#') ? 1 : 0;
    size_t n = s.size() - begin;
    if (n != 6 && n != 8) return false;
    auto nibble = [](char c, int& v) {
        if (c >= '0' && c <= '9') v = c - '0';
        else if (c >= 'a' && c <= 'f') v = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') v = c - 'A' + 10;
        else return false;
        return true;
    };
    float comp[4] = {0, 0, 0, 1};
    for (size_t i = 0; i < n / 2; ++i) {
        int hi = 0, lo = 0;
        if (!nibble(s[begin + i * 2], hi) || !nibble(s[begin + i * 2 + 1], lo)) return false;
        comp[i] = static_cast<float>(hi * 16 + lo) / 255.0f;
    }
    out = Color{srgb_to_linear(comp[0]), srgb_to_linear(comp[1]), srgb_to_linear(comp[2]), comp[3]};
    return true;
}

Color read_color(const nlohmann::json& j) {
    if (j.is_string()) {
        Color c;
        if (!parse_hex_color(j.get<std::string>(), c))
            throw Error("E005", "color string must be \"#rrggbb\" or \"#rrggbbaa\"");
        return c;
    }
    if (!j.is_array() || (j.size() != 3 && j.size() != 4))
        throw Error("E005", "color must be [r,g,b], [r,g,b,a] or a \"#rrggbb\" string");
    Color c;
    c.r = json_number(j[0], "color");
    c.g = json_number(j[1], "color");
    c.b = json_number(j[2], "color");
    c.a = j.size() == 4 ? json_number(j[3], "color") : 1.0f;
    return c;
}

nlohmann::json write_color(const Color& c) {
    nlohmann::json a = nlohmann::json::array({num(c.r), num(c.g), num(c.b)});
    if (c.a != 1.0f) a.push_back(num(c.a));
    return a;
}

Interp read_interp(const nlohmann::json& j) {
    if (!j.is_string()) throw Error("E005", "interp must be \"linear\", \"step\" or \"smooth\"");
    Interp i = Interp::Linear;
    if (!parse_interp(j.get<std::string>(), i))
        throw Error("E005", "unknown interp \"" + j.get<std::string>() + "\"");
    return i;
}

Curve read_curve(const nlohmann::json& j) {
    Curve c;
    const nlohmann::json* items = nullptr;
    if (j.is_object()) {
        auto it = j.find("keys");
        if (it == j.end() || !it->is_array()) throw Error("E005", "curve object must have a \"keys\" array");
        items = &*it;
    } else if (j.is_array()) {
        items = &j;
    } else {
        throw Error("E005", "curve must be [[t,v],...] or {\"keys\":[{\"t\",\"v\",\"interp\"}]}");
    }
    for (const auto& k : *items) {
        CurveKey key;
        if (k.is_array()) {
            if (k.size() < 2 || k.size() > 3) throw Error("E005", "curve key must be [t,v] or [t,v,interp]");
            key.t = json_number(k[0], "curve key");
            key.v = json_number(k[1], "curve key");
            if (k.size() == 3) key.interp = read_interp(k[2]);
        } else if (k.is_object()) {
            if (!k.contains("t") || !k.contains("v")) throw Error("E005", "curve key object needs \"t\" and \"v\"");
            key.t = json_number(k.at("t"), "curve key");
            key.v = json_number(k.at("v"), "curve key");
            if (k.contains("interp")) key.interp = read_interp(k.at("interp"));
        } else {
            throw Error("E005", "curve key must be an array or an object");
        }
        c.keys.push_back(key);
    }
    std::stable_sort(c.keys.begin(), c.keys.end(), [](const CurveKey& a, const CurveKey& b) { return a.t < b.t; });
    return c;
}

nlohmann::json write_curve(const Curve& c) {
    bool all_linear = true;
    for (const auto& k : c.keys) all_linear = all_linear && k.interp == Interp::Linear;
    nlohmann::json arr = nlohmann::json::array();
    for (const auto& k : c.keys) {
        if (all_linear) arr.push_back(nlohmann::json::array({num(k.t), num(k.v)}));
        else arr.push_back(nlohmann::json::array({num(k.t), num(k.v), std::string(to_string(k.interp))}));
    }
    return arr;
}

Gradient read_gradient(const nlohmann::json& j) {
    Gradient g;
    const nlohmann::json* items = nullptr;
    if (j.is_object()) {
        auto it = j.find("keys");
        if (it == j.end() || !it->is_array()) throw Error("E005", "gradient object must have a \"keys\" array");
        items = &*it;
    } else if (j.is_array()) {
        items = &j;
    } else {
        throw Error("E005", "gradient must be [[t,[r,g,b,a]],...] or {\"keys\":[{\"t\",\"color\"}]}");
    }
    for (const auto& k : *items) {
        GradientKey key;
        if (k.is_array()) {
            if (k.size() != 2) throw Error("E005", "gradient key must be [t, color]");
            key.t = json_number(k[0], "gradient key");
            key.color = read_color(k[1]);
        } else if (k.is_object()) {
            if (!k.contains("t") || !k.contains("color"))
                throw Error("E005", "gradient key object needs \"t\" and \"color\"");
            key.t = json_number(k.at("t"), "gradient key");
            key.color = read_color(k.at("color"));
        } else {
            throw Error("E005", "gradient key must be an array or an object");
        }
        g.keys.push_back(key);
    }
    std::stable_sort(g.keys.begin(), g.keys.end(), [](const GradientKey& a, const GradientKey& b) { return a.t < b.t; });
    return g;
}

nlohmann::json write_gradient(const Gradient& g) {
    nlohmann::json arr = nlohmann::json::array();
    for (const auto& k : g.keys) arr.push_back(nlohmann::json::array({num(k.t), write_color(k.color)}));
    return arr;
}

float interp_factor(Interp interp, float u) {
    switch (interp) {
        case Interp::Step: return 0.0f;
        case Interp::Smooth: return u * u * (3.0f - 2.0f * u);
        case Interp::Linear: break;
    }
    return u;
}

bool is_numeric(ValueType t) { return t == ValueType::Int || t == ValueType::Float; }

}  // namespace

std::string_view to_string(ValueType t) {
    for (const auto& [k, v] : kValueTypes)
        if (k == t) return v;
    return "unknown";
}

bool parse_value_type(std::string_view s, ValueType& out) {
    for (const auto& [k, v] : kValueTypes)
        if (v == s) { out = k; return true; }
    return false;
}

ValueType value_type_of(const Value& v) {
    switch (v.index()) {
        case 0: return ValueType::Bool;
        case 1: return ValueType::Int;
        case 2: return ValueType::Float;
        case 3: return ValueType::Vec2;
        case 4: return ValueType::Vec3;
        case 5: return ValueType::Vec4;
        case 6: return ValueType::Color;
        case 7: return ValueType::String;
        case 8: return ValueType::Curve;
        case 9: return ValueType::Gradient;
        case 10: return ValueType::FloatList;
        case 11: return ValueType::Vec3List;
        default: return ValueType::Json;
    }
}

bool value_compatible(const Value& v, ValueType expected) {
    if (expected == ValueType::Json) return true;  // json accepts anything
    ValueType actual = value_type_of(v);
    if (actual == expected) return true;
    if (expected == ValueType::Float && actual == ValueType::Int) return true;
    if ((expected == ValueType::Enum || expected == ValueType::Ref) && actual == ValueType::String) return true;
    return false;
}

float value_as_float(const Value& v) {
    if (const float* f = std::get_if<float>(&v)) return *f;
    if (const int* i = std::get_if<int>(&v)) return static_cast<float>(*i);
    type_error(v, "float");
}

int value_as_int(const Value& v) {
    if (const int* i = std::get_if<int>(&v)) return *i;
    if (const float* f = std::get_if<float>(&v)) {
        if (std::floor(*f) == *f) return static_cast<int>(*f);
        throw Error("E005", "expected int value, got a non-integral float");
    }
    type_error(v, "int");
}

bool value_as_bool(const Value& v) {
    if (const bool* b = std::get_if<bool>(&v)) return *b;
    type_error(v, "bool");
}

Vec2 value_as_vec2(const Value& v) {
    if (const Vec2* p = std::get_if<Vec2>(&v)) return *p;
    type_error(v, "vec2");
}
Vec3 value_as_vec3(const Value& v) {
    if (const Vec3* p = std::get_if<Vec3>(&v)) return *p;
    type_error(v, "vec3");
}
Vec4 value_as_vec4(const Value& v) {
    if (const Vec4* p = std::get_if<Vec4>(&v)) return *p;
    type_error(v, "vec4");
}
Color value_as_color(const Value& v) {
    if (const Color* p = std::get_if<Color>(&v)) return *p;
    type_error(v, "color");
}
const std::string& value_as_string(const Value& v) {
    if (const std::string* p = std::get_if<std::string>(&v)) return *p;
    type_error(v, "string");
}
const Curve& value_as_curve(const Value& v) {
    if (const Curve* p = std::get_if<Curve>(&v)) return *p;
    type_error(v, "curve");
}
const Gradient& value_as_gradient(const Value& v) {
    if (const Gradient* p = std::get_if<Gradient>(&v)) return *p;
    type_error(v, "gradient");
}
const std::vector<float>& value_as_float_list(const Value& v) {
    if (const std::vector<float>* p = std::get_if<std::vector<float>>(&v)) return *p;
    type_error(v, "float_list");
}
const std::vector<Vec3>& value_as_vec3_list(const Value& v) {
    if (const std::vector<Vec3>* p = std::get_if<std::vector<Vec3>>(&v)) return *p;
    type_error(v, "vec3_list");
}

Value lerp_value(const Value& a, const Value& b, float t) {
    ValueType ta = value_type_of(a), tb = value_type_of(b);
    if (is_numeric(ta) && is_numeric(tb)) {
        if (ta == ValueType::Int && tb == ValueType::Int) {
            float f = lerp(static_cast<float>(std::get<int>(a)), static_cast<float>(std::get<int>(b)), t);
            return static_cast<int>(std::lround(f));
        }
        return lerp(value_as_float(a), value_as_float(b), t);
    }
    if (ta == tb) {
        switch (ta) {
            case ValueType::Vec2: return lerp(std::get<Vec2>(a), std::get<Vec2>(b), t);
            case ValueType::Vec3: return lerp(std::get<Vec3>(a), std::get<Vec3>(b), t);
            case ValueType::Vec4: return lerp(std::get<Vec4>(a), std::get<Vec4>(b), t);
            case ValueType::Color: return lerp(std::get<Color>(a), std::get<Color>(b), t);
            default: break;
        }
    }
    return t >= 1.0f ? b : a;  // step for everything else
}

bool values_equal(const Value& a, const Value& b) {
    ValueType ta = value_type_of(a), tb = value_type_of(b);
    if (ta != tb) {
        if (is_numeric(ta) && is_numeric(tb)) return value_as_float(a) == value_as_float(b);
        return false;
    }
    return a == b;
}

nlohmann::json value_to_json(const Value& v) {
    switch (v.index()) {
        case 0: return std::get<bool>(v);
        case 1: return std::get<int>(v);
        case 2: return num(std::get<float>(v));
        case 3: { Vec2 p = std::get<Vec2>(v); return nlohmann::json::array({num(p.x), num(p.y)}); }
        case 4: { Vec3 p = std::get<Vec3>(v); return nlohmann::json::array({num(p.x), num(p.y), num(p.z)}); }
        case 5: { Vec4 p = std::get<Vec4>(v); return nlohmann::json::array({num(p.x), num(p.y), num(p.z), num(p.w)}); }
        case 6: return write_color(std::get<Color>(v));
        case 7: return std::get<std::string>(v);
        case 8: return write_curve(std::get<Curve>(v));
        case 9: return write_gradient(std::get<Gradient>(v));
        case 10: {
            nlohmann::json arr = nlohmann::json::array();
            for (float f : std::get<std::vector<float>>(v)) arr.push_back(num(f));
            return arr;
        }
        case 11: {
            nlohmann::json arr = nlohmann::json::array();
            for (Vec3 p : std::get<std::vector<Vec3>>(v)) arr.push_back(nlohmann::json::array({num(p.x), num(p.y), num(p.z)}));
            return arr;
        }
        default: return std::get<nlohmann::json>(v);
    }
}

Value value_from_json(const nlohmann::json& j, ValueType expected) {
    switch (expected) {
        case ValueType::Bool:
            if (!j.is_boolean()) throw Error("E005", "expected a bool");
            return j.get<bool>();
        case ValueType::Int: {
            if (j.is_number_integer()) return j.get<int>();
            if (j.is_number_float()) {
                double d = j.get<double>();
                if (std::floor(d) == d) return static_cast<int>(d);
            }
            throw Error("E005", "expected an int");
        }
        case ValueType::Float:
            if (!j.is_number()) throw Error("E005", "expected a float");
            return j.get<float>();
        case ValueType::Vec2: { auto a = read_array<2>(j, "vec2"); return Vec2{a[0], a[1]}; }
        case ValueType::Vec3: { auto a = read_array<3>(j, "vec3"); return Vec3{a[0], a[1], a[2]}; }
        case ValueType::Vec4: { auto a = read_array<4>(j, "vec4"); return Vec4{a[0], a[1], a[2], a[3]}; }
        case ValueType::Color: return read_color(j);
        case ValueType::String:
        case ValueType::Enum:
        case ValueType::Ref:
            if (!j.is_string()) throw Error("E005", "expected a string");
            return j.get<std::string>();
        case ValueType::Curve: return read_curve(j);
        case ValueType::Gradient: return read_gradient(j);
        case ValueType::FloatList: {
            if (!j.is_array()) throw Error("E005", "expected an array of numbers (float_list)");
            std::vector<float> out;
            out.reserve(j.size());
            for (const auto& e : j) out.push_back(json_number(e, "float_list"));
            return out;
        }
        case ValueType::Vec3List: {
            if (!j.is_array()) throw Error("E005", "expected an array of [x,y,z] (vec3_list)");
            std::vector<Vec3> out;
            out.reserve(j.size());
            for (const auto& e : j) { auto a = read_array<3>(e, "vec3_list"); out.push_back(Vec3{a[0], a[1], a[2]}); }
            return out;
        }
        case ValueType::Json: break;
    }
    return j;
}

Value Parameter::eval(double time) const {
    if (track.empty()) return value;
    if (track.size() == 1 || time <= track.front().time) return track.front().value;
    if (time >= track.back().time) return track.back().value;
    size_t i = 1;
    while (i < track.size() && track[i].time <= time) ++i;
    const Keyframe& a = track[i - 1];
    const Keyframe& b = track[i];
    double span = b.time - a.time;
    float u = span > 0.0 ? static_cast<float>((time - a.time) / span) : 1.0f;
    return lerp_value(a.value, b.value, interp_factor(a.interp, u));
}

void Parameter::set_keyframe(double time, Value v, Interp interp) {
    for (auto& k : track) {
        if (std::fabs(k.time - time) <= 1e-9) {
            k.value = std::move(v);
            k.interp = interp;
            return;
        }
    }
    auto pos = std::find_if(track.begin(), track.end(), [&](const Keyframe& k) { return k.time > time; });
    track.insert(pos, Keyframe{time, std::move(v), interp});
}

bool Parameter::remove_keyframe(double time, double tolerance) {
    auto it = std::find_if(track.begin(), track.end(),
                           [&](const Keyframe& k) { return std::fabs(k.time - time) <= tolerance; });
    if (it == track.end()) return false;
    track.erase(it);
    return true;
}

bool Parameter::operator==(const Parameter& o) const {
    if (!values_equal(value, o.value)) return false;
    if (track.size() != o.track.size()) return false;
    for (size_t i = 0; i < track.size(); ++i) {
        if (track[i].time != o.track[i].time) return false;
        if (track[i].interp != o.track[i].interp) return false;
        if (!values_equal(track[i].value, o.track[i].value)) return false;
    }
    return true;
}

nlohmann::json parameter_to_json(const Parameter& p) {
    if (p.track.empty()) return value_to_json(p.value);
    nlohmann::json track = nlohmann::json::array();
    for (const auto& k : p.track) {
        nlohmann::json kf{{"time", k.time}, {"value", value_to_json(k.value)}};
        if (k.interp != Interp::Linear) kf["interp"] = std::string(to_string(k.interp));
        track.push_back(std::move(kf));
    }
    return nlohmann::json{{"value", value_to_json(p.value)}, {"track", std::move(track)}};
}

Parameter parameter_from_json(const nlohmann::json& j, ValueType expected) {
    // Curve/gradient objects carry "keys"; json parameters are always bare; the
    // wrapped form is the only object with "value"/"track".
    const bool wrapped = expected != ValueType::Json && j.is_object() && !j.contains("keys") &&
                         (j.contains("value") || j.contains("track"));
    if (!wrapped) return Parameter{value_from_json(j, expected)};

    Parameter p;
    auto tit = j.find("track");
    if (tit != j.end()) {
        if (!tit->is_array()) throw Error("E005", "parameter \"track\" must be an array of keyframes");
        for (const auto& k : *tit) {
            if (!k.is_object() || !k.contains("time") || !k.contains("value"))
                throw Error("E005", "keyframe must be {\"time\", \"value\", \"interp\"?}");
            Keyframe kf;
            if (!k.at("time").is_number()) throw Error("E005", "keyframe \"time\" must be a number");
            kf.time = k.at("time").get<double>();
            kf.value = value_from_json(k.at("value"), expected);
            if (k.contains("interp")) kf.interp = read_interp(k.at("interp"));
            p.track.push_back(std::move(kf));
        }
        // Intentionally not sorted: validate() reports E018 for an unsorted track
        // instead of silently reinterpreting the document.
    }
    auto vit = j.find("value");
    if (vit != j.end()) p.value = value_from_json(*vit, expected);
    else if (!p.track.empty()) p.value = p.track.front().value;
    else throw Error("E005", "parameter object needs \"value\" or a non-empty \"track\"");
    return p;
}

}  // namespace aether
