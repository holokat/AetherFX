// Compiler acceptance: the three reference documents must compile with zero
// errors, produce the expected tiers/backends/resources/windows, and the
// compiler must never mutate its input. See docs/ARCHITECTURE.md section 4.
#include <algorithm>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/rng.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/imageio/image_io.hpp"

using namespace aether;
using compiler::CompiledEffect;
using compiler::CompiledNode;
using compiler::CompileOptions;
using compiler::Tier;

namespace {

std::filesystem::path example_path(const std::string& name) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name;
}

Effect load_example(const std::string& name) { return load_effect_file(example_path(name)); }

bool has_code(const Diagnostics& d, const std::string& code) {
    return std::any_of(d.items.begin(), d.items.end(), [&](const Diagnostic& i) { return i.code == code; });
}

bool has_code_for(const Diagnostics& d, const std::string& code, const std::string& node) {
    return std::any_of(d.items.begin(), d.items.end(),
                       [&](const Diagnostic& i) { return i.code == code && i.node && *i.node == node; });
}

size_t index_of(const CompiledEffect& c, const std::string& id) {
    for (size_t i = 0; i < c.nodes.size(); ++i)
        if (c.nodes[i].id == id) return i;
    FAIL("node not found in compiled plan: " << id);
    return 0;
}

std::filesystem::path output_dir() {
    std::filesystem::path dir(AETHER_TEST_OUTPUT_DIR);
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    return dir;
}

// Flipbook cells carry a distinct flat value so a baked strip can be checked cell by cell.
constexpr int kCellW = 8;
constexpr int kCellH = 6;
float cell_value(int index) { return static_cast<float>(index + 1) / 16.0f; }

// Writes a columns x rows sprite sheet whose cells are numbered left to right, top to bottom.
// The PNG is written without the filmic curve so reading it back recovers the same linear value.
void write_test_grid(const std::filesystem::path& path, int columns, int rows, int cell_w, int cell_h) {
    Image sheet(columns * cell_w, rows * cell_h);
    for (int r = 0; r < rows; ++r) {
        for (int c = 0; c < columns; ++c) {
            const float v = cell_value(r * columns + c);
            for (int y = 0; y < cell_h; ++y)
                for (int x = 0; x < cell_w; ++x) sheet.set(c * cell_w + x, r * cell_h + y, Color{v, v, v, 1.0f});
        }
    }
    imageio::TonemapSettings tonemap;
    tonemap.filmic = false;
    imageio::write_png(path, sheet, tonemap);
}

std::string message_for(const Diagnostics& d, const std::string& code) {
    for (const Diagnostic& i : d.items)
        if (i.code == code) return i.message;
    return {};
}

void print_warnings(const std::string& name, const Diagnostics& d) {
    for (const Diagnostic& item : d.items) {
        if (item.severity == Severity::Error) continue;
        std::cout << "  [" << name << "] " << to_string(item.severity) << " " << item.code << " "
                  << (item.node ? "(" + *item.node + ") " : "") << item.message << "\n";
    }
}

}  // namespace

TEST_CASE("the three examples compile with zero errors", "[compiler][examples]") {
    for (const std::string& name : {"fireball.json", "fire_aoe.json", "lightning_strike.json"}) {
        const Effect effect = load_example(name);
        const CompiledEffect compiled = compiler::compile(effect);
        INFO(name << " diagnostics:\n" << compiled.diagnostics.summary());
        CHECK(compiled.diagnostics.error_count() == 0);
        CHECK(compiled.ok());
        CHECK(!compiled.nodes.empty());
        CHECK(compiled.fixed_dt == 1.0 / 60.0);
        CHECK(compiled.source_hash == effect_hash(effect));
        print_warnings(name, compiled.diagnostics);
    }
}

TEST_CASE("compile never mutates its input", "[compiler]") {
    for (const std::string& name : {"fireball.json", "fire_aoe.json", "lightning_strike.json"}) {
        Effect effect = load_example(name);
        const uint64_t before = effect_hash(effect);
        const CompiledEffect compiled = compiler::compile(effect);
        CHECK(effect_hash(effect) == before);
        CHECK(compiled.source_hash == before);
        CHECK(effect_hash(compiled.effect) == before);
    }
}

TEST_CASE("tiers and backends", "[compiler][plan]") {
    SECTION("fire_aoe rigid physics falls back with W102") {
        const CompiledEffect c = compiler::compile(load_example("fire_aoe.json"));
        const CompiledNode* rock = c.find("rock_ps");
        REQUIRE(rock != nullptr);
        CHECK(rock->tier == Tier::Physics);
        CHECK(rock->backend == "cpu_particles_collision");
        CHECK(has_code_for(c.diagnostics, "W102", "rock_ps"));

        const CompiledNode* spark = c.find("spark_ps");  // colliders, no rigid physics
        REQUIRE(spark != nullptr);
        CHECK(spark->tier == Tier::Particles);
        CHECK(spark->backend == "cpu_particles_collision");

        const CompiledNode* wall = c.find("flame_ps");  // no colliders
        REQUIRE(wall != nullptr);
        CHECK(wall->tier == Tier::Particles);
        CHECK(wall->backend == "cpu_particles");

        const CompiledNode* emitter = c.find("fire_wall");
        REQUIRE(emitter != nullptr);
        CHECK(emitter->tier == Tier::Particles);
        CHECK(emitter->backend == "cpu_particles");
        CHECK(emitter->particle_system == "flame_ps");

        for (const char* analytic : {"cam", "rune", "fire_light", "haze", "mat_fire", "tex_puff", "ground", "gravity"}) {
            const CompiledNode* n = c.find(analytic);
            REQUIRE(n != nullptr);
            CHECK(n->tier == Tier::Analytic);
            CHECK(n->backend == "analytic");
        }

        const nlohmann::json plan = c.plan_json();
        CHECK(plan.at("tiers").at("physics").get<int>() == 1);
        CHECK(plan.at("tiers").at("particles").get<int>() > 0);
        CHECK(plan.at("tiers").at("analytic").get<int>() > 0);
        CHECK(plan.at("tiers").at("volumetric").get<int>() == 0);
        CHECK(plan.at("backends").at("cpu_particles_collision").get<int>() >= 2);
        CHECK(plan.at("source_hash").is_string());
        CHECK(plan.at("fixed_dt").get<double>() == 1.0 / 60.0);
        CHECK(plan.at("nodes").size() == c.nodes.size());
        CHECK(plan.at("nodes")[0].contains("seed"));
        CHECK(plan.at("nodes")[0].contains("start_time"));
        CHECK(plan.at("nodes")[0].contains("noise_node"));
    }

    SECTION("lightning debris_ps is a physics-tier fallback too") {
        const CompiledEffect c = compiler::compile(load_example("lightning_strike.json"));
        const CompiledNode* debris = c.find("debris_ps");
        REQUIRE(debris != nullptr);
        CHECK(debris->tier == Tier::Physics);
        CHECK(debris->backend == "cpu_particles_collision");
        CHECK(has_code_for(c.diagnostics, "W102", "debris_ps"));

        const CompiledNode* event = c.find("impact_event");
        REQUIRE(event != nullptr);
        CHECK(event->targets == std::vector<NodeId>{"debris", "shock_ring", "impact_sparks"});
    }
}

TEST_CASE("resolved windows", "[compiler][window]") {
    const CompiledEffect aoe = compiler::compile(load_example("fire_aoe.json"));
    const CompiledNode* gather = aoe.find("gather");
    REQUIRE(gather != nullptr);
    CHECK(gather->start_time == 0.0);
    CHECK(gather->end_time == 0.4);

    const CompiledNode* burst = aoe.find("burst");
    REQUIRE(burst != nullptr);
    CHECK(burst->start_time == 0.4);
    CHECK(burst->end_time == 0.8);

    const CompiledNode* embers = aoe.find("embers");  // start_time 2.1, unbounded
    REQUIRE(embers != nullptr);
    CHECK(embers->start_time == Catch::Approx(2.1));
    CHECK(embers->end_time < 0.0);

    const CompiledNode* haze = aoe.find("haze");  // start_time 0.5, duration 2.1
    REQUIRE(haze != nullptr);
    CHECK(haze->start_time == Catch::Approx(0.5));
    CHECK(haze->end_time == Catch::Approx(2.6));

    const CompiledEffect fire = compiler::compile(load_example("fireball.json"));
    const CompiledNode* heat = fire.find("heat");  // no window parameters at all
    REQUIRE(heat != nullptr);
    CHECK(heat->start_time == 0.0);
    CHECK(heat->end_time == -1.0);
}

TEST_CASE("unknown phase produces W001 and the start_time fallback", "[compiler][window]") {
    Effect effect = load_example("fireball.json");
    Node* flames = effect.find_node("flames");
    REQUIRE(flames != nullptr);
    flames->parameters["phase"] = Parameter{std::string("nope")};
    flames->parameters["start_time"] = Parameter{0.25f};
    flames->parameters["duration"] = Parameter{0.5f};

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.diagnostics.error_count() == 0);
    CHECK(has_code_for(c.diagnostics, "W001", "flames"));
    const CompiledNode* n = c.find("flames");
    REQUIRE(n != nullptr);
    CHECK(n->start_time == Catch::Approx(0.25));
    CHECK(n->end_time == Catch::Approx(0.75));

    // resolve_window is exposed for tools and behaves the same way standalone.
    double start = -1.0, end = -1.0;
    Diagnostics d;
    compiler::resolve_window(effect, *flames, start, end, &d);
    CHECK(start == Catch::Approx(0.25));
    CHECK(end == Catch::Approx(0.75));
    CHECK(has_code(d, "W001"));
}

TEST_CASE("baked resources", "[compiler][resources]") {
    const CompiledEffect aoe = compiler::compile(load_example("fire_aoe.json"));
    for (const char* id : {"tex_puff", "tex_spark", "tex_rune", "tex_scorch"}) {
        const TextureResource* tex = aoe.resources.texture(id);
        INFO("texture " << id);
        REQUIRE(tex != nullptr);
        CHECK(!tex->image.empty());
    }
    CHECK(aoe.resources.texture("tex_puff")->frames == 8);  // animated puff strip
    CHECK(aoe.resources.texture("tex_puff")->image.width == 128 * 8);
    CHECK(aoe.resources.texture("tex_puff")->image.height == 128);
    CHECK(aoe.resources.texture("tex_spark")->image.width == 32);
    CHECK(aoe.resources.texture("tex_rune")->image.width == 512);
    CHECK(aoe.resources.texture("tex_scorch")->image.width == 256);

    const MeshData* rock = aoe.resources.mesh("rock_mesh");  // procedural rock with variants
    REQUIRE(rock != nullptr);
    CHECK(rock->triangle_count() > 12);
    CHECK(rock->total_area() > 0.0f);  // area table pre-built by the compiler
    CHECK(aoe.find("rock_mesh")->mesh_variants == 8);
    CHECK(aoe.resources.mesh("rock_mesh#7") != nullptr);

    for (const char* id : {"mat_fire", "mat_smoke", "mat_rock"}) {
        const MaterialDesc* mat = aoe.resources.material(id);
        INFO("material " << id);
        REQUIRE(mat != nullptr);
        CHECK(mat->id == id);
    }
    CHECK(aoe.resources.material("mat_fire")->blend == BlendMode::Additive);
    CHECK(aoe.resources.material("mat_fire")->dissolve == Catch::Approx(0.55f));
    CHECK(aoe.resources.material("mat_smoke")->shading == Shading::Lit);
    CHECK(aoe.resources.material("mat_rock")->base_color.r == Catch::Approx(0.09f));
    CHECK(aoe.resources.material("mat_rock")->fresnel_power == Catch::Approx(2.5f));

    // texture ids reach the systems that use them
    CHECK(aoe.find("flame_ps")->sprite_id == "tex_flame");
    CHECK(aoe.find("flame_ps")->material_id == "mat_fire");
    CHECK(aoe.find("rock_ps")->mesh_id == "rock_mesh");
    CHECK(aoe.find("rune")->texture_id == "tex_rune");

    const CompiledEffect lightning = compiler::compile(load_example("lightning_strike.json"));
    const MeshData* chunk = lightning.resources.mesh("chunk");
    REQUIRE(chunk != nullptr);
    CHECK(chunk->triangle_count() == 12);

    const CompiledEffect fireball = compiler::compile(load_example("fireball.json"));
    REQUIRE(fireball.resources.mesh("core") != nullptr);
    CHECK(fireball.resources.mesh("core")->positions.size() > 100);
    CHECK(fireball.find("trail")->source_node == "core");
    CHECK(fireball.find("flame_ps")->forces == std::vector<NodeId>{"flame_curl", "buoyancy"});
    CHECK(fireball.find("spark_ps")->colliders == std::vector<NodeId>{"ground"});
}

TEST_CASE("materials carry their texture ports", "[compiler][resources]") {
    Effect effect = load_example("fireball.json");
    Node* mat = effect.find_node("mat_flame");
    REQUIRE(mat != nullptr);
    mat->inputs["base_texture"] = {NodeRef::parse("tex_puff")};
    mat->inputs["noise_texture"] = {NodeRef::parse("tex_spark")};

    const CompiledEffect c = compiler::compile(effect);
    REQUIRE(c.diagnostics.error_count() == 0);
    const MaterialDesc* desc = c.resources.material("mat_flame");
    REQUIRE(desc != nullptr);
    CHECK(desc->base_texture == "tex_puff");
    CHECK(desc->noise_texture == "tex_spark");
    CHECK(desc->gradient_texture.empty());
    CHECK(c.find("mat_flame")->texture_id == "tex_puff");
}

TEST_CASE("bake_textures=false skips texture baking", "[compiler][resources]") {
    CompileOptions options;
    options.bake_textures = false;
    const CompiledEffect c = compiler::compile(load_example("fireball.json"), options);
    CHECK(c.resources.textures.empty());
    CHECK(!c.resources.meshes.empty());
    CHECK(!c.resources.materials.empty());
}

TEST_CASE("a file texture bakes its flipbook grid into a horizontal strip", "[compiler][resources]") {
    const std::filesystem::path sheet = output_dir() / "grid_4x2.png";
    write_test_grid(sheet, 4, 2, kCellW, kCellH);

    Effect effect = load_example("fireball.json");
    Node* tex = effect.find_node("tex_spark");
    REQUIRE(tex != nullptr);
    tex->parameters["source"] = Parameter{std::string("file")};
    tex->parameters["path"] = Parameter{sheet.string()};
    tex->parameters.erase("width");  // unset: the baked frames keep the file's cell size
    tex->parameters.erase("height");
    tex->parameters["columns"] = Parameter{4};
    tex->parameters["rows"] = Parameter{2};

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.diagnostics.error_count() == 0);
    CHECK_FALSE(has_code_for(c.diagnostics, "W105", "tex_spark"));
    const TextureResource* baked = c.resources.texture("tex_spark");
    REQUIRE(baked != nullptr);
    CHECK(baked->frames == 8);
    CHECK(baked->frame_width() == kCellW);
    CHECK(baked->image.width == kCellW * 8);
    CHECK(baked->image.height == kCellH);
    // Cells are laid out left to right, top to bottom, so frame i carries cell i's value.
    for (int i = 0; i < 8; ++i) {
        const Color c0 = baked->image.get(i * kCellW + kCellW / 2, kCellH / 2);
        CHECK(c0.r == Catch::Approx(cell_value(i)).margin(0.01));
    }
    CHECK(c.resources.texture("tex_puff") != nullptr);
}

TEST_CASE("a file texture resamples when width/height are set", "[compiler][resources]") {
    const std::filesystem::path sheet = output_dir() / "grid_strip.png";
    write_test_grid(sheet, 3, 1, kCellW, kCellH);

    Effect effect = load_example("fireball.json");
    Node* tex = effect.find_node("tex_spark");
    REQUIRE(tex != nullptr);
    tex->parameters["source"] = Parameter{std::string("file")};
    tex->parameters["path"] = Parameter{sheet.string()};
    tex->parameters["frames"] = Parameter{3};  // a horizontal strip needs no columns/rows
    tex->parameters["width"] = Parameter{16};
    tex->parameters["height"] = Parameter{20};

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.diagnostics.error_count() == 0);
    const TextureResource* baked = c.resources.texture("tex_spark");
    REQUIRE(baked != nullptr);
    CHECK(baked->frames == 3);
    CHECK(baked->frame_width() == 16);
    CHECK(baked->image.width == 48);
    CHECK(baked->image.height == 20);
    for (int i = 0; i < 3; ++i) {
        const Color c0 = baked->image.get(i * 16 + 8, 10);
        CHECK(c0.r == Catch::Approx(cell_value(i)).margin(0.02));
    }
}

TEST_CASE("a missing file texture is E102 and names the path", "[compiler][resources]") {
    Effect effect = load_example("fireball.json");
    Node* tex = effect.find_node("tex_spark");
    REQUIRE(tex != nullptr);
    tex->parameters["source"] = Parameter{std::string("file")};
    tex->parameters["path"] = Parameter{std::string("sprites/does_not_exist.png")};

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.diagnostics.error_count() > 0);
    CHECK(has_code_for(c.diagnostics, "E102", "tex_spark"));
    CHECK(message_for(c.diagnostics, "E102").find("does_not_exist.png") != std::string::npos);
    CHECK(c.resources.texture("tex_spark") == nullptr);
}

TEST_CASE("a relative file texture path resolves against CompileOptions::base_dir", "[compiler][resources]") {
    const std::filesystem::path dir = output_dir() / "textures";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    write_test_grid(dir / "relative.png", 2, 1, kCellW, kCellH);

    Effect effect = load_example("fireball.json");
    Node* tex = effect.find_node("tex_spark");
    REQUIRE(tex != nullptr);
    tex->parameters["source"] = Parameter{std::string("file")};
    tex->parameters["path"] = Parameter{std::string("textures/relative.png")};
    tex->parameters.erase("width");
    tex->parameters.erase("height");
    tex->parameters["frames"] = Parameter{2};

    CHECK(compiler::compile(effect).diagnostics.error_count() > 0);  // no base_dir: not found

    CompileOptions options;
    options.base_dir = output_dir();
    const CompiledEffect c = compiler::compile(effect, options);
    CHECK(c.diagnostics.error_count() == 0);
    const TextureResource* baked = c.resources.texture("tex_spark");
    REQUIRE(baked != nullptr);
    CHECK(baked->frames == 2);
    CHECK(baked->frame_width() == kCellW);
}

TEST_CASE("a broken texture graph becomes a compile error", "[compiler][resources]") {
    Effect effect = load_example("fireball.json");
    Node* tex = effect.find_node("tex_puff");
    REQUIRE(tex != nullptr);
    nlohmann::json graph = param_json(*tex, "graph");
    graph["nodes"][2]["inputs"]["b"] = "does_not_exist";
    tex->parameters["graph"] = Parameter{graph};

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.diagnostics.error_count() > 0);
    CHECK(has_code_for(c.diagnostics, "T003", "tex_puff"));
    CHECK(c.resources.texture("tex_puff") == nullptr);
}

TEST_CASE("mesh source fallbacks and import failures", "[compiler][resources]") {
    SECTION("procedural meshes fall back to a sphere with W106") {
        Effect effect = load_example("fireball.json");
        effect.find_node("core")->parameters["source"] = Parameter{std::string("procedural")};
        const CompiledEffect c = compiler::compile(effect);
        CHECK(c.diagnostics.error_count() == 0);
        CHECK(has_code_for(c.diagnostics, "W106", "core"));
        REQUIRE(c.resources.mesh("core") != nullptr);
    }
    SECTION("a missing OBJ is reported as E101") {
        Effect effect = load_example("fireball.json");
        effect.find_node("core")->parameters["source"] = Parameter{std::string("imported")};
        effect.find_node("core")->parameters["path"] = Parameter{std::string("/nonexistent/mesh.obj")};
        const CompiledEffect c = compiler::compile(effect);
        CHECK(has_code_for(c.diagnostics, "E101", "core"));
    }
}

TEST_CASE("validation errors stop compilation unless allow_errors", "[compiler][diagnostics]") {
    Effect effect = load_example("fireball.json");
    effect.find_node("flame_ps")->parameters["lifetime"] = Parameter{-4.0f};  // E006 out of range

    const CompiledEffect strict = compiler::compile(effect);
    CHECK(strict.diagnostics.error_count() > 0);
    CHECK(strict.nodes.empty());
    CHECK(strict.resources.textures.empty());
    CHECK(strict.effect.nodes.size() == effect.nodes.size());
    CHECK(strict.source_hash == effect_hash(effect));

    CompileOptions options;
    options.allow_errors = true;
    const CompiledEffect loose = compiler::compile(effect, options);
    CHECK(loose.diagnostics.error_count() > 0);
    CHECK(!loose.nodes.empty());
}

TEST_CASE("execution order respects dependencies", "[compiler][order]") {
    const CompiledEffect c = compiler::compile(load_example("fire_aoe.json"));
    // an emitter depends on its particle system
    CHECK(index_of(c, "flame_ps") < index_of(c, "fire_wall"));
    CHECK(index_of(c, "column_ps") < index_of(c, "column"));
    // forces, colliders, materials and sprites come before the system using them
    CHECK(index_of(c, "flame_curl") < index_of(c, "flame_ps"));
    CHECK(index_of(c, "buoyancy_fire") < index_of(c, "flame_ps"));
    CHECK(index_of(c, "lift") < index_of(c, "rock_ps"));
    CHECK(index_of(c, "ground") < index_of(c, "rock_ps"));
    CHECK(index_of(c, "mat_fire") < index_of(c, "flame_ps"));
    CHECK(index_of(c, "tex_flame") < index_of(c, "flame_ps"));
    CHECK(index_of(c, "rock_mesh") < index_of(c, "rock_ps"));
    // a parent comes before its child
    const CompiledEffect fireball = compiler::compile(load_example("fireball.json"));
    CHECK(index_of(fireball, "core") < index_of(fireball, "flames"));
    CHECK(index_of(fireball, "core") < index_of(fireball, "glow"));
}

TEST_CASE("disabled nodes are dropped from the plan and from references", "[compiler][order]") {
    Effect effect = load_example("fireball.json");
    effect.find_node("buoyancy")->enabled = false;

    const CompiledEffect c = compiler::compile(effect);
    CHECK(c.find("buoyancy") == nullptr);
    CHECK(c.find("flame_ps")->forces == std::vector<NodeId>{"flame_curl"});
    CHECK(c.find("smoke_ps")->forces == std::vector<NodeId>{"smoke_turb"});
}

TEST_CASE("node seeds are derived and stable", "[compiler][determinism]") {
    const Effect effect = load_example("fireball.json");
    const CompiledEffect a = compiler::compile(effect);
    const CompiledEffect b = compiler::compile(effect);
    REQUIRE(a.nodes.size() == b.nodes.size());
    for (size_t i = 0; i < a.nodes.size(); ++i) {
        CHECK(a.nodes[i].id == b.nodes[i].id);
        CHECK(a.nodes[i].seed == b.nodes[i].seed);
    }
    CHECK(a.find("flames")->seed == derive_seed(effect.seed, "flames", std::nullopt));

    Effect reseeded = effect;
    reseeded.seed = effect.seed + 1;
    CHECK(compiler::compile(reseeded).find("flames")->seed != a.find("flames")->seed);
}

TEST_CASE("the particle budget is reported as I001", "[compiler][diagnostics]") {
    const CompiledEffect c = compiler::compile(load_example("fire_aoe.json"));
    const auto it = std::find_if(c.diagnostics.items.begin(), c.diagnostics.items.end(),
                                 [](const Diagnostic& d) { return d.code == "I001"; });
    REQUIRE(it != c.diagnostics.items.end());
    CHECK(it->severity == Severity::Info);
    CHECK(it->message.find("21120") != std::string::npos);  // sum of max_particles in fire_aoe.json
}

TEST_CASE("mesh and sdf colliders warn W101", "[compiler][diagnostics]") {
    Effect effect = load_example("fireball.json");
    effect.find_node("ground")->parameters["collider_type"] = Parameter{std::string("mesh")};
    effect.find_node("ground")->inputs["mesh"] = {NodeRef::parse("core")};
    const CompiledEffect c = compiler::compile(effect);
    CHECK(has_code_for(c.diagnostics, "W101", "ground"));
    CHECK(c.find("ground")->shape_mesh == "core");
}

TEST_CASE("a trail on a particle system warns W103", "[compiler][diagnostics]") {
    Effect effect = load_example("fireball.json");
    effect.find_node("trail")->inputs["source"] = {NodeRef::parse("spark_ps")};
    const CompiledEffect c = compiler::compile(effect);
    CHECK(has_code_for(c.diagnostics, "W103", "trail"));
    CHECK(c.find("trail")->particle_system == "spark_ps");
    CHECK(c.find("spark_ps")->trail == "trail");
}

TEST_CASE("volume nodes compile to the stub backend with W104", "[compiler][diagnostics]") {
    Effect effect = load_example("fireball.json");
    Node volume;
    volume.id = "smoke_volume";
    volume.type = NodeType::Volume;
    volume.parameters["volume_type"] = Parameter{std::string("smoke")};
    effect.add_node(volume);

    const CompiledEffect c = compiler::compile(effect);
    const CompiledNode* n = c.find("smoke_volume");
    REQUIRE(n != nullptr);
    CHECK(n->tier == Tier::Volumetric);
    CHECK(n->backend == "volume_stub");
    CHECK(has_code_for(c.diagnostics, "W104", "smoke_volume"));
    CHECK(c.plan_json().at("tiers").at("volumetric").get<int>() == 1);
}

TEST_CASE("seeded mesh primitives bake one resource per variant", "[compiler][mesh][variants]") {
    Effect effect = load_example("fireball.json");
    Node crystal;
    crystal.id = "spike";
    crystal.type = NodeType::Mesh;
    crystal.parameters["primitive"] = Parameter{std::string("crystal")};
    crystal.parameters["variants"] = Parameter{4};
    crystal.parameters["irregularity"] = Parameter{0.6f};
    crystal.parameters["segments"] = Parameter{6};
    crystal.parameters["radius"] = Parameter{0.2f};
    crystal.parameters["height"] = Parameter{0.8f};
    effect.add_node(crystal);

    const CompiledEffect c = compiler::compile(effect);
    INFO(c.diagnostics.summary());
    CHECK(c.diagnostics.error_count() == 0);

    const CompiledNode* node = c.find("spike");
    REQUIRE(node != nullptr);
    CHECK(node->mesh_variants == 4);
    CHECK(node->to_json().at("mesh_variants").get<int>() == 4);

    // Variant 0 keeps the plain node id; the rest are suffixed "#1".."#N-1".
    REQUIRE(c.resources.mesh("spike") != nullptr);
    for (int i = 1; i < 4; ++i) REQUIRE(c.resources.mesh("spike#" + std::to_string(i)) != nullptr);
    CHECK(c.resources.mesh("spike#4") == nullptr);

    // Each variant is seeded from derive_seed(node stream, i), so they differ.
    for (int i = 0; i < 4; ++i)
        for (int j = i + 1; j < 4; ++j) {
            const MeshData* a = c.resources.mesh(i == 0 ? "spike" : "spike#" + std::to_string(i));
            const MeshData* b = c.resources.mesh(j == 0 ? "spike" : "spike#" + std::to_string(j));
            INFO("variants " << i << " and " << j);
            CHECK(a->positions != b->positions);
        }

    // Every variant is still usable as an emitter shape (area table built).
    for (int i = 0; i < 4; ++i) {
        const MeshData* m = c.resources.mesh(i == 0 ? "spike" : "spike#" + std::to_string(i));
        CHECK(m->triangle_count() > 0);
        CHECK(m->total_area() > 0.0f);
    }

    SECTION("compilation is reproducible") {
        const CompiledEffect again = compiler::compile(effect);
        CHECK(again.resources.mesh("spike#2")->positions == c.resources.mesh("spike#2")->positions);
    }

    SECTION("the effect seed changes the variants") {
        Effect reseeded = effect;
        reseeded.seed += 1;
        const CompiledEffect other = compiler::compile(reseeded);
        CHECK(other.resources.mesh("spike")->positions != c.resources.mesh("spike")->positions);
    }
}

TEST_CASE("variants are ignored by the non-seeded primitives", "[compiler][mesh][variants]") {
    Effect effect = load_example("fireball.json");
    Node sphere;
    sphere.id = "ball";
    sphere.type = NodeType::Mesh;
    sphere.parameters["primitive"] = Parameter{std::string("sphere")};
    sphere.parameters["variants"] = Parameter{8};
    effect.add_node(sphere);

    const CompiledEffect c = compiler::compile(effect);
    INFO(c.diagnostics.summary());
    CHECK(c.diagnostics.error_count() == 0);
    CHECK(c.find("ball")->mesh_variants == 1);
    CHECK(c.resources.mesh("ball") != nullptr);
    CHECK(c.resources.mesh("ball#1") == nullptr);
    // and no warning is emitted for the ignored parameter
    CHECK(!has_code_for(c.diagnostics, "W005", "ball"));
}

TEST_CASE("rock and shard primitives compile", "[compiler][mesh]") {
    for (const std::string& primitive : {"rock", "shard"}) {
        Effect effect = load_example("fireball.json");
        Node mesh;
        mesh.id = "debris";
        mesh.type = NodeType::Mesh;
        mesh.parameters["primitive"] = Parameter{primitive};
        mesh.parameters["variants"] = Parameter{3};
        effect.add_node(mesh);

        const CompiledEffect c = compiler::compile(effect);
        INFO(primitive << ": " << c.diagnostics.summary());
        CHECK(c.diagnostics.error_count() == 0);
        CHECK(c.find("debris")->mesh_variants == 3);
        CHECK(c.resources.mesh("debris#2") != nullptr);
    }
}
