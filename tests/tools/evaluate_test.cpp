// The image metrics an agent judges its own work with. The reference numbers
// come from python/aetherfx/evaluate.py, which measures the same images.
#include <filesystem>
#include <string>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/core/image.hpp"
#include "aether/render/image_io.hpp"
#include "aether/tools/registry.hpp"

using namespace aether;
using namespace aether::tools;
using Catch::Approx;

namespace {

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

std::filesystem::path synthetic_reference() {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "references" / "fire_aoe_synthetic.png";
}

nlohmann::json call(Session& session, const std::string& tool, nlohmann::json args = nlohmann::json::object()) {
    return ToolRegistry::standard().call(session, tool, args);
}

std::filesystem::path write_solid(const std::string& name, Color color, int size = 256) {
    Image image(size, size, color);
    const std::filesystem::path path = output_dir() / name;
    render::write_png(path, image);
    return path;
}

}  // namespace

TEST_CASE("image_stats measures a reference image", "[tools][evaluate]") {
    Session session(output_dir());
    const nlohmann::json stats = call(session, "inspect_render", {{"path", synthetic_reference().string()}});

    CHECK(stats["width"].get<int>() > 0);
    CHECK(stats["height"].get<int>() > 0);
    CHECK(stats["coverage"].get<double>() > 0.0);
    CHECK(stats["coverage"].get<double>() < 1.0);
    CHECK(stats["mean_luminance"].get<double>() > 0.0);
    CHECK(stats["max_luminance"].get<double>() > stats["mean_luminance"].get<double>());
    REQUIRE(stats["dominant_colors"].is_array());
    CHECK(stats["dominant_colors"].size() == 5);

    double total_share = 0.0;
    double previous = 1.1;
    for (const auto& entry : stats["dominant_colors"]) {
        CHECK(entry["rgb"].size() == 3);
        const double share = entry["share"].get<double>();
        CHECK(share <= previous);  // sorted by descending share
        previous = share;
        total_share += share;
    }
    CHECK(total_share == Approx(1.0).margin(1e-6));

    REQUIRE(stats["bbox"].size() == 4);
    CHECK(stats["bbox"][0].get<double>() < stats["bbox"][2].get<double>());
    CHECK(stats["bbox"][1].get<double>() < stats["bbox"][3].get<double>());
    CHECK(stats["hash"].get<std::string>().size() == 16);

    // The numbers python/aetherfx/evaluate.py reports for this file.
    CHECK(stats["coverage"].get<double>() == Approx(0.164394).margin(1e-5));
    CHECK(stats["coverage_threshold"].get<double>() == Approx(0.242188).margin(1e-6));
    CHECK(stats["mean_luminance"].get<double>() == Approx(0.115923).margin(1e-5));
    CHECK(stats["max_luminance"].get<double>() == Approx(0.95836).margin(1e-5));
    CHECK(stats["bbox"][0].get<double>() == Approx(0.07617).margin(1e-5));
    CHECK(stats["bbox"][3].get<double>() == Approx(0.80859).margin(1e-5));
}

TEST_CASE("inspect_render rejects a missing file", "[tools][evaluate]") {
    Session session(output_dir());
    CHECK_THROWS_AS(call(session, "inspect_render", {{"path", "no_such_image.png"}}), Error);
}

TEST_CASE("comparing an image with itself scores 1", "[tools][evaluate]") {
    Session session(output_dir());
    const nlohmann::json result = call(session, "compare_reference", {{"reference_path", synthetic_reference().string()},
                                                                      {"render_path", synthetic_reference().string()}});
    CHECK(result["coverage_iou"] == Approx(1.0));
    CHECK(result["palette_distance"] == Approx(0.0).margin(1e-9));
    CHECK(result["luminance_histogram_distance"] == Approx(0.0).margin(1e-9));
    CHECK(result["centroid_offset"] == Approx(0.0).margin(1e-9));
    CHECK(result["radial_profile_distance"] == Approx(0.0).margin(1e-9));
    CHECK(result["score"] == Approx(1.0));
    CHECK(result["notes"].empty());
}

TEST_CASE("comparing a reference with a black frame scores low", "[tools][evaluate]") {
    Session session(output_dir());
    const std::filesystem::path black = write_solid("compare_black.png", Color::black());
    const nlohmann::json result =
        call(session, "compare_reference", {{"reference_path", synthetic_reference().string()}, {"render_path", black.string()}});

    CHECK(result["coverage_iou"].get<double>() == Approx(0.0).margin(1e-9));
    CHECK(result["centroid_offset"] == Approx(1.0));
    CHECK(result["score"].get<double>() < 0.45);
    CHECK_FALSE(result["notes"].empty());
    bool mentions_missing_coverage = false;
    for (const auto& note : result["notes"])
        if (note.get<std::string>().find("no lit coverage") != std::string::npos) mentions_missing_coverage = true;
    CHECK(mentions_missing_coverage);
}

TEST_CASE("a differently coloured version of the reference is penalised on palette", "[tools][evaluate]") {
    Session session(output_dir());
    const Image reference = render::read_image(synthetic_reference());
    Image cool = reference;
    for (size_t i = 0; i < cool.pixel_count(); ++i) {
        float* pixel = &cool.rgba[i * 4];
        std::swap(pixel[0], pixel[2]);  // swap red and blue: same shape, wrong colour
    }
    const std::filesystem::path path = output_dir() / "compare_cool.png";
    render::write_png(path, cool);

    const nlohmann::json result =
        call(session, "compare_reference", {{"reference_path", synthetic_reference().string()}, {"render_path", path.string()}});
    CHECK(result["coverage_iou"].get<double>() > 0.3);       // the silhouette survives
    CHECK(result["palette_distance"].get<double>() > 0.05);  // the colour does not
    CHECK(result["score"].get<double>() < 0.99);
    bool mentions_palette = false;
    for (const auto& note : result["notes"])
        if (note.get<std::string>().find("palette") != std::string::npos) mentions_palette = true;
    CHECK(mentions_palette);
}

TEST_CASE("compare_reference needs something to compare against", "[tools][evaluate]") {
    Session session(output_dir());
    CHECK_THROWS_AS(call(session, "compare_reference", {{"reference_path", synthetic_reference().string()}}), Error);
    CHECK_THROWS_AS(call(session, "compare_reference", {{"reference_path", "no_such_reference.png"}, {"time", 0.0}}),
                    Error);
}

TEST_CASE("two flat colours are compared by palette alone", "[tools][evaluate]") {
    Session session(output_dir());
    const std::filesystem::path orange = write_solid("flat_orange.png", Color{1.0f, 0.4f, 0.05f, 1.0f});
    const std::filesystem::path white = write_solid("flat_white.png", Color{1.0f, 1.0f, 1.0f, 1.0f});
    const nlohmann::json result =
        call(session, "compare_reference", {{"reference_path", orange.string()}, {"render_path", white.string()}});
    CHECK(result["palette_distance"].get<double>() > 0.1);
    CHECK(result["score"].get<double>() < 1.0);
}
