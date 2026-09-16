#pragma once
// Small self-contained math library. Header-only, deterministic (no fast-math).
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>
#include <string_view>

namespace aether {

inline constexpr float kPi = 3.14159265358979323846f;
inline constexpr float kTwoPi = 2.0f * kPi;
inline constexpr float kHalfPi = 0.5f * kPi;
inline constexpr float kEpsilon = 1e-6f;

inline float deg_to_rad(float d) { return d * (kPi / 180.0f); }
inline float rad_to_deg(float r) { return r * (180.0f / kPi); }
template <class T> inline T clamp(T v, T lo, T hi) { return v < lo ? lo : (v > hi ? hi : v); }
inline float saturate(float v) { return clamp(v, 0.0f, 1.0f); }
inline float lerp(float a, float b, float t) { return a + (b - a) * t; }
inline double lerp(double a, double b, double t) { return a + (b - a) * t; }
inline float inverse_lerp(float a, float b, float v) { return (b - a) != 0.0f ? (v - a) / (b - a) : 0.0f; }
inline float smoothstep(float e0, float e1, float x) {
    float t = saturate((x - e0) / (e1 - e0));
    return t * t * (3.0f - 2.0f * t);
}
inline float smootherstep(float t) { t = saturate(t); return t * t * t * (t * (t * 6.0f - 15.0f) + 10.0f); }
inline float fract(float v) { return v - std::floor(v); }
inline float sign(float v) { return v > 0.0f ? 1.0f : (v < 0.0f ? -1.0f : 0.0f); }

struct Vec2 {
    float x = 0.0f, y = 0.0f;
    constexpr Vec2() = default;
    constexpr Vec2(float x_, float y_) : x(x_), y(y_) {}
    constexpr explicit Vec2(float s) : x(s), y(s) {}
    Vec2 operator+(Vec2 o) const { return {x + o.x, y + o.y}; }
    Vec2 operator-(Vec2 o) const { return {x - o.x, y - o.y}; }
    Vec2 operator*(Vec2 o) const { return {x * o.x, y * o.y}; }
    Vec2 operator*(float s) const { return {x * s, y * s}; }
    Vec2 operator/(float s) const { return {x / s, y / s}; }
    Vec2 operator-() const { return {-x, -y}; }
    Vec2& operator+=(Vec2 o) { x += o.x; y += o.y; return *this; }
    Vec2& operator-=(Vec2 o) { x -= o.x; y -= o.y; return *this; }
    Vec2& operator*=(float s) { x *= s; y *= s; return *this; }
    bool operator==(const Vec2&) const = default;
    float& operator[](int i) { return i == 0 ? x : y; }
    float operator[](int i) const { return i == 0 ? x : y; }
};
inline Vec2 operator*(float s, Vec2 v) { return v * s; }
inline float dot(Vec2 a, Vec2 b) { return a.x * b.x + a.y * b.y; }
inline float length(Vec2 v) { return std::sqrt(dot(v, v)); }
inline Vec2 normalize(Vec2 v) { float l = length(v); return l > kEpsilon ? v / l : Vec2{}; }
inline Vec2 lerp(Vec2 a, Vec2 b, float t) { return a + (b - a) * t; }

struct Vec3 {
    float x = 0.0f, y = 0.0f, z = 0.0f;
    constexpr Vec3() = default;
    constexpr Vec3(float x_, float y_, float z_) : x(x_), y(y_), z(z_) {}
    constexpr explicit Vec3(float s) : x(s), y(s), z(s) {}
    Vec3 operator+(Vec3 o) const { return {x + o.x, y + o.y, z + o.z}; }
    Vec3 operator-(Vec3 o) const { return {x - o.x, y - o.y, z - o.z}; }
    Vec3 operator*(Vec3 o) const { return {x * o.x, y * o.y, z * o.z}; }
    Vec3 operator/(Vec3 o) const { return {x / o.x, y / o.y, z / o.z}; }
    Vec3 operator*(float s) const { return {x * s, y * s, z * s}; }
    Vec3 operator/(float s) const { return {x / s, y / s, z / s}; }
    Vec3 operator-() const { return {-x, -y, -z}; }
    Vec3& operator+=(Vec3 o) { x += o.x; y += o.y; z += o.z; return *this; }
    Vec3& operator-=(Vec3 o) { x -= o.x; y -= o.y; z -= o.z; return *this; }
    Vec3& operator*=(float s) { x *= s; y *= s; z *= s; return *this; }
    Vec3& operator*=(Vec3 o) { x *= o.x; y *= o.y; z *= o.z; return *this; }
    bool operator==(const Vec3&) const = default;
    float& operator[](int i) { return i == 0 ? x : (i == 1 ? y : z); }
    float operator[](int i) const { return i == 0 ? x : (i == 1 ? y : z); }
    static constexpr Vec3 zero() { return {0, 0, 0}; }
    static constexpr Vec3 one() { return {1, 1, 1}; }
    static constexpr Vec3 up() { return {0, 1, 0}; }
    static constexpr Vec3 forward() { return {0, 0, -1}; }
    static constexpr Vec3 right() { return {1, 0, 0}; }
};
inline Vec3 operator*(float s, Vec3 v) { return v * s; }
inline float dot(Vec3 a, Vec3 b) { return a.x * b.x + a.y * b.y + a.z * b.z; }
inline Vec3 cross(Vec3 a, Vec3 b) { return {a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x}; }
inline float length_squared(Vec3 v) { return dot(v, v); }
inline float length(Vec3 v) { return std::sqrt(dot(v, v)); }
inline float distance(Vec3 a, Vec3 b) { return length(a - b); }
inline Vec3 normalize(Vec3 v) { float l = length(v); return l > kEpsilon ? v / l : Vec3{}; }
inline Vec3 lerp(Vec3 a, Vec3 b, float t) { return a + (b - a) * t; }
inline Vec3 vmin(Vec3 a, Vec3 b) { return {std::min(a.x, b.x), std::min(a.y, b.y), std::min(a.z, b.z)}; }
inline Vec3 vmax(Vec3 a, Vec3 b) { return {std::max(a.x, b.x), std::max(a.y, b.y), std::max(a.z, b.z)}; }
inline Vec3 vabs(Vec3 a) { return {std::fabs(a.x), std::fabs(a.y), std::fabs(a.z)}; }
inline Vec3 reflect(Vec3 v, Vec3 n) { return v - n * (2.0f * dot(v, n)); }
// Any unit vector perpendicular to n (deterministic choice).
inline Vec3 orthogonal(Vec3 n) {
    Vec3 a = std::fabs(n.x) > 0.9f ? Vec3{0, 1, 0} : Vec3{1, 0, 0};
    return normalize(cross(n, a));
}

struct Vec4 {
    float x = 0.0f, y = 0.0f, z = 0.0f, w = 0.0f;
    constexpr Vec4() = default;
    constexpr Vec4(float x_, float y_, float z_, float w_) : x(x_), y(y_), z(z_), w(w_) {}
    constexpr Vec4(Vec3 v, float w_) : x(v.x), y(v.y), z(v.z), w(w_) {}
    Vec3 xyz() const { return {x, y, z}; }
    Vec4 operator+(Vec4 o) const { return {x + o.x, y + o.y, z + o.z, w + o.w}; }
    Vec4 operator-(Vec4 o) const { return {x - o.x, y - o.y, z - o.z, w - o.w}; }
    Vec4 operator*(float s) const { return {x * s, y * s, z * s, w * s}; }
    bool operator==(const Vec4&) const = default;
    float& operator[](int i) { return i == 0 ? x : (i == 1 ? y : (i == 2 ? z : w)); }
    float operator[](int i) const { return i == 0 ? x : (i == 1 ? y : (i == 2 ? z : w)); }
};
inline Vec4 lerp(Vec4 a, Vec4 b, float t) { return a + (b - a) * t; }
inline float dot(Vec4 a, Vec4 b) { return a.x * b.x + a.y * b.y + a.z * b.z + a.w * b.w; }

// Linear RGBA. Components are >= 0 and unbounded above (HDR).
struct Color {
    float r = 1.0f, g = 1.0f, b = 1.0f, a = 1.0f;
    constexpr Color() = default;
    constexpr Color(float r_, float g_, float b_, float a_ = 1.0f) : r(r_), g(g_), b(b_), a(a_) {}
    Color operator+(Color o) const { return {r + o.r, g + o.g, b + o.b, a + o.a}; }
    Color operator-(Color o) const { return {r - o.r, g - o.g, b - o.b, a - o.a}; }
    Color operator*(Color o) const { return {r * o.r, g * o.g, b * o.b, a * o.a}; }
    Color operator*(float s) const { return {r * s, g * s, b * s, a * s}; }
    Color& operator+=(Color o) { r += o.r; g += o.g; b += o.b; a += o.a; return *this; }
    Color& operator*=(float s) { r *= s; g *= s; b *= s; a *= s; return *this; }
    bool operator==(const Color&) const = default;
    float& operator[](int i) { return i == 0 ? r : (i == 1 ? g : (i == 2 ? b : a)); }
    float operator[](int i) const { return i == 0 ? r : (i == 1 ? g : (i == 2 ? b : a)); }
    Vec3 rgb() const { return {r, g, b}; }
    float luminance() const { return 0.2126f * r + 0.7152f * g + 0.0722f * b; }
    Color rgb_scaled(float s) const { return {r * s, g * s, b * s, a}; }
    static constexpr Color white() { return {1, 1, 1, 1}; }
    static constexpr Color black() { return {0, 0, 0, 1}; }
    static constexpr Color transparent() { return {0, 0, 0, 0}; }
    // Approximate blackbody color for a temperature in Kelvin (1000..40000), normalized so the
    // brightest channel is 1.
    static Color from_temperature(float kelvin);
};
inline Color lerp(Color a, Color b, float t) { return a + (b - a) * t; }
inline float srgb_to_linear(float c) { return c <= 0.04045f ? c / 12.92f : std::pow((c + 0.055f) / 1.055f, 2.4f); }
inline float linear_to_srgb(float c) { c = saturate(c); return c <= 0.0031308f ? c * 12.92f : 1.055f * std::pow(c, 1.0f / 2.4f) - 0.055f; }

inline Color Color::from_temperature(float kelvin) {
    // Tanner Helland approximation, in sRGB then linearized.
    float t = clamp(kelvin, 1000.0f, 40000.0f) / 100.0f;
    float r, g, b;
    if (t <= 66.0f) { r = 255.0f; }
    else { r = 329.698727446f * std::pow(t - 60.0f, -0.1332047592f); }
    if (t <= 66.0f) { g = 99.4708025861f * std::log(t) - 161.1195681661f; }
    else { g = 288.1221695283f * std::pow(t - 60.0f, -0.0755148492f); }
    if (t >= 66.0f) { b = 255.0f; }
    else if (t <= 19.0f) { b = 0.0f; }
    else { b = 138.5177312231f * std::log(t - 10.0f) - 305.0447927307f; }
    Color c{srgb_to_linear(clamp(r, 0.0f, 255.0f) / 255.0f), srgb_to_linear(clamp(g, 0.0f, 255.0f) / 255.0f),
            srgb_to_linear(clamp(b, 0.0f, 255.0f) / 255.0f), 1.0f};
    float m = std::max(c.r, std::max(c.g, c.b));
    return m > 0.0f ? Color{c.r / m, c.g / m, c.b / m, 1.0f} : Color::white();
}

// Column-major 4x4 matrix: m[col*4 + row]. Vectors are columns (M * v).
struct Mat4 {
    std::array<float, 16> m{1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1};
    float& at(int row, int col) { return m[col * 4 + row]; }
    float at(int row, int col) const { return m[col * 4 + row]; }
    static Mat4 identity() { return Mat4{}; }
    static Mat4 translation(Vec3 t) { Mat4 r; r.at(0, 3) = t.x; r.at(1, 3) = t.y; r.at(2, 3) = t.z; return r; }
    static Mat4 scaling(Vec3 s) { Mat4 r; r.at(0, 0) = s.x; r.at(1, 1) = s.y; r.at(2, 2) = s.z; return r; }
    static Mat4 rotation_x(float rad) { Mat4 r; float c = std::cos(rad), s = std::sin(rad); r.at(1, 1) = c; r.at(1, 2) = -s; r.at(2, 1) = s; r.at(2, 2) = c; return r; }
    static Mat4 rotation_y(float rad) { Mat4 r; float c = std::cos(rad), s = std::sin(rad); r.at(0, 0) = c; r.at(0, 2) = s; r.at(2, 0) = -s; r.at(2, 2) = c; return r; }
    static Mat4 rotation_z(float rad) { Mat4 r; float c = std::cos(rad), s = std::sin(rad); r.at(0, 0) = c; r.at(0, 1) = -s; r.at(1, 0) = s; r.at(1, 1) = c; return r; }
    // Euler angles in degrees, applied X then Y then Z (R = Rz * Ry * Rx).
    static Mat4 euler_xyz_deg(Vec3 deg) {
        return rotation_z(deg_to_rad(deg.z)) * rotation_y(deg_to_rad(deg.y)) * rotation_x(deg_to_rad(deg.x));
    }
    static Mat4 trs(Vec3 t, Vec3 rot_deg, Vec3 s) { return translation(t) * euler_xyz_deg(rot_deg) * scaling(s); }
    // Right-handed, camera looks down -Z in view space.
    static Mat4 look_at(Vec3 eye, Vec3 target, Vec3 up) {
        Vec3 f = normalize(target - eye);
        if (length(f) < kEpsilon) f = Vec3::forward();
        Vec3 s = normalize(cross(f, up));
        if (length(s) < kEpsilon) s = orthogonal(f);
        Vec3 u = cross(s, f);
        Mat4 r;
        r.at(0, 0) = s.x; r.at(0, 1) = s.y; r.at(0, 2) = s.z; r.at(0, 3) = -dot(s, eye);
        r.at(1, 0) = u.x; r.at(1, 1) = u.y; r.at(1, 2) = u.z; r.at(1, 3) = -dot(u, eye);
        r.at(2, 0) = -f.x; r.at(2, 1) = -f.y; r.at(2, 2) = -f.z; r.at(2, 3) = dot(f, eye);
        return r;
    }
    // OpenGL-style clip space (z in [-1,1]).
    static Mat4 perspective(float fov_y_rad, float aspect, float near, float far) {
        float t = 1.0f / std::tan(fov_y_rad * 0.5f);
        Mat4 r; r.m.fill(0.0f);
        r.at(0, 0) = t / aspect; r.at(1, 1) = t;
        r.at(2, 2) = (far + near) / (near - far); r.at(2, 3) = (2.0f * far * near) / (near - far);
        r.at(3, 2) = -1.0f;
        return r;
    }
    static Mat4 orthographic(float l, float r_, float b, float t, float n, float f) {
        Mat4 r;
        r.at(0, 0) = 2.0f / (r_ - l); r.at(1, 1) = 2.0f / (t - b); r.at(2, 2) = -2.0f / (f - n);
        r.at(0, 3) = -(r_ + l) / (r_ - l); r.at(1, 3) = -(t + b) / (t - b); r.at(2, 3) = -(f + n) / (f - n);
        return r;
    }
    Mat4 operator*(const Mat4& o) const {
        Mat4 r; r.m.fill(0.0f);
        for (int c = 0; c < 4; ++c)
            for (int rr = 0; rr < 4; ++rr) {
                float s = 0.0f;
                for (int k = 0; k < 4; ++k) s += at(rr, k) * o.at(k, c);
                r.at(rr, c) = s;
            }
        return r;
    }
    Vec4 operator*(Vec4 v) const {
        return {at(0, 0) * v.x + at(0, 1) * v.y + at(0, 2) * v.z + at(0, 3) * v.w,
                at(1, 0) * v.x + at(1, 1) * v.y + at(1, 2) * v.z + at(1, 3) * v.w,
                at(2, 0) * v.x + at(2, 1) * v.y + at(2, 2) * v.z + at(2, 3) * v.w,
                at(3, 0) * v.x + at(3, 1) * v.y + at(3, 2) * v.z + at(3, 3) * v.w};
    }
    Vec3 transform_point(Vec3 p) const { Vec4 r = (*this) * Vec4{p, 1.0f}; return r.w != 0.0f ? Vec3{r.x / r.w, r.y / r.w, r.z / r.w} : r.xyz(); }
    Vec3 transform_vector(Vec3 v) const { return ((*this) * Vec4{v, 0.0f}).xyz(); }
    Vec3 translation_part() const { return {at(0, 3), at(1, 3), at(2, 3)}; }
    Mat4 transposed() const { Mat4 r; for (int i = 0; i < 4; ++i) for (int j = 0; j < 4; ++j) r.at(i, j) = at(j, i); return r; }
    // General inverse (cofactor expansion). Returns identity for singular matrices.
    Mat4 inverse() const {
        const auto& a = m; std::array<float, 16> inv{};
        inv[0] = a[5]*a[10]*a[15] - a[5]*a[11]*a[14] - a[9]*a[6]*a[15] + a[9]*a[7]*a[14] + a[13]*a[6]*a[11] - a[13]*a[7]*a[10];
        inv[4] = -a[4]*a[10]*a[15] + a[4]*a[11]*a[14] + a[8]*a[6]*a[15] - a[8]*a[7]*a[14] - a[12]*a[6]*a[11] + a[12]*a[7]*a[10];
        inv[8] = a[4]*a[9]*a[15] - a[4]*a[11]*a[13] - a[8]*a[5]*a[15] + a[8]*a[7]*a[13] + a[12]*a[5]*a[11] - a[12]*a[7]*a[9];
        inv[12] = -a[4]*a[9]*a[14] + a[4]*a[10]*a[13] + a[8]*a[5]*a[14] - a[8]*a[6]*a[13] - a[12]*a[5]*a[10] + a[12]*a[6]*a[9];
        inv[1] = -a[1]*a[10]*a[15] + a[1]*a[11]*a[14] + a[9]*a[2]*a[15] - a[9]*a[3]*a[14] - a[13]*a[2]*a[11] + a[13]*a[3]*a[10];
        inv[5] = a[0]*a[10]*a[15] - a[0]*a[11]*a[14] - a[8]*a[2]*a[15] + a[8]*a[3]*a[14] + a[12]*a[2]*a[11] - a[12]*a[3]*a[10];
        inv[9] = -a[0]*a[9]*a[15] + a[0]*a[11]*a[13] + a[8]*a[1]*a[15] - a[8]*a[3]*a[13] - a[12]*a[1]*a[11] + a[12]*a[3]*a[9];
        inv[13] = a[0]*a[9]*a[14] - a[0]*a[10]*a[13] - a[8]*a[1]*a[14] + a[8]*a[2]*a[13] + a[12]*a[1]*a[10] - a[12]*a[2]*a[9];
        inv[2] = a[1]*a[6]*a[15] - a[1]*a[7]*a[14] - a[5]*a[2]*a[15] + a[5]*a[3]*a[14] + a[13]*a[2]*a[7] - a[13]*a[3]*a[6];
        inv[6] = -a[0]*a[6]*a[15] + a[0]*a[7]*a[14] + a[4]*a[2]*a[15] - a[4]*a[3]*a[14] - a[12]*a[2]*a[7] + a[12]*a[3]*a[6];
        inv[10] = a[0]*a[5]*a[15] - a[0]*a[7]*a[13] - a[4]*a[1]*a[15] + a[4]*a[3]*a[13] + a[12]*a[1]*a[7] - a[12]*a[3]*a[5];
        inv[14] = -a[0]*a[5]*a[14] + a[0]*a[6]*a[13] + a[4]*a[1]*a[14] - a[4]*a[2]*a[13] - a[12]*a[1]*a[6] + a[12]*a[2]*a[5];
        inv[3] = -a[1]*a[6]*a[11] + a[1]*a[7]*a[10] + a[5]*a[2]*a[11] - a[5]*a[3]*a[10] - a[9]*a[2]*a[7] + a[9]*a[3]*a[6];
        inv[7] = a[0]*a[6]*a[11] - a[0]*a[7]*a[10] - a[4]*a[2]*a[11] + a[4]*a[3]*a[10] + a[8]*a[2]*a[7] - a[8]*a[3]*a[6];
        inv[11] = -a[0]*a[5]*a[11] + a[0]*a[7]*a[9] + a[4]*a[1]*a[11] - a[4]*a[3]*a[9] - a[8]*a[1]*a[7] + a[8]*a[3]*a[5];
        inv[15] = a[0]*a[5]*a[10] - a[0]*a[6]*a[9] - a[4]*a[1]*a[10] + a[4]*a[2]*a[9] + a[8]*a[1]*a[6] - a[8]*a[2]*a[5];
        float det = a[0] * inv[0] + a[1] * inv[4] + a[2] * inv[8] + a[3] * inv[12];
        if (std::fabs(det) < 1e-12f) return Mat4{};
        Mat4 r; float id = 1.0f / det;
        for (int i = 0; i < 16; ++i) r.m[i] = inv[i] * id;
        return r;
    }
    bool operator==(const Mat4&) const = default;
};

struct Bounds {
    Vec3 min{1e30f, 1e30f, 1e30f}, max{-1e30f, -1e30f, -1e30f};
    bool valid() const { return min.x <= max.x; }
    void expand(Vec3 p) { min = vmin(min, p); max = vmax(max, p); }
    Vec3 center() const { return (min + max) * 0.5f; }
    Vec3 extent() const { return max - min; }
};

// FNV-1a 64-bit, used for deterministic seed derivation from identifiers.
inline uint64_t fnv1a_64(std::string_view s) {
    uint64_t h = 1469598103934665603ULL;
    for (unsigned char c : s) { h ^= c; h *= 1099511628211ULL; }
    return h;
}
inline uint64_t hash_combine(uint64_t a, uint64_t b) {
    a ^= b + 0x9e3779b97f4a7c15ULL + (a << 6) + (a >> 2);
    return a;
}

}  // namespace aether
