#pragma once
// Procedural texture graph (docs/VOCABULARY.md, "Procedural texture graph").
// Each op produces an RGBA float image. Deterministic given (graph, options).
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "aether/core/diagnostics.hpp"
#include "aether/core/image.hpp"
#include "aether/core/resources.hpp"

namespace aether::procedural {

struct TextureBakeOptions {
    int width = 256;
    int height = 256;
    int frames = 1;      // >1 bakes an animated strip; ops read `time` in [0,1) per frame
    uint32_t seed = 0;   // combined with per-op seeds
};

// Bakes `graph` ({"nodes":[{id, op, params, inputs}], "output": id}).
// Throws Error("texture_graph") on structural problems; use validate_texture_graph for diagnostics.
TextureResource bake_texture_graph(const nlohmann::json& graph, const TextureBakeOptions& options);
Diagnostics validate_texture_graph(const nlohmann::json& graph);

// Machine-readable op specs: [{"op": name, "params": {name: {type, default, description}}, "inputs": [...]}]
nlohmann::json texture_ops_json();
std::vector<std::string> texture_op_names();

// Built-in sprites used when nothing is connected.
Image default_soft_particle(int size = 64);   // radial smooth falloff, white, alpha = falloff
Image default_spark(int size = 32);           // tight bright core
Image default_smoke_puff(int size = 128, uint32_t seed = 7);  // fbm-eroded radial puff

// Image utilities shared with the renderer's post stack.
Image resample(const Image& src, int width, int height);
Image gaussian_blur(const Image& src, float radius_px);
void premultiply(Image& img);

}  // namespace aether::procedural
