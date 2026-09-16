// Analytic nodes (docs/RUNTIME.md section 7), the three reference documents
// simulated to their full duration, statistics (section 9) and a timing smoke
// test.
#include <chrono>
#include <cmath>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/sim/runtime.hpp"

using namespace aether;
using Catch::Approx;

namespace {

Effect load_example(const std::string& name) {
    return load_effect_file(std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name);
}

std::unique_ptr<sim::IRuntime> runtime_for(const Effect& effect) {
    const compiler::CompiledEffect compiled = compiler::compile(effect);
    INFO(compiled.diagnostics.summary());
    REQUIRE(compiled.diagnostics.error_count() == 0);
    return sim::create_cpu_runtime(compiled);
}

Node node_of(NodeType type, const std::string& id) {
    Node n;
    n.id = id;
    n.type = type;
    return n;
}

const LightState* light_of(const FrameState& s, const std::string& id) {
    for (const LightState& l : s.lights)
        if (l.id == id) return &l;
    return nullptr;
}
const DecalState* decal_of(const FrameState& s, const std::string& id) {
    for (const DecalState& d : s.decals)
        if (d.id == id) return &d;
    return nullptr;
}
const MeshInstanceState* mesh_of(const FrameState& s, const std::string& id) {
    for (const MeshInstanceState& m : s.meshes)
        if (m.id == id) return &m;
    return nullptr;
}
const BeamState* beam_of(const FrameState& s, const std::string& id) {
    for (const BeamState& b : s.beams)
        if (b.id == id) return &b;
    return nullptr;
}
const TrailState* trail_of(const FrameState& s, const std::string& id) {
    for (const TrailState& t : s.trails)
        if (t.id == id) return &t;
    return nullptr;
}

}  // namespace

// ---------------------------------------------------------------------------
// lights
// ---------------------------------------------------------------------------

TEST_CASE("the fireball glow light flickers within its amplitude", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fireball.json"));
    bool varied = false;
    float previous = -1.0f;
    for (int i = 0; i < 90; ++i) {
        rt->step();
        const LightState* glow = light_of(rt->state(), "glow");
        REQUIRE(glow != nullptr);
        CHECK(glow->intensity >= 12.0f * (1.0f - 0.15f) - 1e-3f);
        CHECK(glow->intensity <= 12.0f * (1.0f + 0.15f) + 1e-3f);
        CHECK(glow->radius == Approx(4.0f));
        CHECK(glow->type == LightType::Point);
        if (previous >= 0.0f && std::fabs(glow->intensity - previous) > 1e-4f) varied = true;
        previous = glow->intensity;
    }
    CHECK(varied);
    // the light is parented to the core, so it travels with it
    const LightState* glow = light_of(rt->state(), "glow");
    CHECK(glow->position.z > -3.0f);
    CHECK(glow->position.y == Approx(1.0f));
}

TEST_CASE("a light with zero flicker amplitude is steady, and temperature tints it", "[sim][analytic]") {
    Effect e;
    e.duration = 1.0;
    Node light = node_of(NodeType::Light, "lamp");
    light.parameters["intensity"] = Parameter{25.0f};
    light.parameters["flicker_amplitude"] = Parameter{0.0f};
    light.parameters["temperature"] = Parameter{2000.0f};
    light.parameters["color"] = Parameter{Color{1, 1, 1, 1}};
    light.parameters["position"] = Parameter{Vec3{1, 2, 3}};
    e.add_node(light);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    for (int i = 0; i < 10; ++i) {
        rt->step();
        const LightState* l = light_of(rt->state(), "lamp");
        REQUIRE(l != nullptr);
        CHECK(l->intensity == Approx(25.0f));
        CHECK(l->position.x == Approx(1.0f));
        CHECK(l->color.r > l->color.b);  // warm
    }
}

TEST_CASE("a light outside its window is not emitted", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    rt->simulate_to(0.5);
    CHECK(light_of(rt->state(), "flash") == nullptr);  // phase activation = [0.8, 1.0]
    rt->simulate_to(0.9);
    CHECK(light_of(rt->state(), "flash") != nullptr);
    rt->simulate_to(1.2);
    CHECK(light_of(rt->state(), "flash") == nullptr);
    CHECK(light_of(rt->state(), "fire_light") != nullptr);  // start_time 0.9, unbounded
}

// ---------------------------------------------------------------------------
// beams
// ---------------------------------------------------------------------------

TEST_CASE("the lightning bolt is a jittered polyline with branches", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("lightning_strike.json"));
    rt->step();
    const BeamState* bolt = beam_of(rt->state(), "main_bolt");
    REQUIRE(bolt != nullptr);
    REQUIRE(!bolt->polylines.empty());
    CHECK(bolt->polylines.front().size() == 25u);  // segments + 1
    CHECK(bolt->polylines.size() <= 6u);           // main + at most `branching`
    CHECK(bolt->width == Approx(0.06f));
    CHECK(bolt->pulse_phase < 0.0f);  // pulse_speed = 0
    // the endpoints are exact, the interior is displaced
    CHECK(distance(bolt->polylines.front().front(), Vec3{0.3f, 6.0f, -0.2f}) < 1e-5f);
    CHECK(distance(bolt->polylines.front().back(), Vec3{0, 0, 0}) < 1e-5f);
    bool displaced = false;
    for (size_t i = 1; i + 1 < bolt->polylines.front().size(); ++i) {
        const Vec3 straight = lerp(Vec3{0.3f, 6.0f, -0.2f}, Vec3{0, 0, 0},
                                   static_cast<float>(i) / 24.0f);
        if (distance(bolt->polylines.front()[i], straight) > 1e-4f) displaced = true;
    }
    CHECK(displaced);
    for (size_t i = 1; i < bolt->polylines.size(); ++i) CHECK(bolt->polylines[i].size() == 5u);  // 4 segments
}

TEST_CASE("beam jitter is a pure function of the jitter key", "[sim][analytic]") {
    Effect effect = load_example("lightning_strike.json");
    effect.find_node("main_bolt")->parameters["jitter_rate"] = Parameter{10.0f};  // one key per 0.1 s
    std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);

    rt->step();  // t = 1/60 -> key 0
    const std::vector<Vec3> first = beam_of(rt->state(), "main_bolt")->polylines.front();
    rt->step();  // t = 2/60 -> key 0
    const std::vector<Vec3> same_key = beam_of(rt->state(), "main_bolt")->polylines.front();
    CHECK(same_key == first);

    rt->simulate_to(0.12);  // key 1
    const std::vector<Vec3> next_key = beam_of(rt->state(), "main_bolt")->polylines.front();
    CHECK(next_key.size() == first.size());
    CHECK(next_key != first);
}

TEST_CASE("beam pulses expose a phase", "[sim][analytic]") {
    Effect e;
    e.duration = 2.0;
    Node beam = node_of(NodeType::Beam, "laser");
    beam.parameters["origin"] = Parameter{Vec3{0, 0, 0}};
    beam.parameters["target"] = Parameter{Vec3{0, 4, 0}};
    beam.parameters["segments"] = Parameter{8};
    beam.parameters["noise_amplitude"] = Parameter{0.0f};
    beam.parameters["jitter_rate"] = Parameter{0.0f};
    beam.parameters["pulse_speed"] = Parameter{2.0f};
    beam.parameters["pulse_frequency"] = Parameter{4.0f};
    beam.parameters["emissive"] = Parameter{4.0f};
    e.add_node(beam);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.25);
    const BeamState* b = beam_of(rt->state(), "laser");
    REQUIRE(b != nullptr);
    CHECK(b->pulse_phase >= 0.0f);
    CHECK(b->pulse_phase < 1.0f);
    CHECK(b->pulse_phase == Approx(0.5f).margin(0.02));
    CHECK(b->emissive <= 4.0f * 1.0f + 1e-4f);
    CHECK(b->emissive >= 4.0f * 0.5f - 1e-4f);
    // no noise and no jitter: the polyline is exactly the straight line
    for (size_t i = 0; i < b->polylines.front().size(); ++i)
        CHECK(b->polylines.front()[i].y == Approx(4.0f * static_cast<float>(i) / 8.0f));
}

TEST_CASE("beam endpoints follow origin_node and target_node", "[sim][analytic]") {
    Effect e;
    e.duration = 1.0;
    Node origin = node_of(NodeType::Mesh, "from");
    origin.parameters["position"] = Parameter{Vec3{-2, 1, 0}};
    origin.parameters["visible"] = Parameter{false};
    Node target = node_of(NodeType::Mesh, "to");
    target.parameters["position"] = Parameter{Vec3{3, 0.5f, 1}};
    target.parameters["visible"] = Parameter{false};
    Node beam = node_of(NodeType::Beam, "link");
    beam.parameters["segments"] = Parameter{4};
    beam.parameters["noise_amplitude"] = Parameter{0.0f};
    beam.inputs["origin_node"] = {NodeRef::parse("from")};
    beam.inputs["target_node"] = {NodeRef::parse("to")};
    e.add_node(origin);
    e.add_node(target);
    e.add_node(beam);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const BeamState* b = beam_of(rt->state(), "link");
    REQUIRE(b != nullptr);
    CHECK(distance(b->polylines.front().front(), Vec3{-2, 1, 0}) < 1e-5f);
    CHECK(distance(b->polylines.front().back(), Vec3{3, 0.5f, 1}) < 1e-5f);
    CHECK(mesh_of(rt->state(), "from") == nullptr);  // visible = false
}

// ---------------------------------------------------------------------------
// decals, meshes, camera, post effects, volumes
// ---------------------------------------------------------------------------

TEST_CASE("the fire_aoe rune decal follows its opacity track and fade-in", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    rt->simulate_to(0.15);
    const DecalState* rune = decal_of(rt->state(), "rune");
    REQUIRE(rune != nullptr);
    // track(0.15) = 0.3, fade_in = smoothstep(0, 0.3, 0.15) = 0.5
    CHECK(rune->opacity == Approx(0.15f).margin(0.01));
    CHECK(rune->size.x == Approx(6.0f));
    CHECK(rune->circle);
    CHECK(rune->blend == BlendMode::Additive);
    CHECK(rune->texture_id == "tex_rune");
    CHECK(rune->normal.y == Approx(1.0f));

    rt->simulate_to(0.6);
    CHECK(decal_of(rt->state(), "rune")->opacity == Approx(1.0f).margin(0.02));  // track 1.0, fade complete
    rt->simulate_to(1.3);
    CHECK(decal_of(rt->state(), "rune")->opacity == Approx(0.2f).margin(0.02));

    // the scorch decal only appears from its start_time
    rt->reset();
    rt->simulate_to(0.5);
    CHECK(decal_of(rt->state(), "scorch") == nullptr);
    rt->simulate_to(1.3);
    const DecalState* scorch = decal_of(rt->state(), "scorch");
    REQUIRE(scorch != nullptr);
    CHECK(scorch->opacity > 0.5f);
    CHECK(scorch->blend == BlendMode::Alpha);
}

TEST_CASE("a bounded decal fades out at the end of its window", "[sim][analytic]") {
    Effect e;
    e.duration = 3.0;
    Node decal = node_of(NodeType::Decal, "mark");
    decal.parameters["opacity"] = Parameter{1.0f};
    decal.parameters["fade_in"] = Parameter{0.0f};
    decal.parameters["fade_out"] = Parameter{1.0f};
    decal.parameters["start_time"] = Parameter{0.0f};
    decal.parameters["duration"] = Parameter{2.0f};
    e.add_node(decal);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    CHECK(decal_of(rt->state(), "mark")->opacity == Approx(1.0f));
    rt->simulate_to(1.5);
    CHECK(decal_of(rt->state(), "mark")->opacity == Approx(0.5f).margin(0.05));
    rt->simulate_to(2.5);
    CHECK(decal_of(rt->state(), "mark") == nullptr);
}

TEST_CASE("the fireball core mesh instance follows its keyframe track", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fireball.json"));
    rt->simulate_to(1.25);  // half way through the 2.5 s track
    const MeshInstanceState* core = mesh_of(rt->state(), "core");
    REQUIRE(core != nullptr);
    CHECK(core->mesh_id == "core");
    CHECK(core->material_id == "mat_core");
    const Vec3 position = core->transform.translation_part();
    CHECK(position.x == Approx(0.0f).margin(1e-5));
    CHECK(position.y == Approx(1.0f).margin(1e-5));
    CHECK(position.z == Approx(-0.5f).margin(0.02));  // lerp(-3, 2, 0.5)
    CHECK(core->emissive == Approx(6.0f));
    CHECK(rt->compiled().resources.mesh("core") != nullptr);

    rt->simulate_to(2.4);
    CHECK(mesh_of(rt->state(), "core")->transform.translation_part().z > position.z);
}

TEST_CASE("the camera comes from the first camera node", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    REQUIRE(rt->state().camera.has_value());
    CHECK(rt->state().camera->position.y == Approx(3.2f));
    CHECK(rt->state().camera->fov_deg == Approx(50.0f));
    rt->simulate_to(2.0);
    REQUIRE(rt->state().camera.has_value());
    CHECK(rt->state().camera->target.y == Approx(0.8f));
}

TEST_CASE("post effects appear inside their window", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    rt->simulate_to(0.5);
    CHECK(rt->state().post_effects.empty());  // haze starts at 0.9
    rt->simulate_to(1.5);
    REQUIRE(rt->state().post_effects.size() == 1u);
    CHECK(rt->state().post_effects[0].id == "haze");
    CHECK(rt->state().post_effects[0].post_type == "heat_haze");
    CHECK(rt->state().post_effects[0].intensity == Approx(0.5f));
    CHECK(rt->state().post_effects[0].time == Approx(1.5).margin(0.02));
    rt->simulate_to(4.0);
    CHECK(rt->state().post_effects.empty());  // duration 3.0 -> ends at 3.9
}

TEST_CASE("volume nodes report a stub state", "[sim][analytic]") {
    Effect e;
    e.duration = 1.0;
    Node volume = node_of(NodeType::Volume, "smoke");
    volume.parameters["volume_type"] = Parameter{std::string("smoke")};
    volume.parameters["bounds"] = Parameter{Vec3{4, 2, 4}};
    volume.parameters["density"] = Parameter{0.7f};
    volume.parameters["temperature"] = Parameter{300.0f};
    volume.parameters["position"] = Parameter{Vec3{0, 1, 0}};
    e.add_node(volume);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    REQUIRE(rt->state().volumes.size() == 1u);
    const VolumeState& v = rt->state().volumes[0];
    CHECK(v.id == "smoke");
    CHECK(v.volume_type == "smoke");
    CHECK(v.backend == "volume_stub");
    CHECK(v.density == Approx(0.7f));
    CHECK(v.temperature == Approx(300.0f));
    CHECK(v.bounds_min.y == Approx(0.0f));
    CHECK(v.bounds_max.y == Approx(2.0f));
}

// ---------------------------------------------------------------------------
// trails
// ---------------------------------------------------------------------------

TEST_CASE("the fireball trail grows and follows the core", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fireball.json"));
    rt->step();
    const TrailState* trail = trail_of(rt->state(), "trail");
    REQUIRE(trail != nullptr);
    REQUIRE(trail->ribbons.size() == 1u);
    const size_t after_one = trail->ribbons[0].size();
    CHECK(after_one >= 1u);

    rt->simulate_to(0.3);
    const TrailState* later = trail_of(rt->state(), "trail");
    REQUIRE(later->ribbons.size() == 1u);
    const std::vector<TrailVertex>& ribbon = later->ribbons[0];
    CHECK(ribbon.size() > after_one);
    CHECK(ribbon.size() <= 65u);  // max_segments + 1
    // the newest vertex tracks the core (displaced by the trail noise only)
    const MeshInstanceState* core = mesh_of(rt->state(), "core");
    REQUIRE(core != nullptr);
    CHECK(distance(ribbon.back().position, core->transform.translation_part()) < 0.12f);
    // the oldest vertex is at the front and is the widest (taper 1 -> 0)
    CHECK(ribbon.front().normalized_age > ribbon.back().normalized_age);
    CHECK(ribbon.front().width <= ribbon.back().width);
    CHECK(ribbon.back().width == Approx(0.3f).margin(1e-3));
    CHECK(ribbon.back().color.r == Approx(1.0f));
    CHECK(ribbon.back().emissive == Approx(2.0f));
    for (const TrailVertex& v : ribbon) CHECK(v.age <= 0.45f + 1e-3f);  // lifetime

    // the ribbon length is bounded by the trail lifetime
    rt->simulate_to(2.0);
    CHECK(trail_of(rt->state(), "trail")->ribbons[0].size() <= 65u);
}

TEST_CASE("a trail on a particle system emits one ribbon per particle", "[sim][analytic]") {
    Effect e;
    e.duration = 2.0;
    e.seed = 5;
    Node ps = node_of(NodeType::ParticleSystem, "ps");
    ps.parameters["max_particles"] = Parameter{64};
    ps.parameters["lifetime"] = Parameter{5.0f};
    e.add_node(ps);
    Node em = node_of(NodeType::Emitter, "em");
    em.parameters["shape"] = Parameter{std::string("sphere")};
    em.parameters["rate"] = Parameter{0.0f};
    em.parameters["burst_count"] = Parameter{8};
    em.parameters["burst_times"] = Parameter{std::vector<float>{0.0f}};
    em.parameters["velocity"] = Parameter{2.0f};
    em.parameters["direction"] = Parameter{Vec3{0, 0, 0}};
    em.inputs["particle"] = {NodeRef::parse("ps")};
    e.add_node(em);
    Node trail = node_of(NodeType::Trail, "streak");
    trail.parameters["min_vertex_distance"] = Parameter{0.01f};
    trail.parameters["lifetime"] = Parameter{1.0f};
    trail.inputs["source"] = {NodeRef::parse("ps")};
    e.add_node(trail);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    const TrailState* streak = trail_of(rt->state(), "streak");
    REQUIRE(streak != nullptr);
    CHECK(streak->ribbons.size() == 8u);
    for (const std::vector<TrailVertex>& ribbon : streak->ribbons) CHECK(ribbon.size() > 2u);
    // each ribbon ends at its particle
    const ParticleBuffer* b = rt->state().find_particles("ps");
    REQUIRE(b != nullptr);
    for (size_t i = 0; i < streak->ribbons.size(); ++i)
        CHECK(distance(streak->ribbons[i].back().position, b->position[i]) < 1e-4f);
}

// ---------------------------------------------------------------------------
// the three reference documents
// ---------------------------------------------------------------------------

TEST_CASE("the examples simulate to their full duration", "[sim][examples]") {
    for (const std::string& name : {"fireball.json", "fire_aoe.json", "lightning_strike.json"}) {
        const Effect effect = load_example(name);
        std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);
        REQUIRE_NOTHROW(rt->simulate_to(effect.duration));
        CHECK(rt->time() >= effect.duration - 1e-9);

        const sim::Statistics stats = rt->statistics();
        INFO(name << " -> " << stats.to_json().dump());
        CHECK(stats.frame == rt->frame_index());
        CHECK(!stats.systems.empty());
        size_t alive = 0;
        for (const sim::SystemStatistics& s : stats.systems) {
            INFO(name << " system " << s.system_id);
            CHECK(s.alive <= s.capacity);
            CHECK(s.spawned_total >= s.alive);
            CHECK(s.spawned_total == s.alive + s.died_total + 0u);
            CHECK(s.peak_alive >= s.alive);
            CHECK(s.peak_alive <= s.capacity);
            alive += s.alive;
        }
        CHECK(stats.total_alive == alive);
        CHECK(stats.total_alive == rt->state().total_particles());
        CHECK(stats.total_spawned > 0u);
        CHECK(stats.total_step_ms >= 0.0);
    }
}

TEST_CASE("fireball populations are in the expected range", "[sim][examples]") {
    const Effect effect = load_example("fireball.json");
    std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);
    rt->simulate_to(1.0);
    const sim::Statistics stats = rt->statistics();
    for (const sim::SystemStatistics& s : stats.systems) {
        if (s.system_id != "flame_ps") continue;
        // rate 180 * lifetime 0.35 = 63 particles in steady state
        CHECK(s.alive > static_cast<size_t>(63 * 0.6));
        CHECK(s.alive < static_cast<size_t>(63 * 1.4));
    }
    // sparks bounce off the ground plane
    rt->simulate_to(2.5);
    for (const sim::SystemStatistics& s : rt->statistics().systems)
        if (s.system_id == "spark_ps") CHECK(s.collisions > 0u);
}

TEST_CASE("fire_aoe phases populate their systems in order", "[sim][examples]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    const auto spawned = [&rt](const std::string& id) {
        for (const sim::SystemStatistics& s : rt->statistics().systems)
            if (s.system_id == id) return s.spawned_total;
        return static_cast<size_t>(0);
    };

    rt->simulate_to(0.7);  // anticipation only
    CHECK(spawned("gather_ps") > 0u);
    CHECK(spawned("burst_ps") == 0u);
    CHECK(spawned("wall_ps") == 0u);
    CHECK(spawned("ember_ps") == 0u);

    rt->simulate_to(0.95);  // activation
    CHECK(spawned("burst_ps") > 0u);
    CHECK(spawned("rock_ps") > 0u);

    rt->simulate_to(1.8);  // peak / sustain
    CHECK(spawned("wall_ps") > 0u);
    CHECK(spawned("smoke_ps") > 0u);

    rt->simulate_to(3.4);  // decay
    CHECK(spawned("ember_ps") > 0u);
    CHECK(rt->state().total_particles() > 0u);
}

TEST_CASE("fire_aoe simulates its full duration quickly enough", "[sim][performance]") {
    const Effect effect = load_example("fire_aoe.json");
    std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);
    const auto start = std::chrono::steady_clock::now();
    rt->simulate_to(effect.duration);
    const double elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();

    const sim::Statistics stats = rt->statistics();
    size_t peak = 0;
    for (const sim::SystemStatistics& s : stats.systems) peak += s.peak_alive;
    std::cout << "  [perf] fire_aoe " << effect.duration << " s at " << 1.0 / rt->fixed_dt() << " Hz: " << elapsed_ms
              << " ms (" << rt->frame_index() << " steps, " << stats.total_spawned << " particles spawned, peak "
              << peak << " alive, " << stats.total_step_ms / static_cast<double>(rt->frame_index())
              << " ms/step)\n";
    CHECK(elapsed_ms < 20000.0);
    CHECK(peak > 1000u);
}

TEST_CASE("fireball 60 steps timing", "[sim][performance]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fireball.json"));
    const auto start = std::chrono::steady_clock::now();
    for (int i = 0; i < 60; ++i) rt->step();
    const double elapsed_ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - start).count();
    std::cout << "  [perf] fireball 60 steps: " << elapsed_ms << " ms (" << elapsed_ms / 60.0 << " ms/step, "
              << rt->state().total_particles() << " alive)\n";
    CHECK(rt->statistics().last_step_ms >= 0.0);
    CHECK(rt->statistics().total_step_ms > 0.0);
    CHECK(elapsed_ms < 5000.0);
}
