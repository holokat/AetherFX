// CPU runtime: determinism contract (docs/RUNTIME.md section 10), integration
// and forces (4, 5), emission (3), events (8) and over-life modulation (6).
#include <cmath>
#include <filesystem>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/compiler/compiled_effect.hpp"
#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/sim/runtime.hpp"

using namespace aether;
using Catch::Approx;
using Catch::Matchers::WithinAbs;

namespace {

std::filesystem::path example_path(const std::string& name) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name;
}

Effect load_example(const std::string& name) { return load_effect_file(example_path(name)); }

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

// A point emitter that bursts `count` particles at t=0 into one system.
Effect single_burst_effect(float velocity, Vec3 direction, Vec3 position, int count = 1) {
    Effect e;
    e.name = "test";
    e.duration = 5.0;
    e.seed = 3;
    Node ps = node_of(NodeType::ParticleSystem, "ps");
    ps.parameters["lifetime"] = Parameter{9.0f};
    ps.parameters["max_particles"] = Parameter{256};
    ps.parameters["size"] = Parameter{0.02f};
    ps.parameters["opacity_over_life"] = Parameter{Curve::constant(1.0f)};
    Node em = node_of(NodeType::Emitter, "em");
    em.parameters["shape"] = Parameter{std::string("point")};
    em.parameters["rate"] = Parameter{0.0f};
    em.parameters["burst_count"] = Parameter{count};
    em.parameters["burst_times"] = Parameter{std::vector<float>{0.0f}};
    em.parameters["velocity"] = Parameter{velocity};
    em.parameters["direction"] = Parameter{direction};
    em.parameters["position"] = Parameter{position};
    em.inputs["particle"] = {NodeRef::parse("ps")};
    e.add_node(ps);
    e.add_node(em);
    return e;
}

void attach_force(Effect& e, const std::string& id, const std::string& type, float strength, Vec3 direction = {0, -1, 0},
                  Vec3 position = {0, 0, 0}) {
    Node f = node_of(NodeType::Force, id);
    f.parameters["force_type"] = Parameter{type};
    f.parameters["strength"] = Parameter{strength};
    f.parameters["direction"] = Parameter{direction};
    f.parameters["position"] = Parameter{position};
    e.add_node(f);
    Node* ps = e.find_node("ps");
    ps->inputs["forces"].push_back(NodeRef::parse(id));
}

const ParticleBuffer& buffer_of(const sim::IRuntime& rt, const std::string& id) {
    const ParticleBuffer* b = rt.state().find_particles(id);
    REQUIRE(b != nullptr);
    return *b;
}

sim::SystemStatistics system_stats(const sim::IRuntime& rt, const std::string& id) {
    for (const sim::SystemStatistics& s : rt.statistics().systems)
        if (s.system_id == id) return s;
    FAIL("no statistics for system " << id);
    return {};
}

}  // namespace

// ---------------------------------------------------------------------------
// determinism contract (docs/RUNTIME.md section 10)
// ---------------------------------------------------------------------------

TEST_CASE("the same effect produces the same 60 frame hashes", "[sim][determinism]") {
    const Effect effect = load_example("fireball.json");
    const compiler::CompiledEffect compiled = compiler::compile(effect);
    REQUIRE(compiled.diagnostics.error_count() == 0);

    std::vector<uint64_t> first, second;
    {
        std::unique_ptr<sim::IRuntime> rt = sim::create_cpu_runtime(compiled);
        for (int i = 0; i < 60; ++i) {
            rt->step();
            first.push_back(rt->state().hash());
        }
    }
    {
        std::unique_ptr<sim::IRuntime> rt = sim::create_cpu_runtime(compiled);
        for (int i = 0; i < 60; ++i) {
            rt->step();
            second.push_back(rt->state().hash());
        }
    }
    CHECK(first == second);
    CHECK(first.front() != first.back());

    // reset() restores the same sequence on a used runtime
    std::unique_ptr<sim::IRuntime> rt = sim::create_cpu_runtime(compiled);
    rt->simulate_to(0.7);
    rt->reset();
    CHECK(rt->time() == 0.0);
    CHECK(rt->frame_index() == 0u);
    CHECK(rt->state().total_particles() == 0u);
    std::vector<uint64_t> third;
    for (int i = 0; i < 60; ++i) {
        rt->step();
        third.push_back(rt->state().hash());
    }
    CHECK(third == first);
}

TEST_CASE("changing the effect seed changes the simulation", "[sim][determinism]") {
    Effect effect = load_example("fireball.json");
    std::unique_ptr<sim::IRuntime> a = runtime_for(effect);
    effect.seed += 1;
    std::unique_ptr<sim::IRuntime> b = runtime_for(effect);
    for (int i = 0; i < 30; ++i) {
        a->step();
        b->step();
    }
    CHECK(a->state().hash() != b->state().hash());
    // but the population is comparable
    CHECK(a->state().total_particles() > 0u);
    CHECK(b->state().total_particles() > 0u);
}

TEST_CASE("simulate_to equals stepping manually", "[sim][determinism]") {
    const Effect effect = load_example("fireball.json");
    std::unique_ptr<sim::IRuntime> a = runtime_for(effect);
    std::unique_ptr<sim::IRuntime> b = runtime_for(effect);
    for (int i = 0; i < 45; ++i) a->step();
    b->simulate_to(45.0 / 60.0);
    CHECK(b->frame_index() == 45u);
    CHECK(a->state().hash() == b->state().hash());
    CHECK(a->time() == Approx(b->time()));

    // stepping to a time that is not a multiple of dt stops at the first
    // frame at or after it
    std::unique_ptr<sim::IRuntime> c = runtime_for(effect);
    c->simulate_to(0.5);
    CHECK(c->frame_index() == 30u);
    c->simulate_to(0.5);  // idempotent
    CHECK(c->frame_index() == 30u);
    c->simulate_to(0.51);
    CHECK(c->frame_index() == 31u);
}

TEST_CASE("runtime metadata", "[sim]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("fireball.json"));
    CHECK(rt->backend_name() == "cpu");
    CHECK(rt->fixed_dt() == Approx(1.0 / 60.0));
    CHECK(rt->compiled().effect.name == "Fireball");
    CHECK(rt->state().particles.size() == 3u);  // flame_ps, smoke_ps, spark_ps in compiled order
    CHECK(rt->state().camera.has_value());      // populated by reset() at t=0
}

// ---------------------------------------------------------------------------
// integration and forces (docs/RUNTIME.md sections 4 and 5)
// ---------------------------------------------------------------------------

TEST_CASE("gravity integrates to the analytic free-fall position", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 10, 0});
    attach_force(e, "g", "gravity", 9.81f);
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);

    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    const float expected = 10.0f - 0.5f * 9.81f * 1.0f;  // y0 - 1/2 g t^2
    const float drop = 10.0f - b.position[0].y;
    CHECK(b.position[0].y == Approx(expected).epsilon(0.05));
    CHECK(drop > 0.0f);
    CHECK(b.velocity[0].y == Approx(-9.81f).epsilon(0.02));
    CHECK(b.age[0] == Approx(1.0f - 1.0f / 60.0f).epsilon(0.02));
}

TEST_CASE("a plane collider bounces particles", "[sim][collision]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 3, 0});
    attach_force(e, "g", "gravity", 9.81f);
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["normal"] = Parameter{Vec3{0, 1, 0}};
    collider.parameters["bounce"] = Parameter{0.5f};
    collider.parameters["friction"] = Parameter{0.0f};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    float lowest = 1e9f;
    bool reversed = false;
    float speed_before = 0.0f;
    for (int i = 0; i < 120; ++i) {
        const ParticleBuffer& before = buffer_of(*rt, "ps");
        const float vy = before.count() > 0 ? before.velocity[0].y : 0.0f;
        rt->step();
        const ParticleBuffer& b = buffer_of(*rt, "ps");
        REQUIRE(b.count() == 1u);
        lowest = std::min(lowest, b.position[0].y);
        if (vy < -1.0f && b.velocity[0].y > 0.0f) {
            reversed = true;
            speed_before = -vy;
        }
    }
    CHECK(lowest > -1e-3f);
    CHECK(reversed);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    CHECK(b.position[0].y >= -1e-3f);
    CHECK(speed_before > 0.0f);
    CHECK(system_stats(*rt, "ps").collisions > 0u);
}

TEST_CASE("an attractor pulls toward its position", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{2, 0, 0});
    attach_force(e, "pull", "attractor", 5.0f, Vec3{0, -1, 0}, Vec3{0, 0, 0});
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.2);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    CHECK(b.velocity[0].x < 0.0f);  // toward the origin
    CHECK(b.position[0].x < 2.0f);
    CHECK(std::fabs(b.velocity[0].y) < 1e-4f);
}

TEST_CASE("a repulsor pushes away from its position", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{2, 0, 0});
    attach_force(e, "push", "repulsor", 5.0f, Vec3{0, -1, 0}, Vec3{0, 0, 0});
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.2);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    CHECK(b.velocity[0].x > 0.0f);
}

TEST_CASE("a vortex accelerates tangentially", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{1, 0, 0});
    attach_force(e, "swirl", "vortex", 4.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.1);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    const Vec3 radial = normalize(Vec3{b.position[0].x, 0.0f, b.position[0].z});
    const Vec3 v = b.velocity[0];
    CHECK(length(v) > 0.1f);
    CHECK(std::fabs(dot(normalize(v), radial)) < 0.05f);
    CHECK(std::fabs(v.y) < 1e-4f);
}

TEST_CASE("drag reduces speed", "[sim][forces]") {
    Effect e = single_burst_effect(4.0f, Vec3{1, 0, 0}, Vec3{0, 0, 0});
    attach_force(e, "d", "drag", 3.0f);
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const float initial = length(buffer_of(*rt, "ps").velocity[0]);
    rt->simulate_to(0.5);
    const float later = length(buffer_of(*rt, "ps").velocity[0]);
    CHECK(later < initial);
    CHECK(later > 0.0f);
    CHECK(initial == Approx(4.0f).epsilon(0.1));
}

TEST_CASE("wind relaxes velocity toward the wind vector", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    attach_force(e, "w", "wind", 6.0f, Vec3{1, 0, 0});
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);
    const float after_one = buffer_of(*rt, "ps").velocity[0].x;
    // exponential relaxation toward the wind velocity: v = w * (1 - e^-t)
    CHECK(after_one == Approx(6.0f * (1.0f - std::exp(-1.0f))).epsilon(0.02));
    rt->simulate_to(5.0);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    CHECK(b.velocity[0].x > after_one);
    CHECK(b.velocity[0].x == Approx(6.0f).epsilon(0.02));
    CHECK(std::fabs(b.velocity[0].y) < 1e-4f);
}

TEST_CASE("buoyancy lifts by strength times temperature", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    Node f = node_of(NodeType::Force, "b");
    f.parameters["force_type"] = Parameter{std::string("buoyancy")};
    f.parameters["strength"] = Parameter{2.0f};
    f.parameters["temperature"] = Parameter{0.5f};
    e.add_node(f);
    e.find_node("ps")->inputs["forces"] = {NodeRef::parse("b")};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    CHECK(b.velocity[0].y == Approx(1.0f).epsilon(0.02));  // 2.0 * 0.5 * 1 s
}

TEST_CASE("forces only act inside their own window", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 10, 0});
    attach_force(e, "g", "gravity", 9.81f);
    e.find_node("g")->parameters["start_time"] = Parameter{1.0f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.9);
    CHECK(buffer_of(*rt, "ps").position[0].y == Approx(10.0f));
    rt->simulate_to(1.5);
    CHECK(buffer_of(*rt, "ps").position[0].y < 10.0f);
}

TEST_CASE("system drag damps velocity", "[sim][forces]") {
    Effect e = single_burst_effect(5.0f, Vec3{1, 0, 0}, Vec3{0, 0, 0});
    e.find_node("ps")->parameters["drag"] = Parameter{4.0f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    CHECK(length(b.velocity[0]) < 2.0f);
    CHECK(length(b.velocity[0]) > 0.0f);
}

// ---------------------------------------------------------------------------
// emission (docs/RUNTIME.md sections 1.3 and 3)
// ---------------------------------------------------------------------------

TEST_CASE("a continuous rate spawns rate*duration particles", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    Node* em = e.find_node("em");
    em->parameters["rate"] = Parameter{100.0f};
    em->parameters["burst_count"] = Parameter{0};
    em->parameters["burst_times"] = Parameter{std::vector<float>{}};
    e.find_node("ps")->parameters["max_particles"] = Parameter{4000};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);
    const size_t spawned = system_stats(*rt, "ps").spawned_total;
    CHECK(spawned >= 99u);
    CHECK(spawned <= 101u);
    CHECK(buffer_of(*rt, "ps").count() == spawned);
}

TEST_CASE("bursts fire once, on the step that contains their time", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 7);
    e.find_node("em")->parameters["burst_times"] = Parameter{std::vector<float>{0.5f}};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    for (int i = 0; i < 29; ++i) rt->step();
    CHECK(system_stats(*rt, "ps").spawned_total == 0u);  // t = 29/60 < 0.5
    rt->step();                                          // t = 30/60 = 0.5
    CHECK(system_stats(*rt, "ps").spawned_total == 7u);
    for (int i = 0; i < 30; ++i) rt->step();
    CHECK(system_stats(*rt, "ps").spawned_total == 7u);  // exactly once
}

TEST_CASE("a burst at t=0 fires on the first step", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 3);
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    CHECK(rt->state().total_particles() == 0u);
    rt->step();
    CHECK(buffer_of(*rt, "ps").count() == 3u);
}

TEST_CASE("an emitter only spawns inside its phase window", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    e.timeline.phases.push_back(TimelinePhase{"peak", 1.0, 2.0});
    Node* em = e.find_node("em");
    em->parameters["phase"] = Parameter{std::string("peak")};
    em->parameters["rate"] = Parameter{60.0f};
    em->parameters["burst_count"] = Parameter{0};
    em->parameters["burst_times"] = Parameter{std::vector<float>{}};
    e.find_node("ps")->parameters["max_particles"] = Parameter{500};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.99);
    CHECK(system_stats(*rt, "ps").spawned_total == 0u);
    rt->simulate_to(2.0);
    const size_t inside = system_stats(*rt, "ps").spawned_total;
    CHECK(inside >= 59u);
    CHECK(inside <= 61u);
    rt->simulate_to(3.0);
    CHECK(system_stats(*rt, "ps").spawned_total == inside);  // window closed
}

TEST_CASE("the system cap drops spawns", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 50);
    e.find_node("ps")->parameters["max_particles"] = Parameter{10};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const sim::SystemStatistics s = system_stats(*rt, "ps");
    CHECK(s.alive == 10u);
    CHECK(s.dropped == 40u);
    CHECK(s.capacity == 10u);
    CHECK(buffer_of(*rt, "ps").count() == 10u);
}

TEST_CASE("the emitter cap limits its lifetime total", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    Node* em = e.find_node("em");
    em->parameters["rate"] = Parameter{600.0f};
    em->parameters["burst_count"] = Parameter{0};
    em->parameters["burst_times"] = Parameter{std::vector<float>{}};
    em->parameters["max_particles"] = Parameter{25};
    e.find_node("ps")->parameters["max_particles"] = Parameter{1000};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);
    CHECK(system_stats(*rt, "ps").spawned_total == 25u);
}

TEST_CASE("particles die at the end of their lifetime", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 4);
    e.find_node("ps")->parameters["lifetime"] = Parameter{0.25f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.2);
    CHECK(buffer_of(*rt, "ps").count() == 4u);
    rt->simulate_to(0.4);
    CHECK(buffer_of(*rt, "ps").count() == 0u);
    const sim::SystemStatistics s = system_stats(*rt, "ps");
    CHECK(s.died_total == 4u);
    CHECK(s.spawned_total == 4u);
    CHECK(s.peak_alive == 4u);
}

TEST_CASE("spawn order is preserved when particles die", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0});
    Node* em = e.find_node("em");
    em->parameters["rate"] = Parameter{60.0f};
    em->parameters["burst_count"] = Parameter{0};
    em->parameters["burst_times"] = Parameter{std::vector<float>{}};
    e.find_node("ps")->parameters["lifetime"] = Parameter{0.2f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.0);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() > 2u);
    for (size_t i = 1; i < b.count(); ++i) CHECK(b.age[i] <= b.age[i - 1]);  // oldest first
}

// ---------------------------------------------------------------------------
// events (docs/RUNTIME.md section 8)
// ---------------------------------------------------------------------------

TEST_CASE("an on_time event bursts its targets", "[sim][events]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("lightning_strike.json"));
    rt->simulate_to(0.1);
    CHECK(system_stats(*rt, "debris_ps").spawned_total == 40u);
    CHECK(system_stats(*rt, "shock_ps").spawned_total == 200u);
    CHECK(system_stats(*rt, "spark_ps").spawned_total == 350u);
    CHECK(rt->statistics().events_fired == 1u);
    rt->simulate_to(0.5);
    CHECK(system_stats(*rt, "debris_ps").spawned_total == 40u);  // fires once only
    CHECK(rt->statistics().events_fired == 1u);
}

TEST_CASE("an on_death event spawns into a target", "[sim][events]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 2, 0}, 5);
    e.find_node("ps")->parameters["lifetime"] = Parameter{0.25f};

    Node ps2 = node_of(NodeType::ParticleSystem, "puff_ps");
    ps2.parameters["lifetime"] = Parameter{2.0f};
    ps2.parameters["max_particles"] = Parameter{200};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "puff");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{3};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.parameters["velocity"] = Parameter{0.0f};
    em2.inputs["particle"] = {NodeRef::parse("puff_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "on_die");
    ev.parameters["trigger"] = Parameter{std::string("on_death")};
    ev.inputs["source"] = {NodeRef::parse("ps")};
    ev.inputs["targets"] = {NodeRef::parse("puff")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.2);
    CHECK(system_stats(*rt, "puff_ps").spawned_total == 0u);
    rt->simulate_to(0.5);
    CHECK(system_stats(*rt, "puff_ps").spawned_total == 15u);  // 5 deaths x burst 3
    CHECK(rt->statistics().events_fired == 5u);
    // inherit_position places the burst at the dying particle's position
    const ParticleBuffer& b = buffer_of(*rt, "puff_ps");
    REQUIRE(b.count() == 15u);
    CHECK(b.position[0].y == Approx(2.0f).margin(1e-4));
}

TEST_CASE("an on_spawn event with max_triggers stops firing", "[sim][events]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 10);
    Node ps2 = node_of(NodeType::ParticleSystem, "echo_ps");
    ps2.parameters["max_particles"] = Parameter{100};
    ps2.parameters["lifetime"] = Parameter{4.0f};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "echo");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{1};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.inputs["particle"] = {NodeRef::parse("echo_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "echo_event");
    ev.parameters["trigger"] = Parameter{std::string("on_spawn")};
    ev.parameters["max_triggers"] = Parameter{4};
    ev.inputs["source"] = {NodeRef::parse("ps")};
    ev.inputs["targets"] = {NodeRef::parse("echo")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    CHECK(system_stats(*rt, "echo_ps").spawned_total == 4u);
}

TEST_CASE("an on_distance event fires once per particle", "[sim][events]") {
    Effect e = single_burst_effect(2.0f, Vec3{1, 0, 0}, Vec3{0, 0, 0}, 6);
    Node ps2 = node_of(NodeType::ParticleSystem, "mark_ps");
    ps2.parameters["max_particles"] = Parameter{100};
    ps2.parameters["lifetime"] = Parameter{5.0f};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "mark");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{1};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.inputs["particle"] = {NodeRef::parse("mark_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "far");
    ev.parameters["trigger"] = Parameter{std::string("on_distance")};
    ev.parameters["distance"] = Parameter{1.0f};
    ev.inputs["source"] = {NodeRef::parse("ps")};
    ev.inputs["targets"] = {NodeRef::parse("mark")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.4);  // 2 m/s -> crosses 1 m at 0.5 s
    CHECK(system_stats(*rt, "mark_ps").spawned_total == 0u);
    rt->simulate_to(1.0);
    CHECK(system_stats(*rt, "mark_ps").spawned_total == 6u);
    rt->simulate_to(2.0);
    CHECK(system_stats(*rt, "mark_ps").spawned_total == 6u);  // once per particle
}

TEST_CASE("an on_collision event fires on impact", "[sim][events]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 2, 0}, 3);
    attach_force(e, "g", "gravity", 9.81f);
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["bounce"] = Parameter{0.0f};
    collider.parameters["kill_on_collision"] = Parameter{true};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    Node ps2 = node_of(NodeType::ParticleSystem, "dust_ps");
    ps2.parameters["max_particles"] = Parameter{100};
    ps2.parameters["lifetime"] = Parameter{3.0f};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "dust");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{2};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.inputs["particle"] = {NodeRef::parse("dust_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "impact");
    ev.parameters["trigger"] = Parameter{std::string("on_collision")};
    ev.inputs["source"] = {NodeRef::parse("ps")};
    ev.inputs["targets"] = {NodeRef::parse("dust")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.5);
    CHECK(system_stats(*rt, "ps").collisions == 3u);
    CHECK(system_stats(*rt, "ps").alive == 0u);  // kill_on_collision
    CHECK(system_stats(*rt, "dust_ps").spawned_total == 6u);
}

// ---------------------------------------------------------------------------
// over-life modulation (docs/RUNTIME.md section 6)
// ---------------------------------------------------------------------------

TEST_CASE("size, opacity, emissive and color follow their over-life curves", "[sim][modulation]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 1);
    Node* ps = e.find_node("ps");
    ps->parameters["lifetime"] = Parameter{1.0f};
    ps->parameters["size"] = Parameter{2.0f};
    ps->parameters["size_over_life"] = Parameter{Curve::linear(0.0f, 1.0f)};
    ps->parameters["opacity"] = Parameter{0.8f};
    ps->parameters["opacity_over_life"] = Parameter{Curve::linear(1.0f, 0.0f)};
    ps->parameters["emissive"] = Parameter{4.0f};
    ps->parameters["emissive_over_life"] = Parameter{Curve::linear(1.0f, 0.5f)};
    ps->parameters["color"] = Parameter{Color{1.0f, 1.0f, 1.0f, 1.0f}};
    ps->parameters["color_over_life"] =
        Parameter{Gradient{{0.0f, Color{1, 1, 1, 1}}, {1.0f, Color{0, 0.5f, 1, 1}}}};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    const float u = b.custom0[0];
    CHECK(u == Approx(b.age[0] / b.lifetime[0]));
    CHECK(u == Approx(0.5f).margin(0.02));
    CHECK(b.custom1[0] == 0.0f);
    CHECK(b.size[0] == Approx(2.0f * u).margin(1e-5));
    CHECK(b.opacity[0] == Approx(0.8f * (1.0f - u)).margin(1e-5));
    CHECK(b.emissive[0] == Approx(4.0f * (1.0f - 0.5f * u)).margin(1e-5));
    CHECK(b.color[0].r == Approx(1.0f - u).margin(1e-5));
    CHECK(b.color[0].g == Approx(1.0f - 0.5f * u).margin(1e-5));
    CHECK(b.color[0].b == Approx(1.0f).margin(1e-5));

    rt->simulate_to(0.99);
    const ParticleBuffer& late = buffer_of(*rt, "ps");
    REQUIRE(late.count() == 1u);
    CHECK(late.custom0[0] > 0.9f);
    CHECK(late.opacity[0] < 0.1f);
}

TEST_CASE("initial state comes from the system parameters", "[sim][modulation]") {
    Effect e = single_burst_effect(3.0f, Vec3{0, 1, 0}, Vec3{0, 1, 0}, 1);
    Node* ps = e.find_node("ps");
    ps->parameters["mass"] = Parameter{2.5f};
    ps->parameters["rotation"] = Parameter{90.0f};
    ps->parameters["angular_velocity"] = Parameter{180.0f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    CHECK(b.mass[0] == Approx(2.5f));
    CHECK(b.rotation[0] == Approx(deg_to_rad(90.0f) + deg_to_rad(180.0f) / 60.0f).margin(1e-5));
    CHECK(b.angular_velocity[0] == Approx(deg_to_rad(180.0f)));
    CHECK(b.position[0].y > 1.0f);  // moved along +Y at 3 m/s
    CHECK(b.previous_position[0].y == Approx(1.0f));
    CHECK(b.seed[0] != 0u);
}

TEST_CASE("emitter shapes stay inside their bounds", "[sim][emission]") {
    const auto spawn_with_shape = [](const std::string& shape, float radius, bool surface_only) {
        Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 200);
        Node* em = e.find_node("em");
        em->parameters["shape"] = Parameter{shape};
        em->parameters["radius"] = Parameter{radius};
        em->parameters["inner_radius"] = Parameter{radius * 0.5f};
        em->parameters["surface_only"] = Parameter{surface_only};
        e.find_node("ps")->parameters["max_particles"] = Parameter{400};
        std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
        rt->step();
        std::vector<Vec3> out = rt->state().find_particles("ps")->position;
        return out;
    };

    for (const Vec3& p : spawn_with_shape("sphere", 2.0f, false)) CHECK(length(p) <= 2.0f + 1e-4f);
    for (const Vec3& p : spawn_with_shape("sphere", 2.0f, true)) CHECK(length(p) == Approx(2.0f).margin(1e-4));
    for (const Vec3& p : spawn_with_shape("hemisphere", 1.0f, false)) {
        CHECK(length(p) <= 1.0f + 1e-4f);
        CHECK(p.y >= 0.0f);
    }
    for (const Vec3& p : spawn_with_shape("disc", 3.0f, false)) {
        CHECK(std::fabs(p.y) < 1e-5f);
        const float r = std::sqrt(p.x * p.x + p.z * p.z);
        CHECK(r <= 3.0f + 1e-4f);
        CHECK(r >= 1.5f - 1e-4f);
    }
    for (const Vec3& p : spawn_with_shape("box", 1.0f, false)) {
        CHECK(std::fabs(p.x) <= 0.5f + 1e-5f);
        CHECK(std::fabs(p.y) <= 0.5f + 1e-5f);
        CHECK(std::fabs(p.z) <= 0.5f + 1e-5f);
    }
    for (const Vec3& p : spawn_with_shape("line", 1.0f, false)) {
        CHECK(std::fabs(p.x) <= 0.5f + 1e-5f);
        CHECK(std::fabs(p.y) < 1e-6f);
    }
    for (const Vec3& p : spawn_with_shape("point", 1.0f, false)) CHECK(length(p) < 1e-6f);
}

TEST_CASE("emitter transforms place particles in world space", "[sim][emission]") {
    Effect e = single_burst_effect(1.0f, Vec3{0, 1, 0}, Vec3{5, 2, -1}, 16);
    e.find_node("em")->parameters["shape"] = Parameter{std::string("sphere")};
    e.find_node("em")->parameters["radius"] = Parameter{0.25f};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 16u);
    for (size_t i = 0; i < b.count(); ++i) {
        CHECK(distance(b.previous_position[i], Vec3{5, 2, -1}) <= 0.25f + 1e-4f);
        CHECK(b.velocity[i].y > 0.0f);  // direction +Y with no spread
    }
}

TEST_CASE("inherit_velocity adds the emitter's own motion", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 1);
    Node* em = e.find_node("em");
    Parameter moving{Vec3{0, 0, 0}};
    moving.set_keyframe(0.0, Vec3{0, 0, 0});
    moving.set_keyframe(1.0, Vec3{4, 0, 0});
    em->parameters["position"] = moving;
    em->parameters["inherit_velocity"] = Parameter{1.0f};
    em->parameters["burst_times"] = Parameter{std::vector<float>{0.25f}};
    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.3);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 1u);
    CHECK(b.velocity[0].x == Approx(4.0f).epsilon(0.05));
}

// ---------------------------------------------------------------------------
// remaining shapes, colliders and force overrides
// ---------------------------------------------------------------------------

TEST_CASE("an emitter can sample a mesh surface", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 128);
    Node mesh = node_of(NodeType::Mesh, "cube");
    mesh.parameters["primitive"] = Parameter{std::string("cube")};
    mesh.parameters["size"] = Parameter{Vec3{2, 2, 2}};
    mesh.parameters["visible"] = Parameter{false};
    e.add_node(mesh);
    Node* em = e.find_node("em");
    em->parameters["shape"] = Parameter{std::string("mesh")};
    em->inputs["shape_mesh"] = {NodeRef::parse("cube")};
    e.find_node("ps")->parameters["max_particles"] = Parameter{256};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 128u);
    bool spread_out = false;
    for (size_t i = 0; i < b.count(); ++i) {
        const Vec3 p = b.previous_position[i];
        const float extent = std::max(std::fabs(p.x), std::max(std::fabs(p.y), std::fabs(p.z)));
        CHECK(extent == Approx(1.0f).margin(1e-3));  // on the surface of the cube
        if (i > 0 && distance(p, b.previous_position[0]) > 0.5f) spread_out = true;
    }
    CHECK(spread_out);
}

TEST_CASE("an emitter can sample a curve", "[sim][emission]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 64);
    Node curve = node_of(NodeType::Curve, "path");
    curve.parameters["points"] = Parameter{std::vector<Vec3>{{-1, 0, 0}, {1, 0, 0}}};
    curve.parameters["curve_type"] = Parameter{std::string("linear")};
    e.add_node(curve);
    Node* em = e.find_node("em");
    em->parameters["shape"] = Parameter{std::string("curve")};
    em->inputs["shape_curve"] = {NodeRef::parse("path")};
    e.find_node("ps")->parameters["max_particles"] = Parameter{128};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 64u);
    float lowest = 1.0f, highest = -1.0f;
    for (size_t i = 0; i < b.count(); ++i) {
        const Vec3 p = b.previous_position[i];
        CHECK(std::fabs(p.y) < 1e-5f);
        CHECK(std::fabs(p.z) < 1e-5f);
        CHECK(p.x >= -1.0f - 1e-4f);
        CHECK(p.x <= 1.0f + 1e-4f);
        lowest = std::min(lowest, p.x);
        highest = std::max(highest, p.x);
    }
    CHECK(highest - lowest > 1.0f);
}

TEST_CASE("sphere, box and capsule colliders stop particles", "[sim][collision]") {
    const auto resting_height = [](const std::string& kind, float radius, float height, Vec3 size) {
        Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 5, 0});
        attach_force(e, "g", "gravity", 9.81f);
        Node collider = node_of(NodeType::Collider, "obstacle");
        collider.parameters["collider_type"] = Parameter{kind};
        collider.parameters["radius"] = Parameter{radius};
        collider.parameters["height"] = Parameter{height};
        collider.parameters["size"] = Parameter{size};
        collider.parameters["bounce"] = Parameter{0.0f};
        collider.parameters["friction"] = Parameter{1.0f};
        e.add_node(collider);
        e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("obstacle")};
        std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
        rt->simulate_to(3.0);
        const ParticleBuffer* b = rt->state().find_particles("ps");
        REQUIRE(b != nullptr);
        REQUIRE(b->count() == 1u);
        return b->position[0].y;
    };

    // collision radius = size * 0.5 = 0.01
    CHECK(resting_height("sphere", 1.0f, 1.0f, Vec3{1, 1, 1}) == Approx(1.01f).margin(0.02));
    CHECK(resting_height("box", 0.5f, 1.0f, Vec3{1, 1, 1}) == Approx(1.01f).margin(0.02));
    CHECK(resting_height("capsule", 0.5f, 2.0f, Vec3{1, 1, 1}) == Approx(1.51f).margin(0.02));
}

TEST_CASE("mesh colliders are ignored by the V1 runtime", "[sim][collision]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 2, 0});
    attach_force(e, "g", "gravity", 9.81f);
    Node mesh = node_of(NodeType::Mesh, "wall");
    mesh.parameters["visible"] = Parameter{false};
    e.add_node(mesh);
    Node collider = node_of(NodeType::Collider, "obstacle");
    collider.parameters["collider_type"] = Parameter{std::string("mesh")};
    collider.inputs["mesh"] = {NodeRef::parse("wall")};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("obstacle")};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(1.5);
    CHECK(buffer_of(*rt, "ps").position[0].y < 0.0f);  // fell straight through
    CHECK(system_stats(*rt, "ps").collisions == 0u);
}

TEST_CASE("a noise node overrides a force's built-in noise parameters", "[sim][forces]") {
    const auto simulate = [](bool with_override, const std::string& noise_type) {
        Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 32);
        Node f = node_of(NodeType::Force, "turb");
        f.parameters["force_type"] = Parameter{std::string("turbulence")};
        f.parameters["strength"] = Parameter{5.0f};
        f.parameters["frequency"] = Parameter{1.0f};
        f.parameters["octaves"] = Parameter{2};
        e.add_node(f);
        e.find_node("ps")->inputs["forces"] = {NodeRef::parse("turb")};
        if (with_override) {
            Node noise = node_of(NodeType::Noise, "n");
            noise.parameters["noise_type"] = Parameter{noise_type};
            noise.parameters["frequency"] = Parameter{4.0f};
            noise.parameters["octaves"] = Parameter{3};
            noise.parameters["amplitude"] = Parameter{1.0f};
            e.add_node(noise);
            e.find_node("turb")->inputs["noise"] = {NodeRef::parse("n")};
        }
        e.find_node("em")->parameters["shape"] = Parameter{std::string("sphere")};
        e.find_node("em")->parameters["radius"] = Parameter{1.0f};
        std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
        rt->simulate_to(0.5);
        return rt->state().find_particles("ps")->position;
    };

    const std::vector<Vec3> plain = simulate(false, "");
    const std::vector<Vec3> overridden = simulate(true, "worley");
    REQUIRE(plain.size() == overridden.size());
    bool differs = false;
    for (size_t i = 0; i < plain.size(); ++i)
        if (distance(plain[i], overridden[i]) > 1e-4f) differs = true;
    CHECK(differs);
    for (const Vec3& p : plain) {
        CHECK(std::isfinite(p.x));
        CHECK(length(p) < 100.0f);
    }
}

TEST_CASE("curl noise pushes particles around", "[sim][forces]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 16);
    Node f = node_of(NodeType::Force, "curl");
    f.parameters["force_type"] = Parameter{std::string("curl_noise")};
    f.parameters["strength"] = Parameter{4.0f};
    f.parameters["frequency"] = Parameter{2.0f};
    f.parameters["octaves"] = Parameter{2};
    f.parameters["speed"] = Parameter{1.0f};
    e.add_node(f);
    e.find_node("ps")->inputs["forces"] = {NodeRef::parse("curl")};
    e.find_node("em")->parameters["shape"] = Parameter{std::string("sphere")};
    e.find_node("em")->parameters["radius"] = Parameter{0.5f};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.5);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 16u);
    bool moved = false;
    for (size_t i = 0; i < b.count(); ++i) {
        CHECK(std::isfinite(b.position[i].x));
        if (length(b.velocity[i]) > 1e-3f) moved = true;
    }
    CHECK(moved);
}

TEST_CASE("an on_peak event fires at the start of the peak phase", "[sim][events]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 1);
    e.timeline.phases.push_back(TimelinePhase{"peak", 1.0, 2.0});
    Node ps2 = node_of(NodeType::ParticleSystem, "flash_ps");
    ps2.parameters["max_particles"] = Parameter{64};
    ps2.parameters["lifetime"] = Parameter{4.0f};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "flash");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{12};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.inputs["particle"] = {NodeRef::parse("flash_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "at_peak");
    ev.parameters["trigger"] = Parameter{std::string("on_peak")};
    ev.inputs["targets"] = {NodeRef::parse("flash")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(0.9);
    CHECK(system_stats(*rt, "flash_ps").spawned_total == 0u);
    rt->simulate_to(1.2);
    CHECK(system_stats(*rt, "flash_ps").spawned_total == 12u);
    rt->simulate_to(3.0);
    CHECK(system_stats(*rt, "flash_ps").spawned_total == 12u);
}

TEST_CASE("event probability uses a deterministic per-particle draw", "[sim][events]") {
    const auto spawned_with_probability = [](float probability) {
        Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 0, 0}, 200);
        e.find_node("ps")->parameters["max_particles"] = Parameter{400};
        Node ps2 = node_of(NodeType::ParticleSystem, "echo_ps");
        ps2.parameters["max_particles"] = Parameter{400};
        ps2.parameters["lifetime"] = Parameter{4.0f};
        e.add_node(ps2);
        Node em2 = node_of(NodeType::Emitter, "echo");
        em2.parameters["shape"] = Parameter{std::string("point")};
        em2.parameters["rate"] = Parameter{0.0f};
        em2.parameters["burst_count"] = Parameter{1};
        em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
        em2.inputs["particle"] = {NodeRef::parse("echo_ps")};
        e.add_node(em2);
        Node ev = node_of(NodeType::Event, "maybe");
        ev.parameters["trigger"] = Parameter{std::string("on_spawn")};
        ev.parameters["probability"] = Parameter{probability};
        ev.inputs["source"] = {NodeRef::parse("ps")};
        ev.inputs["targets"] = {NodeRef::parse("echo")};
        e.add_node(ev);
        std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
        rt->simulate_to(0.5);
        return system_stats(*rt, "echo_ps").spawned_total;
    };

    CHECK(spawned_with_probability(1.0f) == 200u);
    CHECK(spawned_with_probability(0.0f) == 0u);
    const size_t half = spawned_with_probability(0.5f);
    CHECK(half > 60u);
    CHECK(half < 140u);
    CHECK(spawned_with_probability(0.5f) == half);  // deterministic
}

// ---------------------------------------------------------------------------
// collision materials and resting contacts (docs/RUNTIME.md section 5)
// ---------------------------------------------------------------------------

namespace {

// Drops one particle from y=3 onto a plane and returns rebound_speed /
// impact_speed, which is exactly the combined bounce for a vertical drop.
float rebound_ratio(float system_bounce, float collider_bounce) {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 3, 0});
    attach_force(e, "g", "gravity", 9.81f);
    e.find_node("ps")->parameters["bounce"] = Parameter{system_bounce};
    e.find_node("ps")->parameters["friction"] = Parameter{0.0f};
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["bounce"] = Parameter{collider_bounce};
    collider.parameters["friction"] = Parameter{0.0f};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    float previous = 0.0f;
    for (int i = 0; i < 180; ++i) {
        rt->step();
        const ParticleBuffer* b = rt->state().find_particles("ps");
        REQUIRE(b != nullptr);
        REQUIRE(b->count() == 1u);
        const float vy = b->velocity[0].y;
        if (vy >= 0.0f && previous < 0.0f) {
            // the colliding step integrated one more dt of gravity first
            const float impact = -previous + 9.81f / 60.0f;
            return vy / impact;
        }
        previous = vy;
    }
    FAIL("the particle never reached the plane");
    return -1.0f;
}

}  // namespace

TEST_CASE("bounce and friction combine both nodes", "[sim][collision]") {
    // sqrt(system * collider): either side can zero the response
    CHECK(rebound_ratio(0.0f, 0.3f) == Approx(0.0f).margin(1e-4));
    CHECK(rebound_ratio(0.3f, 0.0f) == Approx(0.0f).margin(1e-4));
    CHECK(rebound_ratio(0.3f, 0.3f) == Approx(0.3f).margin(0.005));
    CHECK(rebound_ratio(1.0f, 0.25f) == Approx(0.5f).margin(0.005));
    CHECK(rebound_ratio(0.5f, 0.5f) == Approx(0.5f).margin(0.005));

    // friction combines the same way and damps the tangential component
    const auto tangential_after_impact = [](float system_friction, float collider_friction) {
        Effect e = single_burst_effect(2.0f, Vec3{1, 0, 0}, Vec3{0, 1, 0});
        attach_force(e, "g", "gravity", 9.81f);
        e.find_node("ps")->parameters["friction"] = Parameter{system_friction};
        e.find_node("ps")->parameters["bounce"] = Parameter{0.0f};
        Node collider = node_of(NodeType::Collider, "ground");
        collider.parameters["collider_type"] = Parameter{std::string("plane")};
        collider.parameters["friction"] = Parameter{collider_friction};
        collider.parameters["bounce"] = Parameter{0.0f};
        e.add_node(collider);
        e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};
        std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
        rt->simulate_to(1.0);
        return rt->state().find_particles("ps")->velocity[0].x;
    };
    CHECK(tangential_after_impact(0.0f, 0.8f) == Approx(2.0f).margin(1e-4));  // sqrt(0 * 0.8) = 0
    CHECK(tangential_after_impact(1.0f, 1.0f) == Approx(0.0f).margin(1e-4));
    const float half = tangential_after_impact(0.5f, 0.5f);
    CHECK(half > 0.0f);
    CHECK(half < 2.0f);
}

TEST_CASE("resting contacts do not re-fire every step", "[sim][collision]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 1, 0}, 1);
    attach_force(e, "g", "gravity", 9.81f);
    e.find_node("ps")->parameters["bounce"] = Parameter{0.0f};
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["bounce"] = Parameter{0.3f};
    collider.parameters["friction"] = Parameter{0.5f};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    float lowest = 1e9f;
    for (int i = 0; i < 120; ++i) {  // 2 s
        rt->step();
        const ParticleBuffer* b = rt->state().find_particles("ps");
        REQUIRE(b->count() == 1u);
        lowest = std::min(lowest, b->position[0].y);
    }
    const sim::SystemStatistics s = system_stats(*rt, "ps");
    CHECK(s.collisions >= 1u);
    CHECK(s.collisions <= 3u);  // one landing, not 120 steps of contact
    CHECK(lowest > -1e-3f);     // the push-out still runs every step
}

TEST_CASE("a bouncing particle reports each landing", "[sim][collision]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 3, 0}, 1);
    attach_force(e, "g", "gravity", 9.81f);
    e.find_node("ps")->parameters["bounce"] = Parameter{0.8f};
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["bounce"] = Parameter{0.8f};
    collider.parameters["friction"] = Parameter{0.0f};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(4.0);
    const sim::SystemStatistics s = system_stats(*rt, "ps");
    CHECK(s.collisions >= 3u);   // several real bounces
    CHECK(s.collisions <= 20u);  // but nothing like one per step (240 steps)
}

TEST_CASE("an on_collision burst fires once per landing", "[sim][events]") {
    Effect e = single_burst_effect(0.0f, Vec3{0, 1, 0}, Vec3{0, 2, 0}, 3);
    attach_force(e, "g", "gravity", 9.81f);
    e.find_node("ps")->parameters["bounce"] = Parameter{0.0f};  // land and rest
    Node collider = node_of(NodeType::Collider, "ground");
    collider.parameters["collider_type"] = Parameter{std::string("plane")};
    collider.parameters["bounce"] = Parameter{0.3f};
    collider.parameters["friction"] = Parameter{0.6f};
    e.add_node(collider);
    e.find_node("ps")->inputs["colliders"] = {NodeRef::parse("ground")};

    Node ps2 = node_of(NodeType::ParticleSystem, "dust_ps");
    ps2.parameters["max_particles"] = Parameter{500};
    ps2.parameters["lifetime"] = Parameter{5.0f};
    e.add_node(ps2);
    Node em2 = node_of(NodeType::Emitter, "dust");
    em2.parameters["shape"] = Parameter{std::string("point")};
    em2.parameters["rate"] = Parameter{0.0f};
    em2.parameters["burst_count"] = Parameter{2};
    em2.parameters["burst_times"] = Parameter{std::vector<float>{}};
    em2.inputs["particle"] = {NodeRef::parse("dust_ps")};
    e.add_node(em2);
    Node ev = node_of(NodeType::Event, "impact");
    ev.parameters["trigger"] = Parameter{std::string("on_collision")};
    ev.inputs["source"] = {NodeRef::parse("ps")};
    ev.inputs["targets"] = {NodeRef::parse("dust")};
    e.add_node(ev);

    std::unique_ptr<sim::IRuntime> rt = runtime_for(e);
    rt->simulate_to(2.0);
    CHECK(system_stats(*rt, "ps").collisions == 3u);        // one landing each
    CHECK(system_stats(*rt, "dust_ps").spawned_total == 6u);  // 3 landings x burst 2
    rt->simulate_to(4.0);
    CHECK(system_stats(*rt, "dust_ps").spawned_total == 6u);  // resting: no new bursts
}

TEST_CASE("lightning collision counts stay close to one per particle", "[sim][examples]") {
    std::unique_ptr<sim::IRuntime> rt = runtime_for(load_example("lightning_strike.json"));
    rt->simulate_to(1.2);
    const sim::SystemStatistics sparks = system_stats(*rt, "spark_ps");
    const sim::SystemStatistics debris = system_stats(*rt, "debris_ps");
    CHECK(sparks.spawned_total == 350u);
    CHECK(sparks.collisions > 100u);
    CHECK(sparks.collisions < 1000u);  // was one contact per resting particle per step
    CHECK(debris.collisions < 200u);
}

// ---------------------------------------------------------------------------
// mesh particle orientation (docs/VOCABULARY.md, "Mesh particle orientation")
// ---------------------------------------------------------------------------

namespace {

Vec3 quat_rotate(Vec4 q, Vec3 v) {
    const Vec3 u{q.x, q.y, q.z};
    const Vec3 t = cross(u, v) * 2.0f;
    return v + t * q.w + cross(u, t);
}

float quat_length(Vec4 q) { return std::sqrt(q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w); }

// Angle between a particle's mesh up axis and world up, in degrees.
float lean_degrees(Vec4 q) {
    return rad_to_deg(std::acos(clamp(dot(quat_rotate(q, Vec3::up()), Vec3::up()), -1.0f, 1.0f)));
}

// A burst of `count` mesh particles from a point emitter, spread over a cone so
// their velocities differ. `mesh` is wired to the system, so render_mode=mesh
// has something to instance.
Effect mesh_burst_effect(const std::string& orientation, int count = 48, float spread_deg = 60.0f) {
    Effect e;
    e.name = "mesh_orientation";
    e.duration = 5.0;
    e.seed = 12;

    Node geo = node_of(NodeType::Mesh, "geo");
    geo.parameters["primitive"] = Parameter{std::string("cone")};
    geo.parameters["radius"] = Parameter{0.2f};
    geo.parameters["height"] = Parameter{1.0f};
    geo.parameters["visible"] = Parameter{false};
    e.add_node(geo);

    Node ps = node_of(NodeType::ParticleSystem, "ps");
    ps.parameters["lifetime"] = Parameter{9.0f};
    ps.parameters["max_particles"] = Parameter{512};
    ps.parameters["size"] = Parameter{0.2f};
    ps.parameters["render_mode"] = Parameter{std::string("mesh")};
    ps.parameters["orientation"] = Parameter{orientation};
    ps.parameters["opacity_over_life"] = Parameter{Curve::constant(1.0f)};
    ps.inputs["mesh"] = {NodeRef::parse("geo")};
    e.add_node(ps);

    Node em = node_of(NodeType::Emitter, "em");
    em.parameters["shape"] = Parameter{std::string("point")};
    em.parameters["rate"] = Parameter{0.0f};
    em.parameters["burst_count"] = Parameter{count};
    em.parameters["burst_times"] = Parameter{std::vector<float>{0.0f}};
    em.parameters["velocity"] = Parameter{2.0f};
    em.parameters["direction"] = Parameter{Vec3{0, 1, 0}};
    em.parameters["spread"] = Parameter{spread_deg};
    em.inputs["particle"] = {NodeRef::parse("ps")};
    e.add_node(em);
    return e;
}

}  // namespace

TEST_CASE("mesh particles carry a unit orientation quaternion", "[sim][orientation]") {
    const Effect effect = mesh_burst_effect("upright");
    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 48);
    REQUIRE(b.orientation.size() == b.count());
    REQUIRE(b.scale3.size() == b.count());
    REQUIRE(b.variant.size() == b.count());
    for (size_t i = 0; i < b.count(); ++i) {
        CHECK(quat_length(b.orientation[i]) == Approx(1.0f).margin(1e-4));
        CHECK(b.scale3[i] == Vec3{1, 1, 1});
        CHECK(b.variant[i] == 0u);
    }
}

TEST_CASE("billboard systems leave the orientation at identity", "[sim][orientation]") {
    Effect effect = mesh_burst_effect("tumble");
    effect.find_node("ps")->parameters["render_mode"] = Parameter{std::string("billboard")};
    effect.find_node("ps")->parameters["angular_velocity"] = Parameter{180.0f};
    auto rt = runtime_for(effect);
    rt->simulate_to(0.5);
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() > 0);
    for (size_t i = 0; i < b.count(); ++i) {
        CHECK(b.orientation[i] == Vec4{0, 0, 0, 1});
        CHECK(b.scale3[i] == Vec3{1, 1, 1});
    }
    // `rotation` keeps its billboard roll meaning.
    CHECK(b.rotation[0] == Approx(deg_to_rad(180.0f) * 0.5f).margin(1e-4));
}

TEST_CASE("orientation: upright is yaw only until tilt is set", "[sim][orientation]") {
    SECTION("no tilt keeps every mesh perfectly upright") {
        Effect effect = mesh_burst_effect("upright");
        effect.find_node("ps")->parameters["rotation_variance"] = Parameter{180.0f};
        auto rt = runtime_for(effect);
        rt->reset();
        rt->step();
        const ParticleBuffer& b = buffer_of(*rt, "ps");
        bool any_yaw = false;
        for (size_t i = 0; i < b.count(); ++i) {
            CHECK(lean_degrees(b.orientation[i]) == Approx(0.0f).margin(1e-3));
            // the yaw is still there, folded into the quaternion
            const Vec3 side = quat_rotate(b.orientation[i], Vec3::right());
            if (std::fabs(side.x - 1.0f) > 1e-3f) any_yaw = true;
        }
        CHECK(any_yaw);
    }

    SECTION("tilt bounds the lean and spreads it over the cone") {
        const float tilt = 40.0f;
        Effect effect = mesh_burst_effect("upright", 200);
        effect.find_node("ps")->parameters["tilt"] = Parameter{tilt};
        auto rt = runtime_for(effect);
        rt->reset();
        rt->step();
        const ParticleBuffer& b = buffer_of(*rt, "ps");
        REQUIRE(b.count() == 200);
        float worst = 0.0f, smallest = 1e9f;
        for (size_t i = 0; i < b.count(); ++i) {
            const float lean = lean_degrees(b.orientation[i]);
            CHECK(lean <= tilt + 1e-2f);
            CHECK(lean >= -1e-3f);
            worst = std::max(worst, lean);
            smallest = std::min(smallest, lean);
        }
        CHECK(worst > tilt * 0.8f);      // the whole cone is used
        CHECK(smallest < tilt * 0.2f);
    }

    SECTION("angular_velocity keeps precessing a tilted mesh") {
        Effect effect = mesh_burst_effect("upright", 8);
        effect.find_node("ps")->parameters["tilt"] = Parameter{30.0f};
        effect.find_node("ps")->parameters["angular_velocity"] = Parameter{360.0f};
        auto rt = runtime_for(effect);
        rt->reset();
        rt->step();
        const Vec4 first = buffer_of(*rt, "ps").orientation[0];
        const float lean_before = lean_degrees(first);
        rt->step();
        rt->step();
        const Vec4 later = buffer_of(*rt, "ps").orientation[0];
        CHECK(!(later == first));
        // spinning about world up never changes how far the mesh leans
        CHECK(lean_degrees(later) == Approx(lean_before).margin(1e-3));
    }
}

TEST_CASE("orientation: random is a fixed per-particle rotation", "[sim][orientation]") {
    const Effect effect = mesh_burst_effect("random", 64);
    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const std::vector<Vec4> first = buffer_of(*rt, "ps").orientation;
    REQUIRE(first.size() == 64);

    // Different particles get different rotations, and they really are 3D
    // (the up axis is not stuck near +Y the way `upright` leaves it).
    size_t distinct = 0;
    float max_lean = 0.0f;
    for (size_t i = 0; i < first.size(); ++i) {
        max_lean = std::max(max_lean, lean_degrees(first[i]));
        bool unique = true;
        for (size_t j = 0; j < i; ++j)
            if (first[j] == first[i]) unique = false;
        if (unique) ++distinct;
    }
    CHECK(distinct == first.size());
    CHECK(max_lean > 120.0f);

    SECTION("it does not drift over time") {
        rt->simulate_to(1.0);
        const ParticleBuffer& b = buffer_of(*rt, "ps");
        for (size_t i = 0; i < b.count(); ++i) CHECK(b.orientation[i] == first[i]);
    }

    SECTION("it is identical across runs and changes with the effect seed") {
        auto again = runtime_for(effect);
        again->reset();
        again->step();
        CHECK(buffer_of(*again, "ps").orientation == first);

        Effect reseeded = effect;
        reseeded.seed += 1;
        auto other = runtime_for(reseeded);
        other->reset();
        other->step();
        CHECK(buffer_of(*other, "ps").orientation != first);
    }
}

TEST_CASE("orientation: velocity aims the mesh +Y along the velocity", "[sim][orientation]") {
    const Effect effect = mesh_burst_effect("velocity", 64, 80.0f);
    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 64);
    bool any_off_axis = false;
    for (size_t i = 0; i < b.count(); ++i) {
        const Vec3 aim = quat_rotate(b.orientation[i], Vec3::up());
        const Vec3 dir = normalize(b.velocity[i]);
        CHECK(dot(aim, dir) == Approx(1.0f).margin(1e-4));
        if (dot(dir, Vec3::up()) < 0.98f) any_off_axis = true;
    }
    CHECK(any_off_axis);  // the cone spread really did produce varied directions

    SECTION("a force turns the mesh with the velocity") {
        Effect falling = effect;
        attach_force(falling, "g", "gravity", 30.0f, Vec3{0, -1, 0});
        auto sim = runtime_for(falling);
        sim->simulate_to(1.0);
        const ParticleBuffer& fb = buffer_of(*sim, "ps");
        REQUIRE(fb.count() > 0);
        for (size_t i = 0; i < fb.count(); ++i) {
            const Vec3 aim = quat_rotate(fb.orientation[i], Vec3::up());
            CHECK(dot(aim, normalize(fb.velocity[i])) == Approx(1.0f).margin(1e-4));
        }
        CHECK(dot(quat_rotate(fb.orientation[0], Vec3::up()), Vec3::up()) < 0.0f);  // now falling
    }

    SECTION("slow particles fall back to upright") {
        Effect still = mesh_burst_effect("velocity", 8, 0.0f);
        still.find_node("em")->parameters["velocity"] = Parameter{0.0f};
        auto sim = runtime_for(still);
        sim->reset();
        sim->step();
        const ParticleBuffer& sb = buffer_of(*sim, "ps");
        for (size_t i = 0; i < sb.count(); ++i) CHECK(lean_degrees(sb.orientation[i]) == Approx(0.0f).margin(1e-3));
    }
}

TEST_CASE("orientation: tumble spins about a random per-particle axis", "[sim][orientation]") {
    Effect effect = mesh_burst_effect("tumble", 32);
    effect.find_node("ps")->parameters["angular_velocity"] = Parameter{270.0f};
    effect.find_node("ps")->parameters["angular_velocity_variance"] = Parameter{90.0f};
    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const std::vector<Vec4> first = buffer_of(*rt, "ps").orientation;
    REQUIRE(first.size() == 32);

    for (int i = 0; i < 12; ++i) rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    float worst_travel = 0.0f;
    for (size_t i = 0; i < b.count(); ++i) {
        CHECK(quat_length(b.orientation[i]) == Approx(1.0f).margin(1e-4));
        const Vec3 before = quat_rotate(first[i], Vec3::up());
        const Vec3 after = quat_rotate(b.orientation[i], Vec3::up());
        worst_travel = std::max(worst_travel, length(after - before));
    }
    CHECK(worst_travel > 0.2f);  // the meshes really turned in 3D

    SECTION("zero angular velocity is a fixed random orientation") {
        Effect frozen = mesh_burst_effect("tumble", 16);
        frozen.find_node("ps")->parameters["angular_velocity"] = Parameter{0.0f};
        auto sim = runtime_for(frozen);
        sim->reset();
        sim->step();
        const std::vector<Vec4> start = buffer_of(*sim, "ps").orientation;
        sim->simulate_to(1.0);
        CHECK(buffer_of(*sim, "ps").orientation == start);
    }

    SECTION("it is deterministic across runs") {
        auto again = runtime_for(effect);
        again->simulate_to(13.0 / 60.0);
        CHECK(buffer_of(*again, "ps").orientation == b.orientation);
    }
}

TEST_CASE("mesh_scale and mesh_scale_variance give per-axis scale", "[sim][orientation]") {
    Effect effect = mesh_burst_effect("upright", 128);
    effect.find_node("ps")->parameters["mesh_scale"] = Parameter{Vec3{0.4f, 2.5f, 0.4f}};
    effect.find_node("ps")->parameters["mesh_scale_variance"] = Parameter{Vec3{0.2f, 0.8f, 0.2f}};
    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 128);

    bool axes_differ = false, particles_differ = false;
    for (size_t i = 0; i < b.count(); ++i) {
        const Vec3 s = b.scale3[i];
        CHECK(s.x >= 0.4f - 0.2f - 1e-5f);
        CHECK(s.x <= 0.4f + 0.2f + 1e-5f);
        CHECK(s.y >= 2.5f - 0.8f - 1e-5f);
        CHECK(s.y <= 2.5f + 0.8f + 1e-5f);
        if (std::fabs(s.x - s.y) > 1e-4f && std::fabs(s.x - s.z) > 1e-4f) axes_differ = true;
        if (i > 0 && !(b.scale3[i] == b.scale3[0])) particles_differ = true;
    }
    CHECK(axes_differ);
    CHECK(particles_differ);

    SECTION("scale never collapses below the 0.05 floor") {
        Effect crushed = mesh_burst_effect("upright", 64);
        crushed.find_node("ps")->parameters["mesh_scale"] = Parameter{Vec3{0.1f, 0.1f, 0.1f}};
        crushed.find_node("ps")->parameters["mesh_scale_variance"] = Parameter{Vec3{2.0f, 2.0f, 2.0f}};
        auto sim = runtime_for(crushed);
        sim->reset();
        sim->step();
        const ParticleBuffer& cb = buffer_of(*sim, "ps");
        for (size_t i = 0; i < cb.count(); ++i) {
            CHECK(cb.scale3[i].x >= 0.05f);
            CHECK(cb.scale3[i].y >= 0.05f);
            CHECK(cb.scale3[i].z >= 0.05f);
        }
    }

    SECTION("it survives compaction alongside the other arrays") {
        Effect dying = mesh_burst_effect("random", 64);
        dying.find_node("ps")->parameters["lifetime"] = Parameter{0.2f};
        dying.find_node("ps")->parameters["lifetime_variance"] = Parameter{0.15f};
        dying.find_node("ps")->parameters["mesh_scale_variance"] = Parameter{Vec3{0.5f, 0.5f, 0.5f}};
        dying.find_node("em")->parameters["rate"] = Parameter{240.0f};
        auto sim = runtime_for(dying);
        sim->simulate_to(0.6);
        const ParticleBuffer& db = buffer_of(*sim, "ps");
        REQUIRE(db.count() > 0);
        CHECK(db.orientation.size() == db.count());
        CHECK(db.scale3.size() == db.count());
        CHECK(db.variant.size() == db.count());
        // every surviving particle still has its own (valid) orientation
        for (size_t i = 0; i < db.count(); ++i) CHECK(quat_length(db.orientation[i]) == Approx(1.0f).margin(1e-4));
    }
}

TEST_CASE("mesh variant indices span the baked variants", "[sim][orientation][variants]") {
    Effect effect = mesh_burst_effect("random", 256);
    Node* geo = effect.find_node("geo");
    geo->parameters["primitive"] = Parameter{std::string("crystal")};
    geo->parameters["variants"] = Parameter{4};
    geo->parameters["segments"] = Parameter{6};

    auto rt = runtime_for(effect);
    rt->reset();
    rt->step();
    const ParticleBuffer& b = buffer_of(*rt, "ps");
    REQUIRE(b.count() == 256);

    std::vector<int> seen(4, 0);
    for (size_t i = 0; i < b.count(); ++i) {
        REQUIRE(b.variant[i] < 4u);
        ++seen[b.variant[i]];
    }
    for (int k = 0; k < 4; ++k) {
        INFO("variant " << k << " used " << seen[k] << " times");
        CHECK(seen[k] > 0);
    }

    SECTION("a single-variant mesh always uses variant 0") {
        Effect single = effect;
        single.find_node("geo")->parameters["variants"] = Parameter{1};
        auto sim = runtime_for(single);
        sim->reset();
        sim->step();
        const ParticleBuffer& sb = buffer_of(*sim, "ps");
        for (size_t i = 0; i < sb.count(); ++i) CHECK(sb.variant[i] == 0u);
    }
}

TEST_CASE("orientation does not disturb the billboard determinism contract", "[sim][orientation][determinism]") {
    // Particle state for a billboard system must be bit-identical between two
    // fresh runtimes; the orientation draws happen after every existing draw.
    const Effect effect = load_example("fireball.json");
    auto a = runtime_for(effect);
    auto b = runtime_for(effect);
    for (int i = 0; i < 60; ++i) {
        a->step();
        b->step();
        REQUIRE(a->state().hash() == b->state().hash());
    }
}

// docs/RUNTIME.md section 7 "volume" + docs/VOLUMES.md.
TEST_CASE("volume: procedural state is resolved inside the window", "[sim][volume]") {
    Effect e;
    e.name = "volume";
    e.duration = 4.0;
    e.seed = 11;
    Node v = node_of(NodeType::Volume, "cloud");
    v.parameters["mode"] = Parameter{std::string("procedural")};
    v.parameters["shape"] = Parameter{std::string("column")};
    v.parameters["position"] = Parameter{Vec3{1.0f, 2.0f, -3.0f}};
    v.parameters["radius"] = Parameter{1.25f};
    v.parameters["height"] = Parameter{3.0f};
    v.parameters["emission"] = Parameter{2.5f};
    v.parameters["color"] = Parameter{Color{0.2f, 0.4f, 0.9f, 1.0f}};
    v.parameters["color_hot"] = Parameter{Color{1.0f, 0.8f, 0.2f, 1.0f}};
    v.parameters["filament_scale"] = Parameter{3.5f};
    v.parameters["strands"] = Parameter{0.8f};
    v.parameters["carve"] = Parameter{0.3f};
    v.parameters["softness"] = Parameter{0.4f};
    v.parameters["spiral_arms"] = Parameter{5};
    v.parameters["arm_sharpness"] = Parameter{2.5f};
    v.parameters["twist"] = Parameter{0.7f};
    v.parameters["spin"] = Parameter{0.25f};
    v.parameters["climb"] = Parameter{1.5f};
    v.parameters["scatter"] = Parameter{0.9f};
    v.parameters["march_steps"] = Parameter{64};
    v.parameters["start_time"] = Parameter{1.0f};
    v.parameters["duration"] = Parameter{1.0f};
    // Animated: the runtime must read it at t1, not at its constant value.
    Parameter density{0.0f};
    density.set_keyframe(0.0, Value(0.0f));
    density.set_keyframe(1.0, Value(1.0f));
    density.set_keyframe(2.0, Value(3.0f));
    v.parameters["density"] = density;
    e.add_node(v);

    auto runtime = runtime_for(e);
    runtime->simulate_to(0.5);
    CHECK(runtime->state().volumes.empty());  // before the window

    runtime->simulate_to(1.5);
    REQUIRE(runtime->state().volumes.size() == 1);
    const VolumeState& s = runtime->state().volumes.front();
    CHECK(s.id == "cloud");
    CHECK(s.mode == "procedural");
    CHECK(s.shape == "column");
    CHECK(s.backend == "procedural_volume");
    CHECK_THAT(s.radius, WithinAbs(1.25, 1e-5));
    CHECK_THAT(s.height, WithinAbs(3.0, 1e-5));
    CHECK_THAT(s.emission, WithinAbs(2.5, 1e-5));
    CHECK_THAT(s.color.b, WithinAbs(0.9, 1e-5));
    CHECK_THAT(s.color_hot.r, WithinAbs(1.0, 1e-5));
    CHECK_THAT(s.filament_scale, WithinAbs(3.5, 1e-5));
    CHECK_THAT(s.strands, WithinAbs(0.8, 1e-5));
    CHECK_THAT(s.carve, WithinAbs(0.3, 1e-5));
    CHECK_THAT(s.softness, WithinAbs(0.4, 1e-5));
    CHECK(s.spiral_arms == 5);
    CHECK_THAT(s.arm_sharpness, WithinAbs(2.5, 1e-5));
    CHECK_THAT(s.twist, WithinAbs(0.7, 1e-5));
    CHECK_THAT(s.spin, WithinAbs(0.25, 1e-5));
    CHECK_THAT(s.climb, WithinAbs(1.5, 1e-5));
    CHECK_THAT(s.scatter, WithinAbs(0.9, 1e-5));
    CHECK(s.march_steps == 64);
    // Animated density, sampled at the step's t1 (1.5 -> halfway from 1 to 3).
    CHECK_THAT(s.density, WithinAbs(2.0, 1e-3));
    CHECK_THAT(static_cast<double>(s.time), WithinAbs(1.5, 1e-3));

    // Transform and bounds: a column of radius 1.25, height 3 around (1, 2, -3).
    CHECK_THAT(s.transform.at(0, 3), WithinAbs(1.0, 1e-5));
    CHECK_THAT(s.transform.at(1, 3), WithinAbs(2.0, 1e-5));
    CHECK_THAT(s.bounds_min.x, WithinAbs(-0.25, 1e-4));
    CHECK_THAT(s.bounds_max.x, WithinAbs(2.25, 1e-4));
    CHECK_THAT(s.bounds_min.y, WithinAbs(0.5, 1e-4));
    CHECK_THAT(s.bounds_max.y, WithinAbs(3.5, 1e-4));

    runtime->simulate_to(2.5);
    CHECK(runtime->state().volumes.empty());  // after the window
}

TEST_CASE("volume: mode simulation stays the stub", "[sim][volume]") {
    Effect e;
    e.name = "volume";
    e.duration = 2.0;
    e.seed = 3;
    Node v = node_of(NodeType::Volume, "smoke");
    v.parameters["mode"] = Parameter{std::string("simulation")};
    v.parameters["bounds"] = Parameter{Vec3{4.0f, 6.0f, 4.0f}};
    v.parameters["density"] = Parameter{0.75f};
    v.parameters["temperature"] = Parameter{900.0f};
    // Procedural-only parameters are set but must be ignored in simulation mode.
    v.parameters["shape"] = Parameter{std::string("ring")};
    v.parameters["radius"] = Parameter{9.0f};
    e.add_node(v);

    auto runtime = runtime_for(e);
    runtime->simulate_to(0.5);
    REQUIRE(runtime->state().volumes.size() == 1);
    const VolumeState& s = runtime->state().volumes.front();
    CHECK(s.mode == "simulation");
    CHECK(s.backend == "volume_stub");
    CHECK_THAT(s.density, WithinAbs(0.75, 1e-5));
    CHECK_THAT(s.temperature, WithinAbs(900.0, 1e-3));
    CHECK(s.shape == "sphere");  // untouched default, not the authored "ring"
    // Bounds come from the authored simulation domain, not from `radius`.
    CHECK_THAT(s.bounds_min.y, WithinAbs(-3.0, 1e-4));
    CHECK_THAT(s.bounds_max.y, WithinAbs(3.0, 1e-4));
}

TEST_CASE("volume: the void_nebula example emits its procedural volume", "[sim][volume][examples]") {
    auto runtime = runtime_for(load_example("void_nebula.json"));
    runtime->simulate_to(1.2);
    const FrameState& state = runtime->state();
    REQUIRE(state.volumes.size() == 1);
    const VolumeState& v = state.volumes.front();
    CHECK(v.id == "void_core");
    CHECK(v.mode == "procedural");
    CHECK(v.shape == "nebula");
    CHECK(v.backend == "procedural_volume");
    CHECK(v.spiral_arms == 3);
    CHECK(v.density > 0.0f);
    CHECK(v.bounds_max.y > v.bounds_min.y);
}
