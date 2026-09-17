// aether::imageio acceptance: the codecs must round trip through PNG (sRGB, 8-bit)
// and EXR (linear half), and pack_flipbook must lay frames out as a grid.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <filesystem>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/imageio/image_io.hpp"

using namespace aether;
using namespace aether::imageio;
using Catch::Approx;

namespace {

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

Image ramp_image(int w, int h) {
    Image img(w, h);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(w);
            const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(h);
            img.set(x, y, Color{u, v, 1.0f - u, 0.25f + 0.75f * v});
        }
    }
    return img;
}

}  // namespace

TEST_CASE("PNG round trips through sRGB encoding", "[imageio]") {
    const Image source = ramp_image(24, 16);
    const std::filesystem::path path = output_dir() / "roundtrip.png";

    TonemapSettings tonemap;
    tonemap.filmic = false;  // clamp only: the encode is then exactly linear -> sRGB
    write_png(path, source, tonemap);
    REQUIRE(std::filesystem::exists(path));

    const Image read_back = read_image(path);
    REQUIRE(read_back.width == source.width);
    REQUIRE(read_back.height == source.height);
    for (size_t i = 0; i < source.pixel_count(); ++i) {
        for (int c = 0; c < 4; ++c)
            CHECK(read_back.rgba[i * 4 + static_cast<size_t>(c)] ==
                  Approx(source.rgba[i * 4 + static_cast<size_t>(c)]).margin(0.01));
    }
}

TEST_CASE("EXR round trips linear values, including above 1", "[imageio]") {
    Image source(8, 4);
    for (int y = 0; y < 4; ++y)
        for (int x = 0; x < 8; ++x)
            source.set(x, y, Color{static_cast<float>(x) * 0.5f, 4.0f, 0.125f, 0.5f});
    const std::filesystem::path path = output_dir() / "roundtrip.exr";

    write_exr(path, source);
    REQUIRE(std::filesystem::exists(path));

    const Image read_back = read_image(path);
    REQUIRE(read_back.width == 8);
    REQUIRE(read_back.height == 4);
    for (int y = 0; y < 4; ++y) {
        for (int x = 0; x < 8; ++x) {
            const Color c = read_back.get(x, y);
            CHECK(c.r == Approx(static_cast<float>(x) * 0.5f).margin(1e-3));
            CHECK(c.g == Approx(4.0f).margin(1e-2));  // half precision, no clamping at 1
            CHECK(c.b == Approx(0.125f).margin(1e-3));
            CHECK(c.a == Approx(0.5f).margin(1e-3));
        }
    }
}

TEST_CASE("write_image picks the codec from the extension", "[imageio]") {
    const Image source = ramp_image(6, 6);
    write_image(output_dir() / "by_extension.exr", source);
    write_image(output_dir() / "by_extension.png", source);
    CHECK(std::filesystem::exists(output_dir() / "by_extension.exr"));
    CHECK(std::filesystem::exists(output_dir() / "by_extension.png"));
    CHECK_THROWS_AS(write_image(output_dir() / "by_extension.tga", source), Error);
    CHECK_THROWS_AS(read_image(output_dir() / "not_here.png"), Error);
}

TEST_CASE("pack_flipbook lays frames out left to right, top to bottom", "[imageio]") {
    std::vector<Image> frames;
    for (int i = 0; i < 6; ++i) {
        Image f(4, 3);
        const float v = static_cast<float>(i + 1) / 8.0f;
        f.fill(Color{v, v, v, 1.0f});
        frames.push_back(std::move(f));
    }
    int rows = 0;
    const Image sheet = pack_flipbook(frames, 4, rows);
    CHECK(rows == 2);
    CHECK(sheet.width == 16);
    CHECK(sheet.height == 6);
    for (int i = 0; i < 6; ++i) {
        const int x = (i % 4) * 4 + 1;
        const int y = (i / 4) * 3 + 1;
        CHECK(sheet.get(x, y).r == Approx(static_cast<float>(i + 1) / 8.0f));
    }
    CHECK(sheet.get(14, 4).a == Approx(0.0f));  // the two unused cells stay transparent
    CHECK_THROWS_AS(pack_flipbook(frames, 0, rows), Error);
}
