// Image IO tests: tonemapping, PNG/EXR round trips, flipbook packing and the ffmpeg encoder.
#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

#include "aether/core/error.hpp"
#include "aether/render/image_io.hpp"

using namespace aether;
using namespace aether::render;
using Catch::Approx;

namespace {

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

Image gradient_image(int w, int h, float scale) {
    Image img(w, h);
    for (int y = 0; y < h; ++y) {
        for (int x = 0; x < w; ++x) {
            const float u = (static_cast<float>(x) + 0.5f) / static_cast<float>(w);
            const float v = (static_cast<float>(y) + 0.5f) / static_cast<float>(h);
            img.set(x, y, Color{u * scale, v * scale, (1.0f - u) * scale, 0.25f + 0.75f * v});
        }
    }
    return img;
}

}  // namespace

TEST_CASE("aces_fit is a monotonic tonemap anchored at zero", "[render]") {
    const Color zero = aces_fit(Color{0.0f, 0.0f, 0.0f, 1.0f});
    CHECK(zero.r == Approx(0.0f).margin(1e-7));
    CHECK(zero.g == Approx(0.0f).margin(1e-7));
    CHECK(zero.b == Approx(0.0f).margin(1e-7));
    CHECK(zero.a == Approx(1.0f));

    float previous = -1.0f;
    for (int i = 0; i <= 200; ++i) {
        const float x = static_cast<float>(i) * 0.1f;
        const float y = aces_fit(Color{x, x, x, 1.0f}).r;
        CHECK(y >= previous);
        CHECK(y <= 1.0f);
        previous = y;
    }
    CHECK(aces_fit(Color{100.0f, 100.0f, 100.0f, 1.0f}).r > 0.9f);
}

TEST_CASE("tonemap_image is monotonic and bounded", "[render]") {
    Image ramp(64, 1);
    for (int x = 0; x < 64; ++x) {
        const float v = static_cast<float>(x) * 0.25f;
        ramp.set(x, 0, Color{v, v, v, 1.0f});
    }

    TonemapSettings filmic;
    filmic.filmic = true;
    filmic.srgb = true;
    const Image t1 = tonemap_image(ramp, filmic);
    float previous = -1.0f;
    for (int x = 0; x < 64; ++x) {
        const float v = t1.get(x, 0).r;
        CHECK(v >= previous);
        CHECK(v <= 1.0f);
        previous = v;
    }

    TonemapSettings clamped;
    clamped.filmic = false;
    clamped.srgb = false;
    const Image t2 = tonemap_image(ramp, clamped);
    CHECK(t2.get(0, 0).r == Approx(0.0f).margin(1e-6));
    CHECK(t2.get(8, 0).r == Approx(1.0f).margin(1e-6));  // 8*0.25 = 2.0 clamps to 1

    // Exposure brightens.
    TonemapSettings hot = filmic;
    hot.exposure = 4.0f;
    CHECK(tonemap_image(ramp, hot).get(1, 0).r > t1.get(1, 0).r);
}

TEST_CASE("PNG and EXR round-trip", "[render]") {
    const std::filesystem::path dir = output_dir();
    const Image hdr = gradient_image(32, 24, 3.0f);

    SECTION("exr keeps linear HDR values") {
        const std::filesystem::path path = dir / "io_roundtrip.exr";
        REQUIRE_NOTHROW(write_image(path, hdr));
        const Image back = read_image(path);
        REQUIRE(back.width == 32);
        REQUIRE(back.height == 24);
        for (size_t i = 0; i < hdr.rgba.size(); ++i)
            CHECK(back.rgba[i] == Approx(hdr.rgba[i]).epsilon(1e-2).margin(1e-3));
    }

    SECTION("png is tonemapped and sRGB encoded") {
        const std::filesystem::path path = dir / "io_roundtrip.png";
        TonemapSettings tm;
        tm.exposure = 1.0f;
        tm.filmic = true;
        tm.srgb = true;
        REQUIRE_NOTHROW(write_image(path, hdr, tm));
        const Image back = read_image(path);
        REQUIRE(back.width == 32);
        const Image expected = tonemap_image(hdr, tm);
        for (size_t p = 0; p < back.pixel_count(); ++p) {
            for (int c = 0; c < 3; ++c) {
                const float got = linear_to_srgb(back.rgba[p * 4 + static_cast<size_t>(c)]);
                CHECK(got == Approx(expected.rgba[p * 4 + static_cast<size_t>(c)]).margin(2.0 / 255.0));
            }
            CHECK(back.rgba[p * 4 + 3] == Approx(expected.rgba[p * 4 + 3]).margin(2.0 / 255.0));
        }
    }
}

TEST_CASE("read_image reports failures as Error(\"io\")", "[render]") {
    const std::filesystem::path missing = output_dir() / "definitely_not_here.png";
    std::error_code ec;
    std::filesystem::remove(missing, ec);
    REQUIRE_THROWS_AS(read_image(missing), Error);
    try {
        read_image(missing);
    } catch (const Error& e) {
        CHECK(e.code() == "io");
    }
    REQUIRE_THROWS_AS(read_image(output_dir() / "definitely_not_here.exr"), Error);
}

TEST_CASE("pack_flipbook grids frames", "[render]") {
    std::vector<Image> frames;
    for (int i = 0; i < 5; ++i) {
        Image f(8, 4, Color{static_cast<float>(i), 0.0f, 0.0f, 1.0f});
        frames.push_back(f);
    }
    int rows = 0;
    const Image sheet = pack_flipbook(frames, 3, rows);
    CHECK(rows == 2);
    CHECK(sheet.width == 24);
    CHECK(sheet.height == 8);
    CHECK(sheet.get(0, 0).r == Approx(0.0f));
    CHECK(sheet.get(8, 0).r == Approx(1.0f));
    CHECK(sheet.get(16, 0).r == Approx(2.0f));
    CHECK(sheet.get(0, 4).r == Approx(3.0f));
    CHECK(sheet.get(8, 4).r == Approx(4.0f));
    // The unused cell stays transparent black.
    CHECK(sheet.get(16, 4).r == Approx(0.0f));
    CHECK(sheet.get(16, 4).a == Approx(0.0f));

    int single_rows = 0;
    const Image column = pack_flipbook(frames, 1, single_rows);
    CHECK(single_rows == 5);
    CHECK(column.width == 8);
    CHECK(column.height == 20);

    // More columns than frames: the extra cells stay empty.
    int wide_rows = 0;
    const Image wide = pack_flipbook(frames, 8, wide_rows);
    CHECK(wide_rows == 1);
    CHECK(wide.width == 64);
    CHECK(wide.height == 4);
    CHECK(wide.get(40, 0).a == Approx(0.0f));

    int empty_rows = -1;
    CHECK(pack_flipbook({}, 4, empty_rows).empty());
    CHECK(empty_rows == 0);

    std::vector<Image> mismatched{Image(4, 4), Image(4, 5)};
    int bad_rows = 0;
    CHECK_THROWS_AS(pack_flipbook(mismatched, 2, bad_rows), Error);
}

TEST_CASE("encode_video_ffmpeg writes an mp4 when ffmpeg is available", "[render]") {
    const std::filesystem::path dir = output_dir() / "video";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);

    std::vector<std::filesystem::path> frames;
    for (int i = 0; i < 4; ++i) {
        Image f(64, 64, Color{static_cast<float>(i) / 4.0f, 0.2f, 0.8f - static_cast<float>(i) * 0.15f, 1.0f});
        const std::filesystem::path p = dir / ("frame_" + std::to_string(i) + ".png");
        write_png(p, f);
        frames.push_back(p);
    }

    const std::filesystem::path out = dir / "clip.mp4";
    std::filesystem::remove(out, ec);
    std::string error;
    const bool ok = encode_video_ffmpeg(frames, out, 12, &error);
    if (!ok && error.find("not available") != std::string::npos) {
        WARN("ffmpeg is not installed: skipping the video encode check (" << error << ")");
        SUCCEED();
        return;
    }
    INFO("ffmpeg error: " << error);
    REQUIRE(ok);
    REQUIRE(std::filesystem::exists(out));
    CHECK(std::filesystem::file_size(out) > 0);
    // No temporary files left behind.
    CHECK_FALSE(std::filesystem::exists(dir / "clip.aether_concat.txt"));

    // GIF takes the two-pass palette route.
    const std::filesystem::path gif = dir / "clip.gif";
    std::filesystem::remove(gif, ec);
    error.clear();
    CHECK(encode_video_ffmpeg(frames, gif, 8, &error));
    INFO("ffmpeg gif error: " << error);
    CHECK(std::filesystem::exists(gif));
    CHECK_FALSE(std::filesystem::exists(dir / "clip.aether_palette.png"));
}

TEST_CASE("encode_video_ffmpeg rejects bad input", "[render]") {
    std::string error;
    CHECK_FALSE(encode_video_ffmpeg({}, output_dir() / "empty.mp4", 24, &error));
    CHECK_FALSE(error.empty());
    error.clear();
    CHECK_FALSE(encode_video_ffmpeg({output_dir() / "io_roundtrip.png"}, output_dir() / "bad.mp4", 0, &error));
    CHECK_FALSE(error.empty());
}
