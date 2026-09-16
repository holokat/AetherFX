// Image input/output for AetherFX: PNG/JPG/TGA/BMP through stb, EXR through tinyexr, plus the
// tonemapper, flipbook packing and an optional ffmpeg video encode.
//
// This is the single translation unit that instantiates the stb and tinyexr implementations.

#include "aether/render/image_io.hpp"

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>

#include "aether/core/error.hpp"

// --- third party ------------------------------------------------------------------------------
#if defined(__clang__)
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Weverything"
#elif defined(__GNUC__)
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-parameter"
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-but-set-variable"
#pragma GCC diagnostic ignored "-Wunused-variable"
#pragma GCC diagnostic ignored "-Wsign-compare"
#pragma GCC diagnostic ignored "-Wtype-limits"
#pragma GCC diagnostic ignored "-Wshadow"
#pragma GCC diagnostic ignored "-Wmissing-field-initializers"
#pragma GCC diagnostic ignored "-Wimplicit-fallthrough"
#pragma GCC diagnostic ignored "-Wpedantic"
#pragma GCC diagnostic ignored "-Wconversion"
#pragma GCC diagnostic ignored "-Wcast-qual"
#pragma GCC diagnostic ignored "-Wdouble-promotion"
#endif

#define STB_IMAGE_IMPLEMENTATION
#include <stb_image.h>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include <stb_image_write.h>

#define TINYEXR_IMPLEMENTATION
#include <tinyexr.h>

#if defined(__clang__)
#pragma clang diagnostic pop
#elif defined(__GNUC__)
#pragma GCC diagnostic pop
#endif
// ----------------------------------------------------------------------------------------------

namespace aether::render {
namespace {

std::string lowercase_extension(const std::filesystem::path& path) {
    std::string ext = path.extension().string();
    for (char& c : ext) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return ext;
}

bool is_exr(const std::filesystem::path& path) { return lowercase_extension(path) == ".exr"; }

// 8-bit sRGB -> linear lookup, built once.
const std::array<float, 256>& srgb_table() {
    static const std::array<float, 256> table = [] {
        std::array<float, 256> t{};
        for (int i = 0; i < 256; ++i) t[static_cast<size_t>(i)] = srgb_to_linear(static_cast<float>(i) / 255.0f);
        return t;
    }();
    return table;
}

void ensure_parent_directory(const std::filesystem::path& path) {
    const std::filesystem::path parent = path.parent_path();
    if (parent.empty()) return;
    std::error_code ec;
    std::filesystem::create_directories(parent, ec);
}

Image load_exr(const std::filesystem::path& path) {
    float* data = nullptr;
    int w = 0, h = 0;
    const char* err = nullptr;
    const int rc = LoadEXR(&data, &w, &h, path.string().c_str(), &err);
    if (rc != TINYEXR_SUCCESS) {
        std::string message = "failed to read EXR '" + path.string() + "'";
        if (err) {
            message += ": ";
            message += err;
            FreeEXRErrorMessage(err);
        }
        if (data) std::free(data);
        throw Error("io", message);
    }
    Image img(w, h);
    std::memcpy(img.rgba.data(), data, static_cast<size_t>(w) * static_cast<size_t>(h) * 4 * sizeof(float));
    std::free(data);
    return img;
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

// -----------------------------------------------------------------------------------------------
// Reading
// -----------------------------------------------------------------------------------------------
Image read_image(const std::filesystem::path& path) {
    if (is_exr(path)) return load_exr(path);

    const std::string file = path.string();
    int w = 0, h = 0, channels = 0;
    if (stbi_is_16_bit(file.c_str())) {
        stbi_us* data = stbi_load_16(file.c_str(), &w, &h, &channels, 4);
        if (!data) throw Error("io", "failed to read image '" + file + "': " + stbi_failure_reason());
        Image img(w, h);
        const size_t n = static_cast<size_t>(w) * static_cast<size_t>(h);
        for (size_t i = 0; i < n; ++i) {
            for (int c = 0; c < 3; ++c)
                img.rgba[i * 4 + static_cast<size_t>(c)] =
                    srgb_to_linear(static_cast<float>(data[i * 4 + static_cast<size_t>(c)]) / 65535.0f);
            img.rgba[i * 4 + 3] = static_cast<float>(data[i * 4 + 3]) / 65535.0f;  // alpha stays linear
        }
        stbi_image_free(data);
        return img;
    }

    stbi_uc* data = stbi_load(file.c_str(), &w, &h, &channels, 4);
    if (!data) throw Error("io", "failed to read image '" + file + "': " + stbi_failure_reason());
    const std::array<float, 256>& table = srgb_table();
    Image img(w, h);
    const size_t n = static_cast<size_t>(w) * static_cast<size_t>(h);
    for (size_t i = 0; i < n; ++i) {
        for (int c = 0; c < 3; ++c)
            img.rgba[i * 4 + static_cast<size_t>(c)] = table[data[i * 4 + static_cast<size_t>(c)]];
        img.rgba[i * 4 + 3] = static_cast<float>(data[i * 4 + 3]) / 255.0f;  // alpha stays linear
    }
    stbi_image_free(data);
    return img;
}

// -----------------------------------------------------------------------------------------------
// Tonemapping
// -----------------------------------------------------------------------------------------------

// Krzysztof Narkowicz's ACES filmic curve fit. aces_fit(0) == 0; alpha passes through untouched.
Color aces_fit(Color c) {
    constexpr float a = 2.51f, b = 0.03f, cc = 2.43f, d = 0.59f, e = 0.14f;
    auto curve = [](float x) {
        x = std::max(0.0f, x);
        return saturate((x * (a * x + b)) / (x * (cc * x + d) + e));
    };
    return Color{curve(c.r), curve(c.g), curve(c.b), c.a};
}

Image tonemap_image(const Image& hdr, const TonemapSettings& settings) {
    Image out(hdr.width, hdr.height);
    if (hdr.empty()) return out;
    for (size_t i = 0; i < hdr.pixel_count(); ++i) {
        Color c{hdr.rgba[i * 4 + 0] * settings.exposure, hdr.rgba[i * 4 + 1] * settings.exposure,
                hdr.rgba[i * 4 + 2] * settings.exposure, hdr.rgba[i * 4 + 3]};
        if (settings.filmic) {
            c = aces_fit(c);
        } else {
            c.r = saturate(c.r);
            c.g = saturate(c.g);
            c.b = saturate(c.b);
        }
        if (settings.srgb) {
            c.r = linear_to_srgb(c.r);
            c.g = linear_to_srgb(c.g);
            c.b = linear_to_srgb(c.b);
        }
        out.rgba[i * 4 + 0] = c.r;
        out.rgba[i * 4 + 1] = c.g;
        out.rgba[i * 4 + 2] = c.b;
        out.rgba[i * 4 + 3] = saturate(c.a);
    }
    return out;
}

// -----------------------------------------------------------------------------------------------
// Writing
// -----------------------------------------------------------------------------------------------
void write_png(const std::filesystem::path& path, const Image& image, const TonemapSettings& tonemap) {
    if (image.empty()) throw Error("io", "write_png: empty image for '" + path.string() + "'");
    const Image ldr = tonemap_image(image, tonemap);
    std::vector<unsigned char> bytes(ldr.pixel_count() * 4);
    for (size_t i = 0; i < bytes.size(); ++i)
        bytes[i] = static_cast<unsigned char>(std::lround(saturate(ldr.rgba[i]) * 255.0f));
    ensure_parent_directory(path);
    if (stbi_write_png(path.string().c_str(), ldr.width, ldr.height, 4, bytes.data(), ldr.width * 4) == 0)
        throw Error("io", "failed to write PNG '" + path.string() + "'");
}

void write_exr(const std::filesystem::path& path, const Image& image) {
    if (image.empty()) throw Error("io", "write_exr: empty image for '" + path.string() + "'");
    ensure_parent_directory(path);
    const char* err = nullptr;
    const int rc = SaveEXR(image.rgba.data(), image.width, image.height, 4, /*save_as_fp16=*/1,
                           path.string().c_str(), &err);
    if (rc != TINYEXR_SUCCESS) {
        std::string message = "failed to write EXR '" + path.string() + "'";
        if (err) {
            message += ": ";
            message += err;
            FreeEXRErrorMessage(err);
        }
        throw Error("io", message);
    }
}

void write_image(const std::filesystem::path& path, const Image& image, const TonemapSettings& tonemap) {
    if (is_exr(path)) {
        write_exr(path, image);
        return;
    }
    const std::string ext = lowercase_extension(path);
    if (!ext.empty() && ext != ".png")
        throw Error("io", "write_image: unsupported extension '" + ext + "' (use .png or .exr)");
    write_png(path, image, tonemap);
}

// -----------------------------------------------------------------------------------------------
// Flipbook
// -----------------------------------------------------------------------------------------------
Image pack_flipbook(const std::vector<Image>& frames, int columns, int& rows_out) {
    rows_out = 0;
    if (frames.empty()) return Image{};
    if (columns < 1) throw Error("bad_argument", "pack_flipbook: columns must be >= 1");
    const int fw = frames.front().width;
    const int fh = frames.front().height;
    if (fw <= 0 || fh <= 0) throw Error("bad_argument", "pack_flipbook: frames must be non-empty");
    for (const Image& f : frames) {
        if (f.width != fw || f.height != fh)
            throw Error("bad_argument", "pack_flipbook: all frames must have the same size");
    }
    // `columns` is honoured exactly: a sheet wider than the frame count simply has empty cells.
    const int count = static_cast<int>(frames.size());
    const int cols = columns;
    const int rows = (count + cols - 1) / cols;
    rows_out = rows;

    Image sheet(fw * cols, fh * rows, Color::transparent());  // empty cells stay transparent black
    for (int i = 0; i < count; ++i) {
        const int cx = (i % cols) * fw;
        const int cy = (i / cols) * fh;
        const Image& f = frames[static_cast<size_t>(i)];
        for (int y = 0; y < fh; ++y)
            std::memcpy(sheet.at(cx, cy + y), f.at(0, y), static_cast<size_t>(fw) * 4 * sizeof(float));
    }
    return sheet;
}

// -----------------------------------------------------------------------------------------------
// Video
// -----------------------------------------------------------------------------------------------
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
