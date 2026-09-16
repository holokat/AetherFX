#pragma once
// Linear float RGBA image. Origin is top-left, row-major.
#include <cstddef>
#include <vector>

#include "aether/core/math.hpp"

namespace aether {

enum class WrapMode { Clamp, Repeat };

struct Image {
    int width = 0;
    int height = 0;
    std::vector<float> rgba;  // width*height*4

    Image() = default;
    Image(int w, int h, Color fill = Color::transparent()) { resize(w, h, fill); }
    void resize(int w, int h, Color fill = Color::transparent());
    bool empty() const { return width == 0 || height == 0; }
    size_t pixel_count() const { return static_cast<size_t>(width) * static_cast<size_t>(height); }

    float* at(int x, int y) { return &rgba[(static_cast<size_t>(y) * width + x) * 4]; }
    const float* at(int x, int y) const { return &rgba[(static_cast<size_t>(y) * width + x) * 4]; }
    Color get(int x, int y) const { const float* p = at(x, y); return {p[0], p[1], p[2], p[3]}; }
    void set(int x, int y, Color c) { float* p = at(x, y); p[0] = c.r; p[1] = c.g; p[2] = c.b; p[3] = c.a; }
    // Clamped/wrapped integer fetch.
    Color fetch(int x, int y, WrapMode wrap = WrapMode::Clamp) const;
    // Bilinear sample, uv in [0,1] with (0,0) = top-left.
    Color sample(float u, float v, WrapMode wrap = WrapMode::Clamp) const;
    void fill(Color c);
    // Content hash (FNV over float bits) for golden tests.
    uint64_t hash() const;
};

}  // namespace aether
