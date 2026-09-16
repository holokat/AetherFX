// Curve / Gradient evaluation (over-lifetime modulation, t in [0,1]).
#include "aether/core/value.hpp"

namespace aether {
namespace {
float apply_interp(Interp interp, float t) {
    switch (interp) {
        case Interp::Step: return 0.0f;
        case Interp::Smooth: return t * t * (3.0f - 2.0f * t);
        case Interp::Linear: default: return t;
    }
}
}  // namespace

float Curve::eval(float t) const {
    if (keys.empty()) return 1.0f;
    if (keys.size() == 1 || t <= keys.front().t) return keys.front().v;
    if (t >= keys.back().t) return keys.back().v;
    size_t i = 1;
    while (i < keys.size() && keys[i].t < t) ++i;
    const CurveKey& a = keys[i - 1];
    const CurveKey& b = keys[i];
    float span = b.t - a.t;
    float u = span > 0.0f ? (t - a.t) / span : 1.0f;
    return lerp(a.v, b.v, apply_interp(a.interp, u));
}

Color Gradient::eval(float t) const {
    if (keys.empty()) return Color::white();
    if (keys.size() == 1 || t <= keys.front().t) return keys.front().color;
    if (t >= keys.back().t) return keys.back().color;
    size_t i = 1;
    while (i < keys.size() && keys[i].t < t) ++i;
    const GradientKey& a = keys[i - 1];
    const GradientKey& b = keys[i];
    float span = b.t - a.t;
    float u = span > 0.0f ? (t - a.t) / span : 1.0f;
    return lerp(a.color, b.color, u);
}

}  // namespace aether
