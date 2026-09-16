#pragma once
#include <filesystem>
#include <string>
#include <vector>

#include "aether/core/image.hpp"

namespace aether::render {

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

// Encodes an image sequence to MP4/GIF via an external ffmpeg if available. Returns false when not.
bool encode_video_ffmpeg(const std::vector<std::filesystem::path>& frame_paths, const std::filesystem::path& out,
                         int fps, std::string* error = nullptr);

}  // namespace aether::render
