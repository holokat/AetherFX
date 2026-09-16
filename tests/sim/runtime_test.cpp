// CPU runtime: determinism contract (docs/RUNTIME.md section 10), integration
// and forces (4, 5), emission (3), events (8) and over-life modulation (6).
#include <cmath>
#include <filesystem>
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
