// Video encoding for AetherFX. The image codecs, tonemapper and flipbook packing
// live in aether::imageio (src/imageio); this file only drives an external
// ffmpeg over an already written frame sequence.

#include "aether/render/image_io.hpp"

#include <cctype>
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

namespace aether::render {
namespace {

std::string lowercase_extension(const std::filesystem::path& path) {
    std::string ext = path.extension().string();
    for (char& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return ext;
}

void ensure_parent_directory(const std::filesystem::path& path) {
    const std::filesystem::path parent = path.parent_path();
    if (parent.empty()) return;
    std::error_code ec;
    std::filesystem::create_directories(parent, ec);
}

// Quoting for both the shell and ffmpeg's concat demuxer: wrap in single quotes and escape any
// embedded single quote as '\''.
std::string single_quote(const std::string& s) {
    std::string out = "'";
    for (char c : s) {
        if (c == '\'') out += "'\\''";
        else out += c;
    }
    out += "'";
    return out;
}

bool ffmpeg_available() {
#if defined(_WIN32)
    return std::system("ffmpeg -version > nul 2>&1") == 0;
#else
    // Run through an inner shell so that a crash message from a broken install ("Abort trap")
    // is swallowed together with the normal output.
    return std::system("/bin/sh -c '(ffmpeg -version) >/dev/null 2>&1' >/dev/null 2>/dev/null") == 0;
#endif
}

std::string read_text_file(const std::filesystem::path& path) {
    std::ifstream in(path);
    if (!in) return {};
    std::string text((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    if (text.size() > 4000) text = text.substr(text.size() - 4000);
    return text;
}

}  // namespace

bool encode_video_ffmpeg(const std::vector<std::filesystem::path>& frame_paths,
                         const std::filesystem::path& out, int fps, std::string* error) {
    auto fail = [&](const std::string& message) {
        if (error) *error = message;
        return false;
    };
    if (frame_paths.empty()) return fail("encode_video_ffmpeg: no frames given");
    if (fps <= 0) return fail("encode_video_ffmpeg: fps must be > 0");
    if (!ffmpeg_available()) return fail("ffmpeg is not available on PATH (running `ffmpeg -version` failed)");

    std::filesystem::path work = out.parent_path();
    if (work.empty()) work = std::filesystem::path(".");
    ensure_parent_directory(out);

    const std::string stem = out.stem().string();
    const std::filesystem::path list_path = work / (stem + ".aether_concat.txt");
    const std::filesystem::path log_path = work / (stem + ".aether_ffmpeg.log");
    const std::filesystem::path palette_path = work / (stem + ".aether_palette.png");

    {
        std::ofstream list(list_path);
        if (!list) return fail("could not write the ffmpeg concat list '" + list_path.string() + "'");
        const double duration = 1.0 / static_cast<double>(fps);
        char duration_text[64];
        std::snprintf(duration_text, sizeof duration_text, "%.9f", duration);
        for (const std::filesystem::path& p : frame_paths) {
            std::error_code ec;
            const std::filesystem::path abs = std::filesystem::absolute(p, ec);
            list << "file " << single_quote((ec ? p : abs).string()) << "\n";
            list << "duration " << duration_text << "\n";
        }
        // The concat demuxer ignores the last entry's duration, so repeat the final frame.
        std::error_code ec;
        const std::filesystem::path abs = std::filesystem::absolute(frame_paths.back(), ec);
        list << "file " << single_quote((ec ? frame_paths.back() : abs).string()) << "\n";
    }

    const std::string ext = lowercase_extension(out);
    const std::string fps_text = std::to_string(fps);
    const std::string scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2";
    const std::string input = "-f concat -safe 0 -i " + single_quote(list_path.string());
    const std::string redirect = "> " + single_quote(log_path.string()) + " 2>&1";

    int rc = 0;
    if (ext == ".gif") {
        // Two-pass palette generation gives far better GIF quality than the default 256-colour
        // quantiser.
        const std::string filters = "fps=" + fps_text + "," + scale + ":flags=lanczos";
        const std::string pass1 = "ffmpeg -y " + input + " -vf " + single_quote(filters + ",palettegen") + " " +
                                  single_quote(palette_path.string()) + " " + redirect;
        rc = std::system(pass1.c_str());
        if (rc == 0) {
            const std::string pass2 = "ffmpeg -y " + input + " -i " + single_quote(palette_path.string()) +
                                      " -lavfi " + single_quote(filters + " [x]; [x][1:v] paletteuse") + " " +
                                      single_quote(out.string()) + " " + redirect;
            rc = std::system(pass2.c_str());
        }
    } else {
        const std::string filters = "fps=" + fps_text + "," + scale + ",format=yuv420p";
        const std::string cmd = "ffmpeg -y " + input + " -vf " + single_quote(filters) +
                                " -pix_fmt yuv420p -crf 18 " + single_quote(out.string()) + " " + redirect;
        rc = std::system(cmd.c_str());
    }

    const bool ok = rc == 0 && std::filesystem::exists(out);
    if (!ok && error) *error = "ffmpeg failed (exit " + std::to_string(rc) + "): " + read_text_file(log_path);

    std::error_code ec;
    std::filesystem::remove(list_path, ec);
    std::filesystem::remove(log_path, ec);
    std::filesystem::remove(palette_path, ec);
    return ok;
}

}  // namespace aether::render
