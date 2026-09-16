// Frame sequences render on a worker pool while the simulation stays sequential. What matters is
// that the result does not depend on how many workers did it: same frames, same files, same
// statistics (apart from timings), whether AETHER_RENDER_THREADS is 1 or 4.
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/error.hpp"
#include "aether/render/renderer.hpp"
#include "aether/tools/registry.hpp"

using namespace aether;
using namespace aether::tools;
using Catch::Approx;

namespace {

std::filesystem::path output_dir(const std::string& leaf) {
    std::filesystem::path dir = std::filesystem::path(AETHER_TEST_OUTPUT_DIR) / leaf;
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);
    std::filesystem::create_directories(dir, ec);
    return dir;
}

nlohmann::json call(Session& session, const std::string& tool, nlohmann::json args = nlohmann::json::object()) {
    return ToolRegistry::standard().call(session, tool, args);
}

// A small but complete effect: an emitter, a lit smoke system (so the new per-fragment shading is
// exercised) and a light for it to react to.
Session make_session(const std::filesystem::path& dir) {
    Session session(dir);
    call(session, "create_effect", {{"name", "Threaded"}, {"duration", 0.4}, {"seed", 11}});
    call(session, "create_layer", {{"name", "Primary"}, {"role", "primary"}});
    call(session, "create_material",
         {{"id", "mat_smoke"}, {"parameters", {{"shading", "lit"}, {"blend", "alpha"}, {"fresnel_power", 2.0}}}});
    call(session, "create_particle_system",
         {{"id", "smoke_ps"},
          {"layer", "primary"},
          {"parameters", {{"lifetime", 0.5}, {"size", 0.35}, {"max_particles", 400}}}});
    call(session, "connect_nodes", {{"from", "mat_smoke"}, {"to", "smoke_ps"}, {"port", "material"}});
    call(session, "create_emitter",
         {{"id", "smoke"},
          {"layer", "primary"},
          {"parameters", {{"shape", "sphere"}, {"radius", 0.3}, {"rate", 400}}}});
    call(session, "connect_nodes", {{"from", "smoke_ps"}, {"to", "smoke"}, {"port", "particle"}});
    call(session, "create_light",
         {{"id", "key"}, {"parameters", {{"position", {-1.5, 1.0, 1.0}}, {"intensity", 40.0}, {"radius", 8.0}}}});
    return session;
}

// FNV-1a over a file's bytes: the PNGs must match byte for byte, not just visually.
uint64_t file_hash(const std::filesystem::path& path) {
    std::ifstream in(path, std::ios::binary);
    REQUIRE(in.good());
    uint64_t h = 1469598103934665603ULL;
    char buffer[8192];
    while (in.read(buffer, sizeof buffer) || in.gcount() > 0) {
        const std::streamsize n = in.gcount();
        for (std::streamsize i = 0; i < n; ++i) {
            h ^= static_cast<unsigned char>(buffer[i]);
            h *= 1099511628211ULL;
        }
        if (!in) break;
    }
    return h;
}

// Sets AETHER_RENDER_THREADS for its lifetime and restores whatever was there before.
class ScopedThreadCount {
public:
    explicit ScopedThreadCount(const char* value) {
        if (const char* previous = std::getenv("AETHER_RENDER_THREADS")) {
            had_previous_ = true;
            previous_ = previous;
        }
        if (value == nullptr) ::unsetenv("AETHER_RENDER_THREADS");
        else ::setenv("AETHER_RENDER_THREADS", value, 1);
    }
    ~ScopedThreadCount() {
        if (had_previous_) ::setenv("AETHER_RENDER_THREADS", previous_.c_str(), 1);
        else ::unsetenv("AETHER_RENDER_THREADS");
    }
    ScopedThreadCount(const ScopedThreadCount&) = delete;
    ScopedThreadCount& operator=(const ScopedThreadCount&) = delete;

private:
    bool had_previous_ = false;
    std::string previous_;
};

}  // namespace

TEST_CASE("AETHER_RENDER_THREADS selects the worker count", "[tools][render][threads]") {
    {
        const ScopedThreadCount one("1");
        CHECK(render::frame_render_thread_count() == 1);
    }
    {
        const ScopedThreadCount four("4");
        CHECK(render::frame_render_thread_count() == 4);
    }
    {
        const ScopedThreadCount huge("999");  // capped
        CHECK(render::frame_render_thread_count() == 16);
    }
    {
        const ScopedThreadCount nonsense("banana");  // unparseable: fall back to the default
        CHECK(render::frame_render_thread_count() >= 1);
        CHECK(render::frame_render_thread_count() <= 16);
    }
    {
        const ScopedThreadCount unset(nullptr);
        const int automatic = render::frame_render_thread_count();
        CHECK(automatic >= 1);
        CHECK(automatic <= 16);
    }
}

TEST_CASE("a preview renders identically on one worker and on four", "[tools][render][threads]") {
    auto render_preview = [](const std::string& leaf, const char* threads) {
        const ScopedThreadCount scoped(threads);
        const std::filesystem::path dir = output_dir(leaf);
        Session session = make_session(dir);
        const nlohmann::json result = call(session, "render_preview",
                                           {{"fps", 20.0},
                                            {"start", 0.0},
                                            {"end", 0.35},
                                            {"width", 96},
                                            {"height", 96},
                                            {"contact_sheet", true}});
        return result;
    };

    const nlohmann::json single = render_preview("preview_t1", "1");
    const nlohmann::json many = render_preview("preview_t4", "4");

    REQUIRE(single["frames"].size() == 8);
    REQUIRE(many["frames"].size() == 8);
    CHECK(single["statistics"]["threads_used"] == 1);
    CHECK(many["statistics"]["threads_used"] == 4);
    CHECK(single["statistics"]["render"]["threads_used"] == 1);
    CHECK(many["statistics"]["render"]["threads_used"] == 4);
    CHECK(single["statistics"]["render"]["frames"] == 8);

    // Every frame file is byte-identical, and so is the contact sheet packed from the images.
    for (size_t i = 0; i < single["frames"].size(); ++i) {
        const std::filesystem::path a = single["frames"][i].get<std::string>();
        const std::filesystem::path b = many["frames"][i].get<std::string>();
        INFO("frame " << i << ": " << a << " vs " << b);
        CHECK(a.filename() == b.filename());
        CHECK(file_hash(a) == file_hash(b));
    }
    CHECK(file_hash(single["contact_sheet"].get<std::string>()) ==
          file_hash(many["contact_sheet"].get<std::string>()));

    // The work done is identical too; only the wall-clock timings are allowed to differ.
    CHECK(single["statistics"]["render"]["particles_drawn"] == many["statistics"]["render"]["particles_drawn"]);
    CHECK(single["statistics"]["render"]["fragments_shaded"] == many["statistics"]["render"]["fragments_shaded"]);
    CHECK(single["statistics"]["render"]["overdraw"].get<double>() ==
          Approx(many["statistics"]["render"]["overdraw"].get<double>()));
    CHECK(single["statistics"]["render"]["particles_drawn"].get<size_t>() > 0);
    // The simulation is untouched by the worker count (its json carries wall-clock step timings,
    // so compare the deterministic fields).
    for (const char* key : {"frame", "time", "total_spawned", "total_alive", "events_fired", "systems"}) {
        INFO("simulation." << key);
        CHECK(single["statistics"]["simulation"][key] == many["statistics"]["simulation"][key]);
    }
}

TEST_CASE("a turntable renders identically on one worker and on four", "[tools][render][threads]") {
    auto render_turntable = [](const std::string& leaf, const char* threads) {
        const ScopedThreadCount scoped(threads);
        Session session = make_session(output_dir(leaf));
        return call(session, "render_turntable",
                    {{"time", 0.3}, {"frames", 6}, {"width", 80}, {"height_px", 80}});
    };

    const nlohmann::json single = render_turntable("turntable_t1", "1");
    const nlohmann::json many = render_turntable("turntable_t4", "4");
    REQUIRE(single["frames"].size() == 6);
    CHECK(single["render_statistics"]["threads_used"] == 1);
    CHECK(many["render_statistics"]["threads_used"] == 4);
    for (size_t i = 0; i < single["frames"].size(); ++i) {
        CHECK(file_hash(single["frames"][i].get<std::string>()) ==
              file_hash(many["frames"][i].get<std::string>()));
    }
}

TEST_CASE("exported frames and flipbooks are thread-count independent", "[tools][render][threads]") {
    auto export_effect = [](const std::string& leaf, const std::string& format, const char* threads) {
        const ScopedThreadCount scoped(threads);
        const std::filesystem::path dir = output_dir(leaf);
        Session session = make_session(dir);
        const std::string target = format == "flipbook" ? "sheet.png" : "frames";
        return call(session, "export_effect",
                    {{"format", format},
                     {"path", target},
                     {"options", {{"fps", 20.0}, {"end", 0.3}, {"width", 64}, {"height", 64}, {"columns", 4}}}});
    };

    const nlohmann::json frames_one = export_effect("export_frames_t1", "frames", "1");
    const nlohmann::json frames_four = export_effect("export_frames_t4", "frames", "4");
    REQUIRE(frames_one["files"].size() == frames_four["files"].size());
    REQUIRE(frames_one["files"].size() > 1);
    for (size_t i = 0; i < frames_one["files"].size(); ++i) {
        CHECK(file_hash(frames_one["files"][i].get<std::string>()) ==
              file_hash(frames_four["files"][i].get<std::string>()));
    }

    const nlohmann::json book_one = export_effect("export_book_t1", "flipbook", "1");
    const nlohmann::json book_four = export_effect("export_book_t4", "flipbook", "4");
    CHECK(book_one["manifest"]["frames"] == book_four["manifest"]["frames"]);
    CHECK(file_hash(book_one["path"].get<std::string>()) == file_hash(book_four["path"].get<std::string>()));
}

TEST_CASE("the frame pool returns frames in submission order", "[render][threads]") {
    ResourceSet resources;
    MaterialDesc material;
    material.id = "add";
    material.blend = BlendMode::Additive;
    material.soft_particle = false;
    resources.materials["add"] = material;

    RenderSettings settings;
    settings.width = 48;
    settings.height = 48;
    settings.background = Color{0.0f, 0.0f, 0.0f, 0.0f};
    settings.ground_plane = false;
    settings.grid = false;
    settings.bloom = false;

    CameraDesc camera;
    camera.position = Vec3{0.0f, 0.0f, 5.0f};
    camera.target = Vec3{};

    // 40 frames, each with the particle at a different height: the result order must match the
    // submission order even though four workers raced to produce them.
    auto frame_state = [&](int i) {
        FrameState fs;
        ParticleBuffer pb;
        pb.material_id = "add";
        pb.blend = BlendMode::Additive;
        pb.soft_particle_distance = 0.0f;
        pb.position.push_back(Vec3{0.0f, -1.5f + 3.0f * static_cast<float>(i) / 39.0f, 0.0f});
        pb.previous_position.push_back(pb.position.back());
        pb.velocity.push_back(Vec3{});
        pb.acceleration.push_back(Vec3{});
        pb.age.push_back(0.0f);
        pb.lifetime.push_back(1.0f);
        pb.size.push_back(0.5f);
        pb.rotation.push_back(0.0f);
        pb.angular_velocity.push_back(0.0f);
        pb.color.push_back(Color::white());
        pb.opacity.push_back(1.0f);
        pb.emissive.push_back(0.0f);
        pb.mass.push_back(1.0f);
        pb.custom0.push_back(0.0f);
        pb.custom1.push_back(0.0f);
        pb.seed.push_back(static_cast<uint32_t>(i));
        fs.particles.push_back(pb);
        fs.frame_index = static_cast<uint64_t>(i);
        return fs;
    };

    std::vector<uint64_t> expected;
    {
        auto renderer = render::create_software_renderer();
        for (int i = 0; i < 40; ++i) expected.push_back(renderer->render(frame_state(i), resources, camera, settings).hash());
    }

    render::FrameRenderPool pool(resources, settings, 4);
    CHECK(pool.threads() == 4);
    for (int i = 0; i < 40; ++i) pool.submit(frame_state(i), camera, {});
    const std::vector<render::FrameRenderResult> results = pool.finish();
    REQUIRE(results.size() == 40);
    for (size_t i = 0; i < results.size(); ++i) {
        INFO("frame " << i);
        CHECK(results[i].image.hash() == expected[i]);
        CHECK(results[i].path.empty());
        CHECK(results[i].statistics.particles_drawn == 1);
    }
    CHECK(pool.finish().empty());  // finishing twice is harmless
}

TEST_CASE("a failing frame write propagates as an aether::Error", "[render][threads]") {
    ResourceSet resources;
    RenderSettings settings;
    settings.width = 16;
    settings.height = 16;
    CameraDesc camera;

    render::FrameRenderPool pool(resources, settings, 2);
    // A path whose parent is a regular file cannot be created, so the worker throws.
    const std::filesystem::path dir = output_dir("pool_failure");
    const std::filesystem::path blocker = dir / "not_a_directory";
    { std::ofstream out(blocker); out << "x"; }

    bool threw = false;
    try {
        for (int i = 0; i < 8; ++i) pool.submit(FrameState{}, camera, blocker / "frame.png");
        pool.finish();
    } catch (const Error&) {
        threw = true;
    }
    CHECK(threw);
}
