// Analytic nodes (docs/RUNTIME.md section 7), the three reference documents
// simulated to their full duration, statistics (section 9) and a timing smoke
// test.
#include <algorithm>
#include <chrono>
#include <cmath>
#include <optional>
#include <filesystem>
#include <iostream>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/rng.hpp"
#include "aether/core/spec.hpp"
#include "aether/procedural/noise.hpp"
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
    const Node* glow_node = rt->compiled().effect.find_node("glow");
    REQUIRE(glow_node != nullptr);
    const float base_intensity = param_float(*glow_node, "intensity");
    const float base_radius = param_float(*glow_node, "radius");
    const float amplitude = param_float(*glow_node, "flicker_amplitude");
    REQUIRE(amplitude > 0.0f);
    bool varied = false;
    float previous = -1.0f;
    for (int i = 0; i < 90; ++i) {
        rt->step();
        const LightState* glow = light_of(rt->state(), "glow");
        REQUIRE(glow != nullptr);
        CHECK(glow->intensity >= base_intensity * (1.0f - amplitude) - 1e-3f);
        CHECK(glow->intensity <= base_intensity * (1.0f + amplitude) + 1e-3f);
        CHECK(glow->radius == Approx(base_radius));
        CHECK(glow->type == LightType::Point);
        if (previous >= 0.0f && std::fabs(glow->intensity - previous) > 1e-4f) varied = true;
        previous = glow->intensity;
    }
    CHECK(varied);
    // the light is parented to the core, so it travels with it
    const LightState* glow = light_of(rt->state(), "glow");
    const Vec3 start = value_as_vec3(rt->compiled().effect.find_node("core")->find_param("position")->track.front().value);
    CHECK(glow->position.z > start.z);
    CHECK(glow->position.y == Approx(start.y));
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
    rt->simulate_to(0.25);
    CHECK(light_of(rt->state(), "flash") == nullptr);  // phase activation = [0.4, 0.8]
    rt->simulate_to(0.6);
    CHECK(light_of(rt->state(), "flash") != nullptr);
    rt->simulate_to(0.95);
    CHECK(light_of(rt->state(), "flash") == nullptr);
    CHECK(light_of(rt->state(), "fire_light") != nullptr);  // start_time 0.3, unbounded
}

// ---------------------------------------------------------------------------
// beams
// ---------------------------------------------------------------------------

TEST_CASE("the lightning bolt is a jittered polyline with branches", "[sim][analytic]") {
    // Structure, not art direction: every number below is read back out of the
    // effect so retuning the look never breaks the contract this test guards.
    const Effect effect = load_example("lightning_strike.json");
    const Node* node = effect.find_node("main_bolt");
    REQUIRE(node != nullptr);
    const int segments = param_int(*node, "segments");
    const int detail = param_int(*node, "detail");
    const int branching = param_int(*node, "branching");
    const int branch_depth = param_int(*node, "branch_depth");
    const Vec3 origin = param_vec3(*node, "origin");
    const Vec3 target = param_vec3(*node, "target");
    const float width = param_float(*node, "width", 1.0 / 60.0);  // the width is keyframed
    const size_t expected = static_cast<size_t>(segments) * (1u << detail) + 1u;

    std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);
    rt->step();
    const BeamState* bolt = beam_of(rt->state(), "main_bolt");
    REQUIRE(bolt != nullptr);
    REQUIRE(!bolt->paths.empty());
    const BeamPath& main = bolt->paths.front();
    CHECK(main.points.size() == expected);
    CHECK(main.width.size() == main.points.size());
    CHECK(main.intensity.size() == main.points.size());
    CHECK(main.depth == 0);
    CHECK(bolt->width == Approx(width));
    CHECK(bolt->pulse_phase < 0.0f);  // pulse_speed = 0
    // the endpoints are exact, the interior is displaced
    CHECK(distance(main.points.front(), origin) < 1e-5f);
    CHECK(distance(main.points.back(), target) < 1e-5f);
    bool displaced = false;
    for (size_t i = 1; i + 1 < main.points.size(); ++i) {
        const Vec3 straight =
            lerp(origin, target, static_cast<float>(i) / static_cast<float>(main.points.size() - 1));
        if (distance(main.points[i], straight) > 1e-4f) displaced = true;
    }
    CHECK(displaced);
    // Branches: at most `branching` per parent per generation, each one a shorter
    // path of its own, tagged with the generation it belongs to.
    size_t per_depth[4] = {1, 0, 0, 0};
    for (size_t i = 1; i < bolt->paths.size(); ++i) {
        const BeamPath& branch = bolt->paths[i];
        REQUIRE(branch.depth >= 1);
        REQUIRE(branch.depth <= branch_depth);
        CHECK(branch.points.size() == static_cast<size_t>(4 * (1 << detail) + 1));
        CHECK(branch.width.size() == branch.points.size());
        ++per_depth[branch.depth];
    }
    for (int d = 1; d <= branch_depth; ++d)
        CHECK(per_depth[d] <= per_depth[d - 1] * static_cast<size_t>(branching));
}

TEST_CASE("beam jitter is a pure function of the jitter key", "[sim][analytic]") {
    Effect effect = load_example("lightning_strike.json");
    effect.find_node("main_bolt")->parameters["jitter_rate"] = Parameter{10.0f};  // one key per 0.1 s
    std::unique_ptr<sim::IRuntime> rt = runtime_for(effect);

    rt->step();  // t = 1/60 -> key 0
    const std::vector<Vec3> first = beam_of(rt->state(), "main_bolt")->paths.front().points;
    rt->step();  // t = 2/60 -> key 0
    const std::vector<Vec3> same_key = beam_of(rt->state(), "main_bolt")->paths.front().points;
    CHECK(same_key == first);

    rt->simulate_to(0.12);  // key 1
    const std::vector<Vec3> next_key = beam_of(rt->state(), "main_bolt")->paths.front().points;
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
    for (size_t i = 0; i < b->paths.front().points.size(); ++i)
        CHECK(b->paths.front().points[i].y == Approx(4.0f * static_cast<float>(i) / 8.0f));
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
    CHECK(distance(b->paths.front().points.front(), Vec3{-2, 1, 0}) < 1e-5f);
    CHECK(distance(b->paths.front().points.back(), Vec3{3, 0.5f, 1}) < 1e-5f);
    CHECK(mesh_of(rt->state(), "from") == nullptr);  // visible = false
}

// ---------------------------------------------------------------------------
// beams: the strike features (detail, width profile, branch generations,
// per-vertex intensity, flicker, afterglow, impact flare)
// ---------------------------------------------------------------------------

namespace {

// A bolt with everything at its default except the geometry, so the strike
// parameters can be switched on one at a time against a known baseline.
Effect bolt_effect() {
    Effect e;
    e.seed = 1234;
    e.duration = 2.0;
    Node beam = node_of(NodeType::Beam, "bolt");
    beam.parameters["origin"] = Parameter{Vec3{0.3f, 6.0f, -0.2f}};
    beam.parameters["target"] = Parameter{Vec3{0, 0, 0}};
    beam.parameters["segments"] = Parameter{24};
    beam.parameters["width"] = Parameter{0.06f};
    beam.parameters["noise_amplitude"] = Parameter{0.35f};
    beam.parameters["noise_frequency"] = Parameter{3.0f};
    beam.parameters["jitter_rate"] = Parameter{40.0f};
    e.add_node(beam);
    return e;
}

const BeamState& stepped_bolt(std::unique_ptr<sim::IRuntime>& rt, const Effect& e, double time = -1.0) {
    rt = runtime_for(e);
    if (time < 0.0) rt->step();
    else rt->simulate_to(time);
    const BeamState* b = beam_of(rt->state(), "bolt");
    REQUIRE(b != nullptr);
    return *b;
}

}  // namespace

TEST_CASE("a beam with default parameters produces the V1 vertices and width", "[sim][analytic]") {
    // The guarantee behind every new beam parameter: switching none of them on
    // leaves the geometry exactly where it was. The reference below is the V1
    // displacement formula written out by hand.
    Effect e = bolt_effect();
    e.find_node("bolt")->parameters["branching"] = Parameter{5};
    e.find_node("bolt")->parameters["branch_probability"] = Parameter{0.7f};
    std::unique_ptr<sim::IRuntime> rt;
    const BeamState& bolt = stepped_bolt(rt, e);

    const uint64_t stream = derive_seed(e.seed, "bolt", std::optional<uint32_t>{});
    const uint32_t seed = static_cast<uint32_t>(stream ^ (stream >> 32));
    const procedural::FbmParams fbm{3, 2.0f, 0.5f, procedural::NoiseBasis::Simplex};
    const Vec3 origin{0.3f, 6.0f, -0.2f}, target{0, 0, 0};
    const Vec3 span = target - origin;
    const float span_length = length(span);
    const Vec3 forward = span / span_length;
    const Vec3 u = orthogonal(forward);
    const Vec3 v = cross(forward, u);
    const float key = 0.0f;  // t = 1/60, jitter_rate 40 -> floor(0.666) = 0

    const BeamPath& main = bolt.paths.front();
    REQUIRE(main.points.size() == 25u);
    for (int i = 0; i <= 24; ++i) {
        const float f = static_cast<float>(i) / 24.0f;
        Vec3 expected = origin + span * f;
        if (i > 0 && i < 24) {
            const float along = f * span_length * 3.0f;
            expected += u * (0.35f * procedural::fbm3(Vec3{along, 0.0f, key}, seed, fbm)) +
                        v * (0.35f * procedural::fbm3(Vec3{along, 17.0f, key}, seed, fbm));
        }
        INFO("vertex " << i);
        CHECK(distance(main.points[static_cast<size_t>(i)], expected) == 0.0f);  // bit for bit
        CHECK(main.width[static_cast<size_t>(i)] == 0.06f);  // width_profile uniform, no variance
        CHECK(main.intensity[static_cast<size_t>(i)] == 1.0f);
    }
    // Branches keep the 0.6x the reference renderer used to apply itself, and
    // stay uniform: `width_profile: uniform` means uniform everywhere.
    REQUIRE(bolt.paths.size() > 1u);
    for (size_t p = 1; p < bolt.paths.size(); ++p) {
        CHECK(bolt.paths[p].depth == 1);
        CHECK(bolt.paths[p].fade == 1.0f);
        for (float w : bolt.paths[p].width) CHECK(w == Approx(0.06f * 0.6f));
    }
    CHECK(bolt.ghosts.empty());   // afterglow 0
    CHECK(bolt.flares.empty());   // impact_flare 0
    CHECK(bolt.core_width == Approx(0.55f));
    CHECK(bolt.glow_width == Approx(2.6f));
}

TEST_CASE("beam detail subdivides the path deterministically", "[sim][analytic]") {
    Effect e = bolt_effect();
    std::unique_ptr<sim::IRuntime> rt;
    const std::vector<Vec3> plain = stepped_bolt(rt, e).paths.front().points;

    for (int detail = 1; detail <= 4; ++detail) {
        e.find_node("bolt")->parameters["detail"] = Parameter{detail};
        std::unique_ptr<sim::IRuntime> a, b;
        const BeamPath& first = stepped_bolt(a, e).paths.front();
        const BeamPath& again = stepped_bolt(b, e).paths.front();
        INFO("detail " << detail);
        CHECK(first.points.size() == static_cast<size_t>(24 * (1 << detail) + 1));
        CHECK(first.points == again.points);  // two fresh runtimes agree bit for bit
        // the endpoints and the coarse vertices survive; only midpoints move
        CHECK(distance(first.points.front(), plain.front()) == 0.0f);
        CHECK(distance(first.points.back(), plain.back()) == 0.0f);
        const size_t stride = static_cast<size_t>(1 << detail);
        for (size_t i = 0; i < plain.size(); ++i)
            CHECK(distance(first.points[i * stride], plain[i]) == 0.0f);
        bool jagged = false;
        for (size_t i = 1; i + 1 < first.points.size(); i += 2) {
            const Vec3 chord = (first.points[i - 1] + first.points[i + 1]) * 0.5f;
            if (distance(first.points[i], chord) > 1e-6f) jagged = true;
        }
        CHECK(jagged);
    }
}

TEST_CASE("beam width profiles shape the bolt and taper its branches", "[sim][analytic]") {
    Effect e = bolt_effect();
    Node* node = e.find_node("bolt");
    node->parameters["branching"] = Parameter{4};
    node->parameters["branch_probability"] = Parameter{1.0f};

    const auto widths = [&](const char* profile) {
        node->parameters["width_profile"] = Parameter{std::string(profile)};
        std::unique_ptr<sim::IRuntime> rt;
        const BeamState& bolt = stepped_bolt(rt, e);
        std::vector<std::vector<float>> out;
        for (const BeamPath& p : bolt.paths) out.push_back(p.width);
        return out;
    };

    const std::vector<float> taper_end = widths("taper_end").front();
    CHECK(taper_end.front() == Approx(0.06f));
    CHECK(taper_end.back() < taper_end.front() * 0.35f);
    for (size_t i = 1; i < taper_end.size(); ++i) CHECK(taper_end[i] <= taper_end[i - 1] + 1e-6f);

    const std::vector<float> taper_both = widths("taper_both").front();
    const size_t middle = taper_both.size() / 2;
    CHECK(taper_both[middle] > taper_both.front() * 2.0f);
    CHECK(taper_both[middle] > taper_both.back() * 2.0f);

    const std::vector<float> bulge = widths("bulge").front();
    const size_t widest = static_cast<size_t>(
        std::max_element(bulge.begin(), bulge.end()) - bulge.begin());
    CHECK(widest < bulge.size() / 3);                // the shoulder sits near the origin
    CHECK(bulge.back() < bulge[widest] * 0.35f);     // and the tail is thin

    // With any profile but `uniform`, branches die out at their tips.
    const std::vector<std::vector<float>> tapered = widths("taper_end");
    REQUIRE(tapered.size() > 1u);
    for (size_t p = 1; p < tapered.size(); ++p) {
        CHECK(tapered[p].front() > 0.0f);
        CHECK(tapered[p].back() == Approx(0.0f).margin(1e-7));
    }
}

TEST_CASE("beam branches recurse to branch_depth with a thinner, dimmer generation", "[sim][analytic]") {
    Effect e = bolt_effect();
    Node* node = e.find_node("bolt");
    node->parameters["branching"] = Parameter{4};
    node->parameters["branch_probability"] = Parameter{1.0f};
    node->parameters["branch_depth"] = Parameter{2};
    node->parameters["branch_width"] = Parameter{0.5f};
    node->parameters["branch_intensity"] = Parameter{0.6f};

    std::unique_ptr<sim::IRuntime> rt;
    const BeamState& bolt = stepped_bolt(rt, e);
    int counts[3] = {0, 0, 0};
    for (const BeamPath& p : bolt.paths) {
        REQUIRE(p.depth >= 0);
        REQUIRE(p.depth <= 2);
        ++counts[p.depth];
        const float expected_width = 0.06f * std::pow(0.5f, static_cast<float>(p.depth));
        CHECK(p.width.front() == Approx(expected_width));
        CHECK(p.fade == Approx(std::pow(0.6f, static_cast<float>(p.depth))));
    }
    CHECK(counts[0] == 1);
    CHECK(counts[1] == 4);          // probability 1 -> every candidate spawns
    CHECK(counts[2] == 4 * 2);      // half as many candidates per depth-1 parent
}

TEST_CASE("beam flicker and intensity noise stay pure functions of time", "[sim][analytic]") {
    Effect e = bolt_effect();
    Node* node = e.find_node("bolt");
    node->parameters["flicker"] = Parameter{0.6f};
    node->parameters["flicker_frequency"] = Parameter{25.0f};
    node->parameters["intensity_noise"] = Parameter{0.5f};
    node->parameters["emissive"] = Parameter{10.0f};

    std::unique_ptr<sim::IRuntime> a, b;
    const BeamState& first = stepped_bolt(a, e, 0.37);
    const BeamState& again = stepped_bolt(b, e, 0.37);
    CHECK(first.emissive == again.emissive);
    CHECK(first.paths.front().intensity == again.paths.front().intensity);
    CHECK(first.emissive >= 10.0f * 0.4f - 1e-4f);
    CHECK(first.emissive <= 10.0f * 1.6f + 1e-4f);

    bool varies = false;
    const std::vector<float>& intensity = first.paths.front().intensity;
    for (size_t i = 1; i < intensity.size(); ++i) {
        CHECK(intensity[i] >= 0.5f - 1e-5f);
        CHECK(intensity[i] <= 1.5f + 1e-5f);
        if (std::fabs(intensity[i] - intensity[i - 1]) > 1e-4f) varies = true;
    }
    CHECK(varies);

    // the whole-bolt flicker really does move between frames
    std::unique_ptr<sim::IRuntime> c;
    CHECK(stepped_bolt(c, e, 0.53).emissive != first.emissive);
}

TEST_CASE("beam afterglow keeps the previous path as a fading ghost", "[sim][analytic]") {
    Effect e = bolt_effect();
    Node* node = e.find_node("bolt");
    node->parameters["jitter_rate"] = Parameter{10.0f};  // one re-roll per 0.1 s
    node->parameters["afterglow"] = Parameter{0.05f};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();  // t = 1/60, re-roll 0: nothing to fade yet
    CHECK(beam_of(rt->state(), "bolt")->ghosts.empty());

    rt->simulate_to(0.11);  // re-roll 1, 0.01 s old -> a nearly full-strength ghost
    const BeamState* fresh = beam_of(rt->state(), "bolt");
    REQUIRE(!fresh->ghosts.empty());
    CHECK(fresh->ghosts.size() == fresh->paths.size());
    CHECK(fresh->ghosts.front().fade > 0.5f);
    CHECK(fresh->ghosts.front().fade < 1.0f);
    CHECK(fresh->ghosts.front().points.size() == fresh->paths.front().points.size());
    CHECK(fresh->ghosts.front().points != fresh->paths.front().points);  // the *previous* bolt
    const float early = fresh->ghosts.front().fade;

    rt->simulate_to(0.14);  // same re-roll, older ghost
    CHECK(beam_of(rt->state(), "bolt")->ghosts.front().fade < early);

    rt->simulate_to(0.18);  // past `afterglow`, before the next re-roll
    CHECK(beam_of(rt->state(), "bolt")->ghosts.empty());
}

TEST_CASE("beam impact_flare marks both ends of the bolt", "[sim][analytic]") {
    Effect e = bolt_effect();
    std::unique_ptr<sim::IRuntime> none;
    CHECK(stepped_bolt(none, e).flares.empty());

    e.find_node("bolt")->parameters["impact_flare"] = Parameter{0.8f};
    std::unique_ptr<sim::IRuntime> rt;
    const BeamState& bolt = stepped_bolt(rt, e);
    REQUIRE(bolt.flares.size() == 2u);
    CHECK(distance(bolt.flares[0].position, bolt.paths.front().points.back()) == 0.0f);
    CHECK(bolt.flares[0].radius == Approx(0.8f));
    CHECK(bolt.flares[0].intensity == Approx(1.0f));
    CHECK(distance(bolt.flares[1].position, bolt.paths.front().points.front()) == 0.0f);
    CHECK(bolt.flares[1].radius < bolt.flares[0].radius);  // the sky end is smaller
}

// ---------------------------------------------------------------------------
// beams: flow (noise_scroll, noise_loop, noise_taper) - the channel / tether /
// drain half of the primitive
// ---------------------------------------------------------------------------

namespace {

// A horizontal channel with a static shape (no re-rolls), 6 m long.
Effect channel_effect() {
    Effect e;
    e.seed = 4321;
    e.duration = 4.0;
    Node beam = node_of(NodeType::Beam, "bolt");
    beam.parameters["origin"] = Parameter{Vec3{3.0f, 1.25f, 0.0f}};
    beam.parameters["target"] = Parameter{Vec3{-3.0f, 1.35f, 0.0f}};
    beam.parameters["segments"] = Parameter{48};
    beam.parameters["width"] = Parameter{0.08f};
    beam.parameters["noise_amplitude"] = Parameter{0.3f};
    beam.parameters["noise_frequency"] = Parameter{0.5f};
    beam.parameters["jitter_rate"] = Parameter{0.0f};
    e.add_node(beam);
    return e;
}

float largest_move(const std::vector<Vec3>& a, const std::vector<Vec3>& b) {
    REQUIRE(a.size() == b.size());
    float out = 0.0f;
    for (size_t i = 0; i < a.size(); ++i) out = std::max(out, distance(a[i], b[i]));
    return out;
}

}  // namespace

TEST_CASE("beam noise_scroll makes the path travel smoothly instead of re-rolling", "[sim][analytic]") {
    Effect e = channel_effect();
    std::unique_ptr<sim::IRuntime> rt;
    const std::vector<Vec3> still_a = stepped_bolt(rt, e, 0.5).paths.front().points;
    const std::vector<Vec3> still_b = stepped_bolt(rt, e, 1.5).paths.front().points;
    CHECK(still_a == still_b);  // jitter_rate 0 and no scroll: a frozen shape

    e.find_node("bolt")->parameters["noise_scroll"] = Parameter{2.0f};
    std::unique_ptr<sim::IRuntime> a, b, c;
    const std::vector<Vec3> at_050 = stepped_bolt(a, e, 0.5).paths.front().points;
    const std::vector<Vec3> again = stepped_bolt(b, e, 0.5).paths.front().points;
    const std::vector<Vec3> next_frame = stepped_bolt(c, e, 0.5 + 1.0 / 60.0).paths.front().points;
    CHECK(at_050 == again);                                // a pure function of time
    CHECK(at_050 != still_a);                              // it moves ...
    CHECK(largest_move(at_050, next_frame) > 1e-4f);       // ... every frame ...
    CHECK(largest_move(at_050, next_frame) < 0.05f);       // ... and by a little: no re-roll jump
    // the endpoints never leave the anchors
    CHECK(distance(at_050.front(), Vec3{3.0f, 1.25f, 0.0f}) < 1e-5f);
    CHECK(distance(at_050.back(), Vec3{-3.0f, 1.35f, 0.0f}) < 1e-5f);

    // The field travels origin -> target at noise_scroll m/s and is measured from the
    // target end (the end the flow arrives at): at t = 0.5 s and 2 m/s a vertex
    // `d` metres from the target reads the displacement field at `d + 1`.
    const uint64_t stream = derive_seed(e.seed, "bolt", std::optional<uint32_t>{});
    const uint32_t seed = static_cast<uint32_t>(stream ^ (stream >> 32));
    const procedural::FbmParams fbm{3, 2.0f, 0.5f, procedural::NoiseBasis::Simplex};
    const Vec3 origin{3.0f, 1.25f, 0.0f}, target{-3.0f, 1.35f, 0.0f};
    const Vec3 span = target - origin;
    const float span_length = length(span);
    const Vec3 forward = span / span_length;
    const Vec3 u = orthogonal(forward);
    const Vec3 v = cross(forward, u);
    for (int i = 1; i < 48; ++i) {
        const float f = static_cast<float>(i) / 48.0f;
        const float along = ((1.0f - f) * span_length + 2.0f * 0.5f) * 0.5f;
        const Vec3 expected = origin + span * f +
                              u * (0.3f * procedural::fbm3(Vec3{along, 0.0f, 0.0f}, seed, fbm)) +
                              v * (0.3f * procedural::fbm3(Vec3{along, 17.0f, 0.0f}, seed, fbm));
        INFO("vertex " << i);
        CHECK(distance(at_050[static_cast<size_t>(i)], expected) < 1e-5f);
    }
}

TEST_CASE("beam noise_loop repeats the flow exactly", "[sim][analytic]") {
    Effect e = channel_effect();
    Node* node = e.find_node("bolt");
    node->parameters["noise_scroll"] = Parameter{1.5f};
    node->parameters["noise_loop"] = Parameter{1.5f};  // 90 steps of 1/60: frame times land on the period

    std::unique_ptr<sim::IRuntime> a, b, c, d;
    const std::vector<Vec3> start = stepped_bolt(a, e, 0.5).paths.front().points;
    const std::vector<Vec3> one_loop = stepped_bolt(b, e, 2.0).paths.front().points;
    const std::vector<Vec3> two_loops = stepped_bolt(c, e, 3.5).paths.front().points;
    const std::vector<Vec3> half_way = stepped_bolt(d, e, 1.25).paths.front().points;
    CHECK(largest_move(start, one_loop) < 1e-4f);
    CHECK(largest_move(start, two_loops) < 1e-4f);
    CHECK(largest_move(start, half_way) > 0.02f);  // it is a loop, not a freeze

    // No pop where the loop wraps (t = 1.5): consecutive frames across the seam move
    // about as much as consecutive frames anywhere else.
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.5 - 1.0 / 60.0);
    const std::vector<Vec3> before = beam_of(rt->state(), "bolt")->paths.front().points;
    rt->step();
    const std::vector<Vec3> at_seam = beam_of(rt->state(), "bolt")->paths.front().points;
    rt->step();
    const std::vector<Vec3> after = beam_of(rt->state(), "bolt")->paths.front().points;
    const float across = largest_move(before, at_seam);
    const float beyond = largest_move(at_seam, after);
    CHECK(across < 0.05f);
    CHECK(beyond < 0.05f);

    // The cross-fade is renormalised, so the loop's mid point keeps its amplitude.
    const auto reach = [&](const std::vector<Vec3>& points) {
        float out = 0.0f;
        for (size_t i = 0; i < points.size(); ++i) {
            const float f = static_cast<float>(i) / static_cast<float>(points.size() - 1);
            out = std::max(out, distance(points[i], lerp(points.front(), points.back(), f)));
        }
        return out;
    };
    CHECK(reach(half_way) > 0.4f * reach(start));
    CHECK(reach(half_way) < 2.5f * reach(start));
}

TEST_CASE("beams sharing a noise_seed follow one field and noise_offset shifts its phase", "[sim][analytic]") {
    // Three strands between the same anchors: two read the shared field 7, the
    // third keeps the field its own node id seeds.
    Effect e = channel_effect();
    const Vec3 origin{3.0f, 1.25f, 0.0f}, target{-3.0f, 1.35f, 0.0f};
    const float span_length = length(target - origin);
    const float spacing = span_length / 48.0f;  // metres between vertices
    {
        Node* bolt = e.find_node("bolt");
        bolt->parameters["noise_scroll"] = Parameter{2.0f};
        bolt->parameters["noise_seed"] = Parameter{7};
    }
    Node twin = *e.find_node("bolt");
    twin.id = "twin";
    Node shifted = twin;
    shifted.id = "shifted";
    shifted.parameters["noise_offset"] = Parameter{8.0f * spacing};
    Node loner = twin;
    loner.id = "loner";
    loner.parameters["noise_seed"] = Parameter{0};
    e.add_node(twin);
    e.add_node(shifted);
    e.add_node(loner);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.75);
    const std::vector<Vec3> bolt = beam_of(rt->state(), "bolt")->paths.front().points;
    const std::vector<Vec3> same = beam_of(rt->state(), "twin")->paths.front().points;
    const std::vector<Vec3> moved = beam_of(rt->state(), "shifted")->paths.front().points;
    const std::vector<Vec3> own = beam_of(rt->state(), "loner")->paths.front().points;
    REQUIRE(bolt.size() == 49u);
    CHECK(same == bolt);                       // one field, one path - whatever the node is called
    CHECK(largest_move(own, bolt) > 0.02f);    // noise_seed 0 keeps the node's own field
    CHECK(largest_move(moved, bolt) > 0.02f);  // a phase shift is a different path ...

    // ... but the same wave: the field is measured from the target end, so a strand
    // reading it 8 vertex spacings further along has, at vertex i, the displacement
    // the unshifted strand has 8 vertices nearer the origin.
    const auto displacement = [&](const std::vector<Vec3>& points, size_t i) {
        return points[i] - lerp(origin, target, static_cast<float>(i) / 48.0f);
    };
    for (size_t i = 9; i < 48; ++i) {
        INFO("vertex " << i);
        CHECK(distance(displacement(moved, i), displacement(bolt, i - 8)) < 2e-5f);
    }
}

TEST_CASE("beam noise_taper eases the displacement in from both anchors", "[sim][analytic]") {
    Effect e = channel_effect();
    std::unique_ptr<sim::IRuntime> rt;
    const std::vector<Vec3> plain = stepped_bolt(rt, e, 0.5).paths.front().points;

    e.find_node("bolt")->parameters["noise_taper"] = Parameter{0.25f};
    std::unique_ptr<sim::IRuntime> tapered_rt;
    const std::vector<Vec3> tapered = stepped_bolt(tapered_rt, e, 0.5).paths.front().points;
    REQUIRE(tapered.size() == plain.size());

    const Vec3 origin = plain.front(), target = plain.back();
    const auto offset = [&](const std::vector<Vec3>& points, size_t i) {
        const float f = static_cast<float>(i) / static_cast<float>(points.size() - 1);
        return distance(points[i], lerp(origin, target, f));
    };
    const size_t last = plain.size() - 1;
    for (size_t i = 0; i <= last; ++i) {
        const float f = static_cast<float>(i) / static_cast<float>(last);
        const float expected = smoothstep(0.0f, 0.25f, f) * smoothstep(0.0f, 0.25f, 1.0f - f);
        INFO("vertex " << i);
        CHECK(offset(tapered, i) == Approx(offset(plain, i) * expected).margin(1e-5));
    }
    // beside the anchors the path hugs the straight line; the middle is untouched
    CHECK(offset(tapered, 1) < 0.05f * std::max(offset(plain, 1), 1e-3f) + 1e-4f);
    CHECK(offset(tapered, last - 1) < 0.05f * std::max(offset(plain, last - 1), 1e-3f) + 1e-4f);
    CHECK(distance(tapered[last / 2], plain[last / 2]) < 1e-6f);
}

// ---------------------------------------------------------------------------
// decals, meshes, camera, post effects, volumes
// ---------------------------------------------------------------------------

TEST_CASE("the fire_aoe rune decal follows its opacity track and fade-in", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    rt->simulate_to(0.15);
    const DecalState* rune = decal_of(rt->state(), "rune");
    REQUIRE(rune != nullptr);
    // track: 0 at 0, 1 at 0.35 -> 0.43 at 0.15; fade_in is 0 in this example
    CHECK(rune->opacity == Approx(0.43f).margin(0.03));
    CHECK(rune->size.x == Approx(7.2f));
    CHECK(rune->circle);
    CHECK(rune->blend == BlendMode::Additive);
    CHECK(rune->texture_id == "tex_rune");
    CHECK(rune->normal.y == Approx(1.0f));

    rt->simulate_to(0.6);
    CHECK(decal_of(rt->state(), "rune")->opacity == Approx(1.0f).margin(0.02));  // track holds 1.0 until 2.4
    rt->simulate_to(2.9);
    CHECK(decal_of(rt->state(), "rune")->opacity == Approx(0.29f).margin(0.03));  // fading towards 0.15 at 3.0

    // the scorch decal only appears from its start_time
    rt->reset();
    rt->simulate_to(0.3);
    CHECK(decal_of(rt->state(), "scorch") == nullptr);  // scorch starts at 0.4
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
    CHECK(rt->state().camera->position.y == Approx(3.6f));
    CHECK(rt->state().camera->fov_deg == Approx(42.0f));
    rt->simulate_to(2.0);
    REQUIRE(rt->state().camera.has_value());
    CHECK(rt->state().camera->target.y == Approx(1.4f));
}

TEST_CASE("post effects appear inside their window", "[sim][analytic]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fire_aoe.json"));
    rt->simulate_to(0.4);
    CHECK(rt->state().post_effects.empty());  // haze starts at 0.5
    rt->simulate_to(1.5);
    REQUIRE(rt->state().post_effects.size() == 1u);
    CHECK(rt->state().post_effects[0].id == "haze");
    CHECK(rt->state().post_effects[0].post_type == "heat_haze");
    CHECK(rt->state().post_effects[0].intensity == Approx(0.4f));
    CHECK(rt->state().post_effects[0].time == Approx(1.5).margin(0.02));
    rt->simulate_to(4.0);
    CHECK(rt->state().post_effects.empty());  // duration 3.0 -> ends at 3.9
}

TEST_CASE("volume nodes in simulation mode report a stub state", "[sim][analytic]") {
    Effect e;
    e.duration = 1.0;
    Node volume = node_of(NodeType::Volume, "smoke");
    // `mode` defaults to procedural now; the fluid stub is the other branch.
    volume.parameters["mode"] = Parameter{std::string("simulation")};
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

    rt->simulate_to(0.3);  // anticipation only
    CHECK(spawned("gather_ps") > 0u);
    CHECK(spawned("burst_ps") == 0u);
    CHECK(spawned("flame_ps") == 0u);
    CHECK(spawned("ember_ps") == 0u);

    rt->simulate_to(0.6);  // activation (eruption)
    CHECK(spawned("burst_ps") > 0u);
    CHECK(spawned("rock_ps") > 0u);

    rt->simulate_to(1.8);  // peak / sustain
    CHECK(spawned("flame_ps") > 0u);
    CHECK(spawned("column_ps") > 0u);
    CHECK(spawned("smoke_ps") > 0u);

    rt->simulate_to(2.7);  // decay
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
