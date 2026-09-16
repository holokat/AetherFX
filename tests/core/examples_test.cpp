// The three reference documents in examples/effects are the acceptance target
// for core: they must load, validate with zero errors, round-trip and hash
// stably.
#include <filesystem>
#include <fstream>
#include <iostream>
#include <set>
#include <string>
#include <vector>

#include <catch2/catch_approx.hpp>
#include <catch2/catch_test_macros.hpp>

#include "aether/core/serialization.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"

using namespace aether;
using Catch::Approx;

namespace {

std::filesystem::path example_path(const std::string& name) {
    return std::filesystem::path(AETHER_SOURCE_DIR) / "examples" / "effects" / name;
}

const std::vector<std::string>& example_names() {
    static const std::vector<std::string> names{"fireball.json", "fire_aoe.json", "lightning_strike.json"};
    return names;
}

nlohmann::json read_json(const std::filesystem::path& path) {
    std::ifstream in(path);
    REQUIRE(in.good());
    nlohmann::json j;
    in >> j;
    return j;
}

}  // namespace

TEST_CASE("examples load and validate with zero errors", "[core][examples]") {
    for (const std::string& name : example_names()) {
        CAPTURE(name);
        Effect effect = load_effect_file(example_path(name));
        REQUIRE_FALSE(effect.nodes.empty());
        REQUIRE(effect.duration > 0.0);
        REQUIRE(effect.schema_version == kSchemaVersion);

        Diagnostics diagnostics = validate(effect);
        if (!diagnostics.ok() || diagnostics.warning_count() > 0)
            std::cout << "[" << name << "]\n" << diagnostics.summary();
        INFO(diagnostics.summary());
        CHECK(diagnostics.error_count() == 0);
        CHECK(diagnostics.ok());
    }
}

TEST_CASE("examples round-trip through json", "[core][examples]") {
    for (const std::string& name : example_names()) {
        CAPTURE(name);
        const nlohmann::json source = read_json(example_path(name));
        Effect first = effect_from_json(source);
        Effect second = effect_from_json(effect_to_json(first));
        CHECK(effect_to_canonical_string(first) == effect_to_canonical_string(second));
        CHECK(effect_hash(first) == effect_hash(second));

        // A third pass must be a fixed point too.
        Effect third = effect_from_json(effect_to_json(second));
        CHECK(effect_to_canonical_string(second) == effect_to_canonical_string(third));

        // Node/layer/phase structure survives the trip.
        REQUIRE(first.nodes.size() == second.nodes.size());
        for (size_t i = 0; i < first.nodes.size(); ++i) {
            CHECK(first.nodes[i].id == second.nodes[i].id);
            CHECK(first.nodes[i].type == second.nodes[i].type);
            CHECK(first.nodes[i].parameters.size() == second.nodes[i].parameters.size());
            CHECK(first.nodes[i].inputs == second.nodes[i].inputs);
            CHECK(first.nodes[i].parent == second.nodes[i].parent);
            CHECK(first.nodes[i].layer == second.nodes[i].layer);
            for (const auto& [param_name, parameter] : first.nodes[i].parameters) {
                const Parameter* other = second.nodes[i].find_param(param_name);
                REQUIRE(other != nullptr);
                CHECK(parameter == *other);
            }
        }
        CHECK(first.layers.size() == second.layers.size());
        CHECK(first.timeline.phases.size() == second.timeline.phases.size());
    }
}

TEST_CASE("example hashes are stable across loads", "[core][examples]") {
    for (const std::string& name : example_names()) {
        CAPTURE(name);
        Effect a = load_effect_file(example_path(name));
        Effect b = load_effect_file(example_path(name));
        CHECK(effect_hash(a) == effect_hash(b));
        CHECK(effect_to_canonical_string(a) == effect_to_canonical_string(b));
        // A change to any field changes the hash.
        Effect c = a;
        c.duration += 0.5;
        CHECK(effect_hash(c) != effect_hash(a));
    }
}

TEST_CASE("examples survive a save/load cycle on disk", "[core][examples]") {
    const std::filesystem::path out_dir = std::filesystem::path(AETHER_TEST_OUTPUT_DIR);
    std::filesystem::create_directories(out_dir);
    for (const std::string& name : example_names()) {
        CAPTURE(name);
        Effect original = load_effect_file(example_path(name));
        const std::filesystem::path out = out_dir / ("roundtrip_" + name);
        save_effect_file(original, out);
        Effect reloaded = load_effect_file(out);
        CHECK(effect_hash(original) == effect_hash(reloaded));
        CHECK(validate(reloaded).ok());
    }
}

TEST_CASE("topological_order on fireball respects dependencies", "[core][examples][validation]") {
    Effect effect = load_effect_file(example_path("fireball.json"));
    std::vector<NodeId> order = topological_order(effect);
    REQUIRE(order.size() == effect.nodes.size());

    std::set<NodeId> placed;
    for (const NodeId& id : order) {
        const Node* node = effect.find_node(id);
        REQUIRE(node != nullptr);
        for (const auto& [port, refs] : node->inputs) {
            CAPTURE(id, port);
            for (const NodeRef& r : refs) CHECK(placed.count(r.node) == 1);
        }
        if (node->parent) {
            CAPTURE(id);
            CHECK(placed.count(*node->parent) == 1);
        }
        placed.insert(id);
    }

    // Deterministic across calls and independent of a fresh load.
    CHECK(topological_order(effect) == order);
    CHECK(topological_order(load_effect_file(example_path("fireball.json"))) == order);

    // Spot check: a material must come before the system that consumes it.
    auto index_of = [&order](const std::string& id) {
        return std::distance(order.begin(), std::find(order.begin(), order.end(), id));
    };
    CHECK(index_of("mat_flame") < index_of("flame_ps"));
    CHECK(index_of("flame_ps") < index_of("flames"));
    CHECK(index_of("core") < index_of("flames"));  // parent before child
}

TEST_CASE("topological_order covers every example", "[core][examples][validation]") {
    for (const std::string& name : example_names()) {
        CAPTURE(name);
        Effect effect = load_effect_file(example_path(name));
        std::vector<NodeId> order = topological_order(effect);
        CHECK(order.size() == effect.nodes.size());
        std::set<NodeId> unique(order.begin(), order.end());
        CHECK(unique.size() == order.size());
    }
}

TEST_CASE("world_transform follows a keyframed parent in fireball", "[core][examples][effect]") {
    Effect effect = load_effect_file(example_path("fireball.json"));
    const Node* flames = effect.find_node("flames");
    REQUIRE(flames != nullptr);
    REQUIRE(flames->parent.has_value());

    // "core" carries a keyframed position track; "flames" sits at the origin of
    // its parent, so it must follow the track's first and last keys.
    const Node* core = effect.find_node("core");
    REQUIRE(core != nullptr);
    const Parameter* track = core->find_param("position");
    REQUIRE(track != nullptr);
    REQUIRE(track->animated());
    const Vec3 first = value_as_vec3(track->track.front().value);
    const Vec3 last = value_as_vec3(track->track.back().value);
    REQUIRE(first.z != last.z);
    Vec3 at_zero = effect.world_transform(*flames, track->track.front().time).translation_part();
    Vec3 at_end = effect.world_transform(*flames, track->track.back().time).translation_part();
    CHECK(at_zero.z == Approx(first.z).margin(1e-4f));
    CHECK(at_end.z == Approx(last.z).margin(1e-4f));
    CHECK(at_zero.y == Approx(first.y).margin(1e-4f));

    const double mid_time = 0.5 * (track->track.front().time + track->track.back().time);
    Vec3 midway = effect.world_transform(*flames, mid_time).translation_part();
    CHECK(midway.z == Approx(0.5f * (first.z + last.z)).margin(1e-3f));
}
