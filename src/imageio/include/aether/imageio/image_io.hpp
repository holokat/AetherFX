#pragma once
// Image input/output: PNG/JPG/TGA/BMP through stb, EXR through tinyexr, plus the
// tonemapper and flipbook packing.
//
// This module sits directly on top of core so that every consumer (compiler,
// render, tools) shares one decoder: the compiler bakes `source: file` textures
// with it, the renderer writes frames with it.
#include <filesystem>
#include <vector>

#include "aether/core/image.hpp"

namespace aether::imageio {

struct TonemapSettings {
    float exposure = 1.0f;
    bool filmic = true;     // ACES-fit; false = clamp
    bool srgb = true;       // encode to sRGB for 8-bit output
};

// Reads PNG/JPG/TGA/BMP (8-bit, sRGB decoded to linear) or EXR (linear). Throws Error("io").
Image read_image(const std::filesystem::path& path);
// Tonemaps + encodes to 8-bit PNG.
void write_png(const std::filesystem::path& path, const Image& image, const TonemapSettings& tonemap = {});
// Writes linear half-float RGBA EXR.
void write_exr(const std::filesystem::path& path, const Image& image);
// Convenience: chooses by extension (.png/.exr).
void write_image(const std::filesystem::path& path, const Image& image, const TonemapSettings& tonemap = {});

Image tonemap_image(const Image& hdr, const TonemapSettings& settings);  // result in [0,1], still linear unless srgb
Color aces_fit(Color c);

// Packs frames (all same size) into a grid sprite sheet; returns the sheet and fills cols/rows.
Image pack_flipbook(const std::vector<Image>& frames, int columns, int& rows_out);

}  // namespace aether::imageio
