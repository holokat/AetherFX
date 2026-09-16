#include "aether/core/image.hpp"

#include <cstring>

namespace aether {

void Image::resize(int w, int h, Color fill_color) {
    width = w < 0 ? 0 : w;
    height = h < 0 ? 0 : h;
    rgba.assign(pixel_count() * 4, 0.0f);
    fill(fill_color);
}

void Image::fill(Color c) {
    for (size_t i = 0; i < pixel_count(); ++i) {
        rgba[i * 4 + 0] = c.r; rgba[i * 4 + 1] = c.g; rgba[i * 4 + 2] = c.b; rgba[i * 4 + 3] = c.a;
    }
}

Color Image::fetch(int x, int y, WrapMode wrap) const {
    if (empty()) return Color::transparent();
    if (wrap == WrapMode::Repeat) {
        x = ((x % width) + width) % width;
        y = ((y % height) + height) % height;
    } else {
        x = clamp(x, 0, width - 1);
        y = clamp(y, 0, height - 1);
    }
    return get(x, y);
}

Color Image::sample(float u, float v, WrapMode wrap) const {
    if (empty()) return Color::transparent();
    float fx = u * static_cast<float>(width) - 0.5f;
    float fy = v * static_cast<float>(height) - 0.5f;
    int x0 = static_cast<int>(std::floor(fx));
    int y0 = static_cast<int>(std::floor(fy));
    float tx = fx - static_cast<float>(x0);
    float ty = fy - static_cast<float>(y0);
    Color c00 = fetch(x0, y0, wrap), c10 = fetch(x0 + 1, y0, wrap);
    Color c01 = fetch(x0, y0 + 1, wrap), c11 = fetch(x0 + 1, y0 + 1, wrap);
    return lerp(lerp(c00, c10, tx), lerp(c01, c11, tx), ty);
}

uint64_t Image::hash() const {
    uint64_t h = 1469598103934665603ULL;
    h = hash_combine(h, static_cast<uint64_t>(width));
    h = hash_combine(h, static_cast<uint64_t>(height));
    for (float f : rgba) {
        uint32_t bits;
        std::memcpy(&bits, &f, sizeof bits);
        h ^= bits;
        h *= 1099511628211ULL;
    }
    return h;
}

}  // namespace aether
