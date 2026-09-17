"""Shared knowledge for every generator backend: the curated tool list, a
compact vocabulary rendering, VFX authoring recipes distilled from the example
effects, task-prompt construction and render-image helpers."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

#: Engine tools a generating agent may call (docs/AGENT_API.md "MCP surface").
CURATED_TOOLS: tuple[str, ...] = (
    "describe_vocabulary", "create_effect", "load_effect", "save_effect", "list_effects",
    "inspect_graph", "inspect_node", "create_layer", "create_node", "delete_node", "duplicate_layer",
    "set_parameter", "set_parameters", "get_parameter", "set_keyframe", "connect_nodes",
    "disconnect_nodes", "set_timeline_phase", "simulate", "render_frame", "render_preview",
    "inspect_statistics", "compare_reference", "evaluate_effect", "export_effect", "undo", "redo",
    "get_effect_json",
)

RENDER_TOOLS: dict[str, tuple[str, str]] = {
    # tool -> (result key holding an image path, caption)
    "render_frame": ("path", "frame"),
    "render_preview": ("contact_sheet", "preview contact sheet"),
    "render_turntable": ("contact_sheet", "turntable"),
    "evaluate_effect": ("render_path", "evaluation frame"),
}

CONVENTIONS = """You author real-time game VFX for the AetherFX engine by calling its tools. You never write shaders or code: an effect is a graph of typed nodes with parameters, and the deterministic engine simulates and renders it. You can SEE what you make: render tools return the image. Look at it critically like a senior VFX artist and iterate.

WORLD AND UNITS
- Meters, seconds, degrees. Y is up. The ground plane is y = 0 (add a plane collider, e.g. id "ground", so particles land on it).
- Colors are linear RGBA arrays [r, g, b, a]; values above 1 are HDR. `emissive` multiplies brightness (glow through bloom).
- The preview camera comes from the effect's `camera` node (position, target, fov). Default: from (0, 1.5, 5) looking at (0, 1, 0). Build the effect around the origin, roughly 1-3 m tall, and set a camera node that frames it well (closer for small effects).

WORKFLOW (follow it every time)
1. create_effect(name, duration, seed) for a new effect; when modifying, work on the active effect (inspect_graph first).
2. create_layer per semantic layer with roles telegraph / ignition / primary / secondary / interaction / aftermath.
3. Create shared nodes before the nodes that use them: textures (procedural graphs), materials, forces, colliders, then particle_systems (inputs: sprite, material, forces, colliders), then emitters (inputs.particle = particle_system id; set layer), then trails / beams / lights / decals / post_effects and a camera.
4. Timing: set_timeline_phase for anticipation / activation / peak / sustain / decay when the effect builds up; bind emitters and lights to a phase with the `phase` parameter, or keyframe `rate` / `intensity` with set_keyframe (time in seconds, value).
5. simulate(), then render_preview(fps=12, width=384, height=384) and study the contact sheet: silhouette, colors, scale, timing, anything invisible or blown out. Fix with set_parameter / set_parameters and render again. Two or three passes are normal; stop when it reads well.
6. End with a short summary of what you built and what to tune next.

RULES
- Every emitter needs inputs.particle. Every mutating tool returns diagnostics: fix E-codes immediately (unknown parameters and bad enums are errors), W-codes are hints.
- Use only the vocabulary below; ids are snake_case and unique; keep total max_particles under ~20000.
- Prefer a few well-tuned layers over many weak ones. One iteration with real changes beats three with tiny tweaks.
- Parameters are passed as plain JSON values; curves are [[t, v], ...] with t in 0..1, gradients are [[t, [r, g, b, a]], ...]. Keyframe tracks are set with set_keyframe (any parameter marked (A) below: rates, intensities, positions, opacity, size...).
- create_node fields: {type, id, layer, parent, parameters, inputs, metadata}. Only vocabulary parameters go inside `parameters`.

CONTROLS (ship them: they are how a player-facing effect is tuned without a second document)
- A control is a named numeric knob bound to node parameters: {"id", "label", "group", "min", "max", "default", "value", "step", "unit", "bindings": [{"node", "parameter", "op"}]}. `op` is multiply (scalars, every component of a vector, the rgb of a colour), add, set, or hue_shift (degrees; colours and gradients). It is non-destructive: the authored values stay, the compiler folds the control in, so it is safe to move live and to undo.
- Finish every effect with 4-8 meaningful controls, authored with add_control. Give them names an artist recognises - "Flame height", "Ember amount", "Core glow", "Ring size", "Smoke thickness" - not parameter names. Group them by layer: `group` is the layer's display name ("Flames", "Debris, sparks, smoke"), or "Global" for effect-wide.
- Multipliers use min 0, max 3, default 1, step 0.01, unit "x"; a hue control uses min -180, max 180, default 0, step 1, unit "deg".
- What actually moves the picture: brightness is the particle `color` magnitude plus `emissive` and the material's `emissive_intensity`, not `emissive` alone (it is usually 0.05-0.2). Scale is particle `size`, trail/beam `width`, decal `size`, light `radius`. Amount is emitter `rate` and `burst_count`. Bind one control to every node that makes up that idea, so one slider moves the whole look.
- generate_default_controls builds a Global group and one per layer as a starting point; keep the ones that read well, rename them, and remove_control the rest. list_controls and set_control let you try a value and render it before you commit to the range.

RECIPES (proven values from the shipped examples)
- Soft puff sprite (fire, smoke, magic): texture node, width/height 128, graph {"nodes":[{"id":"r","op":"gradient_radial","params":{"radius":0.5,"falloff":"smooth"}},{"id":"n","op":"fbm","params":{"frequency":4,"octaves":4,"seed":3}},{"id":"m","op":"math","params":{"mode":"multiply"},"inputs":{"a":"r","b":"n"}},{"id":"l","op":"levels","params":{"in_low":0.05,"in_high":0.6},"inputs":{"a":"m"}}],"output":"l"}.
- Spark sprite: 32x32, graph {"nodes":[{"id":"r","op":"gradient_radial","params":{"radius":0.5,"falloff":"quadratic"}}],"output":"r"}.
- Rune / telegraph texture (256): ring (radius 0.46, thickness 0.03) max ring (radius 0.3, thickness 0.015) max (cracks density 6 width 0.012 multiplied by gradient_radial radius 0.44 linear).
- Fire particles: blend additive, emissive 2-4, lifetime 0.3-0.8, size 0.1-0.5, size_over_life [[0,0.5],[0.3,1],[1,0.3]], color [1,0.42,0.06,1], color_over_life [[0,[1,0.9,0.45,1]],[0.4,[1,0.4,0.05,1]],[1,[0.3,0.03,0,1]]], opacity_over_life [[0,0],[0.15,1],[0.7,0.8],[1,0]], drag 1.5-3; forces curl_noise (strength 1.5-3, frequency 1-3, octaves 2, speed 0.8-1) and buoyancy (strength 1-2). Emitter velocity 1-3 upward (direction [0,1,0], spread 10-20) or radial (direction [0,0,0]).
- Simulated fire sprite (the one that reads as real fire): texture node width 128, height 192, frames 32, graph {"nodes":[{"id":"fire","op":"fire_sim","params":{"seed":3,"fuel":1.25,"fuel_width":0.6,"buoyancy":1.7,"turbulence":1.15,"turbulence_scale":3,"cooling":1.8,"detail":4,"speed":0.06,"substeps":4,"loop":true,"flicker":0.35,"sharpness":1.25}}],"output":"fire"}. `fire_sim` bakes a 2D flame simulation, so every frame has tongues that split, ragged eroded edges and a white-hot base, and the flipbook loops; `flame` paints one smooth tongue and is cheaper, so keep it for small distant licks. The sheet is heat in [0,1] (rgb and alpha), root at v=0: colour it with material `temperature_gradient` [[0,[0.05,0,0,1]],[0.25,[0.8,0.1,0,1]],[0.5,[1,0.45,0.05,1]],[0.75,[1,0.85,0.35,1]],[1,[1,1,0.85,1]]] (or a `colorize` op with the same ramp). Read it as fire with big overlapping sprites: particle_system sprite_fps 20 (plays the sheet at a fixed rate, independent of lifetime), render_mode stretched_billboard, velocity_stretch 0.05-0.1, blend additive, size 0.7-1.1 with size_variance 0.4 and lifetime 1.0 with lifetime_variance 0.5 (so no two sprites are on the same frame), opacity 0.2 and emissive 1.2 (many additive sheets saturate fast - a blown-out core hides the shapes), opacity_over_life [[0,0],[0.18,1],[0.65,0.8],[1,0]], size_over_life [[0,0.55],[0.3,1],[1,0.85]], color_over_life [[0,[1,1,1,1]],[0.55,[1,0.72,0.4,1]],[1,[0.7,0.22,0.05,1]]], drag 2; forces buoyancy 2 + curl_noise (strength 0.9, frequency 1); emitter disc radius 0.4, rate 120-150, velocity 1.9 with velocity_variance 1.3 and spread 15-20, so the sprite roots sit at different heights and no straight sprite edge shows.
- Smoke: blend alpha, material shading lit, color [0.18,0.16,0.15,1], opacity 0.3-0.5, opacity_over_life [[0,0],[0.25,1],[1,0]], size_over_life [[0,0.4],[1,1.8]], lifetime 1.2-2.5, drag 0.8-1, sort true; forces buoyancy (1-2) + turbulence (strength 0.6-1, frequency 0.6-1.5); soft_particle material with depth_fade 0.2-0.3.
- Sparks / embers: render_mode stretched_billboard, velocity_stretch 0.12-0.2, size 0.02-0.04, emissive 5-8, color [1,0.7,0.25,1] with color_over_life to red, opacity_over_life [[0,1],[0.8,1],[1,0]], drag 0.4-0.5; forces gravity (9.81) + light turbulence; colliders ground; emitter velocity 3-7, velocity_variance 2-3, spread 30-40. Embers: smaller rate, buoyancy instead of gravity, lifetime 2 s.
- Magic / energy: cool colors ([0.3,0.6,1,1] blue, [0.6,0.3,1,1] violet, [0.3,1,0.6,1] green), additive, emissive 3-6, curl_noise, a `trail` (width 0.1-0.3, lifetime 0.3-0.5, source = a moving mesh core) and a point light of the same color parented to the core. Beams: beam node with noise_amplitude 0.2-0.4, noise_frequency 3, jitter_rate 30-40, branching 3-5, emissive 8-14, width 0.05.
- Projectile / missile: a small mesh sphere core (radius 0.06-0.12, emissive 4-8) whose position is keyframed with set_keyframe; emitters, trail and light use `parent` = the core id (NOTE: `parent`, `layer`, `id`, `inputs` are top-level fields of create_node, not entries of `parameters`), emitters use inherit_velocity 0.2-0.3. Forces that should follow the projectile (vortex, attractor) have an animatable `position`: keyframe it on the same path.
- Organic mesh particles (crystals, rocks, shards): mesh node `primitive: crystal|rock|shard` with `variants: 6-8`, `irregularity: 0.5-0.7` (seeded, no two alike); particle_system `render_mode: mesh`, `orientation: upright` + `tilt: 15-35` + `rotation_variance: 180` for spikes leaning out of a ring, `orientation: tumble` + `angular_velocity: 60-120` for debris, `mesh_scale: [0.6, 1.5, 0.6]` + `mesh_scale_variance: [0.2, 0.6, 0.2]` for tall thin shards; grow them with size_over_life [[0,0.02],[0.1,1.05],[0.15,1],[0.88,1],[1,0]] and `drag: 25` so they burst up and hold; material `shading: lit`, `fresnel_power: 2-4` for glowing edges. Never use plain cones/cubes for natural debris.
- Volumetric-looking smoke: material `shading: lit` + `dissolve: 0.4-0.6` + `erosion: 0.2-0.3`, texture `frames: 8` with fbm `animate: 1` for living puffs; a light inside the effect gives the puffs a lit and a dark side.
- Nebula / aura volume (raymarched, not particles): a `volume` node with `mode: procedural` is a closed-form density field the renderer marches - one node replaces a thousand puffs and never pops or sorts. `shape: nebula|sphere|column|disc|ring|cone`, `radius` 1.5-2.5, `height` = the vertical diameter (nebula) or the full height (column/cone); `density` 1-2 (extinction per metre, keyframe it in and out: 0 -> 1.8 -> 0), `emission` 1-2, `color` the cool shell and `color_hot` the bright core (e.g. [0.32,0.1,0.82,1] -> [0.3,0.88,0.95,1] for astral purple/teal), `filament_scale` 1.5-2.5, `strands` 0.6 for stringy filaments or 0.2 for soft cloud, `carve` 0.45-0.55 (higher = more holes), `softness` 0.6-0.8, `spiral_arms` 3 with `arm_sharpness` 1.4-1.8 for a galaxy, `spin` 0.1-0.2 rev/s and `twist` 0.3-0.5 for slow rotation, `climb` 0.1-0.3 for rising smoke, `scatter` 0.3-0.45 with a point light of the same hue inside it, `march_steps` 48. Use it as the body of an effect and keep particles for the sparks and wisps around it (see examples/effects/void_nebula.json); a smoke column is `shape: column` with strands 0.3, climb 0.5 and a warm color_hot.
- Compass / magic-circle runes: texture ops `ring` (several radii) + `spokes` (count 8-16) + `star` (points 4-8, core_radius 0.05) combined with math max; `cracks` masked by `gradient_radial` for frost or lava cracks.
- Impact / explosion: activation-phase burst emitter (rate 0, burst_count 300-900, burst_times [0], velocity 6-12, direction [0,0,0], radial_velocity 1); flash light with intensity keyframes 0 -> 30..120 -> 0 within 0.3 s at radius 10-15; shock ring (ring emitter, radial_velocity 1, velocity 8-9, particles size_over_life growing); debris (particle_system render_mode mesh with a cube mesh node visible=false, physics rigid, angular_velocity 200-360, gravity, ground collider); scorch decal (dark color, opacity keyframed in).
- AOE / ground effect: rune decal (emissive 2-3, opacity keyframed in over 0.5 s) + gathering embers (ring emitter radius R, attractor force toward the center, phase anticipation) -> burst + flash -> ring emitter fire wall (radius R, inner_radius R-0.5, rate keyframed 0 -> 2000 -> 0) + central eruption (disc emitter velocity 6) -> smoke, sparks, rocks -> embers + scorch in decay.
- Lights: ground irradiance is about intensity/(d^2+1). Use 2-6 for hand-held glows, 20-40 for room-scale fire, flashes under ~30 at radius 10-15. Bigger values blow the ground out to white.
- Event-driven emitters keep rate 0 and burst_times []; an event node (trigger on_time, time t, inputs.targets [emitter ids]) fires them. on_death / on_collision events need inputs.source = the particle_system.
- Heat haze: post_effect post_type heat_haze intensity 0.3-0.5 inside a window; bloom is on by default in the renderer.
"""


def tool_fields(spec: Any) -> tuple[str, str, dict[str, Any]]:
    """(name, description, input_schema) from a Client.tools() entry (ToolInfo or dict)."""
    if isinstance(spec, dict):
        return spec["name"], spec.get("description") or spec["name"], spec.get("input_schema") or {"type": "object", "properties": {}}
    return spec.name, getattr(spec, "description", None) or spec.name, getattr(spec, "input_schema", None) or {"type": "object", "properties": {}}


def _fmt(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, (list, dict)):
        text = json.dumps(value, separators=(",", ":"))
        return text if len(text) <= 60 else text[:57] + "..."
    return str(value)


def _range(param: dict[str, Any]) -> str:
    lo, hi = param.get("min"), param.get("max")
    if lo is None and hi is None:
        return ""
    return f"[{_fmt(lo) if lo is not None else ''}..{_fmt(hi) if hi is not None else ''}]"


def render_vocabulary(vocabulary: dict[str, Any]) -> str:
    """Compact, LLM-friendly rendering of describe_vocabulary()."""
    out: list[str] = ["VOCABULARY (type: default [range] (A = keyframe-animatable))"]
    for name, spec in sorted(vocabulary.get("node_types", {}).items()):
        flags = [f for f, on in (("spatial", spec.get("spatial")), ("time_bound", spec.get("time_bound"))) if on]
        out.append(f"\n## {name} (tier {spec.get('default_tier', 0)}{', ' + ', '.join(flags) if flags else ''}): {spec.get('description', '')}")
        params = []
        for pname, p in spec.get("parameters", {}).items():
            s = f"{pname}: {p.get('type')}"
            if p.get("enum"):
                s += "{" + "|".join(p["enum"]) + "}"
            if p.get("default") is not None:
                s += f"={_fmt(p['default'])}"
            rng = _range(p)
            if rng:
                s += " " + rng
            if p.get("animatable"):
                s += " (A)"
            params.append(s)
        out.append("params: " + "; ".join(params))
        inputs = spec.get("inputs", {})
        if inputs:
            ports = []
            for port, i in inputs.items():
                acc = "|".join(i.get("accepts") or ["any"])
                ports.append(f"{port}<{acc}>{' list' if i.get('multi') else ''}{' required' if i.get('required') else ''}")
            out.append("inputs: " + "; ".join(ports))
    ops = vocabulary.get("texture_ops") or []
    if ops:
        out.append("\n## texture graph ops (texture.graph = {nodes:[{id, op, params, inputs}], output})")
        for op in ops:
            if isinstance(op, dict):
                params = ", ".join(f"{k}={_fmt(v.get('default'))}" if isinstance(v, dict) else k for k, v in (op.get("params") or {}).items())
                out.append(f"- {op.get('op')}({params}) inputs: {', '.join(op.get('inputs') or []) or '-'}")
            else:
                out.append(f"- {op}")
    return "\n".join(out)


def build_authoring_guide(vocabulary: dict[str, Any]) -> str:
    return CONVENTIONS + "\n" + render_vocabulary(vocabulary)


REFERENCE_INSTRUCTIONS = """REFERENCE RECONSTRUCTION (Mode A): reference image(s) are attached. Before planning, LOOK at each
one and decompose it like a senior VFX artist (docs/EAD.md): effect category, estimated scale (m),
visual style, dominant colours, energy, symmetry, camera angle, probable duration; primary forms
(ring, column, sphere, beam, arc, cloud, spiral...); every visually separable layer with its semantic
role (telegraph/ignition/primary/secondary/interaction/aftermath), the vocabulary primitive that
matches it (emitter+particle_system, decal, beam, trail, light, mesh particles...), depth ordering,
colour, brightness, opacity, material behaviour and a MOTION HYPOTHESIS (a still image has no
motion: say what you infer); a temporal hypothesis with phases if the sheet shows a timeline. Post
that analysis as one status event, then build the effect to match it. After each preview, call
compare_reference(reference_path, time) with the first reference and use its notes and metrics
together with your own eyes to iterate. Match colours, scale and silhouette before adding detail."""


def build_task_prompt(prompt: str, mode: str, active_effect: dict[str, Any] | None,
                      attachments: list[str] | None = None) -> str:
    if mode == "modify" and active_effect:
        head = (
            f"Modify the active effect \"{active_effect.get('name', '?')}\" (id {active_effect.get('effect_id', '?')}). "
            "Start with inspect_graph to see what exists, change only what the request needs, keep ids stable."
        )
    else:
        head = "Create a new effect with create_effect (choose a good name, duration and seed) and build it completely."
    refs = ""
    if attachments:
        listing = "\n".join(f"- {path}" for path in attachments)
        refs = f"\n\n{REFERENCE_INSTRUCTIONS}\nReference images:\n{listing}\n"
    request = prompt.strip() or ("Recreate the attached reference as a real-time effect." if attachments else "")
    return (
        f"{head}{refs}\n\nREQUEST: {request}\n\n"
        "Work through the workflow, render a preview and look at it, iterate until it reads well, "
        "then reply with a brief summary (what layers/nodes you built, what to tune)."
    )


def summarize_result(name: str, result: Any) -> str:
    """One-line summary of a tool result for the job log."""
    if not isinstance(result, dict):
        return str(result)[:160]
    parts: list[str] = []
    diag = result.get("diagnostics")
    if isinstance(diag, dict):
        e, w = diag.get("errors", 0), diag.get("warnings", 0)
        if e or w:
            parts.append(f"{e} error(s), {w} warning(s)")
            for item in diag.get("items", [])[:3]:
                parts.append(f"{item.get('code')}: {item.get('message')}"[:120])
    node = result.get("node")
    if isinstance(node, dict) and node.get("id"):
        parts.append(f"node {node['id']} ({node.get('type')})")
    if "effect_id" in result:
        parts.append(f"effect {result['effect_id']}")
    stats = result.get("statistics")
    if isinstance(stats, dict) and "total_alive" in stats:
        parts.append(f"alive {stats.get('total_alive')} / spawned {stats.get('total_spawned')}")
    if "frames" in result and isinstance(result["frames"], list):
        parts.append(f"{len(result['frames'])} frames")
    if "path" in result:
        parts.append(str(result["path"]))
    return "; ".join(parts) or "ok"


def render_image_paths(name: str, result: Any) -> list[tuple[str, str]]:
    """Image paths (and captions) an agent should look at after a render tool."""
    spec = RENDER_TOOLS.get(name)
    if not spec or not isinstance(result, dict):
        return []
    key, caption = spec
    path = result.get(key)
    if isinstance(path, str) and path.lower().endswith(".png") and Path(path).is_file():
        return [(path, caption)]
    return []


def image_png_base64(path: str, max_px: int = 768) -> tuple[str, int, int] | None:
    """Load a PNG, downscale to at most max_px on the long side, return (base64, w, h)."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            im = im.convert("RGBA")
            w, h = im.size
            scale = min(1.0, max_px / max(w, h))
            if scale < 1.0:
                im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            return base64.b64encode(buf.getvalue()).decode("ascii"), im.size[0], im.size[1]
    except OSError:
        return None
