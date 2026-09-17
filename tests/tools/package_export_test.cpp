// export_effect(format="package"): the interchange directory an engine importer
// reads. The contract these tests pin down is docs/PACKAGE_FORMAT.md.
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
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

std::filesystem::path example(const std::string& file) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / file;
}

std::string read_text(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    std::ostringstream buffer;
    buffer << in.rdbuf();
    return buffer.str();
}

nlohmann::json read_json(const std::filesystem::path& path) { return nlohmann::json::parse(read_text(path)); }

// One export, reused by every case that reads it: exporting fire_aoe simulates
// and renders the preview frames, which is the only slow part of these tests.
struct Package {
    std::filesystem::path dir;
    nlohmann::json result;
    nlohmann::json manifest;
    nlohmann::json runtime;
    nlohmann::json effect;
    nlohmann::json graph;  // inspect_graph, to check that no enabled node was dropped
};

Package export_package(const std::string& source, const std::string& out_name) {
    Package package;
    package.dir = output_dir() / out_name;
    std::error_code ec;
    std::filesystem::remove_all(package.dir, ec);

    Session session(output_dir());
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", {{"path", example(source).string()}});
    package.result = registry.call(session, "export_effect",
                                   {{"format", "package"},
                                    {"path", package.dir.string()},
                                    {"options", {{"width", 192}, {"height", 192}}}});
    package.graph = registry.call(session, "inspect_graph", nlohmann::json::object());
    package.manifest = read_json(package.dir / "manifest.json");
    package.runtime = read_json(package.dir / "runtime.json");
    package.effect = read_json(package.dir / "effect.json");
    return package;
}

const Package& fire() {
    static const Package package = export_package("fire_aoe.json", "package_fire_aoe.aetherfx");
    return package;
}

const Package& lightning() {
    static const Package package = export_package("lightning_strike.json", "package_lightning.aetherfx");
    return package;
}

const nlohmann::json* find_by_id(const nlohmann::json& array, const std::string& id) {
    for (const auto& entry : array)
        if (entry.value("id", "") == id) return &entry;
    return nullptr;
}

// Counts the "v " and "f " lines an OBJ importer would read.
struct ObjCounts {
    size_t positions = 0;
    size_t normals = 0;
    size_t uvs = 0;
    size_t faces = 0;
};

ObjCounts count_obj(const std::filesystem::path& path) {
    ObjCounts counts;
    std::ifstream in(path);
    std::string line;
    while (std::getline(in, line)) {
        if (line.rfind("v ", 0) == 0) ++counts.positions;
        else if (line.rfind("vn ", 0) == 0) ++counts.normals;
        else if (line.rfind("vt ", 0) == 0) ++counts.uvs;
        else if (line.rfind("f ", 0) == 0) ++counts.faces;
    }
    return counts;
}

}  // namespace

TEST_CASE("a package export writes the manifest, the document and the resolved runtime", "[tools][package]") {
    const Package& package = fire();
    CHECK(package.result["path"].get<std::string>() == package.dir.string());
    CHECK(package.result["files"].size() >= 4);

    CHECK(std::filesystem::is_regular_file(package.dir / "manifest.json"));
    CHECK(std::filesystem::is_regular_file(package.dir / "effect.json"));
    CHECK(std::filesystem::is_regular_file(package.dir / "runtime.json"));

    CHECK(package.manifest["format"] == "aetherfx-package");
    CHECK(package.manifest["version"] == 1);
    CHECK(package.manifest["effect"] == "Fire AOE");
    CHECK(package.manifest["duration"].get<double>() == Approx(3.0));
    CHECK(package.manifest["seed"].get<int>() == 7);
    CHECK(package.manifest["fixed_dt"].get<double>() > 0.0);
    CHECK(package.manifest["generator"].get<std::string>().rfind("aetherfx", 0) == 0);
    CHECK(package.manifest["source_hash"].get<std::string>().size() == 16);
    CHECK(package.manifest["units"] == nlohmann::json({{"length", "meter"}, {"up", "y"}, {"handedness", "right"}}));

    // Every path in the manifest is package-relative and really exists.
    const nlohmann::json& files = package.manifest["files"];
    CHECK(files["effect"] == "effect.json");
    CHECK(files["runtime"] == "runtime.json");
    for (const char* group : {"textures", "meshes", "preview"}) {
        REQUIRE(files[group].is_array());
        for (const auto& entry : files[group]) {
            const std::string relative = entry.get<std::string>();
            CHECK(relative.find("..") == std::string::npos);
            CHECK(std::filesystem::is_regular_file(package.dir / relative));
        }
    }

    // effect.json is the source document, not a summary of it.
    CHECK(package.effect["name"] == "Fire AOE");
    CHECK(package.effect["nodes"].size() == package.graph["nodes"].size());

    // The resolved view repeats the identity of the effect so it stands alone.
    CHECK(package.runtime["format"] == "aetherfx-runtime");
    CHECK(package.runtime["duration"].get<double>() == Approx(3.0));
    CHECK(package.runtime["seed"].get<int>() == 7);
    CHECK(package.runtime["source_hash"] == package.manifest["source_hash"]);
    CHECK(package.runtime["timeline"]["phases"].size() == 5);
    CHECK(package.runtime["layers"].size() == 6);
    CHECK(package.runtime["layers"][0]["role"] == "telegraph");
    CHECK(package.runtime["render_settings"]["exposure"].get<double>() == Approx(0.95));
    CHECK(package.runtime["render_settings"]["grid"] == false);
}

TEST_CASE("runtime.json carries every enabled node with a resolved window", "[tools][package]") {
    const Package& package = fire();
    std::set<std::string> enabled;
    for (const auto& node : package.graph["nodes"])
        if (node["enabled"].get<bool>()) enabled.insert(node["id"].get<std::string>());
    REQUIRE(!enabled.empty());

    std::set<std::string> exported;
    for (const auto& node : package.runtime["nodes"]) {
        const std::string id = node["id"].get<std::string>();
        exported.insert(id);
        INFO("node " << id);
        CHECK(node["type"].is_string());
        CHECK(node["tier"].is_string());
        CHECK(node["backend"].is_string());
        CHECK(node["seed"].is_number());
        CHECK(node["parameters"].is_object());
        REQUIRE(node["window"].is_object());
        CHECK(node["window"]["start"].get<double>() >= 0.0);
        const double end = node["window"]["end"].get<double>();
        CHECK((end < 0.0 || end > node["window"]["start"].get<double>()));
    }
    CHECK(exported == enabled);

    // Resolved references replace the port names an importer would have to walk.
    const nlohmann::json* wall = find_by_id(package.runtime["nodes"], "fire_wall");
    REQUIRE(wall != nullptr);
    CHECK((*wall)["layer"] == "primary");
    CHECK((*wall)["tier"] == "particles");
    CHECK((*wall)["window"]["start"].get<double>() == Approx(0.4));
    CHECK((*wall)["window"]["end"].get<double>() == Approx(-1.0));  // -1 = open until the effect ends
    CHECK((*wall)["resolved"]["particle_system"] == "flame_ps");
    CHECK((*wall)["resolved"]["shape"] == "ring");

    const nlohmann::json* rocks = find_by_id(package.runtime["nodes"], "rock_ps");
    REQUIRE(rocks != nullptr);
    CHECK((*rocks)["resolved"]["mesh"] == "rock_mesh");
    CHECK((*rocks)["resolved"]["mesh_variants"].get<int>() == 8);
    CHECK((*rocks)["resolved"]["material"] == "mat_rock");
    CHECK((*rocks)["resolved"]["forces"] == nlohmann::json({"lift", "smoke_turb"}));
    CHECK((*rocks)["resolved"]["colliders"] == nlohmann::json({"ground"}));

    const nlohmann::json* rune = find_by_id(package.runtime["nodes"], "rune");
    REQUIRE(rune != nullptr);
    CHECK((*rune)["resolved"]["texture"] == "tex_rune");
}

TEST_CASE("an animated parameter is sampled across its window", "[tools][package]") {
    const Package& package = fire();
    const nlohmann::json* wall = find_by_id(package.runtime["nodes"], "fire_wall");
    REQUIRE(wall != nullptr);
    const nlohmann::json& rate = (*wall)["parameters"]["rate"];

    REQUIRE(rate.is_object());
    REQUIRE(rate.contains("track"));   // the authored keyframes survive
    REQUIRE(rate.contains("samples"));
    CHECK(rate["track"].size() == 5);
    CHECK(rate["track"][0]["time"].get<double>() == Approx(0.4));

    const nlohmann::json& samples = rate["samples"];
    const double fps = package.runtime["sampling"]["fps"].get<double>();
    CHECK(fps == Approx(30.0));
    REQUIRE(samples.size() > 60);  // 0.4 s .. 3.0 s at 30 Hz

    // The samples span the resolved window, an open window running to the end
    // of the effect, and they are ordered.
    CHECK(samples.front()[0].get<double>() == Approx(0.4));
    CHECK(samples.back()[0].get<double>() == Approx(3.0));
    double previous = -1.0;
    double peak = 0.0;
    for (const auto& sample : samples) {
        REQUIRE(sample.size() == 2);
        const double time = sample[0].get<double>();
        CHECK(time > previous);
        previous = time;
        peak = std::max(peak, sample[1].get<double>());
    }
    CHECK(peak == Approx(1300.0).epsilon(0.01));  // the keyframed peak is reachable from the samples

    // A constant parameter stays a bare value, so importers do not special-case it.
    CHECK((*wall)["parameters"]["radius"].get<double>() == Approx(3.5));
    CHECK((*wall)["parameters"]["spread"].get<double>() == Approx(14.0));
}

TEST_CASE("curves and gradients keep their keys and gain a sample table", "[tools][package]") {
    const Package& package = fire();
    const nlohmann::json* system = find_by_id(package.runtime["nodes"], "flame_ps");
    REQUIRE(system != nullptr);
    const int expected = package.runtime["sampling"]["curve_samples"].get<int>();
    CHECK(expected == 32);

    const nlohmann::json& size = (*system)["parameters"]["size_over_life"];
    REQUIRE(size.is_object());
    CHECK(size["keys"].size() == 3);
    REQUIRE(size["samples"].size() == 32);
    CHECK(size["samples"][0].get<double>() == Approx(size["keys"][0][1].get<double>()));
    CHECK(size["samples"][31].get<double>() == Approx(size["keys"][2][1].get<double>()));

    const nlohmann::json& color = (*system)["parameters"]["color_over_life"];
    REQUIRE(color.is_object());
    CHECK(color["keys"].size() == 4);
    REQUIRE(color["samples"].size() == 32);
    for (const auto& sample : color["samples"]) CHECK(sample.size() >= 3);  // rgb, a appended when not 1

    // A parameter left at its spec default is still written, with its samples.
    const nlohmann::json* smoke = find_by_id(package.runtime["nodes"], "smoke_ps");
    REQUIRE(smoke != nullptr);
    CHECK((*smoke)["parameters"]["emissive_over_life"]["samples"].size() == 32);
}

TEST_CASE("mesh variants are written as OBJ files", "[tools][package]") {
    const Package& package = fire();
    const nlohmann::json* mesh = find_by_id(package.runtime["meshes"], "rock_mesh");
    REQUIRE(mesh != nullptr);
    CHECK((*mesh)["variants"].get<int>() == 8);
    REQUIRE((*mesh)["files"].size() == 8);
    CHECK((*mesh)["file"] == "meshes/rock_mesh.obj");
    CHECK((*mesh)["vertex_count"].get<size_t>() > 0);
    CHECK((*mesh)["triangle_count"].get<size_t>() > 0);
    CHECK((*mesh)["bounds"]["min"][1].get<double>() < (*mesh)["bounds"]["max"][1].get<double>());

    for (const auto& entry : (*mesh)["files"]) {
        const std::filesystem::path path = package.dir / entry.get<std::string>();
        INFO("obj " << path.string());
        REQUIRE(std::filesystem::is_regular_file(path));
        const ObjCounts counts = count_obj(path);
        CHECK(counts.positions > 0);
        CHECK(counts.faces > 0);
        CHECK(counts.normals == counts.positions);
        CHECK(counts.uvs == counts.positions);
    }

    // Variant 0 is the one the summary describes.
    const ObjCounts first = count_obj(package.dir / "meshes" / "rock_mesh.obj");
    CHECK(first.positions == (*mesh)["vertex_count"].get<size_t>());
    CHECK(first.faces == (*mesh)["triangle_count"].get<size_t>());

    // The seeded variants really differ, which is why they are all shipped.
    CHECK(read_text(package.dir / "meshes" / "rock_mesh.obj") !=
          read_text(package.dir / "meshes" / "rock_mesh_v1.obj"));
}

TEST_CASE("baked textures are written with their frame layout", "[tools][package]") {
    const Package& package = fire();
    REQUIRE(package.runtime["textures"].size() == 4);

    const nlohmann::json* puff = find_by_id(package.runtime["textures"], "tex_puff");
    REQUIRE(puff != nullptr);
    CHECK((*puff)["frames"].get<int>() == 8);
    CHECK((*puff)["height"].get<int>() == 128);
    CHECK((*puff)["width"].get<int>() == 8 * (*puff)["frame_width"].get<int>());
    CHECK((*puff)["frame_width"].get<int>() == 128);
    CHECK((*puff)["channels"].get<int>() == 4);
    CHECK((*puff)["usage"] == "sprite");

    const nlohmann::json* rune = find_by_id(package.runtime["textures"], "tex_rune");
    REQUIRE(rune != nullptr);
    CHECK((*rune)["usage"] == "decal");
    CHECK((*rune)["frames"].get<int>() == 1);
    CHECK((*rune)["width"].get<int>() == 512);

    // The PNGs on disk match the sizes runtime.json promises, and no EXR is
    // written unless it was asked for.
    for (const auto& texture : package.runtime["textures"]) {
        const std::filesystem::path path = package.dir / texture["file"].get<std::string>();
        INFO("texture " << path.string());
        REQUIRE(std::filesystem::is_regular_file(path));
        const Image image = render::read_image(path);
        CHECK(image.width == texture["width"].get<int>());
        CHECK(image.height == texture["height"].get<int>());
        CHECK_FALSE(texture.contains("exr"));
    }
}

TEST_CASE("materials carry the resolved shading fields", "[tools][package]") {
    const Package& package = fire();
    REQUIRE(package.runtime["materials"].size() == 3);

    const nlohmann::json* fire_material = find_by_id(package.runtime["materials"], "mat_fire");
    REQUIRE(fire_material != nullptr);
    CHECK((*fire_material)["blend"] == "additive");
    CHECK((*fire_material)["shading"] == "unlit");
    CHECK((*fire_material)["dissolve"].get<double>() == Approx(0.55));
    CHECK((*fire_material)["erosion"].get<double>() == Approx(0.3));
    CHECK((*fire_material)["depth_fade"].get<double>() == Approx(0.15));
    CHECK((*fire_material)["soft_particle"] == true);
    CHECK((*fire_material)["emissive_intensity"].get<double>() == Approx(0.8));
    CHECK((*fire_material)["base_texture"].is_null());
    CHECK((*fire_material)["base_color"].size() >= 3);
    CHECK((*fire_material)["uv_scroll"].size() == 2);

    const nlohmann::json* rock_material = find_by_id(package.runtime["materials"], "mat_rock");
    REQUIRE(rock_material != nullptr);
    CHECK((*rock_material)["shading"] == "lit");
    CHECK((*rock_material)["fresnel_power"].get<double>() == Approx(2.5));
    CHECK((*rock_material)["double_sided"].is_boolean());
}

TEST_CASE("the package ships preview images of the intended look", "[tools][package]") {
    const Package& package = fire();
    const std::filesystem::path sheet = package.dir / "preview" / "contact_sheet.png";
    REQUIRE(std::filesystem::is_regular_file(sheet));
    const Image image = render::read_image(sheet);
    CHECK(image.width > image.height);  // the three frames sit side by side

    size_t frames = 0;
    for (const auto& entry : package.manifest["files"]["preview"]) {
        const std::string relative = entry.get<std::string>();
        if (relative.find("frame_") != std::string::npos) ++frames;
        CHECK(std::filesystem::is_regular_file(package.dir / relative));
    }
    CHECK(frames == 3);
}

TEST_CASE("exporting the same effect twice produces the same runtime.json", "[tools][package]") {
    const Package& first = fire();
    const Package second = export_package("fire_aoe.json", "package_fire_aoe_again.aetherfx");
    CHECK(read_text(second.dir / "runtime.json") == read_text(first.dir / "runtime.json"));
    CHECK(second.manifest["source_hash"] == first.manifest["source_hash"]);
}

TEST_CASE("a second effect exports its own analytic nodes", "[tools][package]") {
    const Package& package = lightning();
    CHECK(package.manifest["format"] == "aetherfx-package");
    CHECK(package.runtime["duration"].get<double>() == Approx(1.2));

    std::set<std::string> enabled;
    for (const auto& node : package.graph["nodes"])
        if (node["enabled"].get<bool>()) enabled.insert(node["id"].get<std::string>());
    std::set<std::string> exported;
    for (const auto& node : package.runtime["nodes"]) exported.insert(node["id"].get<std::string>());
    CHECK(exported == enabled);

    const nlohmann::json* bolt = find_by_id(package.runtime["nodes"], "main_bolt");
    REQUIRE(bolt != nullptr);
    CHECK((*bolt)["type"] == "beam");
    REQUIRE((*bolt)["resolved"].is_object());
    CHECK((*bolt)["resolved"].contains("origin_node"));
    CHECK((*bolt)["resolved"].contains("target_node"));

    const nlohmann::json* event = find_by_id(package.runtime["nodes"], "impact_event");
    REQUIRE(event != nullptr);
    CHECK((*event)["resolved"]["targets"].size() == 3);

    const nlohmann::json* chunk = find_by_id(package.runtime["meshes"], "chunk");
    REQUIRE(chunk != nullptr);
    CHECK((*chunk)["variants"].get<int>() == 1);
    CHECK(std::filesystem::is_regular_file(package.dir / (*chunk)["file"].get<std::string>()));
}

TEST_CASE("package options turn the optional outputs on and off", "[tools][package]") {
    const std::filesystem::path dir = output_dir() / "package_options.aetherfx";
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);

    Session session(output_dir());
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", {{"path", example("lightning_strike.json").string()}});
    const nlohmann::json result =
        registry.call(session, "export_effect",
                      {{"format", "package"},
                       {"path", dir.string()},
                       {"options", {{"preview", false}, {"obj", false}, {"exr", true}, {"fps", 10}, {"curve_samples", 8}}}});

    CHECK_FALSE(std::filesystem::exists(dir / "preview"));
    CHECK(result["manifest"]["files"]["meshes"].empty());
    CHECK(result["manifest"]["files"]["preview"].empty());

    const nlohmann::json runtime = read_json(dir / "runtime.json");
    CHECK(runtime["sampling"]["fps"].get<double>() == Approx(10.0));
    CHECK(runtime["sampling"]["curve_samples"].get<int>() == 8);
    for (const auto& texture : runtime["textures"]) {
        REQUIRE(texture.contains("exr"));
        CHECK(std::filesystem::is_regular_file(dir / texture["exr"].get<std::string>()));
    }
    // The mesh is still described even when the OBJ files were not written, but
    // nothing in the package names a file that is not there.
    REQUIRE(runtime["meshes"].size() == 1);
    CHECK(runtime["meshes"][0]["triangle_count"].get<size_t>() > 0);
    CHECK(runtime["meshes"][0]["file"].is_null());
    CHECK(runtime["meshes"][0]["files"].empty());
    CHECK_FALSE(std::filesystem::exists(dir / "meshes" / "chunk.obj"));
}

TEST_CASE("export_effect rejects an unknown format", "[tools][package]") {
    Session session(output_dir());
    const ToolRegistry& registry = ToolRegistry::standard();
    registry.call(session, "load_effect", {{"path", example("lightning_strike.json").string()}});
    CHECK_THROWS_AS(registry.call(session, "export_effect",
                                  {{"format", "unreal"}, {"path", (output_dir() / "nope").string()}}),
                    Error);
}
