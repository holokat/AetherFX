// The vocabulary registry. This file *is* docs/VOCABULARY.md in code: every
// node type, parameter (type/default/range/enum/animatable/units) and port.
// schema/vocabulary.json and schema/effect.schema.json are generated from it.
#include "aether/core/spec.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdio>
#include <map>
#include <utility>

#include "aether/core/error.hpp"

namespace aether {
namespace {

// ---------------------------------------------------------------------------
// builders
// ---------------------------------------------------------------------------

struct Pb {
    ParamSpec spec;
    Pb(std::string param_name, ValueType param_type, Value def) {
        spec.name = std::move(param_name);
        spec.type = param_type;
        spec.default_value = std::move(def);
    }
    Pb& min_of(double v) { spec.min = v; return *this; }
    Pb& range_of(double a, double b) { spec.min = a; spec.max = b; return *this; }
    Pb& animated() { spec.animatable = true; return *this; }
    Pb& doc(std::string text) { spec.description = std::move(text); return *this; }
    Pb& unit(std::string text) { spec.units = std::move(text); return *this; }
    Pb& options(std::vector<std::string> values) { spec.enum_values = std::move(values); return *this; }
    operator ParamSpec() const { return spec; }  // NOLINT(google-explicit-constructor)
};

Pb pb_bool(std::string n, bool d) { return Pb(std::move(n), ValueType::Bool, d); }
Pb pb_int(std::string n, int d) { return Pb(std::move(n), ValueType::Int, d); }
Pb pb_float(std::string n, float d) { return Pb(std::move(n), ValueType::Float, d); }
Pb pb_vec2(std::string n, Vec2 d) { return Pb(std::move(n), ValueType::Vec2, d); }
Pb pb_vec3(std::string n, Vec3 d) { return Pb(std::move(n), ValueType::Vec3, d); }
Pb pb_color(std::string n, Color d) { return Pb(std::move(n), ValueType::Color, d); }
Pb pb_string(std::string n, std::string d) { return Pb(std::move(n), ValueType::String, std::move(d)); }
Pb pb_enum(std::string n, std::string d, std::vector<std::string> values) {
    return Pb(std::move(n), ValueType::Enum, std::move(d)).options(std::move(values));
}
Pb pb_curve(std::string n, Curve d) { return Pb(std::move(n), ValueType::Curve, std::move(d)); }
Pb pb_gradient(std::string n, Gradient d) { return Pb(std::move(n), ValueType::Gradient, std::move(d)); }
Pb pb_float_list(std::string n, std::vector<float> d) { return Pb(std::move(n), ValueType::FloatList, std::move(d)); }
Pb pb_vec3_list(std::string n, std::vector<Vec3> d) { return Pb(std::move(n), ValueType::Vec3List, std::move(d)); }
Pb pb_json(std::string n, nlohmann::json d) { return Pb(std::move(n), ValueType::Json, std::move(d)); }

PortSpec port(std::string n, std::vector<NodeType> accepts, bool multi, bool required, std::string description) {
    return PortSpec{std::move(n), std::move(accepts), multi, required, std::move(description)};
}

void add_transform(std::vector<ParamSpec>& out) {
    out.push_back(pb_vec3("position", Vec3{0, 0, 0}).animated().doc("local position").unit("m"));
    out.push_back(pb_vec3("rotation", Vec3{0, 0, 0}).animated().doc("euler angles, applied X then Y then Z").unit("deg"));
    out.push_back(pb_vec3("scale", Vec3{1, 1, 1}).animated().doc("local scale"));
}

void add_window(std::vector<ParamSpec>& out) {
    out.push_back(pb_float("start_time", 0.0f).min_of(0.0).doc("effect time at which the node becomes active").unit("s"));
    out.push_back(pb_float("duration", -1.0f).doc("active duration; -1 = until the end of the effect").unit("s"));
    out.push_back(pb_string("phase", "").doc("timeline phase name; when set it overrides start_time/duration"));
}

const std::vector<std::string>& blend_values() {
    static const std::vector<std::string> v{"additive", "alpha", "premultiplied"};
    return v;
}

// ---------------------------------------------------------------------------
// node types (docs/VOCABULARY.md)
// ---------------------------------------------------------------------------

NodeSpec spec_emitter() {
    NodeSpec n;
    n.type = NodeType::Emitter;
    n.description = "Produces particles for one particle_system, or acts as a source for a volume.";
    n.default_tier = 1;
    n.spatial = true;
    n.time_bound = true;
    add_transform(n.params);
    add_window(n.params);
    n.params.push_back(pb_enum("shape", "sphere",
                               {"point", "sphere", "hemisphere", "box", "disc", "ring", "cone", "line", "curve",
                                "mesh", "volume"})
                           .doc("emission shape"));
    n.params.push_back(pb_float("radius", 0.5f).min_of(0.0).doc("sphere/hemisphere/disc/ring/cone base").unit("m"));
    n.params.push_back(pb_float("inner_radius", 0.0f).min_of(0.0).doc("ring/disc (annulus)").unit("m"));
    n.params.push_back(pb_vec3("size", Vec3{1, 1, 1}).doc("box extents (full)").unit("m"));
    n.params.push_back(pb_float("angle", 30.0f).range_of(0.0, 180.0).doc("cone half-angle").unit("deg"));
    n.params.push_back(pb_float("length", 1.0f).min_of(0.0).doc("line length along +X (local)").unit("m"));
    n.params.push_back(pb_bool("surface_only", false).doc("emit on the surface instead of the volume"));
    n.params.push_back(pb_float("rate", 50.0f).min_of(0.0).animated().doc("particles per second").unit("1/s"));
    n.params.push_back(pb_int("burst_count", 0).min_of(0.0).doc("particles emitted at each burst time"));
    n.params.push_back(pb_float_list("burst_times", {0.0f}).doc("seconds relative to start_time").unit("s"));
    n.params.push_back(pb_float("velocity", 1.0f).animated().doc("initial speed").unit("m/s"));
    n.params.push_back(pb_float("velocity_variance", 0.0f).min_of(0.0).doc("+/- uniform on speed").unit("m/s"));
    n.params.push_back(pb_vec3("direction", Vec3{0, 1, 0}).doc("local direction; zero vector = radial from shape center"));
    n.params.push_back(pb_float("spread", 0.0f).range_of(0.0, 180.0).doc("cone half-angle around direction").unit("deg"));
    n.params.push_back(pb_float("inherit_velocity", 0.0f).range_of(0.0, 1.0).doc("fraction of the emitter's own velocity added"));
    n.params.push_back(pb_float("radial_velocity", 0.0f).doc("extra speed along (particle - center)").unit("m/s"));
    n.params.push_back(pb_int("max_particles", 0).min_of(0.0).doc("0 = unlimited (the system cap still applies)"));
    n.inputs = {port("particle", {NodeType::ParticleSystem}, false, true,
                     "the particle system this emitter spawns into (not required when feeding a volume)"),
                port("shape_curve", {NodeType::Curve}, false, false, "emission path when shape=curve"),
                port("shape_mesh", {NodeType::Mesh}, false, false, "emission mesh when shape=mesh"),
                port("shape_volume", {NodeType::Volume}, false, false, "emission volume when shape=volume")};
    n.outputs = {"particles", "spawn"};
    return n;
}

NodeSpec spec_particle_system() {
    NodeSpec n;
    n.type = NodeType::ParticleSystem;
    n.description = "Defines what a particle is: initial state, evolution over life, rendering.";
    n.default_tier = 1;
    n.params.push_back(pb_int("max_particles", 10000).range_of(1.0, 2000000.0).doc("hard cap on live particles"));
    n.params.push_back(pb_float("lifetime", 1.0f).min_of(0.001).doc("particle lifetime").unit("s"));
    n.params.push_back(pb_float("lifetime_variance", 0.0f).min_of(0.0).doc("+/- uniform on lifetime").unit("s"));
    n.params.push_back(pb_float("size", 0.1f).min_of(0.0).doc("particle diameter").unit("m"));
    n.params.push_back(pb_float("size_variance", 0.0f).min_of(0.0).doc("+/- uniform on size").unit("m"));
    n.params.push_back(pb_curve("size_over_life", Curve::constant(1.0f)).doc("multiplies size over normalized age"));
    n.params.push_back(pb_float("rotation", 0.0f).doc("initial roll").unit("deg"));
    n.params.push_back(pb_float("rotation_variance", 0.0f).doc("+/- uniform on the initial roll").unit("deg"));
    n.params.push_back(pb_float("angular_velocity", 0.0f).doc("roll speed").unit("deg/s"));
    n.params.push_back(pb_float("angular_velocity_variance", 0.0f).doc("+/- uniform on angular_velocity").unit("deg/s"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).doc("base particle color (linear)"));
    n.params.push_back(pb_gradient("color_over_life", Gradient::constant(Color{1, 1, 1, 1}))
                           .doc("multiplies color over normalized age"));
    n.params.push_back(pb_float("opacity", 1.0f).range_of(0.0, 1.0).doc("base opacity"));
    n.params.push_back(pb_curve("opacity_over_life", Curve::linear(1.0f, 0.0f)).doc("opacity over normalized age"));
    n.params.push_back(pb_float("emissive", 0.0f).min_of(0.0).doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_curve("emissive_over_life", Curve::constant(1.0f)).doc("emissive over normalized age"));
    n.params.push_back(pb_float("mass", 1.0f).min_of(0.0001).doc("particle mass").unit("kg"));
    n.params.push_back(pb_float("drag", 0.0f).min_of(0.0).doc("linear drag coefficient").unit("1/s"));
    n.params.push_back(pb_enum("render_mode", "billboard",
                               {"billboard", "stretched_billboard", "mesh", "ribbon", "none"})
                           .doc("how a particle is drawn"));
    n.params.push_back(pb_enum("blend", "additive", blend_values()).doc("blend mode"));
    n.params.push_back(pb_float("velocity_stretch", 0.0f).min_of(0.0)
                           .doc("stretched_billboard: length = size*(1+stretch*speed)"));
    n.params.push_back(pb_bool("align_to_velocity", false).doc("orient the billboard along the velocity"));
    n.params.push_back(pb_float("soft_particle_distance", 0.1f).min_of(0.0).doc("depth fade distance").unit("m"));
    n.params.push_back(pb_bool("sort", false).doc("back-to-front sort for alpha blending"));
    n.params.push_back(pb_int("sprite_columns", 1).range_of(1.0, 64.0).doc("sprite sheet columns"));
    n.params.push_back(pb_int("sprite_rows", 1).range_of(1.0, 64.0).doc("sprite sheet rows"));
    n.params.push_back(pb_float("sprite_fps", 0.0f).doc("0 = map life to the sheet").unit("1/s"));
    n.params.push_back(pb_enum("physics", "none", {"none", "rigid"}).doc("rigid selects Tier 2 (V1 falls back to Tier 1)"));
    n.params.push_back(pb_float("bounce", 0.3f).range_of(0.0, 1.0).doc("restitution on collision"));
    n.params.push_back(pb_float("friction", 0.2f).range_of(0.0, 1.0).doc("tangential friction on collision"));
    n.params.push_back(pb_bool("kill_on_collision", false).doc("destroy the particle on the first collision"));
    n.params.push_back(pb_float("collision_radius", 0.0f).doc("0 = use size*0.5").unit("m"));
    n.params.push_back(pb_enum("orientation", "upright", {"upright", "random", "velocity", "tumble"})
                           .doc("render_mode=mesh: how the instanced mesh is oriented in 3D"));
    n.params.push_back(pb_float("tilt", 0.0f).range_of(0.0, 180.0)
                           .doc("orientation=upright|velocity: per-particle random lean of the up axis, "
                                "uniform in [0, tilt] around a random azimuth")
                           .unit("deg"));
    n.params.push_back(pb_vec3("mesh_scale", Vec3{1, 1, 1}).doc("render_mode=mesh: per-axis multipliers on top of size"));
    n.params.push_back(pb_vec3("mesh_scale_variance", Vec3{0, 0, 0})
                           .doc("+/- uniform per axis on mesh_scale (result clamped to >= 0.05)"));
    n.inputs = {port("material", {NodeType::Material}, false, false, "surface description"),
                port("sprite", {NodeType::Texture}, false, false, "billboard sprite / flipbook"),
                port("mesh", {NodeType::Mesh}, false, false, "instanced mesh when render_mode=mesh"),
                port("forces", {NodeType::Force}, true, false, "forces acting on the particles"),
                port("colliders", {NodeType::Collider}, true, false, "colliders the particles test against"),
                port("trail", {NodeType::Trail}, false, false, "per-particle trail")};
    n.outputs = {"particles", "on_spawn", "on_death", "on_collision"};
    return n;
}

NodeSpec spec_force() {
    NodeSpec n;
    n.type = NodeType::Force;
    n.description = "A force field applied to particles and volumes.";
    n.time_bound = true;
    n.params.push_back(pb_enum("force_type", "gravity",
                               {"gravity", "directional", "radial", "vortex", "turbulence", "curl_noise", "drag",
                                "attractor", "repulsor", "wind", "buoyancy"})
                           .doc("force model"));
    n.params.push_back(pb_float("strength", 9.81f).animated().doc("force magnitude"));
    n.params.push_back(pb_vec3("direction", Vec3{0, -1, 0}).doc("gravity/directional/wind/vortex axis"));
    n.params.push_back(pb_vec3("position", Vec3{0, 0, 0}).animated().doc("radial/vortex/attractor/repulsor center").unit("m"));
    n.params.push_back(pb_float("radius", 0.0f).min_of(0.0).doc("0 = infinite; falloff applies inside the radius").unit("m"));
    n.params.push_back(pb_enum("falloff", "none", {"none", "linear", "inverse_square", "smooth"}).doc("falloff with distance"));
    n.params.push_back(pb_float("frequency", 1.0f).min_of(0.0).doc("turbulence/curl noise spatial frequency").unit("1/m"));
    n.params.push_back(pb_int("octaves", 2).range_of(1.0, 8.0).doc("noise octaves"));
    n.params.push_back(pb_float("speed", 0.5f).doc("noise animation speed").unit("1/s"));
    n.params.push_back(pb_float("temperature", 1.0f).min_of(0.0).doc("buoyancy: multiplies strength"));
    add_window(n.params);
    n.inputs = {port("noise", {NodeType::Noise}, false, false, "optional noise override for turbulence/curl")};
    n.outputs = {"force"};
    return n;
}

NodeSpec spec_field() {
    NodeSpec n;
    n.type = NodeType::Field;
    n.description = "Sampled data grid (V1 evaluates only noise fields).";
    n.params.push_back(pb_enum("field_type", "scalar",
                               {"scalar", "vector", "density", "temperature", "fuel", "velocity", "sdf", "noise"})
                           .doc("what the field stores"));
    n.params.push_back(pb_vec3("bounds_min", Vec3{-1, -1, -1}).doc("field lower bound").unit("m"));
    n.params.push_back(pb_vec3("bounds_max", Vec3{1, 1, 1}).doc("field upper bound").unit("m"));
    n.params.push_back(pb_int("resolution", 32).range_of(4.0, 512.0).doc("cells along the longest axis"));
    n.inputs = {port("source", {NodeType::Noise, NodeType::Volume, NodeType::Mesh}, false, false, "field source")};
    n.outputs = {"field"};
    return n;
}

NodeSpec spec_volume() {
    NodeSpec n;
    n.type = NodeType::Volume;
    n.description =
        "A bounded volume: a procedural raymarched density field (mode: procedural) or, later, a fluid "
        "simulation (mode: simulation, a V1 stub that reports statistics). See docs/VOLUMES.md.";
    n.default_tier = 3;
    n.spatial = true;
    n.time_bound = true;
    add_transform(n.params);
    add_window(n.params);
    n.params.push_back(pb_enum("mode", "procedural", {"procedural", "simulation"})
                           .doc("procedural = raymarched closed-form density; simulation = the future fluid solver (W104)"));
    n.params.push_back(pb_enum("volume_type", "smoke", {"smoke", "fire", "fog", "dust", "magic", "generic_density"})
                           .doc("volume model"));
    n.params.push_back(pb_enum("shape", "sphere", {"sphere", "column", "disc", "ring", "nebula", "cone"})
                           .doc("bounding shape of the procedural field"));
    n.params.push_back(pb_float("radius", 1.5f).min_of(0.0).animated().doc("shape radius").unit("m"));
    n.params.push_back(pb_float("height", 2.0f).min_of(0.0).animated().doc("column/cone height, disc/ring thickness").unit("m"));
    n.params.push_back(pb_vec3("bounds", Vec3{2, 2, 2}).doc("simulation domain extents").unit("m"));
    n.params.push_back(pb_float("voxel_size", 0.05f).range_of(0.005, 1.0).doc("voxel edge length").unit("m"));
    n.params.push_back(pb_float("density", 1.0f).min_of(0.0).animated().doc("overall opacity per metre (injected density in simulation mode)"));
    n.params.push_back(pb_float("emission", 1.0f).min_of(0.0).animated().doc("HDR emission multiplier (bloom feeds on it)"));
    n.params.push_back(pb_color("color", Color{0.6f, 0.3f, 1.0f, 1.0f}).animated().doc("base tint"));
    n.params.push_back(pb_color("color_hot", Color{1, 1, 1, 1}).doc("tint at the densest core"));
    n.params.push_back(pb_float("filament_scale", 2.0f).min_of(0.1).doc("noise frequency").unit("1/m"));
    n.params.push_back(pb_float("strands", 0.5f).range_of(0.0, 1.0).doc("0 = soft clouds, 1 = stringy filaments (ridged noise blend)"));
    n.params.push_back(pb_float("carve", 0.45f).range_of(0.0, 1.0).doc("density threshold: higher carves more holes"));
    n.params.push_back(pb_float("softness", 0.6f).range_of(0.0, 1.0).doc("edge falloff of the bounding shape"));
    n.params.push_back(pb_int("spiral_arms", 0).range_of(0.0, 8.0).doc("azimuthal arm count (nebula/disc)"));
    n.params.push_back(pb_float("arm_sharpness", 1.5f).min_of(0.1).doc("spiral arm contrast and winding"));
    n.params.push_back(pb_float("twist", 0.0f).animated().doc("twist per metre of height").unit("rad/m"));
    n.params.push_back(pb_float("spin", 0.0f).animated().doc("rotation around the up axis").unit("1/s"));
    n.params.push_back(pb_float("climb", 0.0f).animated().doc("upward advection of the noise field").unit("m/s"));
    n.params.push_back(pb_float("scatter", 0.3f).range_of(0.0, 1.0).doc("single-scatter lighting weight from scene lights"));
    n.params.push_back(pb_int("march_steps", 48).range_of(8.0, 192.0).doc("quality/speed knob (a backend may clamp it)"));
    n.params.push_back(pb_float("temperature", 0.0f).min_of(0.0).animated().doc("injected temperature"));
    n.params.push_back(pb_float("fuel", 0.0f).min_of(0.0).doc("injected fuel (combustion)"));
    n.params.push_back(pb_float("dissipation", 0.1f).min_of(0.0).doc("density decay").unit("1/s"));
    n.params.push_back(pb_float("buoyancy", 1.0f).doc("temperature driven lift"));
    n.params.push_back(pb_float("vorticity", 0.2f).min_of(0.0).doc("vorticity confinement"));
    n.params.push_back(pb_float("cooling", 0.5f).min_of(0.0).doc("temperature decay").unit("1/s"));
    n.params.push_back(pb_float("expansion", 0.0f).min_of(0.0).doc("gas expansion on combustion"));
    n.params.push_back(pb_float("combustion_rate", 1.0f).min_of(0.0).doc("fuel burned per second").unit("1/s"));
    n.inputs = {port("sources", {NodeType::Emitter}, true, false, "emitters injecting density/temperature/fuel"),
                port("forces", {NodeType::Force}, true, false, "forces applied to the velocity field"),
                port("colliders", {NodeType::Collider}, true, false, "obstacles"),
                port("material", {NodeType::Material}, false, false, "shading description")};
    n.outputs = {"volume"};
    return n;
}

NodeSpec spec_mesh() {
    NodeSpec n;
    n.type = NodeType::Mesh;
    n.description = "A renderable mesh, an emitter shape or a particle instance source.";
    n.spatial = true;
    n.time_bound = true;
    add_transform(n.params);
    n.params.push_back(pb_enum("source", "primitive", {"primitive", "imported", "procedural", "generated", "particle_instanced"})
                           .doc("where the geometry comes from"));
    n.params.push_back(pb_enum("primitive", "sphere",
                               {"sphere", "cube", "plane", "disc", "ring", "cone", "cylinder", "capsule", "ribbon",
                                "tube", "crystal", "rock", "shard"})
                           .doc("built-in primitive; crystal/rock/shard are procedural and seeded"));
    n.params.push_back(pb_float("radius", 0.5f).min_of(0.0).doc("sphere/disc/ring/cone/cylinder radius").unit("m"));
    n.params.push_back(pb_float("inner_radius", 0.0f).min_of(0.0).doc("ring inner radius").unit("m"));
    n.params.push_back(pb_float("height", 1.0f).min_of(0.0).doc("cone/cylinder/capsule/tube height").unit("m"));
    n.params.push_back(pb_vec3("size", Vec3{1, 1, 1}).doc("cube/plane extents").unit("m"));
    n.params.push_back(pb_int("segments", 24).range_of(3.0, 256.0).doc("tessellation"));
    n.params.push_back(pb_int("variants", 1).range_of(1.0, 16.0)
                           .doc("crystal/rock/shard: how many seeded variants to bake; other primitives ignore it"));
    n.params.push_back(pb_float("irregularity", 0.35f).range_of(0.0, 1.0)
                           .doc("crystal/rock/shard: how far the generator strays from the ideal shape"));
    n.params.push_back(pb_string("path", "").doc("imported (obj) - V1 loads OBJ only"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).animated().doc("tint (linear)"));
    n.params.push_back(pb_float("emissive", 0.0f).min_of(0.0).animated().doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_bool("visible", true).doc("false = geometry source only, never drawn"));
    add_window(n.params);
    n.inputs = {port("material", {NodeType::Material}, false, false, "surface description")};
    n.outputs = {"mesh"};
    return n;
}

NodeSpec spec_curve() {
    NodeSpec n;
    n.type = NodeType::Curve;
    n.description = "A path used by emitters, trails, beams and motion.";
    n.spatial = true;
    add_transform(n.params);
    n.params.push_back(pb_vec3_list("points", {Vec3{0, 0, 0}, Vec3{0, 1, 0}}).doc("control points (local space)").unit("m"));
    n.params.push_back(pb_enum("curve_type", "catmull_rom", {"linear", "catmull_rom", "bezier"}).doc("interpolation"));
    n.params.push_back(pb_bool("closed", false).doc("close the loop"));
    n.params.push_back(pb_int("segments", 32).range_of(1.0, 1024.0).doc("sampling resolution"));
    n.params.push_back(pb_float("noise_amplitude", 0.0f).min_of(0.0).doc("displacement of sampled points").unit("m"));
    n.params.push_back(pb_float("noise_frequency", 1.0f).min_of(0.0).doc("displacement frequency").unit("1/m"));
    n.outputs = {"curve"};
    return n;
}

NodeSpec spec_trail() {
    NodeSpec n;
    n.type = NodeType::Trail;
    n.description = "A ribbon following a source node or a particle.";
    n.time_bound = true;
    add_window(n.params);
    n.params.push_back(pb_float("width", 0.1f).min_of(0.0).animated().doc("ribbon width").unit("m"));
    n.params.push_back(pb_float("lifetime", 0.5f).min_of(0.001).doc("segment lifetime").unit("s"));
    n.params.push_back(pb_curve("taper", Curve::linear(1.0f, 0.0f)).doc("width over segment age"));
    n.params.push_back(pb_curve("opacity_over_life", Curve::linear(1.0f, 0.0f)).doc("opacity over segment age"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).animated().doc("ribbon color (linear)"));
    n.params.push_back(pb_float("emissive", 0.0f).min_of(0.0).animated().doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_float("noise_amplitude", 0.0f).min_of(0.0).doc("lateral displacement").unit("m"));
    n.params.push_back(pb_float("noise_frequency", 1.0f).min_of(0.0).doc("displacement frequency").unit("1/m"));
    n.params.push_back(pb_float("twist", 0.0f).doc("twist over the trail length").unit("deg"));
    n.params.push_back(pb_float("uv_scroll", 0.0f).doc("UV units per second").unit("1/s"));
    n.params.push_back(pb_float("min_vertex_distance", 0.02f).min_of(0.0001).doc("minimum spacing between samples").unit("m"));
    n.params.push_back(pb_int("max_segments", 64).range_of(2.0, 1024.0).doc("ring buffer size"));
    n.params.push_back(pb_enum("blend", "additive", blend_values()).doc("blend mode"));
    n.inputs = {port("source", {NodeType::Mesh, NodeType::Emitter, NodeType::Light, NodeType::Curve,
                                NodeType::ParticleSystem},
                     false, true, "the node the trail follows"),
                port("material", {NodeType::Material}, false, false, "surface description")};
    n.outputs = {"trail"};
    return n;
}

NodeSpec spec_beam() {
    NodeSpec n;
    n.type = NodeType::Beam;
    n.description = "Analytic lightning / laser between two points.";
    n.time_bound = true;
    add_window(n.params);
    n.params.push_back(pb_vec3("origin", Vec3{0, 0, 0}).animated().doc("start point (world)").unit("m"));
    n.params.push_back(pb_vec3("target", Vec3{0, 2, 0}).animated().doc("end point (world)").unit("m"));
    n.params.push_back(pb_float("width", 0.05f).min_of(0.0).animated().doc("beam width").unit("m"));
    n.params.push_back(pb_int("segments", 16).range_of(1.0, 256.0).doc("polyline segments"));
    n.params.push_back(pb_float("noise_amplitude", 0.0f).min_of(0.0).doc("displacement of interior points").unit("m"));
    n.params.push_back(pb_float("noise_frequency", 4.0f).min_of(0.0).doc("displacement frequency").unit("1/m"));
    n.params.push_back(pb_float("jitter_rate", 30.0f).min_of(0.0).doc("re-randomizations per second (0 = static)").unit("1/s"));
    n.params.push_back(pb_int("branching", 0).range_of(0.0, 16.0).doc("branch count"));
    n.params.push_back(pb_float("branch_probability", 0.5f).range_of(0.0, 1.0).doc("chance a branch is spawned"));
    n.params.push_back(pb_float("branch_length", 0.3f).range_of(0.0, 1.0).doc("fraction of the main beam length"));
    n.params.push_back(pb_float("pulse_speed", 0.0f).doc("pulse travel speed (beam lengths/s)").unit("1/s"));
    n.params.push_back(pb_float("pulse_frequency", 0.0f).doc("pulses per beam length"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).animated().doc("beam color (linear)"));
    n.params.push_back(pb_float("emissive", 4.0f).min_of(0.0).animated().doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_enum("blend", "additive", blend_values()).doc("blend mode"));
    n.inputs = {port("origin_node", {NodeType::Mesh, NodeType::Emitter, NodeType::Light, NodeType::Curve}, false, false,
                     "node whose world position overrides origin"),
                port("target_node", {NodeType::Mesh, NodeType::Emitter, NodeType::Light, NodeType::Curve}, false, false,
                     "node whose world position overrides target"),
                port("material", {NodeType::Material}, false, false, "surface description")};
    n.outputs = {"beam"};
    return n;
}

NodeSpec spec_light() {
    NodeSpec n;
    n.type = NodeType::Light;
    n.description = "A dynamic light the effect casts onto the scene.";
    n.spatial = true;
    n.time_bound = true;
    add_transform(n.params);
    add_window(n.params);
    n.params.push_back(pb_enum("light_type", "point", {"point", "spot", "area"}).doc("light model"));
    n.params.push_back(pb_float("intensity", 10.0f).min_of(0.0).animated().doc("radiometric-ish, linear"));
    n.params.push_back(pb_float("radius", 5.0f).min_of(0.0).animated().doc("influence radius").unit("m"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).animated().doc("light color (linear)"));
    n.params.push_back(pb_float("temperature", 0.0f).min_of(0.0).doc("Kelvin; 0 = use color as-is").unit("K"));
    n.params.push_back(pb_float("flicker_amplitude", 0.0f).range_of(0.0, 1.0).doc("flicker depth"));
    n.params.push_back(pb_float("flicker_frequency", 12.0f).min_of(0.0).doc("flicker rate").unit("1/s"));
    n.params.push_back(pb_float("cone_angle", 45.0f).range_of(0.0, 90.0).doc("spot half-angle").unit("deg"));
    n.params.push_back(pb_vec3("direction", Vec3{0, -1, 0}).doc("spot/area direction"));
    n.params.push_back(pb_vec2("area_size", Vec2{1, 1}).doc("area light extents").unit("m"));
    n.params.push_back(pb_bool("cast_shadows", false).doc("shadow casting (renderer dependent)"));
    n.outputs = {"light"};
    return n;
}

NodeSpec spec_decal() {
    NodeSpec n;
    n.type = NodeType::Decal;
    n.description = "A projected sprite on the ground or a surface.";
    n.spatial = true;
    n.time_bound = true;
    add_transform(n.params);
    add_window(n.params);
    n.params.push_back(pb_enum("shape", "circle", {"circle", "rect"}).doc("decal footprint"));
    n.params.push_back(pb_vec2("size", Vec2{1, 1}).animated().doc("footprint extents").unit("m"));
    n.params.push_back(pb_color("color", Color{1, 1, 1, 1}).animated().doc("tint (linear)"));
    n.params.push_back(pb_float("opacity", 1.0f).range_of(0.0, 1.0).animated().doc("overall opacity"));
    n.params.push_back(pb_float("emissive", 0.0f).min_of(0.0).animated().doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_float("fade_in", 0.1f).min_of(0.0).doc("fade in duration").unit("s"));
    n.params.push_back(pb_float("fade_out", 0.5f).min_of(0.0).doc("fade out duration").unit("s"));
    n.params.push_back(pb_vec3("projection_normal", Vec3{0, 1, 0}).doc("surface normal the decal projects along"));
    n.params.push_back(pb_enum("blend", "alpha", blend_values()).doc("blend mode"));
    n.inputs = {port("material", {NodeType::Material}, false, false, "surface description"),
                port("texture", {NodeType::Texture}, false, false, "decal texture")};
    n.outputs = {"decal"};
    return n;
}

NodeSpec spec_material() {
    NodeSpec n;
    n.type = NodeType::Material;
    n.description = "Surface description shared by particles, meshes, trails, beams and decals.";
    n.params.push_back(pb_color("base_color", Color{1, 1, 1, 1}).doc("base color (linear)"));
    n.params.push_back(pb_float("opacity", 1.0f).range_of(0.0, 1.0).doc("overall opacity"));
    n.params.push_back(pb_color("emissive_color", Color{1, 1, 1, 1}).doc("emissive tint (linear)"));
    n.params.push_back(pb_float("emissive_intensity", 0.0f).min_of(0.0).doc("emissive multiplier (HDR)"));
    n.params.push_back(pb_enum("blend", "additive", blend_values()).doc("blend mode"));
    n.params.push_back(pb_enum("shading", "unlit", {"unlit", "lit"}).doc("shading model"));
    n.params.push_back(pb_bool("soft_particle", true).doc("depth fade against the depth buffer"));
    n.params.push_back(pb_float("depth_fade", 0.1f).min_of(0.0).doc("soft particle fade distance").unit("m"));
    n.params.push_back(pb_float("distortion", 0.0f).min_of(0.0).doc("screen-space refraction strength"));
    n.params.push_back(pb_float("fresnel_power", 0.0f).min_of(0.0).doc("0 = off"));
    n.params.push_back(pb_vec2("uv_scroll", Vec2{0, 0}).doc("UV units per second").unit("1/s"));
    n.params.push_back(pb_float("uv_rotate", 0.0f).doc("UV rotation speed").unit("deg/s"));
    n.params.push_back(pb_float("dissolve", 0.0f).range_of(0.0, 1.0).doc("threshold against the noise texture"));
    n.params.push_back(pb_float("erosion", 0.0f).range_of(0.0, 1.0).doc("edge erosion width"));
    n.params.push_back(pb_bool("double_sided", true).doc("disable backface culling"));
    n.params.push_back(pb_gradient("temperature_gradient", Gradient{})
                           .doc("when non-empty, remaps luminance/temperature to color"));
    n.inputs = {port("base_texture", {NodeType::Texture}, false, false, "albedo / sprite"),
                port("noise_texture", {NodeType::Texture}, false, false, "dissolve / erosion noise"),
                port("gradient_texture", {NodeType::Texture}, false, false, "color ramp lookup")};
    n.outputs = {"material"};
    return n;
}

NodeSpec spec_noise() {
    NodeSpec n;
    n.type = NodeType::Noise;
    n.description = "Parameter provider for forces and textures.";
    n.params.push_back(pb_enum("noise_type", "simplex",
                               {"perlin", "simplex", "worley", "fbm", "curl", "blue_noise", "cellular"})
                           .doc("noise basis"));
    n.params.push_back(pb_float("frequency", 1.0f).min_of(0.0).doc("spatial frequency").unit("1/m"));
    n.params.push_back(pb_int("octaves", 3).range_of(1.0, 8.0).doc("fbm octaves"));
    n.params.push_back(pb_float("lacunarity", 2.0f).min_of(1.0).doc("frequency multiplier per octave"));
    n.params.push_back(pb_float("gain", 0.5f).range_of(0.0, 1.0).doc("amplitude multiplier per octave"));
    n.params.push_back(pb_float("amplitude", 1.0f).doc("output scale"));
    n.params.push_back(pb_float("speed", 0.0f).doc("animation along the 4th dimension").unit("1/s"));
    n.params.push_back(pb_vec3("offset", Vec3{0, 0, 0}).doc("domain offset").unit("m"));
    n.outputs = {"noise"};
    return n;
}

NodeSpec spec_collider() {
    NodeSpec n;
    n.type = NodeType::Collider;
    n.description = "A collision shape particles and volumes test against.";
    n.spatial = true;
    add_transform(n.params);
    n.params.push_back(pb_enum("collider_type", "plane", {"plane", "sphere", "box", "capsule", "mesh", "sdf"})
                           .doc("collision shape"));
    n.params.push_back(pb_vec3("normal", Vec3{0, 1, 0}).doc("plane normal"));
    n.params.push_back(pb_float("radius", 0.5f).min_of(0.0).doc("sphere/capsule radius").unit("m"));
    n.params.push_back(pb_float("height", 1.0f).min_of(0.0).doc("capsule height").unit("m"));
    n.params.push_back(pb_vec3("size", Vec3{1, 1, 1}).doc("box extents").unit("m"));
    n.params.push_back(pb_float("bounce", 0.3f).range_of(0.0, 1.0).doc("restitution"));
    n.params.push_back(pb_float("friction", 0.2f).range_of(0.0, 1.0).doc("tangential friction"));
    n.params.push_back(pb_bool("kill_on_collision", false).doc("destroy particles on contact"));
    n.inputs = {port("mesh", {NodeType::Mesh}, false, false, "geometry when collider_type=mesh|sdf")};
    n.outputs = {"collider", "on_collision"};
    return n;
}

NodeSpec spec_event() {
    NodeSpec n;
    n.type = NodeType::Event;
    n.description = "Triggers bursts on target emitters when something happens.";
    n.params.push_back(pb_enum("trigger", "on_time",
                               {"on_spawn", "on_death", "on_collision", "on_distance", "on_time", "on_peak", "on_impact"})
                           .doc("what fires the event"));
    n.params.push_back(pb_float("time", 0.0f).doc("on_time: absolute effect time").unit("s"));
    n.params.push_back(pb_float("distance", 1.0f).min_of(0.0).doc("on_distance: from the source origin").unit("m"));
    n.params.push_back(pb_float("probability", 1.0f).range_of(0.0, 1.0).doc("chance the event fires"));
    n.params.push_back(pb_int("max_triggers", 0).min_of(0.0).doc("0 = unlimited"));
    n.params.push_back(pb_int("burst_count", 0).min_of(0.0).doc("override burst on targets (0 = the target's own burst_count)"));
    n.params.push_back(pb_bool("inherit_position", true).doc("spawn at the triggering particle position"));
    n.params.push_back(pb_float("inherit_velocity", 0.0f).range_of(0.0, 1.0).doc("fraction of the triggering velocity"));
    n.inputs = {port("source", {NodeType::ParticleSystem, NodeType::Emitter, NodeType::Collider}, false, false,
                     "required for the particle triggers (on_spawn/on_death/on_collision/on_distance)"),
                port("targets", {NodeType::Emitter}, true, true, "emitters to burst")};
    n.outputs = {"event"};
    return n;
}

NodeSpec spec_camera() {
    NodeSpec n;
    n.type = NodeType::Camera;
    n.description = "Preview camera; tools use it by default.";
    n.params.push_back(pb_vec3("position", Vec3{0, 1.5f, 5}).animated().doc("eye position").unit("m"));
    n.params.push_back(pb_vec3("target", Vec3{0, 1, 0}).animated().doc("look-at point").unit("m"));
    n.params.push_back(pb_vec3("up", Vec3{0, 1, 0}).doc("up vector"));
    n.params.push_back(pb_float("fov", 45.0f).range_of(1.0, 170.0).doc("vertical field of view").unit("deg"));
    n.params.push_back(pb_float("near", 0.05f).min_of(0.0001).doc("near plane").unit("m"));
    n.params.push_back(pb_float("far", 200.0f).min_of(0.001).doc("far plane").unit("m"));
    n.params.push_back(pb_float("exposure", 1.0f).min_of(0.0).doc("linear exposure multiplier"));
    n.outputs = {"camera"};
    return n;
}

NodeSpec spec_post_effect() {
    NodeSpec n;
    n.type = NodeType::PostEffect;
    n.description = "Full-screen post process; use sparingly.";
    n.time_bound = true;
    add_window(n.params);
    n.params.push_back(pb_enum("post_type", "bloom",
                               {"bloom", "distortion", "heat_haze", "chromatic_aberration", "exposure_pulse"})
                           .doc("post process kind"));
    n.params.push_back(pb_float("intensity", 1.0f).min_of(0.0).animated().doc("effect strength"));
    n.params.push_back(pb_float("threshold", 1.0f).min_of(0.0).doc("bloom luminance threshold"));
    n.params.push_back(pb_float("radius", 0.05f).range_of(0.0, 1.0).doc("bloom (fraction of width) / haze radius"));
    n.params.push_back(pb_float("frequency", 8.0f).min_of(0.0).doc("heat_haze / pulse frequency").unit("1/s"));
    n.outputs = {"post"};
    return n;
}

NodeSpec spec_texture() {
    NodeSpec n;
    n.type = NodeType::Texture;
    n.description = "Procedural texture graph or file, baked by the compiler.";
    n.params.push_back(pb_enum("source", "procedural", {"procedural", "file"}).doc("where the pixels come from"));
    n.params.push_back(pb_string("path", "").doc("image path when source=file"));
    n.params.push_back(pb_int("width", 256).range_of(1.0, 4096.0).doc("baked width").unit("px"));
    n.params.push_back(pb_int("height", 256).range_of(1.0, 4096.0).doc("baked height").unit("px"));
    n.params.push_back(pb_int("frames", 1).range_of(1.0, 256.0).doc("flipbook frames"));
    n.params.push_back(pb_int("columns", 1).range_of(1.0, 64.0)
                           .doc("flipbook grid layout of a file texture; frames become columns*rows"));
    n.params.push_back(pb_int("rows", 1).range_of(1.0, 64.0)
                           .doc("flipbook grid layout of a file texture; frames become columns*rows"));
    n.params.push_back(pb_json("graph", nlohmann::json::object())
                           .doc("procedural texture graph {nodes:[{id,op,params,inputs}], output}"));
    n.outputs = {"texture"};
    return n;
}

std::vector<NodeSpec> build_specs() {
    std::vector<NodeSpec> out(static_cast<size_t>(kNodeTypeCount));
    auto put = [&out](NodeSpec s) { out[static_cast<size_t>(s.type)] = std::move(s); };
    put(spec_emitter());
    put(spec_particle_system());
    put(spec_force());
    put(spec_field());
    put(spec_volume());
    put(spec_mesh());
    put(spec_curve());
    put(spec_trail());
    put(spec_beam());
    put(spec_light());
    put(spec_decal());
    put(spec_material());
    put(spec_noise());
    put(spec_collider());
    put(spec_event());
    put(spec_camera());
    put(spec_post_effect());
    put(spec_texture());
    return out;
}

// ---------------------------------------------------------------------------
// json export helpers
// ---------------------------------------------------------------------------

const std::vector<std::pair<std::string, std::string>>& validation_code_table() {
    static const std::vector<std::pair<std::string, std::string>> table{
        {"E001", "duplicate node id"},
        {"E002", "invalid id format (^[a-z][a-z0-9_]*$)"},
        {"E003", "unknown node type"},
        {"E004", "unknown parameter"},
        {"E005", "parameter type mismatch"},
        {"E006", "value out of range"},
        {"E007", "invalid enum value"},
        {"E008", "unresolved reference"},
        {"E009", "port type mismatch"},
        {"E010", "required input missing"},
        {"E011", "multi-input given to a single port"},
        {"E012", "cycle in the parent chain"},
        {"E013", "cycle in the input graph"},
        {"E014", "unknown layer"},
        {"E015", "effect duration invalid"},
        {"E016", "timeline phase invalid (end<=start or outside duration)"},
        {"E017", "unknown port"},
        {"E018", "keyframe time invalid"},
        {"E019", "curve/gradient keys not sorted or out of [0,1]"},
        {"E020", "schema version unsupported"},
        {"W001", "unknown phase reference"},
        {"W002", "unused node (no consumer and not renderable)"},
        {"W003", "max_particles budget high"},
        {"W004", "node disabled but referenced"},
        {"W005", "deprecated parameter"}};
    return table;
}

std::string number_key(double v) {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%g", v);
    std::string s(buf);
    for (char& c : s) {
        if (c == '.') c = 'p';
        else if (c == '-') c = 'n';
        else if (c == '+') c = '_';
    }
    return s;
}

nlohmann::json number_json(double v) {
    if (std::floor(v) == v && std::fabs(v) < 1e15) return static_cast<long long>(v);
    return v;
}

// A bare (unwrapped) JSON Schema fragment for one parameter value.
nlohmann::json bare_schema(const ParamSpec& p) {
    nlohmann::json s;
    switch (p.type) {
        case ValueType::Bool: s = {{"type", "boolean"}}; break;
        case ValueType::Int: s = {{"type", "integer"}}; break;
        case ValueType::Float: s = {{"type", "number"}}; break;
        case ValueType::Vec2: s = {{"$ref", "#/$defs/vec2"}}; break;
        case ValueType::Vec3: s = {{"$ref", "#/$defs/vec3"}}; break;
        case ValueType::Vec4: s = {{"$ref", "#/$defs/vec4"}}; break;
        case ValueType::Color: s = {{"$ref", "#/$defs/color"}}; break;
        case ValueType::String: s = {{"type", "string"}}; break;
        case ValueType::Ref: s = {{"$ref", "#/$defs/node_ref"}}; break;
        case ValueType::Enum: {
            nlohmann::json values = nlohmann::json::array();
            for (const auto& e : p.enum_values) values.push_back(e);
            s = {{"type", "string"}, {"enum", values}};
            break;
        }
        case ValueType::Curve: s = {{"$ref", "#/$defs/curve"}}; break;
        case ValueType::Gradient: s = {{"$ref", "#/$defs/gradient"}}; break;
        case ValueType::FloatList: s = {{"$ref", "#/$defs/float_list"}}; break;
        case ValueType::Vec3List: s = {{"$ref", "#/$defs/vec3_list"}}; break;
        case ValueType::Json: s = nlohmann::json::object(); break;
    }
    if ((p.type == ValueType::Int || p.type == ValueType::Float)) {
        if (p.min) s["minimum"] = number_json(*p.min);
        if (p.max) s["maximum"] = number_json(*p.max);
    }
    return s;
}

// Stable identifier for a bare schema so identical shapes share one $defs entry.
std::string schema_key(const ParamSpec& p) {
    std::string key(to_string(p.type));
    if (p.type == ValueType::Enum) {
        for (const auto& e : p.enum_values) {
            key += "_";
            for (char c : e) key += (std::isalnum(static_cast<unsigned char>(c)) != 0) ? c : '_';
        }
    }
    if (p.type == ValueType::Int || p.type == ValueType::Float) {
        if (p.min) key += "_min" + number_key(*p.min);
        if (p.max) key += "_max" + number_key(*p.max);
    }
    return key;
}

}  // namespace

// ---------------------------------------------------------------------------
// SpecRegistry
// ---------------------------------------------------------------------------

const ParamSpec* NodeSpec::find_param(std::string_view name) const {
    for (const auto& p : params)
        if (p.name == name) return &p;
    return nullptr;
}

const PortSpec* NodeSpec::find_input(std::string_view name) const {
    for (const auto& p : inputs)
        if (p.name == name) return &p;
    return nullptr;
}

SpecRegistry::SpecRegistry() : specs_(build_specs()) {}

const SpecRegistry& SpecRegistry::instance() {
    static const SpecRegistry registry;
    return registry;
}

const NodeSpec& SpecRegistry::get(NodeType t) const {
    size_t i = static_cast<size_t>(t);
    if (i >= specs_.size()) throw Error("E003", "node type out of range");
    return specs_[i];
}

const NodeSpec* SpecRegistry::find(std::string_view type_name) const {
    NodeType t{};
    if (!parse_node_type(type_name, t)) return nullptr;
    return &get(t);
}

nlohmann::json SpecRegistry::to_json() const {
    nlohmann::json node_types = nlohmann::json::object();
    for (const auto& s : specs_) {
        nlohmann::json params = nlohmann::json::object();
        for (const auto& p : s.params) {
            nlohmann::json enum_values = nlohmann::json::array();
            for (const auto& e : p.enum_values) enum_values.push_back(e);
            params[p.name] = {{"type", to_string(p.type)},
                              {"default", value_to_json(p.default_value)},
                              {"min", p.min ? nlohmann::json(number_json(*p.min)) : nlohmann::json()},
                              {"max", p.max ? nlohmann::json(number_json(*p.max)) : nlohmann::json()},
                              {"enum", enum_values},
                              {"animatable", p.animatable},
                              {"description", p.description},
                              {"units", p.units}};
        }
        nlohmann::json inputs = nlohmann::json::object();
        for (const auto& p : s.inputs) {
            nlohmann::json accepts = nlohmann::json::array();
            for (NodeType t : p.accepts) accepts.push_back(to_string(t));
            inputs[p.name] = {{"accepts", accepts},
                              {"multi", p.multi},
                              {"required", p.required},
                              {"description", p.description}};
        }
        nlohmann::json outputs = nlohmann::json::array();
        for (const auto& o : s.outputs) outputs.push_back(o);
        node_types[std::string(to_string(s.type))] = {{"description", s.description},
                                                      {"version", s.version},
                                                      {"default_tier", s.default_tier},
                                                      {"spatial", s.spatial},
                                                      {"time_bound", s.time_bound},
                                                      {"parameters", params},
                                                      {"inputs", inputs},
                                                      {"outputs", outputs}};
    }

    nlohmann::json value_types = nlohmann::json::array();
    for (int i = 0; i <= static_cast<int>(ValueType::Json); ++i)
        value_types.push_back(to_string(static_cast<ValueType>(i)));
    nlohmann::json layer_roles = nlohmann::json::array();
    for (int i = 0; i <= static_cast<int>(LayerRole::Custom); ++i)
        layer_roles.push_back(to_string(static_cast<LayerRole>(i)));
    nlohmann::json blend_modes = nlohmann::json::array();
    for (int i = 0; i <= static_cast<int>(BlendMode::Premultiplied); ++i)
        blend_modes.push_back(to_string(static_cast<BlendMode>(i)));
    nlohmann::json render_modes = nlohmann::json::array();
    for (int i = 0; i <= static_cast<int>(RenderMode::None); ++i)
        render_modes.push_back(to_string(static_cast<RenderMode>(i)));
    nlohmann::json codes = nlohmann::json::object();
    for (const auto& [code, text] : validation_code_table()) codes[code] = text;

    return {{"schema_version", "0.1.0"},
            {"node_types", node_types},
            {"value_types", value_types},
            {"layer_roles", layer_roles},
            {"blend_modes", blend_modes},
            {"render_modes", render_modes},
            {"validation_codes", codes}};
}

nlohmann::json SpecRegistry::effect_json_schema() const {
    using nlohmann::json;
    json defs = json::object();

    defs["node_id"] = {{"type", "string"}, {"pattern", R"(^[a-z][a-z0-9_]*$)"}};
    defs["node_ref"] = {{"type", "string"}, {"pattern", R"(^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)?$)"}};
    defs["input_value"] = {{"anyOf", json::array({json{{"$ref", "#/$defs/node_ref"}},
                                                  json{{"type", "array"}, {"items", json{{"$ref", "#/$defs/node_ref"}}}}})}};
    defs["interp"] = {{"type", "string"}, {"enum", json::array({"linear", "step", "smooth"})}};
    defs["vec2"] = {{"type", "array"}, {"items", json{{"type", "number"}}}, {"minItems", 2}, {"maxItems", 2}};
    defs["vec3"] = {{"type", "array"}, {"items", json{{"type", "number"}}}, {"minItems", 3}, {"maxItems", 3}};
    defs["vec4"] = {{"type", "array"}, {"items", json{{"type", "number"}}}, {"minItems", 4}, {"maxItems", 4}};
    defs["color"] = {{"anyOf", json::array({json{{"type", "array"},
                                                 {"items", json{{"type", "number"}}},
                                                 {"minItems", 3},
                                                 {"maxItems", 4}},
                                            json{{"type", "string"},
                                                 {"pattern", R"(^#?[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$)"}}})}};
    defs["float_list"] = {{"type", "array"}, {"items", json{{"type", "number"}}}};
    defs["vec3_list"] = {{"type", "array"}, {"items", json{{"$ref", "#/$defs/vec3"}}}};
    defs["curve"] = {
        {"description", "over-lifetime curve, t in [0,1]"},
        {"anyOf", json::array({json{{"type", "array"},
                                    {"items", json{{"type", "array"},
                                                   {"minItems", 2},
                                                   {"maxItems", 3},
                                                   {"prefixItems", json::array({json{{"type", "number"}},
                                                                                json{{"type", "number"}},
                                                                                json{{"$ref", "#/$defs/interp"}}})}}}},
                               json{{"type", "object"},
                                    {"required", json::array({"keys"})},
                                    {"properties",
                                     json{{"keys", json{{"type", "array"},
                                                        {"items", json{{"type", "object"},
                                                                       {"required", json::array({"t", "v"})},
                                                                       {"properties",
                                                                        json{{"t", json{{"type", "number"}}},
                                                                             {"v", json{{"type", "number"}}},
                                                                             {"interp", json{{"$ref", "#/$defs/interp"}}}}}}}}}}}}})}};
    defs["gradient"] = {
        {"description", "over-lifetime color gradient, t in [0,1]"},
        {"anyOf", json::array({json{{"type", "array"},
                                    {"items", json{{"type", "array"},
                                                   {"minItems", 2},
                                                   {"maxItems", 2},
                                                   {"prefixItems", json::array({json{{"type", "number"}},
                                                                                json{{"$ref", "#/$defs/color"}}})}}}},
                               json{{"type", "object"},
                                    {"required", json::array({"keys"})},
                                    {"properties",
                                     json{{"keys", json{{"type", "array"},
                                                        {"items", json{{"type", "object"},
                                                                       {"required", json::array({"t", "color"})},
                                                                       {"properties",
                                                                        json{{"t", json{{"type", "number"}}},
                                                                             {"color", json{{"$ref", "#/$defs/color"}}}}}}}}}}}}})}};
    defs["keyframe_track"] = {
        {"type", "array"},
        {"items", json{{"type", "object"},
                       {"required", json::array({"time", "value"})},
                       {"properties", json{{"time", json{{"type", "number"}}},
                                           {"value", json::object()},
                                           {"interp", json{{"$ref", "#/$defs/interp"}}}}},
                       {"additionalProperties", false}}}};
    defs["timeline"] = {{"type", "object"},
                        {"properties",
                         json{{"phases", json{{"type", "array"},
                                              {"items", json{{"type", "object"},
                                                             {"required", json::array({"name", "start", "end"})},
                                                             {"properties", json{{"name", json{{"type", "string"}}},
                                                                                 {"start", json{{"type", "number"}}},
                                                                                 {"end", json{{"type", "number"}}}}},
                                                             {"additionalProperties", false}}}}}}},
                        {"additionalProperties", false}};
    json roles = json::array();
    for (int i = 0; i <= static_cast<int>(LayerRole::Custom); ++i) roles.push_back(to_string(static_cast<LayerRole>(i)));
    defs["layer"] = {{"type", "object"},
                     {"required", json::array({"id"})},
                     {"properties", json{{"id", json{{"type", "string"}}},
                                         {"name", json{{"type", "string"}}},
                                         {"role", json{{"type", "string"}, {"enum", roles}}},
                                         {"enabled", json{{"type", "boolean"}}},
                                         {"metadata", json{{"type", "object"}}}}},
                     {"additionalProperties", false}};

    // One $defs entry per distinct parameter shape, allowing the bare value or
    // the {"value", "track"} wrapper.
    std::map<std::string, json> param_defs;
    auto param_ref = [&param_defs](const ParamSpec& p) {
        std::string key = "param_" + schema_key(p);
        if (param_defs.find(key) == param_defs.end()) {
            json bare = bare_schema(p);
            json wrapper{{"type", "object"},
                         {"required", json::array({"value"})},
                         {"properties", json{{"value", bare}, {"track", json{{"$ref", "#/$defs/keyframe_track"}}}}},
                         {"additionalProperties", false}};
            param_defs[key] = json{{"anyOf", json::array({bare, wrapper})}};
        }
        return json{{"$ref", "#/$defs/" + key}};
    };

    json node_variants = json::array();
    for (const auto& s : specs_) {
        std::string type_name(to_string(s.type));
        json params = json::object();
        for (const auto& p : s.params) params[p.name] = param_ref(p);
        json inputs = json::object();
        for (const auto& p : s.inputs) inputs[p.name] = json{{"$ref", "#/$defs/input_value"}};
        json node{{"type", "object"},
                  {"title", type_name},
                  {"description", s.description},
                  {"required", json::array({"id", "type"})},
                  {"properties",
                   json{{"id", json{{"$ref", "#/$defs/node_id"}}},
                        {"type", json{{"const", type_name}}},
                        {"version", json{{"type", "integer"}, {"minimum", 1}}},
                        {"enabled", json{{"type", "boolean"}}},
                        {"seed", json{{"type", "integer"}, {"minimum", 0}}},
                        {"parent", json{{"$ref", "#/$defs/node_ref"}}},
                        {"layer", json{{"type", "string"}}},
                        {"metadata", json{{"type", "object"}}},
                        {"outputs", json{{"type", "array"}, {"items", json{{"type", "string"}}}}},
                        {"parameters", json{{"type", "object"}, {"properties", params}, {"additionalProperties", false}}},
                        {"inputs", json{{"type", "object"}, {"properties", inputs}, {"additionalProperties", false}}}}},
                  {"additionalProperties", false}};
        defs["node_" + type_name] = node;
        node_variants.push_back(json{{"$ref", "#/$defs/node_" + type_name}});
    }
    defs["node"] = {{"oneOf", node_variants}};
    for (const auto& [key, value] : param_defs) defs[key] = value;

    return {{"$schema", "https://json-schema.org/draft/2020-12/schema"},
            {"$id", "https://aetherfx.dev/schema/effect.schema.json"},
            {"title", "AetherFX Effect"},
            {"description", "An AetherFX effect document (schema 0.1.0). Generated from aether::SpecRegistry."},
            {"type", "object"},
            {"required", nlohmann::json::array({"name", "duration", "nodes"})},
            {"properties",
             json{{"schema_version", json{{"type", "string"}, {"const", "0.1.0"}}},
                  {"name", json{{"type", "string"}}},
                  {"duration", json{{"type", "number"}, {"minimum", 0.01}}},
                  {"seed", json{{"type", "integer"}, {"minimum", 0}}},
                  {"timeline", json{{"$ref", "#/$defs/timeline"}}},
                  {"layers", json{{"type", "array"}, {"items", json{{"$ref", "#/$defs/layer"}}}}},
                  {"nodes", json{{"type", "array"}, {"items", json{{"$ref", "#/$defs/node"}}}}},
                  {"metadata", json{{"type", "object"}}}}},
            {"additionalProperties", false},
            {"$defs", defs}};
}

// ---------------------------------------------------------------------------
// parameter access
// ---------------------------------------------------------------------------

namespace {
const ParamSpec& require_param(const Node& node, std::string_view name) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    const ParamSpec* p = spec.find_param(name);
    if (p == nullptr)
        throw Error("E004", "node type \"" + std::string(to_string(node.type)) + "\" has no parameter \"" +
                                std::string(name) + "\"");
    return *p;
}
}  // namespace

Value param_value(const Node& node, std::string_view name, double time) {
    const ParamSpec& ps = require_param(node, name);
    const Parameter* p = node.find_param(name);
    return p != nullptr ? p->eval(time) : ps.default_value;
}

float param_float(const Node& node, std::string_view name, double time) { return value_as_float(param_value(node, name, time)); }
int param_int(const Node& node, std::string_view name, double time) { return value_as_int(param_value(node, name, time)); }
bool param_bool(const Node& node, std::string_view name, double time) { return value_as_bool(param_value(node, name, time)); }
Vec2 param_vec2(const Node& node, std::string_view name, double time) { return value_as_vec2(param_value(node, name, time)); }
Vec3 param_vec3(const Node& node, std::string_view name, double time) { return value_as_vec3(param_value(node, name, time)); }
Vec4 param_vec4(const Node& node, std::string_view name, double time) { return value_as_vec4(param_value(node, name, time)); }
Color param_color(const Node& node, std::string_view name, double time) { return value_as_color(param_value(node, name, time)); }

std::string param_string(const Node& node, std::string_view name, double time) {
    const ParamSpec& ps = require_param(node, name);
    if (ps.type != ValueType::String && ps.type != ValueType::Enum && ps.type != ValueType::Ref)
        throw Error("E005", "parameter \"" + std::string(name) + "\" is not a string/enum/ref");
    return value_as_string(param_value(node, name, time));
}

Curve param_curve(const Node& node, std::string_view name) { return value_as_curve(param_value(node, name, 0.0)); }
Gradient param_gradient(const Node& node, std::string_view name) { return value_as_gradient(param_value(node, name, 0.0)); }
std::vector<float> param_float_list(const Node& node, std::string_view name) {
    return value_as_float_list(param_value(node, name, 0.0));
}
std::vector<Vec3> param_vec3_list(const Node& node, std::string_view name) {
    return value_as_vec3_list(param_value(node, name, 0.0));
}
nlohmann::json param_json(const Node& node, std::string_view name) {
    Value v = param_value(node, name, 0.0);
    if (const nlohmann::json* j = std::get_if<nlohmann::json>(&v)) return *j;
    return value_to_json(v);
}

TimeWindow node_window(const Node& node) {
    const NodeSpec& spec = SpecRegistry::instance().get(node.type);
    if (!spec.time_bound) return TimeWindow{0.0, -1.0};
    double start = static_cast<double>(param_float(node, "start_time"));
    double duration = static_cast<double>(param_float(node, "duration"));
    return TimeWindow{start, duration < 0.0 ? -1.0 : start + duration};
}

}  // namespace aether
