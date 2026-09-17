// The SpecRegistry is the single source of truth for the vocabulary. Every
// default must satisfy its own spec, and the param_* accessors must fall back
// to those defaults.
#include <algorithm>
#include <set>
#include <string>

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/core/error.hpp"
#include "aether/core/frame_state.hpp"
#include "aether/core/spec.hpp"
#include "aether/core/validation.hpp"

using namespace aether;
using Catch::Matchers::WithinAbs;

namespace {

int components_of(ValueType t) {
    switch (t) {
        case ValueType::Int:
        case ValueType::Float: return 1;
        case ValueType::Vec2: return 2;
        case ValueType::Vec3: return 3;
        case ValueType::Vec4:
        case ValueType::Color: return 4;
        default: return 0;
    }
}

double component_of(const Value& v, int i) {
    switch (value_type_of(v)) {
        case ValueType::Int: return std::get<int>(v);
        case ValueType::Float: return std::get<float>(v);
        case ValueType::Vec2: return std::get<Vec2>(v)[i];
        case ValueType::Vec3: return std::get<Vec3>(v)[i];
        case ValueType::Vec4: return std::get<Vec4>(v)[i];
        case ValueType::Color: return std::get<Color>(v)[i];
        default: return 0.0;
    }
}

}  // namespace

TEST_CASE("registry covers every node type exactly once", "[core][spec]") {
    const SpecRegistry& registry = SpecRegistry::instance();
    REQUIRE(registry.all().size() == static_cast<size_t>(kNodeTypeCount));
    for (int i = 0; i < kNodeTypeCount; ++i) {
        NodeType t = static_cast<NodeType>(i);
        const NodeSpec& spec = registry.get(t);
        CAPTURE(to_string(t));
        CHECK(spec.type == t);
        CHECK_FALSE(spec.description.empty());
        CHECK_FALSE(spec.outputs.empty());
        CHECK(spec.default_tier >= 0);
        CHECK(spec.default_tier <= 3);
        CHECK(registry.find(to_string(t)) == &spec);
    }
    CHECK(registry.find("not_a_node_type") == nullptr);
    CHECK(&SpecRegistry::instance() == &SpecRegistry::instance());  // single instance
}

TEST_CASE("every ParamSpec default satisfies its own spec", "[core][spec]") {
    for (const NodeSpec& spec : SpecRegistry::instance().all()) {
        CAPTURE(to_string(spec.type));
        std::set<std::string> names;
        for (const ParamSpec& p : spec.params) {
            CAPTURE(p.name);
            CHECK(names.insert(p.name).second);  // unique within the type
            CHECK_FALSE(p.description.empty());
            // type
            CHECK(value_compatible(p.default_value, p.type));
            // enum membership
            if (p.type == ValueType::Enum) {
                CHECK_FALSE(p.enum_values.empty());
                const std::string& def = value_as_string(p.default_value);
                CHECK(std::find(p.enum_values.begin(), p.enum_values.end(), def) != p.enum_values.end());
                std::set<std::string> unique(p.enum_values.begin(), p.enum_values.end());
                CHECK(unique.size() == p.enum_values.size());
            } else {
                CHECK(p.enum_values.empty());
            }
            // range
            if (p.min && p.max) CHECK(*p.min <= *p.max);
            for (int i = 0; i < components_of(value_type_of(p.default_value)); ++i) {
                double c = component_of(p.default_value, i);
                CAPTURE(i, c);
                if (p.min) CHECK(c >= *p.min);
                if (p.max) CHECK(c <= *p.max);
            }
            // min/max only make sense on numeric types
            if (p.type != ValueType::Int && p.type != ValueType::Float) {
                CHECK_FALSE(p.min.has_value());
                CHECK_FALSE(p.max.has_value());
            }
            // the default survives the json encoding
            CHECK(values_equal(value_from_json(value_to_json(p.default_value), p.type), p.default_value));
        }
        std::set<std::string> ports;
        for (const PortSpec& port : spec.inputs) {
            CAPTURE(port.name);
            CHECK(ports.insert(port.name).second);
            CHECK_FALSE(port.description.empty());
            CHECK_FALSE(port.accepts.empty());
            CHECK(names.find(port.name) == names.end());  // a port never collides with a parameter
        }
    }
}

TEST_CASE("a node with every parameter written out validates cleanly", "[core][spec][validation]") {
    // Writing each default explicitly must not produce E004/E005/E006/E007/E019.
    for (const NodeSpec& spec : SpecRegistry::instance().all()) {
        CAPTURE(to_string(spec.type));
        Effect effect;
        effect.duration = 4.0;
        Node node;
        node.id = "n";
        node.type = spec.type;
        for (const ParamSpec& p : spec.params) node.parameters[p.name] = Parameter{p.default_value};
        effect.add_node(node);
        Diagnostics d = validate_node(effect, effect.nodes.front());
        INFO(d.summary());
        for (const Diagnostic& item : d.items) {
            CAPTURE(item.code, item.message);
            CHECK(item.code != "E004");
            CHECK(item.code != "E005");
            CHECK(item.code != "E006");
            CHECK(item.code != "E007");
            CHECK(item.code != "E019");
        }
    }
}

TEST_CASE("transform and window groups follow the spatial/time_bound flags", "[core][spec]") {
    for (const NodeSpec& spec : SpecRegistry::instance().all()) {
        CAPTURE(to_string(spec.type));
        const bool has_transform = spec.find_param("position") != nullptr &&
                                   spec.find_param("rotation") != nullptr && spec.find_param("scale") != nullptr;
        CHECK(has_transform == spec.spatial);
        const bool has_window = spec.find_param("start_time") != nullptr && spec.find_param("duration") != nullptr &&
                                spec.find_param("phase") != nullptr;
        CHECK(has_window == spec.time_bound);
        if (spec.spatial) {
            CHECK(spec.find_param("position")->animatable);
            CHECK(spec.find_param("rotation")->animatable);
            CHECK(spec.find_param("scale")->animatable);
        }
    }
    const SpecRegistry& registry = SpecRegistry::instance();
    CHECK(registry.get(NodeType::Emitter).spatial);
    CHECK(registry.get(NodeType::Emitter).time_bound);
    CHECK(registry.get(NodeType::Mesh).spatial);
    CHECK(registry.get(NodeType::Curve).spatial);
    CHECK_FALSE(registry.get(NodeType::Curve).time_bound);
    CHECK(registry.get(NodeType::Trail).time_bound);
    CHECK_FALSE(registry.get(NodeType::Trail).spatial);
    CHECK_FALSE(registry.get(NodeType::ParticleSystem).spatial);
    CHECK_FALSE(registry.get(NodeType::Material).time_bound);
    CHECK_FALSE(registry.get(NodeType::Camera).spatial);  // camera has position/target, not a transform group
}

TEST_CASE("tiers match the architecture document", "[core][spec]") {
    const SpecRegistry& registry = SpecRegistry::instance();
    CHECK(registry.get(NodeType::Emitter).default_tier == 1);
    CHECK(registry.get(NodeType::ParticleSystem).default_tier == 1);
    CHECK(registry.get(NodeType::Volume).default_tier == 3);
    for (NodeType t : {NodeType::Beam, NodeType::Trail, NodeType::Decal, NodeType::Light, NodeType::Mesh,
                       NodeType::Curve, NodeType::Camera, NodeType::PostEffect}) {
        CAPTURE(to_string(t));
        CHECK(registry.get(t).default_tier == 0);
    }
}

TEST_CASE("selected vocabulary entries match docs/VOCABULARY.md", "[core][spec]") {
    const SpecRegistry& registry = SpecRegistry::instance();

    const ParamSpec* rate = registry.get(NodeType::Emitter).find_param("rate");
    REQUIRE(rate != nullptr);
    CHECK(rate->type == ValueType::Float);
    CHECK(value_as_float(rate->default_value) == 50.0f);
    CHECK(rate->animatable);
    REQUIRE(rate->min.has_value());
    CHECK(*rate->min == 0.0);
    CHECK_FALSE(rate->max.has_value());

    const ParamSpec* burst_times = registry.get(NodeType::Emitter).find_param("burst_times");
    REQUIRE(burst_times != nullptr);
    CHECK(burst_times->type == ValueType::FloatList);
    CHECK(value_as_float_list(burst_times->default_value) == std::vector<float>{0.0f});

    const ParamSpec* max_particles = registry.get(NodeType::ParticleSystem).find_param("max_particles");
    REQUIRE(max_particles != nullptr);
    CHECK(max_particles->type == ValueType::Int);
    CHECK(value_as_int(max_particles->default_value) == 10000);
    CHECK(*max_particles->min == 1.0);
    CHECK(*max_particles->max == 2000000.0);

    const ParamSpec* opacity_over_life = registry.get(NodeType::ParticleSystem).find_param("opacity_over_life");
    REQUIRE(opacity_over_life != nullptr);
    CHECK(opacity_over_life->type == ValueType::Curve);
    CHECK(value_as_curve(opacity_over_life->default_value) == Curve::linear(1.0f, 0.0f));

    const ParamSpec* temperature_gradient = registry.get(NodeType::Material).find_param("temperature_gradient");
    REQUIRE(temperature_gradient != nullptr);
    CHECK(temperature_gradient->type == ValueType::Gradient);
    CHECK(value_as_gradient(temperature_gradient->default_value).empty());

    const ParamSpec* graph = registry.get(NodeType::Texture).find_param("graph");
    REQUIRE(graph != nullptr);
    CHECK(graph->type == ValueType::Json);
    CHECK(std::get<nlohmann::json>(graph->default_value) == nlohmann::json::object());

    const ParamSpec* points = registry.get(NodeType::Curve).find_param("points");
    REQUIRE(points != nullptr);
    CHECK(points->type == ValueType::Vec3List);
    CHECK(value_as_vec3_list(points->default_value) == std::vector<Vec3>{{0, 0, 0}, {0, 1, 0}});

    const ParamSpec* blend = registry.get(NodeType::Decal).find_param("blend");
    REQUIRE(blend != nullptr);
    CHECK(value_as_string(blend->default_value) == "alpha");  // decals default to alpha
    CHECK(value_as_string(registry.get(NodeType::Material).find_param("blend")->default_value) == "additive");

    // ports
    const PortSpec* particle = registry.get(NodeType::Emitter).find_input("particle");
    REQUIRE(particle != nullptr);
    CHECK(particle->required);
    CHECK_FALSE(particle->multi);
    CHECK(particle->accepts == std::vector<NodeType>{NodeType::ParticleSystem});
    const PortSpec* forces = registry.get(NodeType::ParticleSystem).find_input("forces");
    REQUIRE(forces != nullptr);
    CHECK(forces->multi);
    CHECK_FALSE(forces->required);
    const PortSpec* targets = registry.get(NodeType::Event).find_input("targets");
    REQUIRE(targets != nullptr);
    CHECK(targets->multi);
    CHECK(targets->required);
    const PortSpec* event_source = registry.get(NodeType::Event).find_input("source");
    REQUIRE(event_source != nullptr);
    CHECK_FALSE(event_source->required);  // only required for the particle triggers
    CHECK(registry.get(NodeType::Trail).find_input("source")->required);
    CHECK(registry.get(NodeType::Light).inputs.empty());
    CHECK(registry.get(NodeType::Emitter).find_input("nope") == nullptr);

    // outputs
    CHECK(registry.get(NodeType::PostEffect).outputs == std::vector<std::string>{"post"});
    CHECK(registry.get(NodeType::Collider).outputs == std::vector<std::string>{"collider", "on_collision"});
}

TEST_CASE("param accessors fall back to spec defaults and honour tracks", "[core][spec]") {
    Node emitter;
    emitter.id = "em";
    emitter.type = NodeType::Emitter;
    CHECK(param_float(emitter, "rate") == 50.0f);
    CHECK(param_float(emitter, "radius") == 0.5f);
    CHECK(param_int(emitter, "burst_count") == 0);
    CHECK(param_bool(emitter, "surface_only") == false);
    CHECK(param_vec3(emitter, "direction") == Vec3{0, 1, 0});
    CHECK(param_vec3(emitter, "scale") == Vec3{1, 1, 1});
    CHECK(param_string(emitter, "shape") == "sphere");
    CHECK(param_float_list(emitter, "burst_times") == std::vector<float>{0.0f});

    emitter.parameters["rate"] = Parameter{Value(10.0f)};
    emitter.parameters["rate"].set_keyframe(0.0, Value(0.0f));
    emitter.parameters["rate"].set_keyframe(1.0, Value(100.0f));
    CHECK_THAT(param_float(emitter, "rate", 0.0), WithinAbs(0.0, 1e-6));
    CHECK_THAT(param_float(emitter, "rate", 0.5), WithinAbs(50.0, 1e-5));
    CHECK_THAT(param_float(emitter, "rate", 5.0), WithinAbs(100.0, 1e-6));

    Node particle_system;
    particle_system.type = NodeType::ParticleSystem;
    CHECK(param_curve(particle_system, "opacity_over_life").eval(1.0f) == 0.0f);
    CHECK(param_gradient(particle_system, "color_over_life").eval(0.5f) == Color::white());
    CHECK(param_color(particle_system, "color") == Color{1, 1, 1, 1});

    Node camera;
    camera.type = NodeType::Camera;
    CHECK(param_vec3(camera, "position") == Vec3{0, 1.5f, 5});
    CHECK(param_float(camera, "fov") == 45.0f);

    Node texture;
    texture.type = NodeType::Texture;
    CHECK(param_json(texture, "graph") == nlohmann::json::object());

    Node light;
    light.type = NodeType::Light;
    CHECK(param_vec2(light, "area_size") == Vec2{1, 1});

    // E004 for unknown parameters, and for parameters belonging to another type
    auto expect_e004 = [](auto&& fn) {
        try {
            fn();
            FAIL("expected E004");
        } catch (const Error& e) {
            CHECK(e.code() == "E004");
        }
    };
    expect_e004([&] { param_float(emitter, "wobble"); });
    expect_e004([&] { param_float(emitter, "max_segments"); });  // trail parameter
    expect_e004([&] { param_value(particle_system, "position"); });
    // param_string refuses non string-ish parameters
    CHECK_THROWS_AS(param_string(emitter, "radius"), Error);
}

TEST_CASE("node_window resolves start/duration", "[core][spec]") {
    Node emitter;
    emitter.type = NodeType::Emitter;
    TimeWindow open = node_window(emitter);
    CHECK(open.start == 0.0);
    CHECK(open.end == -1.0);  // until the end of the effect
    CHECK(open.contains(1.9, 2.0));
    CHECK_FALSE(open.contains(2.0, 2.0));

    emitter.parameters["start_time"] = Parameter{Value(0.5f)};
    emitter.parameters["duration"] = Parameter{Value(1.0f)};
    TimeWindow bounded = node_window(emitter);
    CHECK_THAT(bounded.start, WithinAbs(0.5, 1e-6));
    CHECK_THAT(bounded.end, WithinAbs(1.5, 1e-6));
    CHECK_FALSE(bounded.contains(0.4, 4.0));
    CHECK(bounded.contains(1.0, 4.0));
    CHECK_FALSE(bounded.contains(1.6, 4.0));

    Node material;  // not time bound
    material.type = NodeType::Material;
    CHECK(node_window(material).start == 0.0);
    CHECK(node_window(material).end == -1.0);
}

TEST_CASE("volume: the procedural vocabulary (docs/VOLUMES.md)", "[core][spec][volume]") {
    const NodeSpec& spec = SpecRegistry::instance().get(NodeType::Volume);
    auto find = [&](const std::string& name) -> const ParamSpec* {
        for (const ParamSpec& p : spec.params)
            if (p.name == name) return &p;
        return nullptr;
    };

    SECTION("every parameter of the design exists, with its default and range") {
        struct Expected {
            const char* name;
            ValueType type;
            bool animatable;
        };
        const Expected expected[]{
            {"mode", ValueType::Enum, false},          {"shape", ValueType::Enum, false},
            {"radius", ValueType::Float, true},        {"height", ValueType::Float, true},
            {"density", ValueType::Float, true},       {"emission", ValueType::Float, true},
            {"color", ValueType::Color, true},         {"color_hot", ValueType::Color, false},
            {"filament_scale", ValueType::Float, false}, {"strands", ValueType::Float, false},
            {"carve", ValueType::Float, false},        {"softness", ValueType::Float, false},
            {"spiral_arms", ValueType::Int, false},    {"arm_sharpness", ValueType::Float, false},
            {"twist", ValueType::Float, true},         {"spin", ValueType::Float, true},
            {"climb", ValueType::Float, true},         {"scatter", ValueType::Float, false},
            {"march_steps", ValueType::Int, false},
        };
        for (const Expected& e : expected) {
            CAPTURE(e.name);
            const ParamSpec* p = find(e.name);
            REQUIRE(p != nullptr);
            CHECK(p->type == e.type);
            CHECK(p->animatable == e.animatable);
        }
        // The physical (mode: simulation) parameters stay.
        for (const char* name : {"volume_type", "bounds", "voxel_size", "temperature", "fuel", "dissipation",
                                 "buoyancy", "vorticity", "cooling", "expansion", "combustion_rate"}) {
            CAPTURE(name);
            CHECK(find(name) != nullptr);
        }
    }

    SECTION("defaults") {
        Node volume;
        volume.type = NodeType::Volume;
        CHECK(param_string(volume, "mode") == "procedural");
        CHECK(param_string(volume, "shape") == "sphere");
        CHECK_THAT(param_float(volume, "radius"), WithinAbs(1.5, 1e-6));
        CHECK_THAT(param_float(volume, "height"), WithinAbs(2.0, 1e-6));
        CHECK_THAT(param_float(volume, "density"), WithinAbs(1.0, 1e-6));
        CHECK_THAT(param_float(volume, "emission"), WithinAbs(1.0, 1e-6));
        CHECK_THAT(param_float(volume, "filament_scale"), WithinAbs(2.0, 1e-6));
        CHECK_THAT(param_float(volume, "strands"), WithinAbs(0.5, 1e-6));
        CHECK_THAT(param_float(volume, "carve"), WithinAbs(0.45, 1e-6));
        CHECK_THAT(param_float(volume, "softness"), WithinAbs(0.6, 1e-6));
        CHECK_THAT(param_float(volume, "arm_sharpness"), WithinAbs(1.5, 1e-6));
        CHECK_THAT(param_float(volume, "scatter"), WithinAbs(0.3, 1e-6));
        CHECK(param_int(volume, "spiral_arms") == 0);
        CHECK(param_int(volume, "march_steps") == 48);
        const Color color = param_color(volume, "color");
        CHECK_THAT(color.r, WithinAbs(0.6, 1e-6));
        CHECK_THAT(color.g, WithinAbs(0.3, 1e-6));
        CHECK_THAT(color.b, WithinAbs(1.0, 1e-6));
    }

    SECTION("the enums are the designed ones") {
        REQUIRE(find("mode") != nullptr);
        CHECK(find("mode")->enum_values == std::vector<std::string>{"procedural", "simulation"});
        REQUIRE(find("shape") != nullptr);
        CHECK(find("shape")->enum_values ==
              std::vector<std::string>{"sphere", "column", "disc", "ring", "nebula", "cone"});
    }
}

TEST_CASE("volume_shape_extent bounds every shape", "[core][spec][volume]") {
    CHECK(volume_shape_extent("sphere", 2.0f, 5.0f) == Vec3{2, 2, 2});   // height is unused
    CHECK(volume_shape_extent("column", 1.0f, 4.0f) == Vec3{1, 2, 1});
    CHECK(volume_shape_extent("cone", 1.0f, 4.0f) == Vec3{1, 2, 1});
    CHECK(volume_shape_extent("nebula", 3.0f, 2.0f) == Vec3{3, 1, 3});
    CHECK(volume_shape_extent("disc", 2.0f, 4.0f) == Vec3{2, 0.5f, 2});  // 0.25 * height thick
    CHECK(volume_shape_extent("ring", 2.0f, 4.0f) == Vec3{3, 1, 3});     // major 2 + minor 1
    CHECK(volume_shape_extent("unknown", 1.5f, 9.0f) == Vec3{1.5f, 1.5f, 1.5f});
    // Negative sizes never produce a negative box.
    CHECK(volume_shape_extent("column", -1.0f, -4.0f) == Vec3{0, 0, 0});
}
