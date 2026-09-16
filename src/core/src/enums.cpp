#include "aether/core/enums.hpp"

#include <array>
#include <utility>

namespace aether {
namespace {
template <class E, size_t N>
std::string_view name_of(const std::array<std::pair<E, std::string_view>, N>& table, E e) {
    for (const auto& [k, v] : table) if (k == e) return v;
    return "unknown";
}
template <class E, size_t N>
bool parse_of(const std::array<std::pair<E, std::string_view>, N>& table, std::string_view s, E& out) {
    for (const auto& [k, v] : table) if (v == s) { out = k; return true; }
    return false;
}
constexpr std::array<std::pair<NodeType, std::string_view>, 18> kNodeTypes{{
    {NodeType::Emitter, "emitter"}, {NodeType::ParticleSystem, "particle_system"}, {NodeType::Force, "force"},
    {NodeType::Field, "field"}, {NodeType::Volume, "volume"}, {NodeType::Mesh, "mesh"}, {NodeType::Curve, "curve"},
    {NodeType::Trail, "trail"}, {NodeType::Beam, "beam"}, {NodeType::Light, "light"}, {NodeType::Decal, "decal"},
    {NodeType::Material, "material"}, {NodeType::Noise, "noise"}, {NodeType::Collider, "collider"},
    {NodeType::Event, "event"}, {NodeType::Camera, "camera"}, {NodeType::PostEffect, "post_effect"},
    {NodeType::Texture, "texture"}}};
constexpr std::array<std::pair<LayerRole, std::string_view>, 7> kLayerRoles{{
    {LayerRole::Telegraph, "telegraph"}, {LayerRole::Ignition, "ignition"}, {LayerRole::Primary, "primary"},
    {LayerRole::Secondary, "secondary"}, {LayerRole::Interaction, "interaction"}, {LayerRole::Aftermath, "aftermath"},
    {LayerRole::Custom, "custom"}}};
constexpr std::array<std::pair<BlendMode, std::string_view>, 3> kBlend{{
    {BlendMode::Additive, "additive"}, {BlendMode::Alpha, "alpha"}, {BlendMode::Premultiplied, "premultiplied"}}};
constexpr std::array<std::pair<RenderMode, std::string_view>, 5> kRender{{
    {RenderMode::Billboard, "billboard"}, {RenderMode::StretchedBillboard, "stretched_billboard"},
    {RenderMode::Mesh, "mesh"}, {RenderMode::Ribbon, "ribbon"}, {RenderMode::None, "none"}}};
constexpr std::array<std::pair<Shading, std::string_view>, 2> kShading{{{Shading::Unlit, "unlit"}, {Shading::Lit, "lit"}}};
constexpr std::array<std::pair<LightType, std::string_view>, 3> kLight{{
    {LightType::Point, "point"}, {LightType::Spot, "spot"}, {LightType::Area, "area"}}};
constexpr std::array<std::pair<Interp, std::string_view>, 3> kInterp{{
    {Interp::Linear, "linear"}, {Interp::Step, "step"}, {Interp::Smooth, "smooth"}}};
}  // namespace

std::string_view to_string(NodeType t) { return name_of(kNodeTypes, t); }
std::string_view to_string(LayerRole r) { return name_of(kLayerRoles, r); }
std::string_view to_string(BlendMode b) { return name_of(kBlend, b); }
std::string_view to_string(RenderMode m) { return name_of(kRender, m); }
std::string_view to_string(Shading s) { return name_of(kShading, s); }
std::string_view to_string(LightType t) { return name_of(kLight, t); }
std::string_view to_string(Interp i) { return name_of(kInterp, i); }
bool parse_node_type(std::string_view s, NodeType& out) { return parse_of(kNodeTypes, s, out); }
bool parse_layer_role(std::string_view s, LayerRole& out) { return parse_of(kLayerRoles, s, out); }
bool parse_blend_mode(std::string_view s, BlendMode& out) { return parse_of(kBlend, s, out); }
bool parse_render_mode(std::string_view s, RenderMode& out) { return parse_of(kRender, s, out); }
bool parse_shading(std::string_view s, Shading& out) { return parse_of(kShading, s, out); }
bool parse_light_type(std::string_view s, LightType& out) { return parse_of(kLight, s, out); }
bool parse_interp(std::string_view s, Interp& out) { return parse_of(kInterp, s, out); }

}  // namespace aether
