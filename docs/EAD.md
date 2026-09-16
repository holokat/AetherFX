# Effect Analysis Document (EAD)

Output of reference analysis (Mode A) and the planning input for semantic
generation (Mode B). Produced by any `ReferenceAnalyzer` adapter
(python/aetherfx/ai) and validated against `schema/ead.schema.json`
(generated from the pydantic model in `aetherfx.ead`).

Everything inferred from a still image is *inferred*. The document carries
an explicit `confidence` and an `inferred: true` flag on motion/timing.

```
{
  "ead_version": "0.1.0",
  "source": {"kind": "image|video|text", "path": "...", "prompt": "..."},
  "global": {
    "effect_category": "fire_aoe|projectile|beam|impact|aura|environmental|...",
    "estimated_scale_m": 6.0,
    "visual_style": "realistic|stylized|painterly|anime|hybrid",
    "dominant_colors": [{"rgb": [1.0, 0.4, 0.05], "share": 0.4, "role": "flame"}],
    "contrast": "low|medium|high",
    "energy": "calm|moderate|violent",
    "symmetry": "radial|bilateral|none",
    "camera_angle": "top_down|three_quarter|eye_level|low",
    "probable_duration_s": 4.0,
    "confidence": 0.7
  },
  "primary_forms": [
    {"form": "ring|circle|sphere|column|cone|beam|arc|wave|cloud|spiral|trail|explosion",
     "screen_bbox": [x0, y0, x1, y1], "role": "outer flame wall", "confidence": 0.8}
  ],
  "layers": [
    {
      "id": "outer_flame_wall",
      "semantic_role": "primary",         # telegraph|ignition|primary|secondary|interaction|aftermath
      "primitive": "emitter",             # vocabulary node type that best matches
      "shape_hint": "ring",
      "screen_coverage": 0.25,
      "world_hypothesis": {"center": [0,0,0], "radius_m": 3.0, "height_m": 1.5},
      "depth": "midground",               # foreground|midground|background
      "inside_of": null,                  # layer id it is contained in, if any
      "color": [1.0, 0.45, 0.08],
      "brightness": "emissive_high|emissive|lit|dark",
      "opacity": "opaque|translucent|additive_glow",
      "material_behavior": "flame|smoke|sparks|solid|energy|liquid|distortion",
      "motion_hypothesis": {"inferred": true, "description": "rising turbulent flames", "speed_mps": 2.5, "direction": [0,1,0]},
      "simulation_type": "particles|analytic|rigid|volume",
      "confidence": 0.75
    }
  ],
  "temporal_hypothesis": {
    "inferred": true,
    "phases": [{"name": "anticipation", "start": 0.0, "end": 0.8, "description": "rune fades in, embers gather"}, ...]
  },
  "occlusion": [{"front": "sparks", "behind": "outer_flame_wall"}],
  "implementation_plan": [
    {"layer": "outer_flame_wall",
     "nodes": [{"type": "emitter", "shape": "ring", "radius": 3.0},
               {"type": "particle_system", "blend": "additive"},
               {"type": "force", "force_type": "curl_noise"},
               {"type": "material", "emissive_intensity": 3}]}
  ],
  "open_questions": ["is the rune animated?"]
}
```

`aetherfx.planner.ead_to_effect(ead)` turns the implementation plan into
graph tool calls. The evaluation loop is: render -> `evaluate_render(reference,
render)` (AI adapter or metric baseline) -> a list of `ChangeSuggestion`
`{layer, node, parameter, current, proposed, reason}` -> apply -> repeat.
