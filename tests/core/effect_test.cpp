// Graph structure: references, lookups, mutation and transform composition.
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/core/effect.hpp"
#include "aether/core/error.hpp"
#include "aether/core/spec.hpp"

using namespace aether;
using Catch::Matchers::WithinAbs;

namespace {

Node make_node(const std::string& id, NodeType type) {
    Node n;
    n.id = id;
    n.type = type;
    return n;
}

}  // namespace

TEST_CASE("NodeRef parses node and port", "[core][effect]") {
    NodeRef plain = NodeRef::parse("flame_ps");
    CHECK(plain.node == "flame_ps");
    CHECK(plain.port.empty());
    CHECK(plain.str() == "flame_ps");

    NodeRef ported = NodeRef::parse("sparks.on_death");
    CHECK(ported.node == "sparks");
    CHECK(ported.port == "on_death");
    CHECK(ported.str() == "sparks.on_death");

    CHECK(NodeRef::parse("a.b.c").node == "a");
    CHECK(NodeRef::parse("a.b.c").port == "b.c");  // first dot separates
    CHECK(NodeRef::parse("") == NodeRef{});
    CHECK(NodeRef::parse("a") == NodeRef{"a", ""});
}

TEST_CASE("node parameter and port lookups", "[core][effect]") {
    Node n = make_node("ps", NodeType::ParticleSystem);
    CHECK_FALSE(n.has_param("size"));
    CHECK(n.find_param("size") == nullptr);
    n.parameters["size"] = Parameter{Value(0.5f)};
    CHECK(n.has_param("size"));
    REQUIRE(n.find_param("size") != nullptr);
    CHECK(std::get<float>(n.find_param("size")->value) == 0.5f);
    const Node& const_node = n;
    CHECK(const_node.find_param("size") != nullptr);

    CHECK_FALSE(n.input("forces").has_value());
    CHECK(n.inputs_on("forces") == nullptr);
    n.inputs["forces"] = {NodeRef{"gravity", ""}, NodeRef{"wind", ""}};
    REQUIRE(n.input("forces").has_value());
    CHECK(n.input("forces")->node == "gravity");
    REQUIRE(n.inputs_on("forces") != nullptr);
    CHECK(n.inputs_on("forces")->size() == 2);
}

TEST_CASE("timeline phase lookup", "[core][effect]") {
    Timeline timeline;
    timeline.phases.push_back({"activation", 0.0, 0.5});
    timeline.phases.push_back({"decay", 0.5, 2.0});
    REQUIRE(timeline.find("decay") != nullptr);
    CHECK(timeline.find("decay")->start == 0.5);
    CHECK(timeline.find("nope") == nullptr);
    timeline.find("decay")->end = 3.0;
    CHECK(timeline.find("decay")->end == 3.0);
    const Timeline& const_timeline = timeline;
    CHECK(const_timeline.find("activation") != nullptr);
}

TEST_CASE("effect add_node rejects duplicates with E001", "[core][effect]") {
    Effect e;
    e.add_node(make_node("a", NodeType::Force));
    CHECK(e.has_node("a"));
    CHECK(e.find_node("a") != nullptr);
    CHECK(e.find_node("b") == nullptr);
    try {
        e.add_node(make_node("a", NodeType::Force));
        FAIL("expected E001");
    } catch (const Error& err) {
        CHECK(err.code() == "E001");
    }
    CHECK(e.nodes.size() == 1);
}

TEST_CASE("effect lookups by layer and type", "[core][effect]") {
    Effect e;
    e.layers.push_back({"main", "Main", LayerRole::Primary, true, nlohmann::json::object()});
    Node a = make_node("a", NodeType::Force);
    a.layer = "main";
    Node b = make_node("b", NodeType::Force);
    Node c = make_node("c", NodeType::Emitter);
    c.layer = "main";
    e.add_node(a);
    e.add_node(b);
    e.add_node(c);

    REQUIRE(e.find_layer("main") != nullptr);
    CHECK(e.find_layer("main")->role == LayerRole::Primary);
    CHECK(e.find_layer("other") == nullptr);
    CHECK(e.nodes_in_layer("main").size() == 2);
    CHECK(e.nodes_of_type(NodeType::Force).size() == 2);
    CHECK(e.nodes_of_type(NodeType::Emitter).size() == 1);
    CHECK(e.nodes_of_type(NodeType::Volume).empty());
    e.find_layer("main")->enabled = false;
    CHECK_FALSE(e.find_layer("main")->enabled);
}

TEST_CASE("consumers_of finds input and parent references", "[core][effect]") {
    Effect e;
    e.add_node(make_node("ps", NodeType::ParticleSystem));
    e.add_node(make_node("mat", NodeType::Material));
    Node em = make_node("em", NodeType::Emitter);
    em.inputs["particle"] = {NodeRef{"ps", ""}};
    em.parent = "core";
    e.add_node(em);
    e.add_node(make_node("core", NodeType::Mesh));
    Node ps2 = make_node("ps2", NodeType::ParticleSystem);
    ps2.inputs["material"] = {NodeRef{"mat", ""}};
    e.add_node(ps2);

    REQUIRE(e.consumers_of("ps").size() == 1);
    CHECK(e.consumers_of("ps")[0]->id == "em");
    REQUIRE(e.consumers_of("core").size() == 1);
    CHECK(e.consumers_of("core")[0]->id == "em");  // parent counts as a consumer
    CHECK(e.consumers_of("mat").size() == 1);
    CHECK(e.consumers_of("em").empty());
}

TEST_CASE("remove_node strips every reference to it", "[core][effect]") {
    Effect e;
    e.add_node(make_node("gravity", NodeType::Force));
    e.add_node(make_node("wind", NodeType::Force));
    e.add_node(make_node("core", NodeType::Mesh));
    Node ps = make_node("ps", NodeType::ParticleSystem);
    ps.inputs["forces"] = {NodeRef{"gravity", ""}, NodeRef{"wind", ""}};
    ps.inputs["material"] = {NodeRef{"mat", ""}};
    e.add_node(ps);
    Node em = make_node("em", NodeType::Emitter);
    em.inputs["particle"] = {NodeRef{"ps", ""}};
    em.parent = "core";
    e.add_node(em);

    CHECK_FALSE(e.remove_node("missing"));
    REQUIRE(e.remove_node("gravity"));
    CHECK(e.find_node("gravity") == nullptr);
    REQUIRE(e.find_node("ps")->inputs_on("forces") != nullptr);
    CHECK(e.find_node("ps")->inputs_on("forces")->size() == 1);  // only wind remains
    CHECK(e.find_node("ps")->inputs_on("forces")->front().node == "wind");

    // emptying a port removes the port entry entirely
    REQUIRE(e.remove_node("wind"));
    CHECK(e.find_node("ps")->inputs_on("forces") == nullptr);
    CHECK(e.find_node("ps")->inputs.count("material") == 1);

    // removing a parent clears the child's parent field
    REQUIRE(e.remove_node("core"));
    CHECK_FALSE(e.find_node("em")->parent.has_value());

    REQUIRE(e.remove_node("ps"));
    CHECK(e.find_node("em")->inputs.empty());
    CHECK(e.nodes.size() == 1);
}

TEST_CASE("unique_id generates free ids", "[core][effect]") {
    Effect e;
    CHECK(e.unique_id("flame") == "flame");
    e.add_node(make_node("flame", NodeType::Emitter));
    CHECK(e.unique_id("flame") == "flame_2");
    e.add_node(make_node("flame_2", NodeType::Emitter));
    CHECK(e.unique_id("flame") == "flame_3");
    e.add_node(make_node("flame_3", NodeType::Emitter));
    CHECK(e.unique_id("flame") == "flame_4");
    CHECK(e.unique_id("smoke") == "smoke");
    CHECK(e.unique_id("") == "node");
}

TEST_CASE("world_transform composes a parent chain", "[core][effect]") {
    Effect e;
    Node root = make_node("root", NodeType::Mesh);
    root.parameters["position"] = Parameter{Value(Vec3{1, 0, 0})};
    e.add_node(root);

    Node child = make_node("child", NodeType::Emitter);
    child.parent = "root";
    child.parameters["position"] = Parameter{Value(Vec3{0, 2, 0})};
    e.add_node(child);

    Node grandchild = make_node("grandchild", NodeType::Light);
    grandchild.parent = "child";
    grandchild.parameters["position"] = Parameter{Value(Vec3{0, 0, 3})};
    e.add_node(grandchild);

    CHECK(e.world_transform(*e.find_node("root"), 0.0).translation_part() == Vec3{1, 0, 0});
    CHECK(e.world_transform(*e.find_node("child"), 0.0).translation_part() == Vec3{1, 2, 0});
    CHECK(e.world_transform(*e.find_node("grandchild"), 0.0).translation_part() == Vec3{1, 2, 3});

    // rotation and scale of a parent apply to the child offset
    e.find_node("root")->parameters["rotation"] = Parameter{Value(Vec3{0, 90, 0})};
    Vec3 rotated = e.world_transform(*e.find_node("child"), 0.0).translation_part();
    CHECK_THAT(rotated.y, WithinAbs(2.0, 1e-5));
    e.find_node("root")->parameters["rotation"] = Parameter{Value(Vec3{0, 0, 0})};
    e.find_node("root")->parameters["scale"] = Parameter{Value(Vec3{2, 2, 2})};
    CHECK(e.world_transform(*e.find_node("child"), 0.0).translation_part() == Vec3{1, 4, 0});
}

TEST_CASE("world_transform evaluates a keyframed parent at time t", "[core][effect]") {
    Effect e;
    e.duration = 2.0;
    Node parent = make_node("parent", NodeType::Mesh);
    Parameter moving{Value(Vec3{0, 0, 0})};
    moving.set_keyframe(0.0, Value(Vec3{0, 0, 0}));
    moving.set_keyframe(2.0, Value(Vec3{0, 0, 10}));
    parent.parameters["position"] = moving;
    e.add_node(parent);

    Node child = make_node("child", NodeType::Emitter);
    child.parent = "parent";
    child.parameters["position"] = Parameter{Value(Vec3{1, 0, 0})};
    e.add_node(child);

    CHECK(e.world_transform(*e.find_node("child"), 0.0).translation_part() == Vec3{1, 0, 0});
    Vec3 mid = e.world_transform(*e.find_node("child"), 1.0).translation_part();
    CHECK_THAT(mid.z, WithinAbs(5.0, 1e-5));
    CHECK_THAT(mid.x, WithinAbs(1.0, 1e-6));
    CHECK_THAT(e.world_transform(*e.find_node("child"), 2.0).translation_part().z, WithinAbs(10.0, 1e-6));
    CHECK_THAT(e.world_transform(*e.find_node("child"), 9.0).translation_part().z, WithinAbs(10.0, 1e-6));  // clamped
}

TEST_CASE("world_transform is identity for non-spatial nodes and cycle safe", "[core][effect]") {
    Effect e;
    Node material = make_node("mat", NodeType::Material);
    e.add_node(material);
    CHECK(e.world_transform(*e.find_node("mat"), 0.0) == Mat4::identity());

    // a non-spatial parent contributes identity but does not break the chain
    Node ps = make_node("ps", NodeType::ParticleSystem);
    e.add_node(ps);
    Node child = make_node("child", NodeType::Emitter);
    child.parent = "ps";
    child.parameters["position"] = Parameter{Value(Vec3{0, 5, 0})};
    e.add_node(child);
    CHECK(e.world_transform(*e.find_node("child"), 0.0).translation_part() == Vec3{0, 5, 0});

    // a parent cycle must terminate rather than spin
    Node a = make_node("a", NodeType::Mesh);
    a.parent = "b";
    a.parameters["position"] = Parameter{Value(Vec3{1, 0, 0})};
    Node b = make_node("b", NodeType::Mesh);
    b.parent = "a";
    b.parameters["position"] = Parameter{Value(Vec3{0, 1, 0})};
    e.add_node(a);
    e.add_node(b);
    Mat4 m = e.world_transform(*e.find_node("a"), 0.0);
    CHECK(m.translation_part() == Vec3{1, 1, 0});  // each node applied once

    // a node that is its own parent is also safe
    Node self = make_node("self", NodeType::Mesh);
    self.parent = "self";
    e.add_node(self);
    CHECK(e.world_transform(*e.find_node("self"), 0.0) == Mat4::identity());
}
