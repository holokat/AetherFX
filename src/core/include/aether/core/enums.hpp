#pragma once
#include <string>
#include <string_view>

namespace aether {

enum class NodeType {
    Emitter, ParticleSystem, Force, Field, Volume, Mesh, Curve, Trail, Beam, Light, Decal,
    Material, Noise, Collider, Event, Camera, PostEffect, Texture,
};
enum class LayerRole { Telegraph, Ignition, Primary, Secondary, Interaction, Aftermath, Custom };
enum class BlendMode { Additive, Alpha, Premultiplied };
enum class RenderMode { Billboard, StretchedBillboard, Mesh, Ribbon, None };
enum class Shading { Unlit, Lit };
enum class LightType { Point, Spot, Area };
enum class Interp { Linear, Step, Smooth };

// snake_case names as used in JSON (e.g. "particle_system", "stretched_billboard").
std::string_view to_string(NodeType t);
std::string_view to_string(LayerRole r);
std::string_view to_string(BlendMode b);
std::string_view to_string(RenderMode m);
std::string_view to_string(Shading s);
std::string_view to_string(LightType t);
std::string_view to_string(Interp i);

// Parsing. Return false on unknown names (do not throw; callers produce diagnostics).
bool parse_node_type(std::string_view s, NodeType& out);
bool parse_layer_role(std::string_view s, LayerRole& out);
bool parse_blend_mode(std::string_view s, BlendMode& out);
bool parse_render_mode(std::string_view s, RenderMode& out);
bool parse_shading(std::string_view s, Shading& out);
bool parse_light_type(std::string_view s, LightType& out);
bool parse_interp(std::string_view s, Interp& out);

inline constexpr int kNodeTypeCount = 18;

}  // namespace aether
