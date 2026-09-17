#pragma once
// Compatibility header. The image codecs, tonemapper and flipbook packing moved
// to the standalone `aether::imageio` module (src/imageio) so that the compiler
// can load `source: file` textures without depending on the renderer. The names
// are re-exported here unchanged; `encode_video_ffmpeg` stays in render.
#include <filesystem>
#include <string>
#include <vector>

#include "aether/core/image.hpp"
#include "aether/imageio/image_io.hpp"

namespace aether::render {

using imageio::TonemapSettings;

using imageio::aces_fit;
using imageio::pack_flipbook;
using imageio::read_image;
using imageio::tonemap_image;
using imageio::write_exr;
using imageio::write_image;
using imageio::write_png;

// Encodes an image sequence to MP4/GIF via an external ffmpeg if available. Returns false when not.
bool encode_video_ffmpeg(const std::vector<std::filesystem::path>& frame_paths, const std::filesystem::path& out,
                         int fps, std::string* error = nullptr);

}  // namespace aether::render
